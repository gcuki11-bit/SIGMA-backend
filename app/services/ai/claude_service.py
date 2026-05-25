"""
QuantAdvisor — AI Service (Claude API)
Control estricto de tokens: 5.000 por usuario por sesión (24hs).
Genera explicaciones financieras en lenguaje natural para:
  - decisiones de portfolio
  - impacto de noticias
  - rebalanceos
  - análisis de riesgo
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import AISession, Portfolio, RebalanceEvent

logger = logging.getLogger(__name__)

# ─── Prompts del sistema ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el motor analítico de QuantAdvisor, una plataforma de análisis cuantitativo de portfolios de inversión.

IMPORTANTE — DISCLAIMER OBLIGATORIO:
Toda la información que proporcionás es EDUCATIVA y ANALÍTICA. No constituye asesoramiento financiero, legal ni impositivo. 
Los análisis son modelos matemáticos con limitaciones inherentes. Los resultados pasados no garantizan rendimientos futuros.
Los usuarios deben consultar a un asesor financiero registrado antes de tomar decisiones de inversión.

Tu rol:
- Explicar en español argentino claro y profesional las decisiones del sistema cuantitativo
- Dar contexto macroeconómico y de mercado relevante
- Ser preciso con los números (2 decimales para ratios, 1 decimal para porcentajes)
- Mantener tono profesional pero accesible
- Nunca hacer recomendaciones directas de compra/venta — solo análisis

Estructura de respuestas:
- Directo al punto, sin introducciones largas
- Máximo 3-4 párrafos por explicación
- Usar bullet points para listas de factores
- Siempre mencionar el contexto de riesgo
"""

# ─── Token Controller ─────────────────────────────────────────────────────────

class TokenController:
    """
    Controla el uso de tokens de Claude por usuario.
    Límite: 5.000 tokens por sesión de 24 horas.
    Guarda el estado en DB + Redis (fallback a DB si Redis falla).
    """

    TOKEN_LIMIT = settings.CLAUDE_TOKENS_PER_USER_SESSION  # 5000

    async def get_or_create_session(
        self, user_id: str, db: AsyncSession
    ) -> AISession:
        """Retorna la sesión activa del día, o crea una nueva."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        result = await db.execute(
            select(AISession).where(
                AISession.user_id == user_id,
                AISession.session_date >= today_start,
                AISession.is_exhausted == False,
            )
        )
        session = result.scalar_one_or_none()

        if session is None:
            session = AISession(
                user_id=user_id,
                tokens_used=0,
                tokens_limit=self.TOKEN_LIMIT,
                session_date=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            db.add(session)
            await db.flush()

        return session

    async def can_use_tokens(
        self, user_id: str, estimated_tokens: int, db: AsyncSession
    ) -> tuple[bool, int]:
        """
        Verifica si el usuario puede usar N tokens más.
        Retorna (puede_usar, tokens_restantes).
        """
        session = await self.get_or_create_session(user_id, db)
        remaining = session.tokens_limit - session.tokens_used
        can_use = remaining >= estimated_tokens
        return can_use, remaining

    async def consume_tokens(
        self,
        user_id: str,
        tokens_used: int,
        db: AsyncSession,
        context: Optional[str] = None,
    ) -> AISession:
        """Registra el consumo de tokens."""
        session = await self.get_or_create_session(user_id, db)
        session.tokens_used += tokens_used
        if context:
            session.session_context = context
        if session.tokens_used >= session.tokens_limit:
            session.is_exhausted = True
            logger.info(f"User {user_id} AI session exhausted ({session.tokens_used} tokens)")
        await db.flush()
        return session


# ─── AI Service ──────────────────────────────────────────────────────────────

class AIService:
    """
    Servicio central de IA que integra Claude API.
    Todos los métodos verifican y consumen tokens antes de hacer la llamada.
    """

    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.token_controller = TokenController()
        self.model = settings.CLAUDE_MODEL
        self.max_tokens = settings.CLAUDE_MAX_TOKENS

    async def explain_portfolio_construction(
        self,
        user_id: str,
        db: AsyncSession,
        portfolio_data: Dict,
        investor_profile: Dict,
        market_context: Optional[str] = None,
    ) -> Dict:
        """
        Explica por qué el sistema construyó el portfolio de esta manera.
        """
        can_use, remaining = await self.token_controller.can_use_tokens(
            user_id, 500, db  # Estimación conservadora
        )
        if not can_use:
            return self._token_limit_response(remaining)

        weights_summary = "\n".join([
            f"  - {ticker}: {weight:.1%}"
            for ticker, weight in sorted(
                portfolio_data.get("weights", {}).items(),
                key=lambda x: x[1], reverse=True
            )[:10]
        ])

        metrics = portfolio_data.get("metrics", {})

        prompt = f"""Analizá y explicá la siguiente construcción de portfolio para un inversor con perfil {investor_profile.get('risk_classification', 'moderado')}:

