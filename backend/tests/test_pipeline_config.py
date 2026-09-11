"""流水线配置归一化 / 选优指标回归测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import BadRequest
from app.pipeline.service import _normalize_config, _select_score, _validate_config


def _base_cfg(**over: object) -> dict:
    now = datetime.now(timezone.utc)
    cfg: dict = {
        "research_symbols": ["BTCUSDT"],
        "research_interval": "1h",
        "research_start": now - timedelta(days=400),
        "research_end": now - timedelta(days=120),
        "backtest_start": now - timedelta(days=120),
        "backtest_end": now - timedelta(days=10),
        "max_rounds": 3,
    }
    cfg.update(over)
    return cfg


def test_backtest_interval_forced_to_research_interval():
    cfg = _normalize_config(_base_cfg(backtest_interval="1d"))
    assert cfg["research_interval"] == "1h"
    assert cfg["backtest_interval"] == "1h"


def test_max_corr_defaults_enabled():
    cfg = _normalize_config(_base_cfg())
    assert cfg["max_corr"] == 0.8


def test_max_corr_explicit_disable_preserved():
    cfg = _normalize_config(_base_cfg(max_corr=1.0))
    assert cfg["max_corr"] == 1.0


def test_validate_requires_research_interval():
    with pytest.raises(BadRequest):
        _validate_config(_base_cfg(research_interval=""))


def test_select_score_prefers_validation():
    assert _select_score({"val_rank_ic": 0.05, "oos_rank_ic": 0.01}) == 0.05


def test_select_score_falls_back_to_oos():
    assert _select_score({"val_rank_ic": None, "oos_rank_ic": 0.03}) == 0.03


def test_select_score_none_when_missing():
    assert _select_score({}) is None
