"""
回測腳本 — 裸K + Fibonacci 策略
Walk-forward 模擬：逐根 K 棒重放歷史，使用與真實 bot 完全相同的訊號邏輯

執行方式：
    c:/python312/python.exe scripts/backtest.py
    c:/python312/python.exe scripts/backtest.py --symbol ETHUSDT --tf 4h --months 6
    c:/python312/python.exe scripts/backtest.py --tf 1h --tf 4h   （同時測兩個時間框架）

輸出：
    - 每筆模擬交易結果
    - 勝率、期望值、最大連虧、最大回撤
    - 月度損益摘要
"""
import os
import sys
import math
import argparse
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import pandas_ta as ta
import numpy as np
from dotenv import load_dotenv
from binance.client import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_engine import (
    SignalEngine, KEY_FIB_LEVELS, FIB_TOLERANCE,
    FIB_LONG_MAP, FIB_SHORT_MAP, _normalize_fib_key
)

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,   # 回測時只顯示 WARNING 以上，避免太雜
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("backtest")

TAKER_FEE = 0.0004   # 0.04%

# ── 回測參數（從 .env 讀取，與真實 bot 一致）──────────────────────
DEFAULT_SYMBOL    = "BTCUSDT"
DEFAULT_TF        = ["1h", "4h"]
DEFAULT_MONTHS    = 6         # 抓幾個月歷史資料
INITIAL_BALANCE   = 1000.0    # 模擬起始資金（.env 沒有此項，手動設定）
RISK_PER_TRADE    = float(os.getenv("RISK_PER_TRADE",    "0.02"))
MAX_NOTIONAL_PCT  = float(os.getenv("MAX_NOTIONAL_PCT",  "0.20"))
LEVERAGE          = int(os.getenv("MAX_LEVERAGE",        "2"))
MIN_SCORE         = int(os.getenv("MIN_SIGNAL_SCORE",    "3"))
TAKER_FEE_RATE    = TAKER_FEE
COOLDOWN_BARS     = int(os.getenv("COOLDOWN_BARS",       "6"))

# ── 可調實驗參數（預設值 = 寬鬆模式，方便回測）────────────────────
# 這些參數只影響回測，不自動同步到真實 bot
BT_FIB_TOL        = 0.008   # Fib 容忍度（真實 bot 是 0.005）
BT_VOL_MULT       = 1.1     # 成交量倍率門檻（真實 bot 是 1.3）
BT_SKIP_VOL_RISE  = False   # True = 不要求「當根>前根」
BT_SKIP_BAD_FIB   = True    # True = 跳過 R:R 差的 0.236/0.786 位


# ── 資料結構 ──────────────────────────────────────────────────────
@dataclass
class BtTrade:
    symbol:    str
    direction: str
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    qty:       float
    qty_tp1:   float
    qty_tp2:   float
    fib_level: str
    pattern:   str
    score:     int
    timeframe: str
    open_bar:  int             # 開倉的 bar index
    open_time: datetime = None

    # 結果（收盤後填入）
    result:    str = ""        # "TP1", "TP2", "TP1+TP2", "SL", "TIMEOUT"
    exit_price: float = 0.0
    close_bar:  int = 0
    pnl:        float = 0.0
    fee:        float = 0.0
    net_pnl:    float = 0.0
    close_time: datetime = None
    tp1_hit:    bool = False


