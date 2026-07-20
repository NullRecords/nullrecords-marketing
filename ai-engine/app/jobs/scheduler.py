"""Background job scheduler using APScheduler.

Manages three automated pipelines:
  1. Follow-up execution — checks for due follow-ups, regenerates messages, attempts delivery
  2. Source discovery — periodic scraping of Bandcamp/SoundCloud/YouTube/Reddit
  3. Media ingestion — searches configured sources and downloads + tags new assets
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ── Job implementations ─────────────────────────────────────────────────


def _run_pending_followups():
    """Check for due follow-ups, regenerate messages, and attempt delivery."""
    from app.core.database import SessionLocal
    from app.models.outreach import OutreachLog
    from app.models.playlist import Playlist
    from app.models.influencer import Influencer
    from app.services.outreach.followup import get_pending_followups, schedule_follow_up
    from app.services.outreach.messaging import generate_outreach_message
    from app.services.outreach.email_sender import send_email

    settings = get_settings()
    db = SessionLocal()
    try:
        due = get_pending_followups(db)
        if not due:
            logger.info("No follow-ups due at %s", datetime.now(timezone.utc).isoformat())
            return

        for log in due:
            # Look up target details for message context
            if log.target_type == "playlist":
                target = db.query(Playlist).filter(Playlist.id == log.target_id).first()
                name = target.name if target else f"playlist-{log.target_id}"
                contact = (target.contact if target else "") or ""
                context = (
                    f"Follow-up — playlist with {target.followers} followers, "
                    f"curated by {target.curator_name}"
                ) if target else "Follow-up"
            else:
                target = db.query(Influencer).filter(Influencer.id == log.target_id).first()
                name = target.handle if target else f"influencer-{log.target_id}"
                contact = (target.contact if target else "") or ""
                context = (
                    f"Follow-up — {target.platform} creator in {target.niche} "
                    f"with {target.followers} followers"
                ) if target else "Follow-up"

            # Generate a fresh follow-up message
            message = generate_outreach_message(name, log.target_type, context)
            subject = f"Following up — {settings.label_name}"

            # Attempt email delivery if we have an email-like contact
            result = {"sent": False, "message_id": None, "method": "logged"}
            if contact and "@" in contact:
                result = send_email(contact, subject, message)

            # Update the outreach log
            log.message = message
            log.subject = subject
            log.status = "sent" if result["sent"] else "follow_up_logged"
            log.message_id = result.get("message_id")
            log.follow_up_date = None  # clear so it isn't picked up again
            db.commit()

            logger.info(
                "Follow-up processed: outreach_id=%d target=%s/%d method=%s",
                log.id, log.target_type, log.target_id, result["method"],
            )

            # Auto-schedule next follow-up (in 7 days)
            schedule_follow_up(db, log.id, days=7)

    except Exception:
        logger.exception("Error in follow-up job")
    finally:
        db.close()


def _run_discovery():
    """Periodically discover new playlists and influencers."""
    from app.core.database import SessionLocal
    from app.models.playlist import Playlist
    from app.models.influencer import Influencer
    from app.services.outreach.discovery import discover_playlists, discover_influencers
    from app.services.outreach.scoring import score_relevance

    settings = get_settings()
    query = settings.scheduler_discovery_query
    db = SessionLocal()
    try:
        logger.info("Running scheduled discovery for: %s", query)
        playlists_raw = discover_playlists(query)
        influencers_raw = discover_influencers(query)

        playlists_added = 0
        for p in playlists_raw:
            existing = db.query(Playlist).filter_by(
                name=p["name"], platform=p["platform"]
            ).first()
            if existing:
                continue
            relevance = score_relevance(p["name"], p.get("curator_name", ""))
            pl = Playlist(
                name=p["name"],
                platform=p["platform"],
                curator_name=p.get("curator_name", ""),
                followers=p.get("followers", 0),
                contact=p.get("contact", ""),
                url=p.get("url", ""),
                relevance_score=relevance,
            )
            db.add(pl)
            playlists_added += 1

        influencers_added = 0
        for i in influencers_raw:
            existing = db.query(Influencer).filter_by(
                handle=i["handle"], platform=i["platform"]
            ).first()
            if existing:
                continue
            relevance = score_relevance(i["handle"], i.get("niche", ""))
            inf = Influencer(
                handle=i["handle"],
                platform=i["platform"],
                followers=i.get("followers", 0),
                niche=i.get("niche", ""),
                contact=i.get("contact", ""),
                relevance_score=relevance,
            )
            db.add(inf)
            influencers_added += 1

        db.commit()
        logger.info(
            "Discovery complete — %d new playlists, %d new influencers",
            playlists_added, influencers_added,
        )
    except Exception:
        logger.exception("Error in discovery job")
    finally:
        db.close()


def _run_media_ingest():
    """Search configured sources, download new assets, and tag them."""
    from pathlib import Path
    from app.core.database import SessionLocal
    from app.models.media import MediaAsset
    from app.services.media.pexels import PexelsSource
    from app.services.media.internet_archive import InternetArchiveSource
    from app.services.media.downloader import download_media
    from app.services.media.tagging import tag_media

    settings = get_settings()
    query = settings.scheduler_discovery_query
    sources = [
        ("pexels", PexelsSource()),
        ("internet_archive", InternetArchiveSource()),
    ]

    db = SessionLocal()
    try:
        total_new = 0
        total_downloaded = 0
        total_tagged = 0

        for source_name, source in sources:
            logger.info("Media ingest: searching %s for '%s'", source_name, query)
            try:
                raw_results = source.search(query)
            except Exception:
                logger.exception("Search failed for %s", source_name)
                continue

            for raw in raw_results:
                normalized = source.normalize(raw)
                existing = (
                    db.query(MediaAsset)
                    .filter_by(source=normalized["source"], source_id=normalized["source_id"])
                    .first()
                )
                if existing:
                    continue

                asset = MediaAsset(**normalized)
                db.add(asset)
                db.flush()
                total_new += 1

                # Auto-download if URL available
                if asset.url:
                    try:
                        url_path = asset.url.rsplit("?", 1)[0]
                        ext = Path(url_path).suffix.lstrip(".") if Path(url_path).suffix else "mp4"
                        if len(ext) > 5 or "/" in ext:
                            ext = "mp4"
                        filename = f"{asset.source_id}.{ext}"
                        local_path = download_media(asset.url, asset.source, filename)
                        asset.local_path = local_path
                        asset.downloaded = True
                        total_downloaded += 1
                    except Exception:
                        logger.exception("Download failed for asset %s", asset.source_id)

                # Auto-tag if downloaded
                if asset.downloaded and asset.local_path:
                    try:
                        tags = tag_media(asset.local_path, asset.title or "")
                        asset.tags = ",".join(tags) if isinstance(tags, list) else str(tags)
                        total_tagged += 1
                    except Exception:
                        logger.exception("Tagging failed for asset %s", asset.source_id)

        db.commit()
        logger.info(
            "Media ingest complete — %d new, %d downloaded, %d tagged",
            total_new, total_downloaded, total_tagged,
        )
    except Exception:
        logger.exception("Error in media ingest job")
    finally:
        db.close()


# ── Daily Shorts Auto-Generation ─────────────────────────────────────────


def _run_daily_shorts_generation():
    """Auto-generate 3 short-form videos per day from the MERA catalog.

    Reads daily_shorts_config.json for the track catalog and preset,
    picks tracks/segments/images with variety, renders 3 videos, and
    writes them to the approval queue (daily_shorts_queue.json) with
    status 'pending'.
    """
    import json
    import random
    import uuid
    from pathlib import Path

    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.services.media.video_engine import VideoConfig, VideoEngine

    settings = get_settings()
    exports = Path(settings.exports_dir)
    config_path = exports / "daily_shorts_config.json"
    queue_path = exports / "daily_shorts_queue.json"

    if not config_path.exists():
        logger.warning("daily_shorts_config.json not found — skipping generation")
        return

    with open(config_path) as f:
        catalog = json.load(f)

    tracks = catalog.get("tracks", [])
    image_pool = catalog.get("image_pool", [])
    preset = catalog.get("default_preset", {})
    variation_effects = catalog.get("variation_effects", [])
    hashtags = catalog.get("hashtags", {})

    if not tracks:
        logger.warning("No tracks in catalog — skipping")
        return

    # Load existing queue to check what was already generated today
    queue = []
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
        except Exception:
            queue = []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays_entries = [e for e in queue if e.get("generated_date") == today]
    if len(todays_entries) >= 3:
        logger.info("Already generated %d shorts today — skipping", len(todays_entries))
        return

    needed = 3 - len(todays_entries)

    # Pick tracks + segments with variety (avoid repeating today's picks)
    used_combos = {(e["track_id"], e["segment_label"]) for e in todays_entries}
    candidates = []
    for track in tracks:
        audio_path = exports / "audio_uploads" / track["audio_file"]
        if not audio_path.exists():
            # Try finding the file with a UUID prefix
            matches = list((exports / "audio_uploads").glob(f"*_{track['audio_file']}"))
            if matches:
                audio_path = matches[0]
            else:
                continue
        for seg in track.get("segments", []):
            combo = (track["id"], seg["label"])
            if combo not in used_combos:
                candidates.append((track, seg, str(audio_path)))

    if not candidates:
        logger.warning("No unused track/segment combos left for today")
        return

    random.shuffle(candidates)
    picks = candidates[:needed]

    # Resolve images from a list of filenames → absolute paths
    def _resolve_images(name_list):
        resolved = []
        for img_name in name_list:
            for search_dir in [exports / "image_uploads", Path(settings.media_library_dir) / "images"]:
                exact = search_dir / img_name
                if exact.exists():
                    resolved.append(str(exact))
                    break
                matches = list(search_dir.glob(f"*_{img_name}"))
                if matches:
                    resolved.append(str(matches[0]))
                    break
        return resolved

    # Global image pool (fallback)
    available_images = _resolve_images(image_pool)

    # ── Load track memory files for smarter captions & overlays ─────────
    def _load_track_memory(track_id):
        """Load a track's memory MD file, return parsed meta + body."""
        import yaml
        mem_path = exports / "track_memory" / f"{track_id}.md"
        if not mem_path.exists():
            return {}
        text = mem_path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return yaml.safe_load(parts[1]) or {}
        return {}

    db = SessionLocal()
    engine = VideoEngine()
    generated = 0

    try:
        for idx, (track, segment, audio_path) in enumerate(picks):
            try:
                # Pick effect variation
                effects = random.choice(variation_effects) if variation_effects else {}

                # Prefer per-track curated images, fall back to global pool
                track_images = _resolve_images(track.get("images", []))
                pool = track_images if track_images else available_images
                imgs = random.sample(pool, min(3, len(pool))) if pool else []

                run_id = uuid.uuid4().hex[:8]
                output_name = f"daily_{today}_{track['id']}_{segment['label']}_{run_id}.mp4"

                # Load per-track memory for caption lines & tags
                memory = _load_track_memory(track["id"])
                caption_lines = memory.get("caption_lines", [])
                mem_tags = memory.get("tags", {})

                # Pick a random caption line for overlay subtitle (fallback to band name)
                overlay_sub = random.choice(caption_lines) if caption_lines else "My Evil Robot Army"

                # Build platform-specific tags: prefer per-track memory, fall back to global
                def _build_tags_for(platform):
                    per_track = mem_tags.get(platform, [])
                    if per_track:
                        return per_track
                    return hashtags.get(platform, [])

                # Build caption with per-track tags
                caption_tag_str = " ".join(_build_tags_for("tiktok"))
                caption_text = f"{overlay_sub}\n\n{track['title']} — My Evil Robot Army\n\n{caption_tag_str}"

                config = VideoConfig(
                    audio_path=audio_path,
                    start_ms=segment.get("start_ms", 0),
                    duration_ms=preset.get("duration_ms", 15000),
                    mood=track.get("mood", ""),
                    tags=track.get("tags", []),
                    output_name=output_name,
                    fps=preset.get("fps", 24),
                    use_glitch_transitions=preset.get("use_glitch_transitions", True),
                    aspect=preset.get("aspect", "vertical"),
                    image_paths=imgs,
                    overlay_text=track["title"],
                    overlay_subtitle=overlay_sub,
                    overlay_position=preset.get("overlay_position", "lower-third"),
                    effect_opacity=preset.get("effect_opacity", 1.0),
                    glitch_opacity=preset.get("glitch_opacity", 0.7),
                    color_shift_enabled=effects.get("color_shift_enabled", False),
                    scanline_enabled=effects.get("scanline_enabled", preset.get("scanline_enabled", False)),
                    vhs_enabled=effects.get("vhs_enabled", False),
                    beat_flash_enabled=preset.get("beat_flash_enabled", True),
                    visualizer_enabled=preset.get("visualizer_enabled", True),
                    visualizer_type=effects.get("visualizer_type", preset.get("visualizer_type", "bars")),
                    visualizer_color=preset.get("visualizer_color", "#00ffff"),
                    visualizer_color2=preset.get("visualizer_color2", "#ff5758"),
                    visualizer_glow=preset.get("visualizer_glow", True),
                    show_song_info=preset.get("show_song_info", True),
                    auto_select_clips=len(imgs) == 0,
                )

                # Add QR code if configured
                if preset.get("qr_enabled"):
                    from app.services.media.overlays import QRCodeConfig
                    config.qr_code_config = QRCodeConfig(
                        url=preset.get("qr_url", "https://www.nullrecords.com"),
                    )

                # Add provider icons if configured
                if preset.get("provider_icons_enabled"):
                    from app.services.media.overlays import ProviderIconConfig
                    config.provider_icon_config = ProviderIconConfig()

                result_path = engine.generate_video(db, config)

                # Add to approval queue
                entry = {
                    "id": uuid.uuid4().hex[:12],
                    "status": "pending",
                    "generated_date": today,
                    "track_id": track["id"],
                    "track_title": track["title"],
                    "segment_label": segment["label"],
                    "filename": output_name,
                    "video_path": str(result_path),
                    "mood": track.get("mood", ""),
                    "effects": effects,
                    "images_used": imgs,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "approved_at": None,
                    "posted_at": None,
                    "platforms_posted": [],
                    "caption": caption_text,
                    "caption_line": overlay_sub,
                    "hashtags": {
                        "tiktok": _build_tags_for("tiktok"),
                        "instagram": _build_tags_for("instagram"),
                        "youtube": _build_tags_for("youtube"),
                    },
                }
                queue.append(entry)
                generated += 1
                logger.info("Daily short generated: %s (%s / %s)", output_name, track["title"], segment["label"])

            except Exception:
                logger.exception("Failed to generate daily short for %s/%s", track["id"], segment["label"])

        # Save queue
        queue_path.write_text(json.dumps(queue, indent=2))
        logger.info("Daily shorts generation complete — %d new videos queued for approval", generated)

    except Exception:
        logger.exception("Error in daily shorts generation job")
    finally:
        db.close()


