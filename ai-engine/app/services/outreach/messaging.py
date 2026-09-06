"""Outreach message generation via the configured AI provider."""

from app.core.config import get_settings
from app.services.ai_provider import generate_text


def generate_outreach_message(
    target_name: str,
    target_type: str,
    context: str = "",
    vertical_id: str | None = None,
) -> str:
    """Generate a short, human, non-salesy outreach message (max ~100 words).

    `vertical_id` selects which NullRecords brand is pitching (music, books,
    drift_tv, ...). Without it this pitched the music label for every brand,
    which is wrong for Drift TV or a Null Press title.
    """
    settings = get_settings()
    fallback = (
        f"Hi {target_name}, we're {settings.label_name} — an independent collective "
        "making nu jazz, lo-fi, and experimental electronic music. We'd love to connect "
        "and share some tracks that might be a fit. No pressure at all — just genuine "
        "appreciation for what you do. Cheers!"
    )

    # Ground the model in the real brand + real releases so it cannot invent a
    # catalogue. Observed before this: it pitched a track called "Midnight Echoes"
    # that does not exist.
    brand_summary = ""
    try:
        from app.services.press.brand_profile import get_brand_summary

        brand_summary = get_brand_summary(vertical_id) or ""
    except Exception:  # brand profile is optional — fall back to label settings
        brand_summary = ""

    prompt = (
        "You are writing a short outreach message on behalf of an independent "
        "creative label.\n"
        f"Label: {settings.label_name}\n"
    )
    if brand_summary:
        prompt += f"\nBrand and current releases (the ONLY facts you may use):\n{brand_summary}\n\n"
    else:
        prompt += f"Genre: {settings.label_genre}\n"
    prompt += f"Target: {target_name} ({target_type})\n"
    if context:
        prompt += f"Context about this target: {context}\n"
    prompt += (
        "\nRules:\n"
        "- Human, respectful, non-salesy tone\n"
        "- Maximum 100 words\n"
        "- Reference something specific about their work, platform, or audience based on the context\n"
        "- Show you've done your homework — don't be generic\n"
        "- End with a soft call-to-action (listen to a track, check out a playlist, etc.)\n"
        "- Do NOT include a subject line, just the body\n"
        "\nFACTUAL ACCURACY (this is sent to a real person — treat it as on the record):\n"
        "- Reference ONLY releases, titles and facts written above\n"
        "- If no release title appears above, do NOT name any work at all. Describe what "
        "we make in general terms instead. Naming an unlisted title is a factual error.\n"
        "- NEVER invent a track, album, book, episode, app feature, statistic or partner\n"
        "- Do NOT use placeholders such as [Your Name], [Label] or [Link]. The message is "
        "sent as-is, so write it complete and sign off only as the label name.\n"
    )

    result = generate_text(prompt, temperature=0.7, max_tokens=200)
    if not result:
        return fallback
    return result


def generate_outreach_subject(target_name: str, target_type: str, context: str = "") -> str:
    """Generate a short, compelling email subject line."""
    settings = get_settings()
    prompt = (
        "Write a short, compelling email subject line (max 8 words) for an outreach message "
        f"from {settings.label_name} (an independent {settings.label_genre} label) "
        f"to {target_name} ({target_type}).\n"
    )
    if context:
        prompt += f"Context: {context}\n"
    prompt += (
        "Rules:\n"
        "- No clickbait, no caps lock, no exclamation marks\n"
        "- Personal and specific to the target\n"
        "- Just the subject line, nothing else\n"
    )

    result = generate_text(
        prompt,
        temperature=0.7,
        max_tokens=30,
    )
    if not result:
        return f"Music for {target_name} — from {settings.label_name}"
    return result.strip().strip('"')
