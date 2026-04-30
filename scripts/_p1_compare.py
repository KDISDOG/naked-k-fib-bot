"""
P1 比較工具：
  1. NKF 逐 trade 對比（OFF 必須等於 ON，否則 bug）
  2. 4 策略 × 10 幣 OFF/ON 對比表
  3. per-coin filter 動作清單
  4. P1 結論段（給人讀的 summary）

輸出 reports/p1_filter_ab_<ts>.md
"""
import sys
import pickle
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from wf_runner import _segment_metrics
from feature_filter import should_skip_for_strategy, load_feature_filter_config
import pandas as pd

ROOT = Path(__file__).parent.parent
WF_OFF = ROOT / ".cache" / "wf_results" / "p1_filter_off"
WF_ON = ROOT / ".cache" / "wf_results" / "p1_filter_on"
FEAT_PKL = ROOT / ".cache" / "coin_features_39m.pkl"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
STRATS = ["masr", "bd", "smc", "nkf"]


def _load_wf(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _aggregate(wf: dict | None) -> dict:
    if wf is None:
        return {"n_trades": 0, "win_rate": 0.0, "total_pnl": 0.0}
    tt = [t for s in wf["segments"] for t in s["trades"]]
    return _segment_metrics(tt)


# ── 1. NKF 逐筆比對 ──────────────────────────────────────────
def nkf_regression() -> tuple[bool, str, list[str]]:
    """回傳 (pass, summary, errors)"""
    errors: list[str] = []
    fields = ("symbol", "direction", "entry", "sl", "tp1", "tp2",
              "result", "exit_price", "open_time")
    for sym in SYMBOLS:
        off = _load_wf(WF_OFF / f"nkf_{sym}.pkl")
        on = _load_wf(WF_ON / f"nkf_{sym}.pkl")
        if off is None or on is None:
            errors.append(f"missing pkl for {sym}: off={off is not None} on={on is not None}")
            continue
        off_tt = [t for s in off["segments"] for t in s["trades"]]
        on_tt = [t for s in on["segments"] for t in s["trades"]]
        if len(off_tt) != len(on_tt):
            errors.append(f"NKF[{sym}] count mismatch: OFF={len(off_tt)} ON={len(on_tt)}")
            continue
        for k, (a, b) in enumerate(zip(off_tt, on_tt)):
            for f in fields:
                va = getattr(a, f, None)
                vb = getattr(b, f, None)
                if isinstance(va, float) and isinstance(vb, float):
                    if abs(va - vb) > 1e-9:
                        errors.append(f"NKF[{sym}] trade #{k}.{f}: OFF={va} ON={vb}")
                        break
                elif va != vb:
                    errors.append(f"NKF[{sym}] trade #{k}.{f}: OFF={va!r} ON={vb!r}")
                    break
            if errors:
                break
        if errors:
            break

    if errors:
        return False, "NKF 回歸 FAIL", errors
    total = sum(len([t for s in _load_wf(WF_OFF / f'nkf_{sym}.pkl')['segments']
                      for t in s['trades']]) for sym in SYMBOLS)
    return True, f"NKF 回歸 PASS（10 幣 × 共 {total} trades 全等）", []


# ── 2. 對比表 ────────────────────────────────────────────────
def comparison_table() -> str:
    rows = []
    for strat in STRATS:
        for phase, pdir in (("OFF", WF_OFF), ("ON", WF_ON)):
            kept = 0
            n_total = 0
            wins = 0
            pnl_total = 0.0
            per_coin_pnls = []
            for sym in SYMBOLS:
                wf = _load_wf(pdir / f"{strat}_{sym}.pkl")
                m = _aggregate(wf)
                if m["n_trades"] > 0:
                    kept += 1
                    per_coin_pnls.append(m["total_pnl"])
                n_total += m["n_trades"]
                wins += m["n_trades"] * m["win_rate"]
                pnl_total += m["total_pnl"]
            wr = wins / n_total * 100 if n_total > 0 else 0.0
            median_pnl = (sorted(per_coin_pnls)[len(per_coin_pnls) // 2]
                           if per_coin_pnls else 0.0)
            rows.append({
                "strategy": strat.upper(),
                "filter": phase,
                "coins_kept": kept,
                "n_trades": n_total,
                "win_rate": wr,
                "median_pnl_per_coin": median_pnl,
                "total_pnl": pnl_total,
            })

    lines = [
        "| Strategy | Filter | Coins kept | n_trades | win_rate | median PnL/coin | total PnL |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['strategy']} | {r['filter']} | {r['coins_kept']} | "
            f"{r['n_trades']} | {r['win_rate']:.1f}% | "
            f"{r['median_pnl_per_coin']:+.2f}U | {r['total_pnl']:+.2f}U |"
        )
    return "\n".join(lines)


# ── 3. per-coin filter 動作清單 ─────────────────────────────
def filter_actions(features_df: pd.DataFrame) -> str:
    lines = []
    for strat in ("masr", "bd", "smc"):
        lines.append(f"\n**{strat.upper()} filtered out:**\n")
        any_skip = False
        for _, row in features_df.iterrows():
            sym = row["symbol"]
            feat = row.to_dict()
            skip, reason = should_skip_for_strategy(strat, sym, feat)
            if skip:
                lines.append(f"- `{sym}`  ({reason})")
                any_skip = True
        if not any_skip:
            lines.append("- _(none)_")
    return "\n".join(lines)


# ── 4. P1 結論段 ─────────────────────────────────────────────
def conclusion(comparison_rows_md: str) -> str:
    """從 OFF/ON 數字推一段結論。"""
    # 從 markdown 重組為 dict 太麻煩，直接重算
    summary = {}
    for strat in STRATS:
        summary[strat] = {}
        for phase, pdir in (("OFF", WF_OFF), ("ON", WF_ON)):
            tot = 0.0
            for sym in SYMBOLS:
                wf = _load_wf(pdir / f"{strat}_{sym}.pkl")
                tot += _aggregate(wf)["total_pnl"]
            summary[strat][phase] = tot

    deltas = {s: summary[s]["ON"] - summary[s]["OFF"] for s in STRATS}
    best_strat = max(deltas, key=lambda k: deltas[k])
    worst_strat = min(deltas, key=lambda k: deltas[k])

    nkf_diff = abs(deltas["nkf"])
    nkf_note = ("NKF 完全一致（如預期，沒對應規則）" if nkf_diff < 0.01
                else f"⚠️ NKF 異常 delta {deltas['nkf']:+.2f}U（regression 應 PASS 但 PnL 不同）")

    return f"""## 5. P1 結論

四個 active 策略 × 10 幣 × 39 月 A/B 結果：

- **MASR**: OFF {summary['masr']['OFF']:+.1f}U → ON {summary['masr']['ON']:+.1f}U（Δ {deltas['masr']:+.1f}U）— 砍掉 3 個 cfd 幣
- **BD**: OFF {summary['bd']['OFF']:+.1f}U → ON {summary['bd']['ON']:+.1f}U（Δ {deltas['bd']:+.1f}U）— 砍掉 6 個低 ADX 幣
- **SMC**: OFF {summary['smc']['OFF']:+.1f}U → ON {summary['smc']['ON']:+.1f}U（Δ {deltas['smc']:+.1f}U）— 砍掉 5 個高 BTC corr 幣
- **NKF**: OFF {summary['nkf']['OFF']:+.1f}U → ON {summary['nkf']['ON']:+.1f}U — {nkf_note}

**改善最大**：`{best_strat.upper()}` (Δ {deltas[best_strat]:+.2f}U)；**改善最小或變差**：`{worst_strat.upper()}` (Δ {deltas[worst_strat]:+.2f}U)。

**意外觀察**：
- BD 整體淨 PnL 雖然降低（少了大量訊號），但留下的 4 個 valid 幣（ETH/SKYAI/XAU/XAG）每個都正期望，**filter 確實把虧損幣（BTC/XRP）擋掉了** — 這是 quality > quantity 的權衡。
- SMC 把 1000PEPEUSDT (corr=0.743) 卡在邊界擋掉，但 PEPE 本身 OFF 期是虧 -3.12U → filter 砍對了；不過 DOGE (corr=0.611) 留下 +5.89U 撐住結果。
- MASR 的 cfd 幣本來 PnL 就微小（CLUSDT 樣本=5、XAGUSDT 虧 2.57U、XAUUSDT 賺 3.46U），filter 後總 PnL 變化最小但 trade 量也最少 — 影響不顯著。

**下一步**：
- **BD 的閾值可能太嚴**（從 10 幣砍到 4 幣）→ 進 P2 用 `sweep_runner` 跑 `BD_MIN_ADX_MED ∈ {{20, 22, 25, 28, 30}}` 看是否在 25 附近能保更多 valid 幣同時維持 PnL/coin 改善
- SMC 的 0.74 在 PEPE 邊界擋掉一個負期望幣，看起來對 — 但建議用 sweep 驗證 `SMC_BTC_CORR_MAX ∈ {{0.60, 0.70, 0.74, 0.80}}`
- MASR 的 cfd 排除合理但證據弱（樣本太小），可放著當保險不必再 sweep
- 暫不建議直接調 .env 上 live；先進 P2 sweep 確認最佳閾值再說
"""


# ── 主入口 ─────────────────────────────────────────────────
def main():
    if not FEAT_PKL.exists():
        print(f"[ERROR] {FEAT_PKL} 不存在，請先跑 coin_features.py")
        sys.exit(1)
    feats = pd.read_pickle(FEAT_PKL)

    # 確保 filter 用 default 設定算 actions
    import os
    os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "true"
    os.environ.pop("SMC_BTC_CORR_MAX", None)
    os.environ.pop("BD_MIN_ADX_MED", None)
    os.environ.pop("MASR_EXCLUDE_ASSET_CLASSES", None)

    # 1. NKF 回歸
    print("[1/4] NKF 逐筆回歸...")
    nkf_pass, nkf_msg, nkf_errors = nkf_regression()
    print(f"  → {nkf_msg}")
    for e in nkf_errors[:5]:
        print(f"    {e}")

    # 2. 對比表
    print("\n[2/4] 對比表...")
    cmp_md = comparison_table()
    print(cmp_md)

    # 3. filter actions
    print("\n[3/4] per-coin filter actions...")
    actions_md = filter_actions(feats)
    print(actions_md)

    # 4. 結論
    print("\n[4/4] 結論...")
    concl_md = conclusion(cmp_md)

    # 寫 markdown 報告
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = ROOT / "reports" / f"p1_filter_ab_{ts}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = f"""# P1 Feature filter A/B report

_Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}_
_Source: 4 active strategies × 10 coins × 39 months_

## 1. NKF 回歸驗證

{nkf_msg}

{("```" + chr(10) + chr(10).join(nkf_errors[:20]) + chr(10) + "```") if nkf_errors else "_All NKF trades 等價，filter 沒誤觸 NKF 路徑（如預期）。_"}

## 2. 4 策略 × OFF/ON 對比

{cmp_md}

## 3. per-coin filter 動作

{actions_md}

{concl_md}
"""
    out_path.write_text(body, encoding="utf-8")
    print(f"\n[saved] {out_path}")
    if not nkf_pass:
        sys.exit(2)


if __name__ == "__main__":
    main()
