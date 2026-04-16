"""
State Manager — SQLite 資料庫操作（部位、P&L、交易紀錄）
v2: 新增防重複開倉、冷卻期、手續費欄位、分批止盈追蹤
"""
import os
import logging
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    String, DateTime, Boolean, func, text, inspect
)
from sqlalchemy.orm import declarative_base, sessionmaker

log = logging.getLogger("db")
Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    symbol       = Column(String, nullable=False, index=True)
    direction    = Column(String, nullable=False)       # LONG / SHORT
    entry        = Column(Float)
    sl           = Column(Float)
    tp1          = Column(Float)                        # 第一止盈（1R）
    tp2          = Column(Float)                        # 第二止盈（Fib 目標）
    qty          = Column(Float)
    qty_closed   = Column(Float, default=0.0)           # 已平倉數量
    status       = Column(String, default="open", index=True)
    # open / partial（已部分止盈）/ closed / cancelled
    pnl          = Column(Float, default=0.0)
    fee          = Column(Float, default=0.0)           # 累計手續費
    net_pnl      = Column(Float, default=0.0)           # pnl - fee
    fib_level    = Column(String)
    pattern      = Column(String)
    signal_score = Column(Integer, default=0)
    timeframe    = Column(String, default="1h")
    breakeven    = Column(Boolean, default=False)       # 是否已移至保本
    opened_at    = Column(DateTime, default=datetime.now)
    closed_at    = Column(DateTime, nullable=True)
    # 用於追蹤幣安訂單
    order_id     = Column(String, nullable=True)
    sl_order_id  = Column(String, nullable=True)
    tp1_order_id = Column(String, nullable=True)
    tp2_order_id = Column(String, nullable=True)
    # v3 新增：追蹤止盈 + 相關性紀錄
    use_trailing = Column(Boolean, default=False)      # 是否使用追蹤止盈
    trailing_atr = Column(Float, nullable=True)        # 追蹤用的 ATR 值
    highest_price = Column(Float, nullable=True)       # LONG 進場後最高點
    lowest_price  = Column(Float, nullable=True)       # SHORT 進場後最低點
    btc_corr     = Column(Float, nullable=True)        # 進場時與 BTC 的相關性
    # v5 新增：多策略支援
    strategy     = Column(String, default="naked_k_fib", index=True)  # 策略標識
    timeout_bars = Column(Integer, default=0)          # 已持倉 K 棒數（超時平倉用）
    # v5.1 新增
    margin       = Column(Float, default=0.0)          # 開倉保證金（entry×qty/leverage）
    close_reason = Column(String, nullable=True)       # 平倉原因：TP1/TP2/SL/TIMEOUT/MANUAL/TRAILING


