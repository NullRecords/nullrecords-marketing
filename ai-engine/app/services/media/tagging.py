"""AI-powered media tagging via the configured AI provider."""

import json
from typing import Any

from app.services.ai_provider import generate_text


def tag_media(title: str, description: str = "") -> dict[str, Any]:
    """Use OpenAI to generate tags, mood, and style for a media asset.

    Returns {"tags": [...], "mood": "...", "style": "..."}.
    """
    prompt = (
        "You are a media tagging assistant for a music label that specialises in "
        "nu jazz, lo-fi, and experimental electronic music.\n\n"
        f"Title: {title}\n"
    )
    if description:
        prompt += f"Description: {description}\n"

    prompt += (
        "\nReturn a JSON object with exactly these keys:\n"
        '  "tags": a list of 5-10 descriptive keyword tags\n'
        '  "mood": a single-word mood (e.g. dreamy, energetic, melancholic)\n'
        '  "style": a short style descriptor (e.g. retro sci-fi, urban night)\n'
        "Return ONLY the JSON object, no markdown fences."
    )

    text = generate_text(
        prompt,
        temperature=0.4,
        max_tokens=300,
    )
    if not text:
        return {"tags": [], "mood": "unknown", "style": "unknown"}

    # Strip markdown code fences if the model includes them anyway
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"tags": [], "mood": "unknown", "style": "unknown"}

    return result
