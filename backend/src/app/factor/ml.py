"""ML 多因子训练 v0：特征=研究通过的因子 → 按时间序列切分 → L2 Ridge 回归。

- 纯 NumPy 实现（无额外依赖）；若环境装了 scikit-learn，会自动用其 Ridge 求更稳的解。
- 标签：未来 h 根收益 close.pct_change(h).shift(-h)。
- 切分：每个标的内部按时间先后切 train/test（保留未来无泄漏），可设 purge 间隔。
- 评价：样本外 IC / RankIC / 方向命中率 / 预测 top vs bottom 分组平均收益差（长空价差代理）。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..data.repository.kline_repo import KlineRepository
from .registry import compute_factor, get_factors
from .compute import compute_series, meta_or_extra
from .sandbox import safe_eval_expr


async def _load_frame(repo: KlineRepository, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = await repo.query_or_aggregate(symbol, interval, start, end, limit=50_000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def _rank(s: np.ndarray) -> np.ndarray:
    order = np.argsort(np.argsort(s))
    return order.astype(float)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0 or not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    return float(np.corrcoef(_rank(a), _rank(b))[0, 1])


def _zscore(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd, sd


def _pearson_arr(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _filter_high_corr(
    X: np.ndarray, y: np.ndarray, names: list[str], threshold: float
) -> tuple[list[int], list[dict[str, Any]]]:
    """基于训练集做相关性去冗余：贪心保留 |单因子IC| 更强的因子，丢弃与之 |相关|>threshold 的。"""
    if threshold is None or threshold >= 1.0 or names and X.shape[1] <= 2:
        return list(range(X.shape[1])), []
    n_feat = X.shape[1]
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.abs(np.corrcoef(X.T))
    # 常数列/全 NaN 列参与相关会得到 NaN，统一视为“与其它因子无关”
    corr[np.isnan(corr)] = 0.0
    univ = np.array([abs(_pearson_arr(X[:, i], y)) if X[:, i].std() > 0 else 0.0 for i in range(n_feat)])
    univ[np.isnan(univ)] = 0.0
    order = np.argsort(-univ)

    keep: list[int] = []
    removed: list[dict[str, Any]] = []
    for idx in order:
        dup = None
        for j in keep:
            if corr[idx, j] > threshold:
                dup = j
                break
        if dup is not None:
            removed.append(
                {
                    "code": names[idx],
                    "name": get_factors().get(names[idx], {}).get("name", names[idx]),
                    "dropped_because": f"与 {names[dup]} |相关|={corr[idx, dup]:.3f} > {threshold}",
                }
            )
        else:
            keep.append(int(idx))
    keep.sort()
    return keep, removed


def _ridge(X: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    # 优先 scikit-learn（更数值稳定），缺失时用 numpy 正规方程
    try:  # pragma: no cover - 可选依赖
        from sklearn.linear_model import Ridge

        return Ridge(alpha=alpha, fit_intercept=False).fit(X, y).coef_.astype(float)
    except Exception:  # noqa: BLE001
        n, p = X.shape
        a = X.T @ X + alpha * np.eye(p)
        return np.linalg.solve(a, X.T @ y)


async def run_ml_research(
    repo: KlineRepository,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    test_ratio: float = 0.25,
    alpha: float = 1.0,
    max_corr: float | None = 0.85,
    model: str = "ridge",
    learning_rate: float = 0.05,
    n_estimators: int = 240,
    max_depth: int = 3,
    num_leaves: int = 7,
    subsample: float = 0.9,
    colsample_bytree: float = 0.9,
    extras: dict[str, dict] | None = None,
    return_artifact: bool = False,
) -> dict[str, Any]:
    """训练多因子（内置或用户表达式因子），返回样本外评价。extras 提供用户因子 code→{expression,...}。"""
    valid = [c for c in codes if meta_or_extra(c, extras) is not None]
    if not valid:
        return {"found": False, "message": "没有可用因子代码或表达式不可用"}

    Xtr_parts: list[np.ndarray] = []
    ytr_parts: list[np.ndarray] = []
    Xte_parts: list[np.ndarray] = []
    yte_parts: list[np.ndarray] = []
    used_symbols: list[str] = []
    feature_cols: list[str] = []

    for sym in symbols:
        df = await _load_frame(repo, sym, interval, start, end)
        if df.empty or len(df) < 60:
            continue
        frame: dict[str, pd.Series] = {"close": df["close"]}
        for code in valid:
            meta = meta_or_extra(code, extras) or {}
            if len(df) < (meta.get("min_bars") or 10) + 2:
                continue
            try:
                frame[code] = compute_series(code, df, extras)
            except (KeyError, ValueError):
                continue
        fdf = pd.DataFrame(frame)
        fdf["label"] = fdf["close"].pct_change(periods=horizon).shift(-horizon)
        cols = valid
        sub = fdf[[*cols, "label"]].dropna()
        if len(sub) < 60:
            continue
        X = sub[cols].to_numpy(dtype=float)
        y = sub["label"].to_numpy(dtype=float)
        n = len(sub)
        n_test = max(30, int(n * test_ratio))
        n_train = n - n_test
        # purge：训练与测试之间留 horizon 根缓冲，避免重叠泄漏
        purge = max(1, horizon)
        te_start = n_train
        tr_end = max(1, te_start - purge)
        Xte_parts.append(X[te_start:])
        yte_parts.append(y[te_start:])
        Xtr_parts.append(X[:tr_end])
        ytr_parts.append(y[:tr_end])
        used_symbols.append(sym)
        if not feature_cols:
            feature_cols = cols

    if not used_symbols or not feature_cols:
        return {"found": False, "message": "窗口内样本不足（至少需 60 根可用 K 线）"}

    Xtr = np.vstack(Xtr_parts)
    ytr = np.concatenate(ytr_parts)
    Xte = np.vstack(Xte_parts)
    yte = np.concatenate(yte_parts)

    # —— 特征相关性去冗余（训练集内） ——
    keep_idx, removed_feats = _filter_high_corr(Xtr, ytr, feature_cols, max_corr)
    feature_cols = [feature_cols[i] for i in keep_idx]
    Xtr = Xtr[:, keep_idx]
    Xte = Xte[:, keep_idx]

    model_name = model if model in ("ridge", "gbdt") else "ridge"
    Xtr_z, _ = _zscore(Xtr)
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0)
    sd[sd == 0] = 1.0
    Xte_z = (Xte - mu) / sd

    if model_name == "gbdt":
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:  # pragma: no cover
            return {"found": False, "message": "未安装 lightgbm（pip install lightgbm）"}
        booster = LGBMRegressor(
            n_estimators=max(10, int(n_estimators)),
            learning_rate=float(learning_rate),
            max_depth=max(-1, int(max_depth)),
            num_leaves=max(2, int(num_leaves)),
            subsample=min(1.0, max(0.1, float(subsample))),
            colsample_bytree=min(1.0, max(0.1, float(colsample_bytree))),
            random_state=7,
        ).fit(Xtr_z, ytr)
        w = booster.feature_importances_.astype(float)
        s = w.sum()
        w = w / s if s > 0 else np.zeros_like(w)
        pred = booster.predict(Xte_z)
        engine = "lightgbm-gbdt"
    else:
        w = _ridge(Xtr_z, ytr, alpha)
        pred = Xte_z @ w
        try:
            import sklearn  # noqa: F401

            engine = "sklearn-ridge"
        except ImportError:
            engine = "numpy-ridge"

    # —— 样本外评价 ——
    def _pearson(a: np.ndarray, b: np.ndarray) -> float:
        if a.std() == 0 or b.std() == 0:
            return float("nan")
        return float(np.corrcoef(a, b)[0, 1])

    if model_name == "ridge":
        pred_train = Xtr_z @ w
    else:
        pred_train = booster.predict(Xtr_z)
    train_ic = _pearson(pred_train, ytr)
    oos_ic_raw = _pearson(pred, yte)
    oos_ic = oos_ic_raw
    overfit_gap = (
        abs(train_ic - oos_ic) if not math.isnan(train_ic) and not math.isnan(oos_ic) else None
    )
    oos_rank_ic = _spearman(pred, yte)
    hit = float(np.mean(np.sign(pred) == np.sign(yte)))

    # top/bottom 20% 平均未来收益差（长空价差代理）
    k = max(5, int(len(pred) * 0.2))
    order = np.argsort(pred)
    bottom = order[:k]
    top = order[-k:]
    top_mean = float(yte[top].mean())
    btm_mean = float(yte[bottom].mean())

    univariate: list[dict[str, Any]] = []
    for i, code in enumerate(feature_cols):
        col = Xte_z[:, i]
        ui = _pearson(col, yte) if len(np.unique(col)) > 2 else float("nan")
        ur = _spearman(col, yte) if len(np.unique(col)) > 2 else float("nan")
        univariate.append(
            {
                "code": code,
                "name": (meta_or_extra(code, extras) or {}).get("name", code),
                "coef": round(float(w[i]), 5),
                "ic_oos": round(ui, 5) if not math.isnan(ui) else None,
                "rank_ic_oos": round(ur, 5) if not math.isnan(ur) else None,
            }
        )
    univariate.sort(key=lambda x: abs(x["coef"]), reverse=True)

    artifact = {
        "model": model_name,
        "interval": interval,
        "horizon": horizon,
        "alpha": alpha,
        "max_corr": max_corr,
        "hyperparams": {
            "learning_rate": learning_rate,
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "num_leaves": num_leaves,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
        },
        "features": [
            {
                **{"code": code, "coef": float(w[i]), "mean": float(mu[i]), "std": float(sd[i])},
                **({"expression": extras[code]["expression"]} if extras and code in extras else {}),
            }
            for i, code in enumerate(feature_cols)
        ],
    }

    resp: dict[str, Any] = {
        "found": True,
        "model": model_name,
        "engine": engine,
        "symbols": used_symbols,
        "interval": interval,
        "horizon": horizon,
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "feature_filter": {"max_corr": max_corr, "removed": removed_feats},
        "oos_ic": round(oos_ic, 5) if not math.isnan(oos_ic) else None,
        "oos_rank_ic": round(oos_rank_ic, 5) if not math.isnan(oos_rank_ic) else None,
        "train_ic": round(train_ic, 5) if not math.isnan(train_ic) else None,
        "overfit_gap": round(overfit_gap, 5) if overfit_gap is not None else None,
        "n_features_kept": len(feature_cols),
        "hit_rate": round(hit, 5),
        "top_quintile_mean_ret": round(top_mean, 6),
        "bottom_quintile_mean_ret": round(btm_mean, 6),
        "spread": round(top_mean - btm_mean, 6),
        "features": univariate,
    }
    if return_artifact:
        resp["artifact"] = artifact
    return resp


async def score_with_model(
    repo: KlineRepository,
    features: list[dict[str, Any]],
    interval: str,
    symbol: str,
    start: datetime,
    end: datetime,
    limit: int = 600,
) -> dict[str, Any]:
    """用已保存模型对某标的区间逐 bar 打分（标准化 + 线性加权），返回最近 limit 个点。"""
    df = await _load_frame(repo, symbol, interval, start, end)
    if df.empty:
        return {"found": False, "message": "区间内无数据"}

    feats = [f for f in features if f.get("std") and float(f["std"]) != 0]
    if not feats:
        return {"found": False, "message": "模型特征参数缺失"}

    series: dict[str, pd.Series] = {}
    for f in feats:
        code = f["code"]
        try:
            if f.get("expression"):
                series[code] = safe_eval_expr(str(f["expression"]), df).astype(float)
            elif code in get_factors():
                series[code] = compute_factor(code, df)
        except Exception:  # noqa: BLE001
            continue
    if not series:
        return {"found": False, "message": "无法在标的/粒度上计算模型特征"}

    fdf = pd.DataFrame(series).dropna()
    score = pd.Series(0.0, index=fdf.index)
    for f in feats:
        code = f["code"]
        if code not in fdf.columns:
            continue
        z = (fdf[code] - float(f["mean"])) / float(f["std"])
        score = score + float(f["coef"]) * z

    pts = score.iloc[-limit:]
    return {
        "found": True,
        "symbol": symbol,
        "interval": interval,
        "n": int(len(pts)),
        "last_score": round(float(pts.iloc[-1]), 6) if len(pts) else None,
        "points": [
            {"ts": pd.Timestamp(ts).to_pydatetime().isoformat(), "score": round(float(v), 6)}
            for ts, v in pts.items()
        ],
    }