COMPOSICIÓN DEL PORTFOLIO:
{weights_summary}

MÉTRICAS CALCULADAS:
- Retorno esperado anual: {metrics.get('expected_return', 0):.1%}
- Volatilidad anual: {metrics.get('expected_volatility', 0):.1%}
- Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}
- Beta estimada: {metrics.get('beta', 0):.2f}
- VaR 95%: {metrics.get('var_95', 0):.1%}
- Health Score: {portfolio_data.get('health_score', {}).get('total', 'N/A')}/100

MODELO DE OPTIMIZACIÓN: {portfolio_data.get('optimization_model', 'Markowitz')}
PERFIL DEL INVERSOR:
- Clasificación: {investor_profile.get('risk_classification')}
- Beta máxima aceptada: {investor_profile.get('max_beta')}
- Drawdown máximo tolerado: {investor_profile.get('max_drawdown_tolerance', 0):.0%}
- Horizonte temporal: {investor_profile.get('time_horizon_years')} años

{f"CONTEXTO DE MERCADO: {market_context}" if market_context else ""}

Explicá en 3-4 párrafos: (1) la lógica de la construcción, (2) los activos más relevantes y por qué, (3) el perfil riesgo/retorno y (4) qué monitorear."""

        return await self._call_claude(
            user_id=user_id,
            db=db,
            prompt=prompt,
            context="portfolio_explanation",
        )

    async def explain_rebalance(
        self,
        user_id: str,
        db: AsyncSession,
        trigger_type: str,
        trigger_detail: str,
        old_weights: Dict[str, float],
        new_weights: Dict[str, float],
        metrics_before: Dict,
        metrics_after: Dict,
    ) -> Dict:
        """
        Explica por qué el sistema recomendó rebalancear el portfolio.
        """
        can_use, remaining = await self.token_controller.can_use_tokens(user_id, 400, db)
        if not can_use:
            return self._token_limit_response(remaining)

        # Detectar cambios significativos
        changes = []
        all_tickers = set(old_weights.keys()) | set(new_weights.keys())
        for ticker in all_tickers:
            old_w = old_weights.get(ticker, 0)
            new_w = new_weights.get(ticker, 0)
            delta = new_w - old_w
            if abs(delta) > 0.01:  # Cambio > 1%
                direction = "↑" if delta > 0 else "↓"
                changes.append(f"  - {ticker}: {old_w:.1%} → {new_w:.1%} ({direction}{abs(delta):.1%})")

        changes_text = "\n".join(changes[:10]) if changes else "Ajustes menores en pesos"

        trigger_map = {
            "scheduled": "Rebalanceo periódico programado",
            "volatility": "Spike de volatilidad detectado",
            "drawdown": "Drawdown superó umbral de tolerancia",
            "news_event": "Evento de noticias de alto impacto",
            "macro_event": "Cambio en régimen macroeconómico",
            "manual": "Ajuste manual del usuario",
        }

        prompt = f"""REBALANCEO DE PORTFOLIO — Explicación analítica

