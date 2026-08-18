"""Retention sweeps.

A retention policy that exists only in a document is not a policy. These
functions are scheduled and tested, so "images are deleted after 24 hours" is
a property of the system rather than an intention.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.db.models import GeneratedDocument, Job, Upload
from app.db.session import session_factory
from app.services.storage import get_storage
from sqlalchemy import delete, select

log = logging.getLogger(__name__)


async def sweep_expired() -> dict[str, int]:
    now = datetime.now(UTC)
    storage = get_storage()
    counts = {"uploads": 0, "documents": 0, "jobs": 0}

    async with session_factory() as session:
        rows = (await session.execute(
            select(Upload).where(Upload.expires_at < now))).scalars().all()
        for upload in rows:
            await storage.delete(upload.storage_key)
            await session.delete(upload)
            counts["uploads"] += 1

        rows = (await session.execute(
            select(GeneratedDocument)
            .where(GeneratedDocument.expires_at < now))).scalars().all()
        for doc in rows:
            await storage.delete(doc.storage_key)
            await session.delete(doc)
            counts["documents"] += 1

        result = await session.execute(delete(Job).where(Job.expires_at < now))
        counts["jobs"] = result.rowcount or 0
        await session.commit()

    log.info("retention sweep removed %s", counts)
    return counts
