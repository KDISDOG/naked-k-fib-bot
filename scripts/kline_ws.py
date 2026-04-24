"""
Binance Futures K 線 WebSocket 管理器（multiplex 版）

設計目的：取代原本「每 5 分鐘輪詢 check_signals」的排程式訊號檢查，
改為 K 線收盤事件驅動，訊號延遲從 0–5 分鐘降到 < 2 秒。

架構：
  - ThreadedWebsocketManager（python-binance）管理 WS 連線與自動重連
  - 使用 futures_multiplex_socket：把所有 <symbol>@kline_<interval> 串流
    塞進單一 WS 連線，避免 N 支幣 = N 條並發 handshake 在薄網路下超時
  - 收到 k.x == True（K 線已收盤）事件時，把 (symbol, interval, close_time)
    推進 queue.Queue，由 bot_main.py 的 worker thread 消費
  - reconcile(target) 若目標串流集合變動，重連 multiplex socket（每次掃幣
    後呼叫，頻率低、成本可接受）

容錯：
  - WS 斷線由 ThreadedWebsocketManager 內建 reconnect 處理
  - handshake timeout 時整個 socket 會被 TWM 丟棄並重試；bot_main.py 另
    保留 schedule 每 15 分跑 check_signals 當兜底
"""
import logging
import threading
import queue
import time
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
        # 當前 multiplex socket 的 conn_key（單一）與訂閱的 stream 集合
        self._conn_key: Optional[str] = None
        self._streams: set[str] = set()
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
                if self._conn_key:
                    try:
                        self._twm.stop_socket(self._conn_key)
                    except Exception as e:
                        log.debug(f"停止 multiplex socket 失敗（忽略）: {e}")
                    self._conn_key = None
                self._streams.clear()
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
        讓當前訂閱集合等於 target。multiplex socket 的限制：串流清單無法
        動態增減，所以清單變動時會重啟整條 socket。

        target: iterable of (symbol, interval) tuples
          例如：{("BTCUSDT", "1h"), ("ETHUSDT", "15m")}
        """
        if not self._started:
            self.start()
        assert self._twm is not None

        new_streams = {self._stream_name(s, i) for s, i in target}

        with self._lock:
            if new_streams == self._streams and self._conn_key:
                return  # 無變動
            added = new_streams - self._streams
            removed = self._streams - new_streams

            # 停掉舊 multiplex socket
            if self._conn_key:
                try:
                    self._twm.stop_socket(self._conn_key)
                except Exception as e:
                    log.debug(f"停止舊 socket 失敗（忽略）: {e}")
                self._conn_key = None

            # 小延遲避免 TWM 還沒清理完就開新的
            time.sleep(0.2)

            # 開新 multiplex socket（若目標是空集合就不開）
            if new_streams:
                try:
                    self._conn_key = self._twm.start_futures_multiplex_socket(
                        callback=self._on_message,
                        streams=sorted(new_streams),  # 排序讓 log 穩定
                    )
                    self._streams = new_streams
                    log.info(
                        f"WS multiplex 重建：+{len(added)} -{len(removed)} "
                        f"當前 {len(new_streams)} 條串流"
                    )
                except Exception as e:
                    log.error(f"啟動 multiplex socket 失敗: {e}")
                    self._streams = set()
            else:
                self._streams = set()
                log.info("WS 目標串流集合為空，暫不建立連線")

    def subscribed_count(self) -> int:
        with self._lock:
            return len(self._streams)

    # ── 訊息處理 ────────────────────────────────────────────────
    def _on_message(self, msg: dict):
        """
        multiplex 串流回呼格式：
          {
            "stream": "btcusdt@kline_1h",
            "data": {
              "e": "kline" / "continuous_kline",
              "E": event_time,
              "s": "BTCUSDT",
              "k": { "t": ..., "T": ..., "s": "BTCUSDT", "i": "1h",
                     ..., "x": is_closed, ... }
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

            # multiplex：資料在 msg["data"]，單一串流：直接在 msg
            data = msg.get("data") if isinstance(msg.get("data"), dict) else msg
            k = data.get("k") if isinstance(data, dict) else None
            if not isinstance(k, dict):
                return
            if not k.get("x"):
                return

            symbol = data.get("s") or k.get("s") or ""
            # continuous_kline 的 symbol 在 data["ps"]
            if not symbol:
                symbol = data.get("ps", "")
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
