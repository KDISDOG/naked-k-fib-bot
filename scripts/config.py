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
    MARGIN_USDT       = float(os.getenv("MARGIN_USDT", 50.0))  # 單筆保證金上限（USDT），實際保證金由 RISK_PCT 決定
    MAX_POSITIONS     = int(os.getenv("MAX_POSITIONS", 6))     # 同時最多 6 倉
    # 單邊上限：預設等於 MAX_POSITIONS，代表不額外限制同方向倉位數
    # （僅受總倉位上限約束）。若要啟用單邊限制，設 MAX_LONGS/MAX_SHORTS 環境變數
    MAX_LONGS         = int(os.getenv("MAX_LONGS", MAX_POSITIONS))
    MAX_SHORTS        = int(os.getenv("MAX_SHORTS", MAX_POSITIONS))
    COOLDOWN_BARS     = int(os.getenv("COOLDOWN_BARS", 6))
    MAX_DAILY_LOSS    = float(os.getenv("MAX_DAILY_LOSS", 0.08))

    # Risk-based sizing：每筆最多虧損「MARGIN_USDT × RISK_PCT_PER_TRADE」
    # 用固定保證金的百分比作 base（非總餘額），因為用戶保證金是固定的。
    # 反推 qty → 高波動幣種 SL 距離大 → 倉位自動變小；MARGIN_USDT 做為上限。
    # 預設 10% = MARGIN_USDT × 0.10 為每筆最大損失；設 0 則退回純固定保證金邏輯。
    RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.10))

    # ── 策略專屬 R:R 門檻（per-strategy）────────────────────────
    NKF_MIN_RR = float(os.getenv("NKF_MIN_RR", 1.2))
    # MR 從 1.05 提到 1.2：1.05 毛 RR 扣完 round-trip 手續費後 net ≈ 1，
    # 長期勝率要 >55% 才不賠。用 1.2 維持合理安全邊際。
    MR_MIN_RR  = float(os.getenv("MR_MIN_RR", 1.2))

    # ── 手續費率（VIP 等級不同可自訂，留 env override）──────────
    TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", 0.0004))  # 0.04%
    MAKER_FEE_RATE = float(os.getenv("MAKER_FEE_RATE", 0.0002))  # 0.02%

    # ── 排程 ─────────────────────────────────────────────────────
    RESCAN_MIN       = int(os.getenv("RESCAN_MIN", 15))
    SIGNAL_CHECK_MIN = int(os.getenv("SIGNAL_CHECK_MIN", 5))
    SYNC_SEC         = int(os.getenv("SYNC_SEC", 30))

    # WS 靜默重建門檻（秒）：超過此秒數沒收到任何 K 線 tick 則重建連線。
    # Multiplex 連線即使全是 1h timeframe 也應每幾秒收到未收盤 tick；
    # 5 分鐘無事件 = 連線必然斷了。
    WS_SILENCE_RESET_SEC = float(os.getenv("WS_SILENCE_RESET_SEC", 300))

    # Dust 自動關閉門檻（USDT）：trailing/TP 平倉後殘留 qty 因幣安 stepSize
    # 截斷而清不掉時，若殘留名目 < 此值視為實質已平倉，自動 close_trade。
    # 預設 1 USDT = 殘留價值 < $1 直接收掉。
    DUST_CLOSE_NOTIONAL = float(os.getenv("DUST_CLOSE_NOTIONAL", 1.0))

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

    # ── 選幣層相對強弱濾網（v3 新增）────────────────────────────
    # 所有策略的 screen_coins 階段都會檢查「個幣 24h 漲跌 vs BTC 24h」差值：
    #   - CoinScreener (NKF)：方向感知加權，swing=up 要強、swing=down 要弱
    #   - BreakdownShort：做空方向，要求跑輸 BTC
    #   - MomentumLong：做多方向，要求跑贏 BTC（signal 層 ML_REL_STRENGTH 再擋一層）
    SCREEN_REL_STRENGTH_ENABLED   = os.getenv(
        "SCREEN_REL_STRENGTH_ENABLED", "true"
    ).lower() == "true"
    SCREEN_REL_STRENGTH_MIN_DIFF  = float(
        os.getenv("SCREEN_REL_STRENGTH_MIN_DIFF", 1.0)  # % 單位
    )

    # ── 選幣後相關性去重（v3 新增）─────────────────────────────
    # 候選幣 1h 收盤相關係數 > SCREEN_CORR_THRESHOLD 時，保留排序較前者，
    # 後位的同板塊幣剔除，避免 top 清單全部押同一風險因子。
    SCREEN_CORR_DEDUPE_ENABLED = os.getenv(
        "SCREEN_CORR_DEDUPE_ENABLED", "true"
    ).lower() == "true"
    SCREEN_CORR_THRESHOLD      = float(os.getenv("SCREEN_CORR_THRESHOLD", 0.85))

    # ── 進場前快檢查（v3 新增）─────────────────────────────────
    # 選幣→進場之間可能隔數分鐘到十幾分鐘，進場前用輕量 API 再查：
    #   - funding rate 沒飆極端（|fr| > 0.15%/8h）
    #   - 相對強弱方向仍與進場方向匹配
    #   - mark price 沒偏離訊號 entry 太多（v4 新增，擋市價單滑價爆倉）
    PRE_ENTRY_RECHECK_ENABLED = os.getenv(
        "PRE_ENTRY_RECHECK_ENABLED", "true"
    ).lower() == "true"
    # 訊號→執行之間若 mark price 已偏離訊號 entry 超過此比例，跳過該訊號
    # 避免市價單在薄市 / 急行情中滑價 3%+ 觸發 order_executor 的緊急平倉
    PRE_ENTRY_MAX_MARK_DEVIATION = float(
        os.getenv("PRE_ENTRY_MAX_MARK_DEVIATION", 0.005)  # 0.5%
    )

    # 成交後偏離門檻：fill_price 偏離訊號 entry 超過此比例就緊急平倉放棄。
    # 3% 太寬、只擋得住閃崩；1.5% 可吸收新幣市價單 1-2% 正常滑價，
    # 但擋住成交已穿越 SL / TP 等結構壞掉的情況。
    MAX_FILL_SLIP = float(os.getenv("MAX_FILL_SLIP", 0.015))  # 1.5%

    # 新幣過濾天數（onboardDate < 此天數者不進候選池）
    # 新幣前 30-60 天流動性差、MM 操縱風險高、K 線結構不穩
    NEW_COIN_MIN_DAYS = int(os.getenv("NEW_COIN_MIN_DAYS", 60))

    # ── 選幣層硬流動性門檻（v4 新增）─────────────────────────────
    # 24h USDT 成交量低於此值直接排除，不進 scoring。
    # 原先流動性只給「分數」不是硬門檻 → 薄流動性幣進榜 → 市價單滑價 5%+ → 放棄開倉。
    # 預設 50M（配合原本 _score_liquidity 的 +1 分級）。設 0 關閉硬門檻。
    SCREEN_MIN_QAV_24H = float(
        os.getenv("SCREEN_MIN_QAV_24H", 50_000_000)
    )

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
    MR_MIN_SCORE     = int(os.getenv("MR_MIN_SCORE", 2))   # 2 為實務運作門檻：_score_signal 的 RSI bonus 門檻(≤15/≥85)配
                                                            # OVERSOLD=30/OVERBOUGHT=70 時永遠摸不到，score 實際只會落在 2-3，
                                                            # 拉到 4 會直接把 MR 關掉；DB 證據 score=2 期望值為正 (+8.84/單)
    MR_VOL_MULT      = float(os.getenv("MR_VOL_MULT", 0.9))   # 均值回歸：要求縮量確認（賣盤衰竭），避免放量急跌時接刀
    MR_TIMEOUT_BARS  = int(os.getenv("MR_TIMEOUT_BARS", 20))  # 超時 K 棒數

    # ── Market Regime Gate ─────────────────────────────────────
    # BTC 4h ADX + 日線 MA50 判斷大盤型態（TREND_UP / TREND_DOWN /
    # RANGE / CHOPPY），讓策略只在合適型態下放行：
    #   TREND_UP   → momentum_long
    #   TREND_DOWN → breakdown_short
    #   RANGE      → mean_reversion
    #   CHOPPY     → 三者全擋；naked_k_fib 不受影響（內部自有多空過濾）
    REGIME_GATE_ENABLED = os.getenv("REGIME_GATE_ENABLED", "true").lower() == "true"

    # ── 追蹤止盈（Trailing Stop）────────────────────────────────
    # 總開關：關閉後純靠 SL/TP1/TP2 + 保本一次性移動。避免 30 秒推進
    # 造成孤兒單累積 / 掛單爆量。
    # 預設 true：ML/BD 這類強趨勢突破策略能吃到完整波段；
    # 各策略仍有 per-strategy 旗標，可個別關閉。
    TRAILING_ENABLED = os.getenv("TRAILING_ENABLED", "true").lower() == "true"

    # Per-strategy 追蹤止盈開關（需 TRAILING_ENABLED=true 才生效）
    TRAILING_ML_ENABLED  = os.getenv("TRAILING_ML_ENABLED",  "true").lower()  == "true"
    TRAILING_BD_ENABLED  = os.getenv("TRAILING_BD_ENABLED",  "true").lower()  == "true"
    TRAILING_NKF_ENABLED = os.getenv("TRAILING_NKF_ENABLED", "false").lower() == "true"
    TRAILING_MR_ENABLED  = os.getenv("TRAILING_MR_ENABLED",  "false").lower() == "true"
    TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", 1.5))  # 追蹤距離 = N × ATR
    TRAILING_ACTIVATE_AFTER_TP1 = os.getenv(
        "TRAILING_ACTIVATE_AFTER_TP1", "true"
    ).lower() == "true"  # TP1 成交後自動啟用追蹤止盈（需 TRAILING_ENABLED=true）
    # 最小推進步長：SL 至少前進 N × ATR 才換單，避免每 30 秒重下單（瘋狂開委託）
    TRAILING_MIN_STEP_ATR = float(os.getenv("TRAILING_MIN_STEP_ATR", 0.3))

    # ── Breakdown Short 策略（熊市做空）──────────────────────────
    BD_TIMEFRAME     = os.getenv("BD_TIMEFRAME", "1h")
    BD_ADX_MIN       = float(os.getenv("BD_ADX_MIN", 25))
    BD_ADX_MAX       = float(os.getenv("BD_ADX_MAX", 50))
    # BD_ADX_EXTREME：25-50 是 2 分甜蜜區、50-EXTREME 降為 1 分但不淘汰
    # 做空在強下跌動能（ADX 50-65）反而受益，不應被 Screener 一刀排除
    BD_ADX_EXTREME   = float(os.getenv("BD_ADX_EXTREME", 65))
    BD_LOOKBACK_BARS = int(os.getenv("BD_LOOKBACK_BARS", 20))    # 支撐突破回看根數
    BD_VOL_MULT      = float(os.getenv("BD_VOL_MULT", 1.3))     # 突破量確認倍數
    BD_SL_ATR_MULT   = float(os.getenv("BD_SL_ATR_MULT", 1.0))  # SL = 突破點 + N×ATR
    BD_MIN_SCORE     = int(os.getenv("BD_MIN_SCORE", 3))         # 最低訊號評分
    BD_TIMEOUT_BARS  = int(os.getenv("BD_TIMEOUT_BARS", 24))     # 超時平倉根數（15m×24=6h）
    BD_MIN_RR        = float(os.getenv("BD_MIN_RR", 1.2))        # 最低 R:R
    BD_MAX_POSITIONS = int(os.getenv("BD_MAX_POSITIONS", 2))      # 最大持倉數
    # TP2 Fib extension 延伸倍率（相對 swing diff）：
    #   0.272 → 1.272 extension（保守，命中率最高）
    #   0.382 → 1.382 extension（平衡，預設）
    #   0.618 → 1.618 extension（激進，教科書目標但命中率 30-40%）
    BD_TP2_FIB_MULT  = float(os.getenv("BD_TP2_FIB_MULT", 0.382))

    # ── Momentum Breakout Long 策略（牛市做多）───────────────────
    ML_TIMEFRAME     = os.getenv("ML_TIMEFRAME", "1h")
    ML_ADX_MIN       = float(os.getenv("ML_ADX_MIN", 25))
    ML_ADX_MAX       = float(os.getenv("ML_ADX_MAX", 50))
    # ML_ADX_EXTREME：25-50 是 2 分甜蜜區、50-EXTREME 降為 1 分但不淘汰
    # 做多在強動能突破（ADX 50-65）仍可做，但過熱不再加到 2 分
    ML_ADX_EXTREME   = float(os.getenv("ML_ADX_EXTREME", 65))
    ML_LOOKBACK_BARS = int(os.getenv("ML_LOOKBACK_BARS", 20))    # 阻力突破回看根數
    ML_VOL_MULT      = float(os.getenv("ML_VOL_MULT", 1.3))     # 突破量確認倍數
    ML_SL_ATR_MULT   = float(os.getenv("ML_SL_ATR_MULT", 1.0))  # SL = 突破點 - N×ATR
    ML_MIN_SCORE     = int(os.getenv("ML_MIN_SCORE", 3))         # 最低訊號評分
    ML_TIMEOUT_BARS  = int(os.getenv("ML_TIMEOUT_BARS", 24))     # 超時平倉根數（15m×24=6h）
    ML_MIN_RR        = float(os.getenv("ML_MIN_RR", 1.2))        # 最低 R:R
    ML_MAX_POSITIONS = int(os.getenv("ML_MAX_POSITIONS", 2))      # 最大持倉數
    # TP2 Fib extension 延伸倍率（相對 swing diff）：
    #   0.272 → 1.272 extension（保守，命中率最高）
    #   0.382 → 1.382 extension（平衡，預設）
    #   0.618 → 1.618 extension（激進，教科書目標但命中率 30-40%）
    ML_TP2_FIB_MULT  = float(os.getenv("ML_TP2_FIB_MULT", 0.382))

    # ── ML 實證調校（B1 + B3，基於 DB 回測：score=5 WR 14%） ─────
    # B1 相對強度過濾：個幣 24h 漲幅必須強於 BTC 至少 N% 才做多
    # 理由：alt 連 BTC 都打不過時，做多它沒有相對強度支撐，容易被震盪洗
    ML_REL_STRENGTH_ENABLED = os.getenv("ML_REL_STRENGTH_ENABLED", "true").lower() == "true"
    ML_REL_STRENGTH_MIN_DIFF = float(os.getenv("ML_REL_STRENGTH_MIN_DIFF", 1.0))  # % 單位

    # BD 相對弱勢硬門檻（v5）：個幣 24h 必須跑輸 BTC ≥ MIN_DIFF % 才能做空
    # 對應 ML 的 hard block，讓 BD 也只在「相對弱勢」幣做空（不在強勢幣逆勢）
    BD_REL_STRENGTH_ENABLED  = os.getenv("BD_REL_STRENGTH_ENABLED", "true").lower() == "true"
    BD_REL_STRENGTH_MIN_DIFF = float(os.getenv("BD_REL_STRENGTH_MIN_DIFF", 1.0))  # % 單位

    # MR 結構性確認（v5）：避免單純 RSI 觸發在無 S/R 區的雜訊位
    # 兩道過濾：
    #   1. _has_rsi_divergence：價格 LL/HH + RSI HL/LH（≥2 點 RSI 差）
    #   2. _has_sr_test：當前 close 接近最近 swing low/high（±tolerance）
    # MR_STRUCTURAL_REQUIRED：需通過幾道才放行
    #   0 = 都不擋（純 RSI/BB 觸發）
    #   1 = 至少一道（默認、推薦）— 過濾雜訊但保留訊號量
    #   2 = 兩道都要（嚴格、可能訊號太少）
    MR_REQUIRE_DIVERGENCE  = os.getenv("MR_REQUIRE_DIVERGENCE", "true").lower() == "true"
    MR_REQUIRE_SR_TEST     = os.getenv("MR_REQUIRE_SR_TEST", "true").lower() == "true"
    MR_STRUCTURAL_REQUIRED = int(os.getenv("MR_STRUCTURAL_REQUIRED", 1))  # 0/1/2
    MR_DIV_LOOKBACK        = int(os.getenv("MR_DIV_LOOKBACK", 20))   # 找 swing low/high 的回看根數
    MR_SR_LOOKBACK         = int(os.getenv("MR_SR_LOOKBACK", 30))    # 找關鍵 S/R 的回看根數
    MR_SR_TOLERANCE        = float(os.getenv("MR_SR_TOLERANCE", 0.015))  # 1.5% 容忍貼合度

    # ── SMC（Smart Money Concepts）Liquidity Sweep + Reversal ─────
    # 取代 MR：在 swing high/low 被刺破後反轉時順機構方向開倉。
    # 多空雙向、與 BD/ML 互補（SMC 不依賴 trend regime）。
    SMC_TIMEFRAME       = os.getenv("SMC_TIMEFRAME", "1h")
    SMC_SWING_LOOKBACK  = int(os.getenv("SMC_SWING_LOOKBACK", 50))   # 找 swing 的回看根數
    SMC_SWING_LEFT      = int(os.getenv("SMC_SWING_LEFT", 3))        # fractal 左側確認根數
    SMC_SWING_RIGHT     = int(os.getenv("SMC_SWING_RIGHT", 3))       # fractal 右側確認根數
    SMC_SWEEP_MIN_PCT   = float(os.getenv("SMC_SWEEP_MIN_PCT", 0.001))  # 0.1% 最小刺破
    SMC_SWEEP_MAX_PCT   = float(os.getenv("SMC_SWEEP_MAX_PCT", 0.020))  # 2% 最大（防止破壞性下跌假認為 sweep）
    SMC_VOL_MULT        = float(os.getenv("SMC_VOL_MULT", 1.3))       # 入場 K 棒量能下限倍數
    SMC_SL_BUFFER       = float(os.getenv("SMC_SL_BUFFER", 0.5))      # SL 在刺破點外側 0.5×ATR
    SMC_MIN_SCORE       = int(os.getenv("SMC_MIN_SCORE", 3))          # 最低訊號評分
    SMC_TIMEOUT_BARS    = int(os.getenv("SMC_TIMEOUT_BARS", 24))      # 1h × 24 = 24h（v2：12→24，給 sweep 反轉時間）
    SMC_MIN_RR          = float(os.getenv("SMC_MIN_RR", 1.5))         # 最低 R:R
    # SMC v3 HTF 趨勢過濾：4h EMA50 必須與交易方向同向
    # 過濾掉 BTC/SOL/HYPE/DOGE 在 4h chop 期間的雜訊 sweep
    SMC_HTF_FILTER_ENABLED = os.getenv("SMC_HTF_FILTER_ENABLED", "true").lower() == "true"
    SMC_HTF_TIMEFRAME      = os.getenv("SMC_HTF_TIMEFRAME", "4h")
    SMC_HTF_EMA_PERIOD     = int(os.getenv("SMC_HTF_EMA_PERIOD", 50))

    # SMC v4 嚴 HTF：除了「同方向」還要求離 EMA 至少 X%（避免貼著 EMA 的 chop）
    SMC_HTF_MIN_DISTANCE_PCT = float(
        os.getenv("SMC_HTF_MIN_DISTANCE_PCT", 0.005)  # 0.5%
    )
    # SMC v4 排除清單：個別幣 SL 平均虧太深（gap risk / 流動性差）
    # 預設只擋 HYPEUSDT（回測 8 單 50% win 但 worst -3.37 = 33% margin）
    SMC_EXCLUDED_SYMBOLS = os.getenv("SMC_EXCLUDED_SYMBOLS", "HYPEUSDT")

    # SMC v5 HTF 斜率過濾：4h EMA50 必須正在朝交易方向移動
    # 「close > EMA + 0.5%」還可能在 chop 反彈中；要 EMA 本身也在上升才是真趨勢
    SMC_HTF_REQUIRE_SLOPE = os.getenv("SMC_HTF_REQUIRE_SLOPE", "true").lower() == "true"
    SMC_HTF_SLOPE_BARS    = int(os.getenv("SMC_HTF_SLOPE_BARS", 5))    # 算斜率的回看根數
    # B3 高 Score 反轉：實證 score=5 時 WR=14.3%（反指標，視為過熱頂部）
    # 預設擋 score >= 5 的訊號，只放行 score ∈ [ML_MIN_SCORE, ML_MAX_SCORE]
    ML_MAX_SCORE     = int(os.getenv("ML_MAX_SCORE", 4))         # 過熱上限

    # ── OI 異常過濾 ──────────────────────────────────────────────
    # OI 24h 變動 > N% 視為異常（大戶佈局，技術面易失效）
    # 20%→30%：小市值幣天然 OI 波動大，20% 容易誤殺；30% 是更合理的「明顯大戶動作」門檻
    OI_CHANGE_MAX = float(os.getenv("OI_CHANGE_MAX", 30.0))

    # ── Symbol 黑名單：穩定幣 / 包裝幣 / 指數類（避免誤開倉）────
    EXCLUDED_SYMBOLS = {
        "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT",
        "DAIUSDT", "PAXUSDT", "USTUSDT", "USTCUSDT",
        "BTCDOMUSDT", "DEFIUSDT",  # 指數類合約
    }
    # 名稱含這些 token 的槓桿代幣（幣安期貨已下架多數，但保險起見）
    EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

    @classmethod
    def is_excluded_symbol(cls, symbol: str) -> bool:
        """檢查 symbol 是否在黑名單中"""
        if symbol in cls.EXCLUDED_SYMBOLS:
            return True
        return any(symbol.endswith(s) for s in cls.EXCLUDED_SUFFIXES)
