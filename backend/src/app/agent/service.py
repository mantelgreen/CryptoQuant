"""AI 因子草稿服务：调用已配置 LLM（默认供应商）把自然语言目标转成可校验因子。"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..factor.sandbox import validate_expression
from ..llm.client import llm_chat
from ..llm.repo import LlmProviderRepository
from ..llm.usage import record_usage

_SYSTEM = """你是量化因子研究员。用户会用自然语言描述想捕捉的行情规律或研究目标。
只输出一个 JSON 对象（不要 markdown、不要代码块、不要任何解释文字）：
{"name": "≤10字中文名", "description": "一句话说明因子含义", "expression": "因子表达式"}

expression 是受限 DSL，必须严格遵守：
- 数据列（K 线）：open / high / low / close / volume / quote_volume
- 内置技术指标序列（可直接当列使用，勿用 ewm/rolling 重算）：
  macd / macd_signal / macd_hist（MACD 12,26,9）
  rsi（RSI 14）、atr（ATR 14）、obv（OBV）、vwap（累计 VWAP）
  boll_mid / boll_upper / boll_lower（布林带 20,2）
  stoch_k / stoch_d / stoch_j（KDJ 14,3,3）、wr（威廉 %R 14）、roc（变动率 12）、cci（CCI 20）
  sma_5 / sma_10 / sma_20 / sma_60、ema_5 / ema_10 / ema_20 / ema_60（常用均线）
  例：macd > 0、rsi < 30、close > boll_upper、macd_hist > macd_hist.shift(1)
- 链式方法（接在列或已赋值变量后）：
  shift(n) diff(n) pct_change(n)
  rolling(n).mean() rolling(n).std() rolling(n).max() rolling(n).min() rolling(n).sum()
  rolling(n).median() rolling(n).quantile(q) rolling(n).skew() rolling(n).var()
  ewm(span=s).mean() ewm(alpha=a).mean()
  abs() clip(lower=x, upper=y) rank() fillna(x) ffill() bfill()
  cumsum() cummax() cummin() cumprod() round(n) pow(n)
- 元素级函数：abs(x) log(x) sign(x) where(cond, a, b)
- 运算符：+ - * / ** ；比较 < > <= >= == != ；逻辑 and / or ；括号
- 布尔/条件组合因子：每个条件单独加括号后用 and / or / & / | 连接；结尾不要写 `& 1` / `* 1`，布尔结果会自动转 0/1 数值
- 数值常量用小数（写 0.05，不要写 5%）
- 内置指标是“序列变量”不是函数：直接用 `ema_20` / `boll_upper` / `macd`，不要写 `ema_20(close)` 或 `boll_upper(20)`、`macd(12,26,9)`（参数已固定）；不要用 `and(a, b)`/`or(a, b)` 函数式写法（and/or 是关键字），组合条件写成 `(a) and (b)` 或 `(a) & (b)`
- 多行写法：每行 `变量名 = 表达式`，最后一行必须是“裸表达式”（直接写最终式子，不要 return）

