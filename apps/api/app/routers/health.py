from app.config import get_settings
from fastapi import APIRouter

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
    }
