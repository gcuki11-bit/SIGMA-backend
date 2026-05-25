"""
QuantAdvisor — Database Engine y Session Factory (async SQLAlchemy 2.x)
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# ─── Engine ──────────────────────────────────────────────────────────────────

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,          # Valida conexiones antes de usar
    pool_recycle=3600,           # Recicla conexiones cada hora
    echo=settings.DEBUG,         # SQL logging solo en dev
)

# ─── Session Factory ─────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,      # Evita lazy-load post-commit en async
    autocommit=False,
    autoflush=False,
)


# ─── Dependency ──────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: inyecta sesión async en cada request.
    Garantiza cierre correcto incluso ante excepciones.
    """
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
    """
    Crea todas las tablas si no existen.
    Solo para dev/test — en producción usar Alembic migrations.
    """
    from app.models.models import Base  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
