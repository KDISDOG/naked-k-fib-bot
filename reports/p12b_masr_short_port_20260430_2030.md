# P12B: MASR Short v2 → live port + 等價驗證

_Generated: 2026-04-30 20:30_

把 `scripts/backtest.py:run_backtest_masr_short_v2` 的 logic 移植到
`scripts/strategies/ma_sr_short.py:MaSrShortStrategy`（替代 v1 dead 版），
透過 shared helper `_v2_check_at_bar` 確保 backtest 與 live 完全等價。

---

## 任務 1：v1 logic 封存

`scripts/strategies/ma_sr_short.py`：
- 既有 class `MaSrShortStrategy` → 改名 `MaSrShortV1Deprecated`，加 deprecated docstring
- name 由 "ma_sr_short" → "ma_sr_short_v1_deprecated"（避免 bot_main strategy registry 誤撈）
- bot_main.py 不變（仍 `from strategies.ma_sr_short import MaSrShortStrategy` → 自動拿到下方新 v2 class）

---

## 任務 2：v2 移植到 strategies/ma_sr_short.py

新增結構：

| 函式/類別 | 來源 |
|-----------|------|
| `_align_higher_to_lower_value(df_higher, series, target_time)` | mirror `backtest.py:_align_higher_to_lower` 的單點版 |
| `_v2_find_support(...)` | mirror `backtest.py:_bt_masr_short_find_support` |
| **`_v2_check_at_bar(df_1h, df_4h, df_1d, df_btc_1d, df_btc_4h, bar_idx_1h, variant)`** | **per-bar source-of-truth helper**，逐行對應 backtest line 2972-3105 |
| `class MaSrShortStrategy(BaseStrategy)` | live entry，`__init__(variant=None)` 從 `Config.MASR_SHORT_VARIANT` 讀（default "slow"） |

**關鍵設計**：helper 是純函數，不依賴策略物件 state。check_signal 只是「fetch dfs + 呼叫 helper」的薄包裝。verifier 也呼叫同一 helper，確保 live = backtest。

`scripts/config.py` + `.env.example`：
- 新增 `MASR_SHORT_VARIANT=slow`（live default，per P12 audit ROBUST adj +24.15U）
- 保留 `MASR_SHORT_V2_VARIANT=fast`（backtest default，不變）

---

## 任務 3：等價驗證結果

### 移植過程的 3 個 logic drift（已修）

第一次跑 verifier 時觸發 42 real_mismatches，逐個排查：

1. **ATR 過熱 check 不應在 helper**  
   v1 有 `if atr_v >= atr_q: return None`，但 v2 backtest **沒有**這條 check。  
   修正：helper 移除該檢查（mirror v2 而非 v1）。

2. **Slow variant 的 entry 應該用 i+1，不是 i**  
   backtest line 3072-3078：`if var == "slow": entry_idx = i+1; entry = closes_arr[i+1]`。  
   原 helper 一律用 `cur_close`（= bar i 的 close）。  
   修正：helper 加 `if variant == "slow": entry = df_1h["close"].iloc[bar_idx + 1]`，並 return `entry_time = time[i+1]` 對齊 backtest 的 `open_time`。

3. **Cooldown 計算邊界**  
   backtest 的 SL trade 觸發 `cooldown_until = close_bar + COOLDOWN_BARS`，
   下一次 iteration `i_emit + 1`（slow: open_bar；fast: open_bar+1）開始就已被 block。  
   驗證腳本最初只 block `[close_bar+1, close_bar+6]`，**漏掉 trade 期間 + close_bar 那一根**。  
   修正：verifier 內部 `_build_cooldown_set(... variant)` 改為 block
   `[i_emit+1, close_bar+COOLDOWN_BARS]`，並區分 slow/fast 的 i_emit 不同。

### 等價驗證最終結果

3 syms × 12m × 2 variants = 6 個獨立 audit run：

