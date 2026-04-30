"""
shadow_runner.py — Live vs backtest 訊號等價性比對器

P10 phase 3：訊號產生時觸發，呼叫 backtest path 在同一根 K 線上跑一遍，
比對 live signal 跟 backtest signal 的 entry/SL/TP/score。

Diff classification（三層）：
  1. 完全一致（within TOLERANCE）              → "exact"
  2. 落在 KNOWN_ACCEPTABLE_DIFFS 已登記偏差內   → "acceptable"
  3. 其他                                       → "real_mismatch"  ← alert

Real mismatch 寫 reports/shadow_diffs/<sym>_<bar_time>.json + caller alert。
shadow 失敗（exception）不阻塞 live 訊號（caller try/except）。

設計依據：reports/p10_recon_20260430_1752.md §1.3 等價性 diff 分析。
"""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).parent))
log = logging.getLogger("shadow_runner")

ROOT = Path(__file__).parent.parent
SHADOW_DIFFS_DIR = ROOT / "reports" / "shadow_diffs"


# ── 對照容差 ─────────────────────────────────────────────────────
# 直接計算後的 raw 容差（先過這道；通過後再進 KNOWN_ACCEPTABLE_DIFFS 檢查）
TOLERANCE = {
    "direction": "exact",          # 必須完全一致
    "entry":     {"max_pct": 0.0005},   # 0.05%
    "sl":        {"max_pct": 0.0005},
    "tp1":       {"max_pct": 0.0005},
    "tp2":       {"max_pct": 0.0005},
    "score":     {"max_delta": 1},      # ±1 (acceptable 之一)
}

# Phase 1 recon 列出的 5 點 acceptable diff（對應 reports/p10_recon_*.md §1.3.E）
KNOWN_ACCEPTABLE_DIFFS = {
    "30d_return_calc_path": {
        "description": "live 用日線 30d return / backtest 用 4h × 180 bars，"
                       "邊界對齊不完全",
        "fields": ["passed_30d_filter"],
        "max_delta_pct": 0.001,
        "applies_to_strategy": ["masr"],
    },
    "adx_score_bonus": {
        "description": "live _score_signal 算 ADX>30 +1 bonus；"
                       "backtest 註解'ADX bonus 跳過'",
        "fields": ["score"],
        "max_delta": 1,
        "applies_to_strategy": ["masr"],
    },
    "min_breakout_pct_fallback": {
        "description": "live getattr fallback 0.005 / backtest 直讀 default 0.0；"
                       "Config 本身 default 0.0 → 兩邊等價，是 code smell",
        "fields": ["score", "passed"],
        "max_delta": 0,
        "applies_to_strategy": ["masr"],
    },
    "atr_window_boundary": {
        "description": "atr_window iloc[-N:] vs iloc[i-N+1:i+1] 邊界寫法不同，"
                       "實測等價，留容差防浮點誤差",
        "fields": ["atr"],
        "max_delta_pct": 0.0001,
        "applies_to_strategy": ["masr"],
    },
    # 第 5 點 (TP1 後 SL fixed BE vs ATR trailing) 屬於 trade management，
    # 不在 shadow 比對範圍（shadow 只比訊號產生時的 entry/SL/TP）
}


