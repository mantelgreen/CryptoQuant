"""ML 多因子训练 v0：特征=研究通过的因子 → 按时间序列切分 → Ridge / LightGBM。

- Ridge：纯 NumPy 实现（无额外依赖）；若环境装了 scikit-learn，会自动用其求更稳的解。
- LightGBM（gbdt）：树模型 artifact 以 pickle 落盘（config.MODEL_STORAGE_DIR），DB 只存引用。
- 标签：未来 h 根收益 close.pct_change(h).shift(-h)。
- 切分：每个标的内部按时间先后切 train/test（保留未来无泄漏），可设 purge 间隔。
- 评价：样本外 IC / RankIC / 方向命中率 / 预测 top vs bottom 分组平均收益差（长空价差代理）。
"""
from __future__ import annotations

import math
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd

from ..core.config import get_settings
from ..data.repository.kline_repo import KlineRepository
from .registry import compute_factor, get_factors
from .compute import compute_series, meta_or_extra
from .sandbox import safe_eval_expr

# 跨粒度聚合特征：用更细粒度 K 线在每根粗 bar 内汇总出的低噪声特征
_AGG_TYPES = ("rv", "vol_imb", "taker_ratio")
_AGG_NAMES = {
    "agg:rv": "细粒度已实现波动率",
    "agg:vol_imb": "量能买卖不平衡",
    "agg:taker_ratio": "主动买占比",
}
_AGG_DESC = {
    "rv": "每根粗 bar 内细粒度对数收益平方和开方（真实波动率，比收盘价 RV 更精确）",
    "vol_imb": "细粒度 (主动买量 − 主动卖量) / 总成交量，衡量买方压力",
    "taker_ratio": "细粒度主动买量 / 总成交量，taker 买盘占比",
}
_INTERVAL_SEC = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def model_storage_dir() -> Path:
    """pickle 模型文件的存放目录（首次调用自动创建）。"""
    d = get_settings().model_storage_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_model_pickle(mid: UUID, obj: Any) -> str:
    """把可 pickle 的模型对象写到存储目录，返回相对文件名（形如 <mid>.pkl）。"""
    rel = f"{mid}.pkl"
    with open(model_storage_dir() / rel, "wb") as fh:
        pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return rel


def load_model_pickle(rel: str) -> Any:
    """按相对文件名读取模型对象（仅取文件名，防目录穿越）。"""
    path = model_storage_dir() / Path(rel or "").name
    with open(path, "rb") as fh:
        return pickle.load(fh)


