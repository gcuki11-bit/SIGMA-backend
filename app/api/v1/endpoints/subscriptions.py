"""
QuantAdvisor — Subscriptions Endpoints
GET  /subscriptions/plans           → Lista planes con pricing
GET  /subscriptions/me              → Suscripción activa del usuario
POST /subscriptions/checkout/stripe → Crea Stripe Checkout Session
POST /subscriptions/checkout/mp     → Crea MercadoPago Preference
POST /subscriptions/webhook/stripe  → Webhook Stripe (raw body)
POST /subscriptions/webhook/mp      → Webhook MercadoPago
POST /subscriptions/cancel          → Cancela suscripción
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.feature_flags import FOUNDER_PRICING, PRICING, BillingPeriod, PlanType
from app.models.models import Invoice, Subscription, User

logger = logging.getLogger(__name__)
router = APIRouter()

# Init Stripe
if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


# ─── Schemas ──────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_type: str
    billing_period: str
    is_founder_pricing: bool = False
    success_url: str = "https://quantadvisor.vercel.app/dashboard?payment=success"
    cancel_url: str = "https://quantadvisor.vercel.app/billing?payment=canceled"


class CancelRequest(BaseModel):
    reason: Optional[str] = None


class ActivateRequest(BaseModel):
    email: str
    plan: str
    billing: str = "monthly"
    provider: str = "stripe"


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans():
    """Retorna todos los planes con pricing actual."""
    plans = []
    for plan_type, periods in PRICING.items():
        plan_info = {
            "plan": plan_type.value,
            "pricing": {period.value: price for period, price in periods.items()},
            "founder_pricing": FOUNDER_PRICING.get(plan_type),
        }
        plans.append(plan_info)

    return {
        "plans": plans,
        "currency": "ARS",
        "founder_beta_active": True,
        "disclaimer": "Precios en pesos argentinos. Incluye IVA.",
    }


@router.post("/activate", include_in_schema=False)
async def activate_plan(
    payload: ActivateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Internal endpoint — called by Vercel webhook after Stripe payment confirmed.
    Creates or updates the user's subscription in the DB.
    """
    # Optional internal secret check
    if settings.INTERNAL_WEBHOOK_SECRET:
        provided = request.headers.get("X-Webhook-Secret", "")
        if provided != settings.INTERNAL_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        # Find user by email
        result = await db.execute(select(User).where(User.email == payload.email))
        user = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"[activate] DB query error: {e}")
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")

    if not user:
        logger.warning(f"[activate] user not found for email={payload.email} — creating minimal record")
        try:
            import uuid as _uuid
            user = User(
                id=str(_uuid.uuid4()),
                email=payload.email,
                role="user",
                is_active=True,
                is_email_verified=False,
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
        except Exception as e:
            logger.error(f"[activate] failed to create user: {e}")
            raise HTTPException(status_code=503, detail=f"Could not create user: {str(e)}")

    try:
        # Deactivate any existing active subscription
        existing = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.status.in_(["active", "trialing"]),
            )
        )
        for old_sub in existing.scalars().all():
            old_sub.status = "expired"

        # Create new subscription
        sub = Subscription(
            user_id=user.id,
            plan_type=payload.plan,
            billing_period=payload.billing,
            status="active",
            current_period_start=datetime.now(timezone.utc),
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    except Exception as e:
        logger.error(f"[activate] DB write error: {e}")
        raise HTTPException(status_code=503, detail=f"Database write error: {str(e)}")

    logger.info(
        f"[activate] plan activated: user={user.id} email={payload.email} "
        f"plan={payload.plan} billing={payload.billing} provider={payload.provider}"
    )

    return {"success": True, "user_id": user.id, "subscription_id": sub.id}


@router.get("/me")
async def get_my_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retorna la suscripción activa del usuario."""
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(["active", "trialing", "past_due"]),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    sub = result.scalar_one_or_none()

    if not sub:
        return {
            "plan": "none",
            "status": "inactive",
            "message": "Sin suscripción activa",
        }

    return {
        "id": sub.id,
        "plan": sub.plan_type,
        "billing_period": sub.billing_period,
        "status": sub.status,
        "is_founder_pricing": sub.is_founder_pricing,
        "amount_ars": sub.amount_ars,
        "current_period_end": sub.current_period_end,
        "trial_ends_at": sub.trial_ends_at,
    }


@router.post("/checkout/stripe")
async def create_stripe_checkout(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea una sesión de Stripe Checkout para suscripción."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe no configurado")

    try:
        plan = PlanType(payload.plan_type)
        period = BillingPeriod(payload.billing_period)
    except ValueError:
        raise HTTPException(status_code=400, detail="Plan o período inválido")

    # Calcular precio
    if payload.is_founder_pricing and period == BillingPeriod.MONTHLY:
        amount_ars = FOUNDER_PRICING.get(plan)
        if amount_ars is None:
            raise HTTPException(status_code=400, detail="Founder pricing no disponible para este plan")
    else:
        amount_ars = PRICING[plan].get(period)
        if amount_ars is None:
            raise HTTPException(status_code=400, detail="Precio no disponible para este período")

    # Convertir a centavos (Stripe usa enteros)
    amount_cents = int(amount_ars * 100)

    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=current_user.email,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "ars",
                    "product_data": {
                        "name": f"QuantAdvisor {plan.value.title()} — {period.value.title()}",
                        "description": f"Plataforma de análisis cuantitativo de portfolios",
                    },
                    "unit_amount": amount_cents,
                    "recurring": {
                        "interval": _period_to_stripe_interval(period),
                        "interval_count": _period_to_stripe_count(period),
                    } if period != BillingPeriod.LIFETIME else None,
                },
                "quantity": 1,
            }],
            mode="subscription" if period != BillingPeriod.LIFETIME else "payment",
            success_url=payload.success_url + "&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=payload.cancel_url,
            metadata={
                "user_id": current_user.id,
                "plan_type": plan.value,
                "billing_period": period.value,
                "is_founder_pricing": str(payload.is_founder_pricing),
            },
        )

        logger.info(
            f"Stripe checkout created: user={current_user.id}, "
            f"plan={plan.value}, period={period.value}"
        )

        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=f"Error en Stripe: {e.user_message}")


@router.post("/checkout/mp")
async def create_mp_checkout(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea una preferencia de MercadoPago."""
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="MercadoPago no configurado")

    try:
        import mercadopago
        sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
    except ImportError:
        raise HTTPException(status_code=503, detail="SDK de MercadoPago no instalado")

    try:
        plan = PlanType(payload.plan_type)
        period = BillingPeriod(payload.billing_period)
    except ValueError:
        raise HTTPException(status_code=400, detail="Plan o período inválido")

    amount_ars = PRICING[plan].get(period, 0)

    preference_data = {
        "items": [{
            "title": f"QuantAdvisor {plan.value.title()} — {period.value.title()}",
            "description": "Plataforma de análisis cuantitativo de portfolios",
            "quantity": 1,
            "currency_id": "ARS",
            "unit_price": float(amount_ars),
        }],
        "payer": {"email": current_user.email},
        "back_urls": {
            "success": payload.success_url,
            "failure": payload.cancel_url,
            "pending": payload.cancel_url,
        },
        "auto_return": "approved",
        "metadata": {
            "user_id": current_user.id,
            "plan_type": plan.value,
            "billing_period": period.value,
        },
        "notification_url": f"{settings.NEXTAUTH_URL}/api/v1/subscriptions/webhook/mp",
    }

    result = sdk.preference().create(preference_data)
    if result["status"] not in [200, 201]:
        raise HTTPException(status_code=500, detail="Error creando preferencia MP")

    preference = result["response"]
    logger.info(f"MercadoPago preference created: user={current_user.id}")

    return {
        "preference_id": preference["id"],
        "init_point": preference["init_point"],
        "sandbox_init_point": preference.get("sandbox_init_point"),
    }


