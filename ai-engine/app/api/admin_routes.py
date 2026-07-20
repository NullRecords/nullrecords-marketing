"""Admin API routes — powers the web-based admin dashboard."""

import csv
import io
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.media import MediaAsset
from app.models.playlist import Playlist
from app.models.influencer import Influencer
from app.models.outreach import OutreachLog
from app.models.credentials import APICredential
from app.services.ai_provider import ai_status

router = APIRouter(prefix="/admin/api", tags=["admin"])

# File extensions → source type and media type mapping
_EXT_MAP = {
    ".wav": ("upload", "audio"), ".mp3": ("upload", "audio"), ".flac": ("upload", "audio"),
    ".ogg": ("upload", "audio"), ".aac": ("upload", "audio"),
    ".mp4": ("generated", "video"), ".mov": ("generated", "video"),
    ".webm": ("generated", "video"), ".avi": ("generated", "video"),
    ".jpg": ("upload", "image"), ".jpeg": ("upload", "image"),
    ".png": ("upload", "image"), ".gif": ("upload", "image"),
    ".webp": ("upload", "image"),
}

# Directories to scan (relative to ai-engine/)
_SCAN_DIRS = {
    "exports/audio_uploads": "upload",
    "exports/image_uploads": "upload",
    "exports/videos": "generated",
    "exports/social": "generated",
    "media-library/images": "upload",
    "media-library/pexels": "pexels",
    "media-library/internet_archive": "internet_archive",
    "media-library/test_clips": "generated",
}


@router.post("/scan-disk")
def scan_disk(db: Session = Depends(get_db)):
    """Scan export/media directories and register any files not yet in the DB."""
    base = Path(__file__).resolve().parent.parent.parent  # ai-engine/
    added = 0
    skipped = 0

    for rel_dir, default_source in _SCAN_DIRS.items():
        scan_path = base / rel_dir
        if not scan_path.is_dir():
            continue
        for f in scan_path.iterdir():
            if not f.is_file() or f.name.startswith("."):
                continue
            ext = f.suffix.lower()
            source, _ = _EXT_MAP.get(ext, (default_source, "unknown"))

            # Check if already registered by local_path
            local = str(f.relative_to(base))
            exists = db.query(MediaAsset).filter_by(local_path=local).first()
            if exists:
                skipped += 1
                continue

            title = f.stem.replace("_", " ").replace("-", " ").title()
            url = f"/{local}"  # served via /exports/ or /media-library/ mounts
            asset = MediaAsset(
                source=source,
                source_id=f.name,
                title=title,
                url=url,
                local_path=local,
                tags="[]",
                mood="",
                style="",
                duration=None,
                downloaded=True,
            )
            db.add(asset)
            added += 1

    db.commit()
    return {"added": added, "skipped": skipped}


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """Aggregate counts and recent activity for the dashboard overview."""
    settings = get_settings()

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    media_total = db.query(func.count(MediaAsset.id)).scalar() or 0
    media_downloaded = db.query(func.count(MediaAsset.id)).filter(MediaAsset.downloaded.is_(True)).scalar() or 0
    media_tagged = db.query(func.count(MediaAsset.id)).filter(MediaAsset.mood != "", MediaAsset.mood.isnot(None)).scalar() or 0

    playlists_total = db.query(func.count(Playlist.id)).scalar() or 0
    influencers_total = db.query(func.count(Influencer.id)).scalar() or 0

    outreach_total = db.query(func.count(OutreachLog.id)).scalar() or 0
    outreach_sent = db.query(func.count(OutreachLog.id)).filter(OutreachLog.status.in_(["sent", "delivered", "opened", "clicked"])).scalar() or 0
    outreach_opened = db.query(func.count(OutreachLog.id)).filter(OutreachLog.opened_at.isnot(None)).scalar() or 0
    outreach_clicked = db.query(func.count(OutreachLog.id)).filter(OutreachLog.clicked_at.isnot(None)).scalar() or 0
    outreach_logged = db.query(func.count(OutreachLog.id)).filter(OutreachLog.status == "logged").scalar() or 0
    outreach_delivered = db.query(func.count(OutreachLog.id)).filter(OutreachLog.status == "delivered").scalar() or 0
    outreach_pending = db.query(func.count(OutreachLog.id)).filter(
        OutreachLog.follow_up_date.isnot(None),
        OutreachLog.follow_up_date <= now,
        OutreachLog.status != "followed_up",
    ).scalar() or 0

    creds = db.query(APICredential.service).all()
    configured_services = [c.service for c in creds]

    # Config status
    ai = ai_status()
    api_status = {
        "local_ai": ai["local_ai"],
        "openai": ai["openai"],
        "pexels": bool(settings.pexels_api_key),
        "spotify": bool(settings.spotify_client_id and settings.spotify_client_secret),
        "youtube": bool(settings.youtube_api_key),
    }

    return {
        "media": {"total": media_total, "downloaded": media_downloaded, "tagged": media_tagged},
        "outreach": {
            "playlists": playlists_total,
            "influencers": influencers_total,
            "messages_sent": outreach_sent,
            "delivered": outreach_delivered,
            "emails_opened": outreach_opened,
            "links_clicked": outreach_clicked,
            "logged": outreach_logged,
            "followups_due": outreach_pending,
            "total_logs": outreach_total,
        },
        "api_status": api_status,
        "ai_status": ai,
        "configured_services": configured_services,
        "label": {"name": settings.label_name, "genre": settings.label_genre},
    }


