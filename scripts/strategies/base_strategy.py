"""
base_strategy.py — 策略抽象基類

所有策略都必須繼承 BaseStrategy 並實現抽象方法。
Signal dataclass 是所有策略共用的訊號輸出格式。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Signal:
    """所有策略共用的訊號輸出格式"""
    symbol:        str
    side:          str             # "LONG" 或 "SHORT"
    entry_price:   float
    stop_loss:     float
    take_profit_1: float
    take_profit_2: float
    score:         int             # 訊號強度（1-10）
    strategy_name: str             # 策略標識
    timeframe:     str             # 進場時間框架
    metadata:      dict = field(default_factory=dict)
    # 追蹤止盈
    use_trailing:  bool  = False
    trailing_atr:  float = 0.0
    # 其他附帶資訊（兼容 NKF 欄位）
    fib_level:     str   = ""
    pattern:       str   = ""
    btc_corr:      float = 0.0


class BaseStrategy(ABC):
    """策略抽象基類 — 所有策略都必須實現這些方法"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱，用於 DB 記錄和 Dashboard 顯示"""

    @property
    @abstractmethod
    def default_timeframe(self) -> str:
        """預設進場時間框架"""

    @abstractmethod
    def screen_coins(self, candidates: List[str]) -> List[str]:
        """
        從候選幣種中篩選適合本策略的幣。
        輸入：候選 symbol 列表（如 ['BTCUSDT', 'ETHUSDT', ...]）
        輸出：篩選後的 symbol 列表
        """

    @abstractmethod
    def check_signal(self, symbol: str) -> Optional[Signal]:
        """
        檢查特定幣種是否有進場訊號。
        輸出：Signal 物件（有訊號）或 None（無訊號）
        """

    def validate_signal(self, signal: Signal) -> bool:
        """通用訊號驗證（TP/SL 方向合理性）"""
        if signal.side == "LONG":
            return signal.take_profit_1 > signal.entry_price > signal.stop_loss
        else:
            return signal.take_profit_1 < signal.entry_price < signal.stop_loss
