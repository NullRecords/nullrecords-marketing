"""AI-powered press release generator.

Generates press releases for events like album releases, book launches,
general announcements, etc. Uses the brand profile + configured AI provider.
"""

import json
from datetime import datetime
import logging
from typing import Any

from app.services.ai_provider import generate_text
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


# ── Fallback (no AI provider) ───────────────────────────────────────────

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


def _markdown_to_html(text: str) -> str:
    """Convert the generated markdown-ish press text to email-safe HTML.

    The previous version only handled bold and newlines, so markdown HEADINGS
    ("### Unwind with Ambient Serenity") rendered literally in the email body.
    Headings are converted first, before the paragraph pass, so their line breaks
    are not turned into <br>.
    """
    import re

    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # The lone '###' is the standard press-release end marker, not a heading.
        if stripped == "###":
            out.append("<hr>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)) + 1, 6)  # '#' -> h2, keeps h1 for the client
            out.append(f"<h{level}>{m.group(2).strip()}</h{level}>")
            continue
        out.append(line)

    html = "\n".join(out)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", html)

    # Paragraph/line-break pass, skipping block-level tags we just emitted.
    blocks = [b.strip() for b in html.split("\n\n") if b.strip()]
    rendered = []
    for b in blocks:
        if re.match(r"^<(h[1-6]|hr|ul|ol|p)\b", b):
            rendered.append(b)
        else:
            rendered.append("<p>" + b.replace("\n", "<br>\n") + "</p>")
    return "\n".join(rendered)


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
    profile = load_brand_profile()
    brand_summary = get_brand_summary(vertical_id)
    boilerplate = _build_boilerplate(profile)

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

    # The model must never invent the date: left to itself it emits its training
    # cutoff (observed: "October 12, 2023"). Inject today's date and tell it to
    # reproduce that string verbatim.
    today_str = datetime.now().strftime("%B %-d, %Y")

    user_prompt = (
        f"Write a press release for: **{event_cfg['label']}**\n\n"
        f"{event_cfg['prompt_hint']}\n\n"
        f"Release details:\n{json.dumps(release_info, indent=2, ensure_ascii=False)}\n\n"
        "Format requirements:\n"
        f"- Start with 'FOR IMMEDIATE RELEASE' then this exact date on its own line: {today_str}\n"
        "- Include a compelling headline (not the same as the subject line)\n"
        "- A strong lede paragraph with the key announcement\n"
        "- 2-3 body paragraphs with details and context\n"
        "- Include relevant links where provided\n"
        "- End with '###' (standard press release ending)\n"
        "- Total length: 300-500 words\n"
        "- Do NOT include the boilerplate 'About' section — it will be appended separately\n"
        "- Use plain prose. Do NOT use markdown headings (no '#', '##', '###') anywhere\n"
        "  except the single '###' that ends the release\n"
        "\nFACTUAL ACCURACY (this copy is sent to journalists — treat it as on the record):\n"
        "- Use ONLY the facts in 'Release details' and the brand context above\n"
        "- Do NOT invent titles, track names, dates, prices, statistics, or partner names\n"
        "- Do NOT invent quotes or attribute statements to anyone\n"
        "- If a detail is missing, omit it rather than guessing\n"
    )

    # Generate body
    body_text = generate_text(
        user_prompt,
        system=system_prompt,
        temperature=0.6,
        max_tokens=1000,
    )
    if not body_text:
        return _fallback_press_release(event_type, release_info, profile)

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

    subject = generate_text(
        subject_prompt,
        temperature=0.5,
        max_tokens=40,
    )
    if not subject:
        subject = f"Press Release: {release_info.get('title', 'New Release')}"
    subject = subject.strip().strip('"')

    # Combine body + boilerplate
    full_text = f"{body_text}\n\n{boilerplate}"

    body_html = _markdown_to_html(full_text)

    return {
        "subject": subject,
        "body_text": full_text,
        "body_html": body_html,
        "boilerplate": boilerplate,
    }


def list_event_types() -> list[dict]:
    """Return available event types for the UI."""
    return [{"id": k, "label": v["label"]} for k, v in EVENT_TYPES.items()]
