# 多策略交易機器人 Skill (v5.1)

> 基於 naked-k-fib-bot v5 升級，新增安全基礎設施（Telegram 通知 / API 重試 / 每日虧損熔斷 / SL 掛單驗證 / 選幣限流）

| name        | multi-strategy-trading-bot                                                                                                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| description | 升級版幣安合約交易機器人，支援多策略切換：裸K+Fib、RSI均值回歸。具備策略抽象基類、自動選幣、自動開單、風控、倉位同步、分批止盈、即時 Dashboard、Telegram 即時通知、API 重試容錯、每日虧損熔斷。觸發時機：使用者提到「均值回歸」「RSI」「mean reversion」「多策略」「策略切換」「通知」「安全」。 |

## 快速決策樹

```
使用者需求
├── 「建立策略基類」      → 建立 scripts/strategies/base_strategy.py
├── 「搬移原策略」        → 重構到 scripts/strategies/naked_k_fib.py
├── 「實作均值回歸」      → scripts/strategies/mean_reversion.py
├── 「統一配置」          → scripts/config.py
├── 「修改 DB」           → state_manager.py 加 strategy 欄位
├── 「修改主排程」        → bot_main.py 多策略調度
├── 「回測新策略」        → backtest.py --strategy mean_reversion
├── 「升級 Dashboard」   → 加策略標籤 + 分別統計
└── 「測試」             → Testnet 48 小時
```

## 安全守則（繼承 v4，新增）

1. **v4 所有安全守則仍然適用**
2. 兩個策略同時跑時，總持倉不得超過 MAX_POSITIONS（預設 **3**，小資金適用）
3. 槓桿統一 **3x**（Config.MAX_LEVERAGE）— 所有模組與 Dashboard 一致
4. 均值回歸止損 = min(MR_SL_PCT × entry, 1×ATR)，上限 3%
5. MR TP 結構化：TP1 = BB 中軌（高勝率）；TP2 = BB 對側（高賠率）
6. 選幣區間分離：MR 僅 ADX<20；NKF 僅 ADX 20–45（避免重疊互搶）
7. Cooldown 按 per-strategy 計算（同幣種另一策略不被封鎖）
8. R:R 門檻 per-strategy：NKF_MIN_RR=1.2、MR_MIN_RR=1.05
9. MR 超時平倉以**時間**計算（opened_at + MR_TIMEOUT_BARS × timeframe），非排程計數
10. 策略熱切換時，已開倉的單子不受影響，走完原策略邏輯
11. 超時平倉是保命機制，不可關閉

## 架構概覽（v5 升級後）

```
Binance Futures API + CoinGecko
        │
    Config（統一 .env 配置）
        │
    Scheduler (掃幣 / 訊號檢查 / 倉位同步 + 超時檢查 + 每日總結)
        │
  ┌─────┴──────────────────────────────────────────┐
         MarketContext（BTC Dom / 週線 / ADX 狀態偵測）
  ┌─────┴──────────────────────────────────────────┐
  │  Strategy Router（根據 ACTIVE_STRATEGY 調度）  │
  │  ┌─────────────────┬──────────────────────┐    │
  │  │ NakedKFibStrategy│ MeanReversionStrategy│    │
  │  │ (原 v4 邏輯)     │ (方案 A)             │    │
  │  └────────┬────────┴──────────┬───────────┘    │
  │           │    BaseStrategy   │                 │
  │           │    (共用介面)      │                 │
  └─────┬──────────────────────────────────────────┘
        │
  ┌─────┴──────────────────────────────────────────┐
  Risk Manager ──→ Order Executor（分批 TP / breakeven）
  │  └→ daily_loss_exceeded() 熔斷                 │
  │  └→ SL 掛單失敗 → 自動平倉保護                │
  └─────┬──────────────────────────────────────────┘
        │            ↓
  ┌─────┴─────────┐  api_retry（指數退避重試 wrapper）
Position    State     Dashboard    Notifier
Syncer    Manager    (FastAPI)   (Telegram)
(含超時) (v5 DB)    (含策略篩選)  (開/平倉/錯誤/日報)
```

