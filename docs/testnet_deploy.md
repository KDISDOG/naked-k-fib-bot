# Testnet Deploy Checklist (P10)

部署 MASR long-only + cfd filter + shadow comparison 到 Binance Futures Testnet。
**主網不在這份文件範圍**——main 網部署需在 testnet paper 完成 1-2 週並通過所有檢查後另行討論。

---

## Stage 1 — Pre-deploy verification（本機，read-only）

逐項勾完才進 Stage 2。

- [ ] **git status 乾淨**
  ```bash
  git status                      # 應為 "nothing to commit, working tree clean"
  git log --oneline -10           # 確認 P10 phase 1/2/3 commits 都在
  ```
  預期看到至少：
  ```
  63f8f21  feat(shadow): P10 phase 3 — shadow_runner + check_signal hook + initial verification
  5636e2e  feat(masr-live): P10 phase 2 — apply P4 config + cfd filter live hook + smoke test
  4742306  docs(recon): P10 phase 1 — bot_main paths / live universe / signal-path equivalence
  ```

- [ ] **Unit tests 全 PASS**
  ```bash
  PYTHONIOENCODING=utf-8 python -m pytest scripts/test_*.py -q
  ```
  預期：`69 passed`（feature_filter 56 + stability_audit 8 + 5 misc）

- [ ] **MASR cfd filter smoke test PASS**
  ```bash
  PYTHONIOENCODING=utf-8 python scripts/_smoke_test_masr_screen.py
  ```
  預期：`✅ MASR screen cfd filter smoke test ALL PASSED`，看到 `10 → 7 (3 skipped: XAUUSDT(cfd), XAGUSDT(cfd), CLUSDT(cfd))`

- [ ] **Shadow integration verification PASS（real_mismatches = 0）**
  ```bash
  PYTHONIOENCODING=utf-8 python scripts/_verify_shadow_initial.py
  ```
  預期：`✅ SHADOW INTEGRATION VERIFIED`，`real_mismatches: 0`。
  
  上次基準：1260 bars / 47 signals / 47 exact / 0 real_mismatch。

如果上述任何一項 FAIL → **不要往下做**。回去修問題並重新跑 verification。

---

## Stage 2 — .env testnet 段落範本

複製這段到 `.env`（如果該檔不存在，從 `.env.example` 複製一份再改）。**`.env` 是 gitignored，testnet API key 絕對不要 commit**。

```env
# ── Binance API（Testnet）─────────────────────────────────────
BINANCE_TESTNET=true
BINANCE_API_KEY=YOUR_TESTNET_API_KEY      # 從 https://testnet.binancefuture.com 申請
BINANCE_SECRET=YOUR_TESTNET_SECRET

# ── 策略選擇（P10 後收斂）──────────────────────────────────
ACTIVE_STRATEGY=ma_sr_breakout

# ── MASR P4 sweep 後保守版（stability-adjusted #1）────────
# 證據：reports/p4_masr_sweep_20260430_1740.md
MASR_RES_LOOKBACK=50
MASR_RES_TOL_ATR_MULT=0.2
MASR_TP1_RR=1.5
MASR_SL_ATR_MULT=2.0

# ── Shadow comparison（testnet 預設 ON）─────────────────────
ENABLE_SHADOW_COMPARE=true

# ── Feature filter（live screen_coins 用，default 已是 cfd-only）─
BACKTEST_USE_FEATURE_FILTERS=true
MASR_EXCLUDE_ASSET_CLASSES=cfd

# ── 風控（testnet 可放保守一些）─────────────────────────────
MAX_LEVERAGE=3
MARGIN_USDT=10
MAX_POSITIONS=10
RISK_PCT_PER_TRADE=0.10
MAX_DAILY_LOSS=0.08

# ── Telegram（可選；testnet alert 仍會發送，建議用獨立的 testnet bot）─
TG_BOT_TOKEN=YOUR_TESTNET_BOT_TOKEN
TG_CHAT_ID=YOUR_TESTNET_CHAT_ID
```

### 申請 Testnet API key

1. 前往 https://testnet.binancefuture.com/
2. 用 GitHub / Google 登入（與 mainnet 帳號**完全分離**——不共用密鑰）
3. 右上角頭像 → API Management → Create API
4. 勾選 "Enable Futures" 權限；**不要勾 "Enable Withdrawals"**
5. 抄下 API Key + Secret，貼進 `.env`