# ── Scheduled Posting of Approved Shorts ─────────────────────────────────


def _run_daily_shorts_posting():
    """Post approved shorts that are scheduled for today.

    Finds entries in daily_shorts_queue.json with status 'approved'
    and posts them to configured platforms via the video publish pipeline.
    Staggers posts across the day based on the entry index.
    """
    import json
    from pathlib import Path

    from app.core.config import get_settings

    settings = get_settings()
    exports = Path(settings.exports_dir)
    queue_path = exports / "daily_shorts_queue.json"

    if not queue_path.exists():
        return

    try:
        queue = json.loads(queue_path.read_text())
    except Exception:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_hour = datetime.now(timezone.utc).hour

    # Post schedule: slot 0 at 10:00, slot 1 at 14:00, slot 2 at 18:00 UTC
    posting_hours = [10, 14, 18]
    posted_count = 0

    for entry in queue:
        if entry.get("status") != "approved":
            continue
        if entry.get("generated_date") != today:
            continue
        if entry.get("posted_at"):
            continue

        # Determine this entry's posting slot
        todays_approved = [e for e in queue
                          if e.get("generated_date") == today
                          and e.get("status") in ("approved", "posted")]
        slot_idx = next(
            (i for i, e in enumerate(todays_approved) if e["id"] == entry["id"]),
            0
        )
        target_hour = posting_hours[slot_idx] if slot_idx < len(posting_hours) else posting_hours[-1]

        if current_hour < target_hour:
            continue  # Not time yet

        # Attempt to post
        video_path = Path(entry["video_path"])
        if not video_path.exists():
            logger.warning("Video file missing for posting: %s", entry["video_path"])
            entry["status"] = "error"
            entry["error"] = "Video file not found"
            continue

        try:
            from app.services.media.publishers import publish_video
            results = publish_video(
                video_path=str(video_path),
                title=entry.get("track_title", ""),
                description=entry.get("caption", ""),
                tags=entry.get("hashtags", {}).get("youtube", []),
                platforms=["youtube", "tiktok", "instagram"],
            )
            entry["status"] = "posted"
            entry["posted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry["platforms_posted"] = [r["platform"] for r in results if r.get("status") in ("uploaded", "manual")]
            entry["post_results"] = results
            posted_count += 1
            logger.info("Posted daily short: %s to %s", entry["filename"], entry["platforms_posted"])

            # Append to posting history
            try:
                history_path = exports / "posting_history.json"
                history = []
                if history_path.exists():
                    try:
                        history = json.loads(history_path.read_text())
                    except Exception:
                        pass
                for r in results:
                    history.append({
                        "entry_id": entry["id"],
                        "track_id": entry.get("track_id", ""),
                        "track_title": entry.get("track_title", ""),
                        "filename": entry.get("filename", ""),
                        "platform": r.get("platform", ""),
                        "status": r.get("status", ""),
                        "video_id": r.get("video_id", ""),
                        "url": r.get("url", ""),
                        "error": r.get("error", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    })
                history_path.write_text(json.dumps(history, indent=2))
            except Exception:
                logger.exception("Failed to save posting history entry")

        except Exception:
            logger.exception("Failed to post daily short: %s", entry["filename"])
            entry["status"] = "post_failed"
            entry["error"] = "Posting failed — will retry next cycle"

    # Save updated queue
    queue_path.write_text(json.dumps(queue, indent=2))
    if posted_count:
        logger.info("Daily shorts posting complete — %d videos posted", posted_count)


# ── Scheduler lifecycle ──────────────────────────────────────────────────


def _run_campaign_reminder():
    """Log today's campaign posts — acts as a daily content prompt."""
    import json
    from pathlib import Path

    campaign_file = Path(__file__).resolve().parents[1] / "static" / "presave_campaign.json"
    if not campaign_file.exists():
        logger.info("No campaign file found — skipping")
        return

    with open(campaign_file) as f:
        data = json.load(f)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for post in data.get("daily_posts", []):
        if post.get("day") == today:
            posted = post.get("posted", {})
            platforms = [p for p in ("tiktok", "instagram", "youtube", "twitter_x") if p in post]
            pending = [p for p in platforms if p not in posted]
            if pending:
                logger.info(
                    "CAMPAIGN REMINDER — %s | Theme: %s | Pending platforms: %s",
                    data.get("campaign", "?"),
                    post.get("theme", "?"),
                    ", ".join(pending),
                )
            else:
                logger.info("All campaign posts for today are marked as posted")
            return

    logger.info("No campaign posts scheduled for %s", today)


def _has_actionable_contact(contact: str) -> bool:
    """Check if a contact string is something we can actually send to."""
    if not contact:
        return False
    if "@" in contact:
        return True
    if ":" in contact and any(p in contact.lower() for p in ["instagram", "twitter", "tiktok", "discord"]):
        return True
    return False


def _run_contact_enrichment():
    """Scrape web pages to find email/social contacts for targets missing them."""
    from app.core.database import SessionLocal
    from app.models.playlist import Playlist
    from app.models.influencer import Influencer
    from app.services.outreach.contact_finder import enrich_contact

    db = SessionLocal()
    try:
        enriched = 0
        checked = 0

        # Enrich playlists
        playlists = db.query(Playlist).all()
        for p in playlists:
            if _has_actionable_contact(p.contact):
                continue
            if not p.url:
                continue
            checked += 1
            try:
                new_contact = enrich_contact(p.contact or "", p.url)
                if new_contact and new_contact != (p.contact or ""):
                    p.contact = new_contact
                    enriched += 1
                    logger.info("Enriched playlist %s: %s", p.name, new_contact)
            except Exception:
                logger.exception("Failed to enrich playlist %s", p.name)

        # Enrich influencers
        influencers = db.query(Influencer).all()
        for i in influencers:
            if _has_actionable_contact(i.contact):
                continue
            url = i.contact if i.contact and i.contact.startswith("http") else ""
            if not url:
                continue
            checked += 1
            try:
                new_contact = enrich_contact("", url)
                if new_contact and new_contact != (i.contact or ""):
                    i.contact = new_contact
                    enriched += 1
                    logger.info("Enriched influencer %s: %s", i.handle, new_contact)
            except Exception:
                logger.exception("Failed to enrich influencer %s", i.handle)

        db.commit()
        logger.info(
            "Contact enrichment complete — checked %d, enriched %d",
            checked, enriched,
        )
    except Exception:
        logger.exception("Error in contact enrichment job")
    finally:
        db.close()


def _run_news_search():
    """Search the web for mentions of NullRecords and My Evil Robot Army."""
    import json
    from pathlib import Path
    from app.services.outreach.news_search import search_news

    settings = get_settings()
    try:
        results = search_news(youtube_api_key=settings.youtube_api_key)

        workspace = Path(__file__).resolve().parents[3]
        news_file = workspace / "docs" / "news_articles.json"
        try:
            existing = json.loads(news_file.read_text()) if news_file.exists() else []
        except (json.JSONDecodeError, OSError):
            existing = []

        existing_urls = {a.get("url", "") for a in existing}
        new_count = 0
        for r in results:
            if r["url"] in existing_urls:
                continue
            existing.append(r)
            existing_urls.add(r["url"])
            new_count += 1

        if new_count > 0:
            news_file.write_text(json.dumps(existing, indent=2, default=str))

        logger.info(
            "News search complete — %d results, %d new articles saved (total: %d)",
            len(results), new_count, len(existing),
        )
    except Exception:
        logger.exception("Error in news search job")


def _run_brevo_crm_sync():
    """Pull contacts from Brevo into the local CRM database every 6 hours."""
    from app.core.database import SessionLocal
    from app.models.subscriber import Subscriber
    from app.services.crm.brevo_contacts import _headers

    import requests

    settings = get_settings()
    if not settings.smtp_key:
        logger.info("Brevo API key not configured — skipping CRM sync")
        return

    db = SessionLocal()
    try:
        imported = 0
        skipped = 0
        offset = 0
        limit = 50
        url = "https://api.brevo.com/v3/contacts"

        while True:
            resp = requests.get(
                url,
                headers=_headers(),
                params={"limit": limit, "offset": offset},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("Brevo sync — list failed: %s %s", resp.status_code, resp.text[:200])
                break

            data = resp.json()
            contacts = data.get("contacts", [])
            if not contacts:
                break

            for contact in contacts:
                email = contact.get("email", "").strip().lower()
                if not email:
                    continue

                existing = db.query(Subscriber).filter(Subscriber.email == email).first()
                if existing:
                    if not existing.brevo_contact_id and contact.get("id"):
                        existing.brevo_contact_id = str(contact["id"])
                    skipped += 1
                    continue

                attrs = contact.get("attributes", {})
                name_parts = [attrs.get("FIRSTNAME", ""), attrs.get("LASTNAME", "")]
                name = " ".join(p for p in name_parts if p).strip() or None

                sub = Subscriber(
                    email=email,
                    name=name,
                    source=attrs.get("SOURCE", "brevo-sync"),
                    tags="brevo-import",
                    status="active",
                    brevo_contact_id=str(contact.get("id", "")),
                )
                db.add(sub)
                imported += 1

            db.commit()
            offset += limit
            if offset >= data.get("count", 0):
                break

        logger.info("Brevo CRM sync complete — %d imported, %d skipped", imported, skipped)
    except Exception:
        logger.exception("Error in Brevo CRM sync job")
    finally:
        db.close()


def _run_dj_radio_discovery():
    """Discover DJs, radio stations, and shows for outreach."""
    from app.core.database import SessionLocal
    from app.models.influencer import Influencer
    from app.services.outreach.dj_radio_discovery import discover_dj_radio
    from app.services.outreach.scoring import score_relevance

    db = SessionLocal()
    try:
        raw = discover_dj_radio()
        added = 0
        for r in raw:
            existing = db.query(Influencer).filter_by(
                handle=r["handle"], platform=r["platform"]
            ).first()
            if existing:
                continue
            relevance = score_relevance(r["handle"], r.get("niche", ""))
            inf = Influencer(
                handle=r["handle"],
                platform=r["platform"],
                followers=r.get("followers", 0),
                niche=r.get("niche", ""),
                contact=r.get("contact", ""),
                relevance_score=relevance,
            )
            db.add(inf)
            added += 1

        db.commit()
        logger.info("DJ/Radio discovery complete — %d discovered, %d new added", len(raw), added)
    except Exception:
        logger.exception("Error in DJ/radio discovery job")
    finally:
        db.close()


def _run_auto_outreach():
    """Send outreach to high-scoring uncontacted targets with actionable contacts only."""
    from app.core.database import SessionLocal
    from app.models.playlist import Playlist
    from app.models.influencer import Influencer
    from app.models.outreach import OutreachLog
    from app.services.outreach.messaging import generate_outreach_message
    from app.services.outreach.followup import schedule_follow_up
    from app.services.outreach.email_sender import send_email

    settings = get_settings()
    min_score = settings.auto_outreach_min_score
    max_per_run = settings.auto_outreach_max_per_run
    db = SessionLocal()
    try:
        targets = []

        # Find uncontacted playlists above threshold
        playlists = (
            db.query(Playlist)
            .filter(
                Playlist.relevance_score >= min_score,
                Playlist.last_contacted.is_(None),
            )
            .order_by(Playlist.relevance_score.desc())
            .limit(max_per_run * 2)  # fetch extra since some may lack contacts
            .all()
        )
        for p in playlists:
            if not _has_actionable_contact(p.contact or ""):
                continue
            if len(targets) >= max_per_run:
                break
            targets.append(("playlist", p.id, p.name, p.contact or "",
                f"Playlist with {p.followers} followers, curated by {p.curator_name}"))

        # Find uncontacted influencers above threshold
        remaining = max_per_run - len(targets)
        if remaining > 0:
            influencers = (
                db.query(Influencer)
                .filter(
                    Influencer.relevance_score >= min_score,
                    Influencer.last_contacted.is_(None),
                )
                .order_by(Influencer.relevance_score.desc())
                .limit(remaining * 2)
                .all()
            )
            for i in influencers:
                if not _has_actionable_contact(i.contact or ""):
                    continue
                if len(targets) >= max_per_run:
                    break
                targets.append(("influencer", i.id, i.handle, i.contact or "",
                    f"{i.platform} creator in {i.niche} with {i.followers} followers"))

        if not targets:
            logger.info("Auto-outreach: no contactable uncontacted targets above %.1f score", min_score)
            return

        sent = 0
        skipped = 0
        for target_type, target_id, name, contact, context in targets:
            message = generate_outreach_message(name, target_type, context)
            subject = f"Hello from {settings.label_name}"

            result = {"sent": False, "message_id": None, "method": "logged"}
            if contact and "@" in contact:
                result = send_email(contact, subject, message)
            else:
                # Social-only contacts — log for manual DM
                skipped += 1

            log = OutreachLog(
                target_type=target_type,
                target_id=target_id,
                message=message,
                subject=subject,
                status="sent" if result["sent"] else "needs_dm",
                message_id=result.get("message_id"),
            )
            db.add(log)
            db.flush()

            # Mark contacted
            if target_type == "playlist":
                t = db.query(Playlist).get(target_id)
            else:
                t = db.query(Influencer).get(target_id)
            if t:
                t.last_contacted = datetime.now(timezone.utc)

            schedule_follow_up(db, log.id, days=5)

            if result["sent"]:
                sent += 1

        db.commit()
        logger.info(
            "Auto-outreach complete — %d emailed, %d need manual DM (score >= %.1f)",
            sent, skipped, min_score,
        )
    except Exception:
        logger.exception("Error in auto-outreach job")
    finally:
        db.close()


# ── Platform Stats Refresh ───────────────────────────────────────────────

def _run_platform_stats_refresh():
    """Fetch latest stats from YouTube for all posted shorts.

    Reads posting_history.json, collects YouTube video IDs,
    calls YouTube Data API v3 for stats, and updates each record.
    """
    import json
    from pathlib import Path

    from app.core.config import get_settings
    from app.services.social.youtube import fetch_video_stats

    settings = get_settings()
    exports = Path(settings.exports_dir)
    history_path = exports / "posting_history.json"

    if not history_path.exists():
        return

    try:
        history = json.loads(history_path.read_text())
    except Exception:
        return

    if not history:
        return

    # Collect YouTube video IDs → record indices
    yt_map: dict[str, list[int]] = {}
    for i, rec in enumerate(history):
        if rec.get("platform") == "youtube" and rec.get("video_id"):
            vid = rec["video_id"]
            yt_map.setdefault(vid, []).append(i)

    if not yt_map:
        logger.debug("Platform stats refresh — no YouTube video IDs to check")
        return

    stats = fetch_video_stats(list(yt_map.keys()))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = 0

    for vid, indices in yt_map.items():
        if vid in stats:
            for idx in indices:
                history[idx]["stats"] = stats[vid]
                history[idx]["stats_updated"] = now
                updated += 1

    if updated:
        history_path.write_text(json.dumps(history, indent=2))
        logger.info("Platform stats refresh — %d YouTube records updated (%d videos)", updated, len(stats))
    else:
        logger.debug("Platform stats refresh — no stats returned from YouTube API")


def _run_marketing_report(cadence: str):
    """Generate and save a marketing automation report."""
    from app.core.database import SessionLocal
    from app.services.marketing_reports import build_marketing_report, save_marketing_report

    db = SessionLocal()
    try:
        report = build_marketing_report(db, cadence, scheduler_status=get_scheduler_status())
        paths = save_marketing_report(report)
        logger.info("Marketing %s report generated: %s", cadence, paths["latest_html"])
    except Exception:
        logger.exception("Error generating %s marketing report", cadence)
    finally:
        db.close()


def _run_press_discovery():
    """Periodically discover new press contacts for music and books verticals."""
    from app.services.press.press_discovery import (
        discover_press_contacts,
        merge_press_contacts,
    )

    try:
        for vertical in ("music", "books"):
            logger.info("Press discovery starting for vertical: %s", vertical)
            new = discover_press_contacts(vertical_id=vertical, max_per_search=6)
            if new:
                result = merge_press_contacts(new)
                logger.info(
                    "Press discovery [%s]: %d added, %d total",
                    vertical, result["added"], result["total"],
                )
            else:
                logger.info("Press discovery [%s]: no new contacts", vertical)
    except Exception:
        logger.exception("Error in press discovery job")


def _run_press_enrichment():
    """Find emails for press contacts that are missing them."""
    from app.services.press.press_discovery import enrich_all_press_contacts

    try:
        enriched = enrich_all_press_contacts(max_enrich=10)
        logger.info("Press enrichment: %d contacts enriched with emails", enriched)
    except Exception:
        logger.exception("Error in press enrichment job")


def start_scheduler():
    """Create and start the APScheduler background scheduler."""
    global _scheduler
    settings = get_settings()

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled via config")
        return

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler already running")
        return

    _scheduler = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    _scheduler.add_job(
        _run_pending_followups,
        trigger=IntervalTrigger(minutes=settings.scheduler_followup_minutes),
        id="followup_check",
        name="Check & execute due follow-ups",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_discovery,
        trigger=IntervalTrigger(hours=settings.scheduler_discovery_hours),
        id="source_discovery",
        name="Discover new playlists & influencers",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_media_ingest,
        trigger=IntervalTrigger(hours=settings.scheduler_media_ingest_hours),
        id="media_ingest",
        name="Search, download & tag new media",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_campaign_reminder,
        trigger=IntervalTrigger(hours=8),
        id="campaign_reminder",
        name="Check campaign posts for today",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_auto_outreach,
        trigger=IntervalTrigger(hours=settings.auto_outreach_interval_hours),
        id="auto_outreach",
        name="Auto-outreach to uncontacted high-score targets",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_contact_enrichment,
        trigger=IntervalTrigger(hours=6),
        id="contact_enrichment",
        name="Find emails & social handles for targets",
        replace_existing=True,
    )

    # NEWS SCRAPER DISABLED — replaced by manual blog posts (2026-04-12)
    # _scheduler.add_job(
    #     _run_news_search,
    #     trigger=IntervalTrigger(hours=12),
    #     id="news_search",
    #     name="Search web for NullRecords/MERA mentions",
    #     replace_existing=True,
    # )

    _scheduler.add_job(
        _run_brevo_crm_sync,
        trigger=IntervalTrigger(hours=6),
        id="brevo_crm_sync",
        name="Sync Brevo contacts into local CRM",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_dj_radio_discovery,
        trigger=IntervalTrigger(hours=48),
        id="dj_radio_discovery",
        name="Discover DJs, radio stations & shows",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_daily_shorts_generation,
        trigger=IntervalTrigger(hours=24),
        id="daily_shorts_generation",
        name="Auto-generate 3 daily shorts from MERA catalog",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_press_discovery,
        trigger=IntervalTrigger(hours=48),
        id="press_discovery",
        name="Discover new press contacts for both verticals",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_press_enrichment,
        trigger=IntervalTrigger(hours=12),
        id="press_enrichment",
        name="Enrich press contacts — find emails",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_daily_shorts_posting,
        trigger=IntervalTrigger(hours=2),
        id="daily_shorts_posting",
        name="Post approved daily shorts on schedule",
        replace_existing=True,
    )

    _scheduler.add_job(
        _run_platform_stats_refresh,
        trigger=IntervalTrigger(hours=6),
        id="platform_stats_refresh",
        name="Refresh YouTube stats for posted shorts",
        replace_existing=True,
    )

    _scheduler.add_job(
        lambda: _run_marketing_report("hourly"),
        trigger=IntervalTrigger(minutes=settings.scheduler_report_hourly_minutes),
        id="marketing_report_hourly",
        name="Generate hourly marketing automation report",
        replace_existing=True,
    )

    _scheduler.add_job(
        lambda: _run_marketing_report("daily"),
        trigger=IntervalTrigger(hours=settings.scheduler_report_daily_hours),
        id="marketing_report_daily",
        name="Generate daily marketing automation report",
        replace_existing=True,
    )

    _scheduler.add_job(
        lambda: _run_marketing_report("weekly"),
        trigger=IntervalTrigger(hours=settings.scheduler_report_weekly_hours),
        id="marketing_report_weekly",
        name="Generate weekly marketing automation report",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — followups every %dm, discovery every %dh, "
        "media ingest every %dh, Brevo CRM sync every 6h, DJ/radio every 48h, "
        "daily shorts generation every 24h, daily shorts posting every 2h, "
        "platform stats refresh every 6h, marketing reports hourly/daily/weekly",
        settings.scheduler_followup_minutes,
        settings.scheduler_discovery_hours,
        settings.scheduler_media_ingest_hours,
    )


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def get_scheduler_status() -> dict:
    """Return current scheduler status and job info."""
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"running": True, "jobs": jobs}


def trigger_job(job_id: str) -> bool:
    """Manually trigger a scheduled job to run immediately."""
    if not _scheduler or not _scheduler.running:
        return False

    job = _scheduler.get_job(job_id)
    if not job:
        return False

    job.modify(next_run_time=datetime.now(timezone.utc))
    return True
