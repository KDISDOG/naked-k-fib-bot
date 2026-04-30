"""
test_feature_filter.py — 驗證 should_skip_for_strategy 規則 + fail-open + 抽象化

跑：python -m pytest scripts/test_feature_filter.py -q
"""
import os
import sys
import json
import math
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from feature_filter import (
    should_skip_for_strategy,
    load_feature_filter_config,
)


RELEVANT_ENV = [
    "BACKTEST_USE_FEATURE_FILTERS",
    "SMC_BTC_CORR_MAX", "SMC_RULES_JSON", "SMC_REQUIRE_ALL",
    "BD_MIN_ADX_MED", "BD_RULES_JSON", "BD_REQUIRE_ALL",
    "MASR_EXCLUDE_ASSET_CLASSES", "MASR_RULES_JSON", "MASR_REQUIRE_ALL",
    "NKF_RULES_JSON", "NKF_REQUIRE_ALL",
    "MR_RULES_JSON", "MR_REQUIRE_ALL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in RELEVANT_ENV:
        monkeypatch.delenv(k, raising=False)
    yield


# ═══════════════════════════════════════════════════════════════
# Regression：P1 既有行為（SMC/BD/MASR）必須完全保留
# ═══════════════════════════════════════════════════════════════

# ── 1. SMC：btc_corr_30d ─────────────────────────────────────
def test_smc_high_corr_skip():
    feat = {"btc_corr_30d": 0.85, "asset_class": "crypto_major"}
    skip, reason = should_skip_for_strategy("smc", "BTCUSDT", feat)
    assert skip is True


def test_smc_low_corr_pass():
    feat = {"btc_corr_30d": 0.50, "asset_class": "crypto_alt"}
    skip, reason = should_skip_for_strategy("smc", "DOGEUSDT", feat)
    assert skip is False


def test_smc_at_threshold_pass():
    """0.74 是 op '<=' 閾值；P1 用 '>' 也會 pass at 0.74，新版用 '<=' rule 保持一致"""
    feat = {"btc_corr_30d": 0.74}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is False


# ── 2. BD：adx_med ────────────────────────────────────────────
def test_bd_low_adx_skip():
    feat = {"adx_med": 20.0}
    skip, reason = should_skip_for_strategy("bd", "BTCUSDT", feat)
    assert skip is True


def test_bd_high_adx_pass():
    feat = {"adx_med": 35.0}
    skip, _ = should_skip_for_strategy("bd", "ETHUSDT", feat)
    assert skip is False


def test_bd_at_threshold_pass():
    feat = {"adx_med": 28.0}
    skip, _ = should_skip_for_strategy("bd", "X", feat)
    assert skip is False


# ── 3. MASR：asset_class ─────────────────────────────────────
def test_masr_cfd_skip():
    feat = {"asset_class": "cfd"}
    skip, reason = should_skip_for_strategy("masr", "XAUUSDT", feat)
    assert skip is True


def test_masr_crypto_major_pass():
    feat = {"asset_class": "crypto_major"}
    skip, _ = should_skip_for_strategy("masr", "BTCUSDT", feat)
    assert skip is False


def test_masr_meme_pass():
    feat = {"asset_class": "meme"}
    skip, _ = should_skip_for_strategy("masr", "DOGEUSDT", feat)
    assert skip is False


def test_masr_alt_pass():
    feat = {"asset_class": "crypto_alt"}
    skip, _ = should_skip_for_strategy("masr", "SOLUSDT", feat)
    assert skip is False


# ── 4. fail-open：features=None ─────────────────────────────
@pytest.mark.parametrize("strat", ["smc", "bd", "masr", "mr", "nkf", "unknown"])
def test_features_none_pass(strat):
    skip, reason = should_skip_for_strategy(strat, "X", None)
    assert skip is False
    assert reason == "no_features_skip_filter"


# ── 5. master switch ─────────────────────────────────────────
def test_master_switch_off_disables_all(monkeypatch):
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "false")
    feat = {"btc_corr_30d": 0.99, "adx_med": 1.0, "asset_class": "cfd"}
    for strat in ["smc", "bd", "masr"]:
        skip, reason = should_skip_for_strategy(strat, "X", feat)
        assert skip is False
        assert reason == "filters_disabled"


def test_master_switch_explicit_true(monkeypatch):
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "true")
    feat = {"btc_corr_30d": 0.99}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is True


# ── 6. NaN / 缺欄 → fail-open ─────────────────────────────
def test_smc_nan_btc_corr_pass():
    feat = {"btc_corr_30d": float("nan")}
    skip, reason = should_skip_for_strategy("smc", "X", feat)
    assert skip is False