testnet 帳號預設給 100,000 USDT 測試金；如需更多 → API Faucet。

---

## Stage 3 — 部署順序（嚴格遵守）

### Step a. Pre-flight check 重跑一次

切到 testnet `.env` 之後，再跑一次 unit tests 跟 shadow verification：
```bash
PYTHONIOENCODING=utf-8 python -m pytest scripts/test_*.py -q
PYTHONIOENCODING=utf-8 python scripts/_smoke_test_masr_screen.py
PYTHONIOENCODING=utf-8 python scripts/_verify_shadow_initial.py
```
都過了再往下。

### Step b. Dry-run（不下單，只跑 scan + check_signal）

先驗證 testnet client 連得通，能拿 K 線、能跑 screen：
```bash
PYTHONIOENCODING=utf-8 python -c "
import os, sys
sys.path.insert(0,'scripts')
from dotenv import load_dotenv; load_dotenv()
from binance.client import Client
from config import Config
c = Client(Config.BINANCE_API_KEY, Config.BINANCE_SECRET, testnet=Config.BINANCE_TESTNET)
print('endpoint:', c.FUTURES_URL)            # 預期 testnet.binancefuture.com
print('account_balance:', c.futures_account_balance()[:1])
print('exchange_info usdt count:',
    sum(1 for s in c.futures_exchange_info()['symbols']
        if s.get('quoteAsset')=='USDT' and s.get('status')=='TRADING'))
"
```

確認：
- endpoint 是 `testnet.binancefuture.com`（不是 `fapi.binance.com`）
- account_balance 看得到 testnet USDT
- exchange_info 有 100+ USDT 永續

### Step c. 啟動 bot

```bash
# 推薦：開 tmux/screen 跑，避免斷線中斷
tmux new -s naked-bot
cd /path/to/naked-k-fib-bot
PYTHONIOENCODING=utf-8 python scripts/bot_main.py --no-dashboard
```

啟動 log 要看到：
- `BINANCE_TESTNET=true` 之下的 client init
- `[MASR] 選幣完成：N 支入選`（N 應該是 10 或更少）
- `[MASR screen] cfd filter: ... (N skipped: ...)` ← 如果 N>0 → cfd filter 在跑

### Step d. 監控 24h

第一個 24h 主要看「bot 沒崩 + WS 訂閱穩定 + 沒有異常 alert」：

- [ ] bot process 持續活著（`ps aux | grep bot_main`）
- [ ] WS event log 持續進來（`tail -f logs/*.log | grep WS`）
- [ ] **Telegram 沒有 SHADOW MISMATCH alert** ← 最重要
- [ ] 沒有 `error` 或 `exception` 在 log 裡

24h 內可能還沒有訊號（4h × 7 幣 × 1 天 = 42 bars，產生訊號機率視市場）。**沒訊號是正常的**——驗證的是 wiring 而非生財。

---

## Stage 4 — 1 週後檢查

### Shadow mismatch log

```bash
ls -la reports/shadow_diffs/
```
預期：**空目錄或不存在**（shadow_compare 沒寫過 real_mismatch）。

如果有檔案 → 開來看每個 diff，確認 KNOWN_ACCEPTABLE_DIFFS 沒漏掉某種 case。**任何 real_mismatch 出現 → STOP 並回頭修 shadow logic**。

### 訊號發生數 vs 預期

從 testnet log 抽出 MASR 訊號：
```bash
grep "MASR 訊號：" logs/*.log | wc -l
```

從 backtest 推算 1 週預期：
- 39 月 = 1170 天 → 1 週 = 7 天
- P4 audit 顯示 39 月 ~860 trades / 7 coins → 每 coin 每天 ~3 trades
- 7 天 × 7 coins ≈ 5-15 trades 預期

如果實際 < 1：可能 universe 沒入訊號好幣 / market regime 不利
如果實際 > 50：universe 太大 / 過濾沒啟用 / 邏輯 bug

### 訊號 entry vs current price

每筆訊號 alert 進場 → 看 `entry vs market price` 滑點：
- 預期：< 0.1% slippage（4h 收盤後立即下單）
- 異常：> 0.5% slippage → executor 邏輯問題

### API weight 用量

```bash
grep "X-MBX-USED-WEIGHT-1M" logs/*.log | tail -50
```
應在 1800/min 上限以下。靠近 1800 → 有 burst，回查 fetch_klines 是否在 cache miss。

