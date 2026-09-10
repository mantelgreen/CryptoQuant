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


_PICK_SYSTEM = """你是量化多因子研究员，负责从一批「已经实测过 IC」的候选因子里，挑出最适合进入多因子模型的特征池。
输入是一个 JSON 数组，每项含 code / name / tags / description / ic / rank_ic / decay(h1 h3 h5) / n_total。
挑选原则（严格按数据，不编造）：
1. 优先 |RankIC| 或 |IC| 更强、且方向稳定（IC 与 decay 的 h1/h3 符号一致）的因子；
2. 控制冗余：参考 tags/description 判断思路是否同质，尽量挑风格互补、信息来源不重叠的；
3. n_total 过小（<30）或 IC/RankIC 缺失的因子不要选；
4. 数量不超过用户要求的 max_pick 个，按质量降序排列。
只输出一个 JSON 对象（不要 markdown、不要代码块、不要任何解释文字）：
{"picks":[{"code":"因子 code，必须原样来自输入列表","reason":"≤25 字中文理由"}]}
绝不允许输出输入列表中不存在的 code，也不要重复。"""


def _fmt_metric(v: Any) -> str:
    return "—" if v is None else f"{float(v):+.4f}"


def _pick_lines(factors: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, f in enumerate(factors, 1):
        decay = f.get("decay") or {}
        tags = "、".join(f.get("tags") or []) or "—"
        desc = (f.get("description") or "").strip() or (f.get("name") or "")
        lines.append(
            f"{i}. code={f.get('code')} | 名称={f.get('name')} | 思路={desc[:40]} | 标签={tags[:20]} | "
            f"IC={_fmt_metric(f.get('ic'))} | RankIC={_fmt_metric(f.get('rank_ic'))} | "
            f"衰减 h1={_fmt_metric(decay.get('h1', decay.get('h_1')))} h3={_fmt_metric(decay.get('h3', decay.get('h_3')))} "
            f"h5={_fmt_metric(decay.get('h5', decay.get('h_5')))} | 样本 n={f.get('n_total') or f.get('n')}"
        )
    return lines


def _heuristic_pick(factors: list[dict[str, Any]], max_pick: int) -> list[dict[str, Any]]:
    """无 LLM / 解析失败时的规则兜底：按 |RankIC/IC| 排序 + 方向稳定加分。"""

    def score(f: dict[str, Any]) -> float:
        ic = f.get("ic")
        rank = f.get("rank_ic")
        base = abs(float(rank if rank is not None else ic if ic is not None else 0.0))
        decay = f.get("decay") or {}
        h1 = decay.get("h1", decay.get("h_1"))
        same = 0.0
        if ic is not None:
            if h1 is not None and float(h1) * float(ic) > 0:
                same += 0.01
            if decay.get("h3", decay.get("h_3")) is not None and float(decay.get("h3", decay.get("h_3"))) * float(ic) > 0:
                same += 0.005
        n = f.get("n_total") or f.get("n") or 0
        if n and n < 30:
            same -= 0.05
        return base + same

    ranked = sorted(factors, key=lambda f: (f.get("ic") is not None, score(f)), reverse=True)
    return [
        {"code": f["code"], "reason": "|RankIC/IC| 靠前且方向稳定（规则兜底）"}
        for f in ranked[:max_pick]
    ]


def _extract_json_object(raw: str) -> dict[str, Any]:
    """从模型输出（可能夹带推理/代码块/前后文）里提取第一个完整、平衡的 JSON 对象。

    内部扫描引号与转义，避免因 JSON 里的嵌套花括号（如 picks 数组）而误切。
    """
    text = raw.strip()
    start = text.find("{")
    while start != -1 and start < len(text):
        depth = 0
        in_str = False
        esc = False
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    frag = text[start : j + 1]
                    try:
                        obj = json.loads(frag)
                    except Exception:  # noqa: BLE001
                        break  # 该起点不可用，换下一个 '{'
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    raise ValueError("模型未返回 JSON 对象")


def _parse_picks(raw: str, allowed: set[str], max_pick: int) -> list[dict[str, Any]]:
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    obj = _extract_json_object(text)
    if not isinstance(obj.get("picks"), list):
        raise ValueError("JSON 缺少 picks")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in obj["picks"]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if code not in allowed or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "reason": str(item.get("reason", ""))[:80]})
        if len(out) >= max_pick:
            break
    if not out:
        raise ValueError("模型返回的 code 均不在输入列表")
    return out


