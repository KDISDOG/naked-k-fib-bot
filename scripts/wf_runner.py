"""
wf_runner.py — Walk-forward 回測引擎

對任一 run_backtest_* 函式，跑全段一次後依 trade.open_time 切 N 段。
**不切 K 線分別呼叫** — 會在切點漏訊號。

輸出 dict + pickle 到 .cache/wf_results/。
"""
import os
import sys
import time
import pickle
import inspect
import logging
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Callable, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import Config

log = logging.getLogger("wf_runner")

# ── ConfigPatch（task 2 sweep_runner 會 re-export）─────────────
@contextmanager
def ConfigPatch(overrides: Optional[dict]):
    """
    暫時改 Config.XXX，exit 時保證 revert（即使 raise）。
    None / 空 dict 直接 no-op。
    """
    if not overrides:
        yield
        return
    saved = {}
    try:
        for k, v in overrides.items():
            saved[k] = getattr(Config, k, _MISSING)
            setattr(Config, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                # Config 原本沒這 attr → 移除（理論上不該發生，但保險）
                try:
                    delattr(Config, k)
                except AttributeError:
                    pass
            else:
                setattr(Config, k, v)


_MISSING = object()


def _accepts_config_overrides(fn: Callable) -> bool:
    """檢測 backtest_fn 簽名是否含 config_overrides 參數。"""
    try:
        sig = inspect.signature(fn)
        return "config_overrides" in sig.parameters
    except (TypeError, ValueError):
        return False


# ── Metrics ────────────────────────────────────────────────────
def _segment_metrics(trades: list) -> dict:
    """
    對一段 trades（已 closed）算指標：
      n_trades / win_rate / avg_rr / total_pnl / max_dd / median_give_back
    """
    closed = [t for t in trades if getattr(t, "result", "") not in ("", "OPEN")]
    n = len(closed)
    if n == 0:
        return {"n_trades": 0, "win_rate": 0.0, "avg_rr": 0.0,
                "total_pnl": 0.0, "max_dd": 0.0, "median_give_back": None}

    closed.sort(key=lambda t: t.open_time or datetime.min)
    pnls = np.array([float(t.net_pnl) for t in closed])
    wins_arr = pnls[pnls > 0]
    losses_arr = pnls[pnls < 0]

    win_rate = float((pnls > 0).sum()) / n
    total_pnl = float(pnls.sum())

    # avg_rr = mean win / |mean loss|
    if len(wins_arr) > 0 and len(losses_arr) > 0:
        avg_rr = float(wins_arr.mean()) / abs(float(losses_arr.mean()))
    else:
        avg_rr = float("inf") if len(losses_arr) == 0 else 0.0

    # max DD（cumulative balance from 0）
    bal = peak = mdd = 0.0
    for p in pnls:
        bal += p
        peak = max(peak, bal)
        mdd = max(mdd, peak - bal)

    # give-back 中位數（mfe > 0 且 final ≤ 0）
    gbs = []
    for t in closed:
        if t.net_pnl > 0:
            continue
        mfp = getattr(t, "max_favorable_price", t.entry)
        if t.direction == "LONG":
            mfe = (mfp - t.entry) * t.qty
        else:
            mfe = (t.entry - mfp) * t.qty
        if mfe > 0:
            gbs.append((mfe - float(t.net_pnl)) / mfe)
    median_gb = float(np.median(gbs)) if gbs else None

    return {
        "n_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_rr": round(avg_rr, 4) if avg_rr != float("inf") else None,
        "total_pnl": round(total_pnl, 4),
        "max_dd": round(mdd, 4),
        "median_give_back": round(median_gb, 4) if median_gb is not None else None,
    }


def _split_segments(trades: list, n_segments: int,
                    period_start: datetime, period_end: datetime) -> list[dict]:
    """
    把 trades 按 entry_time 切 N 段（IS / OOS1 / OOS2 / ...）。
    n_segments=3 → ["IS", "OOS1", "OOS2"]。
    """
    span_sec = (period_end - period_start).total_seconds()
    seg_sec = span_sec / n_segments

    labels = ["IS"] + [f"OOS{i}" for i in range(1, n_segments)]
    out = []
    for k in range(n_segments):
        t0 = period_start + pd.Timedelta(seconds=seg_sec * k)
        t1 = period_start + pd.Timedelta(seconds=seg_sec * (k + 1))
        sub = [t for t in trades
               if t.open_time and t0 <= t.open_time < t1]
        out.append({
            "label": labels[k],
            "from": t0,
            "to": t1,
            "trades": sub,
            "metrics": _segment_metrics(sub),
        })
    return out


# ── 主入口 ─────────────────────────────────────────────────────
def run_walk_forward(
    backtest_fn: Callable,
    client,
    symbols: list[str],
    months: int,
    n_segments: int = 3,
    config_overrides: Optional[dict] = None,
    config_label: str = "default",
    extra_kwargs: Optional[dict] = None,
) -> dict:
    """
    對 symbols 跑 backtest_fn，取得全段 trades 後切 n_segments。

    config_overrides:
      若 backtest_fn 簽名有 config_overrides 參數 → 直接傳遞（首選，較乾淨）
      否則 → 用 ConfigPatch 暫時改 Config.XXX
    extra_kwargs: 傳給 backtest_fn 的其他參數（如 variant="fast"）
    """
    extra_kwargs = extra_kwargs or {}
    accepts_overrides = _accepts_config_overrides(backtest_fn)
    fn_name = backtest_fn.__name__

    # 全期間：從 (now - months) 到 now（與 fetch_klines 對齊）
    period_end = datetime.now(timezone.utc).replace(tzinfo=None)
    period_start = period_end - pd.Timedelta(days=30 * months)

    seg_aggregate: list[list] = [[] for _ in range(n_segments)]
    by_coin: dict[str, list[dict]] = {}

    print(f"[WF] {fn_name} on {len(symbols)} symbols × {months}m, segments={n_segments}, label={config_label}")

    for sym in symbols:
        t0 = time.time()
        try:
            if accepts_overrides:
                trades = backtest_fn(
                    client, sym, months, debug=False,
                    config_overrides=config_overrides,
                    config_label=config_label,
                    **extra_kwargs,
                )
            else:
                with ConfigPatch(config_overrides):
                    trades = backtest_fn(client, sym, months, debug=False,
                                          **extra_kwargs)
        except Exception as e:
            print(f"  [{sym}] failed: {e}", file=sys.stderr)
            by_coin[sym] = [{"n_trades": 0} for _ in range(n_segments)]
            continue

        segs = _split_segments(trades, n_segments, period_start, period_end)
        by_coin[sym] = [s["metrics"] for s in segs]
        for k, s in enumerate(segs):
            seg_aggregate[k].extend(s["trades"])
        elapsed = time.time() - t0
        n_total = sum(s["metrics"]["n_trades"] for s in segs)
        print(f"  [{sym}] {n_total} trades  {elapsed:.1f}s  "
              + " | ".join(f"{s['label']}={s['metrics']['n_trades']}"
                            for s in segs))

    # 跨幣聚合 segment metrics
    aggregated_segs = []
    labels = ["IS"] + [f"OOS{i}" for i in range(1, n_segments)]
    for k in range(n_segments):
        t0 = period_start + pd.Timedelta(seconds=(period_end - period_start).total_seconds() * k / n_segments)
        t1 = period_start + pd.Timedelta(seconds=(period_end - period_start).total_seconds() * (k + 1) / n_segments)
        aggregated_segs.append({
            "label": labels[k],
            "from": t0,
            "to": t1,
            "trades": seg_aggregate[k],
            "metrics": _segment_metrics(seg_aggregate[k]),
        })

    result = {
        "fn_name": fn_name,
        "config_label": config_label,
        "config_overrides": config_overrides,
        "symbols": symbols,
        "months": months,
        "n_segments": n_segments,
        "segments": aggregated_segs,
        "by_coin": by_coin,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }

    # Pickle 持久化
    out_dir = Path(__file__).parent.parent / ".cache" / "wf_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{fn_name}_{config_label}_{int(time.time())}.pkl"
    out_path = out_dir / fname
    with open(out_path, "wb") as fp:
        pickle.dump(result, fp)
    print(f"[WF] saved → {out_path}")

    result["_pickle_path"] = str(out_path)
    return result


__all__ = ["run_walk_forward", "ConfigPatch"]
