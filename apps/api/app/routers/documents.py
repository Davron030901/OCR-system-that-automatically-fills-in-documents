"""Document generation."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.db.models import GeneratedDocument, Job, Template
from app.db.session import get_session
from app.security.crypto import decrypt_json
from app.services.storage import Storage, get_storage
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from packages.docgen.engine import TemplateRejected, render_docx
from packages.schema.models import ExtractionResult

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class GenerateRequest(BaseModel):
    job_id: str
    template_id: str
    output_format: str = "docx"
    extra_fields: dict = {}


@router.post("", status_code=201)
async def generate(req: GenerateRequest,
                   session: AsyncSession = Depends(get_session),
                   settings: Settings = Depends(get_settings),
                   storage: Storage = Depends(get_storage)) -> dict:
    if req.output_format == "pdf" and not settings.enable_pdf_output:
        raise HTTPException(
            422,
            "PDF chiqishi o'chirilgan. DOCX formatida yuklab oling, yoki "
            "converter servisini yoqib ENABLE_PDF_OUTPUT=true qiling "
            "(make dev-full).")

    job = (await session.execute(
        select(Job).where(Job.id == req.job_id)
        .options(selectinload(Job.extraction)))).scalar_one_or_none()
    if job is None or job.extraction is None:
        raise HTTPException(404, "Ma'lumot topilmadi")
    tpl = await session.get(Template, req.template_id)
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    if req.output_format not in tpl.output_formats:
        raise HTTPException(
            422, f"Bu shablon '{req.output_format}' formatini qo'llab-quvvatlamaydi")

    payload = decrypt_json(job.extraction.data_encrypted,
                           settings.encryption_key, aad=job.id.encode())
    result = ExtractionResult.model_validate(payload)

    template_bytes = await storage.get(tpl.storage_key)
    try:
        filled = render_docx(template_bytes, result, req.extra_fields)
    except TemplateRejected as exc:
        raise HTTPException(422, str(exc)) from exc

    content, fmt = filled.content, "docx"
    if req.output_format == "pdf":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{settings.converter_url}/docx-to-pdf",
                    files={"file": ("document.docx", content)},
                    headers={"X-Internal-Token": settings.internal_token},
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                503, "PDF yaratish xizmati ishlamayapti. Hujjat DOCX "
                     "formatida tayyor — shu formatda yuklab oling.") from exc
        if resp.status_code != 200:
            raise HTTPException(
                503, "PDF yaratish xizmati hozir mavjud emas. "
                     "DOCX formatida yuklab oling.")
        content, fmt = resp.content, "pdf"

    key = f"documents/{job.id}/{tpl.id}.{fmt}"
    await storage.put(key, content, "application/octet-stream")

    doc = GeneratedDocument(job_id=job.id, template_id=tpl.id, storage_key=key,
                            format=fmt, missing_fields=filled.missing_fields)
    session.add(doc)
    await session.commit()

    return {
        "id": doc.id, "format": fmt,
        "missing_fields": filled.missing_fields,
        "warnings": filled.warnings,
        "download_url": storage.signed_url(key),
    }


@router.get("/{document_id}/download")
async def download(document_id: str,
                   session: AsyncSession = Depends(get_session),
                   storage: Storage = Depends(get_storage)) -> dict:
    doc = await session.get(GeneratedDocument, document_id)
    if doc is None:
        raise HTTPException(404, "Hujjat topilmadi")
    # Short-lived signed URL: the storage location itself is never exposed.
    return {"url": storage.signed_url(doc.storage_key)}
