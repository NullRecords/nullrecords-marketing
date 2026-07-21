"""YouTube upload service — OAuth2 / Service Account + YouTube Data API v3.

Supports two authentication methods:
1. Service Account (preferred): Uses GOOGLE_SERVICE_ACCOUNT_FILE from .env
2. OAuth2 User Flow: Uses YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET

Tokens are persisted to disk so re-auth is only needed once.
"""

import json
import logging
import time
from pathlib import Path

import requests

from app.core.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_FILE = Path(get_settings().exports_dir).parent / ".youtube_tokens.json"
_SA_TOKEN_CACHE: dict = {}  # In-memory cache for service account tokens

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _get_service_account_path() -> Path | None:
    """Get service account JSON path from settings or common locations."""
    settings = get_settings()
    
    # Check explicit setting first
    sa_file = getattr(settings, 'google_service_account_file', None)
    if sa_file:
        p = Path(sa_file)
        if p.exists():
            return p
    
    # Check common locations
    common_paths = [
        Path(settings.exports_dir).parent.parent / "dashboard" / "nullrecords-ga4-credentials.json",
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
    ]
    for p in common_paths:
        if p.exists():
            return p
    return None


def _get_service_account_token() -> str | None:
    """Get access token using service account credentials via google-auth library."""
    global _SA_TOKEN_CACHE
    
    sa_path = _get_service_account_path()
    if not sa_path:
        return None
    
    # Check cache
    cached = _SA_TOKEN_CACHE.get("token")
    if cached and _SA_TOKEN_CACHE.get("expires_at", 0) > time.time() + 60:
        return cached
    
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        
        credentials = service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=SCOPES
        )
        
        # Refresh to get access token
        credentials.refresh(Request())
        
        _SA_TOKEN_CACHE["token"] = credentials.token
        _SA_TOKEN_CACHE["expires_at"] = time.time() + 3500  # ~1 hour minus buffer
        
        log.info("Service account token obtained successfully")
        return credentials.token
        
    except ImportError:
        log.warning("google-auth not installed - run: pip install google-auth")
        return None
    except Exception as e:
        log.warning("Service account auth failed: %s", e)
        return None


def _load_tokens() -> dict | None:
    if _TOKEN_FILE.exists():
        return json.loads(_TOKEN_FILE.read_text())
    return None


def _save_tokens(tokens: dict) -> None:
    _TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def get_auth_url(redirect_uri: str) -> str:
    """Return the Google OAuth2 consent URL the user must visit."""
    settings = get_settings()
    client_id = settings.youtube_client_id
    if not client_id:
        raise ValueError("YOUTUBE_CLIENT_ID not configured in .env")

    return (
        f"{AUTH_URL}?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
    )


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    settings = get_settings()
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    log.info("YouTube tokens saved successfully.")
    return tokens


def _get_access_token() -> str:
    """Get an access token for upload operations.
    
    Tries in order:
    1. Service account (from GOOGLE_SERVICE_ACCOUNT_FILE or common paths)
    2. OAuth2 refresh token (from stored tokens)
    """
    # Try service account first
    sa_token = _get_service_account_token()
    if sa_token:
        return sa_token
    
    # Fall back to OAuth2 user tokens
    tokens = _load_tokens()
    if tokens and "refresh_token" in tokens:
        return _refresh_oauth_token(tokens)
    
    raise ValueError(
        "YouTube auth not configured. Either:\n"
        "1. Add service account JSON at dashboard/nullrecords-ga4-credentials.json, or\n"
        "2. Add YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET to .env and authorize via /video/youtube/auth"
    )


def _refresh_oauth_token(tokens: dict) -> str:
    """Use the stored refresh token to get a fresh access token."""

    settings = get_settings()
    resp = requests.post(TOKEN_URL, data={
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    new_tokens = resp.json()
    # Preserve the refresh token (Google doesn't always re-issue it)
    new_tokens["refresh_token"] = tokens["refresh_token"]
    _save_tokens(new_tokens)
    return new_tokens["access_token"]


def is_authenticated() -> bool:
    """Check whether we can obtain an access token for upload operations."""
    try:
        return bool(_get_access_token())
    except Exception as exc:
        log.info("YouTube auth check failed: %s", exc)
        return False


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "10",  # Music category
) -> dict:
    """Upload a video to YouTube.

    Returns dict with video id, url, and status.
    """
    access_token = _get_access_token()
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    metadata = {
        "snippet": {
            "title": title or video.stem,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Resumable upload — initiate
    init_resp = requests.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video.stat().st_size),
        },
        json=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_uri = init_resp.headers["Location"]

    # Upload the video bytes
    with open(video, "rb") as f:
        upload_resp = requests.put(
            upload_uri,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4",
            },
            data=f,
            timeout=600,
        )
    upload_resp.raise_for_status()
    result = upload_resp.json()

    video_id = result.get("id", "")
    log.info("YouTube upload complete: %s", video_id)

    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "status": result.get("status", {}).get("uploadStatus", "unknown"),
    }


def fetch_video_stats(video_ids: list[str]) -> dict[str, dict]:
    """Fetch statistics for one or more YouTube video IDs.

    Uses the API key (no OAuth needed for public video stats).
    Returns {video_id: {views, likes, comments, favorites}} for each found video.
    """
    settings = get_settings()
    api_key = settings.youtube_api_key
    if not api_key:
        log.warning("No YOUTUBE_API_KEY — cannot fetch stats")
        return {}

    stats = {}
    # API allows up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = requests.get(
                VIDEOS_URL,
                params={
                    "part": "statistics,snippet",
                    "id": ",".join(batch),
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                vid = item["id"]
                s = item.get("statistics", {})
                sn = item.get("snippet", {})
                stats[vid] = {
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)),
                    "comments": int(s.get("commentCount", 0)),
                    "favorites": int(s.get("favoriteCount", 0)),
                    "title": sn.get("title", ""),
                    "published_at": sn.get("publishedAt", ""),
                }
        except Exception:
            log.exception("Failed to fetch YouTube stats for batch starting at %d", i)

    return stats