async def pick_factors(
    db: AsyncSession,
    factors: list[dict[str, Any]],
    model_id: str | None = None,
    max_pick: int = 8,
) -> dict[str, Any]:
    """LLM 依据已实测的 IC/RankIC/衰减 从候选因子中挑出最优组合（进多因子训练池）。

    factors 每项需含 code/name，可带 tags/description/ic/rank_ic/decay/n_total。
    返回 {model, provider, picks:[{code,reason}], fallback}；模型输出解析失败时用规则兜底。
    """
    cleaned = []
    for f in factors:
        code = str(f.get("code", "")).strip()
        if code:
            cleaned.append({**f, "code": code})
    if len(cleaned) < 2:
        raise ValueError("至少需要 2 个已实测的因子才能做 AI 挑选")
    cleaned = cleaned[:40]
    allowed = {f["code"] for f in cleaned}
    max_pick = max(1, min(int(max_pick or 8), len(allowed)))

    provider = await resolve_provider(db, model_id)
    payload = "候选因子实测数据（请从中挑选）：\n" + "\n".join(_pick_lines(cleaned))
    user = f"请从以下候选里挑选最多 {max_pick} 个最适合进多因子模型的特征：\n{payload}"
    messages = [
        {"role": "system", "content": _PICK_SYSTEM},
        {"role": "user", "content": user[:6000]},
    ]
    result = await llm_chat(provider, messages, temperature=0.2, max_tokens=1200, timeout_s=240)
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=provider.get("provider") or "openai",
        model=result["model"],
        caller="agent.factor_pick",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    content = result["content"] or ""
    if not content:
        detail = "模型只输出了推理、未给出 JSON" if result.get("reasoning_content") else "输出为空"
        raise ValueError(
            f"模型「{result.get('model') or provider.get('model')}」未返回内容（{detail}）。"
            "若是思考型模型，请在「模型设置」对该供应商勾选“关闭思维模式”，或改用非思考模型后重试"
        )
    try:
        picks = _parse_picks(content, allowed, max_pick)
        fallback = False
    except ValueError:
        picks = _heuristic_pick(cleaned, max_pick)
        fallback = True
    return {
        "model": result["model"],
        "provider": provider.get("name"),
        "picks": picks,
        "fallback": fallback,
    }


