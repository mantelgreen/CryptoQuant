"""策略模板库（内置 v1）。

模板签名：position_signal(df, params) -> pd.Series
返回目标仓位 0..1（多头仓位比例），回测引擎据此撮合。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...factor.registry import compute_factor as _compute_factor, get_factors as _get_factors

RegistryType = dict[str, dict]


def _sma(df: pd.DataFrame, n: int) -> pd.Series:
    return df["close"].rolling(window=n, min_periods=n).mean()


def register_templates() -> RegistryType:
    templates: RegistryType = {}

    def sma_cross(df: pd.DataFrame, params: dict) -> pd.Series:
        fast = int(params.get("fast", 10))
        slow = int(params.get("slow", 30))
        fast_s = _sma(df, fast)
        slow_s = _sma(df, slow)
        # fast 上穿 slow → 满仓；下穿 → 空仓
        pos = (fast_s > slow_s).astype(float)
        # 平滑：仓位变化只发生在快线穿越慢线那一根之后，默认 target next bar
        return pos.fillna(0.0)

    def sma_cross_longshort(df: pd.DataFrame, params: dict) -> pd.Series:
        fast = int(params.get("fast", 10))
        slow = int(params.get("slow", 30))
        fast_s = _sma(df, fast)
        slow_s = _sma(df, slow)
        pos = (fast_s > slow_s).astype(float) * 1.0 - (fast_s < slow_s).astype(float) * 1.0
        return pos.fillna(0.0)

    def rsi_mean_revert(df: pd.DataFrame, params: dict) -> pd.Series:
        """RSI 超卖回补 / 超买卖出（逆势，多头）。"""
        n = int(params.get("rsi_period", 14))
        buy = float(params.get("buy", 30))
        sell = float(params.get("sell", 70))
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = (-delta.clip(upper=0)).rolling(n).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = 100 - 100 / (1 + rs)
        pos = pd.Series(0.0, index=df.index)
        # 从超卖区上穿买线 → 持仓；上穿卖线 → 平仓
        below = rsi < buy
        above_sell = rsi > sell
        signal = below.astype(int) - above_sell.astype(int)
        pos = signal.cumsum().clip(0, 1)
        return pos.fillna(0.0).astype(float)

    def buy_and_hold(df: pd.DataFrame, params: dict) -> pd.Series:
        return pd.Series(1.0, index=df.index)

    def ml_score(df: pd.DataFrame, params: dict) -> pd.Series:
        """隐藏模板：用已保存 ML 模型（factor + coef/mean/std）逐 bar 打分 → 滚动分位择时。

        参数：features=[{code,coef,mean,std}...]，window/enter_q/exit_q（因果滚动分位数）。
        打分用 ≤t 的信息，仓位自 t+1 起生效，无未来函数。
        """
        feats = params.get("features") or []
        if not feats:
            return pd.Series(0.0, index=df.index)
        score = pd.Series(0.0, index=df.index)
        available = set(_get_factors().keys())
        for f in feats:
            code = f.get("code")
            if code not in available:
                continue
            try:
                val = _compute_factor(code, df)
            except KeyError:
                continue
            mean = float(f.get("mean", 0.0) or 0.0)
            std = float(f.get("std", 1.0) or 1.0)
            if std == 0:
                std = 1.0
            score += float(f.get("coef", 0.0) or 0.0) * (val - mean) / std

        window = int(params.get("window", 60) or 60)
        enter_q = float(params.get("enter_q", 0.8) or 0.8)
        exit_q = float(params.get("exit_q", 0.2) or 0.2)
        roll = score.rolling(window, min_periods=max(2, window // 2))
        q_hi = roll.quantile(enter_q)
        q_lo = roll.quantile(exit_q)
        state = pd.Series(np.nan, index=score.index)
        state[score > q_hi] = 1.0
        state[score < q_lo] = 0.0
        return state.ffill().fillna(0.0).astype(float)

    templates["sma_cross"] = {
        "title": "双均线交叉",
        "description": "快线上穿慢线做多，下穿空仓。经典趋势跟踪。",
        "logic": "当快线(参数 fast)上穿慢线(参数 slow)时满仓做多；快线下穿慢线时清仓空仓。",
        "long_short": False,
        "params_schema": {
            "fast": {"type": "int", "default": 10, "min": 2, "max": 200},
            "slow": {"type": "int", "default": 30, "min": 5, "max": 500},
        },
        "fn": sma_cross,
    }
    templates["sma_cross_longshort"] = {
        "title": "双均线交叉（多空）",
        "description": "上穿做多、下穿做空（含做空）。",
        "logic": "当快线(fast)在上时满仓做多；快线在下时满仓做空——两个方向都吃趋势。",
        "long_short": True,
        "params_schema": {
            "fast": {"type": "int", "default": 10, "min": 2, "max": 200},
            "slow": {"type": "int", "default": 30, "min": 5, "max": 500},
        },
        "fn": sma_cross_longshort,
    }
    templates["rsi_mean_revert"] = {
        "title": "RSI 均值回归",
        "description": "RSI 超卖后反弹做多，超买后平仓。",
        "logic": "RSI(rsi_period) 从低于 buy 的区域回升时入场做多，升到高于 sell 时平仓；最多持满 100% 仓位。",
        "long_short": False,
        "params_schema": {
            "rsi_period": {"type": "int", "default": 14, "min": 2, "max": 100},
            "buy": {"type": "float", "default": 30, "min": 5, "max": 50},
            "sell": {"type": "float", "default": 70, "min": 50, "max": 95},
        },
        "fn": rsi_mean_revert,
    }
    templates["buy_and_hold"] = {
        "title": "买入并持有（基准）",
        "description": "全程满仓，作为其它策略的对照基准。",
        "logic": "任何时刻都保持 100% 仓位，不择时——用来检验“躺着不动”的收益与回撤。",
        "long_short": False,
        "params_schema": {},
        "fn": buy_and_hold,
    }
    templates["ml_score"] = {
        "title": "ML 模型打分择时（内部）",
        "description": "用已保存的 ML 模型对行情逐 bar 打分，高于入场阈值做多、低于离场阈值清仓（状态化）。",
        "logic": "模型打分 score=Σ(coef·标准化因子)；score 高于滚动 q80(enter_q) → 满仓，低于滚动 q20(exit_q) → 清仓，否则保持（窗口参数可调）。",
        "long_short": False,
        "hidden": True,
        "params_schema": {},
        "fn": ml_score,
    }
    return templates


# 模块级缓存，避免每次 import 重建闭包
_TEMPLATES: RegistryType | None = None


def get_templates() -> RegistryType:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = register_templates()
    return _TEMPLATES


def run_template(template_id: str, df: pd.DataFrame, params: dict) -> pd.Series:
    tpl = get_templates().get(template_id)
    if tpl is None:
        raise KeyError(f"unknown_strategy_template:{template_id}")
    return tpl["fn"](df, params)

