"""AI 流水线编排服务：因子生成→研究→选择→训练→回测→评估→（入库|诊断）多轮自进化闭环。

进程模型：start 入队 round 1；worker 每跑完一轮再入队下一轮（一轮一 job，规避 ARQ 30 分钟超时）。
进度经 Redis `ws:pipeline:{id}` 推送，由网关 SSE 转发。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..core.redis import get_redis
from .repo import PipelineRoundRepository, PipelineRunRepository

logger = get_logger(__name__)

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "oos_rank_ic": 0.02,
    "spread": 0.0,
    "hit_rate": 0.5,
    "overfit_gap": 0.06,
    "total_return": 0.0,
    "excess_return": 0.0,
    "sharpe": 0.5,
    "max_drawdown": -0.20,
    "num_trades": 3,
    "excellent_sharpe": 1.0,
    "excellent_max_drawdown": -0.12,
    "param_retry": 3,
}

_STAGES = ("gen", "research", "select", "train", "backtest", "evaluate", "finalize")

# 可由 LLM 诊断建议覆盖的训练超参键（与 agent.service._HYPER_SPEC 对齐）
_PARAM_KEYS = (
    "model",
    "horizon",
    "test_ratio",
    "alpha",
    "max_corr",
    "winsor",
    "learning_rate",
    "n_estimators",
    "max_depth",
    "num_leaves",
    "min_child_samples",
    "subsample",
    "colsample_bytree",
)


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _norm_expr(expr: str | None) -> str:
    return " ".join((expr or "").split())


def _pct(round_no: int, max_rounds: int, stage_idx: int) -> int:
    n = len(_STAGES)
    base = (round_no - 1) / max(1, max_rounds)
    within = (stage_idx + 1) / n / max(1, max_rounds)
    return int(min(100, (base + within) * 100))


async def publish_progress(run_id: str, **payload: Any) -> None:
    frame = json.dumps({"type": "progress", "run_id": run_id, **payload}, default=str)
    try:
        r = get_redis()
        await r.publish(f"ws:pipeline:{run_id}", frame)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pipeline.progress_publish_failed", error=str(exc))


async def _publish(
    run_id: str,
    *,
    round_no: int,
    max_rounds: int,
    stage_idx: int,
    phase: str,
    status: str,
    message: str = "",
    **extra: Any,
) -> None:
    await publish_progress(
        run_id,
        status=status,
        round_no=round_no,
        phase=phase,
        pct=_pct(round_no, max_rounds, stage_idx),
        message=message,
        **extra,
    )


# =============================================================================
# 配置校验 / 归一化
# =============================================================================


def _validate_config(cfg: dict[str, Any]) -> None:
    from ..core.errors import BadRequest

    syms = [s.strip().upper() for s in (cfg.get("research_symbols") or []) if s.strip()]
    if not syms:
        raise BadRequest("research_symbols_required")
    rs = _parse_dt(cfg["research_start"])
    re_ = _parse_dt(cfg["research_end"])
    bs = _parse_dt(cfg["backtest_start"])
    be = _parse_dt(cfg["backtest_end"])
    if re_ <= rs:
        raise BadRequest("research_end_must_be_after_start")
    if be <= bs:
        raise BadRequest("backtest_end_must_be_after_start")
    if bs < re_:
        raise BadRequest(
            "backtest_window_must_follow_research_window（强制样本外：回测起点须晚于训练结束）"
        )
    mr = int(cfg.get("max_rounds") or 1)
    if mr < 1 or mr > 20:
        raise BadRequest("max_rounds_out_of_range")


def _normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    th = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
    out = dict(cfg)
    out["name"] = (cfg.get("name") or "").strip() or f"流水线-{datetime.now():%m%d%H%M}"
    out["research_symbols"] = [
        s.strip().upper() for s in (cfg.get("research_symbols") or []) if s.strip()
    ]
    out["backtest_symbol"] = (cfg.get("backtest_symbol") or "").strip().upper() or out[
        "research_symbols"
    ][0]
    out["backtest_interval"] = cfg.get("backtest_interval") or cfg.get(
        "research_interval"
    )
    out["research_start"] = _parse_dt(cfg["research_start"]).isoformat()
    out["research_end"] = _parse_dt(cfg["research_end"]).isoformat()
    out["backtest_start"] = _parse_dt(cfg["backtest_start"]).isoformat()
    out["backtest_end"] = _parse_dt(cfg["backtest_end"]).isoformat()
    out["factor_count_per_round"] = max(
        1, min(5, int(cfg.get("factor_count_per_round") or 4))
    )
    out["max_pick"] = max(2, min(12, int(cfg.get("max_pick") or 6)))
    out["max_rounds"] = int(cfg.get("max_rounds") or 5)
    # 验证集比例：调参/因子精筛只在验证集上做，测试集仅最终报告一次（默认 0.15）
    vr = cfg.get("val_ratio")
    out["val_ratio"] = max(0.0, min(0.4, float(0.15 if vr is None else vr)))
    # 稳健化 Winsorize：默认 1%（加密厚尾收益），抑制离群值对 L2/相关性过滤的扰动；显式传 0 可关闭
    wv = cfg.get("winsor")
    out["winsor"] = float(wv) if wv is not None else 0.01
    # 往返成本（bps）：把毛价差折成净价差，模型层达标也按净价差判定；默认 15bps
    cb = cfg.get("cost_bps")
    out["cost_bps"] = float(cb) if cb is not None else 15.0
    # 特征标准化：global（默认，全局 mean/std）| expanding（因果滚动 z-score，适配 regime 漂移）
    fnn = (cfg.get("feature_norm") or "global").strip().lower()
    out["feature_norm"] = fnn if fnn in ("global", "expanding") else "global"
    out["model"] = cfg.get("model") or "ridge"
    # gbdt 目标函数：默认 huber（对厚尾稳健），可选 regression(l2)/regression_l1
    go = (cfg.get("gbdt_objective") or "huber").strip().lower()
    out["gbdt_objective"] = go if go in ("regression", "regression_l1", "huber") else "huber"
    out["min_child_samples"] = max(
        5, min(200, int(cfg.get("min_child_samples") or 20))
    )
    out["seed_ensemble"] = max(1, min(10, int(cfg.get("seed_ensemble") or 3)))
    # 回测择时参数：score 滚动分位窗口 + 进出场分位（可配，默认 60/0.8/0.2）
    out["bt_window"] = max(20, min(240, int(cfg.get("bt_window") or 60)))
    out["bt_enter_q"] = max(0.5, min(0.95, float(cfg.get("bt_enter_q") or 0.8)))
    out["bt_exit_q"] = max(0.05, min(0.5, float(cfg.get("bt_exit_q") or 0.2)))
    out["thresholds"] = th
    out["stop_on_excellent"] = bool(cfg.get("stop_on_excellent", True))
    out["save_quality_factors"] = bool(cfg.get("save_quality_factors", True))
    return out


# =============================================================================
# 编排入口
# =============================================================================


async def start_pipeline(db: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    _validate_config(config)
    cfg = _normalize_config(config)
    rid = await PipelineRunRepository(db).create(name=cfg["name"], config=cfg)

    from arq import create_pool
    from arq.connections import RedisSettings

    from ..core.config import get_settings

    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await redis.enqueue_job(
            "run_pipeline_round_task", str(rid), 1, _queue_name=settings.JOB_QUEUE_DATA
        )
    finally:
        await redis.aclose()
    return {"run_id": str(rid), "status": "pending"}


async def stop_pipeline(db: AsyncSession, run_id: UUID) -> bool:
    rrun = PipelineRunRepository(db)
    run = await rrun.get(run_id)
    if not run:
        return False
    if run["status"] in ("succeeded", "finished", "failed", "stopped"):
        return True  # 已终态，幂等
    await rrun.update_status(run_id, status="stopped")
    await publish_progress(str(run_id), status="stopped")
    return True


async def delete_pipeline(db: AsyncSession, run_id: UUID) -> bool:
    rrun = PipelineRunRepository(db)
    run = await rrun.get(run_id)
    if not run:
        return False
    if run["status"] not in ("succeeded", "finished", "failed", "stopped"):
        await rrun.update_status(run_id, status="stopped")
        await publish_progress(str(run_id), status="stopped")
    return await rrun.delete(run_id)


async def list_pipelines(db: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    return await PipelineRunRepository(db).list_recent(limit)


async def get_pipeline_detail(db: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    rrun = PipelineRunRepository(db)
    run = await rrun.get(run_id)
    if not run:
        return None
    rounds = await PipelineRoundRepository(db).list_for(run_id)
    return {"run": run, "rounds": rounds}


# =============================================================================
# 单轮执行
# =============================================================================


def _apply_params(cfg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 诊断出的超参覆盖到当前配置，返回新配置（不改原对象）。"""
    out = dict(cfg)
    for k, v in (params or {}).items():
        if k in _PARAM_KEYS and v is not None:
            out[k] = v
    return out