@router.get("/media")
def list_media(limit: int = 50, offset: int = 0, source: str = Query(default="", description="Filter by source type"), db: Session = Depends(get_db)):
    """Paginated media asset list with optional source filter."""
    q = db.query(MediaAsset)
    if source:
        q = q.filter(MediaAsset.source == source)
    total = q.count()
    items = (
        q.order_by(MediaAsset.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": m.id,
                "source": m.source,
                "title": m.title,
                "url": m.url,
                "local_path": m.local_path or "",
                "tags": m.tags,
                "mood": m.mood,
                "style": m.style,
                "duration": m.duration or 0,
                "downloaded": m.downloaded,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in items
        ],
    }


@router.get("/playlists")
def list_playlists(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """Paginated playlist list."""
    total = db.query(func.count(Playlist.id)).scalar() or 0
    items = (
        db.query(Playlist)
        .order_by(Playlist.relevance_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "name": p.name,
                "platform": p.platform,
                "curator_name": p.curator_name,
                "followers": p.followers,
                "contact": p.contact or "",
                "url": p.url,
                "relevance_score": p.relevance_score,
                "last_contacted": p.last_contacted.isoformat() if p.last_contacted else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in items
        ],
    }


@router.get("/influencers")
def list_influencers(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """Paginated influencer list."""
    total = db.query(func.count(Influencer.id)).scalar() or 0
    items = (
        db.query(Influencer)
        .order_by(Influencer.relevance_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": i.id,
                "handle": i.handle,
                "platform": i.platform,
                "followers": i.followers,
                "niche": i.niche,
                "contact": i.contact or "",
                "relevance_score": i.relevance_score,
                "last_contacted": i.last_contacted.isoformat() if i.last_contacted else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ],
    }


@router.get("/outreach-log")
def outreach_log(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """Paginated outreach log."""
    total = db.query(func.count(OutreachLog.id)).scalar() or 0
    items = (
        db.query(OutreachLog)
        .order_by(OutreachLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": o.id,
                "target_type": o.target_type,
                "target_id": o.target_id,
                "message": o.message[:200] if o.message else "",
                "status": o.status,
                "follow_up_date": o.follow_up_date.isoformat() if o.follow_up_date else None,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in items
        ],
    }


@router.get("/reports/summary")
def report_summary(db: Session = Depends(get_db)):
    """Generate an on-the-fly summary report of all AI Engine activity."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Media stats
    media_total = db.query(func.count(MediaAsset.id)).scalar() or 0
    media_by_source = (
        db.query(MediaAsset.source, func.count(MediaAsset.id))
        .group_by(MediaAsset.source)
        .all()
    )

    # Outreach stats
    outreach_by_status = (
        db.query(OutreachLog.status, func.count(OutreachLog.id))
        .group_by(OutreachLog.status)
        .all()
    )
    playlists_by_platform = (
        db.query(Playlist.platform, func.count(Playlist.id))
        .group_by(Playlist.platform)
        .all()
    )

    # Top playlists
    top_playlists = (
        db.query(Playlist)
        .order_by(Playlist.relevance_score.desc())
        .limit(5)
        .all()
    )

    # Top influencers
    top_influencers = (
        db.query(Influencer)
        .order_by(Influencer.relevance_score.desc())
        .limit(5)
        .all()
    )

    # Recent activity (last 7 days)
    week_ago = now - timedelta(days=7)
    recent_outreach = db.query(func.count(OutreachLog.id)).filter(
        OutreachLog.created_at >= week_ago
    ).scalar() or 0
    recent_media = db.query(func.count(MediaAsset.id)).filter(
        MediaAsset.created_at >= week_ago
    ).scalar() or 0

    return {
        "generated_at": now.isoformat(),
        "label": {"name": settings.label_name, "genre": settings.label_genre},
        "media": {
            "total": media_total,
            "by_source": {s: c for s, c in media_by_source},
            "added_last_7d": recent_media,
        },
        "outreach": {
            "by_status": {s: c for s, c in outreach_by_status},
            "sent_last_7d": recent_outreach,
            "playlists_by_platform": {p: c for p, c in playlists_by_platform},
            "top_playlists": [
                {"name": p.name, "platform": p.platform, "score": p.relevance_score}
                for p in top_playlists
            ],
            "top_influencers": [
                {"handle": i.handle, "platform": i.platform, "score": i.relevance_score}
                for i in top_influencers
            ],
        },
    }


@router.get("/outreach")
def list_outreach_log(
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    status: str = Query(default="", description="Filter by status"),
    db: Session = Depends(get_db),
):
    """Paginated outreach log with optional status filter."""
    q = db.query(OutreachLog).order_by(OutreachLog.created_at.desc())
    if status:
        q = q.filter(OutreachLog.status == status)
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "status": log.status,
                "subject": getattr(log, "subject", None) or "",
                "message": (log.message or "")[:200],
                "message_id": getattr(log, "message_id", None) or "",
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "follow_up_date": log.follow_up_date.isoformat() if log.follow_up_date else None,
                "delivered_at": getattr(log, "delivered_at", None),
                "opened_at": getattr(log, "opened_at", None),
                "clicked_at": getattr(log, "clicked_at", None),
            }
            for log in items
        ],
    }


# ---------------------------------------------------------------------------
# Unified Contacts API
# ---------------------------------------------------------------------------

def _classify_contact(raw: str) -> dict:
    """Parse a raw contact string into type, value, and capability flags."""
    raw = (raw or "").strip()
    if not raw:
        return {"contact_method": "none", "contact_value": "", "has_email": False, "has_social": False, "can_send": False}

    has_email = "@" in raw and not raw.startswith("instagram:") and not raw.startswith("twitter:")
    has_social = any(raw.startswith(p) for p in ("instagram:", "twitter:", "tiktok:", "discord:", "reddit_modmail:"))

    if has_email:
        method = "email"
        value = raw
    elif raw.startswith("instagram:"):
        method = "instagram"
        value = raw.replace("instagram:", "").strip()
    elif raw.startswith("twitter:"):
        method = "twitter"
        value = raw.replace("twitter:", "").strip()
    elif raw.startswith("tiktok:"):
        method = "tiktok"
        value = raw.replace("tiktok:", "").strip()
    elif raw.startswith("discord:"):
        method = "discord"
        value = raw.replace("discord:", "").strip()
    elif raw.startswith("reddit_modmail:"):
        method = "reddit_modmail"
        value = raw.replace("reddit_modmail:", "").strip()
    elif "youtube.com" in raw or "youtu.be" in raw:
        method = "youtube_url"
        value = raw
    else:
        method = "url"
        value = raw

    can_send = has_email or has_social
    return {"contact_method": method, "contact_value": value, "has_email": has_email, "has_social": has_social, "can_send": can_send}


def _get_outreach_status(db: Session, target_type: str, target_id: int) -> dict:
    """Get the latest outreach status for a target."""
    log = (
        db.query(OutreachLog)
        .filter(OutreachLog.target_type == target_type, OutreachLog.target_id == target_id)
        .order_by(OutreachLog.created_at.desc())
        .first()
    )
    if not log:
        return {"outreach_status": "not_contacted", "last_outreach_date": None, "messages_sent": 0, "follow_up_due": None}

    count = (
        db.query(func.count(OutreachLog.id))
        .filter(OutreachLog.target_type == target_type, OutreachLog.target_id == target_id)
        .scalar() or 0
    )
    return {
        "outreach_status": log.status,
        "last_outreach_date": log.created_at.isoformat() if log.created_at else None,
        "messages_sent": count,
        "follow_up_due": log.follow_up_date.isoformat() if log.follow_up_date else None,
    }


@router.get("/contacts")
def list_contacts(
    contact_type: str = Query(default="", description="Filter: playlist, influencer, or empty for all"),
    platform: str = Query(default="", description="Filter by platform"),
    has_email: str = Query(default="", description="true/false — filter by email availability"),
    has_social: str = Query(default="", description="true/false — filter by social DM capability"),
    can_send: str = Query(default="", description="true/false — filter by any outreach capability"),
    contact_method: str = Query(default="", description="Filter by contact method: email, instagram, twitter, discord, reddit_modmail, url, none"),
    outreach_status: str = Query(default="", description="not_contacted, sent, delivered, opened, logged"),
    min_score: float = Query(default=0.0, description="Minimum relevance score"),
    sort: str = Query(default="score", description="Sort by: score, name, contacted, platform"),
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Unified contacts list combining playlists and influencers with outreach status."""
    contacts = []

    # Build playlist contacts
    if contact_type in ("", "playlist"):
        pq = db.query(Playlist)
        if platform:
            pq = pq.filter(Playlist.platform == platform)
        if min_score > 0:
            pq = pq.filter(Playlist.relevance_score >= min_score)
        for p in pq.all():
            c = _classify_contact(p.contact)
            o = _get_outreach_status(db, "playlist", p.id)
            contacts.append({
                "id": p.id,
                "type": "playlist",
                "name": p.name,
                "platform": p.platform,
                "curator": p.curator_name or "",
                "followers": p.followers,
                "niche": "",
                "url": p.url,
                "raw_contact": p.contact or "",
                **c,
                **o,
                "relevance_score": p.relevance_score,
                "last_contacted": p.last_contacted.isoformat() if p.last_contacted else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

    # Build influencer contacts
    if contact_type in ("", "influencer"):
        iq = db.query(Influencer)
        if platform:
            iq = iq.filter(Influencer.platform == platform)
        if min_score > 0:
            iq = iq.filter(Influencer.relevance_score >= min_score)
        for i in iq.all():
            c = _classify_contact(i.contact)
            o = _get_outreach_status(db, "influencer", i.id)
            contacts.append({
                "id": i.id,
                "type": "influencer",
                "name": i.handle,
                "platform": i.platform,
                "curator": "",
                "followers": i.followers,
                "niche": i.niche or "",
                "url": i.contact if i.contact and i.contact.startswith("http") else "",
                "raw_contact": i.contact or "",
                **c,
                **o,
                "relevance_score": i.relevance_score,
                "last_contacted": i.last_contacted.isoformat() if i.last_contacted else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            })

    # Apply boolean filters
    if has_email == "true":
        contacts = [c for c in contacts if c["has_email"]]
    elif has_email == "false":
        contacts = [c for c in contacts if not c["has_email"]]

    if has_social == "true":
        contacts = [c for c in contacts if c["has_social"]]
    elif has_social == "false":
        contacts = [c for c in contacts if not c["has_social"]]

    if can_send == "true":
        contacts = [c for c in contacts if c["can_send"]]
    elif can_send == "false":
        contacts = [c for c in contacts if not c["can_send"]]

    if contact_method:
        contacts = [c for c in contacts if c["contact_method"] == contact_method]

    if outreach_status:
        contacts = [c for c in contacts if c["outreach_status"] == outreach_status]

    # Sort
    sort_keys = {
        "score": lambda c: (-c["relevance_score"], c["name"].lower()),
        "name": lambda c: c["name"].lower(),
        "contacted": lambda c: (c["last_contacted"] or "", c["name"].lower()),
        "platform": lambda c: (c["platform"], -c["relevance_score"]),
    }
    contacts.sort(key=sort_keys.get(sort, sort_keys["score"]))

    total = len(contacts)
    page = contacts[offset:offset + limit]

    # Summary stats
    all_emails = sum(1 for c in contacts if c["has_email"])
    all_social = sum(1 for c in contacts if c["has_social"])
    all_sendable = sum(1 for c in contacts if c["can_send"])
    all_no_contact = sum(1 for c in contacts if not c["can_send"])
    all_not_contacted = sum(1 for c in contacts if c["outreach_status"] == "not_contacted")
    platforms = {}
    for c in contacts:
        platforms[c["platform"]] = platforms.get(c["platform"], 0) + 1

    return {
        "total": total,
        "summary": {
            "with_email": all_emails,
            "with_social": all_social,
            "can_send": all_sendable,
            "needs_research": all_no_contact,
            "not_yet_contacted": all_not_contacted,
            "by_platform": platforms,
        },
        "items": page,
    }


@router.get("/contacts/export")
def export_contacts(
    format: str = Query(default="csv", description="csv or json"),
    contact_type: str = Query(default=""),
    platform: str = Query(default=""),
    has_email: str = Query(default=""),
    has_social: str = Query(default=""),
    can_send: str = Query(default=""),
    min_score: float = Query(default=0.0),
    db: Session = Depends(get_db),
):
    """Export contacts as CSV or JSON for sharing with team/intern."""
    # Reuse the list endpoint logic
    result = list_contacts(
        contact_type=contact_type, platform=platform,
        has_email=has_email, has_social=has_social, can_send=can_send,
        contact_method="", outreach_status="", min_score=min_score, sort="score",
        limit=10000, offset=0, db=db,
    )
    items = result["items"]

    if format == "json":
        import json
        content = json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "total": len(items), "contacts": items}, indent=2, default=str)
        return StreamingResponse(
            io.StringIO(content),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=nullrecords_contacts_{datetime.now().strftime('%Y%m%d')}.json"},
        )

    # CSV export
    columns = [
        "id", "name", "type", "platform", "curator", "followers", "niche",
        "url", "contact_method", "contact_value", "has_email", "has_social",
        "can_send", "relevance_score", "outreach_status", "messages_sent",
        "last_outreach_date", "follow_up_due", "raw_contact",
        "last_contacted", "created_at", "research_notes",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        item["research_notes"] = ""  # blank column for intern to fill in
        writer.writerow(item)

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nullrecords_contacts_{datetime.now().strftime('%Y%m%d')}.csv"},
    )
