"""
P12B Task 4：MASR Short v2 screen smoke test。

不打 API、不抓 K 線——只驗證 cfd asset_class 過濾邏輯（對稱於 MASR Long
P10 phase 2 smoke test）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_RULES_JSON",
          "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES",
          "MASR_SHORT_VARIANT"):
    os.environ.pop(k, None)


def main():
    import pandas as pd
    from feature_filter import classify_asset, load_feature_filter_config
    from strategies.ma_sr_short import MaSrShortStrategy, MaSrShortV1Deprecated, _v2_check_at_bar

    # 1. 兩個 variant 都能 init
    for variant in ("slow", "fast"):
        s = MaSrShortStrategy(client=None, variant=variant)
        assert s.variant == variant, f"variant {variant} 初始化失敗"
        assert s.name == "ma_sr_short", "name 應為 'ma_sr_short'（v2 暴露為 active）"
    print(f"  [1/4] MaSrShortStrategy init slow + fast OK")

    # 2. v1 仍能 import 且名稱明確 deprecated
    v1 = MaSrShortV1Deprecated(client=None)
    assert v1.name == "ma_sr_short_v1_deprecated", \
        "v1 name 應該標明 deprecated 不會被 bot_main 註冊到 'ma_sr_short'"
    print(f"  [2/4] MaSrShortV1Deprecated 標明 v1_deprecated, name={v1.name}")

    # 3. 變數環境讀取
    cfg = load_feature_filter_config()
    excluded = cfg["MASR_EXCLUDE_ASSET_CLASSES"]
    assert excluded == ["cfd"], f"default cfd excluded list = {excluded!r}"
    print(f"  [3/4] feature_filter cfd default = {excluded}")

    # 4. screen_coins cfd filter integration
    test_universe = [
        "BTCUSDT", "ETHUSDT", "DOGEUSDT", "1000PEPEUSDT", "SOLUSDT",
        "XAUUSDT", "XAGUSDT", "CLUSDT",   # cfd
        "SKYAIUSDT", "XYZRANDOM",         # unknown
    ]
    for variant in ("slow", "fast"):
        strategy = MaSrShortStrategy(client=None, variant=variant)
        # stub _get_klines 回空 df → 後段下游邏輯 continue，cfd hook 已執行
        strategy._get_klines = lambda *a, **kw: pd.DataFrame()
        # 攔截 log
        import logging
        captured = []

        class _Cap(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        log = logging.getLogger("strategy.masr_short")
        log.setLevel(logging.INFO)
        log.handlers = [_Cap()]

        out = strategy.screen_coins(test_universe)
        cfd_log = [m for m in captured if "cfd filter" in m]
        assert cfd_log, f"[{variant}] 應該有 'cfd filter' log；got: {captured[:5]}"
        msg = cfd_log[0]
        assert "10 → 7" in msg, \
            f"[{variant}] 應該 10→7（砍 3 cfd）；got: {msg}"

    print(f"  [4/4] screen_coins cfd filter (slow + fast variants) PASS")

    print("\n✅ MASR Short v2 smoke test ALL PASSED")


if __name__ == "__main__":
    main()