绝对禁止（会直接校验失败）：
- return / def / import / lambda / 注释(#) / 字符串
- np. 或 pd. 前缀；max(序列)/min(序列)/sum(序列) 这类函数调用
- .iloc .values .apply .resample .expanding .corr .cov 等未列出的方法

正确示例：
- 单行：{"expression": "(close - close.shift(12)) / close.shift(12)"}
- 多行：
  ma = close.rolling(20).mean()
  vol = close.pct_change().rolling(20).std()
  (close - ma) / (vol + 1e-8)
- 条件：where(close > close.rolling(20).max(), 1, 0)
- 量能均线需自己写：volume / volume.rolling(20).mean()（没有 volume_20 这类内置变量，只有 close 有 sma_*/ema_*）
- 布尔组合：
  (close > sma_20) & (close > close.shift(12)) & (volume > volume.shift(1))

错误示例（不要这样写）：
- "return close.pct_change(20)"   → 去掉 return
- "def f(): ..." / "import pandas" → 禁止
- "max(close.rolling(10))"        → 用 close.rolling(10).max()
- "close > 5%"                     → 用 close > 0.05

现在根据用户目标生成因子。"""


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
        max_tokens=1500,
        timeout_s=300,
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
    max_tokens: int = 1500,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """自然语言 → 因子草稿（带沙箱预校验 + 自动重试）。

    返回 {model, provider, name, description, code, raw, verified, verify_errors, verify_warnings}。
    每次 LLM 输出都会先过沙箱干跑；不通过则把具体错误回喂模型重写，最多 max_attempts 次。
    """
    provider = await resolve_provider(db, model_id)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt[:1200]},
    ]
    last_candidate: dict[str, Any] | None = None
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] | None = None
    for _attempt in range(max(1, max_attempts)):
        result = await llm_chat(
            provider,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=300,
        )
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
        content = result["content"] or ""
        if not content:
            detail = "模型只输出了推理、未给出最终 JSON" if result.get("reasoning_content") else "输出为空"
            raise ValueError(
                f"模型「{result.get('model') or provider.get('model')}」未返回内容（{detail}）。"
                "若是思考型模型，请在「模型设置」对该供应商勾选“关闭思维模式”；"
                "若该模型不支持关闭，请改用非思考模型，或调大 max_tokens 后重试"
            )
        try:
            candidate = _parse_draft(content)
        except ValueError as exc:
            candidate = None
            errors = [f"JSON 解析失败：{exc}"]
        if candidate is not None:
            last_candidate = candidate
            ok, errors, warnings = validate_expression(candidate["code"])
            if ok:
                break
        # 回喂具体错误，要求只修正 expression 后重出完整 JSON
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": (
                    "上一条 expression 未通过沙箱校验：" + "；".join(errors)
                    + "。请只修正 expression（末行必须是裸表达式，禁止 return/def/import/注释/np./pd.），"
                    "严格遵守 DSL，重新输出完整 JSON。"
                ),
            }
        )
    if last_candidate is None or result is None:
        raise ValueError("；".join(errors) or "模型未返回有效 JSON")
    last_candidate["model"] = result["model"]
    last_candidate["provider"] = provider.get("name")
    last_candidate["verified"] = not errors
    last_candidate["verify_errors"] = errors
    last_candidate["verify_warnings"] = warnings
    return last_candidate


async def generate_factor_idea(
    db: AsyncSession,
    model_id: str | None = None,
) -> dict[str, Any]:
    """随机生成一个可量化的因子研究需求（自然语言，非表达式）。

    用于「AI 因子工坊」用户一时没有思路时，由模型给一个灵感。
    返回 {idea, model, provider}；模型输出为空时抛出带原因的 ValueError。
    """
    provider = await resolve_provider(db, model_id)
    system = (
        "你是量化因子研究员。请随机给出一条有创意、可量化的数字币行情因子研究需求，"
        "用一句中文自然语言描述（例如「捕捉放量突破后回踩不破的延续」「低波动横盘后均线粘合的突破」）。"
        "只输出这一句话本身，不要编号、不要解释、不要 markdown、不要引号，20 字以内。"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "给我一个随机因子研究需求。"},
    ]
    result = await llm_chat(
        provider,
        messages,
        temperature=1.1,
        max_tokens=1500,
        timeout_s=120,
    )
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=provider.get("provider") or "openai",
        model=result["model"],
        caller="agent.factor_idea",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    idea = _clean_idea(result.get("content"))
    if not idea:
        detail = "模型只输出了推理过程、未给出结论" if result.get("reasoning_content") else "输出为空"
        raise ValueError(
            f"模型「{result.get('model') or provider.get('model')}」未返回有效内容（{detail}）。"
            "通常是思考型模型的推理占满了输出上限，可在「模型设置」关闭思考模式、增大 max_tokens，"
            "或改用非思考模型后重试"
        )
    return {"idea": idea, "model": result["model"], "provider": provider.get("name")}


def _clean_idea(raw: str | None) -> str:
    """清洗模型输出：去 markdown 代码块、引号、编号前缀，取一句简短需求。"""
    if not raw:
        return ""
    text = raw.strip()
    # 去 markdown 代码块
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    # 若模型把需求写在引号/书名号里，优先取该片段
    m = re.search(r"[「『“\"]([^」』”\"]{2,})[」』”\"]", text)
    if m:
        text = m.group(1).strip()
    # 去各类引号（含全角）
    text = text.strip("\"'「」『』“”‘’`")
    # 去形如 "1." "1、" "1)" 的编号前缀
    text = re.sub(r"^\s*\d+[\.、)）]\s*", "", text).strip()
    return text.strip()
