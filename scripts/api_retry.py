"""
API retry wrapper — 為所有幣安 API 呼叫加上指數退避重試

使用方式：
  from api_retry import retry_api
  result = retry_api(client.futures_account)
  result = retry_api(client.futures_klines, symbol="BTCUSDT", interval="1h")
"""
import time
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