# ── K 線下載（幣安期貨）──────────────────────────────────────────
def fetch_klines(client: Client, symbol: str, interval: str,
                 months: int) -> pd.DataFrame:
    """下載最近 N 個月的期貨 K 線"""
    start = datetime.utcnow() - timedelta(days=30 * months)
    start_ms = int(start.timestamp() * 1000)
    all_klines = []
    limit = 1500

    print(f"  下載 {symbol} {interval} {months}個月歷史資料...", end="", flush=True)
    while True:
        raw = client.futures_klines(
            symbol=symbol, interval=interval,
            startTime=start_ms, limit=limit
        )
        if not raw:
            break
        all_klines.extend(raw)
        last_t = raw[-1][0]
        if len(raw) < limit:
            break
        start_ms = last_t + 1

    print(f" {len(all_klines)} 根")

    df = pd.DataFrame(all_klines, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "qav"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df.reset_index(drop=True)


# ── 複用 SignalEngine 的核心方法（不需真實 client）───────────────
class BacktestSignalEngine(SignalEngine):
    """繼承 SignalEngine，覆寫 _get_klines 使用本地資料"""

    def __init__(self, fib_tol: float = 0.008, vol_mult: float = 1.1,
                 skip_vol_rise: bool = False, skip_bad_fib: bool = True):
        super().__init__(client=None)
        self._fib_tol      = fib_tol
        self._vol_mult     = vol_mult
        self._skip_vol_rise = skip_vol_rise
        self._skip_bad_fib  = skip_bad_fib
        # 低 R:R 的 Fib 位（LONG 0.236 TP1=0%, SHORT 0.786 TP1=1.0）
        self._bad_fib_levels = {"0.236", "0.786"}

    def _price_near_fib(self, price: float, fib_levels: dict) -> Optional[str]:
        """覆寫：使用回測專用的 fib_tol"""
        for level, fib_price in fib_levels.items():
            if fib_price == 0:
                continue
            if abs(price - fib_price) / fib_price <= self._fib_tol:
                return level
        return None

    def check_on_bar(self, df_tf: pd.DataFrame, df_daily: pd.DataFrame,
                     bar_idx: int, timeframe: str) -> Optional[object]:
        """
        在 bar_idx 這根 K 棒（已收盤）上跑訊號偵測
        df_tf：已過濾到 bar_idx 之前的資料（不含未來）
        """
        df = df_tf.iloc[:bar_idx + 1].copy().reset_index(drop=True)
        if len(df) < 50:
            return None

        # ── 複用 SignalEngine 的各個方法 ──
        swing_result = self._get_latest_swing_pair(df_daily)
        if not swing_result:
            return None
        swing_h, swing_l, swing_trend = swing_result
        if swing_h <= swing_l:
            return None

        fib_levels = self._calc_fib(swing_h, swing_l)
        price = df["close"].iloc[-1]
        fib_hit = self._price_near_fib(price, fib_levels)
        if not fib_hit:
            return None

        # 跳過低 R:R Fib 位
        if self._skip_bad_fib and fib_hit in self._bad_fib_levels:
            return None

        direction = self._determine_direction(df_daily, swing_trend)
        if not direction:
            return None

        if self._swing_structure_broken(df, direction, swing_h, swing_l):
            return None

        fib_price = fib_levels.get(fib_hit)
        if fib_price and not self._is_fib_fresh(df, fib_price):
            return None

        pattern_result = self._detect_pattern(df, direction)
        if not pattern_result:
            return None
        pattern_name, pattern_strength = pattern_result

        if not self._volume_confirmed(df, period=20, ratio=self._vol_mult):
            return None
        if not self._skip_vol_rise and not self._volume_rising(df):
            return None

        tp_sl = self._calc_fib_tp_sl(direction, fib_hit, swing_h, swing_l)

        if direction == "LONG":
            if tp_sl["sl"] >= price or tp_sl["tp1"] <= price:
                return None
        else:
            if tp_sl["sl"] <= price or tp_sl["tp1"] >= price:
                return None

        # 成交量評分
        vol_score = self._volume_ratio_score(df, period=20)
        exhaustion_bonus = 0
        if direction == "LONG" and self._volume_exhaustion_long(df):
            exhaustion_bonus = 1
        elif direction == "SHORT" and self._volume_exhaustion_short(df):
            exhaustion_bonus = 1

        score = (
            pattern_strength
            + (1 if float(fib_hit) == 0.618 else 0)
            + (1 if swing_trend == ("up" if direction == "LONG" else "down") else 0)
            + vol_score
            + exhaustion_bonus
        )
        score = max(0, min(5, score))

        from signal_engine import Signal
        return Signal(
            symbol     = "BT",
            direction  = direction,
            entry      = price,
            sl         = tp_sl["sl"],
            tp1        = tp_sl["tp1"],
            tp2        = tp_sl["tp2"],
            fib_level  = fib_hit,
            pattern    = pattern_name,
            score      = score,
            timeframe  = timeframe,
            swing_high = swing_h,
            swing_low  = swing_l,
        )


# ── 倉位計算（脫離 client，使用模擬餘額）───────────────────────────
def calc_position(balance: float, entry: float, sl: float,
                  tp1: float, tp2: float) -> Optional[dict]:
    sl_pct = abs(entry - sl) / entry
    if sl_pct < 0.003 or sl_pct > 0.12:
        return None

    risk_usdt = balance * RISK_PER_TRADE
    effective_sl_pct = sl_pct + 2 * TAKER_FEE_RATE
    qty = risk_usdt / (effective_sl_pct * entry)

    notional = qty * entry
    margin = notional / LEVERAGE
    if margin > balance * MAX_NOTIONAL_PCT:
        margin = balance * MAX_NOTIONAL_PCT
        notional = margin * LEVERAGE
        qty = notional / entry

    qty = max(round(qty, 6), 0.000001)
    qty_tp1 = round(qty * 0.5, 6)
    qty_tp2 = qty - qty_tp1

    # 手續費估算
    fee_open  = qty * entry * TAKER_FEE_RATE
    fee_if_sl = qty * sl * TAKER_FEE_RATE + fee_open
    fee_if_tp = fee_open + qty_tp1 * tp1 * TAKER_FEE_RATE + qty_tp2 * tp2 * TAKER_FEE_RATE

    raw_risk = abs(entry - sl) * qty
    raw_reward = (abs(tp1 - entry) * qty_tp1 + abs(tp2 - entry) * qty_tp2)
    net_rr = (raw_reward - fee_if_tp) / (raw_risk + fee_if_sl) if (raw_risk + fee_if_sl) > 0 else 0

    if net_rr < 1.2:
        return None

    return {"qty": qty, "qty_tp1": qty_tp1, "qty_tp2": qty_tp2, "margin": margin}


# ── 模擬交易結果（逐根比對後續 K 棒）───────────────────────────────
def simulate_trade(trade: BtTrade, df_future: pd.DataFrame,
                   max_bars: int = 48) -> BtTrade:
    """
    從開倉後，逐根 K 棒檢查是否觸發 SL / TP1 / TP2
    max_bars：最多持倉幾根（超過就以市價平倉）
    """
    tp1_hit = False
    remaining_qty = trade.qty

    for i, (_, bar) in enumerate(df_future.iterrows()):
        if i >= max_bars:
            # 超時平倉
            exit_p = bar["close"]
            pnl = ((exit_p - trade.entry) * remaining_qty
                   if trade.direction == "LONG"
                   else (trade.entry - exit_p) * remaining_qty)
            fee = exit_p * remaining_qty * TAKER_FEE_RATE + trade.qty * trade.entry * TAKER_FEE_RATE
            trade.result     = "TIMEOUT"
            trade.exit_price = exit_p
            trade.close_bar  = trade.open_bar + i
            trade.pnl        = round(pnl, 4)
            trade.fee        = round(fee, 4)
            trade.net_pnl    = round(pnl - fee, 4)
            trade.close_time = bar["time"]
            trade.tp1_hit    = tp1_hit
            return trade

        high, low = bar["high"], bar["low"]

        if trade.direction == "LONG":
            # ── SL 觸發 ──
            if low <= trade.sl:
                exit_p = trade.sl
                sl_qty = remaining_qty
                pnl = (exit_p - trade.entry) * sl_qty
                fee = (exit_p * sl_qty + trade.qty * trade.entry) * TAKER_FEE_RATE
                tp1_pnl = ((trade.tp1 - trade.entry) * (trade.qty - remaining_qty)
                           if tp1_hit else 0)
                tp1_fee = (trade.tp1 * (trade.qty - remaining_qty) * TAKER_FEE_RATE
                           if tp1_hit else 0)
                total_pnl = pnl + tp1_pnl
                total_fee = fee + tp1_fee
                trade.result     = "TP1+SL" if tp1_hit else "SL"
                trade.exit_price = exit_p
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(total_pnl, 4)
                trade.fee        = round(total_fee, 4)
                trade.net_pnl    = round(total_pnl - total_fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = tp1_hit
                return trade

            # ── TP1 觸發（部分平倉）──
            if not tp1_hit and high >= trade.tp1:
                tp1_hit = True
                remaining_qty = trade.qty_tp2

            # ── TP2 觸發（剩餘平倉）──
            if tp1_hit and high >= trade.tp2:
                tp2_pnl = (trade.tp2 - trade.entry) * trade.qty_tp2
                tp1_pnl = (trade.tp1 - trade.entry) * trade.qty_tp1
                fee = (trade.qty * trade.entry +
                       trade.qty_tp1 * trade.tp1 +
                       trade.qty_tp2 * trade.tp2) * TAKER_FEE_RATE
                trade.result     = "TP1+TP2"
                trade.exit_price = trade.tp2
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(tp1_pnl + tp2_pnl, 4)
                trade.fee        = round(fee, 4)
                trade.net_pnl    = round(tp1_pnl + tp2_pnl - fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = True
                return trade

        else:  # SHORT
            # ── SL 觸發 ──
            if high >= trade.sl:
                exit_p = trade.sl
                sl_qty = remaining_qty
                pnl = (trade.entry - exit_p) * sl_qty
                fee = (exit_p * sl_qty + trade.qty * trade.entry) * TAKER_FEE_RATE
                tp1_pnl = ((trade.entry - trade.tp1) * (trade.qty - remaining_qty)
                           if tp1_hit else 0)
                tp1_fee = (trade.tp1 * (trade.qty - remaining_qty) * TAKER_FEE_RATE
                           if tp1_hit else 0)
                total_pnl = pnl + tp1_pnl
                total_fee = fee + tp1_fee
                trade.result     = "TP1+SL" if tp1_hit else "SL"
                trade.exit_price = exit_p
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(total_pnl, 4)
                trade.fee        = round(total_fee, 4)
                trade.net_pnl    = round(total_pnl - total_fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = tp1_hit
                return trade

            # ── TP1 觸發 ──
            if not tp1_hit and low <= trade.tp1:
                tp1_hit = True
                remaining_qty = trade.qty_tp2

            # ── TP2 觸發 ──
            if tp1_hit and low <= trade.tp2:
                tp2_pnl = (trade.entry - trade.tp2) * trade.qty_tp2
                tp1_pnl = (trade.entry - trade.tp1) * trade.qty_tp1
                fee = (trade.qty * trade.entry +
                       trade.qty_tp1 * trade.tp1 +
                       trade.qty_tp2 * trade.tp2) * TAKER_FEE_RATE
                trade.result     = "TP1+TP2"
                trade.exit_price = trade.tp2
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(tp1_pnl + tp2_pnl, 4)
                trade.fee        = round(fee, 4)
                trade.net_pnl    = round(tp1_pnl + tp2_pnl - fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = True
                return trade

    # 沒撞到任何條件（資料不夠）
    trade.result = "OPEN"
    return trade


# ── 輸出統計 ──────────────────────────────────────────────────────
def print_stats(trades: list, timeframe: str, symbol: str,
                initial_balance: float):
    closed = [t for t in trades if t.result not in ("", "OPEN")]
    if not closed:
        print(f"\n[{symbol} {timeframe}] 無已結算交易")
        return

    wins   = [t for t in closed if t.net_pnl > 0]
    losses = [t for t in closed if t.net_pnl <= 0]
    tp2_hits = [t for t in closed if t.result == "TP1+TP2"]
    sl_hits  = [t for t in closed if t.result in ("SL", "TP1+SL")]
    timeout  = [t for t in closed if t.result == "TIMEOUT"]

    total_net = sum(t.net_pnl for t in closed)
    total_fee = sum(t.fee for t in closed)
    win_rate  = len(wins) / len(closed) * 100
    avg_win   = (sum(t.net_pnl for t in wins) / len(wins)) if wins else 0
    avg_loss  = (sum(t.net_pnl for t in losses) / len(losses)) if losses else 0
    expectancy = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss

    # 最大連虧
    max_dd_streak = 0
    streak = 0
    for t in closed:
        if t.net_pnl <= 0:
            streak += 1
            max_dd_streak = max(max_dd_streak, streak)
        else:
            streak = 0

    # 最大回撤（以模擬餘額計算）
    balance = initial_balance
    peak = balance
    max_drawdown = 0.0
    for t in closed:
        balance += t.net_pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_drawdown = max(max_drawdown, dd)

    final_balance = initial_balance + total_net

    print(f"\n{'=' * 60}")
    print(f"  回測結果：{symbol} {timeframe}")
    print(f"{'=' * 60}")
    print(f"  交易總數：{len(closed)}  "
          f"（TP2全達：{len(tp2_hits)}  止損：{len(sl_hits)}  超時：{len(timeout)}）")
    print(f"  勝率：    {win_rate:.1f}%  ({len(wins)} 勝 / {len(losses)} 敗)")
    print(f"  期望值：  {expectancy:+.4f} USDT/單")
    print(f"  平均獲利：{avg_win:+.4f} USDT")
    print(f"  平均虧損：{avg_loss:+.4f} USDT")
    print(f"  總淨損益：{total_net:+.4f} USDT  （手續費：{total_fee:.4f} USDT）")
    print(f"  起始資金：{initial_balance:.2f} → 最終：{final_balance:.2f} USDT "
          f"（{(final_balance/initial_balance - 1)*100:+.1f}%）")
    print(f"  最大連虧：{max_dd_streak} 單")
    print(f"  最大回撤：{max_drawdown:.1f}%")

    # 月度摘要
    print(f"\n  月度損益：")
    monthly: dict[str, float] = {}
    for t in closed:
        if not t.close_time:
            continue
        key = t.close_time.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + t.net_pnl
    for month, pnl in sorted(monthly.items()):
        bar = "█" * int(abs(pnl) / max(abs(v) for v in monthly.values()) * 20)
        sign = "+" if pnl >= 0 else ""
        print(f"    {month}：{sign}{pnl:>8.4f} USDT  {bar}")

    # 最近 10 筆交易
    print(f"\n  最近 10 筆交易：")
    print(f"  {'時間':<18} {'方向':<6} {'結果':<10} {'Fib':<7} {'形態':<22} {'淨損益':>10}")
    print(f"  {'-'*78}")
    for t in closed[-10:]:
        ts = t.close_time.strftime("%m/%d %H:%M") if t.close_time else "—"
        mark = "✓" if t.net_pnl > 0 else "✗"
        print(f"  {ts:<18} {t.direction:<6} {mark}{t.result:<9} "
              f"{t.fib_level:<7} {t.pattern:<22} {t.net_pnl:>+10.4f}")

    print(f"{'=' * 60}")


# ── 主回測邏輯 ───────────────────────────────────────────────────
def run_backtest(client: Client, symbol: str, timeframe: str,
                 months: int) -> list:

    print(f"\n[{symbol} {timeframe}] 回測開始")

    # 下載相同時間區間的 daily（給 swing 判斷用）
    df_tf    = fetch_klines(client, symbol, timeframe, months)
    df_daily = fetch_klines(client, symbol, "1d", months + 1)

    if len(df_tf) < 100:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    # 預留前 60 根作為 warm-up（swing 判斷需要歷史資料）
    warmup = 60
    trades: list[BtTrade] = []
    balance = INITIAL_BALANCE
    in_trade = False
    cooldown_until = -1  # 冷卻期結束的 bar index

    # 建立回測引擎（使用可調參數）
    engine = BacktestSignalEngine(
        fib_tol       = BT_FIB_TOL,
        vol_mult      = BT_VOL_MULT,
        skip_vol_rise = BT_SKIP_VOL_RISE,
        skip_bad_fib  = BT_SKIP_BAD_FIB,
    )

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        # 冷卻期
        if i <= cooldown_until:
            continue

        # 當根已收盤 → 用截至當根的資料檢查訊號
        df_slice  = df_tf.iloc[:i + 1].copy().reset_index(drop=True)

        # daily 同步截到當根時間
        bar_time = df_tf["time"].iloc[i]
        df_d_slice = df_daily[df_daily["time"] <= bar_time].copy().reset_index(drop=True)
        if len(df_d_slice) < 20:
            continue

        try:
            sig = engine.check_on_bar(df_slice, df_d_slice, len(df_slice) - 1, timeframe)
        except Exception:
            continue

        if not sig or sig.score < MIN_SCORE:
            continue

        # 計算倉位
        pos = calc_position(balance, sig.entry, sig.sl, sig.tp1, sig.tp2)
        if not pos:
            continue

        # 建立模擬交易
        trade = BtTrade(
            symbol    = symbol,
            direction = sig.direction,
            entry     = sig.entry,
            sl        = sig.sl,
            tp1       = sig.tp1,
            tp2       = sig.tp2,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            fib_level = sig.fib_level,
            pattern   = sig.pattern,
            score     = sig.score,
            timeframe = timeframe,
            open_bar  = i,
            open_time = bar_time,
        )

        # 用後續 K 棒模擬結果
        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future)

        if trade.result in ("", "OPEN"):
            continue

        balance += trade.net_pnl
        trades.append(trade)

        # 止損後設冷卻期
        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    return trades


# ── 主程式 ───────────────────────────────────────────────────────
def main():
    global INITIAL_BALANCE, MIN_SCORE, BT_FIB_TOL, BT_VOL_MULT, BT_SKIP_VOL_RISE, BT_SKIP_BAD_FIB
    parser = argparse.ArgumentParser(description="裸K+Fib 策略回測")
    parser.add_argument("--symbol",         default=DEFAULT_SYMBOL, help="幣種，預設 BTCUSDT")
    parser.add_argument("--tf",             action="append",        help="時間框架，可多次指定")
    parser.add_argument("--months",         type=int,   default=DEFAULT_MONTHS,    help="回測月數")
    parser.add_argument("--balance",        type=float, default=INITIAL_BALANCE,   help="起始資金")
    parser.add_argument("--score",          type=int,   default=MIN_SCORE,         help="最低訊號強度")
    parser.add_argument("--fib-tol",        type=float, default=BT_FIB_TOL,        help=f"Fib 容忍度 (預設{BT_FIB_TOL}，真實bot=0.005)")
    parser.add_argument("--vol-mult",       type=float, default=BT_VOL_MULT,       help=f"成交量倍率門檻 (預設{BT_VOL_MULT}，真實bot=1.3)")
    parser.add_argument("--skip-vol-rise",  action="store_true", default=BT_SKIP_VOL_RISE,  help="不要求當根成交量>前根")
    parser.add_argument("--no-skip-bad-fib",action="store_true",                   help="包含低R:R的0.236/0.786位")
    args = parser.parse_args()

    timeframes = args.tf or DEFAULT_TF

    INITIAL_BALANCE   = args.balance
    MIN_SCORE         = args.score
    BT_FIB_TOL        = args.fib_tol
    BT_VOL_MULT       = args.vol_mult
    BT_SKIP_VOL_RISE  = args.skip_vol_rise
    BT_SKIP_BAD_FIB   = not args.no_skip_bad_fib

    testnet = os.getenv("BINANCE_TESTNET", "true") == "true"
    client = Client(
        os.getenv("BINANCE_API_KEY"),
        os.getenv("BINANCE_SECRET"),
        testnet=testnet,
    )

    print(f"\n{'=' * 60}")
    print(f"  裸K + Fib 策略回測")
    print(f"  幣種：{args.symbol}  時間框架：{timeframes}")
    print(f"  回測期間：最近 {args.months} 個月")
    print(f"  起始資金：{args.balance} USDT  最低訊號強度：{args.score}")
    print(f"  槓桿：{LEVERAGE}x  每單風險：{RISK_PER_TRADE*100:.1f}%")
    print(f"  Fib 容忍度：±{BT_FIB_TOL*100:.1f}%  成交量門檻：{BT_VOL_MULT}x")
    print(f"  跳過量增要求：{BT_SKIP_VOL_RISE}  跳過低R:R Fib：{BT_SKIP_BAD_FIB}")
    print(f"{'=' * 60}")

    all_trades = []
    for tf in timeframes:
        trades = run_backtest(client, args.symbol, tf, args.months)
        print_stats(trades, tf, args.symbol, args.balance)
        all_trades.extend(trades)

    # 如果測了多個時間框架，印合併統計
    if len(timeframes) > 1 and all_trades:
        print_stats(all_trades, "+".join(timeframes), args.symbol, args.balance)


if __name__ == "__main__":
    main()