## 新增檔案清單

| 檔案                                   | 用途                                       | 優先級 |
| -------------------------------------- | ------------------------------------------ | ------ |
| `scripts/strategies/__init__.py`       | 策略模組初始化                             | P1     |
| `scripts/strategies/base_strategy.py`  | 策略抽象基類（Signal dataclass + ABC）     | P1     |
| `scripts/strategies/naked_k_fib.py`    | 原 v4 策略重構（從 signal_engine.py 搬入） | P1     |
| `scripts/strategies/mean_reversion.py` | 方案 A：RSI + BB 均值回歸策略              | P2     |
| `scripts/config.py`                    | 統一 .env 配置管理                         | P1     |
| `scripts/notifier.py`                  | Telegram 即時通知（開/平倉、錯誤、日報）   | P1     |
| `scripts/api_retry.py`                 | API 指數退避重試 wrapper（3次，1→2→4秒）   | P1     |

## 修改檔案清單

| 檔案                             | 修改內容                                         | 優先級 |
| -------------------------------- | ------------------------------------------------ | ------ |
| `scripts/bot_main.py`            | 多策略載入 + 調度 + 超時檢查 + 每日虧損熔斷 + TG通知 | P1     |
| `scripts/signal_engine.py`       | 精簡為策略調度器（或廢棄，由 bot_main 直接調度） | P1     |
| `scripts/state_manager.py`       | trades 表加 strategy + timeout_bars 欄位         | P1     |
| `scripts/init_db.py`             | DB schema 加新欄位                               | P1     |
| `scripts/risk_manager.py`        | 支援不同策略風控 + API retry                     | P2     |
| `scripts/order_executor.py`      | SL 掛單驗證 + 失敗自動平倉 + API retry + TG通知  | P1     |
| `scripts/position_syncer.py`     | API retry + 平倉時 TG 通知                       | P2     |
| `scripts/coin_screener.py`       | 選幣限流（每5幣停0.5秒）+ API retry              | P2     |
| `scripts/backtest.py`            | 支援 --strategy 參數                             | P2     |
| `scripts/coin_screener.py`       | 支援策略專屬選幣邏輯                             | P2     |
| `dashboard/server.py`            | 策略標籤 + 分別統計 + 切換 API                   | P3     |
| `dashboard/templates/index.html` | 策略篩選 UI                                      | P3     |
| `.env.example`                   | 新增策略參數                                     | P1     |

## 均值回歸策略速查

### 選幣（`MeanReversionStrategy._score_symbol`，滿分 10 分，≥ 6 才入選）

| 條件                             | 分值                                | 實際判斷                               |
| -------------------------------- | ----------------------------------- | -------------------------------------- |
| 流動性：24h USDT 成交量 ≥ 500 萬 | +2（≥200萬得+1；不足直接跳過）      | `qav.tail(96).sum()`                   |
| 波動適中：ATR(14) 在 1.5%-4%     | +2（1%-1.5%得+1）                   | `atr / close * 100`                    |
| 非趨勢：ADX(14) < 25             | +2（<20得+2；<25得+1；≥25直接排除） | `adx_df["ADX_14"].iloc[-1]`            |
| BB 帶寬適中：3%-15%              | +2（1.5%-3%得+1）                   | `(BBU-BBL)/BBM * 100`                  |
| 均值吸引：近50根觸及BB中軌 ≥ 5次 | +2（≥3次得+1）                      | `abs(close - bb_mid) <= bb_mid × 0.5%` |
| 過濾：近24h跳空 > 5% → 排除      | 硬門檻                              | `max(abs(open-prev_close)/prev_close)` |

> ⚠ **BB 欄位命名**：pandas*ta 產生 `BBU_20_2.0_2.0`（雙 std 後綴），需用 `startswith("BBU*")` 自動偵測，不可硬編碼欄位名稱。