async def _load_frame(repo: KlineRepository, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = await repo.query_or_aggregate(symbol, interval, start, end)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def _naive_utc_ns(values: Any) -> np.ndarray:
    """把（可能是带时区的）时间序列规整为 naive-UTC 的 datetime64[ns]，便于跨粒度对齐。"""
    idx = pd.DatetimeIndex(values)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return np.asarray(idx, dtype="datetime64[ns]")


async def _load_fine_agg(
    repo: KlineRepository,
    symbol: str,
    fine_interval: str,
    start: datetime,
    end: datetime,
    coarse_index: pd.Index,
) -> dict[str, pd.Series]:
    """拉取细粒度 K 线，按粗 bar 逐根汇总出 rv / vol_imb / taker_ratio 三个低噪声特征。

    返回以 coarse_index 为索引的 Series 字典（缺失桶为 NaN）。
    """
    rows = await repo.query_or_aggregate(symbol, fine_interval, start, end)
    if not rows:
        return {}
    fdf = pd.DataFrame(rows)
    if "open_time" not in fdf.columns or "close" not in fdf.columns:
        return {}
    close = pd.to_numeric(fdf["close"], errors="coerce")
    volume = pd.to_numeric(fdf.get("volume"), errors="coerce")
    tb = pd.to_numeric(fdf.get("taker_buy_base"), errors="coerce")
    r = np.log(close / close.shift(1))

    coarse_times = _naive_utc_ns(coarse_index)
    fine_times = _naive_utc_ns(fdf["open_time"])
    n_coarse = len(coarse_times)
    if n_coarse == 0 or len(fine_times) == 0:
        return {}

    bucket = np.searchsorted(coarse_times, fine_times, side="right") - 1
    valid = (bucket >= 0) & (bucket < n_coarse)

    g = pd.DataFrame(
        {"bucket": bucket[valid], "r2": (r.to_numpy(dtype=float) ** 2)[valid], "vol": volume.to_numpy(dtype=float)[valid], "tb": tb.to_numpy(dtype=float)[valid]}
    ).groupby("bucket")

    rv = np.sqrt(g["r2"].sum())
    vol_sum = g["vol"].sum().replace(0, np.nan)
    tb_sum = g["tb"].sum()
    vol_imb = (2.0 * tb_sum - g["vol"].sum()) / vol_sum
    taker_ratio = tb_sum / vol_sum

    idx = pd.RangeIndex(n_coarse)
    out: dict[str, pd.Series] = {}
    for key, s in (("agg:rv", rv), ("agg:vol_imb", vol_imb), ("agg:taker_ratio", taker_ratio)):
        out[key] = s.reindex(idx).set_axis(coarse_index).astype(float)
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0 or not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    return float(pd.Series(a).rank().corr(pd.Series(b)))


def _zscore(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd, sd


def _clip_bounds(a: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray]:
    """逐列计算 Winsorize 上下界（q / 1-q 分位数，用于钳制厚尾离群值）。"""
    lo = np.nanquantile(a, q, axis=0)
    hi = np.nanquantile(a, 1.0 - q, axis=0)
    return lo, hi


def _std_feature(s: pd.Series, f: dict[str, Any]) -> pd.Series:
    """按模型保存的 clip 上下界与 mean/std 对单列特征做稳健标准化（打分时与训练口径一致）。"""
    if f.get("clip_low") is not None and f.get("clip_high") is not None:
        s = s.clip(float(f["clip_low"]), float(f["clip_high"]))
    return (s - float(f["mean"] or 0.0)) / float(f["std"] or 1.0)


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
                    "name": get_factors().get(names[idx], {}).get("name", _AGG_NAMES.get(names[idx], names[idx])),
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


def _quantile_groups(pred: np.ndarray, groups: int) -> np.ndarray:
    """按预测值从低到高等分成 groups 组，返回每个样本的组号（0 最低 ~ groups-1 最高）。

    采用“秩等分”而非数值阈值，避免重复预测值导致分桶边界错位。
    """
    n = len(pred)
    order = np.argsort(pred, kind="stable")
    grp = np.empty(n, dtype=int)
    for gi in range(groups):
        lo = round(n * gi / groups)
        hi = round(n * (gi + 1) / groups)
        grp[order[lo:hi]] = gi
    return grp


def _downsample_idx(n: int, max_pts: int) -> np.ndarray:
    """均匀抽稀下标（首尾包含），n<=max_pts 时原样返回。"""
    if n <= max_pts:
        return np.arange(n)
    return np.unique(np.round(np.linspace(0, n - 1, max_pts)).astype(int))


def _iso_ts(x: Any) -> str:
    return pd.Timestamp(x).to_pydatetime().isoformat()


def _build_oos_diag(
    ts: np.ndarray,
    pred: np.ndarray,
    actual: np.ndarray,
    max_scatter: int = 600,
    max_nav_pts: int = 280,
) -> dict[str, Any]:
    """把样本外“逐样本预测/实际”整理成页面可直接绘制的诊断数据。

    - scatter：预测分 vs 实际收益散点（按时间抽稀），前端叠加拟合线看 IC 斜率；
    - deciles：按预测分等分 10 组，返回每组平均实际收益 → 单调性柱状图；
    - nav    ：按预测分五等分，在整段样本外时间轴上累计(1+实际收益)的分层净值
      （该组 bar 才“持仓”，其余视为空仓），q5 高分组应在 q1 低分组之上。
    所有序列先按时间升序重排，兼容多标的拼接输入。
    """
    order = np.argsort(np.asarray(ts, dtype="datetime64[ns]"), kind="stable")
    t = np.asarray(ts, dtype="datetime64[ns]")[order]
    p = np.asarray(pred, dtype=float)[order]
    a = np.asarray(actual, dtype=float)[order]
    n = int(p.size)
    out: dict[str, Any] = {"scatter": [], "deciles": [], "nav": {"ts": [], "q1": [], "q2": [], "q3": [], "q4": [], "q5": []}}
    if n == 0:
        return out

    # 1) 散点（抽稀到 max_scatter 内）
    for i in _downsample_idx(n, max_scatter):
        out["scatter"].append(
            {"ts": _iso_ts(t[i]), "pred": round(float(p[i]), 6), "actual": round(float(a[i]), 6)}
        )

    # 2) 十分位单调性：分组数随样本量自适应（10 → 5 → 2），保证每桶非空
    g_dec = 10 if n >= 40 else 5 if n >= 20 else 2
    g_dec = max(1, min(g_dec, n))
    dec_grp = _quantile_groups(p, g_dec)
    for gi in range(g_dec):
        mask = dec_grp == gi
        if mask.sum() == 0:
            continue
        out["deciles"].append(
            {
                "label": f"D{gi + 1}",
                "mean_actual": round(float(a[mask].mean()), 6),
                "mean_pred": round(float(p[mask].mean()), 6),
                "n": int(mask.sum()),
            }
        )

    # 3) 五等分分层净值（整段时间轴累计，非本组 bar 视为空仓）
    g_q = min(5, n)
    nav_grp = _quantile_groups(p, g_q)
    keep = _downsample_idx(n, max_nav_pts)
    for gi in range(g_q):
        wealth = np.cumprod(1.0 + np.where(nav_grp == gi, a, 0.0))
        out["nav"][f"q{gi + 1}"] = [round(float(v), 6) for v in wealth[keep]]
    out["nav"]["ts"] = [_iso_ts(t[i]) for i in keep]
    return out


def _eval_block(pred: np.ndarray, y: np.ndarray, cost_bps: float) -> dict[str, Any]:
    """样本外评价块（返回未取整的原始值，由调用方决定展示精度）。

    IC / RankIC / 方向命中率 / Top−Bottom 价差 / 模型退化体检。
    """
    ic = _pearson_arr(pred, y)
    rank_ic = _spearman(pred, y)
    nz = np.abs(y) > 1e-12
    hit = (
        float(np.mean(np.sign(pred[nz]) == np.sign(y[nz])))
        if int(nz.sum()) > 0
        else float("nan")
    )
    k = max(5, int(len(pred) * 0.2)) if len(pred) else 0
    if k:
        order = np.argsort(pred)
        top = order[-k:]
        bottom = order[:k]
        top_mean = float(y[top].mean())
        btm_mean = float(y[bottom].mean())
    else:
        top_mean = btm_mean = float("nan")
    spread = top_mean - btm_mean
    net_spread = spread - float(cost_bps) / 1e4
    pred_nunique = int(len(np.unique(np.round(pred, 10)))) if pred.size else 0
    pred_std = float(np.std(pred)) if pred.size else 0.0
    degenerate = bool(pred.size > 0 and (pred_nunique < 20 or pred_nunique < 0.05 * pred.size))
    return {
        "ic": ic,
        "rank_ic": rank_ic,
        "hit": hit,
        "top_mean": top_mean,
        "btm_mean": btm_mean,
        "spread": spread,
        "net_spread": net_spread,
        "pred_nunique": pred_nunique,
        "pred_std": pred_std,
        "degenerate": degenerate,
    }


def _univariate_block(
    Xz: np.ndarray, y: np.ndarray, feature_cols: list[str], w: np.ndarray, extras: dict | None
) -> list[dict[str, Any]]:
    """逐特征单因子 IC（在给定样本块上），按 |coef| 降序。"""
    out: list[dict[str, Any]] = []
    for i, code in enumerate(feature_cols):
        col = Xz[:, i]
        ui = _pearson_arr(col, y) if len(np.unique(col)) > 2 else float("nan")
        ur = _spearman(col, y) if len(np.unique(col)) > 2 else float("nan")
        out.append(
            {
                "code": code,
                "name": _AGG_NAMES.get(code)
                or (meta_or_extra(code, extras) or {}).get("name", code),
                "coef": round(float(w[i]), 5),
                "ic_oos": round(ui, 5) if not math.isnan(ui) else None,
                "rank_ic_oos": round(ur, 5) if not math.isnan(ur) else None,
            }
        )
    out.sort(key=lambda x: abs(x["coef"]), reverse=True)
    return out


async def run_ml_research(
    repo: KlineRepository,
    codes: list[str],
    symbols: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    horizon: int = 1,
    test_ratio: float = 0.25,
    val_ratio: float = 0.0,
    alpha: float = 1.0,
    max_corr: float | None = 0.85,
    winsor: float | None = None,
    cost_bps: float = 0.0,
    fine_interval: str | None = None,
    agg_features: list[str] | None = None,
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

    # 跨粒度聚合：仅当 fine_interval 确比 interval 更细且可整除时启用
    agg_codes: list[str] = []
    if fine_interval:
        fi_sec = _INTERVAL_SEC.get(fine_interval)
        it_sec = _INTERVAL_SEC.get(interval)
        if fi_sec and it_sec and fi_sec < it_sec and it_sec % fi_sec == 0:
            seen: set[str] = set()
            for t in agg_features or []:
                if t in _AGG_TYPES and t not in seen:
                    seen.add(t)
                    agg_codes.append(f"agg:{t}")
        else:
            fine_interval = None

    if not valid and not agg_codes:
        return {"found": False, "message": "没有可用因子代码或表达式不可用"}

    Xtr_parts: list[np.ndarray] = []
    ytr_parts: list[np.ndarray] = []
    Xval_parts: list[np.ndarray] = []
    yval_parts: list[np.ndarray] = []
    Xte_parts: list[np.ndarray] = []
    yte_parts: list[np.ndarray] = []
    val_time_parts: list[np.ndarray] = []
    te_time_parts: list[np.ndarray] = []
    used_symbols: list[str] = []
    feature_cols: list[str] = []
    test_windows: list[dict[str, Any]] = []

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
        agg_series: dict[str, pd.Series] = {}
        if agg_codes and fine_interval:
            agg_series = await _load_fine_agg(repo, sym, fine_interval, start, end, df.index)
        for acode in agg_codes:
            frame[acode] = agg_series.get(acode, pd.Series(np.nan, index=df.index))
        fdf = pd.DataFrame(frame)
        fdf["label"] = fdf["close"].pct_change(periods=horizon, fill_method=None).shift(-horizon)
        cols = valid + agg_codes
        sub = fdf[[*cols, "label"]].dropna()
        if len(sub) < 60:
            continue
        X = sub[cols].to_numpy(dtype=float)
        y = sub["label"].to_numpy(dtype=float)
        n = len(sub)
        # 时间序切分：train | (purge) | val | (purge) | test。测试集只在最终评估用一次；
        # 验证集用于方向校准/调参/因子精筛；purge 留 horizon 根缓冲避免标签重叠泄漏。
        purge = max(1, horizon)
        n_test = max(30, int(n * test_ratio))
        use_val = bool(val_ratio and val_ratio > 0)
        n_val = max(30, int(n * val_ratio)) if use_val else 0
        if use_val and n - n_test - n_val < 60:
            use_val = False
            n_val = 0
        te_start = n - n_test
        if use_val:
            val_end = te_start - purge
            val_start = val_end - n_val
            tr_end = val_start - purge
            if tr_end < 60:
                use_val = False
                n_val = 0
        if not use_val:
            tr_end = max(1, te_start - purge)

        Xtr_parts.append(X[:tr_end])
        ytr_parts.append(y[:tr_end])
        if use_val:
            Xval_parts.append(X[val_start:val_end])
            yval_parts.append(y[val_start:val_end])
            val_time_parts.append(
                np.asarray(sub.index[val_start:val_end], dtype="datetime64[ns]")
            )
        Xte_parts.append(X[te_start:])
        yte_parts.append(y[te_start:])
        used_symbols.append(sym)
        te_time_parts.append(np.asarray(sub.index[te_start:], dtype="datetime64[ns]"))
        # 记录该标的本轮切出的真实测试段时间窗（末段 n_test 根），供模型库展示测试集
        idx = sub.index
        test_windows.append(
            {
                "symbol": sym,
                "interval": interval,
                "start": pd.Timestamp(idx[te_start]).to_pydatetime().isoformat(),
                "end": pd.Timestamp(idx[-1]).to_pydatetime().isoformat(),
                "bars": int(len(idx) - te_start),
            }
        )
        if not feature_cols:
            feature_cols = cols

    if not used_symbols or not feature_cols:
        return {"found": False, "message": "窗口内样本不足（至少需 60 根可用 K 线）"}

    Xtr = np.vstack(Xtr_parts)
    ytr = np.concatenate(ytr_parts)
    Xte = np.vstack(Xte_parts)
    yte = np.concatenate(yte_parts)
    te_time = np.concatenate(te_time_parts) if te_time_parts else np.array([], dtype="datetime64[ns]")
    has_val = bool(Xval_parts)
    if has_val:
        Xval = np.vstack(Xval_parts)
        yval = np.concatenate(yval_parts)
        val_time = (
            np.concatenate(val_time_parts)
            if val_time_parts
            else np.array([], dtype="datetime64[ns]")
        )
    else:
        Xval = np.empty((0, Xtr.shape[1]))
        yval = np.array([], dtype=float)
        val_time = np.array([], dtype="datetime64[ns]")

    # —— 稳健化：用训练集分位数对特征与标签做 Winsorize，钳制厚尾离群值 ——
    # 特征需 train/val/test 同口径钳制（打分时同样用这些边界）；标签只对训练目标稳健化，
    # 样本外评价仍用原始收益，保证 IC/价差/命中率反映真实可交易表现。
    clip_low: np.ndarray | None = None
    clip_high: np.ndarray | None = None
    if winsor is not None and 0.0 < winsor < 0.5:
        clip_low, clip_high = _clip_bounds(Xtr, winsor)
        Xtr = np.clip(Xtr, clip_low, clip_high)
        Xte = np.clip(Xte, clip_low, clip_high)
        if has_val:
            Xval = np.clip(Xval, clip_low, clip_high)
        y_lo = float(np.quantile(ytr, winsor))
        y_hi = float(np.quantile(ytr, 1.0 - winsor))
        ytr = np.clip(ytr, y_lo, y_hi)

    # —— 特征相关性去冗余（训练集内） ——
    keep_idx, removed_feats = _filter_high_corr(Xtr, ytr, feature_cols, max_corr)
    feature_cols = [feature_cols[i] for i in keep_idx]
    Xtr = Xtr[:, keep_idx]
    Xte = Xte[:, keep_idx]
    if has_val:
        Xval = Xval[:, keep_idx]
    if clip_low is not None and clip_high is not None:
        clip_low = clip_low[keep_idx]
        clip_high = clip_high[keep_idx]

    model_name = model if model in ("ridge", "gbdt") else "ridge"
    Xtr_z, _ = _zscore(Xtr)
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0)
    sd[sd == 0] = 1.0
    Xte_z = (Xte - mu) / sd
    Xval_z = (Xval - mu) / sd if has_val else np.empty((0, Xtr_z.shape[1]))

    # LightGBM 超参先做联合约束：max_depth>0 时需满足 num_leaves <= 2^max_depth，否则训练期抛 LightGBMError
    max_depth_v = max(-1, int(max_depth))
    num_leaves_v = max(2, int(num_leaves))
    if max_depth_v > 0:
        num_leaves_v = min(num_leaves_v, 2 ** max_depth_v)

    learning: dict[str, Any] | None = None
    best_iteration: int | None = None
    if model_name == "gbdt":
        try:
            from lightgbm import LGBMRegressor, early_stopping, record_evaluation
        except ImportError as exc:  # pragma: no cover
            return {"found": False, "message": "未安装 lightgbm（pip install lightgbm）"}
        lgb_params: dict[str, Any] = {
            "n_estimators": max(10, int(n_estimators)),
            "learning_rate": float(learning_rate),
            "max_depth": max_depth_v,
            "num_leaves": num_leaves_v,
            # L2 正则用 sklearn 参数 reg_lambda（原 lambda_l2 是原生别名，且 alpha*1e-3 实际≈关闭）。
            # 叶子 hessian 之和 ≥ min_child_samples(默认20)，alpha=1 时收缩约 5%，温和有效。
            "reg_lambda": max(0.0, float(alpha)),
            "subsample": min(1.0, max(0.1, float(subsample))),
            # LightGBM 需 bagging_freq>0 才启用行采样，否则 subsample 是空操作
            "subsample_freq": 1,
            "colsample_bytree": min(1.0, max(0.1, float(colsample_bytree))),
            "random_state": 7,
        }
        booster = None
        # 学习曲线用验证集（无验证集时退回测试集，仅展示）；早停只在有独立验证集时启用，避免用测试集早停泄漏。
        eval_X = (Xtr_z, Xval_z) if has_val else (Xtr_z, Xte_z)
        eval_y = (ytr, yval) if has_val else (ytr, yte)
        try:
            evals: dict[str, Any] = {}
            callbacks: list[Any] = [record_evaluation(evals)]
            if has_val:
                callbacks.append(early_stopping(30, verbose=False))
            booster = LGBMRegressor(**lgb_params).fit(
                Xtr_z,
                ytr,
                eval_X=eval_X,
                eval_y=eval_y,
                eval_names=["train", "oos"],
                eval_metric="l2",
                callbacks=callbacks,
            )
            # 早停后 sklearn predict / booster.predict 会自动采用 best_iteration，artifact 口径一致
            best_iteration = int(getattr(booster, "best_iteration_", 0) or 0) or None
            train_loss = next(iter((evals.get("train") or {}).values()), None)
            oos_loss = next(iter((evals.get("oos") or {}).values()), None)
            if train_loss and oos_loss and len(train_loss) == len(oos_loss):
                learning = {
                    "rounds": list(range(1, len(train_loss) + 1)),
                    "train": [round(float(v), 8) for v in train_loss],
                    "oos": [round(float(v), 8) for v in oos_loss],
                }
                if best_iteration:
                    learning["best_iteration"] = best_iteration
                    learning["best_oos"] = round(float(oos_loss[best_iteration - 1]), 8)
        except Exception:  # noqa: BLE001 - 记录失败不影响训练本身
            booster = None
        if booster is None:
            booster = LGBMRegressor(**lgb_params).fit(Xtr_z, ytr)
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

    # —— 方向自校准：仅用「训练集」决定符号，避免用验证/测试集挑方向（防泄漏） ——
    if model_name == "ridge":
        pred_train = Xtr_z @ w
        pred_val = Xval_z @ w if has_val else np.array([])
    else:
        pred_train = booster.predict(Xtr_z)
        pred_val = booster.predict(Xval_z) if has_val else np.array([])
    pred = Xte_z @ w if model_name == "ridge" else booster.predict(Xte_z)

    pred_flip = 1.0
    raw_rank = _spearman(pred_train, ytr)
    if not math.isnan(raw_rank) and raw_rank < 0:
        pred_flip = -1.0
        pred_train = -pred_train
        pred = -pred
        if has_val:
            pred_val = -pred_val

    train_ic = _pearson_arr(pred_train, ytr)
    test = _eval_block(pred, yte, cost_bps)
    val = _eval_block(pred_val, yval, cost_bps) if has_val else None

    oos_ic = test["ic"]
    oos_rank_ic = test["rank_ic"]
    overfit_gap = (
        abs(train_ic - oos_ic)
        if not math.isnan(train_ic) and not math.isnan(oos_ic)
        else None
    )

    def _r(v: Any, n: int) -> float | None:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        return round(fv, n) if not math.isnan(fv) else None

    univariate = _univariate_block(Xte_z, yte, feature_cols, w, extras)
    val_univariate = (
        _univariate_block(Xval_z, yval, feature_cols, w, extras) if has_val else None
    )

    # —— 样本外诊断图表：散点 / 十分位单调性 / 分层净值（逐样本 pred·actual·时间） ——
    oos_charts: dict[str, Any] = {}
    if te_time.size == yte.size and yte.size > 0:
        oos_charts = _build_oos_diag(te_time, pred, yte)
    val_charts: dict[str, Any] = {}
    if has_val and val_time.size == yval.size and yval.size > 0:
        val_charts = _build_oos_diag(val_time, pred_val, yval)

    artifact = {
        "model": model_name,
        "interval": interval,
        "horizon": horizon,
        "alpha": alpha,
        "max_corr": max_corr,
        "winsor": winsor,
        "cost_bps": cost_bps,
        "fine_interval": fine_interval,
        "agg_features": agg_codes,
        "hyperparams": {
            "learning_rate": learning_rate,
            "n_estimators": n_estimators,
            "max_depth": max_depth_v,
            "num_leaves": num_leaves_v,
            "reg_lambda": max(0.0, float(alpha)),
            "subsample": subsample,
            "subsample_freq": 1,
            "colsample_bytree": colsample_bytree,
            "best_iteration": best_iteration,
        },
        "features": [
            {
                **{"code": code, "coef": float(w[i]), "mean": float(mu[i]), "std": float(sd[i])},
                **(
                    {"name": _AGG_NAMES.get(code), "aggregate": {"type": code.split(":", 1)[1], "fine_interval": fine_interval}}
                    if code.startswith("agg:") and fine_interval
                    else {}
                ),
                **(
                    {"clip_low": float(clip_low[i]), "clip_high": float(clip_high[i])}
                    if clip_low is not None and clip_high is not None
                    else {}
                ),
                **({"expression": extras[code]["expression"]} if extras and code in extras else {}),
            }
            for i, code in enumerate(feature_cols)
        ],
        "flip": pred_flip,
        "booster": booster if model_name == "gbdt" else None,
    }

    resp: dict[str, Any] = {
        "found": True,
        "model": model_name,
        "engine": engine,
        "symbols": used_symbols,
        "interval": interval,
        "horizon": horizon,
        "winsor": winsor,
        "cost_bps": cost_bps,
        "fine_interval": fine_interval,
        "agg_features": agg_codes,
        "test_windows": test_windows,
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "n_val": int(len(yval)) if has_val else 0,
        "val_ratio": val_ratio,
        "feature_filter": {"max_corr": max_corr, "removed": removed_feats},
        "oos_ic": _r(oos_ic, 5),
        "oos_rank_ic": _r(oos_rank_ic, 5),
        "train_ic": _r(train_ic, 5),
        "overfit_gap": _r(overfit_gap, 5),
        "n_features_kept": len(feature_cols),
        "hit_rate": _r(test["hit"], 5),
        "top_quintile_mean_ret": _r(test["top_mean"], 6),
        "bottom_quintile_mean_ret": _r(test["btm_mean"], 6),
        "spread": _r(test["spread"], 6),
        "net_spread": _r(test["net_spread"], 6),
        "pred_nunique": test["pred_nunique"],
        "pred_std": _r(test["pred_std"], 8),
        "degenerate": test["degenerate"],
        "flip": pred_flip,
        "best_iteration": best_iteration,
        "features": univariate,
        "oos_charts": oos_charts,
    }
    if val is not None:
        resp.update(
            {
                "val_ic": _r(val["ic"], 5),
                "val_rank_ic": _r(val["rank_ic"], 5),
                "val_hit_rate": _r(val["hit"], 5),
                "val_spread": _r(val["spread"], 6),
                "val_net_spread": _r(val["net_spread"], 6),
                "val_overfit_gap": _r(
                    abs(train_ic - val["ic"])
                    if not math.isnan(train_ic) and not math.isnan(val["ic"])
                    else None,
                    5,
                ),
                "val_degenerate": val["degenerate"],
                "val_features": val_univariate,
                "val_charts": val_charts,
            }
        )
    if learning is not None:
        resp["learning"] = learning
    if return_artifact:
        resp["artifact"] = artifact
    return resp


async def score_with_model(
    repo: KlineRepository,
    model: dict[str, Any],
    interval: str,
    symbol: str,
    start: datetime,
    end: datetime,
    limit: int = 600,
) -> dict[str, Any]:
    """用已保存模型对某标的区间逐 bar 打分，返回最近 limit 个点。

    ridge：按特征 mean/std 标准化后线性加权（coef）。
    gbdt ：从磁盘加载 pickle 模型，特征按训练顺序标准化后用 booster.predict。
    """
    df = await _load_frame(repo, symbol, interval, start, end)
    if df.empty:
        return {"found": False, "message": "区间内无数据"}

    model_type = (model.get("model_type") or "ridge")
    features = model.get("features") or []
    feats = [f for f in features if f.get("code") and f.get("std") is not None and float(f.get("std", 0)) != 0]
    if not feats:
        return {"found": False, "message": "模型特征参数缺失"}

    series: dict[str, pd.Series] = {}
    agg_feats = [f for f in features if f.get("aggregate")]
    agg_series: dict[str, pd.Series] = {}
    if agg_feats:
        fine_interval = agg_feats[0].get("aggregate", {}).get("fine_interval")
        if fine_interval:
            agg_series = await _load_fine_agg(repo, symbol, str(fine_interval), start, end, df.index)
    for f in features:
        code = f["code"]
        try:
            if f.get("expression"):
                series[code] = safe_eval_expr(str(f["expression"]), df).astype(float)
            elif f.get("aggregate"):
                if code in agg_series:
                    series[code] = agg_series[code]
            elif code in get_factors():
                series[code] = compute_factor(code, df)
        except Exception:  # noqa: BLE001
            continue
    if not series:
        return {"found": False, "message": "无法在标的/粒度上计算模型特征"}

    fdf = pd.DataFrame(series).dropna()

    if model_type == "gbdt":
        missing = [f["code"] for f in feats if f["code"] not in fdf.columns]
        if missing:
            return {
                "found": False,
                "message": "树模型需要全部特征才能打分，以下特征无法计算：" + "、".join(missing[:5]),
            }
        try:
            booster = load_model_pickle(model.get("model_path") or "")
        except Exception as exc:  # noqa: BLE001
            return {"found": False, "message": f"模型文件缺失或损坏：{exc}"}
        cols = [f["code"] for f in feats]
        z = fdf[cols].copy()
        for f in feats:
            z[f["code"]] = _std_feature(z[f["code"]], f)
        pred = booster.predict(z.to_numpy(dtype=float))
        score = pd.Series(pred, index=fdf.index)
    else:
        score = pd.Series(0.0, index=fdf.index)
        for f in feats:
            code = f["code"]
            if code not in fdf.columns:
                continue
            z = _std_feature(fdf[code], f)
            score = score + float(f["coef"] or 0.0) * z

    # 方向自校准：保存时若 OOS 整体反向已做 flip=-1，这里同样取反，保证“高分为多”
    flip = float((model.get("dataset") or {}).get("flip", 1) or 1)
    if flip < 0:
        score = -score

    pts = score.iloc[-limit:]
    return {
        "found": True,
        "symbol": symbol,
        "interval": interval,
        "model_type": model_type,
        "flip": flip,
        "n": int(len(pts)),
        "last_score": round(float(pts.iloc[-1]), 6) if len(pts) else None,
        "points": [
            {"ts": pd.Timestamp(ts).to_pydatetime().isoformat(), "score": round(float(v), 6)}
            for ts, v in pts.items()
        ],
    }
