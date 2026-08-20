"""Liveness, readiness and the small amount of configuration the UI needs."""

from app.config import get_settings
from fastapi import APIRouter

from packages.llm.demo import DEMO_WARNING_UZ

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict:
    s = get_settings()
    return {
        "status": "ready",
        "pdf_output": s.enable_pdf_output,
        "llm_enabled": s.llm_enabled,
        "l3_vision": s.enable_l3_vision,
        "demo_mode": s.demo_mode,
    }


@router.get("/api/v1/config")
async def client_config() -> dict:
    """Public, non-secret settings the frontend needs before the first upload.

    demo_mode is here so the warning is rendered from the SERVER's actual
    configuration. A build-time frontend flag would drift: the banner would
    keep promising production behaviour after someone flipped the backend to
    free-tier keys, which is precisely the moment the warning matters.
    """
    s = get_settings()
    return {
        "demo_mode": s.demo_mode,
        "demo_warning": DEMO_WARNING_UZ if s.demo_mode else None,
        "pdf_output": s.enable_pdf_output,
        "max_upload_bytes": s.max_upload_bytes,
    }
