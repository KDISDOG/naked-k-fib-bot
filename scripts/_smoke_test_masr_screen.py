"""
P10 phase 2.3：MASR screen_coins cfd filter smoke test。

不打 API、不抓 K 線——只驗證 cfd asset_class 過濾邏輯。
透過 stub 一個假 _get_klines 讓 screen_coins 跑完前段 cfd hook 後就回，
不依賴日線下載。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# 清掉 filter 環境，用 default
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_RULES_JSON",
          "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES"):
    os.environ.pop(k, None)


def main():
    from feature_filter import classify_asset, load_feature_filter_config

    # 1. classify_asset unit test（內部一致性）
    cases = [
        ("BTCUSDT",        "crypto_major"),
        ("ETHUSDT",        "crypto_major"),
        ("DOGEUSDT",       "meme"),
        ("1000PEPEUSDT",   "meme"),
        ("1000SHIBUSDT",   "meme"),
        ("XAUUSDT",        "cfd"),
        ("XAGUSDT",        "cfd"),
        ("CLUSDT",         "cfd"),
        ("XAUTUSDT",       "cfd"),       # XAU prefix → 視為 cfd-related
        ("SOLUSDT",        "crypto_alt"),
        ("SKYAIUSDT",      "crypto_alt"),
        ("XYZRANDOM",      "unknown"),    # 無 USDT 後綴
        ("",               "unknown"),
    ]
    for sym, want in cases:
        got = classify_asset(sym)
        assert got == want, f"classify_asset({sym!r}) → {got!r}, expected {want!r}"
    print(f"  [1/3] classify_asset 13 cases PASS")

    # 2. cfd filter logic（不需 strategy 物件，直接驗證 hook 段邏輯）
    test_universe = [
        "BTCUSDT", "ETHUSDT", "DOGEUSDT", "1000PEPEUSDT", "SOLUSDT",
        "XAUUSDT", "XAGUSDT", "CLUSDT",   # cfd
        "SKYAIUSDT", "XYZRANDOM",         # unknown
    ]
    cfg = load_feature_filter_config()
    excluded = cfg["MASR_EXCLUDE_ASSET_CLASSES"]
    assert excluded == ["cfd"], f"default MASR_EXCLUDE_ASSET_CLASSES = {excluded!r}"

    after = [s for s in test_universe if classify_asset(s) not in excluded]
    assert "XAUUSDT" not in after, "cfd should be filtered"
    assert "XAGUSDT" not in after, "cfd should be filtered"
    assert "CLUSDT"  not in after, "cfd should be filtered"
    assert "BTCUSDT" in after, "major must pass"
    assert "ETHUSDT" in after, "major must pass"
    assert "DOGEUSDT" in after, "meme must pass"
    assert "1000PEPEUSDT" in after, "meme must pass"
    assert "SOLUSDT" in after, "crypto_alt must pass"
    assert "SKYAIUSDT" in after, "crypto_alt must pass"
    assert "XYZRANDOM" in after, "unknown should fail-open pass"
    print(f"  [2/3] cfd filter logic 10 assertions PASS  "
          f"(in={len(test_universe)} → out={len(after)})")

    # 3. 整合：MaSrBreakoutStrategy.screen_coins() 的 cfd hook 段
    # stub _get_klines 回空 df → 後段日線判定全 fail，但 cfd hook 已先執行
    import pandas as pd
    from strategies.ma_sr_breakout import MaSrBreakoutStrategy

    strategy = MaSrBreakoutStrategy(client=None, market_ctx=None, db=None)
    # stub _get_klines 回空（後段邏輯會 continue，cfd hook 已執行）
    strategy._get_klines = lambda *a, **kw: pd.DataFrame()
    # 攔 logging 看 cfd skip 訊息
    import logging
    captured = []

    class _Cap(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    log = logging.getLogger("strategy.masr")
    log.setLevel(logging.INFO)
    log.addHandler(_Cap())

    # 跑（會吐空 list 因為下游沒 K 線，但 cfd hook log 會看得到）
    out = strategy.screen_coins(test_universe)
    cfd_log = [m for m in captured if "cfd filter" in m]
    assert cfd_log, f"應該有 'cfd filter' log。got: {captured[:5]}"
    msg = cfd_log[0]
    assert "10 → 7" in msg or "10 → 7 " in msg or " → 7 " in msg, \
        f"應該 10→7（砍 3 cfd）。got: {msg}"
    print(f"  [3/3] strategy.screen_coins integration PASS")
    print(f"      log: {msg}")

    print("\n✅ MASR screen cfd filter smoke test ALL PASSED")


if __name__ == "__main__":
    main()
