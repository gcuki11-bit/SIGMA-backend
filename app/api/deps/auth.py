"""
QuantAdvisor — API Dependencies
Inyección de dependencias para autenticación, autorización y feature flags.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.feature_flags import PlanType, get_plan_features
from app.models.models import Subscription, User

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ─── Token Decoding ───────────────────────────────────────────────────────────

def decode_token(token: str) -> dict:
    """
    Decodifica y valida el JWT.
    Lanza HTTPException si el token es inválido o expirado.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


# ─── get_current_user ─────────────────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency principal de autenticación.
    Valida JWT y retorna el User desde DB.
    """
    payload = decode_token(token)
    user_id: str = payload["sub"]

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada",
        )
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Alias explícito para mayor claridad en endpoints."""
    return current_user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Solo administradores."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido a administradores",
        )
    return current_user


# ─── Plan & Feature Checks ───────────────────────────────────────────────────

async def get_user_active_plan(user: User, db: AsyncSession) -> PlanType:
    """
    Retorna el PlanType activo del usuario.
    Fallback a STARTER si no tiene suscripción activa.
    """
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status.in_(["active", "trialing"]),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        return PlanType.STARTER

    # Verificar que no esté expirada
    if sub.current_period_end and sub.current_period_end < datetime.now(timezone.utc):
        return PlanType.STARTER

    try:
        return PlanType(sub.plan_type)
    except ValueError:
        logger.warning(f"Unknown plan type: {sub.plan_type} for user {user.id}")
        return PlanType.STARTER


class require_feature:
    """
    Dependency factory para verificar features por plan.

    Uso:
        @router.get("/...", dependencies=[Depends(require_feature("ai_news_engine"))])

    O en función:
        async def endpoint(
            current_user: User = Depends(get_current_user),
            _: None = Depends(require_feature("monte_carlo")),
        ):
    """

    def __init__(self, feature_name: str):
        self.feature_name = feature_name

    async def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        plan = await get_user_active_plan(current_user, db)
        features = get_plan_features(plan)

        if not getattr(features, self.feature_name, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"La función '{self.feature_name}' no está disponible en tu plan {plan.value}. "
                    "Visitá /billing para hacer upgrade."
                ),
            )


class require_plan:
    """
    Dependency factory para requerir un plan mínimo.

    Uso:
        @router.post("/...", dependencies=[Depends(require_plan(PlanType.ELITE))])
    """

    PLAN_HIERARCHY = {
        PlanType.STARTER: 0,
        PlanType.PRO: 1,
        PlanType.ELITE: 2,
        PlanType.INSTITUTIONAL: 3,
    }

    def __init__(self, minimum_plan: PlanType):
        self.minimum_plan = minimum_plan

    async def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        plan = await get_user_active_plan(current_user, db)
        user_level = self.PLAN_HIERARCHY.get(plan, 0)
        required_level = self.PLAN_HIERARCHY.get(self.minimum_plan, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Esta función requiere plan {self.minimum_plan.value} o superior. "
                    f"Tu plan actual es {plan.value}."
                ),
            )
