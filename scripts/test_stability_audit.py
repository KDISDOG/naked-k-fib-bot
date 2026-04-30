"""
test_stability_audit.py — 驗證 audit_candidate_stability 兩個 mode 的行為

跑：python -m pytest scripts/test_stability_audit.py -q
"""
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from stability_audit import (
    audit_candidate_stability,
    compute_stability_metrics,
    classify_status,
)


# ── compute_stability_metrics ─────────────────────────────────
def _seg(n, wr, pnl):
    return {"metrics": {"n_trades": n, "win_rate": wr, "total_pnl": pnl}}


def test_metrics_all_positive():
    m = compute_stability_metrics([_seg(40, 0.5, 5.0), _seg(50, 0.5, 6.0), _seg(40, 0.5, 7.0)])
    assert m["all_positive"] is True
    assert m["n_negative"] == 0
    assert m["min_n_trades"] == 40


def test_metrics_one_negative():
    m = compute_stability_metrics([_seg(30, 0.4, -3.0), _seg(40, 0.5, 5.0), _seg(40, 0.5, 4.0)])
    assert m["n_negative"] == 1
    assert m["sign_flip_count"] == 1


def test_metrics_concentration():
    m = compute_stability_metrics([_seg(10, 0.4, 1.0), _seg(40, 0.5, 9.0), _seg(10, 0.4, 0.5)])
    # max=9, positive_total=10.5, conc=9/10.5 ≈ 0.857
    assert m["concentration"] == pytest.approx(0.857, abs=0.01)


# ── classify_status ───────────────────────────────────────────
def test_classify_robust():
    m = compute_stability_metrics([_seg(40, 0.50, 3.0), _seg(40, 0.51, 3.0), _seg(40, 0.49, 3.0)])
    status, _ = classify_status(m)
    assert status == "ROBUST"


def test_classify_rejected_two_neg():
    m = compute_stability_metrics([_seg(40, 0.30, -5.0), _seg(40, 0.50, 3.0), _seg(40, 0.30, -5.0)])
    status, _ = classify_status(m)
    assert status == "REJECTED"


def test_classify_rejected_high_wr_std():
    # 三段全正但 wr_std > 10pp
    m = compute_stability_metrics([_seg(40, 0.30, 1.0), _seg(40, 0.50, 1.0), _seg(40, 0.65, 1.0)])
    status, _ = classify_status(m)
    assert status == "REJECTED"


def test_classify_overfit_suspect():
    # 一段大正、其他平 + 高集中
    m = compute_stability_metrics([_seg(40, 0.50, 0.5), _seg(40, 0.55, 10.0), _seg(40, 0.48, -2.0)])
    status, _ = classify_status(m)
    # 1 negative + concentration > 70% (10 / 10.5 = 0.95)
    assert status == "OVERFIT_SUSPECT"


def test_classify_stable_but_thin():
    # 三段全正但 min_n=10 < 30
    m = compute_stability_metrics([_seg(10, 0.50, 1.0), _seg(40, 0.51, 1.0), _seg(40, 0.49, 1.0)])
    status, _ = classify_status(m)
    assert status == "STABLE_BUT_THIN"


# ── mode parameter ───────────────────────────────────────────
def test_mode_invalid_raises():
    with pytest.raises(ValueError, match="unknown mode"):
        audit_candidate_stability(
            "masr", [], "AND", client=None, symbols=[],
            fn=lambda *a, **kw: [], mode="bogus",
        )


def test_mode_config_override_requires_dict():
    """config_override mode 但沒 config_overrides → ValueError"""
    with patch("stability_audit.run_walk_forward") as mock_wf:
        mock_wf.return_value = {
            "segments": [{"metrics": {"n_trades": 0, "win_rate": 0, "total_pnl": 0},
                           "trades": []}] * 3,
            "by_coin": {},
            "_pickle_path": "/tmp/nonexistent.pkl",
        }
        with pytest.raises(ValueError, match="config_overrides"):
            audit_candidate_stability(
                "masr", [], "AND", client=None, symbols=["BTCUSDT"],
                fn=lambda *a, **kw: [], months=39, n_segments=3,
                mode="config_override", config_overrides=None,
            )