async def run_round(
    db: AsyncSession, run: dict[str, Any], round_no: int
) -> dict[str, Any]:
    cfg = run["config"]
    th = cfg.get("thresholds") or DEFAULT_THRESHOLDS
    max_rounds = int(cfg.get("max_rounds") or 1)
    run_id = run["id"]
    rid_str = str(run_id)
    rrun = PipelineRunRepository(db)
    rrepo = PipelineRoundRepository(db)

    round_id = await rrepo.create(pipeline_run_id=run_id, round_no=round_no)

    async def mark(
        phase: str,
        status: str = "running",
        stage_idx: int = 0,
        message: str = "",
        **extra: Any,
    ) -> None:
        await rrepo.update(round_id, phase=phase, status=status)
        await _publish(
            rid_str,
            round_no=round_no,
            max_rounds=max_rounds,
            stage_idx=stage_idx,
            phase=phase,
            status=status,
            message=message,
            **extra,
        )

    prev_diag = None
    if round_no > 1:
        prev = await rrepo.get_by_no(run_id, round_no - 1)
        if prev:
            prev_diag = prev.get("diagnosis")

    # 1. gen
    await mark("gen", stage_idx=0, message="生成因子")
    new_codes = await _gen_factors(db, cfg, prev_diag)
    # 新因子前置：挑选阶段只取候选前 40 条，前置可保证本轮新因子一定进入挑选视野，
    # 不被历史累积的因子池挤出（研究阶段仍对整池计算 IC）。
    prev_pool = [c for c in (run.get("factor_pool") or []) if isinstance(c, str)]
    pool = list(dict.fromkeys([*new_codes, *prev_pool]))
    await rrun.update_status(
        run_id, status="running", factor_pool=pool, current_round=round_no
    )

    # 2. research
    await mark("research", stage_idx=1, message="实测因子 IC/RankIC")
    research = await _research_pool(db, cfg, pool)

    # 3. select
    await mark("select", stage_idx=2, message="AI 选择因子")
    picked, _pick_meta = await _pick(db, cfg, research)

    # name map（诊断负反馈用）
    name_map = {r["code"]: r.get("name") or r["code"] for r in research}

    # 因子详情（name/description/expression），供流水线记录展示
    from ..factor.service import build_factor_extras

    _extras = await build_factor_extras(db)

    def _factor_detail(code: str) -> dict[str, Any]:
        e = _extras.get(str(code)) or {}
        return {
            "code": code,
            "name": e.get("name") or name_map.get(code, code),
            "description": e.get("description") or "",
            "expression": e.get("expression") or "",
        }

    # 4. train
    await mark("train", stage_idx=3, message="训练多因子模型")
    train_resp = await _train(db, cfg, picked)

    model_id: UUID | None = None
    diagnosis: dict[str, Any] | None = None
    next_pool: list[str] = pool
    terminate = False
    succeeded = False
    active_cfg = cfg

    best_score = None
    if run.get("best_metrics"):
        best_score = (run["best_metrics"] or {}).get("sharpe")

    # 参数调优循环：仅在「验证集」上判定是否达标并给诊断，测试集不参与调参（防泄漏/多重检验）。
    # 复用同一批已选因子（不重新生成/研究），开销可控；仍不理想则本轮停止、进入下一轮。
    param_retry_limit = int(th.get("param_retry", 3) or 0)
    param_attempts = 0
    applied_param_keys: set[str] = set()
    val_ok = _ml_ok(train_resp, th, val=True)
    if not val_ok and param_retry_limit > 0:
        diagnosis = await _diagnose(db, active_cfg, train_resp, None, name_map, "warn")
        while (
            not val_ok
            and param_attempts < param_retry_limit
            and diagnosis.get("root_cause") == "params"
            and diagnosis.get("params")
        ):
            param_attempts += 1
            applied_param_keys |= {
                k for k in (diagnosis.get("params") or {}) if k in _PARAM_KEYS
            }
            active_cfg = _apply_params(active_cfg, diagnosis.get("params") or {})
            await mark(
                "train",
                stage_idx=3,
                message=f"参数调优重训（第 {param_attempts}/{param_retry_limit} 次，按验证集判定）",
                attempt=param_attempts,
                params=diagnosis.get("params") or {},
            )
            train_resp = await _train(db, active_cfg, picked)
            val_ok = _ml_ok(train_resp, th, val=True)
            if val_ok:
                break
            diagnosis = await _diagnose(db, active_cfg, train_resp, None, name_map, "warn")

    # 5. backtest：真正的样本外持有窗，调参结束后只跑一次
    await mark("backtest", stage_idx=4, message="样本外回测")
    bt = await _backtest(db, active_cfg, train_resp)

    # 6. evaluate：测试集 + 回测，最终判定（测试集只在此处用一次）
    await mark("evaluate", stage_idx=5, message="效果评估")
    grade, excellent, score, checks = evaluate_round(train_resp, bt, th)

    if grade == "ok" and train_resp.get("found") and "artifact" in train_resp:
        # 入库（使用调优后的最终参数）
        await mark("finalize", stage_idx=6, message="入库模型")
        saved = await _persist(db, active_cfg, train_resp, round_no, run["name"])
        model_id = UUID(saved["model_id"])
        # 优质因子入因子库：把进入该模型的因子打上「优质」标签
        n_quality = 0
        if active_cfg.get("save_quality_factors", True):
            quality_codes = [
                f.get("code")
                for f in (train_resp.get("artifact") or {}).get("features") or []
                if f.get("code")
            ]
            n_quality = await _tag_quality_factors(db, quality_codes)
        review_text = "样本外与回测均达标，模型已入库；{} 个优质因子已打标进入因子库。".format(
            n_quality
        )
        if param_attempts:
            review_text += f"（经 {param_attempts} 次参数调优后达标）"
        diagnosis = {
            "grade": "ok",
            "root_cause": "none",
            "review": review_text,
            "advice": "可在模型库查看该模型，或在因子库按「优质」标签筛选复用这些因子。",
            "keep": picked,
            "drop": [],
            "params": {},
            "checks": checks,
            "param_attempts": param_attempts,
            "params_applied": {k: active_cfg.get(k) for k in applied_param_keys},
        }
        next_pool = picked
        # 更新 best / stall
        if best_score is None or score > best_score:
            await rrun.set_best(
                run_id,
                model_id=model_id,
                metrics={
                    "sharpe": score,
                    "total_return": (bt.get("metrics") or {}).get("total_return"),
                    "oos_rank_ic": train_resp.get("oos_rank_ic"),
                },
            )
            new_stall = 0
        else:
            new_stall = (run.get("stall_count") or 0) + 1
            await rrun.update_status(run_id, status="running", stall_count=new_stall)
        if excellent and th.get("stop_on_excellent", True):
            terminate = True
            succeeded = True
    else:
        # 诊断（调优循环已给出最终诊断时不再重复调用 LLM）
        await mark("finalize", stage_idx=6, message="失败诊断")
        if diagnosis is None:
            diagnosis = await _diagnose(db, active_cfg, train_resp, bt, name_map, grade)
        diagnosis["grade"] = grade
        diagnosis["param_attempts"] = param_attempts
        diagnosis["param_retry_limit"] = param_retry_limit
        diagnosis["params_applied"] = {k: active_cfg.get(k) for k in applied_param_keys}
        diagnosis["val_ok"] = val_ok
        next_pool = diagnosis.get("keep") or []
        if len(next_pool) < 2:
            next_pool = picked or pool
        new_stall = (run.get("stall_count") or 0) + 1
        await rrun.update_status(run_id, status="running", stall_count=new_stall)

    # 写回 factor_pool（保留 keep 供下一轮）
    await rrun.update_status(run_id, status="running", factor_pool=next_pool)

    # 落 round 记录
    await rrepo.update(
        round_id,
        phase="finalize",
        status="done",
        factors_generated=[_factor_detail(c) for c in new_codes],
        factors_picked=[_factor_detail(c) for c in picked],
        train_result=_trim_train(train_resp, active_cfg),
        backtest_summary={
            "metrics": bt.get("metrics"),
            "summary": bt.get("summary"),
            "equity_points": bt.get("equity_points"),
        },
        diagnosis=diagnosis,
        model_id=model_id,
    )
    await _publish(
        rid_str,
        round_no=round_no,
        max_rounds=max_rounds,
        stage_idx=len(_STAGES) - 1,
        phase="finalize",
        status="running",
        message="本轮完成",
        summary={
            "grade": grade,
            "oos_rank_ic": train_resp.get("oos_rank_ic"),
            "val_rank_ic": train_resp.get("val_rank_ic"),
            "sharpe": (bt.get("metrics") or {}).get("sharpe"),
            "excess_return": (bt.get("metrics") or {}).get("excess_return"),
            "param_attempts": param_attempts,
        },
    )
    return {"terminate": terminate, "succeeded": succeeded, "grade": grade}