_TRAIN_REVIEW_SYSTEM = """你是量化多因子模型诊断与调参专家。用户给你一次多因子模型训练的结果，你要：
1) 基于样本外指标判断这轮训练效果是否理想；
2) 效果不理想时，从“进入模型的特征”里做进一步精筛（建议下一轮保留/剔除哪些因子）；
3) 结合“当前超参数配置”给出下一轮建议的超参数（需要时才改，尽量小步）。

判断基准（严格按数据）：
- 理想：样本外 IC/RankIC 与 train 同号或方向一致、|RankIC|≥0.02、Top−Bottom 价差>0，且过拟合差距 |train_IC−OOS_IC|<0.06；
- 一般：方向对但强度不足（0.01≤|RankIC|<0.02），或过拟合差距偏大；
- 不理想：价差≤0、方向反转、|RankIC|<0.01，或明显在记忆噪声（train_IC 远高 OOS_IC）。

诊断时的调参思路（据此给建议，不要乱给）：
- 过拟合差距大 / OOS loss 拐点早：降低 learning_rate、提高 alpha/L2、减 n_estimators、加深正则（num_leaves/max_depth 更小、提高 min_child_samples）、加大 subsample/colsample 采样收紧等；
- 信号弱但稳健：适度加大容量（gbdt 增加 num_leaves/n_estimators，ridge 调低 alpha）；
- 样本少/噪声大：优先 Ridge 并提高 alpha；数据量大且非线性明显才用 gbdt；
- 收益/特征离群明显（极值价差、厚尾）：适度加大 winsor（0.01→0.02→0.05）；
- 只在确有理由时切换 model（ridge/gbdt），并把相关超参一起给出；
- 不要把容量压到无法拟合：gbdt 的 learning_rate 不要低于 0.01、n_estimators 不要低于 100、max_depth 不要设成 1（树桩），否则输出会退化成近似常值、回测无交易。

精筛原则：剔除样本外单因子 IC 方向与组合相反、|IC| 极弱、明显冗余或仅靠个别时点贡献的特征；不要为了剔除而剔除，特征数≤3 时除非某因子明显有害否则保留。

只输出一个 JSON 对象（不要 markdown、不要代码块、不要解释文字）：
{"grade":"ok|warn|bad","review":"≤220字，换行分段或用 1. 2. 3. 列要点","advice":"≤60字下一步建议","keep":["保留的因子 code，全部来自输入特征列表"],"drop":[{"code":"剔除的因子 code","reason":"≤25字理由"}],"agg_keep":["下一轮要保留的聚合特征类型"],"params":{...建议的超参，只包含需要改的键...}}
超参 params 可用的键与范围（键名与数值要落在范围内）：
- model: "ridge" | "gbdt"（仅在确实要换模型时给出）
- horizon: 1~10（int）；test_ratio: 0.15~0.5
- alpha: 0~50；max_corr: 0~0.99
- learning_rate: 0.01~1；n_estimators: 100~5000；max_depth: -1~32；num_leaves: 2~256；min_child_samples: 5~200；subsample: 0.1~1；colsample_bytree: 0.1~1
- winsor: 0 | 0.01 | 0.02 | 0.05（仅从这 4 个值里选，0 表示关闭稳健化；离群明显时建议调大）
规则：
- grade 只能是 ok/warn/bad；keep 至少 2 个、code 必须原样来自输入的特征列表；keep 与 drop 不要重叠；
- agg_keep 的值只能来自输入里给出的“聚合特征类型”（rv/vol_imb/taker_ratio），表示下一轮保留哪些聚合特征——想剔除某个聚合特征就把它排除在 agg_keep 之外；全保留就给全量；本轮没用聚合特征时给 []；
- 效果理想且无必要改动时 drop 给空数组、keep 保留全部特征、agg_keep 保留全部聚合特征、params 给空对象 {}；
- params 只放“这轮值得调”的键，数值必须落在上面范围，且不能调当前模型不用的键（如 ridge 不调 learning_rate/n_estimators/…）。"""


def _fmt_metric(v: Any, digits: int = 4) -> str:
    return "—" if v is None else f"{float(v):.{digits}f}"


_HYPER_SPEC: dict[str, dict[str, Any]] = {
    "model": {"type": "enum", "values": ["ridge", "gbdt"]},
    "horizon": {"type": "int", "min": 1, "max": 10},
    "test_ratio": {"type": "float", "min": 0.15, "max": 0.5},
    "alpha": {"type": "float", "min": 0.0, "max": 50.0},
    "max_corr": {"type": "float", "min": 0.0, "max": 0.99},
    "learning_rate": {"type": "float", "min": 0.01, "max": 1.0},
    "n_estimators": {"type": "int", "min": 100, "max": 5000},
    "max_depth": {"type": "int", "min": -1, "max": 32},
    "num_leaves": {"type": "int", "min": 2, "max": 256},
    "min_child_samples": {"type": "int", "min": 5, "max": 200},
    "subsample": {"type": "float", "min": 0.1, "max": 1.0},
    "colsample_bytree": {"type": "float", "min": 0.1, "max": 1.0},
    "winsor": {"type": "float", "min": 0.0, "max": 0.2},
}

_GBDT_KEYS = {
    "learning_rate",
    "n_estimators",
    "max_depth",
    "num_leaves",
    "min_child_samples",
    "subsample",
    "colsample_bytree",
}

_WINSOR_CHOICES = (0.0, 0.01, 0.02, 0.05)