---

## Stage 5 — 全綠後切 main net（不在本文件範圍）

只有以下全部達標才能討論 main net：

- [ ] testnet paper ≥ 1 週
- [ ] **shadow_mismatch_count = 0**（嚴格）
- [ ] 至少 5 筆 trade 完成 entry → exit 全流程
- [ ] 沒有未預期的 exception / SL placement 失敗 / dust 問題
- [ ] BACKLOG 第 1 項（TP1 後 SL trailing vs BE divergence）有了真實出場數據可 reconcile

main net 切換步驟另開 doc，不在 P10 範圍內。

---

## 監控指標（要看的）

| 指標 | 來源 | 預期 / 觸發 |
|------|------|-------------|
| `shadow_mismatch_count` | `reports/shadow_diffs/` 檔案數 | **必須 = 0**；> 0 立即 STOP |
| MASR 訊號產生數 / 預期 | log grep `MASR 訊號：` | 1 週 5-15 筆；< 1 警告；> 50 警告 |
| `entry vs market price` slippage | trade open log | < 0.1% normal；> 0.5% bug |
| API weight 用量 | log `X-MBX-USED-WEIGHT-1M` | < 1800/min |
| WS 重連次數 | log grep `reconnect` | < 3/day（網路波動正常） |

## 不要看的指標（避免誤導）

| 指標 | 為什麼不能看 |
|------|-------------|
| testnet PnL 跟 backtest 比 | 樣本太小不可比；testnet 訂單簿稀薄 fill 不寫實 |
| testnet wr 跟 backtest 比 | 樣本太小（< 30 trades 不下結論） |
| testnet drawdown 跟 backtest 比 | 同上；且 testnet 沒人交易 → MFE/MAE 都不對 |
| 「testnet 賺了沒」 | testnet **不是賺錢測試**；是 wiring/邏輯/穩定性測試 |

---

## 關於 .env 同步

P10 phase 2 改動了 `.env.example`，但 `.env`（你的本機 secrets）需要**手動更新**：

```diff
# .env (gitignored，從 .env.example 同步以下變更)
-MASR_RES_LOOKBACK=100
+MASR_RES_LOOKBACK=50
-MASR_RES_TOL_ATR_MULT=0.3
+MASR_RES_TOL_ATR_MULT=0.2
-MASR_TP1_RR=2.0
+MASR_TP1_RR=1.5
-MASR_SL_ATR_MULT=1.5
+MASR_SL_ATR_MULT=2.0
+ENABLE_SHADOW_COMPARE=true
-ACTIVE_STRATEGY=naked_k_fib,ma_sr_breakout,ma_sr_short
+ACTIVE_STRATEGY=ma_sr_breakout
```

Telegram bot token / Binance API key 在 .env 各自寫死，這份 doc 不會替你套。

---

## 緊急回滾（如果 testnet 出問題）

1. **Bot 行為怪異** → `Ctrl+C` 停止 bot，先看 log。
2. **大量 SHADOW MISMATCH alert** → 立即停 bot，把 `ENABLE_SHADOW_COMPARE=false` 暫關（**這只是降噪不是修 bug**），看 alert 細節，回頭看 `reports/shadow_diffs/`。
3. **bot crash** → 分析 traceback；常見：API key 錯（不是 testnet 的）、rate limit 撞牆、網路斷。
4. **想完全回到 P10 之前的行為** → `git revert 5636e2e 63f8f21`（保留 phase 1 recon doc），重新建 .env。**這個動作會丟失 P4 sweep 的 config 改動**。

---

## 參考文件

- P1 baseline + filter A/B：`reports/p1_filter_ab_*.md`
- P2B-1 candidate validation：`reports/validate_*.md`
- P2B-1.5 NKF stability audit：`reports/p2b15_nkf_audit_summary_*.md`
- P3A cross-strategy stability：`reports/p3a_cross_strategy_stability_*.md`
- **P4 MASR sweep + audit**：`reports/p4_masr_sweep_*.md`（最終 config 來源）
- P10 phase 1 recon：`reports/p10_recon_*.md`
- BACKLOG（已知未做）：`scripts/BACKLOG.md`

---

# Adding short pair (P12D 完成後)

P12D（2026-04-30）完成 MASR Short v2 移植 + cooldown gate + shadow integration。
short pair 上 testnet 的前置條件 + 步驟如下。

