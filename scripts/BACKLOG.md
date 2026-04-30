# Backlog — 已知但暫不處理的事

P10 phase 2/3/4 期間刻意延後的議題；每項都附「何時 revisit」的觸發條件，避免變成永久遺忘。

---

## 1. TP1 後 SL 處理：live (ATR trailing) vs backtest (fixed BE) divergence

**發現於**：Phase 1 recon (2026-04-30, `reports/p10_recon_20260430_1752.md`，§1.3.E "5 點 acceptable diff" 第 2 點)

**現況**：
- Live (`MaSrBreakoutStrategy`)：`Signal.use_trailing=True` → `executor` 用 ATR trailing stop
- Backtest (`run_backtest_masr` + `simulate_trade`)：`sl_to_be_after_tp1=True` → 固定移到 entry 保本

**為何延後**：
- 改 backtest trailing 邏輯會讓 P4 sweep 找到的 ROBUST 結論（reports/p4_masr_sweep_*.md）失效——因為 audit 是在 fixed-BE 模型下做的。
- 哪邊是「對的」沒實證——需要小 sweep 驗證 (`SL_AFTER_TP1 ∈ {fixed_BE, atr_0.5x_trail, atr_1x_trail}`) 才能下結論，憑空選一邊都是賭。
- Paper trading 期間可以收集 live exit price 數據，作為 reconcile 依據，比現在憑空猜更可靠。

**何時 revisit**：testnet paper 至少 1 週、有 ≥ 5 筆 trade 完成 TP1 並進入 trailing/BE 階段的真實出場數據後。

**重新評估什麼**：
1. 哪邊出場價平均更好（每筆 (live exit) vs (backtest 預期 exit_price) 比對）？
2. 哪邊 PnL 變動更小？
3. 是否該做 sweep grid: `SL_AFTER_TP1 ∈ {fixed_BE, atr_0.5x_trail, atr_1x_trail}` 找最優？

**Workaround during paper**：
- shadow comparison **不比對** TP1 後的 trade management（只比 entry/SL/TP 訊號）→ 不會誤報
- 在 BACKLOG 留錨點，避免「paper 看起來沒問題 = 沒問題」的錯覺

---

## 2. Universe contract type whitelist

**發現於**：Phase 1 recon (`TRADIFI_PERPETUAL` 沒被 bot_main 過濾，§1.1.D)

**現況**：
- `bot_main.scan_coins()` 只過濾 `quoteAsset=USDT + status=TRADING + not endswith("_PERP") + 黑名單 + 新幣`
- 沒過濾 `contractType` → `TRADIFI_PERPETUAL`（XAU/XAG/CL）會混進候選池
- P10 phase 2 加的 cfd filter 處理當前已知問題（asset_class=cfd 名稱判定），但只是 patch

**為何延後**：
- 本輪 cfd filter 已能 cover 已知 TRADIFI 幣（XAU/XAG/CL）
- 若 Binance 將來加新 contractType（例如 commodity-tracking 或 leveraged token 變種），cfd filter 不會自動 cover
- 這是結構性 universe-layer fix，應該獨立評估，不該擠進當前 deploy 流程

**何時 revisit**：cfd filter 上 testnet 驗證完後 1-2 週，paper 過程中 Binance 若新增任何非 `PERPETUAL` 類型，立即觸發。

**做法**：
```python
# bot_main.py scan_coins() 加：
ALLOWED_CONTRACT_TYPES = {"PERPETUAL"}  # 預設只 PERPETUAL
all_symbols = [
    s["symbol"] for s in info["symbols"]
    if s["quoteAsset"] == "USDT"
    and s["status"] == "TRADING"
    and s.get("contractType") in ALLOWED_CONTRACT_TYPES   # 新增
    and not Config.is_excluded_symbol(s["symbol"])
    and ...
]
```
配合 `.env`：`ALLOWED_CONTRACT_TYPES=PERPETUAL`（預設），讓未來新增類型時可以白名單擴充。

---

## 3. `MASR_MIN_BREAKOUT_PCT` getattr fallback 寫法不一

**發現於**：Phase 1 recon §1.3.E 第 4 點

**現況**：
- Live: `getattr(Config, "MASR_MIN_BREAKOUT_PCT", 0.005)` (fallback 0.005)
- Backtest: `float(Config.MASR_MIN_BREAKOUT_PCT)` (直接讀，default 0.0)
- Config 本身定義 default 0.0 → live 的 0.005 fallback 永遠不觸發

**為何延後**：
- 現況等價（兩邊都讀到 0.0），是 code smell 不是 bug
- 改它不影響任何訊號

**何時 revisit**：下一次 MASR 策略邏輯重構時順手清理。

---

## 4. Score `ADX bonus` live vs backtest divergence

**發現於**：Phase 1 recon §1.3.E 第 3 點

**現況**：
- Live `_score_signal`：算 `ADX > 30` 加 1 分
- Backtest 評分：`# ADX bonus（從 atr_window 推不出，跳過 ADX bonus 在回測中）`
- 結果：同一根 K 線上 live score 可能比 backtest 多 1

**為何延後**：
- Min score 通常設 2，± 1 不會在多數 case 翻訊號
- 補 backtest ADX bonus 會讓 P4 sweep score 全部 +1，影響 stability ranking 解讀
- shadow 已把 score `±1` 列入 KNOWN_ACCEPTABLE_DIFFS

**何時 revisit**：paper 若觀察到「live 進場但 backtest 預期不進場」mismatch 集中在 score boundary (live=2, backtest=1) 時。
