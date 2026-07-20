"""Web news search — finds mentions of NullRecords products and artists.

Uses DuckDuckGo HTML search (no API key) plus targeted site scraping
for music press, social media, and forums.
"""

import hashlib
import html as html_mod
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, unquote

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
_TIMEOUT = 15

# Search terms for our artists/label
SEARCH_QUERIES = [
    '"NullRecords"',
    '"Null Records" music',
    '"My Evil Robot Army"',
    '"My Evil Robot Army" band',
    '"MERA" "nu jazz"',
    '"nullrecords.com"',
    '"My Evil Robot Army" album',
    '"My Evil Robot Army" review',
    '"Drift TV" "NullRecords"',
    '"Drift TV" "Plex" "Jellyfin"',
    '"Drift TV" "Android TV"',
    '"Washoku Plus"',
    '"washokuplus.com"',
    '"Washoku Plus" app',
]


def _get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, **kwargs)
        r.raise_for_status()
        return r
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


def _clean(text: str) -> str:
    text = html_mod.unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _make_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}|{url}".encode()).hexdigest()[:12]


# ── DuckDuckGo HTML search ──────────────────────────────────────────────

def _search_ddg(query: str) -> list[dict[str, Any]]:
    """Search DuckDuckGo HTML for news mentions (no API key)."""
    results = []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = _get(url)
    if not resp:
        return results

    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.+?)</a>.*?'
        r'class="result__snippet"[^>]*>(.+?)</span>',
        resp.text, re.DOTALL,
    ):
        raw_link, title, snippet = m.group(1), _clean(m.group(2)), _clean(m.group(3))
        link = raw_link
        uddg = re.search(r'[?&]uddg=([^&]+)', raw_link)
        if uddg:
            link = unquote(uddg.group(1))

        results.append({
            "title": title[:200],
            "url": link,
            "excerpt": snippet[:500],
            "source": _extract_domain(link),
        })

    return results


def _extract_domain(url: str) -> str:
    m = re.search(r'https?://(?:www\.)?([^/]+)', url)
    return m.group(1) if m else "unknown"


# ── Reddit search ────────────────────────────────────────────────────────

def _search_reddit(query: str) -> list[dict[str, Any]]:
    """Search Reddit for mentions (public JSON API)."""
    results = []
    url = f"https://www.reddit.com/search.json?q={quote_plus(query)}&sort=new&limit=15"
    resp = _get(url)
    if not resp:
        return results

    try:
        children = resp.json().get("data", {}).get("children", [])
        for child in children:
            d = child.get("data", {})
            title = d.get("title", "")
            permalink = d.get("permalink", "")
            selftext = d.get("selftext", "")[:500]
            subreddit = d.get("subreddit_name_prefixed", "")
            created = d.get("created_utc", 0)
            results.append({
                "title": title,
                "url": f"https://www.reddit.com{permalink}" if permalink else "",
                "excerpt": selftext or f"Post in {subreddit}",
                "source": f"reddit/{subreddit}",
                "discovered_date": datetime.fromtimestamp(created, tz=timezone.utc).isoformat() if created else None,
            })
    except Exception:
        logger.debug("Reddit search parse failed for: %s", query)

    return results


# ── YouTube search ───────────────────────────────────────────────────────

