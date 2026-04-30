"""
feature_filter.py — backtest-only feature filter（純函式）

設計原則（與 SKILL.md 對齊）：
  - 純函式 module，不依賴 Config class（避免污染 live 路徑）
  - 從 .env 讀，提供安全 default
  - master switch BACKTEST_USE_FEATURE_FILTERS 預設 True；False 等於整層關掉
  - features=None → fail-open，回 (False, "no_features_skip_filter")

P2B-1 抽象化後，所有策略 rule 統一走 rule list 形式：
  rules: list[dict]，每 dict 為 {feature, op, threshold}
    - op ∈ {">=", "<=", ">", "<", "==", "!=", "in", "not_in"}
    - threshold 對 in/not_in 是 list；對其他 op 是 scalar
  邏輯由 <STRATEGY>_REQUIRE_ALL 控制（true=AND，false=OR）

策略 rule 來源：
  SMC/BD/MASR：保留向後相容的 hardcoded default（與 P1 行為等價），
                env override 可用 SMC_RULES_JSON 等覆蓋。
  NKF/MR：     預設空 list（不 filter），需 env 顯式給 NKF_RULES_JSON / MR_RULES_JSON。

僅供 scripts/backtest.py 內 run_backtest_{masr,bd,smc,nkf,mr} 使用。
不要在 live (bot_main / strategies/*) 引入。
"""
import os
import json
import logging
from typing import Optional, Iterable

log = logging.getLogger("feature_filter")

# ── 純名稱判定 asset_class（不依賴 cache，給 live 路徑用）──────────
# P10 phase 2：live screen_coins 走 startswith prefix 判定，避免依賴
# coin_features pickle（39m 歷史，新幣不在裡面 → 無法 lookup）
# coin_features.classify_asset 也有類似函式，但用 base==exact 比對；
# 這裡用 startswith 涵蓋變種（如 XAUTUSDT 也視為 cfd-related）。
_CFD_PREFIXES = ("XAU", "XAG", "CL", "CO", "NG")
_MEME_PREFIXES = ("DOGE", "1000PEPE", "1000SHIB", "FLOKI", "BONK", "PEPE", "SHIB")
_MAJOR_PREFIXES = ("BTC", "ETH")


def classify_asset(symbol: str) -> str:
    """純名稱判定 asset_class，純函式（給 live screen_coins 用）。
    回傳 ∈ {"cfd", "meme", "crypto_major", "crypto_alt", "unknown"}。
    fail-open semantics：無法判定 → "unknown"，caller 用 `in [excluded]` 判斷，
    "unknown" 不會在 list 內 → 自動 pass。
    """
    if not symbol:
        return "unknown"
    s = symbol.upper()
    for p in _CFD_PREFIXES:
        if s.startswith(p):
            return "cfd"
    for p in _MEME_PREFIXES:
        if s.startswith(p):
            return "meme"
    for p in _MAJOR_PREFIXES:
        if s.startswith(p):
            return "crypto_major"
    if s.endswith("USDT"):
        return "crypto_alt"
    return "unknown"

# ── 預設值與 P1 hardcoded rules（向後相容）────────────────
_DEFAULTS = {
    "BACKTEST_USE_FEATURE_FILTERS": True,
    # SMC/BD/MASR：P1 等價的單條 rule
    "SMC_RULES": [
        {"feature": "btc_corr_30d", "op": "<=", "threshold": 0.74},
    ],
    "SMC_REQUIRE_ALL": True,
    "BD_RULES": [
        {"feature": "adx_med", "op": ">=", "threshold": 28.0},
    ],
    "BD_REQUIRE_ALL": True,
    "MASR_RULES": [
        {"feature": "asset_class", "op": "not_in", "threshold": ["cfd"]},
    ],
    "MASR_REQUIRE_ALL": True,
    "MASR_EXCLUDE_ASSET_CLASSES": ["cfd"],  # 衍生欄位 default
    # NKF/MR：預設不 filter
    "NKF_RULES": [],
    "NKF_REQUIRE_ALL": True,
    "MR_RULES": [],
    "MR_REQUIRE_ALL": True,
}

# 維持 P1 相容性的單值 env：若使用者設了這些，要轉換成 rule
_LEGACY_ENV_MAPPING = {
    "SMC_BTC_CORR_MAX": ("smc", "btc_corr_30d", "<="),
    "BD_MIN_ADX_MED":   ("bd", "adx_med", ">="),
    # MASR_EXCLUDE_ASSET_CLASSES 是 list，獨立處理
}


# ── parsers ─────────────────────────────────────────────────────
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


def _parse_rules_json(v: Optional[str], default: list[dict]) -> list[dict]:
    """Parse JSON string into list of rule dicts. 失敗 → fallback default。"""
    if v is None or str(v).strip() == "":
        return list(default)
    try:
        parsed = json.loads(v)
        if not isinstance(parsed, list):
            log.warning(f"[feature_filter] RULES_JSON 不是 list：{v!r}，fallback default")
            return list(default)
        # 簡單 schema 驗證
        for r in parsed:
            if not isinstance(r, dict):
                log.warning(f"[feature_filter] rule 不是 dict：{r!r}，fallback default")
                return list(default)
            if not {"feature", "op", "threshold"} <= set(r.keys()):
                log.warning(f"[feature_filter] rule 缺欄：{r!r}，fallback default")
                return list(default)
        return parsed
    except json.JSONDecodeError as e:
        log.warning(f"[feature_filter] RULES_JSON parse 失敗：{e}，fallback default")
        return list(default)