def test_bd_missing_adx_pass():
    feat = {"asset_class": "meme"}
    skip, _ = should_skip_for_strategy("bd", "X", feat)
    assert skip is False


def test_masr_missing_asset_class_pass():
    feat = {"adx_med": 30.0}
    skip, _ = should_skip_for_strategy("masr", "X", feat)
    assert skip is False


# ── 7. 不在受控策略 → 不擋 ─────────────────────────────────
@pytest.mark.parametrize("strat", ["granville", "masr_short", "unknown"])
def test_unrelated_strategy_pass(strat):
    feat = {"btc_corr_30d": 0.99}
    skip, reason = should_skip_for_strategy(strat, "X", feat)
    assert skip is False
    assert reason == "no_rule_for_strategy"


# ── 8. P1 legacy env override 仍生效（向後相容）──────────────
def test_env_override_smc_threshold(monkeypatch):
    monkeypatch.setenv("SMC_BTC_CORR_MAX", "0.30")
    feat = {"btc_corr_30d": 0.50}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is True


def test_env_override_bd_threshold(monkeypatch):
    monkeypatch.setenv("BD_MIN_ADX_MED", "10")
    feat = {"adx_med": 15.0}
    skip, _ = should_skip_for_strategy("bd", "X", feat)
    assert skip is False


def test_env_override_masr_excludes(monkeypatch):
    monkeypatch.setenv("MASR_EXCLUDE_ASSET_CLASSES", "meme,cfd")
    feat = {"asset_class": "meme"}
    skip, _ = should_skip_for_strategy("masr", "X", feat)
    assert skip is True


# ═══════════════════════════════════════════════════════════════
# 新增：抽象化 rule list (P2B-1)
# ═══════════════════════════════════════════════════════════════

# ── 9. NKF 預設無 rule（不 filter）──────────────────────────
def test_nkf_default_no_rules():
    feat = {"adx_med": 5.0, "btc_corr_30d": 0.99, "asset_class": "cfd"}
    skip, reason = should_skip_for_strategy("nkf", "X", feat)
    assert skip is False
    assert reason == "no_rules"


def test_mr_default_no_rules():
    feat = {"adx_med": 5.0}
    skip, reason = should_skip_for_strategy("mr", "X", feat)
    assert skip is False
    assert reason == "no_rules"


# ── 10. NKF 雙條件 AND（require_all=true）─────────────────
def test_nkf_and_both_pass(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "true")
    feat = {"atr_pct_med": 0.06, "adx_med": 30}
    skip, _ = should_skip_for_strategy("nkf", "X", feat)
    assert skip is False


def test_nkf_and_one_fail(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "true")
    feat = {"atr_pct_med": 0.06, "adx_med": 20}  # adx 不過
    skip, reason = should_skip_for_strategy("nkf", "X", feat)
    assert skip is True
    assert "adx_med" in reason


def test_nkf_and_both_fail(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "true")
    feat = {"atr_pct_med": 0.01, "adx_med": 5}
    skip, _ = should_skip_for_strategy("nkf", "X", feat)
    assert skip is True


# ── 11. NKF 雙條件 OR（require_all=false）──────────────────
def test_nkf_or_both_pass(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "false")
    feat = {"atr_pct_med": 0.06, "adx_med": 30}
    skip, _ = should_skip_for_strategy("nkf", "X", feat)
    assert skip is False


def test_nkf_or_one_pass(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "false")
    feat = {"atr_pct_med": 0.01, "adx_med": 30}
    skip, reason = should_skip_for_strategy("nkf", "X", feat)
    assert skip is False
    assert reason == "passed_any"


def test_nkf_or_all_fail(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 0.05},
        {"feature": "adx_med", "op": ">=", "threshold": 25},
    ]))
    monkeypatch.setenv("NKF_REQUIRE_ALL", "false")
    feat = {"atr_pct_med": 0.01, "adx_med": 5}
    skip, _ = should_skip_for_strategy("nkf", "X", feat)
    assert skip is True


# ── 12. MR 同樣兩種模式 ───────────────────────────────────
def test_mr_and_pass(monkeypatch):
    monkeypatch.setenv("MR_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 7.5},
    ]))
    feat = {"atr_pct_med": 8.0}
    skip, _ = should_skip_for_strategy("mr", "X", feat)
    assert skip is False


