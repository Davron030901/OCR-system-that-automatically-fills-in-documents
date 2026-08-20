"""Configuration. Every secret comes from the environment, never from code."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "OCR Hujjat Tizimi"
    environment: str = "development"
    debug: bool = False

    # --- data stores ------------------------------------------------------
    database_url: str = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocr"
    redis_url: str = "redis://localhost:6379/0"
    storage_endpoint: str = "http://localhost:9000"
    storage_bucket: str = "ocr-uploads"
    storage_access_key: str = ""
    storage_secret_key: str = ""

    # --- services ---------------------------------------------------------
    ml_service_url: str = "http://localhost:8100"
    converter_url: str = "http://localhost:8200"
    internal_token: str = "change-me-in-production"

    # --- crypto -----------------------------------------------------------
    encryption_key: str = ""      # 32-byte urlsafe-base64; see docs/SECURITY.md
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 60

    # --- retention (hours) ------------------------------------------------
    upload_ttl_hours: int = 24
    extraction_ttl_days: int = 30
    document_ttl_days: int = 90

    # --- limits -----------------------------------------------------------
    max_upload_bytes: int = 10 * 1024 * 1024
    max_template_bytes: int = 20 * 1024 * 1024
    rate_limit_uploads_per_hour: int = 30
    rate_limit_templates_per_day: int = 20

    # --- features ---------------------------------------------------------
    enable_pdf_output: bool = True
    llm_enabled: bool = True
    llm_daily_budget_usd: float = 5.0
    enable_l3_vision: bool = False   # sends the image off-premises: opt in

    # Demo deployments run on free-tier LLM keys and must not process real
    # documents. This does NOT relax the PII gate: a real passport uploaded to
    # a demo is refused by packages/llm/pii_gate.py, not quietly forwarded.
    # See packages/llm/demo.py for why the failure mode is a refusal.
    demo_mode: bool = False

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
