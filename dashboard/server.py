"""
Dashboard Server — FastAPI + Jinja2 Web 介面
啟動: python dashboard/server.py --port 8089
"""
import os
import sys
import argparse
import logging
import uvicorn
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from state_manager import StateManager
from order_executor import OrderExecutor
from market_context import MarketContext
from binance.client import Client

load_dotenv()
log = logging.getLogger("dashboard")
app = FastAPI(title="裸K+Fib Bot Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

client = Client(
    os.getenv("BINANCE_API_KEY"),
    os.getenv("BINANCE_SECRET"),
    testnet=os.getenv("BINANCE_TESTNET", "true") == "true"
)
db         = StateManager()
executor   = OrderExecutor(client, db)
market_ctx = MarketContext(client)

def _get_account_balance() -> dict:
    """安全取得帳戶餘額 + 全帳戶權益，連線失敗回傳 -1"""
    try:
        account = client.futures_account()
        wallet = -1
        available = -1
        unrealized = 0.0
        for asset in account["assets"]:
            if asset["asset"] == "USDT":
                wallet    = round(float(asset["walletBalance"]), 2)
                available = round(float(asset["availableBalance"]), 2)
                unrealized = round(float(asset.get("unrealizedProfit", 0)), 2)
                break
        # 合約帳戶總權益（含所有資產 + 未實現損益）
        total_equity = round(
            float(account.get("totalMarginBalance")
                  or account.get("totalWalletBalance", 0)),
            2
        )
        return {
            "wallet":       wallet,
            "available":    available,
            "unrealized":   unrealized,
            "total_equity": total_equity,
        }
    except Exception as e:
        log.warning(f"取得餘額失敗: {e}")
    return {"wallet": -1, "available": -1, "unrealized": 0, "total_equity": -1}

# ── 頁面 ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats     = db.get_stats()
    trades    = db.get_all_trades(limit=50)
    today_pnl = db.get_today_pnl()
    balance   = _get_account_balance()
    load_dotenv(override=True)  # 重新讀取最新 .env
    risk_pct  = float(os.getenv("RISK_PER_TRADE", "0.03"))
    return templates.TemplateResponse(request, "index.html", context={
        "stats":     stats,
        "trades":    trades,
        "today_pnl": round(today_pnl, 2),
        "balance":   balance,
        "now":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "testnet":   os.getenv("BINANCE_TESTNET", "true"),
        "risk_pct":  risk_pct,
    })

# ── API 端點 ───────────────────────────────────────────────────────
@app.get("/api/stats")
async def api_stats():
    return db.get_stats()

@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return db.get_all_trades(limit=limit)

@app.get("/api/balance")
async def api_balance():
    """回傳帳戶 USDT 餘額"""
    return _get_account_balance()

@app.get("/api/pnl_curve")
async def api_pnl_curve():
    """回傳累積淨 P&L 曲線資料（扣除手續費，用於 Chart.js）"""
    trades = db.get_all_trades(limit=200)
    closed = [t for t in reversed(trades) if t["status"] == "closed" and t["closed_at"]]
    cumulative = 0.0
    labels, values = [], []
    for t in closed:
        cumulative += t["net_pnl"] or 0
        labels.append(t["closed_at"][:10])
        values.append(round(cumulative, 2))
    return {"labels": labels, "values": values}

@app.post("/api/emergency_stop")
async def emergency_stop():
    """緊急全平 + 撤單"""
    executor.cancel_all()
    executor.close_all_positions()
    return {"status": "ok", "message": "緊急全平完成"}

@app.get("/api/dashboard_kpis")
async def api_dashboard_kpis():
    """一次取得 KPI 卡片所需全部資料"""
    stats     = db.get_stats()
    today_pnl = db.get_today_pnl()
    balance   = _get_account_balance()
    return {"stats": stats, "today_pnl": round(today_pnl, 2), "balance": balance}

@app.get("/api/open_positions")
async def api_open_positions():
    """回傳當前開倉 + 即時價格 + 未實現損益"""
    trades = db.get_open_trades()
    result = []
    for t in trades:
        current_price = None
        try:
            ticker = client.futures_symbol_ticker(symbol=t["symbol"])
            current_price = round(float(ticker["price"]), 4)
        except Exception:
            pass

        remaining_qty = (t["qty"] or 0) - (t["qty_closed"] or 0)
        unrealized_pnl = None
        rr = None
        if current_price is not None and t.get("entry"):
            if t["direction"] == "LONG":
                unrealized_pnl = round((current_price - t["entry"]) * remaining_qty, 2)
            else:
                unrealized_pnl = round((t["entry"] - current_price) * remaining_qty, 2)
            if t.get("sl") and t["entry"]:
                risk = abs(t["entry"] - t["sl"]) * remaining_qty
                rr = round(unrealized_pnl / risk, 2) if risk > 0 else 0

        leverage = int(os.getenv("MAX_LEVERAGE", 3))
        margin = round((t["qty"] or 0) * (t["entry"] or 0) / leverage, 2)
        result.append({**t, "current_price": current_price,
                       "unrealized_pnl": unrealized_pnl, "rr": rr,
                       "margin": margin})
    return result

@app.post("/api/close_position/{trade_id}")
async def api_close_position(trade_id: int):
    """手動平倉指定交易"""
    trade = db.get_trade_by_id(trade_id)
    if not trade:
        return JSONResponse({"status": "error", "message": "找不到交易"}, status_code=404)
    if trade["status"] not in ("open", "partial"):
        return JSONResponse({"status": "error", "message": "此交易已平倉"}, status_code=400)

    symbol = trade["symbol"]
    try:
        try:
            client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception:
            pass

        positions = client.futures_position_information(symbol=symbol)
        exit_price = trade.get("entry") or 0
        for pos in positions:
            qty = float(pos["positionAmt"])
            if qty == 0:
                continue
            side = "SELL" if qty > 0 else "BUY"
            client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=abs(qty), reduceOnly=True,
            )
            ticker = client.futures_symbol_ticker(symbol=symbol)
            exit_price = float(ticker["price"])

        remaining_qty = (trade["qty"] or 0) - (trade["qty_closed"] or 0)
        fee = round(exit_price * remaining_qty * 0.0004, 4)
        db.close_trade(trade_id, exit_price, fee=fee, close_reason="MANUAL")
        return {"status": "ok", "message": f"{symbol} 已手動平倉"}
    except Exception as e:
        log.error(f"手動平倉失敗 #{trade_id}: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/update_config")
