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
db       = StateManager()
executor = OrderExecutor(client, db)

def _get_account_balance() -> dict:
    """安全取得帳戶餘額，連線失敗回傳 -1"""
    try:
        account = client.futures_account()
        for asset in account["assets"]:
            if asset["asset"] == "USDT":
                return {
                    "wallet":    round(float(asset["walletBalance"]), 2),
                    "available": round(float(asset["availableBalance"]), 2),
                }
    except Exception as e:
        log.warning(f"取得餘額失敗: {e}")
    return {"wallet": -1, "available": -1}

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
        "now":       datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
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

        result.append({**t, "current_price": current_price,
                       "unrealized_pnl": unrealized_pnl, "rr": rr})
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
        db.close_trade(trade_id, exit_price, fee=fee)
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
