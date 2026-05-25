"""Initial migration — all tables

Revision ID: 001_initial
Revises: 
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(255)),
        sa.Column('full_name', sa.String(255)),
        sa.Column('avatar_url', sa.String(512)),
        sa.Column('role', sa.String(20), nullable=False, server_default='user'),
        sa.Column('google_id', sa.String(255), unique=True),
        sa.Column('oauth_provider', sa.String(50)),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('email_verification_token', sa.String(255)),
        sa.Column('password_reset_token', sa.String(255)),
        sa.Column('password_reset_expires', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_login_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_google_id', 'users', ['google_id'])

    # ── investor_profiles ──────────────────────────────────────────────────
    op.create_table(
        'investor_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('risk_score', sa.Integer()),
        sa.Column('risk_classification', sa.String(30)),
        sa.Column('max_beta', sa.Float()),
        sa.Column('max_drawdown_tolerance', sa.Float()),
        sa.Column('max_volatility', sa.Float()),
        sa.Column('max_single_asset_weight', sa.Float()),
        sa.Column('max_sector_weight', sa.Float()),
        sa.Column('time_horizon_years', sa.Integer()),
        sa.Column('dividend_preference', sa.Boolean(), server_default='false'),
        sa.Column('liquidity_need', sa.String(20), server_default='low'),
        sa.Column('esg_preference', sa.Boolean(), server_default='false'),
        sa.Column('excluded_sectors', postgresql.JSONB()),
        sa.Column('excluded_countries', postgresql.JSONB()),
        sa.Column('excluded_tickers', postgresql.JSONB()),
        sa.Column('preferred_sectors', postgresql.JSONB()),
        sa.Column('questionnaire_responses', postgresql.JSONB()),
        sa.Column('questionnaire_version', sa.String(10), server_default='1.0'),
        sa.Column('health_score', sa.Integer()),
        sa.Column('health_score_breakdown', postgresql.JSONB()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_investor_profiles_user_id', 'investor_profiles', ['user_id'])

    # ── subscriptions ──────────────────────────────────────────────────────
    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_type', sa.String(30), nullable=False),
        sa.Column('billing_period', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='trialing'),
        sa.Column('amount_ars', sa.Float()),
        sa.Column('is_founder_pricing', sa.Boolean(), server_default='false'),
        sa.Column('stripe_subscription_id', sa.String(255), unique=True),
        sa.Column('stripe_customer_id', sa.String(255)),
        sa.Column('mp_subscription_id', sa.String(255), unique=True),
        sa.Column('mp_preapproval_id', sa.String(255)),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True)),
        sa.Column('current_period_start', sa.DateTime(timezone=True)),
        sa.Column('current_period_end', sa.DateTime(timezone=True)),
        sa.Column('canceled_at', sa.DateTime(timezone=True)),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_subscriptions_user_id', 'subscriptions', ['user_id'])
    op.create_index('ix_subscriptions_status', 'subscriptions', ['status'])

    # ── invoices ───────────────────────────────────────────────────────────
    op.create_table(
        'invoices',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('subscription_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount_ars', sa.Float(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('invoice_url', sa.String(512)),
        sa.Column('external_invoice_id', sa.String(255)),
        sa.Column('paid_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── assets ─────────────────────────────────────────────────────────────
    op.create_table(
        'assets',
        sa.Column('ticker', sa.String(20), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('asset_type', sa.String(30), nullable=False),
        sa.Column('exchange', sa.String(20)),
        sa.Column('currency', sa.String(5), server_default='USD'),
        sa.Column('country', sa.String(3)),
        sa.Column('sector', sa.String(50)),
        sa.Column('industry', sa.String(100)),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('is_operable_from_argentina', sa.Boolean(), server_default='true'),
        sa.Column('fundamental_data', postgresql.JSONB()),
        sa.Column('technical_data', postgresql.JSONB()),
        sa.Column('liquidity_data', postgresql.JSONB()),
        sa.Column('bond_data', postgresql.JSONB()),
        sa.Column('last_price', sa.Float()),
        sa.Column('last_price_updated_at', sa.DateTime(timezone=True)),
        sa.Column('data_updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_assets_asset_type', 'assets', ['asset_type'])
    op.create_index('ix_assets_country', 'assets', ['country'])
    op.create_index('ix_assets_sector', 'assets', ['sector'])

    # ── portfolios ─────────────────────────────────────────────────────────
    op.create_table(
        'portfolios',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False, server_default='Mi Portfolio'),
        sa.Column('optimization_model', sa.String(30), server_default='markowitz'),
        sa.Column('simulated_capital_ars', sa.Float()),
        sa.Column('target_return', sa.Float()),
        sa.Column('realized_return_ytd', sa.Float()),
        sa.Column('realized_return_1y', sa.Float()),
        sa.Column('sharpe_ratio', sa.Float()),
        sa.Column('sortino_ratio', sa.Float()),
        sa.Column('max_drawdown', sa.Float()),
        sa.Column('current_drawdown', sa.Float()),
        sa.Column('beta', sa.Float()),
        sa.Column('alpha', sa.Float()),
        sa.Column('volatility_annual', sa.Float()),
        sa.Column('tracking_error', sa.Float()),
        sa.Column('var_95', sa.Float()),
        sa.Column('cvar_95', sa.Float()),
        sa.Column('health_score', sa.Integer()),
        sa.Column('health_breakdown', postgresql.JSONB()),
        sa.Column('last_rebalanced_at', sa.DateTime(timezone=True)),
        sa.Column('next_rebalance_at', sa.DateTime(timezone=True)),
        sa.Column('rebalance_frequency', sa.String(20), server_default='monthly'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_portfolios_user_id', 'portfolios', ['user_id'])

    # ── positions ──────────────────────────────────────────────────────────
    op.create_table(
        'positions',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('portfolio_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('portfolios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ticker', sa.String(20), sa.ForeignKey('assets.ticker'), nullable=False),
        sa.Column('weight_recommended', sa.Float(), nullable=False),
        sa.Column('weight_actual', sa.Float()),
        sa.Column('weight_is_manual', sa.Boolean(), server_default='false'),
        sa.Column('contribution_to_return', sa.Float()),
        sa.Column('contribution_to_risk', sa.Float()),
        sa.Column('position_beta', sa.Float()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('portfolio_id', 'ticker', name='uq_portfolio_ticker'),
    )
    op.create_index('ix_positions_portfolio_id', 'positions', ['portfolio_id'])

    # ── rebalance_events ───────────────────────────────────────────────────
    op.create_table(
        'rebalance_events',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('portfolio_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('portfolios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trigger_type', sa.String(30), nullable=False),
        sa.Column('trigger_detail', sa.Text()),
        sa.Column('old_weights', postgresql.JSONB()),
        sa.Column('new_weights', postgresql.JSONB()),
        sa.Column('metrics_before', postgresql.JSONB()),
        sa.Column('metrics_after', postgresql.JSONB()),
        sa.Column('ai_explanation', sa.Text()),
        sa.Column('ai_tokens_used', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── news_signals ───────────────────────────────────────────────────────
    op.create_table(
        'news_signals',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('source_url', sa.String(512)),
        sa.Column('headline', sa.Text(), nullable=False),
        sa.Column('body_summary', sa.Text()),
        sa.Column('sentiment_score', sa.Float()),
        sa.Column('sentiment_label', sa.String(20)),
        sa.Column('impact_level', sa.String(20)),
        sa.Column('event_category', sa.String(30)),
        sa.Column('affected_tickers', postgresql.JSONB()),
        sa.Column('affected_sectors', postgresql.JSONB()),
        sa.Column('affected_countries', postgresql.JSONB()),
        sa.Column('rebalance_recommendation', sa.Text()),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('content_hash', sa.String(64), unique=True),
    )
    op.create_index('ix_news_signals_source', 'news_signals', ['source'])
    op.create_index('ix_news_signals_impact_level', 'news_signals', ['impact_level'])
    op.create_index('ix_news_signals_published_at', 'news_signals', ['published_at'])

    # ── ai_sessions ────────────────────────────────────────────────────────
    op.create_table(
        'ai_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_limit', sa.Integer(), nullable=False, server_default='5000'),
        sa.Column('session_context', sa.String(50)),
        sa.Column('session_date', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('is_exhausted', sa.Boolean(), server_default='false'),
    )
    op.create_index('ix_ai_sessions_user_id', 'ai_sessions', ['user_id'])
    op.create_index('ix_ai_sessions_session_date', 'ai_sessions', ['session_date'])

    # ── audit_logs ─────────────────────────────────────────────────────────
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id')),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_type', sa.String(50)),
        sa.Column('resource_id', sa.String(255)),
        sa.Column('details', postgresql.JSONB()),
        sa.Column('ip_address', sa.String(45)),
        sa.Column('user_agent', sa.String(512)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('ai_sessions')
    op.drop_table('news_signals')
    op.drop_table('rebalance_events')
    op.drop_table('positions')
    op.drop_table('portfolios')
    op.drop_table('assets')
    op.drop_table('invoices')
    op.drop_table('subscriptions')
    op.drop_table('investor_profiles')
    op.drop_table('users')
