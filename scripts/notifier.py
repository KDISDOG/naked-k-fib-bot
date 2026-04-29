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

    def bot_started(self, regime: str = ""):
        txt = "🤖 <b>機器人已啟動</b>"
        if regime:
            txt += f"\nBTC Regime: <b>{regime}</b>"
        self._send(txt)

    def bot_stopped(self):
        self._send("🛑 <b>機器人已停止</b>")

    def regime_changed(self, old: str, new: str):
        self._send(
            f"🌤 <b>BTC Regime 變化</b>\n"
            f"<code>{old}</code> → <b>{new}</b>"
        )

    def daily_summary(self, today_pnl: float, total_pnl: float,
                      win_rate: float, open_count: int,
                      per_strategy: dict | None = None,
                      regime: str = ""):
        sign = "+" if today_pnl >= 0 else ""
        lines = [
            "📊 <b>每日總結</b>",
            f"今日: <b>{sign}{today_pnl:.2f} USDT</b>",
            f"累計: {'+' if total_pnl >= 0 else ''}{total_pnl:.2f} USDT",
            f"勝率: {win_rate:.1f}%  目前持倉: {open_count}",
        ]
        if regime:
            lines.append(f"BTC Regime: <b>{regime}</b>")

        if per_strategy:
            lines.append("───────────────")
            # 排序：先有交易的（net_pnl 絕對值大）、再沒交易的
            def _key(item):
                _s, g = item
                traded = g.get("trades", 0) > 0
                return (0 if traded else 1, -abs(g.get("net_pnl", 0)))

            short_map = {
                "naked_k_fib":     "NKF",
                "mean_reversion":  "MR",
                "momentum_long":   "ML",
                "breakdown_short": "BD",
                "smc_sweep":       "SMC",
                "ma_sr_breakout":  "MASR",
                "ma_sr_short":     "MASRS",
                "granville":       "GRV",
            }
            for strat, g in sorted(per_strategy.items(), key=_key):
                label   = short_map.get(strat, strat[:4].upper())
                trades  = g.get("trades", 0)
                wins    = g.get("wins", 0)
                losses  = trades - wins
                netp    = g.get("net_pnl", 0.0)
                avg     = g.get("avg", 0.0)
                wr      = g.get("win_rate", 0.0)
                open_n  = g.get("open", 0)
                sgn_p   = "+" if netp >= 0 else ""
                sgn_a   = "+" if avg  >= 0 else ""
                if trades == 0:
                    lines.append(
                        f"<b>{label}</b>  無平倉  持倉 {open_n}"
                    )
                else:
                    lines.append(
                        f"<b>{label}</b>  {sgn_p}{netp:.2f}  "
                        f"{trades}戰{wins}勝{losses}負 "
                        f"WR {wr:.0f}%  avg {sgn_a}{avg:.2f}  "
                        f"持倉 {open_n}"
                    )

        self._send("\n".join(lines))

    # ── 持倉小時報（每 1 小時）──────────────────────────────────
    def positions_report(self, items: list[dict], regime: str = ""):
        """
        每小時推送所有開倉的即時成效。
        items 每筆含：
          symbol, direction, strategy, entry, current, tp1, tp2, sl,
          qty, margin, unrealized_pnl, price_pct, roe_pct
        regime: 可選 BTC 市場型態標籤（會印在 header）
        訊息太長自動分頁（Telegram 單則 4096 字元上限）。
        """
        regime_tag = f"  Regime: <b>{regime}</b>" if regime else ""
        if not items:
            self._send(
                f"📊 <b>持倉小時報</b>{regime_tag}\n目前無開倉。"
            )
            return

        total_pnl = sum(float(it.get("unrealized_pnl", 0) or 0) for it in items)
        header = (
            f"📊 <b>持倉小時報</b>  持倉 {len(items)} 筆{regime_tag}\n"
            f"未實現總計: <b>{'+' if total_pnl >= 0 else ''}"
            f"{total_pnl:.2f} USDT</b>\n"
            f"───────────────"
        )

        blocks: list[str] = []
        for it in items:
            direction = it.get("direction", "")
            dir_icon  = "🟢" if direction == "LONG" else "🔴"
            strat     = it.get("strategy", "") or "-"
            symbol    = it.get("symbol", "")
            entry     = float(it.get("entry", 0) or 0)
            current   = float(it.get("current", 0) or 0)
            tp1       = float(it.get("tp1", 0) or 0)
            tp2       = float(it.get("tp2", 0) or 0)
            sl        = float(it.get("sl", 0) or 0)
            pnl       = float(it.get("unrealized_pnl", 0) or 0)
            price_pct = float(it.get("price_pct", 0) or 0)
            roe_pct   = float(it.get("roe_pct", 0) or 0)
            pnl_icon  = "🟩" if pnl >= 0 else "🟥"
            sign_pct  = "+" if price_pct >= 0 else ""
            sign_roe  = "+" if roe_pct   >= 0 else ""
            sign_pnl  = "+" if pnl       >= 0 else ""

            blocks.append(
                f"{dir_icon} <b>{symbol}</b> {direction} "
                f"[<i>{strat}</i>]\n"
                f"  入場 <code>{entry:g}</code> → 現價 "
                f"<code>{current:g}</code>  "
                f"({sign_pct}{price_pct:.2f}%)\n"
                f"  TP1 <code>{tp1:g}</code>  TP2 <code>{tp2:g}</code>  "
                f"SL <code>{sl:g}</code>\n"
                f"  {pnl_icon} ROE <b>{sign_roe}{roe_pct:.2f}%</b>  "
                f"PnL {sign_pnl}{pnl:.2f} USDT"
            )

        # 分頁（Telegram 4096 字元上限，保險抓 3500）
        MAX = 3500
        chunks: list[str] = []
        buf = header
        for b in blocks:
            # 幣與幣之間空一行分隔
            piece = "\n\n" + b
            if len(buf) + len(piece) > MAX:
                chunks.append(buf)
                buf = b
            else:
                buf += piece
        chunks.append(buf)
        for c in chunks:
            self._send(c)


# 全局單例
notify = TelegramNotifier()
