"""
test_feature_filter.py — 驗證 should_skip_for_strategy 三條規則 + fail-open

跑：cd scripts && pytest test_feature_filter.py -q
或：python -m pytest scripts/test_feature_filter.py -q
"""
import os
import sys
import math
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from feature_filter import (
    should_skip_for_strategy,
    load_feature_filter_config,
)


# ── fixture：每個 test 前清掉相關 env，避免互汙染 ────────────────
RELEVANT_ENV = [
    "BACKTEST_USE_FEATURE_FILTERS",
    "SMC_BTC_CORR_MAX",
    "BD_MIN_ADX_MED",
    "MASR_EXCLUDE_ASSET_CLASSES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in RELEVANT_ENV:
        monkeypatch.delenv(k, raising=False)
    yield


# ── 1. SMC：btc_corr_30d ─────────────────────────────────────
def test_smc_high_corr_skip():
    feat = {"btc_corr_30d": 0.85, "asset_class": "crypto_major"}
    skip, reason = should_skip_for_strategy("smc", "BTCUSDT", feat)
    assert skip is True
    assert "btc_corr=0.85" in reason
    assert "0.74" in reason


def test_smc_low_corr_pass():
    feat = {"btc_corr_30d": 0.50, "asset_class": "crypto_alt"}
    skip, reason = should_skip_for_strategy("smc", "DOGEUSDT", feat)
    assert skip is False
    assert reason == "passed"


def test_smc_at_threshold_pass():
    """v == threshold 不 skip（嚴格大於才擋）"""
    feat = {"btc_corr_30d": 0.74}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is False


# ── 2. BD：adx_med ────────────────────────────────────────────
def test_bd_low_adx_skip():
    feat = {"adx_med": 20.0}
    skip, reason = should_skip_for_strategy("bd", "BTCUSDT", feat)
    assert skip is True
    assert "adx_med=20.0" in reason
    assert "28" in reason


def test_bd_high_adx_pass():
    feat = {"adx_med": 35.0}
    skip, reason = should_skip_for_strategy("bd", "ETHUSDT", feat)
    assert skip is False
    assert reason == "passed"


def test_bd_at_threshold_pass():
    """v == 28 不 skip（嚴格小於才擋）"""
    feat = {"adx_med": 28.0}
    skip, _ = should_skip_for_strategy("bd", "X", feat)
    assert skip is False


# ── 3. MASR：asset_class ─────────────────────────────────────
def test_masr_cfd_skip():
    feat = {"asset_class": "cfd"}
    skip, reason = should_skip_for_strategy("masr", "XAUUSDT", feat)
    assert skip is True
    assert "cfd" in reason


def test_masr_crypto_major_pass():
    feat = {"asset_class": "crypto_major"}
    skip, reason = should_skip_for_strategy("masr", "BTCUSDT", feat)
    assert skip is False
    assert reason == "passed"


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


# ── 5. master switch 關閉 → 全 pass ─────────────────────────
def test_master_switch_off_disables_all(monkeypatch):
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "false")
    # 即使是會 skip 的 case，也應 pass
    feat = {"btc_corr_30d": 0.99, "adx_med": 1.0, "asset_class": "cfd"}
    for strat in ["smc", "bd", "masr"]:
        skip, reason = should_skip_for_strategy(strat, "X", feat)
        assert skip is False, f"{strat} 應該 pass when master switch off"
        assert reason == "filters_disabled"


# ── 6. master switch 顯式 true 行為等同 default ─────────────
def test_master_switch_explicit_true(monkeypatch):
    monkeypatch.setenv("BACKTEST_USE_FEATURE_FILTERS", "true")
    feat = {"btc_corr_30d": 0.99}
    skip, _ = should_skip_for_strategy("smc", "X", feat)
    assert skip is True


# ── 7. NaN / 缺欄 → fail-open + reason 標明 ─────────────────
def test_smc_nan_btc_corr_pass():
    feat = {"btc_corr_30d": float("nan")}
    skip, reason = should_skip_for_strategy("smc", "X", feat)
    assert skip is False
    assert "feature_missing:btc_corr_30d" in reason


def test_bd_missing_adx_pass():
    feat = {"asset_class": "meme"}  # 沒 adx_med 欄
    skip, reason = should_skip_for_strategy("bd", "X", feat)
    assert skip is False
    assert "feature_missing:adx_med" in reason


def test_masr_missing_asset_class_pass():
    feat = {"adx_med": 30.0}  # 沒 asset_class
    skip, reason = should_skip_for_strategy("masr", "X", feat)
    assert skip is False
    assert "feature_missing:asset_class" in reason


# ── 8. 不在受控策略 → 不擋 ─────────────────────────────────
@pytest.mark.parametrize("strat", ["mr", "nkf", "granville", "masr_short", ""])
def test_unrelated_strategy_pass(strat):
    feat = {"btc_corr_30d": 0.99, "adx_med": 1.0, "asset_class": "cfd"}
    skip, reason = should_skip_for_strategy(strat, "X", feat)
    assert skip is False
    assert reason == "no_rule_for_strategy"


# ── 9. env override 生效 ──────────────────────────────────────
def test_env_override_smc_threshold(monkeypatch):
    monkeypatch.setenv("SMC_BTC_CORR_MAX", "0.30")
    feat = {"btc_corr_30d": 0.50}
    skip, reason = should_skip_for_strategy("smc", "X", feat)
    assert skip is True
    assert "0.3" in reason  # 0.30 or 0.3 都 OK


def test_env_override_bd_threshold(monkeypatch):
    monkeypatch.setenv("BD_MIN_ADX_MED", "10")
    feat = {"adx_med": 15.0}
    skip, _ = should_skip_for_strategy("bd", "X", feat)
    assert skip is False  # 15 > 10 → pass


def test_env_override_masr_excludes(monkeypatch):
    monkeypatch.setenv("MASR_EXCLUDE_ASSET_CLASSES", "meme,cfd")
    feat = {"asset_class": "meme"}
    skip, reason = should_skip_for_strategy("masr", "X", feat)
    assert skip is True
    assert "meme" in reason


def test_env_override_masr_empty_string_uses_default(monkeypatch):
    """空字串應 fallback 至 default ['cfd']"""
    monkeypatch.setenv("MASR_EXCLUDE_ASSET_CLASSES", "")
    feat = {"asset_class": "cfd"}
    skip, _ = should_skip_for_strategy("masr", "X", feat)
    assert skip is True


# ── 10. load_feature_filter_config 結構 ─────────────────────
def test_config_structure():
    cfg = load_feature_filter_config()
    assert set(cfg.keys()) == {
        "BACKTEST_USE_FEATURE_FILTERS",
        "SMC_BTC_CORR_MAX",
        "BD_MIN_ADX_MED",
        "MASR_EXCLUDE_ASSET_CLASSES",
    }
    assert isinstance(cfg["BACKTEST_USE_FEATURE_FILTERS"], bool)
    assert isinstance(cfg["SMC_BTC_CORR_MAX"], float)
    assert isinstance(cfg["BD_MIN_ADX_MED"], float)
    assert isinstance(cfg["MASR_EXCLUDE_ASSET_CLASSES"], list)