# ── config 載入 ────────────────────────────────────────────────
def load_feature_filter_config() -> dict:
    """從環境變數讀 filter config。每次呼叫都重讀（pytest 設 env 才能立即生效）。
    向後相容：若使用者只設了 P1 風格的單值 env（SMC_BTC_CORR_MAX 等），
                會被轉成對應 rule list；新的 SMC_RULES_JSON 會 override。
    """
    cfg = {
        "BACKTEST_USE_FEATURE_FILTERS": _parse_bool(
            os.getenv("BACKTEST_USE_FEATURE_FILTERS"),
            _DEFAULTS["BACKTEST_USE_FEATURE_FILTERS"],
        ),
    }

    # SMC
    smc_rules_json = os.getenv("SMC_RULES_JSON")
    if smc_rules_json:
        cfg["SMC_RULES"] = _parse_rules_json(smc_rules_json, _DEFAULTS["SMC_RULES"])
    else:
        # 對應 P1 legacy env
        legacy = os.getenv("SMC_BTC_CORR_MAX")
        if legacy is not None:
            thr = _parse_float(legacy, 0.74)
            cfg["SMC_RULES"] = [{"feature": "btc_corr_30d", "op": "<=", "threshold": thr}]
        else:
            cfg["SMC_RULES"] = list(_DEFAULTS["SMC_RULES"])
    cfg["SMC_REQUIRE_ALL"] = _parse_bool(
        os.getenv("SMC_REQUIRE_ALL"), _DEFAULTS["SMC_REQUIRE_ALL"])

    # BD
    bd_rules_json = os.getenv("BD_RULES_JSON")
    if bd_rules_json:
        cfg["BD_RULES"] = _parse_rules_json(bd_rules_json, _DEFAULTS["BD_RULES"])
    else:
        legacy = os.getenv("BD_MIN_ADX_MED")
        if legacy is not None:
            thr = _parse_float(legacy, 28.0)
            cfg["BD_RULES"] = [{"feature": "adx_med", "op": ">=", "threshold": thr}]
        else:
            cfg["BD_RULES"] = list(_DEFAULTS["BD_RULES"])
    cfg["BD_REQUIRE_ALL"] = _parse_bool(
        os.getenv("BD_REQUIRE_ALL"), _DEFAULTS["BD_REQUIRE_ALL"])

    # MASR
    masr_rules_json = os.getenv("MASR_RULES_JSON")
    if masr_rules_json:
        cfg["MASR_RULES"] = _parse_rules_json(masr_rules_json, _DEFAULTS["MASR_RULES"])
    else:
        legacy = os.getenv("MASR_EXCLUDE_ASSET_CLASSES")
        if legacy is not None:
            excl = _parse_csv(legacy, ["cfd"])
            cfg["MASR_RULES"] = [
                {"feature": "asset_class", "op": "not_in", "threshold": excl}
            ]
        else:
            cfg["MASR_RULES"] = list(_DEFAULTS["MASR_RULES"])
    cfg["MASR_REQUIRE_ALL"] = _parse_bool(
        os.getenv("MASR_REQUIRE_ALL"), _DEFAULTS["MASR_REQUIRE_ALL"])

    # 衍生：MASR_EXCLUDE_ASSET_CLASSES（給 live screen_coins 用的便利欄位）
    # 從 MASR_RULES 抽 op="not_in" 的 asset_class threshold；找不到回 ["cfd"]
    cfg["MASR_EXCLUDE_ASSET_CLASSES"] = _DEFAULTS["MASR_EXCLUDE_ASSET_CLASSES"]
    for r in cfg["MASR_RULES"]:
        if r.get("feature") == "asset_class" and r.get("op") == "not_in":
            thr = r.get("threshold", [])
            if isinstance(thr, list):
                cfg["MASR_EXCLUDE_ASSET_CLASSES"] = thr
                break

    # NKF
    cfg["NKF_RULES"] = _parse_rules_json(
        os.getenv("NKF_RULES_JSON"), _DEFAULTS["NKF_RULES"])
    cfg["NKF_REQUIRE_ALL"] = _parse_bool(
        os.getenv("NKF_REQUIRE_ALL"), _DEFAULTS["NKF_REQUIRE_ALL"])

    # MR
    cfg["MR_RULES"] = _parse_rules_json(
        os.getenv("MR_RULES_JSON"), _DEFAULTS["MR_RULES"])
    cfg["MR_REQUIRE_ALL"] = _parse_bool(
        os.getenv("MR_REQUIRE_ALL"), _DEFAULTS["MR_REQUIRE_ALL"])

    return cfg


# ── rule 評估 ───────────────────────────────────────────────────
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