_AGG_LABELS = {"rv": "已实现波动率", "vol_imb": "量能买卖不平衡", "taker_ratio": "主动买占比"}


def _sanitize_hypers(raw: Any, current: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 给的超参建议清洗到合法范围；剔掉当前（或拟切换后）模型用不上的键。"""
    if not isinstance(raw, dict):
        return {}
    cur_model = str(current.get("model") or "ridge")
    target_model = cur_model
    raw_model = str(raw.get("model") or "").strip().lower()
    if raw_model in _HYPER_SPEC["model"]["values"]:
        target_model = raw_model

    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "model":
            continue
        spec = _HYPER_SPEC.get(k)
        if spec is None:
            continue
        if target_model != "gbdt" and k in _GBDT_KEYS:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        lo = float(spec["min"])
        hi = float(spec["max"])
        if fv < lo or fv > hi:
            continue
        if spec["type"] == "int":
            fv = int(round(fv))
            fv = max(int(lo), min(int(hi), fv))
        else:
            fv = max(lo, min(hi, round(fv, 4)))
        if k == "winsor":
            fv = min(_WINSOR_CHOICES, key=lambda c: abs(c - fv))
        out[k] = fv
    if target_model != cur_model:
        out["model"] = target_model
    return out


def _rule_grade(metrics: dict[str, Any]) -> str:
    """无 LLM 可用时的规则判断。"""
    ic = metrics.get("oos_ic")
    rank = metrics.get("oos_rank_ic")
    spread = metrics.get("spread")
    gap = metrics.get("overfit_gap")
    if ic is None and rank is None:
        return "warn"
    a = float(ic if ic is not None else 0.0)
    b = float(rank if rank is not None else 0.0)
    sign_ok = (ic is None or rank is None) or a * b >= 0
    mag = max(abs(a), abs(b))
    if (spread or 0) > 0 and sign_ok and mag >= 0.02 and (gap is None or abs(gap) < 0.06):
        return "ok"
    if (spread or 0) > 0 and sign_ok and mag >= 0.01:
        return "warn"
    return "bad"


def _rule_refine(features: list[dict[str, Any]], metrics: dict[str, Any], grade: str):
    """无 LLM 时的精筛兜底：不理想时保留方向一致且单因子 IC 较强的因子。"""
    cur = [f["code"] for f in features]
    if grade in ("ok", "warn") or len(cur) < 3:
        return list(cur), []
    ref = metrics.get("oos_rank_ic")
    if ref is None:
        ref = metrics.get("oos_ic")
    sign = 1.0
    try:
        if ref is not None and float(ref) < 0:
            sign = -1.0
    except (TypeError, ValueError):
        pass

    def score(f: dict[str, Any]) -> float:
        v = f.get("ic_oos")
        return abs(float(v)) if v is not None else 0.0

    def dir_ok(f: dict[str, Any]) -> bool:
        v = f.get("ic_oos")
        if v is None:
            return True
        try:
            return float(v) * sign >= -1e-12
        except (TypeError, ValueError):
            return True

    bad = [f for f in features if not dir_ok(f)]
    good = [f for f in features if dir_ok(f)]
    if len(good) < 2:
        rest = sorted(bad, key=score, reverse=True)
        good = good + rest[: 2 - len(good)]
        bad = [f for f in bad if f not in good]
    drops = [
        {"code": f["code"], "reason": "样本外单因子 IC 与组合方向相反/偏弱（规则兜底）"}
        for f in bad
    ]
    return [f["code"] for f in good], drops


def _parse_training_review(raw: str, allowed: set[str], current_cfg: dict[str, Any] | None = None, allowed_agg: set[str] | None = None):
    obj = _extract_json_object(raw)
    grade = obj.get("grade")
    if grade not in {"ok", "warn", "bad"}:
        raise ValueError("模型返回了未知的 grade")
    review = str(obj.get("review") or "")[:2500].strip()
    advice = str(obj.get("advice") or "")[:200].strip()
    keep_raw = obj.get("keep")
    if not isinstance(keep_raw, list):
        raise ValueError("JSON 缺少 keep 数组")
    keep: list[str] = []
    for c in keep_raw:
        c = str(c).strip()
        if c in allowed and c not in keep:
            keep.append(c)
    if len(keep) < 2:
        raise ValueError("keep 不足 2 个")
    drop: list[dict[str, Any]] = []
    if isinstance(obj.get("drop"), list):
        for it in obj["drop"]:
            if not isinstance(it, dict):
                continue
            c = str(it.get("code", "")).strip()
            if c in allowed and c not in keep and all(d["code"] != c for d in drop):
                drop.append({"code": c, "reason": str(it.get("reason", ""))[:100]})
    params = _sanitize_hypers(obj.get("params"), current_cfg or {})
    agg_keep: list[str] = []
    agg_allowed = allowed_agg or set()
    if isinstance(obj.get("agg_keep"), list):
        for a in obj["agg_keep"]:
            a = str(a).strip()
            if a in agg_allowed and a not in agg_keep:
                agg_keep.append(a)
    return grade, review, advice, keep, drop, params, agg_keep


async def review_training(
    db: AsyncSession,
    payload: dict[str, Any],
    model_id: str | None = None,
) -> dict[str, Any]:
    """让 LLM 对一次多因子训练结果做诊断；效果不理想时给出特征精筛建议。

    payload 需含 metrics / features 等；返回
    {model, provider, grade, review, advice, keep, drop, fallback}。
    """
    features = payload.get("features") or []
    features = [f for f in features if str(f.get("code", "")).strip()]
    if not features:
        raise ValueError("训练结果里没有可用特征，无法做 AI 解析")
    features = features[:30]
    allowed = {f["code"] for f in features}
    metrics = payload.get("metrics") or {}
    cfg = payload.get("config") or {}
    cfg_txt = "；".join(f"{k}={v}" for k, v in cfg.items()) or "—"
    grade_rule = _rule_grade(metrics)

    current_agg: list[str] = []
    for a in cfg.get("agg_features") or []:
        a = str(a).strip()
        if a in _AGG_LABELS and a not in current_agg:
            current_agg.append(a)
    allowed_agg = set(current_agg)

    def feature_line(f: dict[str, Any]) -> str:
        return (
            f"- {f.get('code')}（{f.get('name') or '?'}）coef={_fmt_metric(f.get('coef'))} "
            f"OOS IC={_fmt_metric(f.get('ic_oos'))} rankIC={_fmt_metric(f.get('rank_ic_oos'))}"
        )

    checks = payload.get("checks") or []
    checks_txt = "；".join(
        f"「{c.get('label')}」:{('通过' if c.get('ok') is True else '未达' if c.get('ok') is False else '待跑')}"
        for c in checks[:12]
    )
    removed = payload.get("removed") or []
    removed_txt = "；".join(
        f"{r.get('code')}({r.get('name') or '?'})"
        for r in removed[:8]
    ) or "无"
    m = metrics
    diag = payload.get("oos_charts") or {}
    learning = payload.get("learning")
    learn_txt = "无"
    if learning:
        learn_txt = (
            f"{learning.get('rounds')} 轮，末轮 OOS loss={_fmt_metric(learning.get('oos_final'), 6)}，"
            f"谷底 {_fmt_metric(learning.get('oos_min'), 6)} @第 {learning.get('oos_min_round')} 轮"
        )

    content = (
        f"训练对象 {payload.get('symbol') or '?'} / {payload.get('interval') or '?'}，"
        f"预测窗口 h={payload.get('horizon')}，模型 {payload.get('model') or '?'}（{payload.get('engine') or '?'}），"
        f"样本 train={payload.get('n_train')} / OOS test={payload.get('n_test')}，保留特征 {len(features)} 个。\n"
        f"当前超参配置：{cfg_txt}。\n"
        f"指标：train_IC={_fmt_metric(m.get('train_ic'))}，OOS_IC={_fmt_metric(m.get('oos_ic'))}，"
        f"OOS_RankIC={_fmt_metric(m.get('oos_rank_ic'))}，过拟合差距={_fmt_metric(m.get('overfit_gap'))}，"
        f"命中率={_fmt_metric(m.get('hit_rate'))}%，Top−Bottom 价差={_fmt_metric(m.get('spread'))}，"
        f"净价差（扣成本）={_fmt_metric(m.get('net_spread'))}。\n"
        f"体检勾选：{checks_txt or '—'}。\n"
        f"已因相关性剔除：{removed_txt}。\n"
        f"特征明细（共 {len(features)} 个）：\n" + "\n".join(feature_line(f) for f in features) + "\n"
        f"本轮细粒度聚合特征（fine={cfg.get('fine_interval') or '—'}）："
        f"{'、'.join(f'{a}({_AGG_LABELS.get(a, a)})' for a in current_agg) or '无'}。\n"
        f"分层诊断：十分位单调相关 r={_fmt_metric(diag.get('monotonic_r'))}，"
        f"高分组净值终点={_fmt_metric(diag.get('q5_end'))}，低分组={_fmt_metric(diag.get('q1_end'))}。\n"
        f"LightGBM 训练曲线：{learn_txt}。\n"
        f"请给出诊断、特征精筛、聚合特征取舍与下一轮超参建议（grade、keep/drop、agg_keep、params）。"
    )

    provider = await resolve_provider(db, model_id)
    messages = [
        {"role": "system", "content": _TRAIN_REVIEW_SYSTEM},
        {"role": "user", "content": content[:9000]},
    ]
    result = await llm_chat(provider, messages, temperature=0.2, max_tokens=2000, timeout_s=300)
    usage = result.get("usage") or {}
    await record_usage(
        db,
        provider=provider.get("provider") or "openai",
        model=result["model"],
        caller="agent.train_review",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        latency_ms=result.get("latency_ms", 0),
    )
    content_raw = result["content"] or ""
    if not content_raw:
        detail = "模型只输出了推理、未给出 JSON" if result.get("reasoning_content") else "输出为空"
        raise ValueError(
            f"模型「{result.get('model') or provider.get('model')}」未返回内容（{detail}）。"
            "若是思考型模型，请在「模型设置」对该供应商勾选“关闭思维模式”，或改用非思考模型后重试"
        )
    try:
        grade, review, advice, keep, drop, params, agg_keep = _parse_training_review(
            content_raw, allowed, cfg, allowed_agg
        )
        fallback = False
    except ValueError:
        fallback = True
        params = {}
        grade = grade_rule
        keep, drop = _rule_refine(features, metrics, grade)
        agg_keep = list(current_agg)
        review = {
            "ok": "样本外方向一致、价差为正且过拟合差距可控，这轮信号初步可用。建议先去回测与滚动验证，而非继续加因子。",
            "warn": "方向对但强度或过拟合差距一般，可小步调整（更多样本、更严相关性过滤、降低模型容量）后再看。",
            "bad": "这轮样本外信号偏弱/方向不稳，已给出规则兜底的特征精筛；建议按精筛子集重训，或换窗口与更稳健因子。",
        }[grade]
        advice = {
            "ok": "进入回测 / 模拟盘验证，跟踪 RankIC 是否随样本外推移衰减。",
            "warn": "调参或扩样本后再训，用精筛结果对比。",
            "bad": "按精筛后的特征子集重新训练并对比 OOS。",
        }[grade]
    return {
        "model": result["model"],
        "provider": provider.get("name"),
        "grade": grade,
        "review": review,
        "advice": advice,
        "keep": keep,
        "drop": drop,
        "agg_keep": agg_keep,
        "params": params,
        "fallback": fallback,
    }


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

async def review_backtest_task(db, backtest_id: str) -> dict[str, Any]:
    """加载回测并调用 LLM 复盘（服务层编排；找不到时抛 ValueError('backtest_not_found')）。"""
    from uuid import UUID

    from ..strategy.engine.repo import BacktestRepository

    try:
        uid = UUID(str(backtest_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("backtest_not_found") from exc
    row = await BacktestRepository(db).get(uid)
    if row is None:
        raise ValueError("backtest_not_found")
    return await review_backtest(db, row)