def _search_youtube(query: str, api_key: str = "") -> list[dict[str, Any]]:
    """Search YouTube for video mentions."""
    results = []

    if api_key:
        url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&q={quote_plus(query)}&type=video&maxResults=10&key={api_key}"
        )
        resp = _get(url)
        if resp:
            try:
                items = resp.json().get("items", [])
                for item in items:
                    snippet = item.get("snippet", {})
                    vid = item.get("id", {}).get("videoId", "")
                    results.append({
                        "title": snippet.get("title", ""),
                        "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                        "excerpt": snippet.get("description", "")[:500],
                        "source": f"youtube/{snippet.get('channelTitle', 'unknown')}",
                        "discovered_date": snippet.get("publishedAt"),
                    })
            except Exception:
                pass
        return results

    # Fallback: HTML scrape (no key)
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    resp = _get(url)
    if not resp:
        return results

    import json as json_mod
    match = re.search(r'var ytInitialData = ({.*?});\s*</script>', resp.text, re.DOTALL)
    if not match:
        return results

    try:
        data = json_mod.loads(match.group(1))
        sections = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                vr = item.get("videoRenderer")
                if not vr:
                    continue
                title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                vid = vr.get("videoId", "")
                desc_runs = vr.get("detailedMetadataSnippets", [{}])[0].get("snippetText", {}).get("runs", [])
                desc = "".join(r.get("text", "") for r in desc_runs)[:500]
                channel = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                results.append({
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                    "excerpt": desc,
                    "source": f"youtube/{channel}",
                })
    except (KeyError, TypeError, IndexError):
        pass

    return results


# ── Bandcamp search ──────────────────────────────────────────────────────

def _search_bandcamp(query: str) -> list[dict[str, Any]]:
    """Search Bandcamp for album/track mentions."""
    results = []
    for item_type in ["t", "a"]:  # tracks and albums
        url = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type={item_type}"
        resp = _get(url)
        if not resp:
            continue
        for m in re.finditer(
            r'class="heading">\s*<a href="(https?://[^"]+)"[^>]*>([^<]+)</a>.*?'
            r'class="subhead">\s*([^<]*?)(?:</div>|<span)',
            resp.text, re.DOTALL,
        ):
            link, title, sub = m.group(1), _clean(m.group(2)), _clean(m.group(3))
            results.append({
                "title": title,
                "url": link,
                "excerpt": sub[:500],
                "source": "bandcamp",
            })
    return results


# ── Aggregate search ────────────────────────────────────────────────────

def search_news(youtube_api_key: str = "", extra_queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Run all search queries across all sources. Returns deduplicated results."""
    all_results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    queries = list(SEARCH_QUERIES)
    if extra_queries:
        queries.extend(extra_queries)

    for query in queries:
        logger.info("News search: %s", query)

        # DuckDuckGo
        for r in _search_ddg(query):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)
        time.sleep(2)  # Rate limit between searches

        # Reddit
        for r in _search_reddit(query):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

        # YouTube
        for r in _search_youtube(query, youtube_api_key):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

        # Bandcamp
        for r in _search_bandcamp(query):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    # Classify relevance
    for r in all_results:
        r["id"] = _make_id(r.get("title", ""), r.get("url", ""))
        r["discovered_date"] = r.get("discovered_date") or datetime.now(timezone.utc).isoformat()
        r["status"] = "needs_verification"
        r["sentiment"] = "neutral"
        r["article_type"] = _classify_type(r)
        r["artist_mentioned"] = _extract_artists(r)

    logger.info("News search complete — %d unique results across %d queries", len(all_results), len(queries))
    return all_results


def _classify_type(result: dict) -> str:
    """Classify article type from source and content."""
    source = result.get("source", "").lower()
    title = result.get("title", "").lower()
    if "reddit" in source:
        return "social_mention"
    if "youtube" in source:
        return "video"
    if "bandcamp" in source:
        return "release"
    if any(w in title for w in ["review", "rating", "stars"]):
        return "review"
    if any(w in title for w in ["interview", "q&a", "talks"]):
        return "interview"
    return "mention"


def _extract_artists(result: dict) -> list[str]:
    """Extract which NullRecords artists or products are mentioned."""
    text = f"{result.get('title', '')} {result.get('excerpt', '')}".lower()
    artists = []
    if "my evil robot army" in text or "mera" in text:
        artists.append("My Evil Robot Army")
    if "nullrecords" in text or "null records" in text:
        artists.append("NullRecords")
    if "drift tv" in text:
        artists.append("Drift TV")
    if "washoku plus" in text or "washokuplus.com" in text:
        artists.append("Washoku Plus")
    return artists or ["NullRecords"]
