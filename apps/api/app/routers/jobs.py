"""Extraction jobs.

Upload returns immediately with a job id. OCR plus an LLM round trip takes
seconds, which is far too long to hold an HTTP request open on a mobile
connection.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
from app.config import Settings, get_settings
from app.db.models import Extraction, Job, Upload
from app.db.session import get_session
from app.security.crypto import decrypt_json, encrypt_json
from app.security.validation import UploadRejected, validate_upload
from app.services.storage import Storage, get_storage
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# Progress messages are user-facing, so they are in Uzbek and describe what is
# happening rather than which service is being called.
STAGES = {
    "queued": "Navbatda",
    "preprocessing": "Rasm tayyorlanmoqda",
    "classifying": "Hujjat turi aniqlanmoqda",
    "extracting": "Ma'lumotlar o'qilmoqda",
    "validating": "Tekshirilmoqda",
    "done": "Tayyor",
}


@router.post("", status_code=202)
async def create_job(
    request: Request,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: Storage = Depends(get_storage),
) -> dict:
    if len(files) > 4:
        raise HTTPException(400, "Bir vaqtda 4 tadan ko'p fayl yuklab bo'lmaydi")

    job = Job(status="queued")
    session.add(job)
    await session.flush()

    for f in files:
        data = await f.read()
        try:
            mime, digest = validate_upload(data, settings.max_upload_bytes)
        except UploadRejected as exc:
            raise HTTPException(400, str(exc)) from exc

        key = f"uploads/{job.id}/{digest[:16]}"
        await storage.put(key, data, mime)
        session.add(Upload(job_id=job.id, storage_key=key, mime=mime,
                           size=len(data), sha256=digest))

    await session.commit()
    asyncio.create_task(_process(job.id))
    return {"job_id": job.id, "status": job.status}


async def _process(job_id: str) -> None:
    """Hand the images to the ML service and store the encrypted result."""
    settings = get_settings()
    from app.db.session import session_factory

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        storage = get_storage()
        try:
            job.status = "extracting"
            await session.commit()

            uploads = list(job.uploads)
            files = [("files", (u.storage_key.split("/")[-1],
                                await storage.get(u.storage_key), u.mime))
                     for u in uploads]

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{settings.ml_service_url}/extract",
                    files=files,
                    headers={"X-Internal-Token": settings.internal_token},
                )
                resp.raise_for_status()
                result = resp.json()

            job.status = result.get("status", "ok")
            job.doc_type = result.get("doc_type")
            job.stages_used = result.get("stages_used", [])
            job.llm_cost_usd = result.get("llm_cost_usd", 0.0)
            job.error_code = result.get("error_code")
            job.error_message = result.get("error_message")
            job.completed_at = datetime.now(UTC)

            session.add(Extraction(
                job_id=job.id,
                data_encrypted=encrypt_json(result, settings.encryption_key,
                                            aad=job.id.encode()),
                overall_confidence=result.get("overall_confidence", 0.0),
                needs_review=result.get("needs_review", []),
                warnings=result.get("warnings", []),
                model_versions=result.get("model_versions", {}),
            ))
        except Exception:                                   # noqa: BLE001
            # The exception text may echo document content, so it is never
            # stored or shown; only a stable code reaches the user.
            job.status = "failed"
            job.error_code = "PROCESSING_FAILED"
            job.error_message = ("Hujjatni qayta ishlashda xatolik yuz berdi. "
                                 "Qaytadan urinib ko'ring.")
        await session.commit()


@router.get("/{job_id}")
async def get_job(job_id: str,
                  session: AsyncSession = Depends(get_session),
                  settings: Settings = Depends(get_settings)) -> dict:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Topilmadi")

    payload: dict = {
        "job_id": job.id,
        "status": job.status,
        "stage_label": STAGES.get(job.status, job.status),
        "doc_type": job.doc_type,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "stages_used": job.stages_used or [],
    }
    if job.extraction is not None:
        payload["result"] = decrypt_json(
            job.extraction.data_encrypted, settings.encryption_key,
            aad=job.id.encode())
        payload["needs_review"] = job.extraction.needs_review or []
        payload["warnings"] = job.extraction.warnings or []
    return payload


@router.get("/{job_id}/stream")
async def stream_job(job_id: str,
                     session: AsyncSession = Depends(get_session)):
    """Server-sent events so the UI can show real progress, not a spinner."""
    async def gen():
        for _ in range(120):
            job = await session.get(Job, job_id)
            if job is None:
                yield 'event: error\ndata: {"error":"not_found"}\n\n'
                return
            await session.refresh(job)
            yield (f"data: {json.dumps({'status': job.status, 'label': STAGES.get(job.status, job.status)})}\n\n")
            if job.status in ("ok", "review_needed", "failed", "bad_quality",
                              "unknown_doc_type"):
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.patch("/{job_id}/fields")
async def correct_fields(job_id: str, corrections: dict,
                         session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """Apply human corrections from the review UI.

    Corrections are recorded with source="manual" so downstream consumers and
    the audit trail can distinguish what a person asserted from what a model
    inferred.
    """
    job = await session.get(Job, job_id)
    if job is None or job.extraction is None:
        raise HTTPException(404, "Topilmadi")

    data = decrypt_json(job.extraction.data_encrypted, settings.encryption_key,
                        aad=job.id.encode())

    for path, value in corrections.items():
        node = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node.get(part, {})
        leaf = parts[-1]
        if isinstance(node, dict) and leaf in node and isinstance(node[leaf], dict):
            node[leaf].update({"value": value, "source": "manual",
                               "confidence": 1.0, "validated": False})

    remaining = [p for p in (job.extraction.needs_review or [])
                 if p not in corrections]
    job.extraction.data_encrypted = encrypt_json(data, settings.encryption_key,
                                                 aad=job.id.encode())
    job.extraction.needs_review = remaining
    job.extraction.reviewed_at = datetime.now(UTC)
    await session.commit()
    return {"job_id": job_id, "remaining_review": remaining}


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str,
                     session: AsyncSession = Depends(get_session),
                     storage: Storage = Depends(get_storage)) -> None:
    """Right to erasure. Removes stored objects as well as database rows."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    for upload in job.uploads:
        await storage.delete(upload.storage_key)
    await session.delete(job)
    await session.commit()