### 入場（`MeanReversionStrategy.check_signal`）

- RSI ≤ `MR_RSI_OVERSOLD`（30）+ 收盤 ≤ BB 下軌 × 1.005（0.5% 容忍）→ 做多
- RSI ≥ `MR_RSI_OVERBOUGHT`（70）+ 收盤 ≥ BB 上軌 × 0.995（0.5% 容忍）→ 做空
- ADX < 25（作為 score bonus，不是硬門檻）
- 評分制（min_score=2，base score=1）：
    - 止跌/見頂 K 棒形態（`_has_reversal`）：+1
    - vol_ok（成交量 ≤ 均量 × `MR_VOL_MULT` = 1.5）：+1
    - RSI 極端（≤15 或 ≥85）：+1
    - Stoch RSI 同向確認：+1
    - MACD 直方圖方向確認：+1

### 止盈止損

- TP1（60%）：BB 中軌（`bb_mid`，若中軌不合理則 +1%）
- TP2（40%）：BB 對側軌（若不合理則 TP1 × 1.5%）
- SL：`min(MR_SL_PCT × entry, 1 × ATR)`，限制在 `[0.5%, 3%]`
- 超時：`MR_TIMEOUT_BARS` 根 K 棒後強制平倉（預設 24 根 15m = 6 小時）

---

## 裸K+Fib 策略速查

### 選幣（`CoinScreener.scan`，滿分 12 分，≥ 8 才入選）

| 維度                                              | 分值                         | 實際判斷                         |
| ------------------------------------------------- | ---------------------------- | -------------------------------- |
| **流動性品質**（3分）                             |                              |                                  |
| 24h USDT 成交量 ≥ 2億                             | +2（≥5千萬得+1）             | `qav.tail(24).sum()`             |
| 資金費率中性（\|FR\| < 0.05%）                    | +1（方向有利+1；極端不利-1） | `futures_funding_rate()`         |
| **趨勢結構品質**（3分）                           |                              |                                  |
| ADX 在 `SCREEN_ADX_MIN`-`SCREEN_ADX_MAX`（15-45） | +1                           | 適中趨勢，Fib 回撤有效           |
| Swing count 在 3-8 個                             | +1                           | `_count_swings(left=5, right=5)` |
| ATR% 在 1.2%-`SCREEN_ATR_MAX`（做多4%/做空8%）    | +1                           | 波動適中，不過激                 |
| **K 棒品質**（3分）                               |                              |                                  |
| 實體佔比 ≥ 50%（≥35%得+1）                        | +2 / +1                      | `body / total_range`             |
| 方向一致性 ≥ 45%                                  | +1                           | 連續同向K棒比例                  |
| **Fib 歷史反應**（3分）                           |                              |                                  |
| 近60根內Fib位觸及後反應，反應率 ≥ 60%             | +3（≥40%得+2；≥25%得+1）     | 0.382/0.5/0.618 位 ±0.8% 容忍    |

**策略 ADX 分工**：NKF = ADX 15-45（有趨勢）；MR = ADX < 25（非趨勢），兩者不重疊。

### 入場（`SignalEngine.check`）

- 1h + 4h 雙時間框架確認
- 裸K 形態（吞噬、錘子、流星等） + Fib 回撤位（0.382 / 0.500 / 0.618）重疊
- Fib 容忍度：±0.5%（`FIB_TOLERANCE`）
- Swing 識別：最近 60 根 K 棒取高低點

### 止盈止損

- TP1（60%）：Fib 延伸 1.272；TP2（40%）：Fib 延伸 1.618
- SL：Swing 極端點外 + ATR 緩衝
- 超時：`--max-bars` 根後強制平倉（預設 48 根）

---

## ✅ 實作完成狀態 (2026-04-17)

### 安全基礎設施（v5.1 新增，已完成）

