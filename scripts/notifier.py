"""
Telegram 通知模組 — 交易事件、錯誤、每日總結

使用方式：
  from notifier import notify
  notify.trade_opened(symbol, direction, qty, entry, sl, tp1, strategy)
  notify.trade_closed(symbol, direction, net_pnl, close_reason)
  notify.error("SL 掛單失敗", extra="symbol=BTCUSDT")
  notify.daily_summary(today_pnl, total_pnl, win_rate, open_count)
  notify.bot_started()
  notify.daily_loss_paused(today_pnl, loss_pct)
"""
import os
import logging
import threading
import urllib.request
import urllib.parse
import json
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("notifier")


class TelegramNotifier:
    def __init__(self):
        self._token = os.getenv("TG_BOT_TOKEN", "")
        self._chat_id = os.getenv("TG_CHAT_ID", "")
        self._enabled = bool(self._token and self._chat_id)
        if not self._enabled:
            log.warning(
                "Telegram 通知未啟用（缺少 TG_BOT_TOKEN 或 TG_CHAT_ID）"
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _send(self, text: str):
        """非同步發送 Telegram 訊息（不阻塞主線程）"""
        if not self._enabled:
            return
        threading.Thread(
            target=self._send_sync, args=(text,), daemon=True
        ).start()

    def _send_sync(self, text: str):
        try:
            url = (
                f"https://api.telegram.org/bot{self._token}/sendMessage"
            )
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    log.warning(f"Telegram 回應非 200: {resp.status}")
        except Exception as e:
            log.warning(f"Telegram 發送失敗: {e}")

    # ── 交易事件 ─────────────────────────────────────────────────

    def trade_opened(self, symbol: str, direction: str, qty: float,
                     entry: float, sl: float, tp1: float,
                     strategy: str = ""):
        sign = "🟢" if direction == "LONG" else "🔴"
        self._send(
            f"{sign} <b>開倉</b> [{strategy}]\n"
            f"幣種: <code>{symbol}</code>\n"
            f"方向: {direction}  數量: {qty}\n"
            f"入場: {entry}  SL: {sl}  TP1: {tp1}"
        )

    def trade_closed(self, symbol: str, direction: str,
                     net_pnl: float, close_reason: str = ""):
        icon = "✅" if net_pnl >= 0 else "❌"
        sign = "+" if net_pnl >= 0 else ""
        self._send(
            f"{icon} <b>平倉</b> {symbol}\n"
            f"淨盈虧: <b>{sign}{net_pnl:.2f} USDT</b>\n"
            f"原因: {close_reason}"
        )

    # ── 風控事件 ─────────────────────────────────────────────────

    def daily_loss_paused(self, today_pnl: float, loss_pct: float):
        self._send(
            f"🚨 <b>每日虧損上限觸發 — 機器人已暫停</b>\n"
            f"今日 P&L: {today_pnl:.2f} USDT\n"
            f"虧損比例: {loss_pct:.1%}"
        )

    def sl_placement_failed(self, symbol: str, direction: str):
        self._send(
            f"⚠️ <b>SL 掛單失敗 — 已緊急平倉</b>\n"
            f"幣種: <code>{symbol}</code> 方向: {direction}"
        )

    # ── 系統事件 ─────────────────────────────────────────────────

    def error(self, msg: str, extra: str = ""):
        text = f"⚠️ <b>錯誤</b>\n{msg}"
        if extra:
            text += f"\n<code>{extra}</code>"
        self._send(text)

    def bot_started(self):
        self._send("🤖 <b>機器人已啟動</b>")

    def bot_stopped(self):
        self._send("🛑 <b>機器人已停止</b>")

    def daily_summary(self, today_pnl: float, total_pnl: float,
                      win_rate: float, open_count: int):
        sign = "+" if today_pnl >= 0 else ""
        self._send(
            f"📊 <b>每日總結</b>\n"
            f"今日: {sign}{today_pnl:.2f} USDT\n"
            f"累計: {'+' if total_pnl >= 0 else ''}{total_pnl:.2f} USDT\n"
            f"勝率: {win_rate:.1f}%  目前持倉: {open_count}"
        )


# 全局單例
notify = TelegramNotifier()
