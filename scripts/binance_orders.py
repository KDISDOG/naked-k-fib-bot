"""
binance_orders.py — 統一管理標準訂單與 Algo Conditional 訂單

背景：
Binance USDT-M futures 把 STOP_MARKET / TAKE_PROFIT_MARKET 類訂單
歸類為 "Algo / Conditional" 系統。這些訂單：
  - 不會出現在 futures_get_open_orders
  - 下單 response 回傳 algoId（而非 orderId）
  - 撤單要用 futures_cancel_algo_order(algoId=...)

標準訂單（LIMIT / MARKET）則照常在 futures_get_open_orders 可見。

本模組提供統一的 list / cancel API，把兩套系統合併處理。
所有 bot 的掛單管理邏輯都應透過這些 helper，不要直接呼叫
futures_get_open_orders 或 futures_cancel_order/cancel_all_open_orders。
"""
import logging
from typing import List, Dict, Any, Optional, Tuple

log = logging.getLogger(__name__)


def _normalize_standard(o: dict) -> Dict[str, Any]:
    return {
        "is_algo":       False,
        "orderId":       o.get("orderId"),
        "algoId":        None,
        "symbol":        o.get("symbol"),
        "type":          o.get("type"),
        "side":          o.get("side"),
        "stopPrice":     float(o.get("stopPrice") or 0),
        "origQty":       float(o.get("origQty") or 0),
        "reduceOnly":    bool(o.get("reduceOnly")),
        "closePosition": bool(o.get("closePosition")),
        "status":        o.get("status"),
        "raw":           o,
    }


def _normalize_algo(o: dict) -> Dict[str, Any]:
    # algo 欄位名跟標準不同：orderType / triggerPrice / quantity / algoStatus
    return {
        "is_algo":       True,
        "orderId":       None,
        "algoId":        o.get("algoId"),
        "symbol":        o.get("symbol"),
        "type":          o.get("orderType"),
        "side":          o.get("side"),
        "stopPrice":     float(o.get("triggerPrice") or 0),
        "origQty":       float(o.get("quantity") or 0),
        "reduceOnly":    bool(o.get("reduceOnly")),
        "closePosition": bool(o.get("closePosition")),
        "status":        o.get("algoStatus"),
        "raw":           o,
    }


def list_open_orders(client, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    返回統一格式的所有 open orders（標準 + Algo）。
    任一端點失敗不會讓整體失敗 —— 返回拿到的部分。
    """
    result: List[Dict[str, Any]] = []
    try:
        std = (client.futures_get_open_orders(symbol=symbol)
               if symbol else client.futures_get_open_orders())
        result.extend(_normalize_standard(o) for o in (std or []))
    except Exception as e:
        log.warning(f"futures_get_open_orders({symbol}) 失敗: {e}")
    try:
        algo = (client.futures_get_open_algo_orders(symbol=symbol)
                if symbol else client.futures_get_open_algo_orders())
        result.extend(_normalize_algo(o) for o in (algo or []))
    except Exception as e:
        log.warning(f"futures_get_open_algo_orders({symbol}) 失敗: {e}")
    return result


def cancel_order(client, symbol: str, entry: Dict[str, Any]) -> bool:
    """
    撤一筆 list_open_orders 返回的訂單，依 is_algo 分派到正確端點。
    """
    try:
        if entry.get("is_algo"):
            client.futures_cancel_algo_order(
                symbol=symbol, algoId=int(entry["algoId"])
            )
        else:
            client.futures_cancel_order(
                symbol=symbol, orderId=int(entry["orderId"])
            )
        return True
    except Exception as e:
        kind = "algo" if entry.get("is_algo") else "std"
        oid = entry.get("algoId") or entry.get("orderId")
        log.warning(f"[{symbol}] 撤 {kind} #{oid} 失敗: {e}")
        return False


def cancel_all_for_symbol(client, symbol: str) -> int:
    """
    清空 symbol 所有 open orders（標準 + Algo），冪等。
    回傳估計清除筆數（撤之前先枚舉一次）。
    """
    try:
        before = list_open_orders(client, symbol=symbol)
        count = len(before)
    except Exception:
        count = 0

    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel_all_open_orders 失敗: {e}")
    try:
        client.futures_cancel_all_algo_open_orders(symbol=symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel_all_algo_open_orders 失敗: {e}")

    return count


def extract_id(response: dict) -> Tuple[str, bool]:
    """
    從下單 response 抽取 (id_str, is_algo)。
    Algo 訂單 response 會有 algoId，無 orderId；標準訂單反之。
    """
    if not response:
        return "", False
    if response.get("algoId"):
        return str(response["algoId"]), True
    oid = response.get("orderId")
    return (str(oid), False) if oid else ("", False)


# Algo 訂單「活著」的狀態
_LIVE_ALGO_STATUSES = {"NEW", "WORKING"}
# 標準訂單「活著」的狀態（FILLED 也算成功，雖然少見）
_LIVE_STD_STATUSES  = {"NEW", "PARTIALLY_FILLED", "FILLED"}


def is_order_live(response: dict) -> bool:
    """
    判斷 create_order 回傳的訂單是否真的「活著」。

    背景：Binance 有時對 algo order（STOP_MARKET / TP_MARKET）
    回 HTTP 200 但 body 是 algoStatus=REJECTED / EXPIRED。
    python-binance 不會把這視為 exception，呼叫端若只看 id 會
    誤以為下單成功 → 裸倉。此函式統一判定。
    """
    if not response:
        return False
    if response.get("algoId"):
        return response.get("algoStatus") in _LIVE_ALGO_STATUSES
    if response.get("orderId"):
        return response.get("status") in _LIVE_STD_STATUSES
    return False


def describe_reject(response: dict) -> str:
    """取出 reject 原因字串，供 log / notify 使用"""
    if not response:
        return "empty response"
    status = response.get("algoStatus") or response.get("status") or "?"
    reason = (response.get("failReason")
              or response.get("rejectReason")
              or response.get("msg")
              or "")
    return f"status={status} reason={reason}" if reason else f"status={status}"
