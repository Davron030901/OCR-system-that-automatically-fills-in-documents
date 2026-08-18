"""ML inference service.

Runs separately from the API so the API container stays small and fast to cold
start. Uses ONNX Runtime only -- no PyTorch or PaddlePaddle -- which is what
keeps the image and the memory footprint inside a small instance.
"""
from __future__ import annotations

import logging
import os

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

from packages.ml.pipeline import ExtractionPipeline, PipelineConfig

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ml-service")

INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "change-me-in-production")
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() == "true"
ENABLE_L3 = os.getenv("ENABLE_L3_VISION", "false").lower() == "true"

app = FastAPI(title="OCR ML Service", version="0.1.0")
_pipeline: ExtractionPipeline | None = None


def require_internal(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(401, "unauthorized")


def get_pipeline() -> ExtractionPipeline:
    """Lazy load. Models are pulled on first request, not at import time."""
    global _pipeline
    if _pipeline is None:
        ocr = _build_ocr()
        mapper = _build_mapper() if LLM_ENABLED else None
        _pipeline = ExtractionPipeline(
            ocr=ocr, mapper=mapper, classifier=_build_classifier(),
            config=PipelineConfig(enable_l2=LLM_ENABLED, enable_l3=ENABLE_L3),
        )
    return _pipeline


def _build_classifier():
    """Optional. Returns None when CLASSIFIER_MODEL_PATH is unset or missing.

    Without it the pipeline trusts the caller's document-type hint and still
    extracts; it just loses the ability to refuse an unrecognised photo up
    front. Train one with training/01_classifier.ipynb and point the env var at
    the exported ONNX file.
    """
    from packages.ml.classify import load_classifier
    clf = load_classifier()
    log.info("classifier %s", "loaded" if clf else "disabled")
    return clf


def _build_ocr():
    """Wire in the local OCR engine.

    Left as a seam: swap PaddleOCR, docTR or a Tesseract wrapper here without
    touching the pipeline. Returning None makes the pipeline fall back to MRZ
    plus whatever the LLM stage can do, which is still a working system.
    """
    try:
        from packages.ml.ocr.paddle_engine import PaddleEngine
        return PaddleEngine()
    except Exception as exc:                                     # noqa: BLE001
        log.warning("local OCR unavailable (%s); running MRZ-only",
                    type(exc).__name__)
        return None


def _build_mapper():
    from packages.ml.llm_mapper import CascadeMapper
    return CascadeMapper()


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    pipeline = get_pipeline()
    return {
        "status": "ready",
        "llm_enabled": LLM_ENABLED,
        "l3_vision": ENABLE_L3,
        "ocr": pipeline.ocr is not None,
        "classifier": pipeline.classifier is not None,
    }


@app.post("/extract", dependencies=[Depends(require_internal)])
async def extract(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(400, "no files")

    images: list[np.ndarray] = []
    for f in files:
        data = await f.read()
        if f.content_type == "application/pdf" or data[:4] == b"%PDF":
            images.extend(_pdf_pages(data))
        else:
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if arr is not None:
                images.append(arr)

    if not images:
        raise HTTPException(400, "no decodable images")

    pipeline = get_pipeline()
    result = await pipeline.extract(images[0], job_id="inline")

    # Merge a second page (an ID card back carries the MRZ) into the first.
    for extra in images[1:]:
        other = await pipeline.extract(extra, job_id="inline")
        _merge(result, other)

    return result.model_dump(mode="json")


def _pdf_pages(data: bytes, dpi: int = 300, max_pages: int = 4):
    """Rasterise a PDF. A text-layer PDF needs no OCR at all."""
    try:
        import pymupdf
    except ImportError:
        return []
    out = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in list(doc)[:max_pages]:
            pix = page.get_pixmap(dpi=dpi)
            arr = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n)
            out.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                       if pix.n == 3 else arr)
    return out


def _merge(base, other) -> None:
    """Take any field the second page resolved better than the first."""
    from packages.ml.pipeline import merge_field
    base_flat, other_flat = base.flatten(), other.flatten()
    for path, candidate in other_flat.items():
        current = base_flat.get(path)
        if current is None or candidate.is_empty:
            continue
        winner = merge_field(current, candidate)
        if winner is candidate:
            current.value = candidate.value
            current.confidence = candidate.confidence
            current.source = candidate.source
            current.validated = candidate.validated
    base.stages_used = sorted(set(base.stages_used) | set(other.stages_used))
    base.warnings.extend(w for w in other.warnings if w not in base.warnings)
