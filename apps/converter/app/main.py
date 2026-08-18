"""LibreOffice conversion microservice.

Kept separate because LibreOffice is roughly 400 MB and slow to start. The API
container needs neither. This service is called rarely: converting a .doc
happens once per template, not once per generated document.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("converter")

INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "change-me-in-production")
SOFFICE = os.getenv("SOFFICE_BIN", "soffice")
TIMEOUT = 45
# LibreOffice is single-instance-per-profile and memory hungry; more than two
# concurrent conversions on a small box means swapping, not throughput.
_semaphore = asyncio.Semaphore(2)

app = FastAPI(title="OCR Converter", version="0.1.0")


def require_internal(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(401, "unauthorized")


async def _convert(data: bytes, src_ext: str, target: str) -> bytes:
    async with _semaphore:
        workdir = Path(tempfile.mkdtemp(prefix="conv-"))
        # A private profile per invocation. Sharing one profile across
        # concurrent conversions corrupts it and the failures look random.
        profile = workdir / f"profile-{uuid.uuid4().hex}"
        try:
            src = workdir / f"input.{src_ext}"
            src.write_bytes(data)

            proc = await asyncio.create_subprocess_exec(
                SOFFICE, "--headless", "--norestore", "--nolockcheck",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", target, "--outdir", str(workdir), str(src),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
            except TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise HTTPException(504, "conversion timed out") from exc

            ext = target.split(":")[0]
            produced = workdir / f"input.{ext}"
            if not produced.exists():
                raise HTTPException(422, "conversion produced no output")
            return produced.read_bytes()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "soffice": bool(shutil.which(SOFFICE))}


@app.post("/doc-to-docx", dependencies=[Depends(require_internal)])
async def doc_to_docx(file: UploadFile = File(...)) -> Response:
    out = await _convert(await file.read(), "doc", "docx:MS Word 2007 XML")
    return Response(out, media_type="application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document")


@app.post("/docx-to-pdf", dependencies=[Depends(require_internal)])
async def docx_to_pdf(file: UploadFile = File(...)) -> Response:
    out = await _convert(await file.read(), "docx", "pdf")
    return Response(out, media_type="application/pdf")