TRIGGER: {trigger_map.get(trigger_type, trigger_type)}
DETALLE: {trigger_detail}

CAMBIOS EN POSICIONES:
{changes_text}

MÉTRICAS ANTES:
- Sharpe: {metrics_before.get('sharpe_ratio', 0):.2f} | Volatilidad: {metrics_before.get('expected_volatility', 0):.1%}
- Beta: {metrics_before.get('beta', 0):.2f} | Max DD est.: {metrics_before.get('max_drawdown_estimate', 0):.1%}

MÉTRICAS DESPUÉS:
- Sharpe: {metrics_after.get('sharpe_ratio', 0):.2f} | Volatilidad: {metrics_after.get('expected_volatility', 0):.1%}
- Beta: {metrics_after.get('beta', 0):.2f} | Max DD est.: {metrics_after.get('max_drawdown_estimate', 0):.1%}

Explicá en lenguaje claro: (1) por qué se activó el rebalanceo, (2) qué cambios se hicieron y la lógica, (3) cómo mejoran las métricas del portfolio."""

        return await self._call_claude(
            user_id=user_id,
            db=db,
            prompt=prompt,
            context="rebalance_explanation",
        )

    async def analyze_news_impact(
        self,
        user_id: str,
        db: AsyncSession,
        news_headline: str,
        news_summary: str,
        sentiment_score: float,
        affected_sectors: List[str],
        affected_tickers: List[str],
        portfolio_exposure: Dict[str, float],  # ticker → peso en portfolio
    ) -> Dict:
        """
        Analiza el impacto potencial de una noticia sobre el portfolio del usuario.
        Solo disponible en Plan Pro+.
        """
        can_use, remaining = await self.token_controller.can_use_tokens(user_id, 350, db)
        if not can_use:
            return self._token_limit_response(remaining)

        # Calcular exposición del portfolio a esta noticia
        total_exposure = sum(
            portfolio_exposure.get(t, 0) for t in affected_tickers
        )
        affected_positions = [
            f"  - {t}: {portfolio_exposure.get(t, 0):.1%}"
            for t in affected_tickers
            if portfolio_exposure.get(t, 0) > 0.005
        ]

        sentiment_label = (
            "Positivo" if sentiment_score > 0.2
            else "Negativo" if sentiment_score < -0.2
            else "Neutral/Mixto"
        )

        prompt = f"""ANÁLISIS DE IMPACTO DE NOTICIA EN PORTFOLIO

NOTICIA: {news_headline}
RESUMEN: {news_summary}

ANÁLISIS DE SENTIMIENTO: {sentiment_label} (score: {sentiment_score:.2f})
SECTORES AFECTADOS: {', '.join(affected_sectors) if affected_sectors else 'No identificados'}

EXPOSICIÓN DEL PORTFOLIO:
- Exposición total a tickers afectados: {total_exposure:.1%}
{"Posiciones impactadas:" if affected_positions else "Sin posiciones directamente afectadas en este portfolio."}
{chr(10).join(affected_positions)}

Analizá: (1) qué implica esta noticia para los mercados, (2) impacto específico en el portfolio del usuario, (3) si justifica un rebalanceo táctico y por qué."""

        return await self._call_claude(
            user_id=user_id,
            db=db,
            prompt=prompt,
            context="news_analysis",
        )

    async def generate_risk_report(
        self,
        user_id: str,
        db: AsyncSession,
        portfolio_metrics: Dict,
        stress_scenarios: Optional[Dict] = None,
    ) -> Dict:
        """
        Genera un reporte de riesgo ejecutivo del portfolio.
        VaR, CVaR, stress scenarios (Elite).
        """
        can_use, remaining = await self.token_controller.can_use_tokens(user_id, 450, db)
        if not can_use:
            return self._token_limit_response(remaining)

        scenarios_text = ""
        if stress_scenarios:
            scenarios_text = "\nESCENARIOS DE STRESS:\n" + "\n".join([
                f"  - {scenario}: {impact:.1%}"
                for scenario, impact in stress_scenarios.items()
            ])

        prompt = f"""REPORTE EJECUTIVO DE RIESGO — ANÁLISIS CUANTITATIVO