@router.post("/webhook/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook de Stripe — debe recibir raw body para validar firma.
    """
    body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            body, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Stripe webhook received: {event_type}")

    if event_type == "checkout.session.completed":
        await _handle_stripe_checkout_completed(data, db)
    elif event_type == "customer.subscription.updated":
        await _handle_stripe_subscription_updated(data, db)
    elif event_type == "customer.subscription.deleted":
        await _handle_stripe_subscription_deleted(data, db)
    elif event_type == "invoice.payment_succeeded":
        await _handle_stripe_invoice_paid(data, db)
    elif event_type == "invoice.payment_failed":
        await _handle_stripe_payment_failed(data, db)

    return {"received": True}


@router.post("/cancel")
async def cancel_subscription(
    payload: CancelRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancela la suscripción activa al final del período."""
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == "active",
        )
        .limit(1)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Sin suscripción activa para cancelar")

    if sub.stripe_subscription_id:
        try:
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                cancel_at_period_end=True,
            )
        except stripe.error.StripeError as e:
            logger.error(f"Stripe cancel error: {e}")

    sub.status = "canceled"
    sub.canceled_at = datetime.now(timezone.utc)
    logger.info(f"Subscription canceled: user={current_user.id}, plan={sub.plan_type}")

    return {
        "message": "Suscripción cancelada. Seguís teniendo acceso hasta el fin del período.",
        "access_until": sub.current_period_end,
    }


