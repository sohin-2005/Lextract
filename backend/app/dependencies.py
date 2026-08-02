"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_session


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async database session."""
    async for session in get_session():
        yield session


def settings_dep() -> Settings:
    """Yield the cached settings object."""
    return get_settings()


DBSession = Annotated[AsyncSession, Depends(db_session)]
AppSettings = Annotated[Settings, Depends(settings_dep)]
