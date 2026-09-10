"""回测任务执行（被 ARQ worker 调用）。

流程：加载 klines → 选 symbol → 向量化回测 → 存 equity / trades / summary。
每个关键阶段向 Redis `ws:backtest:{id}` 发布进度，供网关 SSE 实时推送。
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import pandas as pd

from ...core.config import get_settings
from ...core.db import SessionLocal
from ...core.logging import get_logger
from ...core.redis import get_redis
from ...data.repository.kline_repo import KlineRepository
from ..engine.repo import BacktestRepository
from ..engine.runner import BacktestConfig, run_backtest_vectorized
from ..engine.templates import get_templates

logger = get_logger(__name__)
settings = get_settings()


async def _materialize_ml_scorer(session, template_id: str, params: dict, model_row: dict | None = None) -> dict:
    """ml_score + gbdt：回测需真实树模型打分。按 model_id 从磁盘加载 pickle 注入 params。

    strategy_versions.params 只存 JSON（features/model_type/model_id），树对象在这里临时注入，
    不入库、不参与任务序列化；同时把训练时记录的 flip 方向一起注入，保证“高分为多”。
    """
    if template_id != "ml_score":
        return params
    params = dict(params)
    if model_row is None:
        raise RuntimeError("模型不存在或未落盘 artifact（model_row 缺失）")
    if str(params.get("model_type") or "").lower() == "gbdt":
        if not model_row.get("model_path"):
            raise RuntimeError("模型不存在或未落盘 artifact（model_path 缺失）")
        from ...factor.ml import load_model_pickle

        params["_booster"] = load_model_pickle(model_row["model_path"])
    params["features"] = model_row.get("features") or params.get("features") or []
    params["_flip"] = float((model_row.get("dataset") or {}).get("flip", 1) or 1)
    return params


async def _load_ml_model_row(session, template_id: str, params: dict) -> dict | None:
    """ml_score 策略：按 params.model_id 取保存的模型行（含训练窗口 / 树 artifact 路径）。"""
    if template_id != "ml_score" or not params.get("model_id"):
        return None
    from uuid import UUID as _UUID

    from ...factor.repo import MlModelRepository

    row = await MlModelRepository(session).get(_UUID(str(params["model_id"])))
    return row


def _clip_ml_start(row: dict | None, symbol: str, start: datetime) -> datetime:
    """样本排除：回测标的若参与过该模型训练，把起点抬到训练窗口结束之后。"""
    if not row:
        return start
    train_end = row.get("train_end")
    if not train_end:
        return start
    if symbol not in (row.get("symbols") or []):
        return start
    return max(start, train_end)


async def _publish_progress(
    backtest_id: UUID,
    *,
    status: str,
    stage: str | None = None,
    pct: int | None = None,
    error: str | None = None,
    summary: dict | None = None,
) -> None:
    payload = {
        "type": "progress",
        "backtest_id": str(backtest_id),
        "status": status,
        "stage": stage,
        "pct": pct,
        "error": error,
        "summary": summary,
    }
    try:
        r = get_redis()
        await r.publish(f"ws:backtest:{backtest_id}", json.dumps(payload, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.debug("backtest.progress_publish_failed", error=str(exc))


async def run_backtest_job(
    backtest_id: UUID,
    strategy_id: UUID,
    version_id: UUID,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    params: dict,
    template_id: str,
) -> None:
    """执行一次回测并写库。"""
    async with SessionLocal() as session:
        repo = BacktestRepository(session)
        try:
            # 0. ml_score 策略：取模型行，做训练期样本排除（评估起点抬到训练窗口后）。
            #    护栏校验也在 try 内：一旦触发就把该回测置为 failed 并推送终态，避免前端“排队/运行中”卡死。
            model_row = await _load_ml_model_row(session, template_id, params)
            start = _clip_ml_start(model_row, symbol, start)
            if start >= end:
                train_end = (model_row or {}).get("train_end")
                raise RuntimeError(
                    f"回测窗口与训练数据区间重叠已按样本排除（{symbol} 训练至 "
                    f"{train_end:%Y-%m-%d %H:%M}），窗口内已无样本可回测；"
                    "请选择训练结束之后的回测区间，或缩短训练期后再保存模型。"
                )
            await repo.update_status(backtest_id, status="running", progress_pct=5, stage="loading_data")
            await _publish_progress(backtest_id, status="running", stage="loading_data", pct=5)
            # 1. 加载 K 线（支持自动桶聚合：库里只有 15m 时也能回测 30m/1h/1d）
            kline_repo = KlineRepository(session)
            rows = await kline_repo.query_or_aggregate(symbol, interval, start, end)
            if not rows:
                cover = await kline_repo.coverage(symbol)
                have = ", ".join(f"{r['interval']}×{r['count']}" for r in cover) or "无"
                raise RuntimeError(
                    f"窗口内无 {interval} 数据。该币本地已有：{have}。"
                    "可先到「K 线下载」补齐该区间，或把时间窗改到已有数据范围内。"
                )
            df = pd.DataFrame(rows)
            df = df.rename(columns={"open_time": "time"}).set_index("time").sort_index()
            for c in ("open", "high", "low", "close", "volume"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            await repo.update_status(backtest_id, status="running", progress_pct=35, stage="backtesting")
            await _publish_progress(backtest_id, status="running", stage="backtesting", pct=35)

            # 2. 回测（gbdt 需先注入树模型）
            template_meta = get_templates().get(template_id, {})
            allow_short = bool(template_meta.get("long_short", False))
            cfg = BacktestConfig(initial_cash=100_000.0, fee_rate=0.001, allow_short=allow_short)
            bt_params = await _materialize_ml_scorer(session, template_id, params, model_row)
            # 跨粒度聚合特征：把细粒度数据在每根粗 bar 内汇总出的 agg:* 列注入 df，供 ml_score 打分
            agg_feats = [f for f in (bt_params.get("features") or []) if f.get("aggregate")]
            if agg_feats:
                from ...factor.ml import _load_fine_agg

                for fi in {
                    f.get("aggregate", {}).get("fine_interval")
                    for f in agg_feats
                    if f.get("aggregate", {}).get("fine_interval")
                }:
                    agg_series = await _load_fine_agg(kline_repo, symbol, str(fi), start, end, df.index)
                    for code, s in agg_series.items():
                        df[code] = s
            result = run_backtest_vectorized(
                ohlcv=df, template_id=template_id, params=bt_params, cfg=cfg, interval=interval
            )
            await repo.update_status(backtest_id, status="running", progress_pct=80, stage="persisting")
            await _publish_progress(backtest_id, status="running", stage="persisting", pct=80)

            # 3. 写 equity 曲线（抽样，避免太多行）
            equity = result["equity"]
            step = max(1, len(equity) // 1500)
            pts = [(pd.Timestamp(ts).to_pydatetime(), float(v)) for ts, v in equity.iloc[::step].items()]
            await repo.save_equity("backtest", backtest_id, pts)

            trades = result["trades"]
            for t in trades:
                t["symbol"] = symbol
            await repo.save_trades(backtest_id, trades)

            # 4. summary
            summary = {
                "template_id": template_id,
                "symbol": symbol,
                "interval": interval,
                "metrics": result["metrics"],
                "final_equity": float(equity.iloc[-1]) if len(equity) else None,
                "bars": len(equity),
            }
            await repo.update_status(
                backtest_id, status="succeeded", progress_pct=100, stage="done", summary=summary
            )
            await _publish_progress(
                backtest_id, status="succeeded", stage="done", pct=100, summary=summary
            )
            logger.info("backtest.done", backtest_id=str(backtest_id), final=str(summary.get("final_equity")))
        except Exception as exc:
            logger.error("backtest.failed", backtest_id=str(backtest_id), error=str(exc))
            await repo.update_status(backtest_id, status="failed", error=str(exc))
            await _publish_progress(backtest_id, status="failed", error=str(exc))
            raise