# ── Backtest path 單根 K 線評估器（從 run_backtest_masr 抽出）──
def _masr_signal_at_bar(
    df_4h: pd.DataFrame,
    bar_idx: int,
    symbol: str = "X",
) -> Optional[dict]:
    """
    對 df_4h 在 bar_idx 那根 K 線跑 MASR 進場邏輯（mirror run_backtest_masr 的
    per-bar 段，line 2316-2412）。bar_idx 應為「已收盤的目標 bar」。

    回傳 dict（sig）或 None（不過濾條件）。
    """
    from config import Config

    if bar_idx < 60 or bar_idx >= len(df_4h):
        return None

    lookback = int(Config.MASR_RES_LOOKBACK)
    if bar_idx < lookback + 5:
        return None

    # ── 預計算指標（注意：完整 series，跟 backtest 一致）────
    ema20_s = ta.ema(df_4h["close"], length=20)
    ema50_s = ta.ema(df_4h["close"], length=50)
    atr_s = ta.atr(df_4h["high"], df_4h["low"], df_4h["close"], length=14)
    avg_vol_s = df_4h["volume"].rolling(21).mean().shift(1)
    if ema20_s is None or ema50_s is None or atr_s is None:
        return None

    ema20_v = ema20_s.iloc[bar_idx]
    ema50_v = ema50_s.iloc[bar_idx]
    atr_v = atr_s.iloc[bar_idx]
    avg_vol = avg_vol_s.iloc[bar_idx]
    cur_close = float(df_4h["close"].iloc[bar_idx])
    cur_vol = float(df_4h["volume"].iloc[bar_idx])

    if pd.isna(ema20_v) or pd.isna(ema50_v) or pd.isna(atr_v) \
            or pd.isna(avg_vol) or float(avg_vol) <= 0:
        return None
    ema20_v = float(ema20_v)
    ema50_v = float(ema50_v)
    atr_v = float(atr_v)

    # 條件 b: EMA20 > EMA50
    if ema20_v <= ema50_v:
        return None

    # 30 日漲幅（4h × 180 bars）
    min_30d = float(getattr(Config, "MASR_MIN_30D_RETURN_PCT", 0.0))
    bars_30d = 180
    if min_30d > 0 and bar_idx >= bars_30d:
        old_close = float(df_4h["close"].iloc[bar_idx - bars_30d])
        if old_close > 0:
            ret_30d = (cur_close - old_close) / old_close
            if ret_30d < min_30d:
                return None

    # 距 EMA50
    if ema50_v <= 0:
        return None
    dist_ema50 = (cur_close - ema50_v) / ema50_v
    if dist_ema50 > float(Config.MASR_MAX_DIST_FROM_EMA50):
        return None

    # ATR 過熱
    atr_window = atr_s.iloc[bar_idx - lookback + 1:bar_idx + 1]
    atr_q = float(atr_window.quantile(float(Config.MASR_ATR_PERCENTILE_MAX)))
    if atr_v >= atr_q:
        return None

    # 找阻力位（用 backtest 的 _bt_masr_find_resistance）
    from backtest import _bt_masr_find_resistance
    highs_arr = df_4h["high"].values
    resistance = _bt_masr_find_resistance(
        highs_arr, bar_idx, atr_v, lookback,
        float(Config.MASR_RES_TOL_ATR_MULT),
        int(Config.MASR_RES_MIN_TOUCHES),
        cur_close,
    )
    if resistance is None:
        return None

    # 突破檢查
    min_break = float(Config.MASR_MIN_BREAKOUT_PCT)
    if cur_close <= resistance:
        return None
    if cur_close < resistance * (1 + min_break):
        return None

    # 量能
    vol_ratio = cur_vol / float(avg_vol)
    if vol_ratio < float(Config.MASR_VOL_MULT):
        return None

    # SL / TP
    sl_atr = cur_close - float(Config.MASR_SL_ATR_MULT) * atr_v
    sl = max(sl_atr, ema50_v)
    if sl >= cur_close:
        return None
    sl_dist = cur_close - sl
    tp1 = cur_close + float(Config.MASR_TP1_RR) * sl_dist
    tp2 = cur_close + float(Config.MASR_TP2_RR) * sl_dist

    # Score（注意：跟 backtest 一致，沒 ADX bonus）
    score = 1
    ema_gap_pct = (ema20_v - ema50_v) / ema50_v if ema50_v > 0 else 0
    if ema_gap_pct >= 0.01:
        score += 1
    if vol_ratio >= 2.0:
        score += 1
    if len(atr_window) >= 50:
        q40 = float(atr_window.quantile(0.40))
        if atr_v <= q40:
            score += 1
    score = min(score, 5)

    if score < int(Config.MASR_MIN_SCORE):
        return None

    return {
        "direction": "LONG",
        "entry": cur_close,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "score": score,
        "atr": atr_v,
        "ema20": ema20_v,
        "ema50": ema50_v,
        "resistance": resistance,
        "vol_ratio": vol_ratio,
    }


# ── Diff 比對 ────────────────────────────────────────────────────
def _is_within_tolerance_pct(live_v: float, bt_v: float, max_pct: float) -> bool:
    if live_v == bt_v == 0:
        return True
    if bt_v == 0:
        return abs(live_v) < 1e-9
    return abs(live_v - bt_v) / abs(bt_v) <= max_pct