def test_mr_and_fail(monkeypatch):
    monkeypatch.setenv("MR_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 7.5},
    ]))
    feat = {"atr_pct_med": 5.0}
    skip, _ = should_skip_for_strategy("mr", "X", feat)
    assert skip is True


def test_mr_or_with_two_rules(monkeypatch):
    monkeypatch.setenv("MR_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 7.5},
        {"feature": "btc_corr_30d", "op": "<=", "threshold": 0.5},
    ]))
    monkeypatch.setenv("MR_REQUIRE_ALL", "false")
    feat = {"atr_pct_med": 5.0, "btc_corr_30d": 0.3}  # 第二條過
    skip, _ = should_skip_for_strategy("mr", "X", feat)
    assert skip is False


# ── 13. JSON override SMC（新方式）─────────────────────────
def test_smc_rules_json_override(monkeypatch):
    monkeypatch.setenv("SMC_RULES_JSON", json.dumps([
        {"feature": "btc_corr_30d", "op": "<=", "threshold": 0.50},
    ]))
    feat = {"btc_corr_30d": 0.60}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is True


def test_smc_rules_json_overrides_legacy(monkeypatch):
    """同時設 JSON 和 legacy → JSON 優先"""
    monkeypatch.setenv("SMC_BTC_CORR_MAX", "0.95")
    monkeypatch.setenv("SMC_RULES_JSON", json.dumps([
        {"feature": "btc_corr_30d", "op": "<=", "threshold": 0.50},
    ]))
    feat = {"btc_corr_30d": 0.60}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is True


# ── 14. JSON parse 錯誤 → fallback default ─────────────────
def test_invalid_rules_json_falls_back(monkeypatch):
    monkeypatch.setenv("NKF_RULES_JSON", "not-valid-json")
    feat = {"atr_pct_med": 0.06}
    # default NKF_RULES = []，所以無 rule → not skip
    skip, reason = should_skip_for_strategy("nkf", "X", feat)
    assert skip is False


def test_rules_json_wrong_schema(monkeypatch):
    """rule 缺欄 → fallback default (空 list)"""
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "x"},  # 缺 op/threshold
    ]))
    skip, reason = should_skip_for_strategy("nkf", "X", {"x": 1})
    assert skip is False
    assert reason == "no_rules"


# ── 15. 各種 op 都能用 ────────────────────────────────────
@pytest.mark.parametrize("op,thr,v,expected_skip", [
    (">", 5, 6, False),
    (">", 5, 5, True),
    ("<", 5, 4, False),
    ("<", 5, 5, True),
    ("==", 5, 5, False),
    ("==", 5, 6, True),
    ("!=", 5, 6, False),
    ("!=", 5, 5, True),
    ("in", [1, 2, 3], 2, False),
    ("in", [1, 2, 3], 4, True),
    ("not_in", [1, 2, 3], 4, False),
    ("not_in", [1, 2, 3], 2, True),
])
def test_all_ops(monkeypatch, op, thr, v, expected_skip):
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "x", "op": op, "threshold": thr},
    ]))
    feat = {"x": v}
    skip, _ = should_skip_for_strategy("nkf", "T", feat)
    assert skip is expected_skip, f"op={op} thr={thr} v={v}"


# ── 16. config 結構完整性 ─────────────────────────────────
def test_config_structure():
    cfg = load_feature_filter_config()
    expected_keys = {
        "BACKTEST_USE_FEATURE_FILTERS",
        "SMC_RULES", "SMC_REQUIRE_ALL",
        "BD_RULES", "BD_REQUIRE_ALL",
        "MASR_RULES", "MASR_REQUIRE_ALL",
        "NKF_RULES", "NKF_REQUIRE_ALL",
        "MR_RULES", "MR_REQUIRE_ALL",
    }
    assert expected_keys <= set(cfg.keys())
    # P1 default 等價
    assert cfg["NKF_RULES"] == []
    assert cfg["MR_RULES"] == []
    assert cfg["SMC_RULES"][0]["feature"] == "btc_corr_30d"
    assert cfg["BD_RULES"][0]["feature"] == "adx_med"
    assert cfg["MASR_RULES"][0]["feature"] == "asset_class"


# ── 17. master switch off 也擋 NKF/MR ────────────────────
def test_master_switch_off_disables_nkf(monkeypatch):
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "false")
    monkeypatch.setenv("NKF_RULES_JSON", json.dumps([
        {"feature": "atr_pct_med", "op": ">=", "threshold": 99},
    ]))
    skip, reason = should_skip_for_strategy("nkf", "X", {"atr_pct_med": 0.01})
    assert skip is False
    assert reason == "filters_disabled"
