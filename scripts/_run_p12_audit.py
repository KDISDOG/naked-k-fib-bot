"""
P12 Task 3：對訊號量足夠的 MASR_SHORT 版本跑 stability_audit。

從任務 2 結果，v1 只有 3 trades (dead)，v2_fast 1011 trades，v2_slow 594 trades。
audit:
  - v2_fast baseline (no filter)
  - v2_fast p1 (cfd 排除)
  - v2_slow baseline
  - v2_slow p1

stability_audit 用 mode="filter" 透過 env injection；MASR_SHORT 在
feature_filter.py 沒對應規則 → masr 規則不會觸發 short 路徑。
但 audit 的 "p1" 用 strategy="masr_short" 自訂 rule 為 asset_class not_in [cfd]。

實作：在 feature_filter.py 加 MASR_SHORT 支援 (extend _SUPPORTED set + cfg
dict)。如果還沒支援，臨時用 monkey-patching。

策略：寫一個 MASR_SHORT_RULES_JSON env 注入；feature_filter 已支援
"masr_short" strategy name? 讓我先確認。
"""
import os
import sys
import json
import time
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env
for k in ("BACKTEST_USE_FEATURE_FILTERS",
          "MASR_RULES_JSON", "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES",
          "MASR_SHORT_EXCLUDED_SYMBOLS",
          "MASR_SHORT_RULES_JSON"):
    os.environ.pop(k, None)
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

from backtest import run_backtest_masr_short_v2
from feature_filter import _SUPPORTED
print(f"feature_filter._SUPPORTED: {_SUPPORTED}")

# 確認 masr_short 是否已支援
SUPPORTS_MASR_SHORT = "masr_short" in _SUPPORTED
print(f"masr_short in _SUPPORTED: {SUPPORTS_MASR_SHORT}")

# 為了 audit 的 p1 (cfd 排除)，我們用一個 wrapper：
# - run_backtest_masr_short_v2 進場時不啟用任何 filter（feature_filter 沒接 v2）
# - 我們自己在 wrapper 裡先用 classify_asset 擋 cfd，再呼叫 fn
# 這跟 stability_audit mode="filter" 的設計不一致，所以走 mode="config_override" 路徑
# 不對：config_override 是給 ConfigPatch 用，不是 cfd skip
#
# 解：直接在 wrapper 裡判斷 cfd → 回 [] (跟 feature_filter live hook 同樣的「進場 fast skip」)
# 這樣 audit 不需要改 feature_filter

from feature_filter import classify_asset


def _make_v2_wrapper(variant: str, exclude_cfd: bool):
    fn_name = f"run_backtest_masr_short_v2_{variant}"
    if exclude_cfd:
        fn_name += "_cfdfilt"

    def wrapper(client, symbol, months, debug=False, **kw):
        if exclude_cfd and classify_asset(symbol) == "cfd":
            print(f"\n[{symbol} MASR_SHORT_V2:{variant}] FILTERED: asset_class=cfd")
            return []
        return run_backtest_masr_short_v2(client, symbol, months, debug=debug, variant=variant)

    wrapper.__name__ = fn_name
    return wrapper


from stability_audit import audit_candidate_stability

ROOT = Path(__file__).parent.parent
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    out_dir = ROOT / "reports"

    AUDITS = [
        ("v2fast_baseline",    _make_v2_wrapper("fast", exclude_cfd=False), "v2 fast, no filter"),
        ("v2fast_cfd_excluded", _make_v2_wrapper("fast", exclude_cfd=True),  "v2 fast, cfd 排除"),
        ("v2slow_baseline",    _make_v2_wrapper("slow", exclude_cfd=False), "v2 slow, no filter"),
        ("v2slow_cfd_excluded", _make_v2_wrapper("slow", exclude_cfd=True),  "v2 slow, cfd 排除"),
    ]

    results = []
    for i, (cid, fn, label) in enumerate(AUDITS, 1):
        print(f"\n{'='*78}\n [{i}/{len(AUDITS)}] {cid}: {label}\n{'='*78}")
        # 用 mode="filter" 並傳空 rules，等於不啟用 feature_filter；
        # cfd 過濾在 wrapper 內已做完
        res = audit_candidate_stability(
            strategy="masr_short",   # 字串只用於 log/檔名；不會觸發 feature_filter（masr_short 不在 _SUPPORTED）
            candidate_rules=[],
            rule_logic="AND",
            client=client,
            symbols=SYMBOLS,
            fn=fn,
            months=MONTHS,
            n_segments=3,
            candidate_id=cid,
            candidate_label=label,
            output_dir=out_dir,
            mode="filter",
        )
        m = res["metrics"]
        print(f"  → {res['status']}  segs=[{m['seg_pnls'][0]:+.2f}, "
              f"{m['seg_pnls'][1]:+.2f}, {m['seg_pnls'][2]:+.2f}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']}  "
              f"adj={res['stability_adjusted_pnl']:+.2f}U")
        results.append(res)

    # 落 pickle
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"p12_audit_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump(results, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
