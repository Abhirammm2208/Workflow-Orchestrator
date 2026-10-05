"""
app/db/database.py
SQLAlchemy async engine, session factory, and dependency injection helper.
Uses asyncpg driver for all ORM operations.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


# ---------------------------------------------------------------------------
# Base class — all ORM models inherit from this
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Engine — single instance shared across the process
# Pool settings tuned for a multi-worker FastAPI app
# ---------------------------------------------------------------------------
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,      # log SQL in dev, silent in prod
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,                 # validate connections before use
    pool_recycle=3600,                  # recycle connections every hour
)

# ---------------------------------------------------------------------------
# Session factory — use this to create DB sessions
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,             # objects stay usable after commit
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# FastAPI dependency — yields a session and guarantees cleanup
# Usage: async def route(db: AsyncSession = Depends(get_db))
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Context manager for non-FastAPI usage (scripts, seed, init_db)
# Usage: async with db_session() as session: ...
# ---------------------------------------------------------------------------
@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    """Create all tables defined in ORM models. Called at startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Cleanly dispose the engine connection pool. Called at shutdown."""
    await engine.dispose()
