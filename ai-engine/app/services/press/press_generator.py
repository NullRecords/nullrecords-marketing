"""AI-powered press release generator.

Generates press releases for events like album releases, book launches,
general announcements, etc.  Uses the brand profile + OpenAI.
"""

import json
import logging
from typing import Any

from openai import OpenAI

from app.core.config import get_settings
from app.services.press.brand_profile import get_brand_summary, load_brand_profile

log = logging.getLogger(__name__)

# ── Event types and their template hints ────────────────────────────────

EVENT_TYPES = {
    "album_release": {
        "label": "Album / EP Release",
        "prompt_hint": (
            "This is for a new music release (album or EP). "
            "Include streaming links, tracklist highlights, and genre context."
        ),
    },
    "single_release": {
        "label": "Single Release",
        "prompt_hint": (
            "This is for a new single release. "
            "Focus on the track's vibe, collaborators, and where to listen."
        ),
    },
    "book_release": {
        "label": "Book Release",
        "prompt_hint": (
            "This is for a new book release. "
            "Include the book's premise, series context, genre, and where to buy/read."
        ),
    },
    "app_launch": {
        "label": "App Launch",
        "prompt_hint": (
            "This is for a new app launch or major app availability announcement. "
            "Include platform availability, core use cases, privacy or data-handling facts if provided, "
            "where to download or learn more, and the audience the app is built for."
        ),
    },
    "app_update": {
        "label": "App Update",
        "prompt_hint": (
            "This is for a product update to an existing app. "
            "Focus on what changed, who benefits, platform availability, and where to download or learn more."
        ),
    },
    "announcement": {
        "label": "General Announcement",
        "prompt_hint": (
            "This is a general news announcement (partnership, milestone, event, etc.)."
        ),
    },
    "tour_event": {
        "label": "Tour / Event",
        "prompt_hint": (
            "This is about a live performance, tour dates, or event. "
            "Include dates, venues, and ticket info if available."
        ),
    },
}


# ── Boilerplate builder ─────────────────────────────────────────────────

def _build_boilerplate(profile: dict) -> str:
    """Build the 'About' boilerplate block from the brand profile."""
    lines = [f"### About {profile.get('name', '')}"]
    lines.append(profile.get("tagline", ""))
    lines.append("")

    for v in profile.get("verticals", []):
        lines.append(f"**{v.get('name', '')}**: {v.get('description', '')}")

    if profile.get("website"):
        lines.append(f"\nWebsite: {profile['website']}")
    if profile.get("email"):
        lines.append(f"Press contact: {profile['email']}")

    social = profile.get("social", {})
    if social:
        social_parts = []
        for platform, url in social.items():
            if url:
                social_parts.append(f"{platform.title()}: {url}")
        if social_parts:
            lines.append("Social: " + " | ".join(social_parts))

    return "\n".join(lines)


# ── Fallback (no OpenAI) ────────────────────────────────────────────────

def _fallback_press_release(event_type: str, release_info: dict, profile: dict) -> dict:
    """Generate a basic press release without AI."""
    title = release_info.get("title", "New Release")
    name = profile.get("name", "NullRecords")

    subject = f"Press Release: {title} — {name}"
    body = (
        f"FOR IMMEDIATE RELEASE\n\n"
        f"**{title}**\n\n"
        f"{release_info.get('description', 'A new release from ' + name + '.')}\n\n"
        f"{_build_boilerplate(profile)}\n"
    )

    return {
        "subject": subject,
        "body_text": body,
        "body_html": body.replace("\n", "<br>\n"),
        "boilerplate": _build_boilerplate(profile),
    }


# ── Main generation function ────────────────────────────────────────────

def generate_press_release(
    event_type: str,
    release_info: dict[str, Any],
    vertical_id: str | None = None,
) -> dict:
    """Generate a professional press release using AI.

    Parameters:
        event_type: One of EVENT_TYPES keys
        release_info: Dict with keys like title, description, artist, genre,
                      release_date, links, tracklist, etc.
        vertical_id: Optional 'music' or 'books' to focus brand summary

    Returns dict with: subject, body_text, body_html, boilerplate
    """
    settings = get_settings()
    profile = load_brand_profile()
    brand_summary = get_brand_summary(vertical_id)
    boilerplate = _build_boilerplate(profile)

    if not settings.openai_api_key:
        return _fallback_press_release(event_type, release_info, profile)

    event_cfg = EVENT_TYPES.get(event_type, EVENT_TYPES["announcement"])
    tone = profile.get("tone", {})
    voice = tone.get("voice", "Approachable, genuine, independent")
    avoid_list = tone.get("avoid", [])

    system_prompt = (
        "You are a professional press release writer for an independent creative label. "
        "Write press releases that are newsworthy, quotable, and formatted for journalists.\n\n"
        f"Brand context:\n{brand_summary}\n\n"
        f"Tone: {voice}\n"
        f"Avoid: {', '.join(avoid_list) if avoid_list else 'corporate jargon'}\n"
    )

    user_prompt = (
        f"Write a press release for: **{event_cfg['label']}**\n\n"
        f"{event_cfg['prompt_hint']}\n\n"
        f"Release details:\n{json.dumps(release_info, indent=2, ensure_ascii=False)}\n\n"
        "Format requirements:\n"
        "- Start with 'FOR IMMEDIATE RELEASE' and the date\n"
        "- Include a compelling headline (not the same as the subject line)\n"
        "- A strong lede paragraph with the key announcement\n"
        "- 2-3 body paragraphs with details, quotes (you can invent a quote from the label), and context\n"
        "- Include relevant links where provided\n"
        "- End with '###' (standard press release ending)\n"
        "- Total length: 300-500 words\n"
        "- Do NOT include the boilerplate 'About' section — it will be appended separately\n"
    )

    client = OpenAI(api_key=settings.openai_api_key)

    # Generate body
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=1000,
    )
    body_text = response.choices[0].message.content.strip()

    # Generate subject line
    subject_prompt = (
        f"Write a concise email subject line (max 10 words) for a press release about:\n"
        f"- Event type: {event_cfg['label']}\n"
        f"- Title: {release_info.get('title', '')}\n"
        f"- From: {profile.get('name', 'NullRecords')}\n\n"
        "Rules:\n"
        "- Newswire style: factual, compelling, no clickbait\n"
        "- Just the subject line, nothing else\n"
    )

    resp2 = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": subject_prompt}],
        temperature=0.5,
        max_tokens=40,
    )
    subject = resp2.choices[0].message.content.strip().strip('"')

    # Combine body + boilerplate
    full_text = f"{body_text}\n\n{boilerplate}"

    # Simple markdown-to-html conversion
    body_html = full_text
    body_html = body_html.replace("###\n", "<hr>\n")
    body_html = body_html.replace("\n\n", "</p><p>")
    body_html = body_html.replace("\n", "<br>\n")
    # Bold
    import re
    body_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body_html)
    body_html = f"<p>{body_html}</p>"

    return {
        "subject": subject,
        "body_text": full_text,
        "body_html": body_html,
        "boilerplate": boilerplate,
    }


def list_event_types() -> list[dict]:
    """Return available event types for the UI."""
    return [{"id": k, "label": v["label"]} for k, v in EVENT_TYPES.items()]
