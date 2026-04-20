"""
naked_k_fib.py — 裸K + Fibonacci 策略（v4 邏輯的 BaseStrategy 包裝）

這個檔案將原本的 CoinScreener + SignalEngine 包裝成策略插件介面，
讓 bot_main.py 可以用統一的方式調度它。
"""
import logging
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.nkf")


class NakedKFibStrategy(BaseStrategy):
    """裸K + Fib 策略 — 包裝 SignalEngine + CoinScreener"""

    def __init__(self, client: Client, market_ctx=None,
                 coin_screener=None, signal_engine=None):
        """
        接受已初始化的 CoinScreener 和 SignalEngine，
        避免重複建立 Binance client。
        """
        self._client     = client
        self._market_ctx = market_ctx
        self._screener   = coin_screener
        self._engine     = signal_engine

        # 若未傳入，自行建立（保持向下相容）
        if self._screener is None:
            from coin_screener import CoinScreener
            self._screener = CoinScreener(client, market_ctx=market_ctx)
        if self._engine is None:
            from signal_engine import SignalEngine
            self._engine = SignalEngine(client, market_ctx=market_ctx)

    @property
    def name(self) -> str:
        return "naked_k_fib"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.NKF_TIMEFRAME

    def screen_coins(self, candidates: List[str]) -> List[str]:
        """
        使用 CoinScreener 為 candidates 打分、取前 N 支。
        candidates 由 bot_main 統一產生（已過濾黑名單 + 新幣），
        避免各策略各自掃全市場造成重複 API 呼叫與過濾邏輯漂移。
        """
        from config import Config
        if not candidates:
            log.warning("NKF 收到空 candidates，略過選幣")
            return []
        try:
            return self._screener.scan(
                top=20,
                min_score=Config.SCREEN_MIN_SCORE,
                symbols_override=candidates,
            )
        except Exception as e:
            log.error(f"NKF 選幣失敗: {e}")
            return []

    def check_signal(self, symbol: str) -> Optional[Signal]:
        """呼叫 SignalEngine.check() 並轉換為統一 Signal 格式"""
        from config import Config
        try:
            raw = self._engine.check(symbol, timeframe=self.default_timeframe)
        except Exception as e:
            log.warning(f"[{symbol}] NKF 訊號檢查失敗: {e}")
            return None

        if raw is None:
            return None

        # 轉換 SignalEngine 的 Signal → 共用 Signal dataclass
        sig = Signal(
            symbol        = raw.symbol,
            side          = raw.direction,       # LONG / SHORT
            entry_price   = raw.entry,
            stop_loss     = raw.sl,
            take_profit_1 = raw.tp1,
            take_profit_2 = raw.tp2,
            score         = raw.score,
            strategy_name = self.name,
            timeframe     = raw.timeframe,
            fib_level     = raw.fib_level,
            pattern       = raw.pattern,
            use_trailing  = raw.use_trailing,
            trailing_atr  = raw.trailing_atr,
            btc_corr      = raw.btc_corr,
            metadata      = {
                "fib_level":    raw.fib_level,
                "pattern":      raw.pattern,
                "swing_high":   raw.swing_high,
                "swing_low":    raw.swing_low,
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] NKF 訊號 TP/SL 不合理，捨棄")
            return None

        return sig