# ─── Stripe Event Handlers ───────────────────────────────────────────────────

async def _handle_stripe_checkout_completed(data: dict, db: AsyncSession):
    """Activa suscripción tras pago exitoso en Stripe Checkout."""
    metadata = data.get("metadata", {})
    user_id = metadata.get("user_id")
    plan_type = metadata.get("plan_type")
    billing_period = metadata.get("billing_period")
    is_founder = metadata.get("is_founder_pricing", "False") == "True"

    if not user_id:
        return

    # Calcular precio pagado
    amount_total = data.get("amount_total", 0) / 100  # de centavos a ARS

    sub = Subscription(
        user_id=user_id,
        plan_type=plan_type,
        billing_period=billing_period,
        status="active",
        amount_ars=amount_total,
        is_founder_pricing=is_founder,
        stripe_subscription_id=data.get("subscription"),
        stripe_customer_id=data.get("customer"),
        current_period_start=datetime.now(timezone.utc),
    )
    db.add(sub)
    logger.info(f"Subscription activated via Stripe: user={user_id}, plan={plan_type}")


async def _handle_stripe_subscription_updated(data: dict, db: AsyncSession):
    """Actualiza estado de suscripción."""
    stripe_sub_id = data.get("id")
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    sub = result.scalar_one_or_none()
    if sub:
        sub.status = data.get("status", sub.status)
        logger.info(f"Stripe subscription updated: {stripe_sub_id} → {sub.status}")


async def _handle_stripe_subscription_deleted(data: dict, db: AsyncSession):
    stripe_sub_id = data.get("id")
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    sub = result.scalar_one_or_none()
    if sub:
        sub.status = "expired"
        sub.ended_at = datetime.now(timezone.utc)


async def _handle_stripe_invoice_paid(data: dict, db: AsyncSession):
    stripe_sub_id = data.get("subscription")
    if not stripe_sub_id:
        return
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    sub = result.scalar_one_or_none()
    if sub:
        invoice = Invoice(
            subscription_id=sub.id,
            amount_ars=data.get("amount_paid", 0) / 100,
            status="paid",
            external_invoice_id=data.get("id"),
            invoice_url=data.get("hosted_invoice_url"),
            paid_at=datetime.now(timezone.utc),
        )
        db.add(invoice)


async def _handle_stripe_payment_failed(data: dict, db: AsyncSession):
    stripe_sub_id = data.get("subscription")
    if not stripe_sub_id:
        return
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    sub = result.scalar_one_or_none()
    if sub:
        sub.status = "past_due"
        logger.warning(f"Payment failed for subscription: {stripe_sub_id}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _period_to_stripe_interval(period: BillingPeriod) -> str:
    mapping = {
        BillingPeriod.MONTHLY: "month",
        BillingPeriod.QUARTERLY: "month",
        BillingPeriod.ANNUAL: "year",
        BillingPeriod.TRIENNIAL: "year",
    }
    return mapping.get(period, "month")


def _period_to_stripe_count(period: BillingPeriod) -> int:
    mapping = {
        BillingPeriod.MONTHLY: 1,
        BillingPeriod.QUARTERLY: 3,
        BillingPeriod.ANNUAL: 1,
        BillingPeriod.TRIENNIAL: 3,
    }
    return mapping.get(period, 1)
