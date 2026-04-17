"""
config.py — 統一 .env 配置管理（v5 多策略版）
所有模組從此處取得設定值，不再各自讀 os.getenv。
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── API ──────────────────────────────────────────────────────
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET  = os.getenv("BINANCE_SECRET", "")
    BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    # ── 通用風控 ─────────────────────────────────────────────────
    MAX_LEVERAGE      = int(os.getenv("MAX_LEVERAGE", 3))      # 固定 3x
    RISK_PER_TRADE    = float(os.getenv("RISK_PER_TRADE", 0.05))
    MAX_POSITIONS     = int(os.getenv("MAX_POSITIONS", 6))     # 同時最多 6 倉
    MAX_NOTIONAL_PCT  = float(os.getenv("MAX_NOTIONAL_PCT", round(1/6, 4)))  # 每筆保證金 ≈ 16.67%
    COOLDOWN_BARS     = int(os.getenv("COOLDOWN_BARS", 6))
    MAX_DAILY_LOSS    = float(os.getenv("MAX_DAILY_LOSS", 0.08))

    # ── 策略專屬 R:R 門檻（per-strategy）────────────────────────
    NKF_MIN_RR = float(os.getenv("NKF_MIN_RR", 1.2))
    MR_MIN_RR  = float(os.getenv("MR_MIN_RR", 1.05))

    # ── 排程 ─────────────────────────────────────────────────────
    RESCAN_MIN       = int(os.getenv("RESCAN_MIN", 15))
    SIGNAL_CHECK_MIN = int(os.getenv("SIGNAL_CHECK_MIN", 5))
    SYNC_SEC         = int(os.getenv("SYNC_SEC", 30))

    # ── 策略選擇 ─────────────────────────────────────────────────
    # 可選：naked_k_fib / mean_reversion / all
    ACTIVE_STRATEGY  = os.getenv("ACTIVE_STRATEGY", "all")

    # ── 資料庫 ───────────────────────────────────────────────────
    DB_PATH = os.getenv("DB_PATH", "bot_state.db")

    # ── 選幣參數（coin_screener）────────────────────────────────
    SCREEN_MIN_SCORE   = int(os.getenv("SCREEN_MIN_SCORE", 8))
    SCREEN_MIN_VOL_M   = float(os.getenv("SCREEN_MIN_VOL_M", 10))
    SCREEN_ADX_MIN     = float(os.getenv("SCREEN_ADX_MIN", 20))
    SCREEN_ADX_MAX     = float(os.getenv("SCREEN_ADX_MAX", 45))
    SCREEN_ATR_MAX_LONG  = float(os.getenv("SCREEN_ATR_MAX_LONG", 4.0))
    SCREEN_ATR_MAX_SHORT = float(os.getenv("SCREEN_ATR_MAX_SHORT", 8.0))

    # ── 裸K+Fib 入場參數（signal_engine）────────────────────────
    NKF_MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", 3))
    NKF_FIB_TOL          = float(os.getenv("SIGNAL_FIB_TOL", 0.005))
    NKF_VOL_RATIO        = float(os.getenv("SIGNAL_VOL_RATIO", 1.3))
    NKF_VOL_RISING       = os.getenv("SIGNAL_VOL_RISING", "true").lower() == "true"
    NKF_FIB_MAX_TOUCHES  = int(os.getenv("SIGNAL_FIB_MAX_TOUCHES", 1))
    NKF_FRACTAL_LR       = int(os.getenv("SIGNAL_FRACTAL_LR", 5))
    NKF_TIMEFRAME        = os.getenv("NKF_TIMEFRAME", "1h")

    # ── 方案 A：RSI 均值回歸參數 ─────────────────────────────────
    MR_RSI_PERIOD    = int(os.getenv("MR_RSI_PERIOD", 14))
    MR_RSI_OVERSOLD  = float(os.getenv("MR_RSI_OVERSOLD", 25))
    MR_RSI_OVERBOUGHT= float(os.getenv("MR_RSI_OVERBOUGHT", 75))
    MR_BB_PERIOD     = int(os.getenv("MR_BB_PERIOD", 20))
    MR_BB_STD        = float(os.getenv("MR_BB_STD", 2.0))
    MR_TIMEFRAME     = os.getenv("MR_TIMEFRAME", "15m")
    MR_TP_PCT        = float(os.getenv("MR_TP_PCT", 0.05))    # 5% 止盈
    MR_SL_PCT        = float(os.getenv("MR_SL_PCT", 0.025))   # 2.5% 止損
    MR_MIN_SCORE     = int(os.getenv("MR_MIN_SCORE", 3))
    MR_VOL_MULT      = float(os.getenv("MR_VOL_MULT", 1.2))   # 均值回歸：不要求極端縮量，允許小幅放量
    MR_TIMEOUT_BARS  = int(os.getenv("MR_TIMEOUT_BARS", 20))  # 超時 K 棒數

    # ── 追蹤止盈（Trailing Stop）────────────────────────────────
    TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", 1.5))  # 追蹤距離 = N × ATR
    TRAILING_ACTIVATE_AFTER_TP1 = os.getenv(
        "TRAILING_ACTIVATE_AFTER_TP1", "true"
    ).lower() == "true"  # TP1 成交後自動啟用追蹤止盈

    # ── Breakdown Short 策略（熊市做空）──────────────────────────
    BD_TIMEFRAME     = os.getenv("BD_TIMEFRAME", "1h")
    BD_ADX_MIN       = float(os.getenv("BD_ADX_MIN", 25))
    BD_ADX_MAX       = float(os.getenv("BD_ADX_MAX", 50))
    BD_LOOKBACK_BARS = int(os.getenv("BD_LOOKBACK_BARS", 20))    # 支撐突破回看根數
    BD_VOL_MULT      = float(os.getenv("BD_VOL_MULT", 1.3))     # 突破量確認倍數
    BD_SL_ATR_MULT   = float(os.getenv("BD_SL_ATR_MULT", 1.0))  # SL = 突破點 + N×ATR
    BD_MIN_SCORE     = int(os.getenv("BD_MIN_SCORE", 3))         # 最低訊號評分
    BD_TIMEOUT_BARS  = int(os.getenv("BD_TIMEOUT_BARS", 48))     # 超時平倉根數
    BD_MIN_RR        = float(os.getenv("BD_MIN_RR", 1.2))        # 最低 R:R
    BD_MAX_POSITIONS = int(os.getenv("BD_MAX_POSITIONS", 2))      # 最大持倉數

    # ── Momentum Breakout Long 策略（牛市做多）───────────────────
    ML_TIMEFRAME     = os.getenv("ML_TIMEFRAME", "1h")
    ML_ADX_MIN       = float(os.getenv("ML_ADX_MIN", 25))
    ML_ADX_MAX       = float(os.getenv("ML_ADX_MAX", 50))
    ML_LOOKBACK_BARS = int(os.getenv("ML_LOOKBACK_BARS", 20))    # 阻力突破回看根數
    ML_VOL_MULT      = float(os.getenv("ML_VOL_MULT", 1.3))     # 突破量確認倍數
    ML_SL_ATR_MULT   = float(os.getenv("ML_SL_ATR_MULT", 1.0))  # SL = 突破點 - N×ATR
    ML_MIN_SCORE     = int(os.getenv("ML_MIN_SCORE", 3))         # 最低訊號評分
    ML_TIMEOUT_BARS  = int(os.getenv("ML_TIMEOUT_BARS", 48))     # 超時平倉根數
    ML_MIN_RR        = float(os.getenv("ML_MIN_RR", 1.2))        # 最低 R:R
    ML_MAX_POSITIONS = int(os.getenv("ML_MAX_POSITIONS", 2))      # 最大持倉數

    # ── OI 異常過濾 ──────────────────────────────────────────────
    OI_CHANGE_MAX = float(os.getenv("OI_CHANGE_MAX", 20.0))      # OI 24h 變動 > N% 視為異常
