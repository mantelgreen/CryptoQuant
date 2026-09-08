-- Hypertable 与索引（M0：仅建 symbols + 必要的元数据；
-- klines / factor_values / sim_positions / equity_curves / llm_usage 等在 M1 的 alembic 迁移中建）

CREATE TABLE IF NOT EXISTS symbols (
    id              BIGSERIAL PRIMARY KEY,
    exchange        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    base_asset      TEXT NOT NULL,
    quote_asset     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'TRADING',
    is_favorite     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exchange, symbol)
);

CREATE INDEX IF NOT EXISTS ix_symbols_favorite ON symbols (is_favorite) WHERE is_favorite;
CREATE INDEX IF NOT EXISTS ix_symbols_symbol_lower ON symbols (lower(symbol));

-- 模型单价表
CREATE TABLE IF NOT EXISTS llm_costs (
    id              BIGSERIAL PRIMARY KEY,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_per_1k   NUMERIC(20, 8) NOT NULL,
    completion_per_1k NUMERIC(20, 8) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    effective_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, model, effective_at)
);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_id        TEXT,
    action          TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    resource_id     TEXT,
    before          JSONB,
    after           JSONB,
    ip              TEXT,
    ua              TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_logs (ts DESC);
