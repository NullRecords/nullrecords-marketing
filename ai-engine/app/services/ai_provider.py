"""Shared AI text generation with local-first provider routing."""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _local_urls() -> list[str]:
    settings = get_settings()
    return [
        url.strip().rstrip("/")
        for url in settings.local_ai_urls.split(",")
        if url.strip()
    ]


def _ollama_generate(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str | None:
    settings = get_settings()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": settings.local_ai_model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }

    for base_url in _local_urls():
        try:
            response = requests.post(
                f"{base_url}/api/chat",
                json=payload,
                timeout=settings.local_ai_timeout,
            )
            if response.status_code == 404:
                log.info("Local AI endpoint missing at %s", base_url)
                continue
            response.raise_for_status()
            data = response.json()
            content = (data.get("message") or {}).get("content", "").strip()
            if content:
                log.info("Generated text with local AI at %s", base_url)
                return content
        except requests.RequestException as exc:
            log.info("Local AI unavailable at %s: %s", base_url, exc)
        except ValueError as exc:
            log.info("Local AI returned invalid JSON at %s: %s", base_url, exc)

    return None


def _openai_generate(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None

    try:
        from openai import OpenAI

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        log.info("Generated text with OpenAI fallback")
        return content.strip() or None
    except Exception as exc:  # pragma: no cover - defensive around network SDK errors
        log.warning("OpenAI generation failed: %s", exc)
        return None


def generate_text(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 500,
    allow_openai: bool = True,
) -> str | None:
    """Generate text using the configured provider preference.

    Defaults to local Ollama endpoints first, then OpenAI only if configured and
    allowed. Returns None when no provider succeeds so callers can use their
    existing deterministic fallback behavior.
    """
    settings = get_settings()
    provider = settings.ai_provider.strip().lower()

    if provider == "none":
        return None

    if provider in {"local_first", "local", ""}:
        local_result = _ollama_generate(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if local_result or provider == "local":
            return local_result

    should_use_openai = (
        provider == "openai"
        or (provider in {"local_first", ""} and settings.openai_fallback_enabled)
    )
    if should_use_openai and allow_openai:
        return _openai_generate(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return None


def ai_status() -> dict[str, Any]:
    """Return configured provider status for dashboards."""
    settings = get_settings()
    return {
        "provider": settings.ai_provider,
        "local_ai": bool(_local_urls()),
        "local_ai_model": settings.local_ai_model,
        "openai": bool(settings.openai_api_key),
        "openai_fallback": bool(settings.openai_api_key and settings.openai_fallback_enabled),
    }
