"""
臨時腳本：查詢 bot_state.db 策略成效
"""
import sqlite3, statistics, os, sys

DB = os.path.join(os.path.dirname(__file__), "..", "bot_state.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── 1. 總覽 ──────────────────────────────────────────────────────
row = dict(conn.execute("""
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
  SUM(CASE WHEN status IN ('open','partial') THEN 1 ELSE 0 END) as open_now,
  ROUND(SUM(CASE WHEN status='closed' THEN net_pnl ELSE 0 END),4) as total_pnl,
  ROUND(AVG(CASE WHEN status='closed' THEN net_pnl END),4) as avg_pnl,
  SUM(CASE WHEN status='closed' AND net_pnl>0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN status='closed' AND net_pnl<=0 THEN 1 ELSE 0 END) as losses
FROM trades
""").fetchone())
closed = row['closed'] or 0
wr = round(row['wins'] / closed * 100, 1) if closed else 0
print("=== 總覽 ===")
print(f"  總筆數: {row['total']}  已平: {closed}  持倉中: {row['open_now']}")
print(f"  淨PnL : {row['total_pnl']} U   平均/筆: {row['avg_pnl']} U")
print(f"  勝率  : {wr}%  ({row['wins']}W / {row['losses']}L)")
print()

# ── 2. 平倉原因分佈 ────────────────────────────────────────────
print("=== 平倉原因 ===")
for r in conn.execute("""
SELECT close_reason,
  COUNT(*) as n,
  ROUND(SUM(net_pnl),4) as sum_pnl,
  ROUND(AVG(net_pnl),4) as avg_pnl,
  SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) as wins
FROM trades WHERE status='closed'
GROUP BY close_reason ORDER BY n DESC
""").fetchall():
    r = dict(r)
    wr2 = round(r['wins'] / r['n'] * 100, 1) if r['n'] else 0
    reason = str(r['close_reason'] or 'NULL')
    print(f"  {reason:20s}  n={r['n']:3d}  sum={r['sum_pnl']:+7.3f}U  avg={r['avg_pnl']:+6.3f}U  WR={wr2}%")
print()

# ── 3. SL 幅度分佈（LONG 已平倉）─────────────────────────────
print("=== SL 幅度分析（已平倉 LONG）===")
rows3 = [dict(r) for r in conn.execute("""
SELECT
  ROUND((entry - sl)/entry*100, 2) as sl_pct,
  close_reason, net_pnl, symbol
FROM trades
WHERE status='closed' AND direction='LONG'
  AND sl IS NOT NULL AND entry>0 AND sl>0
ORDER BY sl_pct
""").fetchall()]

if rows3:
    sls = [r['sl_pct'] for r in rows3]
    print(f"  SL幅度  最小={min(sls):.2f}%  最大={max(sls):.2f}%  "
          f"中位={statistics.median(sls):.2f}%  均值={statistics.mean(sls):.2f}%")
    buckets = [("<3%", 0, 3), ("3-5%", 3, 5), ("5-8%", 5, 8), (">8%", 8, 9999)]
    for label, lo, hi in buckets:
        v = [r for r in rows3 if lo <= r['sl_pct'] < hi]
        if not v:
            continue
        w = sum(1 for x in v if x['net_pnl'] > 0)
        sp = sum(x['net_pnl'] for x in v)
        wp = sum(x['net_pnl'] for x in v if x['net_pnl'] > 0)
        lp = sum(x['net_pnl'] for x in v if x['net_pnl'] <= 0)
        print(f"  {label:6s}: n={len(v):3d}  WR={round(w/len(v)*100,1):5.1f}%  "
              f"sumPnL={sp:+7.3f}U  win={wp:+6.3f}  loss={lp:+7.3f}")
print()

# ── 4. MFE vs SL 分析（抓到就顯示）──────────────────────────
print("=== MFE 分析（LONG 已平倉，SL/TRAILING/TIMEOUT+SL）===")
sl_rows = conn.execute("""
SELECT symbol,
  ROUND((entry-sl)/entry*100,2) as sl_pct,
  mfe_pct, mae_pct,
  net_pnl, close_reason
FROM trades
WHERE status='closed' AND direction='LONG'
  AND close_reason IN ('SL','TRAILING','TIMEOUT+SL')
ORDER BY CAST(mfe_pct AS REAL) DESC LIMIT 25
""").fetchall()
print(f"  {'symbol':12s}  {'sl%':>6s}  {'mfe%':>7s}  {'mae%':>7s}  {'pnl':>8s}  reason")
for r in sl_rows:
    r = dict(r)
    mfe = f"{r['mfe_pct']:.2f}" if r['mfe_pct'] is not None else "   -"
    mae = f"{r['mae_pct']:.2f}" if r['mae_pct'] is not None else "   -"
    print(f"  {str(r['symbol']):12s}  {str(r['sl_pct']):>6s}%  "
          f"{mfe:>7s}%  {mae:>7s}%  "
          f"{r['net_pnl']:+8.4f}U  {r['close_reason']}")
print()

# ── 5. 最近 30 筆 ──────────────────────────────────────────
print("=== 最近 30 筆已平倉 ===")
print(f"  {'symbol':12s} {'entry':>9s} {'sl%':>6s} {'tp1':>9s} {'net_pnl':>8s}  {'reason':18s}  opened_at")
for r in conn.execute("""
SELECT symbol, direction, entry, sl, tp1,
  ROUND((entry-sl)/entry*100,2) as sl_pct,
  close_reason, net_pnl,
  datetime(opened_at) as opened_at
FROM trades WHERE status='closed'
ORDER BY id DESC LIMIT 30
""").fetchall():
    r = dict(r)
    sp = r['sl_pct'] if r['sl_pct'] is not None else 0.0
    print(f"  {str(r['symbol']):12s} {r['entry']:9.4f} {sp:6.2f}% "
          f"{(r['tp1'] or 0):9.4f} {r['net_pnl']:+8.4f}U  "
          f"{str(r['close_reason']):18s}  {r['opened_at']}")

conn.close()
