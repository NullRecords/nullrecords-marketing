"""Relevance scoring via the configured AI provider."""

from app.core.config import get_settings
from app.services.ai_provider import generate_text


def score_relevance(name: str, description: str = "") -> float:
    """Score how relevant a playlist / influencer is to the label's genre (0.0–1.0)."""
    settings = get_settings()
    prompt = (
        "You are a music marketing analyst.\n"
        f"Our label genre: {settings.label_genre}\n\n"
        f"Target name: {name}\n"
    )
    if description:
        prompt += f"Target description: {description}\n"
    prompt += (
        "\nRate the relevance of this target to our label on a scale of 0.0 to 1.0.\n"
        "Return ONLY a single decimal number (e.g. 0.72). No other text."
    )

    text = generate_text(
        prompt,
        temperature=0.2,
        max_tokens=10,
    )
    if not text:
        return 0.5  # default when no model provider is configured or reachable

    try:
        score = float(text)
        return max(0.0, min(1.0, score))
    except ValueError:
        return 0.5
