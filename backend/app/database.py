"""SQLAlchemy 2.0 async engine, session factory and declarative ``Base``.

Why async
---------
The API is IO-bound end to end: every request either waits on Postgres or on a
remote LLM.  Pairing ``async def`` FastAPI endpoints with a *synchronous* driver
is the classic footgun -- each query would block the event loop and silently
serialise all concurrent extractions.  We therefore use ``asyncpg`` throughout
and Alembic runs its migrations inside ``asyncio.run`` (see ``alembic/env.py``).

``expire_on_commit=False`` keeps ORM attributes readable after ``commit()``,
which matters because our response serialisation happens after the commit.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

def _engine_options(dsn: str) -> dict[str, object]:
    """Connection-pool options appropriate to the backend in use.

    Postgres gets a real pool. SQLite -- which the test suite uses so it can run
    without a database server -- gets none: aiosqlite is served by StaticPool,
    which rejects ``pool_size``/``max_overflow`` outright with a TypeError at
    import time.
    """
    if dsn.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,  # survives a Postgres restart / idle reap
        "pool_size": 10,
        "max_overflow": 20,
    }


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    **_engine_options(settings.database_url),
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session and guarantee rollback-on-error / close-always."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Close the connection pool. Called from the FastAPI lifespan shutdown."""
    await engine.dispose()
