# naked-k-fib-bot

幣安合約多策略自動交易機器人（Binance USDT-M Futures），含 8 個策略 code、共用選幣 / 風控 / 回測 / Dashboard / Telegram 通知，加上一套 **P1–P10 多輪分析框架**（pattern mining、stability audit、coordinate-descent sweep、live-vs-backtest shadow comparison）。

支援 Testnet 與正式網路切換（`.env` 的 `BINANCE_TESTNET`）。

> **目前狀態**（2026-04-30 P10 後）：`ACTIVE_STRATEGY=ma_sr_breakout`（long-only）。
> 其他策略 code（NKF / MR / BD / ML / SMC / MASR Short / Granville）**全部保留**供未來 regime gate 實驗或重新驗證，但不在 active list。決議證據見 `reports/p3a_*` + `reports/p4_*`。

---

## 目錄

- [快速啟動](#快速啟動)
- [當前 active 策略：MASR](#當前-active-策略masr)
- [其他策略（code 保留，未啟用）](#其他策略code-保留未啟用)
- [選幣說明](#選幣說明)
- [回測工具說明](#回測工具說明)
- [分析框架（P1 → P4 → P10）](#分析框架p1--p4--p10)
- [Live vs backtest shadow comparison](#live-vs-backtest-shadow-comparison)
- [Testnet 部署](#testnet-部署)
- [Dashboard / Telegram](#dashboard--telegram)
- [.env 結構](#env-結構)
- [風控](#風控)
- [重啟流程](#重啟流程)

---

## 快速啟動

```cmd
REM 1. 複製 .env.example → .env，填入 BINANCE_API_KEY / SECRET / TG_*
REM 2. 確認 ACTIVE_STRATEGY=ma_sr_breakout（P10 後的預設）
REM 3. 啟動
c:\python312\python.exe scripts\bot_main.py
```

啟動後：
- Bot 在前台 log，背景跑訊號 / 風控 / 同步
- Dashboard：<http://localhost:8089>
- Log：`bot.log`

**Live bot 不會自動 reload .env，改 .env 後需重啟 process。**

> ⚠️ 第一次部署或從 main 切過來請先看 [`docs/testnet_deploy.md`](docs/testnet_deploy.md)——5 個 stage 的 testnet checklist。

---

## 當前 active 策略：MASR

### MASR — MA + 水平支撐阻力突破（`ma_sr_breakout`）⭐

| 項目 | 內容 |
|---|---|
| 方向 | 只做多 |
| Timeframe | 4H |
| 邏輯 | 多頭結構（日線 EMA50>EMA200）+ 4H close 突破近 N 根 ≥2 次測試的水平阻力 + EMA20>EMA50 + 量能 ×1.3 + ATR 不過熱（前 20%）+ 距 EMA50<8% |
| 出場 | SL = max(entry - 2.0×ATR, EMA50)、TP1 = entry + 1.5×SL_dist (50%)、TP2 = entry + 4.0×SL_dist (50%)、TP1 後 SL trailing（live：ATR / backtest：fixed BE，見 BACKLOG #1） |
| 39 月回測 | P4 sweep 後保守版 ROBUST：3 段全正、wr_std 2.4pp、min_n 267、stability-adj +99.10U |
| Live filter | cfd asset_class（XAU/XAG/CL/CO/NG）自動排除（`feature_filter.classify_asset`）|

**P4 sweep 後的 .env config（已寫入 `.env.example`）**：
```env
MASR_RES_LOOKBACK=50            # 之前 100；sweep 找到 50 stability 更好
MASR_RES_TOL_ATR_MULT=0.2       # 之前 0.3；收緊阻力位容忍度過濾雜訊
MASR_TP1_RR=1.5                 # 之前 2.0；早出 TP1 換高 WR
MASR_SL_ATR_MULT=2.0            # 之前 1.5；放寬 SL 容忍 noise
```

詳見 [`reports/p4_masr_sweep_20260430_1740.md`](reports/p4_masr_sweep_20260430_1740.md)。

---

## 其他策略（code 保留，未啟用）

P3A cross-strategy stability audit (`reports/p3a_*.md`) 顯示這些策略在 39 月 walk-forward 下都 REJECTED 或樣本不足，**所以下 active**——但 code、env 變數、coin_screener 全部保留供未來 regime gate 實驗或重新驗證。

| 策略 | 模組 | 簡介 | 暫停原因 |
|---|---|---|---|
| **NKF** Naked-K Fib | `naked_k_fib` | 裸 K + Fib 反轉 | P2B-1.5 audit 全部 10 candidates REJECTED；wr_std 15.4pp、後段紅利集中 |
| **MR** Mean Reversion | `mean_reversion` | RSI + Bollinger 反轉 | 39m total −22.82U，結構性負期望 |
| **BD** Breakdown Short | `breakdown_short` | 動能突破支撐做空 | baseline + p1 filter 都 REJECTED；雙負段 (seg1/seg3) |
| **ML** Momentum Long | `momentum_long` | 動能突破阻力做多 | 12m 邊際正但訊號太少 |
| **SMC** Liquidity Sweep | `smc_sweep` | 刺破 swing high/low 反轉 | baseline OVERFIT_SUSPECT；filter 後 min_n=7 樣本不可信 |
| **MASR Short** | `ma_sr_short` | 水平支撐破位做空 + BTC regime gate | 39m 樣本只 2-3 trades/coin 無法驗證 |
| **Granville** | `granville` | 葛蘭碧 4 法則（1, 2, 5, 6）| 39m 5-7 trades/coin 樣本不足 |
| **MASR Short v2** | （只在 `backtest.py`）| 分級 BTC regime + 鬆綁版 | 純回測比較工具，沒有 live 路徑 |

要重新啟用某策略：修改 `.env` 的 `ACTIVE_STRATEGY=ma_sr_breakout,naked_k_fib`（逗號分隔），重啟 bot。
**但建議先跑 stability audit 確認穩定性**——code 留著不代表 alpha 還在。

---

## 選幣說明

每個策略有獨立 `screen_coins(candidates)` 方法，**互不共享候選池**。每 `RESCAN_MIN`（預設 15 分）執行一次。

### 共用前置過濾（`bot_main.scan_coins`）

1. `quoteAsset == "USDT"` + `status == "TRADING"` + `not endswith("_PERP")`
2. 黑名單：穩定幣 / 槓桿幣（`Config.is_excluded_symbol`）
3. 新幣 < `NEW_COIN_MIN_DAYS` 天上市排除（預設 60）

過濾完成的候選池（mainnet 約 528 個 USDT 永續）傳給每個 active 策略各自 screen。

> ⚠️ bot_main 沒過濾 `contractType`——`TRADIFI_PERPETUAL`（XAU/XAG/CL）會混進候選池，靠 MASR 的 cfd filter 攔截。`scripts/BACKLOG.md` #2 記錄了未來該加 `ALLOWED_CONTRACT_TYPES` whitelist。

### MASR 選幣偏好（active 中的）

| 過濾 | 預設 | 說明 |
|---|---|---|
| **cfd asset_class 排除** | `["cfd"]` | XAU/XAG/CL/CO/NG 直接砍（P10 phase 2 加入；證據見 `reports/p3a_*`）|
| 30 日量門檻 | 50M USDT | `MASR_SCREEN_VOL_M` |
| 上市天數 | 180 天 | `MASR_MIN_LISTING_DAYS` |
| 日線 EMA 排列 | EMA50 > EMA200 + price > EMA200 | 多頭結構必過 |
| 距 EMA200 上限 | 50% | 避免追過熱 |
| ATR / price 範圍 | 2% – 8% | 太低沒波動、太高過熱 |
| 30 日漲幅最低 | 0%（關閉）| `MASR_MIN_30D_RETURN_PCT` 預設 0 |
| Top N | 10 | 按 30 日漲幅排序取 top |

### 相關性去重（所有策略共用）

候選池確定後 `_dedupe_correlated_symbols`（`bot_main.py`）做最後一道：
- 1H 收盤 100 根 rolling correlation > 0.85 視為高相關
- 同板塊保留分數高的，剔除後位
- 避免同一風險因子重押（例：SOL/JUP/JTO 全押）

---

## 回測工具說明

### 主入口

```cmd
PYTHONIOENCODING=utf-8 c:\python312\python.exe scripts\backtest.py [options]
```

### 常用範例

```cmd
REM 單策略 + 單幣 + 12 月
python scripts\backtest.py --strategy masr --symbol BTCUSDT --months 12

REM 多策略 + top 30 幣 + 12 月
python scripts\backtest.py --strategy masr,nkf --top-n 30 --months 12

REM 指定多幣
python scripts\backtest.py --strategy masr --symbols BTCUSDT,ETHUSDT,SOLUSDT --months 12

REM 跑所有策略（含未啟用的 code 保留版）
python scripts\backtest.py --strategy all --top-n 30 --months 12
```

### CLI 別名

| 別名 | 對應策略 |
|---|---|
| `nkf` | naked_k_fib |
| `mr` | mean_reversion |
| `bd` | breakdown_short |
| `ml` | momentum_long |
| `smc` | smc_sweep |
| `masr` | ma_sr_breakout |
| `masrs` / `masr_short` | ma_sr_short |
| `masrs2` / `masrsv2` | ma_sr_short_v2（純回測）|
| `grv` / `grav` | granville |
| `all` | 全部策略 |

### 內建 cache（無需手動）

兩層 K 線快取，**避免被 Binance IP-ban**：
1. **Memory cache**：同 process 內 instant hit
2. **Disk cache**：`.cache/backtest_klines/<symbol>_<interval>_<months>m.pkl`，TTL 24h

第一次抓會走 API（受 `WeightLimiter` 限速 1800 weight/分），之後重跑同樣參數完全離線。

### 自定義分析腳本

模板（`scripts/analyze_<X>.py`）：

```python
from backtest import run_backtest_<strategy>
from binance.client import Client
from config import Config

trades = run_backtest_<strategy>(
    client, "BTCUSDT", months=12,
    config_overrides={"<KEY>": <value>},  # 不改 .env 就能掃參數
)
```

不要改 .env 來測 A/B/C — 用 `config_overrides` 或 `wf_runner.ConfigPatch`。

---

## 分析框架（P1 → P4 → P10）

過去多輪迭代建立的「pattern → filter → audit → sweep → deploy」管線，全部 commits 都在 main branch。

### Pipeline 概覽

```
coin_features.py      → 算每幣 8 個結構特徵（atr/adx/range/whipsaw/gap/volume/btc_corr/asset_class）
       ↓
pattern_miner.py      → 找 (策略, 特徵) 配對 alpha；single-feature 與 2-feature combo
       ↓
feature_filter.py     → 把 mining 結果落實為 backtest filter rules（rule list + AND/OR）
       ↓
wf_runner.py          → walk-forward 切 N 段，run_walk_forward(fn, symbols, months, n_segments=3)
       ↓
sweep_runner.py       → coordinate descent 找最佳 config（用 win-rate-focused objective）
       ↓
stability_audit.py    → 5 級分類：ROBUST / STABLE_BUT_THIN / REGIME_DEPENDENT / OVERFIT_SUSPECT / REJECTED
       ↓
shadow_runner.py      → live 訊號產生時呼叫 backtest path 比對等價性
```

### Phase 對應

| Phase | 主題 | 主要產出 |
|-------|------|----------|
| **P1**  | feature_filter A/B + universe-wide pattern mining | `reports/p1_filter_ab_*.md`、初版 `feature_filter.py` |
| **P2B-1** | NKF/MR relaxed mining + 2-feature combo + candidate validation | `reports/pattern_relaxed_*.md`、`reports/pattern_2feature_*.md`、`reports/validate_*.md` |
| **P2B-1.5** | NKF candidate stability audit | `reports/p2b15_nkf_audit_summary_*.md` — 結論：**NKF 全部 candidates REJECTED** |
| **P3A** | Cross-strategy stability audit (NKF/SMC/BD/MASR) | `reports/p3a_cross_strategy_stability_*.md` — 結論：**只 MASR 可上 active** |
| **P4**  | MASR coordinate descent sweep + top-3 stability audit | `reports/p4_masr_sweep_*.md` — 找到 ROBUST config（top-3 全部 ROBUST）|
| **P10** | Live deployment recon + cfd filter live hook + shadow comparison + testnet checklist | `reports/p10_recon_*.md`、`docs/testnet_deploy.md` |

### 關鍵腳本

| 腳本 | 用途 |
|------|------|
| `scripts/coin_features.py` | 算 39m universe 的 8 特徵，cache 到 `.cache/coin_features_39m.pkl`（TTL 7 天）|
| `scripts/pattern_miner.py` | 三種 mining：strict (1.5σ AND)、relaxed (1.0σ OR)、2-feature combo |
| `scripts/feature_filter.py` | rule list + AND/OR，支援 SMC/BD/MASR/NKF/MR；live 用 `classify_asset()` 純名稱判定 |
| `scripts/wf_runner.py` | `run_walk_forward(fn, symbols, months, n_segments=3)`，自動切 segment 算 metrics |
| `scripts/sweep_runner.py` | `coordinate_descent_sweep(fn, param_grid, baseline_config)` |
| `scripts/stability_audit.py` | `audit_candidate_stability(... mode="filter" \| "config_override")` 走 wf segment 分類 |
| `scripts/shadow_runner.py` | `shadow_compare_signal()` 在 live 訊號產生時跑 backtest path 比對 |

### Backlog（已知未做）

`scripts/BACKLOG.md` 記錄 4 項刻意延後：
1. TP1 後 SL 處理：live ATR trailing vs backtest fixed BE divergence
2. Universe contract type whitelist (`PERPETUAL` only)
3. `MASR_MIN_BREAKOUT_PCT` getattr fallback 寫法不一（code smell）
4. ADX score bonus live vs backtest（acceptable diff，已在 shadow 容差內）

---

## Live vs backtest shadow comparison

P10 phase 3 加入的 wiring：MASR.check_signal() 產生訊號時，呼叫 backtest path（`_masr_signal_at_bar`）跑同一根 K 線，比對 entry/SL/TP/score。

### Diff 三層分類

| 分類 | 條件 | 行動 |
|------|------|------|
| **exact** | 全欄落在 raw TOLERANCE 內 | log.debug |
| **acceptable** | 落在 `KNOWN_ACCEPTABLE_DIFFS` 4 項已登記偏差內 | log.info |
| **real_mismatch** | 其他 | log.error + Telegram 警報 + 寫 `reports/shadow_diffs/<sym>_<bar_time>.json` |

### Tolerance

| 欄位 | 容差 |
|------|------|
| `direction` | 嚴格相等 |
| `entry / sl / tp1 / tp2` | < 0.05% |
| `score` | ±1（acceptable，因 ADX bonus 差異）|

### Master switch

```env
ENABLE_SHADOW_COMPARE=true   # testnet 預設 ON；prod 穩定後可關
```

### 初始驗證

```cmd
PYTHONIOENCODING=utf-8 python scripts/_verify_shadow_initial.py
```

對 7 幣 × 過去 30 天 4h K 線（1260 bars / 47 signals）跑 shadow_compare_signal。**real_mismatches 必須 = 0**。

---

## Testnet 部署

詳見 [`docs/testnet_deploy.md`](docs/testnet_deploy.md)。5 個 stage：

1. **Pre-deploy verification**：`pytest scripts/test_*.py` + smoke test + shadow init verification 全 PASS
2. **`.env` testnet 段落**：`BINANCE_TESTNET=true` + 申請 testnet API key + 同步 P4 config
3. **部署**：dry-run → 啟動 → 24h 監控
4. **1 週後檢查**：shadow_mismatch_count 必須 = 0、訊號數合理、slippage < 0.1%
5. **切 main net**（out of scope，門檻另議）

### 監控指標

| 要看的 | 預期 |
|--------|------|
| `reports/shadow_diffs/` | 空（0 real_mismatch）|
| MASR 訊號數 / 週 | 5–15 筆 |
| entry slippage | < 0.1% |
| API weight | < 1800/min |

| 不要看的 | 原因 |
|----------|------|
| testnet PnL / wr / drawdown | 樣本太小不可比；testnet 訂單簿稀薄 fill 不寫實 |

---

## Dashboard / Telegram

### Dashboard（FastAPI + Jinja2）

啟動 bot 後 <http://localhost:8089>。

| 區塊 | 內容 |
|---|---|
| KPI 卡片 | 今日 PnL / 勝率 / 報酬率 / 持倉數 / 餘額 |
| 累積 PnL 曲線 | 從 DB 取已平倉淨 PnL，每 5 分鐘刷新 |
| 當前持倉 | 即時 mark price + 未實現 PnL |
| 策略統計卡 | 只顯示 ACTIVE_STRATEGY 內的策略，自動隱藏其他 |
| 風控參數 | 即時調整 MARGIN_USDT / 緊急全平按鈕 |
| 策略熱切換 | 下拉選 → 寫回 .env，下次排程生效 |
| 交易紀錄表 | 最近 50 筆，含 strategy badge |

### Telegram

`scripts/notifier.py` 在以下事件推送：

1. **開倉** / **平倉**（含原因 TP1/TP2/SL/TIMEOUT/MANUAL + PnL）
2. **每日 23:55 總結**：當日各策略勝率 / 淨 PnL
3. **持倉每小時報**：當前持倉 + 未實現 PnL
4. **異常**：API 失敗、daily_loss_limit 觸發
5. **⚠️ SHADOW MISMATCH**（P10 新增）：訊號 live vs backtest 不一致時警報

策略顯示縮寫對照（`short_map`）：`NKF / MR / BD / ML / SMC / MASR / MASRS / GRV`

需要 `.env` 設 `TG_BOT_TOKEN` 與 `TG_CHAT_ID`，沒設就跳過。

---

## .env 結構

```
.env          ← 個人 secret + 自選參數（gitignored，不入版控）
.env.example  ← 模板（committed，加新欄位時兩邊同步）
```

每個策略一塊 config，前綴：`NKF_*` / `MR_*` / `BD_*` / `ML_*` / `SMC_*` / `MASR_*` / `MASR_SHORT_*` / `GRANVILLE_*`。

通用區塊：API、風控、排程、選幣、Telegram、DB、cache、shadow comparison、feature filters。

加新欄位時：
1. `scripts/config.py` 加 `os.getenv(...)`
2. `.env` 與 `.env.example` 同步加說明 + 預設值

---

## 風控

| 護欄 | 預設 | 說明 |
|---|---|---|
| `MAX_LEVERAGE` | 3 | 固定槓桿 |
| `MARGIN_USDT` | 10 | 單筆固定保證金 |
| `RISK_PCT_PER_TRADE` | 0.10 | 每筆最多虧 = MARGIN × 10% |
| `MAX_POSITIONS` | 10 | 同時最多持倉 |
| `MAX_LONGS` / `MAX_SHORTS` | =MAX_POSITIONS | 單邊上限（預設不額外限制）|
| `COOLDOWN_BARS` | 3 | 止損後冷卻 K 棒數 |
| `MAX_DAILY_LOSS` | 0.08 | 每日最大虧損 8% |
| `MAX_SAME_DIR_HIGH_CORR` | 2 | 同方向高相關倉位上限 |
| `HIGH_CORR_THRESHOLD` | 0.8 | 高相關門檻 |
| `DIRECTIONAL_BALANCE_RATIO_MAX` | 3.0 | 多空名目 ≥ 3:1 禁同方向新單 |

---

## 重啟流程

```cmd
REM 1. 找到 python 進程
tasklist | findstr python

REM 2. 停掉
taskkill /PID <pid> /F

REM 3. 改 .env

REM 4. 重啟
c:\python312\python.exe scripts\bot_main.py
```

或直接關閉跑 bot 的 cmd 視窗 → 重開新視窗執行。

---

## Unit tests

```cmd
PYTHONIOENCODING=utf-8 c:\python312\python.exe -m pytest scripts\test_*.py -q
```

預期：69 passed（feature_filter 56 + stability_audit 8 + 5 misc）。

加 smoke + shadow integration verification：
```cmd
PYTHONIOENCODING=utf-8 c:\python312\python.exe scripts\_smoke_test_masr_screen.py
PYTHONIOENCODING=utf-8 c:\python312\python.exe scripts\_verify_shadow_initial.py
```

---

## 三條鐵律（從 SKILL.md 摘）

1. **回測別爆 live IP**：所有 `client.futures_klines` 必走 `weight_aware_call(...)`，回測用 `fetch_klines` 自帶 disk cache。
2. **回測 O(n)**：指標一次 vectorize 算完整 series，主迴圈只用 `.iloc[i]`，不在 loop 裡 `.copy()`、不在 loop 裡印大量資訊。
3. **樣本 < 30 不下結論**：`stability_audit.py` 把這條寫成預設閾值，min_n_trades 不到 30 直接砍 stability score 到 0.5x。

詳見 `.claude/skills/naked-k-fib-backtest/SKILL.md`。

---

## 參考報告

| 報告 | 內容 |
|------|------|
| `reports/p1_filter_ab_*.md` | feature_filter A/B + universe-wide pattern mining |
| `reports/pattern_relaxed_*.md` | NKF/MR relaxed single-feature mining |
| `reports/pattern_2feature_*.md` | 2-feature combo mining |
| `reports/validate_*.md` | P2B-1 candidate validation |
| `reports/p2b15_nkf_audit_summary_*.md` | NKF stability audit (全部 REJECTED) |
| `reports/p3a_cross_strategy_stability_*.md` | 跨 4 策略 stability，MASR 唯一可上 |
| **`reports/p4_masr_sweep_*.md`** | **MASR sweep + audit，最終 config 來源（top-3 全 ROBUST）** |
| `reports/p10_recon_*.md` | live deploy recon |
| `reports/audit_*.md` | 各 candidate stability audit 細節 |
| `reports/shadow_diffs/` | live shadow real_mismatches（應為空目錄）|
