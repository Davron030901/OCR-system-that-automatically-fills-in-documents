"""Async database session management."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from app.config import get_settings
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True,
                             echo=_settings.debug)
session_factory = async_sessionmaker(engine, class_=AsyncSession,
                                     expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session