def _eval_rule(rule: dict, features: dict) -> tuple[Optional[bool], str]:
    """評估單條 rule 是否「通過」（即不 skip）。
    回傳 (passed, reason_if_fail_or_missing)
      passed=True → 通過
      passed=False → 沒通過（值有，但不滿足條件）
      passed=None → 特徵缺/NaN → fail-open（reason 給 log）
    """
    feat = rule["feature"]
    op = rule["op"]
    thr = rule["threshold"]
    v = features.get(feat)
    if v is None or _is_nan(v) or (isinstance(v, str) and v.strip() == ""):
        return None, f"feature_missing:{feat}"

    try:
        if op == ">=":
            passed = float(v) >= float(thr)
        elif op == "<=":
            passed = float(v) <= float(thr)
        elif op == ">":
            passed = float(v) > float(thr)
        elif op == "<":
            passed = float(v) < float(thr)
        elif op == "==":
            passed = (str(v) == str(thr)) if isinstance(thr, str) else (v == thr)
        elif op == "!=":
            passed = (str(v) != str(thr)) if isinstance(thr, str) else (v != thr)
        elif op == "in":
            passed = v in (thr if isinstance(thr, (list, tuple, set)) else [thr])
        elif op == "not_in":
            passed = v not in (thr if isinstance(thr, (list, tuple, set)) else [thr])
        else:
            log.warning(f"[feature_filter] 不支援 op={op}，當 fail-open")
            return None, f"unknown_op:{op}"
    except (ValueError, TypeError) as e:
        log.warning(f"[feature_filter] eval error rule={rule}: {e}")
        return None, f"eval_error:{e}"

    if passed:
        return True, "ok"
    # 用 thr 的可讀表示（list 用 ,）
    thr_str = ",".join(map(str, thr)) if isinstance(thr, (list, tuple)) else str(thr)
    if isinstance(v, float):
        v_str = f"{v:.4f}".rstrip("0").rstrip(".")
    else:
        v_str = str(v)
    return False, f"{feat}={v_str} fails {op} {thr_str}"


def _eval_rules(rules: list[dict], features: dict, require_all: bool
                 ) -> tuple[bool, str]:
    """跑整個 rule list。回傳 (skip, reason)。
    skip=True 表示該 (strategy, symbol) 不該進回測。

    require_all=True：所有 rule 都要 passed=True 才不 skip（AND；任一 False/None 不會 skip 因為 None 視為 fail-open per rule，但 False 要 skip）
                       ↑ 修正：None per-rule = 該 rule 略過；False per-rule = 整組 fail
    require_all=False：任一 rule passed=True 即不 skip（OR）；
                       所有 rule 都 False/None → skip？
                       實作：OR 若沒有任何 True 才 skip；全 None 視為 fail-open（不 skip）

    （NaN/缺欄 fail-open per rule 是為了 features pickle 偶有缺值時不要全部誤砍。）
    """
    if not rules:
        return False, "no_rules"

    results = [_eval_rule(r, features) for r in rules]
    # 每筆 rule 的 (passed, reason)
    fails = [r for (p, r) in results if p is False]
    misses = [r for (p, r) in results if p is None]
    passes = [r for (p, r) in results if p is True]

    if require_all:
        # AND：任一 False → skip
        if fails:
            return True, "; ".join(fails)
        # 沒有 fail：可能全 pass 或部分 None
        if misses and not passes:
            # 全 missing → fail-open
            return False, "all_missing:" + ";".join(misses)
        return False, "passed_all"
    else:
        # OR：任一 True → not skip
        if passes:
            return False, "passed_any"
        # 沒有 pass，但若有 missing 應 fail-open
        if misses:
            return False, "all_missing_or_fail:" + ";".join(misses)
        # 全 fail（沒有 pass、沒有 missing）→ skip
        return True, "all_failed:" + "; ".join(fails)


# ── 對外主入口 ─────────────────────────────────────────────────
_SUPPORTED = {"smc", "bd", "masr", "nkf", "mr"}


def should_skip_for_strategy(
    strategy: str,
    symbol: str,
    features: Optional[dict],
) -> tuple[bool, str]:
    """
    回傳 (skip, reason)。
    skip=True 表示該 (strategy, symbol) 不該進回測。

    fail-open 規則：
      - master switch 關閉 → (False, "filters_disabled")
      - features=None       → (False, "no_features_skip_filter")
      - 不在受控策略內       → (False, "no_rule_for_strategy")
      - 規則表為空          → (False, "no_rules")
    """
    cfg = load_feature_filter_config()

    if not cfg["BACKTEST_USE_FEATURE_FILTERS"]:
        return False, "filters_disabled"

    if features is None:
        log.info(f"[feature_filter] {strategy}/{symbol} fail-open: no features")
        return False, "no_features_skip_filter"

    s = strategy.lower()
    if s not in _SUPPORTED:
        return False, "no_rule_for_strategy"

    rules_key = f"{s.upper()}_RULES"
    require_key = f"{s.upper()}_REQUIRE_ALL"
    rules = cfg.get(rules_key, [])
    require_all = cfg.get(require_key, True)

    return _eval_rules(rules, features, require_all)
