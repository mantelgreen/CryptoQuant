"""/api/v1/settings/models —— LLM 模型/供应商设置（base_url、api_key、默认模型等）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db import get_db
from ...core.errors import BadRequest, NotFound
from ...llm.repo import LlmProviderRepository, mask_key
from ...llm.client import llm_chat
from ...llm.usage import record_usage

router = APIRouter(prefix="/settings/models", tags=["settings-llm"])

PROVIDERS = {"openai", "anthropic", "deepseek", "ollama", "custom", "moonshot"}


def _public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["id"] = str(out["id"])
    out["api_key"] = mask_key(out.get("api_key"))
    return out


class ProviderReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    provider: str = Field(default="openai")
    base_url: str | None = None
    api_key: str | None = None
    model: str = Field(..., min_length=1, max_length=120)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=1, le=100_000)
    disable_thinking: bool = False
    enabled: bool = True
    is_default: bool = False


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = Field(default=None, max_length=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=100_000)
    disable_thinking: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None


@router.get("")
async def list_providers(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await LlmProviderRepository(db).list()
    data = [x for x in (_public(r) for r in rows) if x]
    return {"code": "OK", "message": "ok", "data": data}


@router.post("")
async def create_provider(
    req: ProviderReq = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if req.provider not in PROVIDERS:
        raise BadRequest(f"不支持的 provider：{req.provider}，可选 {sorted(PROVIDERS)}")
    repo = LlmProviderRepository(db)
    row = await repo.create(
        name=req.name.strip(), provider=req.provider, base_url=req.base_url,
        api_key=req.api_key or None, model=req.model.strip(),
        temperature=req.temperature, max_tokens=req.max_tokens,
        disable_thinking=req.disable_thinking,
        enabled=req.enabled, is_default=req.is_default,
    )
    return {"code": "OK", "message": "ok", "data": _public(row)}


@router.put("/{provider_id}")
async def update_provider(
    provider_id: UUID,
    req: ProviderUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    fields = req.model_dump(exclude_unset=True)
    if "provider" in fields and fields["provider"] and fields["provider"] not in PROVIDERS:
        raise BadRequest("不支持的 provider")
    row = await LlmProviderRepository(db).update(provider_id, fields)
    if row is None:
        raise NotFound("provider_not_found")
    return {"code": "OK", "message": "ok", "data": _public(row)}


@router.delete("/{provider_id}")
async def delete_provider(provider_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await LlmProviderRepository(db).delete(provider_id)
    return {"code": "OK", "message": "ok", "data": {"deleted": True}}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """测试连接：给配置的模型发一条极简消息并返回响应/用量/延迟。"""
    row = await LlmProviderRepository(db).get_raw(provider_id)
    if row is None:
        raise NotFound("provider_not_found")
    try:
        result = await llm_chat(
            row,
            [{"role": "user", "content": "请只回复：ok"}],
            temperature=0,
            max_tokens=16,
            timeout_s=90,
        )
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(f"模型调用失败：{exc}") from exc
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=row.get("provider") or "openai",
        model=result["model"],
        caller="settings.test",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    return {
        "code": "OK",
        "message": "ok",
        "data": {
            "ok": True,
            "name": row["name"],
            "model": result["model"],
            "reply": (result["content"] or "").strip()[:120],
            "usage": result["usage"],
            "latency_ms": result["latency_ms"],
        },
    }
