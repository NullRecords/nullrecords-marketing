"""Configuration module — loads environment variables and sets project paths."""

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings


# Root of the ai-engine project (parent of /app)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # --- Database ---
    database_url: str = f"sqlite:///{BASE_DIR / 'ai_engine.db'}"

    # --- AI provider routing ---
    # local_first: try Ollama endpoints first, then OpenAI fallback if enabled
    # local: only try Ollama endpoints
    # openai: only use OpenAI
    # none: skip model calls and use deterministic fallbacks
    ai_provider: str = "local_first"
    local_ai_urls: str = "http://localhost:11434,http://alderaan.hoth:11434"
    local_ai_model: str = "llama3.1:8b"
    local_ai_timeout: int = 20
    openai_fallback_enabled: bool = True

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- Pexels ---
    pexels_api_key: str = ""

    # --- Spotify ---
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = ""

    # --- YouTube ---
    youtube_api_key: str = ""
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    # --- Paths ---
    media_library_dir: str = str(BASE_DIR / "media-library")
    exports_dir: str = str(BASE_DIR / "exports")

    # --- Label metadata (used for outreach scoring) ---
    label_genre: str = "nu jazz, experimental electronic"
    label_name: str = "NullRecords"

    # --- Email / SMTP ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_key: str = ""  # Brevo/Sendinblue API key (used as SMTP password)
    smtp_from_email: str = ""
    smtp_use_tls: bool = True

    # --- Scheduler ---
    scheduler_enabled: bool = True
    scheduler_discovery_hours: int = 24
    scheduler_followup_minutes: int = 60
    scheduler_media_ingest_hours: int = 12
    scheduler_discovery_query: str = "nu jazz experimental electronic indie"
    scheduler_report_hourly_minutes: int = 60
    scheduler_report_daily_hours: int = 24
    scheduler_report_weekly_hours: int = 168

    # --- Auto-outreach ---
    auto_outreach_interval_hours: int = 24
    auto_outreach_min_score: float = 0.6
    auto_outreach_max_per_run: int = 5

    model_config = {"env_file": str(BASE_DIR / ".env"), "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