class StateManager:
    def __init__(self, db_path: str = "bot_state.db"):
        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.Session = sessionmaker(bind=engine)
        self._migrate()
        log.info(f"資料庫初始化完成：{db_path}")

    # ── 非破壞性 SQLite 遷移 ─────────────────────────────────────
    def _migrate(self):
        """對舊 DB 新增欄位（不遺失既有資料）"""
        needed = {
            "use_trailing":  "BOOLEAN DEFAULT 0",
            "trailing_atr":  "FLOAT",
            "highest_price": "FLOAT",
            "lowest_price":  "FLOAT",
            "btc_corr":      "FLOAT",
            # v5
            "strategy":      "TEXT DEFAULT 'naked_k_fib'",
            "timeout_bars":  "INTEGER DEFAULT 0",
            # v5.1
            "margin":        "FLOAT DEFAULT 0",
            "close_reason":  "TEXT",
        }
        try:
            insp = inspect(self.engine)
            existing = {c["name"] for c in insp.get_columns("trades")}
            with self.engine.begin() as conn:
                for col, decl in needed.items():
                    if col not in existing:
                        conn.execute(text(
                            f"ALTER TABLE trades ADD COLUMN {col} {decl}"
                        ))
                        log.info(f"[DB] 遷移新增欄位：trades.{col}")
        except Exception as e:
            log.warning(f"[DB] 遷移失敗（可能無需遷移）：{e}")

    # ── 寫入 ─────────────────────────────────────────────────────

    def save_trade(self, symbol, direction, entry, sl, tp1, tp2, qty,
                   fib_level="", pattern="", score=0, timeframe="1h",
                   order_id=None, sl_order_id=None,
                   tp1_order_id=None, tp2_order_id=None,
                   use_trailing=False, trailing_atr=None,
                   btc_corr=None,
                   strategy="naked_k_fib",
                   margin=0.0) -> Trade:
        with self.Session() as session:
            trade = Trade(
                symbol=symbol, direction=direction,
                entry=entry, sl=sl, tp1=tp1, tp2=tp2, qty=qty,
                fib_level=fib_level, pattern=pattern,
                signal_score=score, timeframe=timeframe,
                order_id=order_id, sl_order_id=sl_order_id,
                tp1_order_id=tp1_order_id, tp2_order_id=tp2_order_id,
                use_trailing=use_trailing, trailing_atr=trailing_atr,
                btc_corr=btc_corr,
                strategy=strategy,
                margin=margin,
                highest_price=entry if direction == "LONG" else None,
                lowest_price=entry if direction == "SHORT" else None,
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)
            log.info(f"[DB] 交易紀錄已儲存：#{trade.id} {symbol} {direction}")
            return trade

    # ── 平倉更新 ──────────────────────────────────────────────────

    def close_trade(self, trade_id: int, exit_price: float,
                    fee: float = 0.0, partial: bool = False,
                    closed_qty: float = 0.0,
                    close_reason: str = None):
        """
        更新平倉紀錄
        partial=True: 部分止盈（tp1 觸發），狀態改為 partial
        partial=False: 完全平倉
        close_reason: TP1 / TP2 / SL / TIMEOUT / MANUAL / TRAILING
        """
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return

            # 防護：exit_price=0 會產生天文數字 PnL，改用入場價
            if exit_price <= 0:
                log.warning(
                    f"[DB] #{trade_id} exit_price={exit_price}，"
                    f"fallback 至 entry={trade.entry}"
                )
                exit_price = trade.entry

            if partial:
                trade.status = "partial"
                trade.qty_closed += closed_qty
                # 計算部分 P&L
                if trade.direction == "LONG":
                    partial_pnl = (exit_price - trade.entry) * closed_qty
                else:
                    partial_pnl = (trade.entry - exit_price) * closed_qty
                trade.pnl += partial_pnl
            else:
                trade.status = "closed"
                trade.closed_at = datetime.now()
                remaining = trade.qty - trade.qty_closed
                if trade.direction == "LONG":
                    final_pnl = (exit_price - trade.entry) * remaining
                else:
                    final_pnl = (trade.entry - exit_price) * remaining
                trade.pnl += final_pnl
                trade.qty_closed = trade.qty

            trade.fee += fee
            trade.net_pnl = trade.pnl - trade.fee
            if close_reason:
                trade.close_reason = close_reason
            session.commit()
            log.info(
                f"[DB] {'部分' if partial else '完全'}平倉：#{trade_id} "
                f"P&L={trade.pnl:.2f} fee={trade.fee:.2f} net={trade.net_pnl:.2f}"
            )

    def update_breakeven(self, trade_id: int, new_sl: float,
                         sl_order_id: str = None):
        """標記已移至保本止損"""
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return
            trade.breakeven = True
            trade.sl = new_sl
            if sl_order_id:
                trade.sl_order_id = sl_order_id
            session.commit()
            log.info(f"[DB] #{trade_id} 止損已移至保本：{new_sl}")

    def update_trailing_price(self, trade_id: int,
                              current_price: float):
        """更新 LONG 最高價 / SHORT 最低價（追蹤止盈用）"""
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade or not trade.use_trailing:
                return
            if trade.direction == "LONG":
                if trade.highest_price is None or current_price > trade.highest_price:
                    trade.highest_price = current_price
                    session.commit()
            else:
                if trade.lowest_price is None or current_price < trade.lowest_price:
                    trade.lowest_price = current_price
                    session.commit()

    def update_sl(self, trade_id: int, new_sl: float,
                  sl_order_id: str = None):
        """單純更新止損價（用於追蹤止盈推進）"""
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return
            trade.sl = new_sl
            if sl_order_id:
                trade.sl_order_id = sl_order_id
            session.commit()

    def update_order_ids(self, trade_id: int, **kwargs):
        """更新訂單 ID（sl_order_id, tp1_order_id, tp2_order_id）"""
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return
            for key, val in kwargs.items():
                if hasattr(trade, key):
                    setattr(trade, key, val)
            session.commit()

    # ── 查詢 ─────────────────────────────────────────────────────

    def has_open_position(self, symbol: str) -> bool:
        """檢查某幣種是否已有未平倉（含 partial）"""
        with self.Session() as session:
            count = (
                session.query(Trade)
                .filter(
                    Trade.symbol == symbol,
                    Trade.status.in_(["open", "partial"])
                )
                .count()
            )
            return count > 0

    def in_cooldown(self, symbol: str, cooldown_bars: int = 6,
                    bar_minutes: int = 15,
                    strategy: str = None) -> bool:
        """
        檢查某幣種是否在冷卻期內（per-strategy）：
          上次同策略止損出場後 cooldown_bars 根 K 棒內不開新倉。
          不同策略間不互相阻擋，避免一邊虧了另一邊也被封鎖。
        """
        cooldown_delta = timedelta(minutes=cooldown_bars * bar_minutes)
        cutoff = datetime.now() - cooldown_delta
        with self.Session() as session:
            q = session.query(Trade).filter(
                Trade.symbol == symbol,
                Trade.status == "closed",
                Trade.net_pnl < 0,
                Trade.closed_at >= cutoff,
            )
            if strategy:
                q = q.filter(Trade.strategy == strategy)
            return q.order_by(Trade.closed_at.desc()).first() is not None

    def count_open_positions(self) -> int:
        with self.Session() as session:
            return (
                session.query(Trade)
                .filter(Trade.status.in_(["open", "partial"]))
                .count()
            )

    def get_open_trades(self) -> list[dict]:
        """取得所有未平倉交易"""
        with self.Session() as session:
            trades = (
                session.query(Trade)
                .filter(Trade.status.in_(["open", "partial"]))
                .all()
            )
            return [self._trade_to_dict(t) for t in trades]

    def get_trade_by_id(self, trade_id: int) -> dict | None:
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return None
            return self._trade_to_dict(trade)

    def get_today_pnl(self) -> float:
        today = datetime.now().date()
        with self.Session() as session:
            result = session.query(func.sum(Trade.net_pnl)).filter(
                func.date(Trade.closed_at) == today,
                Trade.status == "closed"
            ).scalar()
            return float(result or 0.0)

    def get_today_fee(self) -> float:
        today = datetime.now().date()
        with self.Session() as session:
            result = session.query(func.sum(Trade.fee)).filter(
                func.date(Trade.closed_at) == today,
                Trade.status == "closed"
            ).scalar()
            return float(result or 0.0)

    def get_all_trades(self, limit=100) -> list:
        with self.Session() as session:
            trades = (
                session.query(Trade)
                .order_by(Trade.opened_at.desc())
                .limit(limit)
                .all()
            )
            return [self._trade_to_dict(t) for t in trades]

    def get_stats(self) -> dict:
        with self.Session() as session:
            closed = session.query(Trade).filter(Trade.status == "closed").all()
            if not closed:
                return {
                    "total": 0, "wins": 0, "losses": 0,
                    "win_rate": 0, "total_pnl": 0, "total_fee": 0,
                    "net_pnl": 0, "avg_pnl": 0
                }
            wins      = [t for t in closed if t.net_pnl > 0]
            total_pnl = sum(t.pnl for t in closed)
            total_fee = sum(t.fee for t in closed)
            net_pnl   = sum(t.net_pnl for t in closed)
            return {
                "total":     len(closed),
                "wins":      len(wins),
                "losses":    len(closed) - len(wins),
                "win_rate":  round(len(wins) / len(closed) * 100, 1),
                "total_pnl": round(total_pnl, 2),
                "total_fee": round(total_fee, 2),
                "net_pnl":   round(net_pnl, 2),
                "avg_pnl":   round(net_pnl / len(closed), 2),
            }

    # ── 內部 ─────────────────────────────────────────────────────

    @staticmethod
    def _trade_to_dict(t: Trade) -> dict:
        return {
            "id":           t.id,
            "symbol":       t.symbol,
            "direction":    t.direction,
            "entry":        t.entry,
            "sl":           t.sl,
            "tp1":          t.tp1,
            "tp2":          t.tp2,
            "qty":          t.qty,
            "qty_closed":   t.qty_closed,
            "status":       t.status,
            "pnl":          t.pnl,
            "fee":          t.fee,
            "net_pnl":      t.net_pnl,
            "fib_level":    t.fib_level,
            "pattern":      t.pattern,
            "score":        t.signal_score,
            "timeframe":    t.timeframe,
            "breakeven":    t.breakeven,
            "opened_at":    t.opened_at.isoformat() if t.opened_at else None,
            "closed_at":    t.closed_at.isoformat() if t.closed_at else None,
            "order_id":     t.order_id,
            "sl_order_id":  t.sl_order_id,
            "tp1_order_id": t.tp1_order_id,
            "tp2_order_id": t.tp2_order_id,
            "use_trailing": t.use_trailing,
            "trailing_atr": t.trailing_atr,
            "highest_price": t.highest_price,
            "lowest_price": t.lowest_price,
            "btc_corr":     t.btc_corr,
            "strategy":     getattr(t, "strategy", "naked_k_fib"),
            "timeout_bars": getattr(t, "timeout_bars", 0),
            "margin":       getattr(t, "margin", 0) or 0,
            "close_reason": getattr(t, "close_reason", None),
        }

    def get_open_trades_by_direction(self, direction: str) -> list[dict]:
        """取得指定方向的未平倉交易"""
        with self.Session() as session:
            trades = (
                session.query(Trade)
                .filter(Trade.status.in_(["open", "partial"]),
                        Trade.direction == direction)
                .all()
            )
            return [self._trade_to_dict(t) for t in trades]

    # ── v5 多策略新增 ─────────────────────────────────────────────

    def get_open_by_strategy(self, strategy_name: str) -> list[dict]:
        """查詢特定策略的所有未平倉交易"""
        with self.Session() as session:
            trades = (
                session.query(Trade)
                .filter(
                    Trade.status.in_(["open", "partial"]),
                    Trade.strategy == strategy_name,
                )
                .all()
            )
            return [self._trade_to_dict(t) for t in trades]

    def count_positions_by_strategy(self, strategy_name: str) -> int:
        """計算特定策略的持倉數"""
        with self.Session() as session:
            return (
                session.query(Trade)
                .filter(
                    Trade.status.in_(["open", "partial"]),
                    Trade.strategy == strategy_name,
                )
                .count()
            )

    def increment_timeout_bars(self, trade_id: int) -> int:
        """持倉 K 棒數 +1，回傳新的 timeout_bars 值（均值回歸超時用）"""
        with self.Session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                return 0
            current = trade.timeout_bars or 0
            trade.timeout_bars = current + 1
            session.commit()
            return trade.timeout_bars

    def get_stats_by_strategy(self, strategy_name: str) -> dict:
        """取得特定策略的統計數據"""
        with self.Session() as session:
            closed = (
                session.query(Trade)
                .filter(Trade.status == "closed",
                        Trade.strategy == strategy_name)
                .all()
            )
            if not closed:
                return {
                    "strategy": strategy_name,
                    "total": 0, "wins": 0, "losses": 0,
                    "win_rate": 0, "total_pnl": 0, "net_pnl": 0,
                }
            wins    = [t for t in closed if t.net_pnl > 0]
            net_pnl = sum(t.net_pnl for t in closed)
            return {
                "strategy": strategy_name,
                "total":    len(closed),
                "wins":     len(wins),
                "losses":   len(closed) - len(wins),
                "win_rate": round(len(wins) / len(closed) * 100, 1),
                "total_pnl": round(sum(t.pnl for t in closed), 2),
                "net_pnl":   round(net_pnl, 2),
                "avg_pnl":   round(net_pnl / len(closed), 2),
            }