def test_mode_config_override_passes_dict_to_wf():
    """config_override mode → run_walk_forward 收到 config_overrides kwarg。"""
    captured = {}

    def fake_wf(fn, client, symbols, months, **kwargs):
        captured.update(kwargs)
        return {
            "segments": [{"metrics": {"n_trades": 30, "win_rate": 0.5, "total_pnl": 1.0},
                           "trades": []}] * 3,
            "by_coin": {},
            "_pickle_path": "/tmp/fake.pkl",
        }

    with patch("stability_audit.run_walk_forward", side_effect=fake_wf):
        result = audit_candidate_stability(
            "masr", [], "AND", client=None, symbols=["BTCUSDT"],
            fn=lambda *a, **kw: [], months=39, n_segments=3,
            mode="config_override",
            config_overrides={"MASR_TP1_RR": 2.5, "MASR_SL_ATR_MULT": 1.5},
            candidate_id="cfg_test", candidate_label="test override",
            output_dir=Path("/tmp"),
        )
    assert "config_overrides" in captured
    assert captured["config_overrides"] == {"MASR_TP1_RR": 2.5, "MASR_SL_ATR_MULT": 1.5}
    assert result["mode"] == "config_override"
    assert result["config_overrides"] == {"MASR_TP1_RR": 2.5, "MASR_SL_ATR_MULT": 1.5}


def test_mode_filter_does_not_pass_overrides_to_wf(monkeypatch):
    """filter mode → wf 不應收到 config_overrides。"""
    captured = {}

    def fake_wf(fn, client, symbols, months, **kwargs):
        captured.update(kwargs)
        return {
            "segments": [{"metrics": {"n_trades": 30, "win_rate": 0.5, "total_pnl": 1.0},
                           "trades": []}] * 3,
            "by_coin": {},
            "_pickle_path": "/tmp/fake.pkl",
        }

    monkeypatch.delenv("BACKTEST_USE_FEATURE_FILTERS", raising=False)
    monkeypatch.delenv("MASR_RULES_JSON", raising=False)
    with patch("stability_audit.run_walk_forward", side_effect=fake_wf):
        audit_candidate_stability(
            "masr",
            [{"feature": "asset_class", "op": "not_in", "threshold": ["cfd"]}],
            "AND", client=None, symbols=["BTCUSDT"],
            fn=lambda *a, **kw: [], months=39, n_segments=3,
            mode="filter",
            candidate_id="filter_test", candidate_label="test filter",
            output_dir=Path("/tmp"),
        )
    assert "config_overrides" not in captured


def test_mode_config_override_disables_filter_env(monkeypatch):
    """config_override mode 必須強制關 filter（避免雙重變因）。"""
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "true")
    monkeypatch.setenv("MASR_RULES_JSON", '[{"feature":"x","op":"==","threshold":"y"}]')

    seen_env = {}

    def fake_wf(fn, client, symbols, months, **kwargs):
        seen_env["BACKTEST_USE_FEATURE_FILTERS"] = os.environ.get("BACKTEST_USE_FEATURE_FILTERS")
        seen_env["MASR_RULES_JSON"] = os.environ.get("MASR_RULES_JSON")
        return {
            "segments": [{"metrics": {"n_trades": 30, "win_rate": 0.5, "total_pnl": 1.0},
                           "trades": []}] * 3,
            "by_coin": {},
            "_pickle_path": "/tmp/fake.pkl",
        }

    with patch("stability_audit.run_walk_forward", side_effect=fake_wf):
        audit_candidate_stability(
            "masr", [], "AND", client=None, symbols=["BTCUSDT"],
            fn=lambda *a, **kw: [], months=39, n_segments=3,
            mode="config_override",
            config_overrides={"MASR_TP1_RR": 2.5},
            candidate_id="cfg_disable_filter", output_dir=Path("/tmp"),
        )
    # 在 wf 被呼叫的時刻，filter 環境變數應該被關掉
    assert seen_env["BACKTEST_USE_FEATURE_FILTERS"] == "false"
    assert seen_env["MASR_RULES_JSON"] is None

    # restore：呼叫返回後，原 env 應該還原
    assert os.environ.get("BACKTEST_USE_FEATURE_FILTERS") == "true"
    assert os.environ.get("MASR_RULES_JSON") == '[{"feature":"x","op":"==","threshold":"y"}]'
