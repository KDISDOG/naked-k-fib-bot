"""
Binance Futures K 線 WebSocket 管理器

設計目的：取代原本「每 5 分鐘輪詢 check_signals」的排程式訊號檢查，
改為 K 線收盤事件驅動，訊號延遲從 0–5 分鐘降到 < 2 秒。

架構：
  - ThreadedWebsocketManager（python-binance）管理 WS 連線與自動重連
  - 訂閱 <symbol_lower>@kline_<interval> 串流
  - 收到 k.x == True（K 線已收盤）事件時，把 (symbol, interval, close_time)
    推進 queue.Queue，由 bot_main.py 的 worker thread 消費
  - reconcile(target) 依最新候選幣 × 策略 timeframe 動態增減訂閱

容錯：
  - WS 斷線由 ThreadedWebsocketManager 內建 reconnect 處理
  - bot_main.py 另保留 schedule 每 15 分跑一次 check_signals 當兜底
"""
import logging
import threading
import queue
from typing import Iterable, Optional

from binance import ThreadedWebsocketManager

log = logging.getLogger("kline_ws")


class KlineWSManager:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        event_queue: Optional[queue.Queue] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        # queue 存放 (symbol, interval, close_time_ms) 三元組
        self.queue: queue.Queue = event_queue or queue.Queue(maxsize=10_000)

        self._twm: Optional[ThreadedWebsocketManager] = None
        # stream_name → conn_key（python-binance 回傳的 socket handle）
        self._subs: dict[str, str] = {}
        self._lock = threading.RLock()
        self._started = False

    # ── 生命週期 ────────────────────────────────────────────────
    def start(self):
        if self._started:
            return
        self._twm = ThreadedWebsocketManager(
            api_key=self._api_key,
            api_secret=self._api_secret,
            testnet=self._testnet,
        )
        self._twm.start()
        self._started = True
        log.info("KlineWSManager 已啟動")

    def stop(self):
        if not self._started or not self._twm:
            return
        try:
            with self._lock:
                self._subs.clear()
            self._twm.stop()
        except Exception as e:
            log.warning(f"停止 WS 時發生例外: {e}")
        finally:
            self._started = False
            self._twm = None
            log.info("KlineWSManager 已停止")

    # ── 訂閱管理 ────────────────────────────────────────────────
    @staticmethod
    def _stream_name(symbol: str, interval: str) -> str:
        return f"{symbol.lower()}@kline_{interval}"

    def reconcile(self, target: Iterable[tuple[str, str]]):
        """
        讓當前訂閱集合等於 target。

        target: iterable of (symbol, interval) tuples
          例如：{("BTCUSDT", "1h"), ("ETHUSDT", "15m")}
        """
        if not self._started:
            self.start()
        assert self._twm is not None

        target_streams = {self._stream_name(s, i): (s, i) for s, i in target}

        with self._lock:
            current = set(self._subs.keys())
            wanted = set(target_streams.keys())
            to_add = wanted - current
            to_remove = current - wanted

            # 取消多餘訂閱
            for name in to_remove:
                conn_key = self._subs.pop(name, None)
                if not conn_key:
                    continue
                try:
                    self._twm.stop_socket(conn_key)
                except Exception as e:
                    log.debug(f"取消訂閱 {name} 失敗（忽略）: {e}")

            # 新增訂閱
            added_ok = 0
            for name in to_add:
                sym, interval = target_streams[name]
                try:
                    conn_key = self._twm.start_kline_futures_socket(
                        callback=self._on_message,
                        symbol=sym.upper(),
                        interval=interval,
                    )
                    self._subs[name] = conn_key
                    added_ok += 1
                except Exception as e:
                    log.error(f"訂閱 {name} 失敗: {e}")

        if to_add or to_remove:
            log.info(
                f"WS 訂閱同步：+{added_ok}/{len(to_add)} "
                f"-{len(to_remove)} 總計 {len(self._subs)}"
            )

    def subscribed_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # ── 訊息處理 ────────────────────────────────────────────────
    def _on_message(self, msg: dict):
        """
        單一 stream 回呼格式：
          {
            "e": "kline", "E": <event_time>, "s": "BTCUSDT",
            "k": {
              "t": start_time, "T": close_time, "s": symbol, "i": interval,
              ..., "x": is_closed, ...
            }
          }
        只有 k.x == True（K 棒已收盤）才推進 queue。
        """
        try:
            if not isinstance(msg, dict):
                return
            if msg.get("e") == "error":
                log.error(f"WS 回傳錯誤: {msg}")
                return

            k = msg.get("k")
            # 兼容 combined stream 格式
            if k is None and isinstance(msg.get("data"), dict):
                k = msg["data"].get("k")
            if not isinstance(k, dict):
                return
            if not k.get("x"):
                return

            symbol = msg.get("s") or k.get("s") or ""
            interval = k.get("i") or ""
            close_time = int(k.get("T") or 0)
            if not symbol or not interval:
                return

            try:
                self.queue.put_nowait((symbol, interval, close_time))
            except queue.Full:
                log.warning(
                    f"WS 事件 queue 已滿，丟棄 {symbol}@{interval}"
                )
        except Exception as e:
            log.error(f"處理 WS 訊息失敗: {e}")
