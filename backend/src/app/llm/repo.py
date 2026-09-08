"""LLM 供应商/模型设置仓库（base_url、api_key、模型参数）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.models.orm import llm_providers_table


def mask_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "••••"
    return f"{api_key[:4]}••••{api_key[-4:]}"


class LlmProviderRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._t = llm_providers_table()

    async def create(
        self,
        *,
        name: str,
        provider: str,
        base_url: str | None,
        api_key: str | None,
        model: str,
        temperature: float,
        max_tokens: int,
        enabled: bool,
        is_default: bool,
    ) -> dict[str, Any]:
        pid = uuid4()
        await self.db.execute(
            self._t.insert().values(
                id=pid, name=name, provider=provider, base_url=base_url, api_key=api_key,
                model=model, temperature=temperature, max_tokens=max_tokens,
                enabled=enabled, is_default=is_default,
            )
        )
        if is_default:
            await self._clear_default(pid)
        await self.db.commit()
        return await self.get_raw(pid)  # type: ignore[return-value]

    async def _clear_default(self, except_id: UUID | None = None) -> None:
        stmt = update(self._t).where(self._t.c.is_default.is_(True))
        if except_id:
            stmt = stmt.where(self._t.c.id != except_id)
        await self.db.execute(stmt.values(is_default=False))

    async def get_raw(self, pid: UUID) -> dict[str, Any] | None:
        row = (await self.db.execute(select(self._t).where(self._t.c.id == pid))).first()
        return dict(row._mapping) if row else None

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.db.execute(select(self._t).order_by(desc(self._t.c.is_default), self._t.c.created_at))
        return [dict(r._mapping) for r in rows]

    async def update(self, pid: UUID, fields: dict[str, Any]) -> dict[str, Any] | None:
        from datetime import datetime, timezone

        current = await self.get_raw(pid)
        if current is None:
            return None
        values: dict[str, Any] = {}
        for col in ("name", "provider", "base_url", "api_key", "model", "temperature", "max_tokens", "enabled"):
            if col not in fields:
                continue
            v = fields[col]
            if col == "api_key" and (v is None or v == ""):
                continue  # 空 = 保留原 key
            values[col] = v
        if "is_default" in fields and fields["is_default"]:
            await self._clear_default(pid)
            values["is_default"] = True
        if values:
            values["updated_at"] = datetime.now(timezone.utc)
            await self.db.execute(
                update(self._t).where(self._t.c.id == pid).values(**values)
            )
        await self.db.commit()
        return await self.get_raw(pid)

    async def set_default(self, pid: UUID) -> dict[str, Any] | None:
        row = await self.get_raw(pid)
        if row is None:
            return None
        await self._clear_default()
        await self.db.execute(self._t.update().where(self._t.c.id == pid).values(is_default=True))
        await self.db.commit()
        return await self.get_raw(pid)

    async def delete(self, pid: UUID) -> None:
        await self.db.execute(self._t.delete().where(self._t.c.id == pid))
        await self.db.commit()
