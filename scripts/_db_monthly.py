"""臨時：月度曲線 + ATR 分析"""
import sqlite3, statistics, os

DB = os.path.join(os.path.dirname(__file__), "..", "bot_state.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=== 月度 PnL 曲線 ===")
for r in conn.execute("""
SELECT strftime('%Y-%m', opened_at) as month,
  COUNT(*) as n,
  SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(net_pnl),3) as pnl,
  SUM(CASE WHEN close_reason='SL' THEN 1 ELSE 0 END) as sl_n,
  ROUND(SUM(CASE WHEN close_reason='SL' THEN net_pnl ELSE 0 END),3) as sl_pnl
FROM trades WHERE status='closed'
GROUP BY month ORDER BY month
""").fetchall():
    r = dict(r)
    wr = round(r['wins'] / r['n'] * 100, 1) if r['n'] else 0
    m = r['month'] or '?'
    print(f"  {m}  n={r['n']:3d}  WR={wr:5.1f}%  pnl={r['pnl']:+7.3f}U"
          f"  SL={r['sl_n']:3d}筆/{r['sl_pnl']:+7.3f}U")

print()

print("=== SL幅度 vs ATR 相關（已有 atr_at_entry 的樣本）===")
rows = [dict(r) for r in conn.execute("""
SELECT
  ROUND((entry-sl)/entry*100,3) as sl_pct,
  ROUND(atr_at_entry/entry*100,3) as atr_pct,
  close_reason, net_pnl
FROM trades
WHERE status='closed' AND direction='LONG'
  AND atr_at_entry IS NOT NULL AND atr_at_entry > 0
  AND entry > 0 AND sl > 0
""").fetchall()]

if rows:
    print(f"  樣本數: {len(rows)}")
    sl_win  = [r for r in rows if r['net_pnl'] > 0]
    sl_loss = [r for r in rows if r['net_pnl'] <= 0]
    if sl_win:
        print(f"  獲利筆  SL幅度均值={statistics.mean(r['sl_pct'] for r in sl_win):.2f}%"
              f"  ATR均值={statistics.mean(r['atr_pct'] for r in sl_win):.2f}%")
    if sl_loss:
        print(f"  虧損筆  SL幅度均值={statistics.mean(r['sl_pct'] for r in sl_loss):.2f}%"
              f"  ATR均值={statistics.mean(r['atr_pct'] for r in sl_loss):.2f}%")
    mults = [r['sl_pct'] / r['atr_pct'] for r in rows
             if r['atr_pct'] and r['atr_pct'] > 0]
    if mults:
        print(f"  SL/ATR implied mult:  最小={min(mults):.2f}x  最大={max(mults):.2f}x"
              f"  中位={statistics.median(mults):.2f}x  均值={statistics.mean(mults):.2f}x")

conn.close()