def _is_acceptable(field: str, live_v, bt_v, strategy: str) -> bool:
    """field 的 (live, bt) 差距是否符合任一 KNOWN_ACCEPTABLE_DIFFS。"""
    for key, info in KNOWN_ACCEPTABLE_DIFFS.items():
        if strategy not in info.get("applies_to_strategy", []):
            continue
        if field not in info.get("fields", []):
            continue
        if "max_delta" in info:
            try:
                if abs(float(live_v) - float(bt_v)) <= info["max_delta"]:
                    return True
            except (TypeError, ValueError):
                continue
        if "max_delta_pct" in info:
            try:
                if _is_within_tolerance_pct(float(live_v), float(bt_v),
                                             info["max_delta_pct"]):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _diff_record(field: str, live_v, bt_v, classification: str,
                  acceptable_key: Optional[str] = None) -> dict:
    rec = {
        "field": field,
        "live": live_v,
        "backtest": bt_v,
        "classification": classification,
    }
    if acceptable_key:
        rec["acceptable_via"] = acceptable_key
    if isinstance(live_v, (int, float)) and isinstance(bt_v, (int, float)):
        if bt_v != 0:
            rec["delta_pct"] = (live_v - bt_v) / bt_v
        rec["delta_abs"] = live_v - bt_v
    return rec


def shadow_compare_signal(
    strategy_name: str,
    symbol: str,
    bar_time,
    live_signal: dict,
    df_klines_4h: pd.DataFrame,
    df_klines_1h: Optional[pd.DataFrame] = None,
) -> dict:
    """
    對 ma_sr_breakout 用 backtest path 跑同 K 線。

    df_klines_4h: 應為「截至 bar_time 的已收盤序列」；最後一根（iloc[-1]）就是
    觸發 live 訊號的 bar。caller 負責切片：live 拿到 df 後做 df.iloc[:-1] 排除
    forming bar 再傳進來。

    回傳：
      {
        "match": bool,
        "diffs": [...],
        "real_mismatches": [...],
        "live_signal": ...,
        "backtest_signal": ...,
      }
    """
    if strategy_name.lower() != "masr":
        return {
            "match": True, "diffs": [], "real_mismatches": [],
            "live_signal": live_signal, "backtest_signal": None,
            "note": f"strategy={strategy_name} not supported",
        }

    bar_idx = len(df_klines_4h) - 1
    bt_sig = _masr_signal_at_bar(df_klines_4h, bar_idx, symbol=symbol)

    diffs: list[dict] = []
    real_mismatches: list[dict] = []

    # 缺一邊有訊號、另一邊沒訊號 → real_mismatch（不容差）
    if bt_sig is None and live_signal is not None:
        rec = _diff_record("signal_existence", "live=signal", "backtest=None",
                           "real_mismatch")
        diffs.append(rec)
        real_mismatches.append(rec)
        _persist_diff(symbol, bar_time, live_signal, bt_sig, diffs)
        return {
            "match": False, "diffs": diffs, "real_mismatches": real_mismatches,
            "live_signal": live_signal, "backtest_signal": bt_sig,
        }
    if bt_sig is not None and live_signal is None:
        rec = _diff_record("signal_existence", "live=None", "backtest=signal",
                           "real_mismatch")
        diffs.append(rec)
        real_mismatches.append(rec)
        return {
            "match": False, "diffs": diffs, "real_mismatches": real_mismatches,
            "live_signal": live_signal, "backtest_signal": bt_sig,
        }
    if bt_sig is None and live_signal is None:
        return {"match": True, "diffs": [], "real_mismatches": [],
                "live_signal": None, "backtest_signal": None}

    # 兩邊都有訊號 → 逐欄比
    # direction
    if str(live_signal.get("direction", "")).upper() != str(bt_sig["direction"]).upper():
        rec = _diff_record("direction", live_signal.get("direction"),
                           bt_sig["direction"], "real_mismatch")
        diffs.append(rec)
        real_mismatches.append(rec)

    # entry / sl / tp1 / tp2 — 數值欄
    for f in ("entry", "sl", "tp1", "tp2"):
        live_v = live_signal.get(f)
        bt_v = bt_sig.get(f)
        if live_v is None or bt_v is None:
            continue
        max_pct = TOLERANCE[f]["max_pct"]
        if _is_within_tolerance_pct(float(live_v), float(bt_v), max_pct):
            continue  # exact
        # 超出 raw 容差 → 看是否符合 KNOWN_ACCEPTABLE
        if _is_acceptable(f, live_v, bt_v, "masr"):
            rec = _diff_record(f, live_v, bt_v, "acceptable")
            diffs.append(rec)
        else:
            rec = _diff_record(f, live_v, bt_v, "real_mismatch")
            diffs.append(rec)
            real_mismatches.append(rec)

    # score — int 欄
    live_score = live_signal.get("score")
    bt_score = bt_sig.get("score")
    if live_score is not None and bt_score is not None:
        delta = abs(int(live_score) - int(bt_score))
        if delta == 0:
            pass  # exact
        elif _is_acceptable("score", live_score, bt_score, "masr"):
            diffs.append(_diff_record("score", live_score, bt_score,
                                       "acceptable", "adx_score_bonus"))
        else:
            rec = _diff_record("score", live_score, bt_score, "real_mismatch")
            diffs.append(rec)
            real_mismatches.append(rec)

    if real_mismatches:
        _persist_diff(symbol, bar_time, live_signal, bt_sig, diffs)

    return {
        "match": len(real_mismatches) == 0,
        "diffs": diffs,
        "real_mismatches": real_mismatches,
        "live_signal": live_signal,
        "backtest_signal": bt_sig,
    }