| 項目                         | 狀態 | 說明                                                                                   |
| ---------------------------- | ---- | -------------------------------------------------------------------------------------- |
| Telegram 通知                | ✅   | `scripts/notifier.py`：開倉🟢/平倉✅❌/SL失敗⚠️/每日虧損暫停🚨/日報📊/啟停🤖🛑 |
| API 指數退避重試             | ✅   | `scripts/api_retry.py`：3 次重試（1→2→4 秒），所有關鍵 API 呼叫已套用                  |
| 每日虧損熔斷                 | ✅   | `bot_main.py` 的 `check_signals()` 開頭呼叫 `daily_loss_exceeded()`，觸發則暫停+TG通知 |
| SL 掛單失敗保護              | ✅   | `order_executor.py`：SL 下單失敗 → 自動市價平倉 + TG 通知，不留裸倉                    |
| 選幣 API 限流                | ✅   | `coin_screener.py`：每 5 個幣種暫停 0.5 秒，避免觸發幣安 1200 weight/min 限制          |
| 每日總結排程                 | ✅   | `bot_main.py`：`schedule.every().day.at("23:55")`，Telegram 推送當日 PnL / 勝率 / 持倉  |

### 安全守則補充（v5.1）

12. SL 掛單失敗時**必須立即平倉**，無 SL 保護的倉位=無限風險
13. API 呼叫均需通過 `retry_api()` wrapper，不可裸呼叫 `client.futures_*`
14. 選幣掃描（`coin_screener.scan()`）必須設 rate limit，禁止 200+ 幣種無間隔連續呼叫
15. `daily_loss_exceeded()` 必須在每次 `check_signals()` 開頭執行，不可跳過
16. Telegram token 不可提交到 Git（`.env` 已在 `.gitignore`）

### 核心架構（已完成）

| 項目                                   | 狀態 | 說明                                                                     |
| -------------------------------------- | ---- | ------------------------------------------------------------------------ |
| `scripts/config.py`                    | ✅   | 統一 `.env` 讀取，所有模組改從此處引用                                   |
| `scripts/strategies/__init__.py`       | ✅   | 匯出 BaseStrategy / Signal / NakedKFibStrategy / MeanReversionStrategy   |
| `scripts/strategies/base_strategy.py`  | ✅   | Signal dataclass + BaseStrategy ABC                                      |
| `scripts/strategies/naked_k_fib.py`    | ✅   | 薄包裝器，直接用 CoinScreener + SignalEngine                             |
| `scripts/strategies/mean_reversion.py` | ✅   | RSI+BB 均值回歸，完整評分+TP/SL                                          |
| `scripts/bot_main.py`                  | ✅   | 多策略調度，`--strategy` CLI，超時自動平倉                               |
| `scripts/state_manager.py`             | ✅   | 新增 `strategy` / `timeout_bars` 欄位，新增 `get_stats_by_strategy()` 等 |
| `scripts/order_executor.py`            | ✅   | `open_position(strategy=)` 參數，`close_position_market()`               |
| `scripts/set_mode.py`                  | ✅   | 寬鬆/嚴格模式切換，含選幣/訊號參數                                       |

### Dashboard（已完成）

| 項目                             | 狀態 | 說明                                                  |
| -------------------------------- | ---- | ----------------------------------------------------- |
| `GET /api/stats/strategy/{name}` | ✅   | 取得單一策略統計                                      |
| `GET /api/stats/all_strategies`  | ✅   | NKF + MR + 合併三段統計                               |
| `POST /api/switch_strategy`      | ✅   | 熱切換 `ACTIVE_STRATEGY`（寫入 `.env`，下次排程生效） |
| `GET /api/active_strategy`       | ✅   | 查詢目前策略                                          |
| strategy 欄位                    | ✅   | 持倉表 / 交易紀錄表新增策略欄，NKF=青色 / MR=紫色     |
| 策略分項統計 Panel               | ✅   | NKF vs MR 並排卡片，每 60 秒自動刷新                  |
| 策略切換 UI                      | ✅   | Controls 區下方 Dropdown + 套用按鈕                   |

