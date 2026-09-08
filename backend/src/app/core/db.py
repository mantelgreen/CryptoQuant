"""异步 SQLAlchemy 引擎与会话。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_settings = get_settings()
engine = create_async_engine(
    _settings.DATABASE_URL,
    pool_size=_settings.DATABASE_POOL_SIZE,
    max_overflow=_settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    future=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends：注入 AsyncSession。"""
    async with SessionLocal() as session:
        yield session


async def ping_db() -> bool:
    """健康检查用。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