def _persist_diff(symbol, bar_time, live_signal, bt_signal, diffs) -> None:
    SHADOW_DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp(bar_time).strftime("%Y%m%d_%H%M%S") if bar_time else "nots"
    out = SHADOW_DIFFS_DIR / f"{symbol}_{ts}.json"
    payload = {
        "symbol": symbol,
        "bar_time": str(bar_time),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "live_signal": _to_jsonable(live_signal),
        "backtest_signal": _to_jsonable(bt_signal),
        "diffs": _to_jsonable(diffs),
    }
    try:
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        log.error(f"shadow_diffs persist failed: {e}")


def _to_jsonable(v):
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_jsonable(val) for k, val in v.items()}
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, pd.Timestamp):
        return str(v)
    return str(v)


# ═══════════════════════════════════════════════════════════════════════
# P12D: MASR Short shadow comparison
# ═══════════════════════════════════════════════════════════════════════

# P12B 等價驗證 6/6 PASS、P12C.5 fast.top3 + P12D cooldown 驗證 0 mismatch。
# 本來不需要 KNOWN_ACCEPTABLE_DIFFS，但保留結構以備 paper 期間發現新 boundary
# case（避免 logic drift）。
SHORT_KNOWN_ACCEPTABLE_DIFFS = {
    # 目前空：v2 helper 跟 backtest 完全等價（reports/p12b_..., p12d_...）
    # 若未來 paper 期間發現新 diff，記錄在這裡並附證據連結
}