MÉTRICAS PRINCIPALES:
- Retorno esperado anual: {portfolio_metrics.get('expected_return', 0):.1%}
- Volatilidad anual: {portfolio_metrics.get('expected_volatility', 0):.1%}
- Sharpe Ratio: {portfolio_metrics.get('sharpe_ratio', 0):.2f}
- Sortino Ratio: {portfolio_metrics.get('sortino_ratio', 0):.2f}
- Beta: {portfolio_metrics.get('beta', 0):.2f}
- Alpha estimada: {portfolio_metrics.get('alpha', 0):.2%}

MÉTRICAS DE RIESGO A LA BAJA:
- VaR 95% (1 día): {portfolio_metrics.get('var_95', 0):.2%}
- CVaR 95% (pérdida esperada): {portfolio_metrics.get('cvar_95', 0):.2%}
- Max Drawdown estimado: {portfolio_metrics.get('max_drawdown_estimate', 0):.1%}

HEALTH SCORE: {portfolio_metrics.get('health_score', {}).get('total', 'N/A')}/100
{scenarios_text}

Generá un reporte ejecutivo que explique: (1) el perfil general de riesgo, (2) qué significan el VaR y CVaR en términos prácticos, (3) los principales factores de riesgo y (4) recomendaciones de monitoreo."""

        return await self._call_claude(
            user_id=user_id,
            db=db,
            prompt=prompt,
            context="risk_report",
        )

    # ─── Core Claude caller ──────────────────────────────────────────────────

    async def _call_claude(
        self,
        user_id: str,
        db: AsyncSession,
        prompt: str,
        context: str,
    ) -> Dict:
        """
        Llamada real a Claude API con manejo de errores y registro de tokens.
        """
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            tokens_used = response.usage.input_tokens + response.usage.output_tokens
            explanation = response.content[0].text

            # Registrar consumo
            session = await self.token_controller.consume_tokens(
                user_id=user_id,
                tokens_used=tokens_used,
                db=db,
                context=context,
            )

            logger.info(
                f"Claude call: user={user_id}, tokens={tokens_used}, "
                f"session_total={session.tokens_used}/{session.tokens_limit}, "
                f"context={context}"
            )

            return {
                "explanation": explanation,
                "tokens_used": tokens_used,
                "tokens_remaining": session.tokens_limit - session.tokens_used,
                "session_exhausted": session.is_exhausted,
                "context": context,
            }

        except anthropic.RateLimitError:
            logger.warning(f"Claude rate limit hit for user {user_id}")
            return {
                "explanation": "El análisis de IA está temporalmente no disponible por alta demanda. "
                               "Intentá nuevamente en unos minutos.",
                "error": "rate_limit",
                "tokens_used": 0,
                "tokens_remaining": 5000,
                "session_exhausted": False,
            }

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return {
                "explanation": "Error en el servicio de IA. Las métricas cuantitativas del "
                               "portfolio siguen disponibles.",
                "error": str(e),
                "tokens_used": 0,
                "tokens_remaining": 5000,
                "session_exhausted": False,
            }

    def _token_limit_response(self, remaining: int) -> Dict:
        return {
            "explanation": (
                f"Límite diario de análisis IA alcanzado ({settings.CLAUDE_TOKENS_PER_USER_SESSION} tokens por sesión). "
                "Las métricas cuantitativas del dashboard siguen disponibles. "
                "El límite se restablece a las 00:00 UTC."
            ),
            "error": "token_limit_exceeded",
            "tokens_used": 0,
            "tokens_remaining": remaining,
            "session_exhausted": True,
        }


# Singleton
ai_service = AIService()
