"""统一配置（pydantic-settings）。"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    环境变量通过 .env 加载；DATABASE_URL / REDIS_URL 由组件自动计算。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== App =====
    APP_NAME: str = "CryptoQuant"
    APP_ENV: str = "dev"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_LOG_LEVEL: str = "INFO"
    APP_SECRET_KEY: str = "please-change-me-in-prod"
    APP_CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8000"

    # ===== PostgreSQL =====
    DATABASE_HOST: str = "127.0.0.1"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "cryptoquant_db"
    DATABASE_USER: str = "root"
    DATABASE_PASSWORD: str = "123456"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # ===== Redis =====
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 8
    REDIS_PASSWORD: Optional[str] = None

    # ===== Binance =====
    BA_BASE_URL: str = "https://api.binance.com"
    KLINE_ENDPOINT: str = "/api/v3/klines"
    BA_RATE_LIMIT_PER_MIN: int = 4500
    BA_REQUEST_TIMEOUT: float = 10.0
    BA_PROXY_URL: Optional[str] = None

    # ===== LLM =====
    LLM_PROVIDER: str = "openai"
    LLM_API_KEY: Optional[str] = None
    LLM_DEFAULT_MODEL: str = "gpt-4o-mini"

    # ===== Job Queues =====
    JOB_QUEUE_DATA: str = "data"
    JOB_QUEUE_COMPUTE: str = "compute"
    JOB_QUEUE_LLM: str = "llm"
    JOB_QUEUE_SIM: str = "sim"
    JOB_QUEUE_ANALYTICS: str = "analytics"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL_SYNC(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.APP_CORS_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
