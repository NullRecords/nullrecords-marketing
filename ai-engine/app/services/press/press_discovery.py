"""Press contact discovery — find press outlets, podcasts, reviewers, influencers.

Uses DuckDuckGo HTML search (no API key), Google Custom Search (optional),
and web scraping to find relevant press contacts per vertical.
Integrates with the existing contact_finder for email extraction.
"""

import hashlib
import html as html_mod
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

from app.core.config import get_settings

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
_TIMEOUT = 15
_RATE_LIMIT_S = 2.5  # seconds between search requests


def _get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, **kwargs)
        r.raise_for_status()
        return r
    except Exception as exc:
        log.debug("GET %s failed: %s", url, exc)
        return None


def _clean(text: str) -> str:
    text = html_mod.unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _contact_hash(name: str, url: str) -> str:
    return hashlib.md5(f"{name}:{url}".lower().encode()).hexdigest()[:12]


# ── DuckDuckGo HTML search ──────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """Search DuckDuckGo and return list of {title, url, snippet}."""
    results = []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = _get(url)
    if not resp:
        return results

    # Parse result blocks
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.+?)</a>.*?'
        r'class="result__snippet"[^>]*>(.+?)</(?:a|td|div)',
        resp.text, re.DOTALL,
    ):
        raw_url = m.group(1)
        title = _clean(m.group(2))
        snippet = _clean(m.group(3))

        # DDG wraps URLs through a redirect
        if "uddg=" in raw_url:
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(raw_url).query)
            raw_url = qs.get("uddg", [raw_url])[0]

        if not title or not raw_url:
            continue

        results.append({"title": title, "url": raw_url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ── Google Custom Search (optional, if keys configured) ─────────────────

def _google_search(query: str, max_results: int = 10) -> list[dict]:
    """Search via Google Custom Search JSON API (requires API key + CX)."""
    settings = get_settings()
    api_key = settings.youtube_api_key  # shares the same Google API key
    # Google Custom Search requires a separate cx; skip if not configured
    cx = getattr(settings, "google_cse_cx", "")
    if not api_key or not cx:
        return []

    results = []
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": query, "num": min(max_results, 10)},
            timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
    except Exception:
        log.debug("Google CSE search failed for: %s", query)

    return results


# ── Contact classification ──────────────────────────────────────────────

# Keywords that indicate press/media relevance by type
_TYPE_SIGNALS = {
    "radio_station": ["radio", "fm", "am", "broadcast", "airplay", "on-air"],
    "podcast": ["podcast", "episode", "listen", "apple podcasts", "spotify podcast"],
    "blog": ["blog", "review", "write-up", "article", "feature", "editorial"],
    "magazine": ["magazine", "publication", "issue", "print", "zine"],
    "playlist_curator": ["playlist", "curator", "curated", "spotify playlist"],
    "influencer": ["influencer", "creator", "youtuber", "tiktoker", "instagram"],
    "newsletter": ["newsletter", "substack", "mailchimp", "subscribe"],
    "book_club": ["book club", "reading group", "readers", "discussion"],
    "library": ["library", "librarian", "acquisition", "collection"],
    "app_reviewer": ["app review", "apps", "app store", "ios app", "android app", "google tv", "apple tv"],
    "food_writer": ["food", "recipe", "meal planning", "nutrition", "dietitian", "healthy eating"],
    "home_theater": ["home theater", "plex", "jellyfin", "cord cutter", "android tv", "apple tv"],
}


def _classify_contact(title: str, snippet: str, url: str) -> str:
    """Classify a discovered contact by type based on text signals."""
    combined = f"{title} {snippet} {url}".lower()
    scores = {}
    for ctype, keywords in _TYPE_SIGNALS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score:
            scores[ctype] = score
    if scores:
        return max(scores, key=scores.get)
    return "publication"


def _classify_vertical(title: str, snippet: str) -> str:
    """Guess which vertical (music or books) a contact belongs to."""
    combined = f"{title} {snippet}".lower()
    book_signals = ["book", "novel", "literature", "author", "read", "publish", "ebook", "library"]
    drift_signals = ["drift tv", "plex", "jellyfin", "android tv", "google tv", "apple tv", "home theater", "cord cutter", "ambient tv"]
    washoku_signals = ["washoku", "meal planning", "nutrition", "healthy eating", "dietitian", "recipe", "food app", "mindful eating"]
    music_signals = ["music", "album", "jazz", "electronic", "radio", "dj", "playlist", "track"]
    book_score = sum(1 for s in book_signals if s in combined)
    drift_score = sum(1 for s in drift_signals if s in combined)
    washoku_score = sum(1 for s in washoku_signals if s in combined)
    music_score = sum(1 for s in music_signals if s in combined)
    app_scores = {"drift_tv": drift_score, "washoku_plus": washoku_score}
    best_app = max(app_scores, key=app_scores.get)
    if app_scores[best_app] > max(book_score, music_score):
        return best_app
    if book_score > music_score:
        return "books"
    return "music"


# ── Main discovery function ─────────────────────────────────────────────

def discover_press_contacts(
    vertical_id: str | None = None,
    max_per_search: int = 8,
    searches: list[str] | None = None,
) -> list[dict]:
    """Discover new press contacts for a vertical (music, books, or both).

    Uses brand profile search terms, classifies results, deduplicates.
    Returns list of new contact dicts ready for storage.
    """
    from app.services.press.brand_profile import load_brand_profile

    profile = load_brand_profile()
    discovery_searches = profile.get("discovery_searches", {})

    if searches:
        all_searches = searches
    elif vertical_id and vertical_id in discovery_searches:
        all_searches = discovery_searches[vertical_id]
    else:
        all_searches = []
        for v_searches in discovery_searches.values():
            all_searches.extend(v_searches)

    # Load existing contacts for dedup
    existing_hashes = set()
    contacts_path = _press_contacts_path()
    if contacts_path.exists():
        try:
            for c in json.loads(contacts_path.read_text()):
                existing_hashes.add(c.get("hash", ""))
        except Exception:
            pass

    new_contacts = []
    seen_urls = set()

    for query in all_searches:
        # Try Google first, fall back to DuckDuckGo
        results = _google_search(query, max_per_search)
        if not results:
            results = _ddg_search(query, max_per_search)
            time.sleep(_RATE_LIMIT_S)

        for r in results:
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            h = _contact_hash(r["title"], url)
            if h in existing_hashes:
                continue
            existing_hashes.add(h)

            contact_type = _classify_contact(r["title"], r["snippet"], url)
            vertical = vertical_id or _classify_vertical(r["title"], r["snippet"])

            new_contacts.append({
                "hash": h,
                "name": r["title"][:120],
                "url": url,
                "snippet": r["snippet"][:300],
                "type": contact_type,
                "vertical": vertical,
                "email": "",
                "status": "discovered",
                "confidence_score": 0.0,
                "discovered_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "discovered_via": query,
                "last_contacted": None,
                "outreach_count": 0,
                "campaigns": [],
            })

    log.info("Press discovery: %d new contacts from %d searches", len(new_contacts), len(all_searches))
    return new_contacts


def _press_contacts_path() -> Path:
    return Path(get_settings().exports_dir) / "press" / "press_contacts.json"


def load_press_contacts() -> list[dict]:
    """Load all press contacts."""
    p = _press_contacts_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return []


def save_press_contacts(contacts: list[dict]) -> None:
    """Save press contacts list."""
    p = _press_contacts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(contacts, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_press_contacts(new_contacts: list[dict]) -> dict:
    """Merge new contacts into existing list, deduplicating by hash.

    Returns {"added": int, "total": int}.
    """
    existing = load_press_contacts()
    existing_hashes = {c["hash"] for c in existing}
    added = 0
    for c in new_contacts:
        if c["hash"] not in existing_hashes:
            existing.append(c)
            existing_hashes.add(c["hash"])
            added += 1
    save_press_contacts(existing)
    return {"added": added, "total": len(existing)}


def enrich_press_contact(contact: dict) -> dict:
    """Try to find an email address for a press contact by scraping their URL."""
    if contact.get("email"):
        return contact

    try:
        from app.services.outreach.contact_finder import find_contact_for_url
        result = find_contact_for_url(contact["url"])
        if result and result.get("email"):
            contact["email"] = result["email"]
            contact["status"] = "enriched"
            log.info("Enriched press contact: %s → %s", contact["name"], contact["email"])
    except Exception:
        log.debug("Could not enrich contact: %s", contact["name"])

    return contact


def enrich_all_press_contacts(max_enrich: int = 20) -> int:
    """Enrich contacts that are missing emails. Returns count enriched."""
    contacts = load_press_contacts()
    enriched = 0
    for c in contacts:
        if enriched >= max_enrich:
            break
        if not c.get("email") and c.get("url"):
            enrich_press_contact(c)
            if c.get("email"):
                enriched += 1
            time.sleep(1)  # rate limit scraping
    save_press_contacts(contacts)
    return enriched
