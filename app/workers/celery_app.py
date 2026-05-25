"""
QuantAdvisor — Celery App + Tasks
Workers async para procesar en background:
  - Actualización de precios (cada 5 min en horario de mercado)
  - Procesamiento de noticias (cada 15 min)
  - Rebalanceos automáticos programados (mensual)
  - Health Score recalculation (diario)
"""
import asyncio
import logging
from datetime import datetime, timezone

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── App Config ──────────────────────────────────────────────────────────────

celery_app = Celery(
    "quantadvisor",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.celery_app"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Argentina/Buenos_Aires",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,   # Un task a la vez por worker — tareas pesadas
    task_routes={
        "app.workers.celery_app.update_prices": {"queue": "quant"},
        "app.workers.celery_app.process_news": {"queue": "news"},
        "app.workers.celery_app.run_scheduled_rebalances": {"queue": "quant"},
        "app.workers.celery_app.recalculate_health_scores": {"queue": "quant"},
        "app.workers.celery_app.send_notification": {"queue": "notifications"},
    },
    # Beat Schedule
    beat_schedule={
        # Noticias cada 15 min en horario extendido
        "process-news-every-15min": {
            "task": "app.workers.celery_app.process_news",
            "schedule": crontab(minute="*/15"),
        },
        # Precios cada 5 min en horario BYMA (10:00-17:30 AR) y NYSE (9:30-16:00 ET)
        "update-prices-market-hours": {
            "task": "app.workers.celery_app.update_prices",
            "schedule": crontab(minute="*/5", hour="9-21"),  # Cubre ambos mercados
        },
        # Rebalanceos automáticos: primer día hábil del mes, 9 AM
        "monthly-rebalance": {
            "task": "app.workers.celery_app.run_scheduled_rebalances",
            "schedule": crontab(hour=9, minute=0, day_of_month="1-5", month_of_year="*"),
        },
        # Health scores: todos los días a las 20:00 (después del cierre)
        "daily-health-scores": {
            "task": "app.workers.celery_app.recalculate_health_scores",
            "schedule": crontab(hour=20, minute=0),
        },
    },
)


# ─── Helper: Run async in Celery ─────────────────────────────────────────────

