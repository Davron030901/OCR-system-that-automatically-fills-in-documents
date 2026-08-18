"""Word template registration.

Templates are untrusted uploads containing executable template code, so the
pipeline is: sniff format, sanitise, repair fragmented runs, analyse, and only
then store. Anything the analyser could not understand is reported back rather
than discovered later in a signed document.
"""
from __future__ import annotations

from dataclasses import asdict

from app.config import Settings, get_settings
from app.db.models import Template
from app.db.session import get_session
from app.services.storage import Storage, get_storage
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.docgen.engine import (
    TemplateRejected,
    analyze,
    detect_format,
    sanitize_office,
)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


@router.post("", status_code=201)
async def upload_template(
    file: UploadFile = File(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: Storage = Depends(get_storage),
) -> dict:
    data = await file.read()
    if len(data) > settings.max_template_bytes:
        raise HTTPException(400, "Shablon fayli juda katta")

    fmt = detect_format(data)
    conversion_report = None

    if fmt == "doc":
        # .doc is a binary OLE format with no reliable pure-Python reader, so
        # it is converted once here at registration time rather than on every
        # document generation.
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.converter_url}/doc-to-docx",
                files={"file": (file.filename, data)},
                headers={"X-Internal-Token": settings.internal_token},
            )
            if resp.status_code != 200:
                raise HTTPException(
                    422, "'.doc' faylni o'girish imkoni bo'lmadi. Word'da "
                         "'.docx' sifatida saqlab qayta yuklang.")
            data = resp.content
            conversion_report = {"from": "doc", "to": "docx"}

    try:
        cleaned, sanitation = sanitize_office(data)
        fixed, spec = analyze(cleaned, name)
    except TemplateRejected as exc:
        raise HTTPException(422, str(exc)) from exc

    key = f"templates/{name}-{file.filename}"
    await storage.put(key, fixed, "application/vnd.openxmlformats-officedocument"
                                  ".wordprocessingml.document")

    tpl = Template(
        name=name, original_format=fmt, storage_key=key,
        spec={
            "variables": [asdict(v) for v in spec.variables],
            "required_fields": spec.required_fields,
            "optional_fields": spec.optional_fields,
            "loops": spec.loops,
        },
        sanitization_report={"findings": sanitation.findings,
                             "user_messages": sanitation.as_user_message()},
        conversion_report=conversion_report,
        output_formats=["docx"] + (["pdf"] if settings.enable_pdf_output else []),
    )
    session.add(tpl)
    await session.commit()

    return {
        "id": tpl.id,
        "name": tpl.name,
        "required_fields": spec.required_fields,
        "optional_fields": spec.optional_fields,
        # Surfaced so the author learns that Word had fragmented their tags and
        # the system repaired them, instead of silently benefiting from it.
        "repaired_tags": spec.repaired_tags,
        "runs_merged": spec.runs_merged,
        "unknown_variables": [
            {"path": p, "suggestion": s} for p, s in spec.unknown_variables],
        "errors": spec.errors,
        "sanitization": sanitation.as_user_message(),
        "output_formats": tpl.output_formats,
    }


@router.get("")
async def list_templates(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(Template))).scalars().all()
    return [{
        "id": t.id, "name": t.name,
        "required_fields": t.spec.get("required_fields", []),
        "output_formats": t.output_formats,
        "is_published": t.is_published,
    } for t in rows]


@router.post("/{template_id}/publish")
async def publish(template_id: str,
                  session: AsyncSession = Depends(get_session)) -> dict:
    tpl = await session.get(Template, template_id)
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    if not tpl.spec.get("required_fields"):
        raise HTTPException(422, "Shablonda hech qanday maydon topilmadi")
    tpl.is_published = True
    await session.commit()
    return {"id": tpl.id, "is_published": True}
