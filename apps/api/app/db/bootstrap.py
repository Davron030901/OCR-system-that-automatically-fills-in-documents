"""Startup bootstrap.

Creates the schema and the storage bucket so a fresh `docker compose up` gives
a working stack rather than a container that starts and then fails on the
first query.

In production this is deliberately inert: schema changes there go through
Alembic, where they are reviewed, ordered and reversible. `create_all` cannot
express a migration -- it only ever adds -- so letting it run against a live
database would silently diverge the schema from the migration history.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.db.models import Base
from app.db.session import engine
from sqlalchemy import text

log = logging.getLogger(__name__)


async def create_schema() -> None:
    settings = get_settings()
    if settings.environment == "production":
        log.info("production environment: skipping create_all, use Alembic")
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database schema ready")


async def wait_for_database(attempts: int = 30, delay: float = 1.0) -> None:
    """Postgres accepts TCP connections before it accepts queries.

    Compose's healthcheck helps but does not fully close the window, and a
    crash loop on first boot looks like a configuration error to whoever is
    setting this up for the first time.
    """
    import asyncio

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:                                 # noqa: BLE001
            if attempt == attempts:
                raise RuntimeError(
                    f"database unreachable after {attempts} attempts: "
                    f"{type(exc).__name__}"
                ) from exc
            await asyncio.sleep(delay)


def ensure_bucket() -> None:
    """Create the object-storage bucket if it is missing.

    MinIO starts empty, so without this the first upload fails with
    NoSuchBucket, which reads like a code bug rather than a setup step.
    """
    settings = get_settings()
    try:
        from app.services.storage import get_storage
        storage = get_storage()
        client = storage._c
        existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        if storage.bucket not in existing:
            client.create_bucket(Bucket=storage.bucket)
            log.info("created bucket %s", storage.bucket)
    except Exception as exc:                                     # noqa: BLE001
        # Storage being unavailable should not stop the API from serving
        # health checks; uploads will fail loudly with their own message.
        log.warning("could not ensure bucket (%s): %s",
                    settings.storage_bucket, type(exc).__name__)


async def bootstrap() -> None:
    await wait_for_database()
    await create_schema()
    ensure_bucket()