def shadow_compare_signal_short(
    strategy_name: str,
    symbol: str,
    bar_time,
    live_signal: Optional[dict],
    df_klines_1h: pd.DataFrame,
    df_klines_4h: pd.DataFrame,
    df_klines_1d: pd.DataFrame,
    df_btc_1d: pd.DataFrame,
    df_btc_4h: pd.DataFrame,
    in_cooldown: bool = False,
    variant: str = "fast",
) -> dict:
    """MASR Short shadow comparison。
    跟 long 同結構但加 cooldown 一致性驗證。

    df_klines_1h: 至 bar_time 為止的 1H 序列；最後一根 = 觸發 live 訊號的
    closed bar。caller 負責切（live 拿到 df 後做 df.iloc[:-1]）。

    in_cooldown: live 是否認為當前 bar 在 cooldown。本檢查用來抓 cooldown drift：
    若 live in_cooldown=True 但 backtest 該 bar 仍會 emit signal → real_mismatch
    （cooldown 過於嚴格）。若 live in_cooldown=False 但 backtest 該 bar 在
    cooldown → real_mismatch（cooldown 漏 trigger）。
    """
    if strategy_name.lower() != "masr_short":
        return {
            "match": True, "diffs": [], "real_mismatches": [],
            "live_signal": live_signal, "backtest_signal": None,
            "note": f"strategy={strategy_name} not supported by short shadow",
        }

    # 從 strategies.ma_sr_short 取共用 helper
    from strategies.ma_sr_short import _v2_check_at_bar

    bar_idx_1h = len(df_klines_1h) - 1
    if variant == "slow" and bar_idx_1h + 1 >= len(df_klines_1h):
        # df 太短不夠 slow variant
        return {"match": True, "diffs": [], "real_mismatches": [],
                "live_signal": live_signal, "backtest_signal": None,
                "note": "df too short for slow variant"}

    bt_sig = _v2_check_at_bar(
        df_klines_1h, df_klines_4h, df_klines_1d, df_btc_1d, df_btc_4h,
        bar_idx_1h=bar_idx_1h, variant=variant,
    )

    diffs: list[dict] = []
    real_mismatches: list[dict] = []

    # ── cooldown consistency check ───────────────────────────────
    # if live in cooldown：live 不會發 signal（live_signal=None）；backtest
    # helper 不知道 cooldown，仍可能算出 signal。此情況非 mismatch（live 端
    # 由 cooldown gate 攔住，backtest 端由 cooldown_until 攔住，兩邊獨立）。
    # 但若 live in_cooldown=False 且 live_signal=None 且 bt_sig 不為 None →
    # real_mismatch（live 該發 signal 但沒發），這個 case 對應 cooldown
    # 漏 trigger / live 邏輯 bug。
    if in_cooldown:
        # live 端被 cooldown 擋住，是預期行為。即使 backtest helper 算出 signal
        # 也不算 real_mismatch（這在 P12D verifier 內計入 cooldown_rejected）。
        if live_signal is not None:
            # live 在 cooldown 內仍下單 → cooldown gate bug
            rec = _diff_record("cooldown_violation",
                                "live_signal_in_cooldown",
                                "expected_None",
                                "real_mismatch")
            real_mismatches.append(rec)
            diffs.append(rec)
        # 不對 bt_sig 做評斷（cooldown 是 live-only 概念）
        return {
            "match": len(real_mismatches) == 0,
            "diffs": diffs, "real_mismatches": real_mismatches,
            "live_signal": live_signal, "backtest_signal": bt_sig,
            "note": "in_cooldown",
        }

    # ── 不在 cooldown：標準 signal-existence + 數值比對 ───────────
    if bt_sig is None and live_signal is not None:
        rec = _diff_record("signal_existence", "live=signal", "backtest=None",
                            "real_mismatch")
        real_mismatches.append(rec)
        diffs.append(rec)
        _persist_diff(symbol, bar_time, live_signal, bt_sig, diffs)
        return {
            "match": False, "diffs": diffs, "real_mismatches": real_mismatches,
            "live_signal": live_signal, "backtest_signal": bt_sig,
        }
    if bt_sig is not None and live_signal is None:
        rec = _diff_record("signal_existence", "live=None", "backtest=signal",
                            "real_mismatch")
        real_mismatches.append(rec)
        diffs.append(rec)
        return {
            "match": False, "diffs": diffs, "real_mismatches": real_mismatches,
            "live_signal": live_signal, "backtest_signal": bt_sig,
        }
    if bt_sig is None and live_signal is None:
        return {"match": True, "diffs": [], "real_mismatches": [],
                "live_signal": None, "backtest_signal": None}

    # 兩邊都有訊號 → 逐欄比
    if str(live_signal.get("direction", "")).upper() != str(bt_sig["direction"]).upper():
        rec = _diff_record("direction", live_signal.get("direction"),
                            bt_sig["direction"], "real_mismatch")
        diffs.append(rec)
        real_mismatches.append(rec)

    for f in ("entry", "sl", "tp1", "tp2"):
        live_v = live_signal.get(f)
        bt_v = bt_sig.get(f)
        if live_v is None or bt_v is None:
            continue
        max_pct = TOLERANCE[f]["max_pct"]
        if _is_within_tolerance_pct(float(live_v), float(bt_v), max_pct):
            continue
        if _is_acceptable_short(f, live_v, bt_v):
            diffs.append(_diff_record(f, live_v, bt_v, "acceptable"))
        else:
            rec = _diff_record(f, live_v, bt_v, "real_mismatch")
            diffs.append(rec)
            real_mismatches.append(rec)

    live_score = live_signal.get("score")
    bt_score = bt_sig.get("score")
    if live_score is not None and bt_score is not None:
        delta = abs(int(live_score) - int(bt_score))
        if delta == 0:
            pass
        elif _is_acceptable_short("score", live_score, bt_score):
            diffs.append(_diff_record("score", live_score, bt_score,
                                        "acceptable"))
        else:
            rec = _diff_record("score", live_score, bt_score, "real_mismatch")
            diffs.append(rec)
            real_mismatches.append(rec)

    if real_mismatches:
        _persist_diff(symbol, bar_time, live_signal, bt_sig, diffs)

    return {
        "match": len(real_mismatches) == 0,
        "diffs": diffs, "real_mismatches": real_mismatches,
        "live_signal": live_signal, "backtest_signal": bt_sig,
    }


def _is_acceptable_short(field: str, live_v, bt_v) -> bool:
    """check SHORT_KNOWN_ACCEPTABLE_DIFFS for masr_short field。"""
    for key, info in SHORT_KNOWN_ACCEPTABLE_DIFFS.items():
        if "masr_short" not in info.get("applies_to_strategy", []):
            continue
        if field not in info.get("fields", []):
            continue
        if "max_delta" in info:
            try:
                if abs(float(live_v) - float(bt_v)) <= info["max_delta"]:
                    return True
            except (TypeError, ValueError):
                continue
        if "max_delta_pct" in info:
            try:
                if _is_within_tolerance_pct(float(live_v), float(bt_v),
                                              info["max_delta_pct"]):
                    return True
            except (TypeError, ValueError):
                continue
    return False

