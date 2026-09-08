"""AI 因子草稿服务：调用已配置 LLM（默认供应商）把自然语言目标转成可校验因子。"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..llm.client import llm_chat
from ..llm.repo import LlmProviderRepository
from ..llm.usage import record_usage

_SYSTEM = """你是量化因子研究员。用户会用自然语言描述想捕捉的市场规律或研究目标。
请给出一个可直接对 K 线列运算的因子表达式，并输出 JSON（不要输出其它文字）：
{"name": "≤10字中文名", "description": "一句话说明因子含义", "expression": "python表达式"}
表达式只能使用列 open/high/low/close/volume/quote_volume 与受限方法 shift/rolling/mean/std/diff/pct_change/abs/clip/rank/sum/cumsum/skew/fillna/sign，及 + - * / 括号、数值常量。
示例："动量偏离": {"expression": "(close - close.shift(12)) / close.shift(12) * 100"}。"""


def _parse_draft(raw: str) -> dict[str, Any]:
    # 兼容模型先输出推理/被代码块包裹：取最后一个平衡的 JSON 对象
    text = raw.strip()
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型未返回 JSON")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON 结构不正确")
    name = str(obj.get("name", "AI 生成因子")).strip()
    desc = str(obj.get("description", "")).strip()
    expr = str(obj.get("expression", obj.get("code", ""))).strip()
    if not expr:
        raise ValueError("缺少 expression")
    return {"name": name[:30], "description": desc[:300], "code": expr, "raw": raw[:2000]}


async def resolve_provider(db: AsyncSession, model_id: str | None = None) -> dict[str, Any]:
    repo = LlmProviderRepository(db)
    if model_id:
        row = await repo.get_raw(provider_id_uuid(model_id))  # type: ignore[attr-defined]
        if row:
            return row
    rows = await repo.list()
    for r in rows:
        if r.get("is_default") and r.get("enabled"):
            return r
    for r in rows:
        if r.get("enabled"):
            return r
    raise ValueError("未配置可用的 LLM 供应商，请先到「模型设置」添加并启用")


def provider_id_uuid(model_id: str):
    from uuid import UUID

    return UUID(model_id)


async def review_backtest(
    db: AsyncSession,
    backtest: dict[str, Any],
) -> dict[str, Any]:
    """对一次回测结果做 LLM 中文复盘。返回 {review, model}。"""
    provider = await resolve_provider(db, None)
    summary = backtest.get("summary") or {}
    metrics = summary.get("metrics") or {}
    symbol = (summary.get("symbol") or (backtest.get("symbols") or ["—"])[0])
    interval = summary.get("interval") or backtest.get("interval") or "?"
    def fmt(v, mul=100.0, suf="%"):
        if v is None:
            return "—"
        try:
            return f"{float(v) * mul:.2f}{suf}"
        except (TypeError, ValueError):
            return "—"

    eq = summary.get("final_equity")
    eq_txt = f"${float(eq):,.2f}" if eq is not None else "—"
    content = (
        f"回测对象 {symbol} / {interval}，区间 {backtest.get('start_time')} ~ {backtest.get('end_time')}，"
        f"状态 {backtest.get('status')}，初始资金 100000，终值 {eq_txt}。"
        f"指标：累计收益 {fmt(metrics.get('total_return'))}、年化 {fmt(metrics.get('annual_return'))}、"
        f"最大回撤 {fmt(metrics.get('max_drawdown'))}、夏普 {metrics.get('sharpe')}、索提诺 {metrics.get('sortino')}、"
        f"卡玛 {metrics.get('calmar')}、胜率 {fmt(metrics.get('win_rate'))}、盈亏比 {metrics.get('profit_factor')}、"
        f"交易 {metrics.get('num_trades')} 笔。回测含 0.1% 手续费与滑点。"
    )
    system = (
        "你是严谨的量化交易策略分析师。请用简体中文给出 ≤220 字的回测复盘，分三点："
        "① 这个结果说明什么（是否跑赢买入持有趋势/是否来自少数几笔）"
        "② 主要风险（回撤形态、样本量、参数敏感性、过拟合隐患）"
        "③ 改进建议（数据/切分/参数/再验证）。不要编造指标，语气客观。"
    )
    result = await llm_chat(
        provider,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        temperature=0.3,
        max_tokens=600,
        timeout_s=240,
    )
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=provider.get("provider") or "openai",
        model=result["model"],
        caller="agent.review_backtest",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    return {"model": result["model"], "review": (result["content"] or "").strip()}


async def generate_factor_draft(
    db: AsyncSession,
    prompt: str,
    model_id: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 700,
) -> dict[str, Any]:
    """自然语言 → 因子草稿。返回 {model, name, description, code, raw}。"""
    provider = await resolve_provider(db, model_id)
    result = await llm_chat(
        provider,
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt[:1200]},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=240,
    )
    parsed = _parse_draft(result["content"])
    parsed["model"] = result["model"]
    parsed["provider"] = provider.get("name")
    # 用量落库
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=provider.get("provider") or "openai",
        model=result["model"],
        caller="agent.factor_draft",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    return parsed