async def update_config(
    risk_pct: float = Form(0.03),
):
    """更新每筆風險 %（寫入 .env，槓桿固定 2x）"""
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("RISK_PER_TRADE="):
            new_lines.append(f"RISK_PER_TRADE={risk_pct}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"RISK_PER_TRADE={risk_pct}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return {"status": "ok", "message": "參數已更新，下次啟動生效"}

# ── v5 多策略 API ─────────────────────────────────────────────────

@app.get("/api/stats/strategy/{strategy_name}")
async def api_strategy_stats(strategy_name: str):
    """取得特定策略的統計數據"""
    return db.get_stats_by_strategy(strategy_name)

@app.get("/api/stats/all_strategies")
async def api_all_strategy_stats():
    """一次取得所有策略的統計"""
    return {
        "naked_k_fib":    db.get_stats_by_strategy("naked_k_fib"),
        "mean_reversion": db.get_stats_by_strategy("mean_reversion"),
        "combined":       db.get_stats(),
    }

@app.post("/api/switch_strategy")
async def switch_strategy(strategy: str = Form(...)):
    """
    熱切換 ACTIVE_STRATEGY（寫入 .env）。
    已開倉的單不受影響，走完原策略邏輯。
    有效值：naked_k_fib / mean_reversion / all
    """
    valid = {"naked_k_fib", "mean_reversion", "all"}
    if strategy not in valid:
        return JSONResponse(
            {"status": "error", "message": f"無效策略：{strategy}"},
            status_code=400,
        )
    import re
    env_path = Path(".env")
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if re.search(r"^ACTIVE_STRATEGY\s*=", text, re.MULTILINE):
        text = re.sub(
            r"^ACTIVE_STRATEGY\s*=.*$",
            f"ACTIVE_STRATEGY={strategy}",
            text, flags=re.MULTILINE
        )
    else:
        text += f"\nACTIVE_STRATEGY={strategy}\n"
    env_path.write_text(text, encoding="utf-8")
    return {"status": "ok", "message": f"策略已切換為 {strategy}，下次排程生效"}

@app.get("/api/market_status")
async def api_market_status():
    """回傳當前市場狀態（BTC 主導率 / BTC 週線方向 / BTC 1h ADX）"""
    import pandas as pd
    import pandas_ta as ta
    status = {
        "btc_dominance":     None,
        "btc_dom_high":      None,
        "btc_weekly_bull":   None,
        "btc_1h_adx":        None,
        "market_regime":     "unknown",
    }
    try:
        dom = market_ctx.btc_dominance()
        status["btc_dominance"] = round(dom, 2) if dom else None
        status["btc_dom_high"]  = market_ctx.is_high_btc_dominance(55.0)
    except Exception:
        pass
    try:
        status["btc_weekly_bull"] = market_ctx.btc_weekly_bullish()
    except Exception:
        pass
    try:
        raw = client.futures_klines(symbol="BTCUSDT", interval="1h", limit=100)
        df = pd.DataFrame(raw, columns=[
            "t","o","h","l","c","v","ct","qav","n","tbv","tbqv","i"
        ])
        for col in ("h","l","c"):
            df[col] = df[col].astype(float)
        adx = ta.adx(df["h"], df["l"], df["c"], length=14)
        adx_val = float(adx["ADX_14"].iloc[-1])
        status["btc_1h_adx"] = round(adx_val, 1)
        if adx_val < 20:
            status["market_regime"] = "range"     # 適合 MR
        elif adx_val <= 45:
            status["market_regime"] = "trend"     # 適合 NKF
        else:
            status["market_regime"] = "overheat"  # 兩者都謹慎
    except Exception:
        pass
    return status

@app.get("/api/active_strategy")
async def api_active_strategy():
    """回傳目前設定的 ACTIVE_STRATEGY"""
    load_dotenv(override=True)
    return {"active_strategy": os.getenv("ACTIVE_STRATEGY", "all")}



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
