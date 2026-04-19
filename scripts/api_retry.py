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
"""
import time
import uuid
import logging

log = logging.getLogger("api_retry")

# 預設重試 3 次，退避 1s → 2s → 4s
_MAX_RETRIES = 3
_BASE_DELAY = 1.0


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

    比 retry_api 安全：
      retry_api 會盲目重下 → 同一張單下 2、3 次（200-order limit 爆掉）
    """
    # 呼叫端未指定 clientOrderId 才自動產生
    coid = params.pop("newClientOrderId", None) or gen_client_order_id()
    params["newClientOrderId"] = coid

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return client.futures_create_order(symbol=symbol, **params)
        except Exception as e:
            last_exc = e
            # 關鍵：先查幣安是否已經收了這張單
            try:
                existing = client.futures_get_order(
                    symbol=symbol, origClientOrderId=coid
                )
                if existing and existing.get("status") not in (
                    None, "", "EXPIRED", "CANCELED"
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