### 回測（已完成）

| 項目                    | 狀態 | 說明                                                            |
| ----------------------- | ---- | --------------------------------------------------------------- |
| `BacktestSignalEngine`  | ✅   | NKF 回測引擎（繼承 SignalEngine）                               |
| `BacktestMREngine`      | ✅   | MR 回測引擎（RSI/BB/ADX on local DataFrame）                    |
| `run_backtest()`        | ✅   | NKF 主回測迴圈                                                  |
| `run_backtest_mr()`     | ✅   | MR 主回測迴圈，矢量化預計算指標（O(n)），超時=`MR_TIMEOUT_BARS` |
| `--strategy` 參數       | ✅   | `naked_k_fib` / `mean_reversion` / `all`                        |
| `--adx-max` 參數        | ✅   | MR 回測 ADX 上限（預設 25，可改 20 更嚴格）                     |
| `--scan` 模式           | ✅   | 批量掃描 20 個幣種，找最適合 MR 的幣，輸出排名表                |
| 合併統計                | ✅   | `--strategy all` 時列印 NKF 合計 + MR 合計 + 兩者合併           |
| `BtTrade.strategy` 欄位 | ✅   | 合併報告時可區分來源策略                                        |

---

## 回測執行速查

```bash
# 只測 NKF（預設 1h+4h）
.venv\Scripts\python.exe scripts/backtest.py --strategy naked_k_fib

# 只測 MR（自動讀取 MR_TIMEFRAME，預設 15m）
.venv\Scripts\python.exe scripts/backtest.py --strategy mean_reversion --symbol NEARUSDT --months 3

# 掃描 20 幣找最適合 MR 的（排名輸出）
.venv\Scripts\python.exe scripts/backtest.py --scan --months 3 --adx-max 20

# MR 用更嚴格 ADX 條件測試單幣
.venv\Scripts\python.exe scripts/backtest.py --strategy mean_reversion --symbol NEARUSDT --adx-max 20

# 同時測兩個策略（並印合併績效）
.venv\Scripts\python.exe scripts/backtest.py --strategy all --months 6

# 常用調參
.venv\Scripts\python.exe scripts/backtest.py --strategy naked_k_fib --fib-tol 0.01 --vol-mult 1.0 --skip-vol-rise
```

---

## 啟動速查

```bash
# 啟動 Bot（預設讀 ACTIVE_STRATEGY，兩個策略同跑）
.venv\Scripts\python.exe scripts/bot_main.py

# 只跑指定策略
.venv\Scripts\python.exe scripts/bot_main.py --strategy naked_k_fib
.venv\Scripts\python.exe scripts/bot_main.py --strategy mean_reversion

# 切換寬鬆/嚴格模式
.venv\Scripts\python.exe scripts/set_mode.py loose
.venv\Scripts\python.exe scripts/set_mode.py strict

# 啟動 Dashboard
.venv\Scripts\python.exe dashboard/server.py --port 8089
```

---

## 重要 .env 參數速查

```dotenv
# 策略選擇
ACTIVE_STRATEGY=all          # naked_k_fib / mean_reversion / all

# MR 策略專屬
MR_TIMEFRAME=15m
MR_RSI_PERIOD=14
MR_RSI_OVERSOLD=30           # ← 已更新：30（原始設計值 20 太嚴格）
MR_RSI_OVERBOUGHT=70         # ← 已更新：70（原始設計值 80 太嚴格）
MR_BB_PERIOD=20
MR_BB_STD=2.0
MR_VOL_MULT=1.5              # 成交量門檻（≤均量×1.5，用於評分bonus；非硬門檻）
MR_SL_PCT=0.025              # 止損百分比（最終SL=min(2.5%×entry, 1×ATR)，上限3%）
MR_MIN_SCORE=2               # 最低訊號評分（base=1，需至少1個bonus條件）
MR_TIMEOUT_BARS=24           # 超時平倉根數（15m × 24 = 6 小時）
MR_MAX_POSITIONS=3           # MR 最大持倉數

# NKF 策略專屬
NKF_TIMEFRAME=1h

# 選幣篩選（可透過 set_mode.py loose/strict 批次調整）
SCREEN_MIN_SCORE=6
SCREEN_MIN_VOL_M=50
SCREEN_ADX_MIN=15
SCREEN_ADX_MAX=45

# Telegram 通知（v5.1 新增）
TG_BOT_TOKEN=                # 從 @BotFather 取得
TG_CHAT_ID=                  # 從 @userinfobot 取得
```

