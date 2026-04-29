"""
API retry wrapper — 為所有幣安 API 呼叫加上指數退避重試

使用方式：
  from api_retry import retry_api, create_order_safe
  result = retry_api(client.futures_account)
  result = retry_api(client.futures_klines, symbol="BTCUSDT", interval="1h")

!!! 警告 !!!
  寫入類操作（futures_create_order / futures_cancel_order）禁止用 retry_api，
  因為網路超時時訂單可能已送達幣安，盲目重試會下重複單。請用：
    - create_order_safe()：附 clientOrderId，重試前先確認訂單存在與否

# WeightLimiter（process 全域）
  幣安 futures REST 上限 2400 weight/分鐘 / IP（不分 process）。
  回測 + live bot 同 IP 共享 quota，回測 burst 會把 live bot 一起 ban。
  WeightLimiter 用 deque 追蹤近 60 秒累積 weight，達上限 sleep 到視窗滑開。

  使用：
    from api_retry import limiter, weight_aware_call
    limiter.acquire(10)               # 顯式扣 weight
    raw = weight_aware_call(client.futures_klines, weight=10,
                            symbol="BTCUSDT", interval="1h", limit=1500)
    # weight_aware_call 會在打 API 前 acquire、回應後讀 X-MBX-USED-WEIGHT-1M
    # 自動回校真實 used；超 2000 自動退讓 30 秒。
"""
import time
import uuid
import logging
import threading
from collections import deque

log = logging.getLogger("api_retry")

# 預設重試 3 次，退避 1s → 2s → 4s
_MAX_RETRIES = 3
_BASE_DELAY = 1.0


# ── Weight Limiter（thread-safe，process 全域單例）─────────────
class WeightLimiter:
    """
    滑動視窗 rate limiter — 60 秒內累積 weight 不超過 max_weight。

    Binance futures REST 限制 2400 weight/分（IP）；預設 1800 留 25% 緩衝給
    burst error 重試與其他工具（dashboard / scan_coins / live signal check 等
    都共用這個 limiter，不會互相吃量）。
    """

    def __init__(self, max_weight: int = 1800, window: float = 60.0):
        self.max = int(max_weight)
        self.window = float(window)
        self._log: deque = deque()  # (ts, weight)
        self._lock = threading.Lock()
        # 真實 used 由 X-MBX-USED-WEIGHT-1M header 回校（雙保險）
        self._real_used: int = 0
        self._real_used_ts: float = 0.0

    def acquire(self, weight: int) -> None:
        """阻塞至少夠送 weight 個 unit 進視窗為止。"""
        while True:
            with self._lock:
                now = time.time()
                # 清掉視窗外
                while self._log and self._log[0][0] < now - self.window:
                    self._log.popleft()
                current = sum(w for _, w in self._log)
                # 真實 used header 校正：以兩者較大為準
                effective = max(current, self._real_used)
                if effective + weight <= self.max:
                    self._log.append((now, weight))
                    return
                # 視窗滿：算還要等多久
                if self._log:
                    sleep_for = self.window - (now - self._log[0][0]) + 0.5
                else:
                    sleep_for = 1.0
                sleep_for = max(sleep_for, 0.5)
            log.info(
                f"WeightLimiter 滿載（內部累積 {current} + 真實 used {self._real_used} "
                f"+ 本次 {weight} > {self.max}），sleep {sleep_for:.1f}s"
            )
            time.sleep(sleep_for)

    def report_header(self, used_weight_1m: int) -> None:
        """API 回應後從 X-MBX-USED-WEIGHT-1M 回校真實 used。"""
        with self._lock:
            self._real_used = int(used_weight_1m)
            self._real_used_ts = time.time()

    def used(self) -> tuple[int, int]:
        """回傳 (內部追蹤 weight, 真實 X-MBX-USED-WEIGHT-1M)"""
        with self._lock:
            now = time.time()
            while self._log and self._log[0][0] < now - self.window:
                self._log.popleft()
            internal = sum(w for _, w in self._log)
            # 真實 used 過 70 秒視為失效
            real = self._real_used if (now - self._real_used_ts) < 70 else 0
            return internal, real


# Process 全域單例（live bot + backtest + scan_coins 共用）
limiter = WeightLimiter(max_weight=1800, window=60.0)


# ── 包裝：自動 acquire + 讀 header 回校 ─────────────────────────
def weight_aware_call(client_method, *args, weight: int = 1,
                       used_header_threshold: int = 2000,
                       throttle_sleep: float = 30.0, **kwargs):
    """
    打 client_method(*args, **kwargs) 之前 acquire(weight)。
    打完後讀 client_method.__self__.response.headers['X-MBX-USED-WEIGHT-1M']
    若 > used_header_threshold 主動 sleep throttle_sleep 秒退讓，
    並把 limiter._real_used 校到該值。

    若 client 沒掛 .response（例如 binance-futures-connector）安靜略過 header。
    """
    limiter.acquire(weight)
    result = client_method(*args, **kwargs)

    try:
        # python-binance：client.response 是 requests.Response
        client_obj = getattr(client_method, "__self__", None)
        resp = getattr(client_obj, "response", None) if client_obj else None
        if resp is not None and getattr(resp, "headers", None):
            used = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", 0))
            if used > 0:
                limiter.report_header(used)
                if used > used_header_threshold:
                    log.warning(
                        f"X-MBX-USED-WEIGHT-1M={used} > {used_header_threshold}，"
                        f"主動 sleep {throttle_sleep}s 退讓"
                    )
                    time.sleep(throttle_sleep)
    except Exception as e:
        log.debug(f"讀 X-MBX-USED-WEIGHT 失敗（忽略）: {e}")

    return result


