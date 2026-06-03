"""
QuantAdvisor — Database Engine y Session Factory (async SQLAlchemy 2.x)
Auto-falls back to SQLite when PostgreSQL is unavailable.
"""
import logging
from typing import AsyncGenerator

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_SQLITE_URL = "sqlite+aiosqlite:////tmp/sigma.db"


def _make_async_engine(url: str):
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=settings.DEBUG,
        )
    try:
        parsed = make_url(url)
        if parsed.drivername in ("postgresql", "postgres"):
            parsed = parsed.set(drivername="postgresql+asyncpg")
        logger.info(f"DB: driver={parsed.drivername} host={parsed.host} db={parsed.database}")
        return create_async_engine(
            str(parsed),
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=settings.DEBUG,
        )
    except Exception as e:
        logger.error(f"DATABASE_URL parse error ({e}) — using SQLite")
        return create_async_engine(
            _SQLITE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=settings.DEBUG,
        )


def _make_session_factory(eng):
    return async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# ─── Engine & Session (module-level, may be replaced in init_db fallback) ────

engine = _make_async_engine(settings.DATABASE_URL)
AsyncSessionLocal = _make_session_factory(engine)


# ─── Dependency ──────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─── Init DB ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables. Auto-falls back to SQLite on any PostgreSQL error."""
    global engine, AsyncSessionLocal
    from app.models.models import Base  # noqa: F401

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"DB init OK [{settings.DATABASE_URL[:30]}...]")
    except Exception as e:
        if not settings.DATABASE_URL.startswith("sqlite"):
            logger.error(f"PostgreSQL init failed ({e}) — switching to SQLite fallback")
            engine = _make_async_engine(_SQLITE_URL)
            AsyncSessionLocal = _make_session_factory(engine)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("SQLite fallback initialized at sigma.db")
        else:
            raise