---

## DB 欄位速查（trades 表）

| 欄位           | 說明                                                   |
| -------------- | ------------------------------------------------------ |
| `strategy`     | "naked_k_fib" / "mean_reversion"（預設 "naked_k_fib"） |
| `timeout_bars` | MR 超時計數器，每次 signal check 週期 +1               |
| `fib_level`    | NKF 用；MR 填 "—"                                      |
| `pattern`      | NKF 用形態名；MR 填 "MR_REVERSAL"                      |

---

## 常見問題

**Q: MR 超時後沒有平倉？**
A: 確認 `bot_main.py` 的 `check_mr_timeout()` 有在排程中，`MR_TIMEOUT_BARS` 是否設定正確。

**Q: 兩個策略搶同一個幣？**
A: 選幣邏輯已分離：MR 只選 ADX<25，NKF 只選 ADX 15–45，避免重疊。

**Q: 回測 MR 結果全是 TIMEOUT？**
A: MR 設計為震盪盤策略，trending 幣種（ADX≥25）會被過濾。試用震盪幣（如 NEARUSDT）或縮短 `--months`。

**Q: Dashboard 策略切換後沒效果？**
A: 切換寫入 `.env`；Bot 下次排程週期才讀新設定。若要立即生效，重啟 Bot。

**Q: MR 所有幣都虧損？**
A: 用 `--scan --months 3 --adx-max 20` 找當前最適合幣種。若全部虧損代表市場目前趨勢性過強，MR 策略暫時不適用。

**Q: pandas_ta BB 欄位找不到？**
A: pandas*ta 產生的欄位名為 `BBU_20_2.0_2.0`（雙 std 後綴），不是 `BBU_20_2.0`。\*\*一律用 `startswith("BBU*")` 自動偵測\*\*，禁止硬編碼欄位名稱。

**Q: 回測速度很慢（跑超過 2 分鐘）？**
A: 每根 K 棒內重新計算 `ta.rsi()`/`ta.bbands()` 等是 O(n²) 反模式。正確做法：在迴圈外一次性計算全部指標，迴圈內只做 `iloc[i]` 查找。

**Q: Telegram 通知沒收到？**
A: 1) 確認 `.env` 的 `TG_BOT_TOKEN` 和 `TG_CHAT_ID` 已填寫。2) `notifier.py` 需要 `load_dotenv()` 先載入環境變數。3) 確認你已在 Telegram 對 bot 按過 Start。4) 用 `python -c "from notifier import notify; notify._send('test')"` 測試。

**Q: `daily_loss_exceeded()` 何時重置？**
A: 每次呼叫時計算當日已實現 PnL（`get_today_pnl()`）vs 帳戶總餘額。跨日自動重置（因為 today_pnl 只計算當天的交易）。`bot_paused` 需重啟 bot 才會解除。

**Q: SL 掛單失敗後倉位怎麼辦？**
A: `order_executor.py` 會自動市價平倉並發 Telegram 通知。如果連平倉都失敗，會再發一次錯誤通知，需人工介入。

**Q: API retry 最多等多久？**
A: 3 次重試，退避 1s→2s→4s，最長等 7 秒。第 4 次仍失敗則拋出原始例外。