def klines_weight(limit: int) -> int:
    """
    futures_klines 的 weight 規則（依 Binance docs）：
      limit ≤ 100  : 1
      limit ≤ 500  : 2
      limit ≤ 1000 : 5
      limit ≤ 1500 : 10
    """
    if limit <= 100:
        return 1
    if limit <= 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


# ── exchange_info 1 小時 cache（symbol list 又不會 15 分鐘變）─────
_EXCHANGE_INFO_CACHE: dict = {"data": None, "ts": 0.0}
_EXCHANGE_INFO_TTL_SEC = 3600


def get_exchange_info_cached(client, ttl: int = _EXCHANGE_INFO_TTL_SEC) -> dict:
    """
    取得 futures_exchange_info 並 cache 1 小時。
    全 process 共用（live bot 各模組 + scan_coins + 多策略都打同一份）。
    """
    now = time.time()
    if (_EXCHANGE_INFO_CACHE["data"] is not None
            and now - _EXCHANGE_INFO_CACHE["ts"] < ttl):
        return _EXCHANGE_INFO_CACHE["data"]
    info = weight_aware_call(client.futures_exchange_info, weight=1)
    _EXCHANGE_INFO_CACHE["data"] = info
    _EXCHANGE_INFO_CACHE["ts"] = now
    return info


def retry_api(func, *args, max_retries: int = _MAX_RETRIES,
              base_delay: float = _BASE_DELAY, **kwargs):
    """
    呼叫 func(*args, **kwargs)，失敗時指數退避重試。
    最後一次仍失敗則 raise 原始例外。

    僅適用於「讀取」或「冪等」的 API（get/query/cancel_all 等）。
    禁用於 futures_create_order — 使用 create_order_safe 取代。
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(
                    f"API 呼叫失敗 ({attempt+1}/{max_retries+1}): {e} "
                    f"— {delay:.1f}s 後重試"
                )
                time.sleep(delay)
            else:
                log.error(
                    f"API 呼叫失敗，已重試 {max_retries} 次: {e}"
                )
    raise last_exc


def gen_client_order_id(prefix: str = "nkf") -> str:
    """產生唯一 clientOrderId（幣安限制 1~36 字元，含 a-zA-Z0-9-_.）"""
    # prefix + 短 uuid，保底在 36 字元內
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


class OrderRejectedError(Exception):
    """
    幣安回了 200 但 body 寫 REJECTED 時拋出。
    舊版 bot 不看 response 狀態，REJECTED 也當成功存 algoId，
    下次啟動才發現根本沒掛上（裸倉 bug）。
    """
    def __init__(self, symbol: str, response: dict):
        self.symbol = symbol
        self.response = response
        status = response.get("algoStatus") or response.get("status") or "?"
        reason = (response.get("failReason")
                  or response.get("rejectReason")
                  or response.get("msg") or "")
        msg = f"[{symbol}] order rejected by exchange: status={status}"
        if reason:
            msg += f" reason={reason}"
        super().__init__(msg)


def create_order_safe(client, symbol: str, max_retries: int = 2,
                      base_delay: float = 1.0, **params):
    """
    下單的冪等重試版本。
    流程：
      1. 產生唯一 newClientOrderId（若呼叫端沒給）
      2. 嘗試 futures_create_order
      3. 若失敗，先呼叫 futures_get_order(origClientOrderId=...)
         確認訂單是否已存在（網路超時但幣安已收單的情況）
         - 若存在 → 直接回傳既有訂單資訊（不重複下單）
         - 若不存在 → 才進入下一輪重試
      4. 全部失敗則 raise 最後一次例外
      5. 成功回傳前驗證 algoStatus / status 必須是 live 狀態，
         否則 raise OrderRejectedError（幣安對 algo order 會出現
         200 + algoStatus=REJECTED 的 silent failure，必須主動偵測）

    比 retry_api 安全：
      retry_api 會盲目重下 → 同一張單下 2、3 次（200-order limit 爆掉）
    """
    # 延後 import 避免循環（binance_orders 目前未 import 本模組，但留保險）
    from binance_orders import is_order_live

    # 呼叫端未指定 clientOrderId 才自動產生
    coid = params.pop("newClientOrderId", None) or gen_client_order_id()
    params["newClientOrderId"] = coid

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.futures_create_order(symbol=symbol, **params)
            # 下單「成功」但 body 回 REJECTED / EXPIRED → 當作失敗
            if not is_order_live(resp):
                raise OrderRejectedError(symbol, resp)
            return resp
        except OrderRejectedError:
            # 交易所拒絕不該重試（重試只會再被拒），直接往外拋
            raise
        except Exception as e:
            last_exc = e
            # 關鍵：先查幣安是否已經收了這張單
            try:
                existing = client.futures_get_order(
                    symbol=symbol, origClientOrderId=coid
                )
                if existing and existing.get("status") not in (
                    None, "", "EXPIRED", "CANCELED", "REJECTED"
                ):
                    log.warning(
                        f"[{symbol}] 下單疑似網路超時但已存在 "
                        f"(coid={coid}, status={existing.get('status')})，"
                        f"避免重複下單，回傳既有訂單"
                    )
                    return existing
            except Exception:
                # 查不到 → 幣安確實沒收，可以安全重試
                pass

            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(
                    f"[{symbol}] create_order 失敗 "
                    f"({attempt+1}/{max_retries+1}): {e} — {delay:.1f}s 後重試"
                )
                time.sleep(delay)
            else:
                log.error(
                    f"[{symbol}] create_order 失敗，已重試 {max_retries} 次: {e}"
                )
    raise last_exc
