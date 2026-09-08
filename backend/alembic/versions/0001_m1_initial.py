"""M1 初始迁移：建 symbols / klines / download_jobs / scheduled_jobs / factor_*
   / agent_* / sim_* / llm_usage / equity_curves / tickers_24h 表与 hypertable。"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_m1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===== 扩展（需 superuser 执行；docker entrypoint 已装则跳过）=====
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")

    # ===== symbols =====
    op.execute(
        """
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
        """
    )

    # ===== klines (hypertable) =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS klines (
            symbol          TEXT NOT NULL,
            interval        TEXT NOT NULL,
            open_time       TIMESTAMPTZ NOT NULL,
            open            DOUBLE PRECISION NOT NULL,
            high            DOUBLE PRECISION NOT NULL,
            low             DOUBLE PRECISION NOT NULL,
            close           DOUBLE PRECISION NOT NULL,
            volume          DOUBLE PRECISION NOT NULL,
            quote_volume    DOUBLE PRECISION,
            trades_count    INTEGER,
            taker_buy_base  DOUBLE PRECISION,
            taker_buy_quote DOUBLE PRECISION,
            close_time      TIMESTAMPTZ,
            PRIMARY KEY (symbol, interval, open_time)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('klines', 'open_time', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_klines_symbol_ts ON klines (symbol, open_time DESC);"
    )

    # ===== tickers_24h (hypertable) =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tickers_24h (
            ts                  TIMESTAMPTZ NOT NULL,
            symbol              TEXT NOT NULL,
            price_change        DOUBLE PRECISION,
            price_change_pct    DOUBLE PRECISION,
            weighted_avg_price  DOUBLE PRECISION,
            last_price          DOUBLE PRECISION,
            bid_price           DOUBLE PRECISION,
            ask_price           DOUBLE PRECISION,
            high_price          DOUBLE PRECISION,
            low_price           DOUBLE PRECISION,
            volume              DOUBLE PRECISION,
            quote_volume        DOUBLE PRECISION,
            open_time           BIGINT,
            close_time          BIGINT,
            count               INTEGER,
            PRIMARY KEY (symbol, ts)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('tickers_24h', 'ts', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);"
    )

    # ===== download_jobs =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS download_jobs (
            id              UUID PRIMARY KEY,
            symbol          TEXT NOT NULL,
            interval        TEXT NOT NULL,
            start_time      TIMESTAMPTZ NOT NULL,
            end_time        TIMESTAMPTZ NOT NULL,
            mode            TEXT NOT NULL DEFAULT 'full',
            status          TEXT NOT NULL DEFAULT 'pending',
            total_bars      INTEGER,
            downloaded_bars INTEGER DEFAULT 0,
            gaps            JSONB,
            error           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_download_jobs_status ON download_jobs (status);
        CREATE INDEX IF NOT EXISTS ix_download_jobs_symbol ON download_jobs (symbol, interval);
        """
    )

    # ===== scheduled_jobs =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id              UUID PRIMARY KEY,
            name            TEXT NOT NULL,
            cron            TEXT NOT NULL,
            symbols         TEXT[] NOT NULL,
            interval        TEXT NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            last_run_at     TIMESTAMPTZ,
            next_run_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_enabled ON scheduled_jobs (enabled);
        """
    )

    # ===== strategies + versions =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategies (
            id              UUID PRIMARY KEY,
            name            TEXT NOT NULL,
            description     TEXT,
            tags            TEXT[] DEFAULT '{}',
            owner_id        TEXT,
            embedding       vector(1024),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id              UUID PRIMARY KEY,
            strategy_id     UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
            version         INTEGER NOT NULL,
            code            TEXT NOT NULL,
            params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            risk_config     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (strategy_id, version)
        );
        """
    )

    # ===== backtests + related =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS backtests (
            id              UUID PRIMARY KEY,
            strategy_id     UUID NOT NULL,
            version_id      UUID NOT NULL,
            symbols         TEXT[] NOT NULL,
            interval        TEXT NOT NULL,
            start_time      TIMESTAMPTZ NOT NULL,
            end_time        TIMESTAMPTZ NOT NULL,
            params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            status          TEXT NOT NULL DEFAULT 'pending',
            progress_pct    INTEGER DEFAULT 0,
            stage           TEXT,
            summary         JSONB,
            error           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_backtests_status ON backtests (status);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id              BIGSERIAL PRIMARY KEY,
            backtest_id     UUID NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
            ts              TIMESTAMPTZ NOT NULL,
            symbol          TEXT NOT NULL,
            side            TEXT NOT NULL,
            price           DOUBLE PRECISION NOT NULL,
            qty             DOUBLE PRECISION NOT NULL,
            fee             DOUBLE PRECISION DEFAULT 0,
            pnl             DOUBLE PRECISION DEFAULT 0,
            reason          TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_bt_trades_bt ON backtest_trades (backtest_id, ts);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS equity_curves (
            run_type        TEXT NOT NULL,
            run_id          UUID NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            equity          DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (run_type, run_id, ts)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('equity_curves', 'ts', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);"
    )

    # ===== factors =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_library (
            id              UUID PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            description     TEXT,
            tags            TEXT[] DEFAULT '{}',
            docstring       TEXT,
            feature_columns JSONB DEFAULT '[]'::jsonb,
            embedding       vector(1024),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_versions (
            id              UUID PRIMARY KEY,
            factor_id       UUID NOT NULL REFERENCES factor_library(id) ON DELETE CASCADE,
            version         INTEGER NOT NULL,
            code            TEXT NOT NULL,
            params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (factor_id, version)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_values (
            factor_id       UUID NOT NULL,
            symbol          TEXT NOT NULL,
            ts              TIMESTAMPTZ NOT NULL,
            value           DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (factor_id, symbol, ts)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('factor_values', 'ts', "
        "chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_research_runs (
            id              UUID PRIMARY KEY,
            factor_id       UUID NOT NULL,
            spec            JSONB NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            train_metrics   JSONB,
            test_metrics    JSONB,
            error           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ
        );
        """
    )

    # ===== agent =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id              UUID PRIMARY KEY,
            agent_type      TEXT NOT NULL,
            title           TEXT,
            owner_id        TEXT,
            total_tokens    BIGINT NOT NULL DEFAULT 0,
            total_cost_usd  NUMERIC(20, 8) NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id              BIGSERIAL PRIMARY KEY,
            session_id      UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            tokens          INTEGER,
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_agent_messages_session ON agent_messages (session_id, ts);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id              UUID PRIMARY KEY,
            session_id      UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decision        JSONB NOT NULL,
            accepted        BOOLEAN NOT NULL DEFAULT TRUE,
            reject_reason   TEXT
        );
        """
    )

    # ===== sim =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_accounts (
            id              UUID PRIMARY KEY,
            name            TEXT NOT NULL,
            account_type    TEXT NOT NULL CHECK (account_type IN ('manual','strategy','llm')),
            config          JSONB NOT NULL DEFAULT '{}'::jsonb,
            initial_cash    NUMERIC(20, 8) NOT NULL DEFAULT 100000,
            current_cash    NUMERIC(20, 8) NOT NULL DEFAULT 100000,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_orders (
            id              UUID PRIMARY KEY,
            account_id      UUID NOT NULL REFERENCES sim_accounts(id) ON DELETE CASCADE,
            symbol          TEXT NOT NULL,
            side            TEXT NOT NULL,
            order_type      TEXT NOT NULL DEFAULT 'market',
            price           DOUBLE PRECISION,
            qty             DOUBLE PRECISION NOT NULL,
            leverage        INTEGER NOT NULL DEFAULT 1,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            filled_at       TIMESTAMPTZ,
            filled_price    DOUBLE PRECISION,
            fee             DOUBLE PRECISION DEFAULT 0,
            reject_reason   TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_sim_orders_account ON sim_orders (account_id, created_at DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_positions (
            account_id      UUID NOT NULL,
            symbol          TEXT NOT NULL,
            qty             DOUBLE PRECISION NOT NULL,
            avg_price       DOUBLE PRECISION NOT NULL,
            mark_price      DOUBLE PRECISION NOT NULL,
            unrealized_pnl  DOUBLE PRECISION NOT NULL DEFAULT 0,
            ts              TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (account_id, symbol, ts)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('sim_positions', 'ts', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sim_positions_account ON sim_positions (account_id, ts DESC);"
    )

    # ===== llm_usage =====
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage (
            ts                  TIMESTAMPTZ NOT NULL,
            provider            TEXT NOT NULL,
            model               TEXT NOT NULL,
            caller              TEXT NOT NULL,
            prompt_tokens       INTEGER NOT NULL DEFAULT 0,
            completion_tokens   INTEGER NOT NULL DEFAULT 0,
            total_tokens        INTEGER NOT NULL DEFAULT 0,
            cost_usd            NUMERIC(20, 8) NOT NULL DEFAULT 0,
            is_estimated        BOOLEAN NOT NULL DEFAULT FALSE,
            latency_ms          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, provider, model, caller)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('llm_usage', 'ts', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_model ON llm_usage (model, ts DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_usage_caller ON llm_usage (caller, ts DESC);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_costs (
            id                  BIGSERIAL PRIMARY KEY,
            provider            TEXT NOT NULL,
            model               TEXT NOT NULL,
            prompt_per_1k       NUMERIC(20, 8) NOT NULL,
            completion_per_1k   NUMERIC(20, 8) NOT NULL,
            currency            TEXT NOT NULL DEFAULT 'USD',
            effective_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (provider, model, effective_at)
        );
        """
    )

    # ===== audit_logs =====
    op.execute(
        """
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
        """
    )

    # ===== 种子数据 =====
    op.execute(
        """
        INSERT INTO symbols (exchange, symbol, base_asset, quote_asset, status, is_favorite) VALUES
            ('binance', 'BTCUSDT', 'BTC', 'USDT', 'TRADING', TRUE),
            ('binance', 'ETHUSDT', 'ETH', 'USDT', 'TRADING', TRUE),
            ('binance', 'SOLUSDT', 'SOL', 'USDT', 'TRADING', TRUE),
            ('binance', 'BNBUSDT', 'BNB', 'USDT', 'TRADING', TRUE),
            ('binance', 'XRPUSDT', 'XRP', 'USDT', 'TRADING', TRUE),
            ('binance', 'DOGEUSDT', 'DOGE', 'USDT', 'TRADING', FALSE),
            ('binance', 'ADAUSDT', 'ADA', 'USDT', 'TRADING', FALSE),
            ('binance', 'AVAXUSDT', 'AVAX', 'USDT', 'TRADING', FALSE),
            ('binance', 'LINKUSDT', 'LINK', 'USDT', 'TRADING', FALSE),
            ('binance', 'MATICUSDT', 'MATIC', 'USDT', 'TRADING', FALSE)
        ON CONFLICT (exchange, symbol) DO NOTHING;
        """
    )


def downgrade() -> None:
    # 谨慎：M1 初始迁移的 downgrade 仅在 dev 用；按依赖反序删除。
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE;")
    op.execute("DROP TABLE IF EXISTS llm_costs CASCADE;")
    op.execute("DROP TABLE IF EXISTS llm_usage CASCADE;")
    op.execute("DROP TABLE IF EXISTS sim_positions CASCADE;")
    op.execute("DROP TABLE IF EXISTS sim_orders CASCADE;")
    op.execute("DROP TABLE IF EXISTS sim_accounts CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_decisions CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_messages CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_sessions CASCADE;")
    op.execute("DROP TABLE IF EXISTS factor_research_runs CASCADE;")
    op.execute("DROP TABLE IF EXISTS factor_values CASCADE;")
    op.execute("DROP TABLE IF EXISTS factor_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS factor_library CASCADE;")
    op.execute("DROP TABLE IF EXISTS equity_curves CASCADE;")
    op.execute("DROP TABLE IF EXISTS backtest_trades CASCADE;")
    op.execute("DROP TABLE IF EXISTS backtests CASCADE;")
    op.execute("DROP TABLE IF EXISTS strategy_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS strategies CASCADE;")
    op.execute("DROP TABLE IF EXISTS scheduled_jobs CASCADE;")
    op.execute("DROP TABLE IF EXISTS download_jobs CASCADE;")
    op.execute("DROP TABLE IF EXISTS tickers_24h CASCADE;")
    op.execute("DROP TABLE IF EXISTS klines CASCADE;")
    op.execute("DROP TABLE IF EXISTS symbols CASCADE;")
