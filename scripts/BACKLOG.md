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

## 4. ~~MASR Short v2: cooldown drift between live and backtest~~ ✅ RESOLVED (P12D)

**狀態**：~~延後~~ → **2026-04-30 P12D 解決**

**解決方式**：
- backtest cooldown 邏輯（line 3151-3152）移植到 `strategies/ma_sr_short.py:on_position_close`
- bot_main 加 strategy registry + dispatcher，`syncer/executor.set_post_close_callback()` 註冊
- live `MaSrShortStrategy.check_signal` 加 cooldown gate（mirror backtest line 2972-2975）

**驗證**：
- `scripts/_verify_masr_short_cooldown.py` 3/3 PASS：BTC bt=42 live=42 / ETH bt=55 live=55 / SOL bt=17 live=17（cooldown 移植前 live 多 30-65%）
- `scripts/_verify_shadow_initial_short.py` 7 syms × 365 days：260 signals 全部 exact match，1056 cooldown_rejected，0 real_mismatches

**證據檔**：`reports/p12d_short_shadow_*.md` / `reports/p12d_cooldown_logic.md`

---

## 4-bis. MASR Long 也應加對稱 cooldown gate

**發現於**：P12D 移植 short cooldown（2026-04-30）

**現況**：
- MASR Long (`strategies/ma_sr_breakout.py:MaSrBreakoutStrategy`) **沒有** `on_position_close` hook
- bot_main 的 `_dispatch_position_close` 對 long 是 no-op（hasattr fallback）
- backtest `run_backtest_masr` 的 cooldown 邏輯（line 2453）跟 short 結構相同，但 live 沒移植
- 跟 short 有同樣的 cooldown drift（live 訊號可能多於 backtest）

**為何延後**：
- MASR Long 已上 testnet checklist（`docs/testnet_deploy.md`），動 long 會延後 testnet 進度
- Long 的訊號量本來就少（4H timeframe），cooldown drift 影響相對小於 short（1H timeframe）
- 等 short cooldown gate 經過 testnet paper 1-2 週證明穩定後，再對稱移植到 long

**何時 revisit**：
- short 上 testnet paper 1 週後（shadow_short_mismatch 持續 = 0）
- 對稱方式：`MaSrBreakoutStrategy.__init__` 加 `_cooldown_until` dict，加 `on_position_close` 方法，docstring 標註「mirror MaSrShortStrategy.on_position_close 設計」

---

## 4-ter. .env.example 跟 config.py 雙處 default 對齊機制

**發現於**：P12C.5 套 fast.top3 時觀察到（`MASR_SHORT_VARIANT` default 在
config.py 跟 .env.example 兩處需手動同步），P12D 又觸發一次（`MASR_SHORT_COOLDOWN_BARS`）

**現況**：
- `scripts/config.py` 用 `os.getenv(NAME, default)` 設 fallback default
- `.env.example` 是 user-facing 文件，列「建議值」
- 兩處的 default value 需要手動同步——但這個契約沒有 enforcement

**為何延後**：
- 不是 bug，是 maintenance pain
- 每加新 env 變數需要小心不要 drift
- 檢查機制可以是單元測試或 commit hook

**何時 revisit**：
- 累積 3-5 個 drift 案例後再投資自動化檢查
- 或加進 SKILL.md「加新 env 必須兩處同步」鐵律

**做法**（未來）：
1. 寫 `scripts/test_env_default_alignment.py` 用 regex parse 兩個檔案，比對 default 值
2. 加進 pytest，CI 自動檢查
3. 或 pre-commit hook

---

## 5. Score `ADX bonus` live vs backtest divergence

**發現於**：P12B 等價驗證 (commit 710a550)；6/6 verifier runs PASS 但 live signal 量比 backtest 多 30-60%。

**觀察**：相同 logic 下，live helper emit 的 signals 數比 backtest 多很多——全部差距由 `cooldown_acc` 解釋（backtest 主迴圈在 SL trade 後設 `cooldown_until = close_bar + COOLDOWN_BARS`，從下一根 bar 開始 skip；live 的 `_v2_check_at_bar` helper 沒有 cooldown 概念）。

| Run | bt signals | live signals | Δ % | cooldown_acc |
|-----|-----------:|-------------:|----:|-------------:|
| slow BTC | 23 | 26 | +13% | 3 |
| slow ETH | 34 | 48 | +41% | 14 |
| slow SOL | 13 | 22 | +69% | 9 |
| fast BTC | 40 | 58 | +45% | 18 |
| fast ETH | 58 | 94 | +62% | 36 |
| fast SOL | 20 | 33 | +65% | 13 |

live 真實環境會由 `bot_main._try_open_for_symbol` + `db.in_cooldown(symbol, COOLDOWN_BARS, bar_minutes=...)` 攔住，所以**「live 多出的 signals 在 production 不會真下單」**——但這個攔截是基於 DB cooldown 表，跟 backtest 主迴圈內建的 `cooldown_until` 不完全一致（DB cooldown 用 `bar_minutes` 換算成秒；backtest cooldown 直接用 bar 數）。

**含意**：
- P12C sweep / audit 用 backtest path 算 PnL → **不含**多出的 30-60% trade。adj_pnl 反映的是「backtest cooldown 模型下的表現」，不是 live 真實表現。
- 多出的 trade 是否賠錢未知 → live 實際 PnL 可能比 sweep 預期低（如果 live cooldown 比 backtest 短、額外 trade 賠錢）。
- 也可能比預期高（如果 live cooldown 更積極、額外 trade 反而被擋掉）。

**何時 revisit**：P12D shadow integration。

**做法**：
1. shadow runner 比對 live 訊號發生點 vs backtest 是否處於 cooldown 區間；
2. 若 live 在 backtest cooldown 區間內仍下單 → log + warning（real_mismatch 變種）；
3. 收集 1-2 週 testnet paper 數據，比對 live 實際 cooldown skip rate vs backtest 預期。

**最終決定**（P12D 完成後）：
- 把 backtest cooldown 同步到 live（推薦：以 backtest 為準，live 端在 strategy.check_signal 內加同樣的 cooldown 檢查）
- 或者反向：把 backtest 改用 bot_main 的 db-based cooldown 模型——但這會讓 backtest 失去 stateless 性質
- 或者接受 drift：把 live cooldown 作為「另一層 risk gate」，backtest 結果視為「上界」而非「準確」

---

## 5. Score `ADX bonus` live vs backtest divergence

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
