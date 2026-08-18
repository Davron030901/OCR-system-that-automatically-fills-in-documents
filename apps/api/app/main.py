"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from app.config import get_settings
from app.routers import documents, health, jobs, templates
from app.security.redaction import install as install_redaction
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
           '"msg":"%(message)s"}',
)
# Installed before anything else can log, so redaction is never bypassed by
# an early startup message.
install_redaction()

log = logging.getLogger("api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.encryption_key and settings.environment == "production":
        raise RuntimeError(
            "ENCRYPTION_KEY must be set in production. Extracted passport data "
            "is stored encrypted; refusing to start without a key.")
    log.info("starting %s (env=%s)", settings.app_name, settings.environment)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Never leak internals to the client.

    Exception text routinely contains fragments of the document being
    processed, so it is logged (through the redaction filter) and replaced with
    a stable message.
    """
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error_code": "INTERNAL_ERROR",
                 "message": "Kutilmagan xatolik yuz berdi. Qaytadan urinib ko'ring."},
    )


app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(templates.router)
app.include_router(documents.router)
