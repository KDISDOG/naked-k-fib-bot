"""
feature_filter.py — backtest-only feature filter（純函式）

集中管理三條從 pattern_mining 推得的篩選規則：
  - SMC：跳過高 BTC 相關幣（low_corr +5.54U vs high_corr -2.02U）
  - BD：跳過弱趨勢幣（high_adx +12.10U vs low_adx -8.13U）
  - MASR：跳過 cfd 類資產（crypto_major +27U vs cfd +0.4U）

設計原則（與 SKILL.md 對齊）：
  - 純函式 module，不依賴 Config class（避免污染 live 路徑）
  - 從 .env 讀，提供安全 default
  - master switch BACKTEST_USE_FEATURE_FILTERS 預設 True；False 等於整層關掉
  - features=None → fail-open，回 (False, "no_features_skip_filter")

僅供 scripts/backtest.py 內 run_backtest_{masr,bd,smc} 使用。
不要在 live (bot_main / strategies/*) 引入。
"""
import os
import logging
from typing import Optional

log = logging.getLogger("feature_filter")

# Default 值（與 .env.example 對齊；env 不存在時 fallback）
_DEFAULTS = {
    "BACKTEST_USE_FEATURE_FILTERS": True,
    "SMC_BTC_CORR_MAX": 0.74,
    "BD_MIN_ADX_MED": 28.0,
    "MASR_EXCLUDE_ASSET_CLASSES": ["cfd"],
}


def _parse_bool(v: Optional[str], default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "t", "y")


def _parse_float(v: Optional[str], default: float) -> float:
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        log.warning(f"[feature_filter] 無法 parse 為 float：{v!r}，用 default {default}")
        return default


def _parse_csv(v: Optional[str], default: list[str]) -> list[str]:
    if v is None or str(v).strip() == "":
        return list(default)
    return [s.strip() for s in v.split(",") if s.strip()]


def load_feature_filter_config() -> dict:
    """從環境變數讀 filter config，回傳 dict。
    每次呼叫都重讀（pytest 設 env 才能立即生效；backtest 進場各跑一次成本可忽略）。
    """
    return {
        "BACKTEST_USE_FEATURE_FILTERS": _parse_bool(
            os.getenv("BACKTEST_USE_FEATURE_FILTERS"),
            _DEFAULTS["BACKTEST_USE_FEATURE_FILTERS"],
        ),
        "SMC_BTC_CORR_MAX": _parse_float(
            os.getenv("SMC_BTC_CORR_MAX"), _DEFAULTS["SMC_BTC_CORR_MAX"],
        ),
        "BD_MIN_ADX_MED": _parse_float(
            os.getenv("BD_MIN_ADX_MED"), _DEFAULTS["BD_MIN_ADX_MED"],
        ),
        "MASR_EXCLUDE_ASSET_CLASSES": _parse_csv(
            os.getenv("MASR_EXCLUDE_ASSET_CLASSES"),
            _DEFAULTS["MASR_EXCLUDE_ASSET_CLASSES"],
        ),
    }


def should_skip_for_strategy(
    strategy: str,
    symbol: str,
    features: Optional[dict],
) -> tuple[bool, str]:
    """
    回傳 (skip, reason)。
    skip=True 表示該 (strategy, symbol) 不該進回測。
    reason 給 log 用，永遠是非空字串。

    fail-open 規則：
      - master switch 關閉 → (False, "filters_disabled")
      - features=None       → (False, "no_features_skip_filter")
      - 對應特徵 NaN/缺欄    → (False, "feature_missing:<key>")
      - 不在三個受控策略內   → (False, "no_rule_for_strategy")
    """
    cfg = load_feature_filter_config()

    if not cfg["BACKTEST_USE_FEATURE_FILTERS"]:
        return False, "filters_disabled"

    if features is None:
        log.info(f"[feature_filter] {strategy}/{symbol} fail-open: no features")
        return False, "no_features_skip_filter"

    s = strategy.lower()

    if s == "smc":
        v = features.get("btc_corr_30d")
        if v is None or _is_nan(v):
            return False, "feature_missing:btc_corr_30d"
        thr = cfg["SMC_BTC_CORR_MAX"]
        if v > thr:
            return True, f"btc_corr={v:.2f} > {thr}"
        return False, "passed"

    if s == "bd":
        v = features.get("adx_med")
        if v is None or _is_nan(v):
            return False, "feature_missing:adx_med"
        thr = cfg["BD_MIN_ADX_MED"]
        if v < thr:
            return True, f"adx_med={v:.1f} < {thr}"
        return False, "passed"

    if s == "masr":
        v = features.get("asset_class")
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return False, "feature_missing:asset_class"
        excl = cfg["MASR_EXCLUDE_ASSET_CLASSES"]
        if v in excl:
            return True, f"asset_class={v} in excluded {excl}"
        return False, "passed"

    return False, "no_rule_for_strategy"


def _is_nan(v) -> bool:
    """同時相容 float NaN 與 numpy.nan / pandas.NA。"""
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:
        pass
    try:
        import pandas as pd
        if pd.isna(v):
            return True
    except Exception:
        pass
    return False