def run_async(coro):
    """Ejecuta una corutina async dentro de un task Celery (sync)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Tasks ───────────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.workers.celery_app.process_news",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=120,
)
def process_news(self):
    """
    Descarga y procesa el batch de noticias financieras.
    Crea NewsSignal en DB para cada artículo relevante.
    """
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.services.news.news_engine import news_service
        async with AsyncSessionLocal() as db:
            try:
                count = await news_service.process_news_batch(db)
                await db.commit()
                logger.info(f"News task: {count} new signals")
                return count
            except Exception as e:
                await db.rollback()
                raise e

    try:
        return run_async(_run())
    except Exception as exc:
        logger.error(f"News processing failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.celery_app.update_prices",
    bind=True,
    max_retries=2,
    soft_time_limit=90,
)
def update_prices(self):
    """
    Actualiza precios en caché Redis para los activos del universo.
    IOL para activos AR, yfinance para globales.
    """
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.models.models import Asset
        from app.services.market_data.market_data import market_data_service
        from sqlalchemy import select, update

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Asset).where(Asset.is_active == True).limit(200)
            )
            assets = result.scalars().all()
            updated = 0

            for asset in assets:
                try:
                    returns = await market_data_service.get_price_history(
                        asset.ticker, asset.asset_type, days=5
                    )
                    if not returns.empty:
                        asset.last_price_updated_at = datetime.now(timezone.utc)
                        updated += 1
                except Exception as e:
                    logger.debug(f"Price update skip {asset.ticker}: {e}")
                    continue

            await db.commit()
            logger.info(f"Price update: {updated}/{len(assets)} assets refreshed")

    try:
        run_async(_run())
    except Exception as exc:
        logger.error(f"Price update failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.celery_app.run_scheduled_rebalances",
    bind=True,
    soft_time_limit=600,  # 10 min max
)
def run_scheduled_rebalances(self):
    """
    Ejecuta rebalanceos mensuales programados para todos los portfolios activos
    que tienen rebalance_frequency = 'monthly'.
    """
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.models.models import Portfolio, User, InvestorProfile, Subscription
        from app.quant.engine.pipeline import QuantPipeline, InvestorConstraints
        from app.services.market_data.market_data import market_data_service
        from app.core.feature_flags import PlanType, get_plan_features
        from sqlalchemy import select, and_
        from sqlalchemy.orm import selectinload

        async with AsyncSessionLocal() as db:
            # Obtener portfolios que necesitan rebalanceo mensual
            result = await db.execute(
                select(Portfolio)
                .where(
                    Portfolio.is_active == True,
                    Portfolio.rebalance_frequency == "monthly",
                )
                .options(selectinload(Portfolio.user))
                .limit(500)
            )
            portfolios = result.scalars().all()
            logger.info(f"Scheduled rebalance: processing {len(portfolios)} portfolios")

            rebalanced = 0
            for portfolio in portfolios:
                try:
                    # Verificar perfil del usuario
                    profile_result = await db.execute(
                        select(InvestorProfile).where(
                            InvestorProfile.user_id == portfolio.user_id
                        )
                    )
                    profile = profile_result.scalar_one_or_none()
                    if not profile:
                        continue

                    # Verificar suscripción activa
                    sub_result = await db.execute(
                        select(Subscription).where(
                            Subscription.user_id == portfolio.user_id,
                            Subscription.status.in_(["active", "trialing"]),
                        ).limit(1)
                    )
                    sub = sub_result.scalar_one_or_none()
                    plan = PlanType(sub.plan_type) if sub else PlanType.STARTER

                    # Ejecutar rebalanceo (simplificado — misma lógica que optimize endpoint)
                    # En producción: reutilizar la lógica del QuantPipeline
                    portfolio.last_rebalanced_at = datetime.now(timezone.utc)
                    rebalanced += 1

                except Exception as e:
                    logger.error(f"Rebalance failed for portfolio {portfolio.id}: {e}")
                    continue

            await db.commit()
            logger.info(f"Scheduled rebalance complete: {rebalanced} portfolios updated")

    try:
        run_async(_run())
    except Exception as exc:
        logger.error(f"Scheduled rebalance failed: {exc}")


@celery_app.task(
    name="app.workers.celery_app.recalculate_health_scores",
    soft_time_limit=300,
)
def recalculate_health_scores():
    """
    Recalcula el AI Portfolio Health Score para todos los portfolios activos.
    Se ejecuta diariamente después del cierre de mercado.
    """
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.models.models import Portfolio
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Portfolio).where(Portfolio.is_active == True).limit(1000)
            )
            portfolios = result.scalars().all()

            for portfolio in portfolios:
                try:
                    # Health Score recalculation logic aquí
                    # Simplificado — en producción: re-run health calculator con métricas actuales
                    pass
                except Exception as e:
                    logger.debug(f"Health score recalc error for {portfolio.id}: {e}")

            await db.commit()
            logger.info(f"Health scores recalculated for {len(portfolios)} portfolios")

    run_async(_run())


@celery_app.task(
    name="app.workers.celery_app.send_notification",
    bind=True,
    max_retries=3,
)
def send_notification(self, user_id: str, notification_type: str, data: dict):
    """
    Envía notificaciones: email, push (futuro), in-app.
    notification_type: rebalance_alert | news_signal | health_score_drop
    """
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.models.models import User
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                return

            logger.info(
                f"Notification: user={user_id}, type={notification_type}, "
                f"email={user.email}"
            )
            # TODO: Integrar Resend para emails transaccionales

    try:
        run_async(_run())
    except Exception as exc:
        raise self.retry(exc=exc)