# =============================================================================
# 各阶段实现
# =============================================================================


# 生成因子时的“探索角度”。每轮每个因子换一个角度，避免同质化——无论用户是否给了研究目标，
# 角度都会注入（用户目标只是主题，不是“只准写同一种表达式”）。
_GEN_ANGLES = (
    "动量/趋势延续：用与其它因子不同的回看周期（短/中/长）",
    "均值回归/超买超卖：乖离率、偏离度、超买超卖后的修复",
    "波动率结构：波动放大或收缩、ATR/已实现波动率的变化",
    "量能/流动性：量比、量价背离、主动买卖不平衡",
    "微观结构/形态：影线、跳空、K 线实体占比、振幅",
    "相对位置/跨周期强弱：相对均线或长周期分位的强弱",
    "尾部/极值：创新高/新低、分位突破、极值聚集度",
    "条件组合：趋势+量能+波动等多条件的组合信号",
)


def _build_gen_prompt(
    base: str, angle: str, negative: str, round_exprs: list[str]
) -> str:
    """拼装因子生成提示词：主题（用户目标或随机灵感）+ 指定角度 + 本轮互斥 + 上一轮负反馈。

    关键点是「角度」与「本轮已生成表达式」会被显式注入，强制模型换思路、不要与同轮其它因子重复。
    """
    parts: list[str] = []
    if base.strip():
        parts.append(base.strip())
    parts.append(f"请从「{angle}」这一角度切入，实现方式要明显区别于其它角度。")
    if round_exprs:
        recent = "；".join(e[:90] for e in round_exprs[-4:])
        parts.append(f"本轮已生成下列因子，务必换不同思路、不得重复或近似：{recent}")
    if negative:
        parts.append(negative)
    return " ".join(parts)


