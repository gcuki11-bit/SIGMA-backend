"""
QuantAdvisor — Database Engine y Session Factory (async SQLAlchemy 2.x)
Supports both SQLite (default/fallback) and PostgreSQL (production with valid URL).
"""
import logging
from typing import AsyncGenerator

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_engine_url(raw_url: str) -> str:
    """Normalize any postgres/postgresql URL to use the asyncpg driver."""
    if raw_url.startswith("sqlite"):
        return raw_url  # SQLite URLs are already correct
    try:
        parsed = make_url(raw_url)
        if parsed.drivername in ("postgresql", "postgres"):
            parsed = parsed.set(drivername="postgresql+asyncpg")
        logger.info(f"DB: driver={parsed.drivername} host={parsed.host} db={parsed.database}")
        return str(parsed)
    except Exception as e:
        logger.error(f"DATABASE_URL parse error ({e}) — falling back to SQLite")
        return "sqlite+aiosqlite:///./sigma.db"


_db_url = _build_engine_url(settings.DATABASE_URL)
_is_sqlite = _db_url.startswith("sqlite")

if _is_sqlite:
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine(
        _db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=settings.DEBUG,
    )
else:
    engine = create_async_engine(
        _db_url,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.DEBUG,
    )

# ─── Session Factory ─────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


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
    """Create all tables if they don't exist."""
    from app.models.models import Base  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
