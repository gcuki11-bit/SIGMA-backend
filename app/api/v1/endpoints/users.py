"""
QuantAdvisor — Users Endpoints
GET  /users/me                  → Perfil del usuario
PATCH /users/me                 → Actualizar datos personales
POST /users/me/profile          → Guardar perfil de inversor
GET  /users/me/profile          → Obtener perfil de inversor
PATCH /users/me/profile/restrictions → Actualizar restricciones personalizadas
DELETE /users/me                → Eliminar cuenta (GDPR)
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, get_user_active_plan
from app.core.database import get_db
from app.core.feature_flags import get_plan_features
from app.models.models import InvestorProfile, User

logger = logging.getLogger(__name__)
router = APIRouter()

RESTRICTION_DISCLAIMER = (
    "La modificación manual de restricciones sectoriales o geográficas puede alterar "
    "el perfil riesgo/rendimiento esperado del portfolio óptimo calculado por el sistema."
)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class InvestorProfileRequest(BaseModel):
    risk_score: int
    risk_classification: str
    max_beta: float
    max_drawdown_tolerance: float
    max_volatility: float
    time_horizon_years: Optional[int] = None
    dividend_preference: bool = False
    liquidity_need: str = "low"
    questionnaire_responses: Optional[Dict] = None
    questionnaire_version: str = "1.0"
    max_single_asset_weight: float = 0.20
    max_sector_weight: float = 0.35


class RestrictionsUpdateRequest(BaseModel):
    excluded_sectors: Optional[List[str]] = None
    excluded_countries: Optional[List[str]] = None
    excluded_tickers: Optional[List[str]] = None
    preferred_sectors: Optional[List[str]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "excluded_sectors": ["Energy", "Defense"],
                "excluded_countries": ["CN"],
                "excluded_tickers": ["TSLA", "XOM"],
                "preferred_sectors": ["Technology", "Healthcare"],
            }
        }


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await get_user_active_plan(current_user, db)
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "avatar_url": current_user.avatar_url,
        "role": current_user.role,
        "is_email_verified": current_user.is_email_verified,
        "plan": plan.value,
        "created_at": current_user.created_at,
        "last_login_at": current_user.last_login_at,
    }


@router.patch("/me")
async def update_me(
    payload: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url
    await db.flush()
    return {"message": "Perfil actualizado"}


@router.post("/me/profile", status_code=status.HTTP_201_CREATED)
async def save_investor_profile(
    payload: InvestorProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Guarda o actualiza el perfil de inversor calculado por el cuestionario.
    Upsert: si ya existe, actualiza.
    """
    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = InvestorProfile(user_id=current_user.id)
        db.add(profile)

    profile.risk_score = payload.risk_score
    profile.risk_classification = payload.risk_classification
    profile.max_beta = payload.max_beta
    profile.max_drawdown_tolerance = payload.max_drawdown_tolerance
    profile.max_volatility = payload.max_volatility
    profile.time_horizon_years = payload.time_horizon_years
    profile.dividend_preference = payload.dividend_preference
    profile.liquidity_need = payload.liquidity_need
    profile.questionnaire_responses = payload.questionnaire_responses
    profile.questionnaire_version = payload.questionnaire_version
    profile.max_single_asset_weight = payload.max_single_asset_weight
    profile.max_sector_weight = payload.max_sector_weight

    await db.flush()
    await db.refresh(profile)

    logger.info(
        f"Investor profile saved: user={current_user.id}, "
        f"classification={payload.risk_classification}, score={payload.risk_score}"
    )

    return {
        "id": profile.id,
        "risk_score": profile.risk_score,
        "risk_classification": profile.risk_classification,
        "max_beta": profile.max_beta,
        "max_drawdown_tolerance": profile.max_drawdown_tolerance,
        "max_volatility": profile.max_volatility,
        "time_horizon_years": profile.time_horizon_years,
        "created_at": profile.created_at,
    }


@router.get("/me/profile")
async def get_investor_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Perfil de inversor no completado. Realizá el cuestionario primero.",
        )

    return {
        "risk_score": profile.risk_score,
        "risk_classification": profile.risk_classification,
        "max_beta": profile.max_beta,
        "max_drawdown_tolerance": profile.max_drawdown_tolerance,
        "max_volatility": profile.max_volatility,
        "time_horizon_years": profile.time_horizon_years,
        "dividend_preference": profile.dividend_preference,
        "liquidity_need": profile.liquidity_need,
        "excluded_sectors": profile.excluded_sectors or [],
        "excluded_countries": profile.excluded_countries or [],
        "excluded_tickers": profile.excluded_tickers or [],
        "preferred_sectors": profile.preferred_sectors or [],
        "health_score": profile.health_score,
        "updated_at": profile.updated_at,
    }


@router.patch("/me/profile/restrictions")
async def update_restrictions(
    payload: RestrictionsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Actualiza restricciones personalizadas.
    Disponible en Plan Pro+.
    Incluye disclaimer obligatorio en la respuesta.
    """
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    if not features.custom_restrictions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Las restricciones personalizadas requieren Plan Pro o superior.",
        )

    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil de inversor no encontrado")

    if payload.excluded_sectors is not None:
        profile.excluded_sectors = payload.excluded_sectors
    if payload.excluded_countries is not None:
        profile.excluded_countries = payload.excluded_countries
    if payload.excluded_tickers is not None:
        profile.excluded_tickers = payload.excluded_tickers
    if payload.preferred_sectors is not None:
        profile.preferred_sectors = payload.preferred_sectors

    await db.flush()
    logger.info(f"Restrictions updated for user {current_user.id}")

    return {
        "message": "Restricciones actualizadas correctamente",
        "disclaimer": RESTRICTION_DISCLAIMER,
        "excluded_sectors": profile.excluded_sectors,
        "excluded_countries": profile.excluded_countries,
        "excluded_tickers": profile.excluded_tickers,
    }


@router.delete("/me", status_code=status.HTTP_200_OK)
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina la cuenta del usuario (GDPR compliance).
    Soft delete — desactiva la cuenta y anonimiza el email.
    """
    current_user.is_active = False
    current_user.email = f"deleted_{current_user.id}@deleted.quantadvisor.com"
    current_user.full_name = "[Deleted]"
    current_user.hashed_password = None
    current_user.google_id = None

    logger.info(f"Account deleted (soft): {current_user.id}")
    return {"message": "Cuenta eliminada correctamente"}