async def _gen_factors(
    db: AsyncSession, cfg: dict[str, Any], prev_diag: dict[str, Any] | None
) -> list[str]:
    from ..agent.service import generate_factor_draft, generate_factor_idea
    from ..factor.repo import UserFactorRepository
    from ..factor.service import join_user_factor

    n = int(cfg.get("factor_count_per_round") or 4)
    objective = (cfg.get("objective") or "").strip()
    model_id = cfg.get("llm_model_id")

    negative = ""
    if prev_diag:
        drops = prev_diag.get("drop") or []
        names = [d.get("name") or d.get("code") for d in drops if d]
        if names:
            negative = "（上一轮以下因子思路已被证伪，请务必换方向，不要重复：{}）".format(
                "、".join(names[:6])
            )

    existing = await UserFactorRepository(db).list()
    existing_expr = {_norm_expr(r.get("expression")) for r in existing}

    new_codes: list[str] = []
    round_exprs: list[str] = []
    # 保底成功数：最多尝试 2n 次（上限 10），达标 n 个即停，避免第 1 轮因失败/去重不足 2 个而废轮。
    target = n
    max_try = max(n, min(n * 2, 10))
    tries = 0
    while len(new_codes) < target and tries < max_try:
        tries += 1
        angle = _GEN_ANGLES[(tries - 1) % len(_GEN_ANGLES)]
        try:
            # 有研究目标 → 以目标为主题（不额外随机灵感）；无目标 → 随机灵感提供主题。
            if objective:
                base = objective
            else:
                idea = await generate_factor_idea(db, model_id=model_id)
                base = idea.get("idea", "")
            prompt = _build_gen_prompt(base, angle, negative, round_exprs)
            draft = await generate_factor_draft(db, prompt, model_id=model_id)
            expr = (draft.get("code") or "").strip()
            if not draft.get("verified") or not expr:
                continue
            ne = _norm_expr(expr)
            if ne in existing_expr:
                continue
            fid = await join_user_factor(
                db,
                name=draft.get("name") or "AI 因子",
                description=draft.get("description") or "",
                expression=expr,
                source_model=draft.get("model"),
                tags=["AI", "pipeline"],
            )
            existing_expr.add(ne)
            round_exprs.append(expr)
            new_codes.append(fid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pipeline.gen_factor_failed", error=str(exc))
            continue
    if len(new_codes) < target:
        logger.info(
            "pipeline.gen_shortfall", target=target, got=len(new_codes), tries=tries
        )
    return new_codes


async def _research_pool(
    db: AsyncSession, cfg: dict[str, Any], codes: list[str]
) -> list[dict[str, Any]]:
    if not codes:
        return []
    from ..factor.service import run_factor_research_batch

    return await run_factor_research_batch(
        db,
        codes=codes,
        symbols=cfg["research_symbols"],
        interval=cfg["research_interval"],
        start=_parse_dt(cfg["research_start"]),
        end=_parse_dt(cfg["research_end"]),
    )


async def _pick(
    db: AsyncSession, cfg: dict[str, Any], research: list[dict[str, Any]]
) -> tuple[list[str], dict[str, Any] | None]:
    from ..agent.service import pick_factors

    usable = [
        r
        for r in research
        if r.get("found") and (r.get("ic") is not None or r.get("rank_ic") is not None)
    ]
    max_pick = int(cfg.get("max_pick") or 6)
    if len(usable) < 2:
        return [r["code"] for r in usable], None
    try:
        result = await pick_factors(
            db,
            usable,
            model_id=cfg.get("llm_model_id"),
            max_pick=min(max_pick, len(usable)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pipeline.pick_failed", error=str(exc))
        return [r["code"] for r in usable[:max_pick]], None
    picks = [p["code"] for p in (result.get("picks") or [])]
    if not picks:
        picks = [r["code"] for r in usable[:max_pick]]
    return picks, result


async def _train(
    db: AsyncSession, cfg: dict[str, Any], codes: list[str]
) -> dict[str, Any]:
    from ..factor.service import train_factor_model_full

    if len(codes) < 2:
        return {
            "found": False,
            "message": "可用因子不足 2 个，无法训练（本轮因子选择问题）",
        }
    return await train_factor_model_full(
        db,
        codes=codes,
        symbols=cfg["research_symbols"],
        interval=cfg["research_interval"],
        start=_parse_dt(cfg["research_start"]),
        end=_parse_dt(cfg["research_end"]),
        horizon=int(cfg.get("horizon") or 1),
        test_ratio=float(cfg.get("test_ratio") or 0.25),
        val_ratio=float(cfg.get("val_ratio") or 0.0),
        alpha=float(cfg.get("alpha") or 1.0),
        max_corr=cfg.get("max_corr"),
        winsor=cfg.get("winsor"),
        feature_norm=cfg.get("feature_norm") or "global",
        cost_bps=float(cfg.get("cost_bps") or 0.0),
        fine_interval=cfg.get("fine_interval"),
        agg_features=cfg.get("agg_features"),
        model=cfg.get("model") or "ridge",
        gbdt_objective=cfg.get("gbdt_objective") or "huber",
        learning_rate=float(cfg.get("learning_rate") or 0.05),
        n_estimators=int(cfg.get("n_estimators") or 600),
        max_depth=int(cfg.get("max_depth") or 3),
        num_leaves=int(cfg.get("num_leaves") or 7),
        min_child_samples=int(cfg.get("min_child_samples") or 20),
        seed_ensemble=int(cfg.get("seed_ensemble") or 3),
        subsample=float(cfg.get("subsample") or 0.9),
        colsample_bytree=float(cfg.get("colsample_bytree") or 0.9),
    )


async def _backtest(
    db: AsyncSession, cfg: dict[str, Any], train_resp: dict[str, Any]
) -> dict[str, Any]:
    if not train_resp.get("found") or "artifact" not in train_resp:
        return {
            "found": False,
            "message": train_resp.get("message") or "训练未产出模型",
        }
    from ..data.repository.kline_repo import KlineRepository

    from .backtest import run_pipeline_backtest

    repo = KlineRepository(db)
    return await run_pipeline_backtest(
        repo,
        symbol=cfg["backtest_symbol"],
        interval=cfg["backtest_interval"],
        start=_parse_dt(cfg["backtest_start"]),
        end=_parse_dt(cfg["backtest_end"]),
        artifact=train_resp["artifact"],
        window=int(cfg.get("bt_window") or 60),
        enter_q=float(cfg.get("bt_enter_q") or 0.8),
        exit_q=float(cfg.get("bt_exit_q") or 0.2),
    )


async def _persist(
    db: AsyncSession,
    cfg: dict[str, Any],
    train_resp: dict[str, Any],
    round_no: int,
    run_name: str,
) -> dict[str, Any]:
    from ..factor.service import persist_model_artifact

    return await persist_model_artifact(
        db,
        name=f"{run_name} · round{round_no}",
        model=train_resp.get("model") or cfg.get("model") or "ridge",
        interval=cfg["research_interval"],
        horizon=int(cfg.get("horizon") or 1),
        symbols=cfg["research_symbols"],
        artifact=train_resp["artifact"],
        resp=train_resp,
        start=_parse_dt(cfg["research_start"]),
        end=_parse_dt(cfg["research_end"]),
        test_ratio=float(cfg.get("test_ratio") or 0.25),
        winsor=cfg.get("winsor"),
        cost_bps=float(cfg.get("cost_bps") or 0.0),
    )


async def _tag_quality_factors(db: AsyncSession, codes: list[str]) -> int:
    from ..factor.service import tag_quality_factors

    return await tag_quality_factors(db, codes, tag="优质")


# =============================================================================
# 评估 / 诊断（纯函数）
# =============================================================================


def _ml_ok(train_resp: dict[str, Any], th: dict[str, Any], *, val: bool = False) -> bool:
    """模型层达标判定。val=True 用验证集指标（调参/因子精筛），否则用测试集指标（最终评估）。"""
    if val:
        rank = train_resp.get("val_rank_ic")
        # 成本感知：优先用净价差（扣 cost_bps），缺失时退回毛价差
        spread = train_resp.get("val_net_spread")
        if spread is None:
            spread = train_resp.get("val_spread")
        hit = train_resp.get("val_hit_rate")
        gap = train_resp.get("val_overfit_gap")
    else:
        rank = train_resp.get("oos_rank_ic")
        spread = train_resp.get("net_spread")
        if spread is None:
            spread = train_resp.get("spread")
        hit = train_resp.get("hit_rate")
        gap = train_resp.get("overfit_gap")
    return bool(
        (rank is not None and float(rank) >= float(th["oos_rank_ic"]))
        and (spread is not None and float(spread) >= float(th.get("spread", 0.0)))
        and (hit is not None and float(hit) >= float(th["hit_rate"]))
        and (gap is None or float(gap) < float(th["overfit_gap"]))
    )


def evaluate_round(
    train_resp: dict[str, Any], bt: dict[str, Any], th: dict[str, Any]
) -> tuple[str, bool, float, dict[str, Any]]:
    ml_ok = _ml_ok(train_resp, th, val=False)
    bm = bt.get("metrics") or {}
    tr = bm.get("total_return")
    excess = bm.get("excess_return")
    sharpe = bm.get("sharpe")
    mdd = bm.get("max_drawdown")
    nt = bm.get("num_trades")
    excess_ok = excess is not None and float(excess) > float(th.get("excess_return", 0.0))
    bt_ok = bool(
        bt.get("found")
        and (tr is not None and float(tr) > float(th["total_return"]))
        and excess_ok
        and (sharpe is not None and float(sharpe) >= float(th["sharpe"]))
        and (mdd is not None and float(mdd) >= float(th["max_drawdown"]))
        and (nt is not None and int(nt) >= int(th["num_trades"]))
    )
    if ml_ok and bt_ok:
        grade = "ok"
    elif ml_ok or bt_ok:
        grade = "warn"
    else:
        grade = "bad"

    excellent = bool(
        grade == "ok"
        and sharpe is not None
        and float(sharpe) >= float(th["excellent_sharpe"])
        and mdd is not None
        and float(mdd) >= float(th["excellent_max_drawdown"])
    )
    score = (
        float(sharpe)
        if (sharpe is not None and bt.get("found"))
        else (float(train_resp.get("oos_rank_ic")) if train_resp.get("oos_rank_ic") is not None else 0.0)
    )
    checks = {"ml_ok": ml_ok, "bt_ok": bt_ok, "excess_ok": excess_ok, "excellent": excellent}
    return grade, excellent, score, checks


def _determine_root_cause(metrics: dict[str, Any], review: dict[str, Any]) -> str:
    if metrics.get("degenerate"):
        return "params"
    gap = metrics.get("overfit_gap")
    if gap is not None and float(gap) >= 0.06:
        return "params"
    rank = metrics.get("rank_ic")
    spread = metrics.get("spread")
    drops = review.get("drop") or []
    if (
        rank is not None
        and spread is not None
        and float(spread) > 0
        and abs(float(rank)) < 0.01
        and not drops
    ):
        return "params"
    return "factor"


def _enrich_diag(charts: dict[str, Any]) -> dict[str, Any]:
    diag = charts or {}
    out = dict(diag)
    deciles = diag.get("deciles") or []
    if len(deciles) >= 2:
        ys = [
            float(d["mean_actual"]) for d in deciles if d.get("mean_actual") is not None
        ]
        if len(ys) >= 2:
            xs = list(range(1, len(ys) + 1))
            if float(np.std(ys)) > 0:
                out["monotonic_r"] = round(float(np.corrcoef(xs, ys)[0, 1]), 5)
    nav = diag.get("nav") or {}
    q5 = nav.get("q5") or []
    q1 = nav.get("q1") or []
    if q5:
        out["q5_end"] = float(q5[-1])
    if q1:
        out["q1_end"] = float(q1[-1])
    return out


def _learning_summary(train_resp: dict[str, Any]) -> dict[str, Any] | None:
    lr = train_resp.get("learning")
    if not lr or not lr.get("oos"):
        return None
    oos = lr["oos"]
    return {
        "rounds": lr.get("rounds"),
        "oos_final": float(oos[-1]),
        "oos_min": float(min(oos)),
        "oos_min_round": int(np.argmin(oos)) + 1,
    }


_TRAIN_CFG_KEYS = (
    "model",
    "gbdt_objective",
    "horizon",
    "test_ratio",
    "val_ratio",
    "alpha",
    "max_corr",
    "winsor",
    "feature_norm",
    "cost_bps",
    "fine_interval",
    "agg_features",
    "learning_rate",
    "n_estimators",
    "max_depth",
    "num_leaves",
    "min_child_samples",
    "seed_ensemble",
    "subsample",
    "colsample_bytree",
)


def _trim_train(
    train_resp: dict[str, Any], cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """把训练结果精简落库（剔除不可 JSON 化的 artifact/booster）。

    cfg 为当轮实际生效的训练配置（含参数调优后的覆盖），落一份 `config` 快照供前端展示
    「训练参数概要」——避免历史轮次只能看到流水线初始配置。
    """
    keep = {
        k: train_resp.get(k)
        for k in (
            "found",
            "model",
            "engine",
            "symbols",
            "interval",
            "horizon",
            "n_train",
            "n_test",
            "n_val",
            "val_ratio",
            "oos_ic",
            "oos_rank_ic",
            "train_ic",
            "overfit_gap",
            "n_features_kept",
            "hit_rate",
            "top_quintile_mean_ret",
            "bottom_quintile_mean_ret",
            "spread",
            "net_spread",
            "pred_nunique",
            "pred_std",
            "degenerate",
            "flip",
            "best_iteration",
            "features",
            "feature_filter",
            "oos_charts",
            "val_ic",
            "val_rank_ic",
            "val_hit_rate",
            "val_spread",
            "val_net_spread",
            "val_overfit_gap",
            "val_degenerate",
            "val_features",
            "val_charts",
            "learning",
            "message",
        )
    }
    out = {k: v for k, v in keep.items() if v is not None}
    if cfg:
        out["config"] = {
            k: cfg.get(k) for k in _TRAIN_CFG_KEYS if cfg.get(k) is not None
        }
    return out


async def _diagnose(
    db: AsyncSession,
    cfg: dict[str, Any],
    train_resp: dict[str, Any],
    bt: dict[str, Any],
    name_map: dict[str, str],
    grade: str,
) -> dict[str, Any]:
    from ..agent.service import review_backtest, review_training

    # 诊断/精筛一律基于「验证集」（无验证集时才退回测试集），避免把测试集信息反馈进下一轮
    has_val = (
        train_resp.get("val_rank_ic") is not None or train_resp.get("val_ic") is not None
    )
    feat_src = train_resp.get("val_features") if has_val else train_resp.get("features")
    features = [f for f in (feat_src or []) if f.get("code")]
    if not features:
        return {
            "grade": grade,
            "root_cause": "factor",
            "review": train_resp.get("message") or "训练未产出可用特征",
            "advice": "换一批因子或扩大样本后再试。",
            "backtest_review": "",
            "keep": [],
            "drop": [],
            "params": {},
            "checks": {},
            "fallback": True,
        }

    metrics = {
        "oos_ic": train_resp.get("val_ic") if has_val else train_resp.get("oos_ic"),
        "oos_rank_ic": (
            train_resp.get("val_rank_ic") if has_val else train_resp.get("oos_rank_ic")
        ),
        "train_ic": train_resp.get("train_ic"),
        "overfit_gap": (
            train_resp.get("val_overfit_gap") if has_val else train_resp.get("overfit_gap")
        ),
        "hit_rate": (
            train_resp.get("val_hit_rate") if has_val else train_resp.get("hit_rate")
        ),
        "spread": train_resp.get("val_spread") if has_val else train_resp.get("spread"),
        "net_spread": (
            train_resp.get("val_net_spread") if has_val else train_resp.get("net_spread")
        ),
    }
    rc_metrics = {
        "degenerate": (
            train_resp.get("val_degenerate") if has_val else train_resp.get("degenerate")
        ),
        "overfit_gap": metrics["overfit_gap"],
        "rank_ic": metrics["oos_rank_ic"],
        "spread": metrics["spread"],
    }
    diag_charts = (
        (train_resp.get("val_charts") if has_val else train_resp.get("oos_charts")) or {}
    )

    payload = {
        "symbol": "|".join(cfg["research_symbols"]),
        "interval": cfg["research_interval"],
        "horizon": cfg.get("horizon") or 1,
        "model": train_resp.get("model"),
        "engine": train_resp.get("engine"),
        "n_train": train_resp.get("n_train"),
        "n_test": train_resp.get("n_val") if has_val else train_resp.get("n_test"),
        "metrics": metrics,
        "features": features,
        "removed": (train_resp.get("feature_filter") or {}).get("removed") or [],
        "oos_charts": _enrich_diag(diag_charts),
        "learning": _learning_summary(train_resp),
        "config": {
            k: cfg.get(k)
            for k in (
                "model",
                "horizon",
                "test_ratio",
                "alpha",
                "max_corr",
                "winsor",
                "cost_bps",
                "fine_interval",
                "agg_features",
                "learning_rate",
                "n_estimators",
    "max_depth",
    "num_leaves",
    "min_child_samples",
    "subsample",
    "colsample_bytree",
)
        },
    }

    try:
        review = await review_training(db, payload, model_id=cfg.get("llm_model_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pipeline.train_review_failed", error=str(exc))
        review = {
            "grade": grade,
            "review": f"训练诊断不可用：{exc}",
            "advice": "建议换因子或调参后重试。",
            "keep": [f["code"] for f in features],
            "drop": [],
            "params": {},
            "fallback": True,
        }

    bt_review_text = ""
    if bt and bt.get("found") and (bt.get("metrics") or {}).get("num_trades", 0) >= 1:
        try:
            bt_payload = {
                "summary": bt.get("summary"),
                "symbols": [cfg["backtest_symbol"]],
                "start_time": cfg["backtest_start"],
                "end_time": cfg["backtest_end"],
                "status": "succeeded",
            }
            bt_review_text = (await review_backtest(db, bt_payload)).get("review") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("pipeline.backtest_review_failed", error=str(exc))

    root = _determine_root_cause(rc_metrics, review)
    drops = [
        {
            "code": d["code"],
            "name": name_map.get(d["code"], d["code"]),
            "reason": d.get("reason", ""),
        }
        for d in (review.get("drop") or [])
        if d.get("code")
    ]
    return {
        "grade": grade,
        "root_cause": root,
        "review": review.get("review") or "",
        "advice": review.get("advice") or "",
        "backtest_review": bt_review_text,
        "keep": review.get("keep") or [f["code"] for f in features],
        "drop": drops,
        "params": review.get("params") or {},
        "fallback": review.get("fallback", False),
    }