## 前置條件

- Long testnet **已跑 ≥ 1 週**，shadow_long_mismatch_count 持續 = 0
- Long 沒有 unexpected exception / SL placement 失敗
- `_verify_shadow_initial_short.py` PASS（`reports/shadow_diffs/` 空）

## Stage S1 — 套 short pair config 到 .env

從 `.env.example` 複製以下 keys 到 `.env`（注意：long config 已經套，short 是新增）：

```env
# === MASR Short P12C.5 fast.top3 ===
MASR_SHORT_VARIANT=fast
MASR_SHORT_RES_LOOKBACK=150
MASR_SHORT_RES_TOL_ATR_MULT=0.4
MASR_SHORT_VOL_MULT=1.2
MASR_SHORT_TP1_RR=1.5
MASR_SHORT_SL_ATR_MULT=2.5
MASR_SHORT_COOLDOWN_BARS=    # 留空 → fallback 至 COOLDOWN_BARS=3

# Shadow 對 short 也生效
ENABLE_SHADOW_COMPARE=true
```

把 `ACTIVE_STRATEGY` 從 `ma_sr_breakout` 改為 long+short：
```diff
-ACTIVE_STRATEGY=ma_sr_breakout
+ACTIVE_STRATEGY=ma_sr_breakout,ma_sr_short
```

## Stage S2 — Pre-deploy verification

```cmd
PYTHONIOENCODING=utf-8 python scripts\_verify_masr_short_cooldown.py
PYTHONIOENCODING=utf-8 python scripts\_verify_shadow_initial_short.py
PYTHONIOENCODING=utf-8 python scripts\_smoke_test_masr_short.py
PYTHONIOENCODING=utf-8 python -m pytest scripts\test_*.py -q
```

預期：
- cooldown verifier：`✅ COOLDOWN EQUIVALENCE VERIFIED`
- shadow initial：`✅ SHADOW SHORT INTEGRATION VERIFIED` (260 signals exact / 0 real_mismatches)
- smoke test：`4/4 PASS`
- pytest：69 passed

任何 FAIL → STOP，不要往下做。

## Stage S3 — 啟動 + 24h 觀察

`tmux` 啟動 bot：
```cmd
PYTHONIOENCODING=utf-8 python scripts\bot_main.py --no-dashboard
```

啟動 log 應該看到：
- `[MASR_SHORT] init variant=fast (cooldown gate enabled)`
- `[MASR_SHORT] 選幣完成：N 支入選`（cfd 如有則 filter log）
- 第一個 short signal 觸發時：
  - `[<sym>] MASR_SHORT v2:fast 訊號：SHORT ...`
  - `[<sym>] shadow short exact match` (debug log)

如果觸發 SL：
- `[<sym>] MASR_SHORT cooldown 設定 → 直到 <T+3h> (reason=SL, bars=3 × 1h)`
- 之後 cooldown 內：`[<sym>] MASR_SHORT cooldown 中（直到 <T>）` (debug log)

## Stage S4 — 1 週後檢查

| 要看的 | 預期 |
|--------|------|
| `reports/shadow_diffs/` short 部分 | 空（0 real_mismatch）|
| short 訊號量 / 週 | sweep 39m × 10 幣 fast.top3 n=1012 → 1 週 ~5-10 筆（視 BTC regime）|
| Long short 訊號是否衝突 | 同幣同時開多空應被 `db.has_opposite_position` 擋 |
| short cooldown gate 觸發數 | log 內 `cooldown 中` 出現次數 ≈ SL 數 × 3（3 個 cooldown bars）|

不要看的指標：
- testnet PnL 對比 backtest baseline — 樣本太小不可比
- Live `cooldown_rejected` 跟 backtest cooldown_acc 比 — 兩邊 cooldown 已 P12D 對齊但比例可能略不同因為 live 同時跑多策略

## Stage S5 — 切 main net 條件（不在 P12D 範圍）

只有以下全部達標才討論 short 上 main：
- [ ] testnet paper short pair ≥ 1 週
- [ ] **shadow_short_mismatch_count = 0**（嚴格）
- [ ] 至少 3 筆 short trade 完成 entry → exit 全流程
- [ ] cooldown gate 至少觸發 2-3 次（觀察是否符合 backtest 預期）
- [ ] 沒有 long/short 同方向自打架