| Variant | Symbol | bars | bt | live | exact | cooldown_acc | **real_mismatch** |
|---------|--------|-----:|---:|-----:|------:|-------------:|-------------------:|
| slow | BTCUSDT | 8533 | 23 | 26 | 23 | 3 | **0** ✅ |
| slow | ETHUSDT | 8533 | 34 | 48 | 34 | 14 | **0** ✅ |
| slow | SOLUSDT | 8533 | 13 | 22 | 13 | 9 | **0** ✅ |
| fast | BTCUSDT | 8534 | 40 | 58 | 40 | 18 | **0** ✅ |
| fast | ETHUSDT | 8534 | 58 | 94 | 58 | 36 | **0** ✅ |
| fast | SOLUSDT | 8534 | 20 | 33 | 20 | 13 | **0** ✅ |

**全部 backtest emitted signal 都被 live helper 完全等價 reproduce**（exact = bt 數）。  
live 多出來的 signals 100% 落在 backtest cooldown 期內（acceptable，live 的 cooldown 由 `bot_main` + `db.has_open_position` 處理，不在 check_signal 範圍）。

```
✅ MASR Short v2 PORT EQUIVALENCE VERIFIED
   Both slow + fast variants: every backtest signal matches live path
   exact entry/sl/tp/score over 3 syms × 12m × 2 variants
   cooldown asymmetry handled (backtest internal, live handled by bot_main)
```

---

## 任務 4：Smoke test + pytest regression

```
$ python scripts/_smoke_test_masr_short.py
  [1/4] MaSrShortStrategy init slow + fast OK
  [2/4] MaSrShortV1Deprecated 標明 v1_deprecated, name=ma_sr_short_v1_deprecated
  [3/4] feature_filter cfd default = ['cfd']
  [4/4] screen_coins cfd filter (slow + fast variants) PASS
✅ MASR Short v2 smoke test ALL PASSED

$ python -m pytest scripts/test_*.py -q
69 passed in 0.78s   (no regression: P10/P11 既有測試全綠)
```

---

## P12B 結論

- **等價驗證 PASS**：6/6 audit runs zero real_mismatch；live `MaSrShortStrategy.check_signal()` 行為跟 `run_backtest_masr_short_v2` 完全一致（含 fast 與 slow 兩 variant、含 entry/sl/tp/score/timestamp）。
- **Diff 分類**：所有「live=signal bt=None」的差異 100% 落在 backtest 內部 cooldown 期內，acceptable。沒有 entry/sl/tp/score 的數值偏離。
- **bot_main 還沒註冊 short pair**：預期狀況。`ACTIVE_STRATEGY=ma_sr_breakout`（不變）；MaSrShortStrategy 雖已 importable 為 v2，但要把 `ma_sr_short` 加進 .env `ACTIVE_STRATEGY` 才會 live 跑。本輪不動 active list，等 P12C/D 完成後再評估。
- **推薦下一步：P12C sweep**：對 v2 (slow + fast 兩個 variant) 跑 P4 風格 coordinate descent sweep，grid 包含 `MASR_SHORT_RES_LOOKBACK ∈ {50, 75, 100, 125, 150}`、`MASR_SHORT_RES_TOL_ATR_MULT ∈ {0.2, 0.3, 0.4, 0.5}`、`MASR_SHORT_TP1_RR ∈ {1.5, 2.0, 2.5, 3.0}`、`MASR_SHORT_SL_ATR_MULT ∈ {1.0, 1.2, 1.5, 2.0}`。理由：v2 用的 SL/TP/lookback 預設值是 long P4 之前的 old config，P4 sweep 沒對 short 跑過，可能存在更好的 stability-adjusted config。

---

## 下一步建議（不在本輪做）

| Phase | 做什麼 |
|-------|--------|
| **P12C** | v2 sweep（slow + fast 各跑），找 stability-adjusted #1 config |
| **P12D** | shadow comparison hook（同 MASR Long），real_mismatch alert + reports/shadow_diffs/ |
| **P12E** | testnet checklist 加 short pair 段落（怎麼開 hedge mode、雙向倉位風控） |
| 後續 | review 後決定是否把 `ma_sr_short` 加進 `ACTIVE_STRATEGY` |
