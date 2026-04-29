# naked-k-fib-bot

幣安合約多策略自動交易機器人（Binance USDT-M Futures），含 7 個策略、共用選幣 / 風控 / 回測 / Dashboard / Telegram 通知。

支援 Testnet 與正式網路切換（`.env` 的 `BINANCE_TESTNET`）。

---

## 目錄

- [快速啟動](#快速啟動)
- [策略說明](#策略說明)
- [選幣說明](#選幣說明)
- [回測工具說明](#回測工具說明)
- [Dashboard / Telegram](#dashboard--telegram)
- [.env 結構](#env-結構)

---

## 快速啟動

```cmd
REM 1. 複製 .env.example → .env，填入 BINANCE_API_KEY / SECRET / TG_*
REM 2. 編輯 ACTIVE_STRATEGY 選想跑的策略
REM 3. 啟動
c:\python312\python.exe scripts\bot_main.py
```

啟動後：
- Bot 在前台 log，背景跑訊號 / 風控 / 同步
- Dashboard：<http://localhost:8089>
- Log：`bot.log`

**Live bot 不會自動 reload .env，改 .env 後需重啟 process。**

---

## 策略說明

ACTIVE_STRATEGY 支援逗號分隔多策略並行。所有策略繼承 `BaseStrategy`，輸出 `Signal` 物件。

### 1. NKF — 裸 K + Fibonacci（`naked_k_fib`）

| 項目 | 內容 |
|---|---|
| 方向 | 雙向 |
| Timeframe | 15m（可調 `NKF_TIMEFRAME`）|
| 邏輯 | 找 swing high/low → 算 Fib retracement → 價格貼到 0.382/0.5/0.618 + 裸 K 反轉形態 + 量能確認 |
| 出場 | Fib 級別配對：TP1 在下一個 Fib 位、TP2 在 swing extension、SL 在 swing 外側 |
| 適用 | 結構乾淨、有 swing 的趨勢中段 |

### 2. MASR — MA + 水平支撐阻力突破（`ma_sr_breakout`）⭐

| 項目 | 內容 |
|---|---|
| 方向 | 只做多 |
| Timeframe | 4H |
| 邏輯 | 多頭結構（日線 EMA50>EMA200）+ 4H close 突破近 100 根 ≥2 次測試的水平阻力 + EMA20>EMA50 + 量能 ×1.3 + ATR 不過熱（前 20%）+ 距 EMA50<8% |
| 出場 | SL = entry - 1.5×ATR、TP1 = entry + 2.0×SL_dist (50%)、TP2 = entry + 4.0×SL_dist (50%)、TP1 後 SL 移到 entry（保本）|
| 39 月回測 | sweep 後最佳 conservative 配置 PnL +82 / 勝率 49% / MDD 32U |

### 3. MASR Short — MA + 水平支撐破位做空（`ma_sr_short`）

| 項目 | 內容 |
|---|---|
| 方向 | 只做空 |
| Timeframe | 1H（規格不對稱反向，比 Long 短）|
| 邏輯 | **mandatory BTC regime gate**：BTC 4H EMA50<EMA200 + BTC 24h<+5%。個幣：日線 EMA 空頭 + 7d 跌≥5% + 距 30d 高跌≥15% + 2-bar 確認跌破支撐 + EMA20<EMA50 + 量 ×1.5 |
| 反追殺 | 4H RSI > 30、距 EMA200 < 10%、24h 跌 < 8%（不殺底）|
| 出場 | SL = entry + 1.2×ATR（比 Long 緊）、TP1 RR=2、TP2 RR=4、24h 強制平倉 |
| 排除 | PAXGUSDT、XAUUSDT（避險資產與 BTC 反向）|

### 4. 已寫但目前未啟用的策略

| 策略 | 模組 | 簡介 | 暫停原因 |
|---|---|---|---|
| **MR** Mean Reversion | `mean_reversion` | RSI + Bollinger 超賣超買反轉 | 12m 邊際負期望、噪音大 |
| **BD** Breakdown Short | `breakdown_short` | 動能突破支撐做空 | 12m 邊際負期望 |
| **ML** Momentum Long | `momentum_long` | 動能突破阻力做多 | 12m 邊際正但訊號太少 |
| **SMC** Liquidity Sweep | `smc_sweep` | 刺破 swing high/low 後反轉 | 12m 微正、邊際 |
| **Granville** | `granville` | 葛蘭碧 4 法則（1, 2, 5, 6）| 39 月測試勝率 43%、未過 45% 門檻 |
| **MASR Short v2** | （只在 `backtest.py`）| 分級 BTC regime + 鬆綁版 | 純回測比較工具，不上 live |

要啟用：把策略名加進 `ACTIVE_STRATEGY`（逗號分隔）。

---

## 選幣說明

每個策略有獨立 `screen_coins(candidates)` 方法，**互不共享候選池**。每 `RESCAN_MIN`（預設 15 分）執行一次。

### 共用前置過濾（`bot_main.scan_coins`）

1. 黑名單：穩定幣 / 槓桿幣 / 指數類（`Config.EXCLUDED_SYMBOLS`）
2. 新幣 < 60 天上市排除（`NEW_COIN_MIN_DAYS`）

過濾完成的候選池傳給每個 active 策略各自 screen。

### 各策略選幣偏好

| 策略 | 適合幣 | 主要過濾 |
|---|---|---|
| NKF | 中等趨勢 + 結構清晰 | ADX 15-55、ATR 1-6%、量、相對 BTC 強弱（方向感知）、score ≥ 6 |
| MASR Long | 趨勢明確的 alt | 30d 量 ≥50M、日線 EMA50>EMA200、ATR 2-8%、距 EMA200<50%、上市≥180d、按 30d 漲幅 top 10 |
| MASR Short | 已破位的弱勢幣 | **BTC regime 必過** + 30d 量 ≥50M + 日線 EMA 空頭 + 7d 跌≥5% + 距 30d 高跌≥15% + 排除 PAXG/XAU、按距 30d 高跌幅 top 10 |
| MR | RANGE 結構 | 低 ADX、適中波動 |
| BD | 弱勢幣（做空）| 高 ADX + 24h 跑輸 BTC ≥1% |
| ML | 強勢幣（做多）| 高 ADX + 24h 跑贏 BTC ≥1% |
| SMC | 4H 有 HTF 趨勢 | 4H EMA50 與交易方向同向、HYPEUSDT 排除 |
| Granville | 明確趨勢幣 | 10 分制：ADX>25 (3) + 連 5 根與 EMA60 同側 (2) + EMA20 斜率 >0.5% (2) + 24h 量>100M (2) + ATR 1.5-4% (1)，門檻 ≥7 |

### 相關性去重（所有策略共用）

候選池確定後，`_dedupe_correlated_symbols`（`bot_main.py`）做最後一道：
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

REM Granville 9 個月
python scripts\backtest.py --strategy granville --top-n 20 --months 9

REM MASR Short v2 fast/slow 變體比較
python scripts\backtest.py --strategy masrs2 --top-n 30 --months 12 --masrs-v2-variant both --masrs-compare

REM 跑所有策略
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

### 重要選項

| Flag | 用途 |
|---|---|
| `--strategy <name>[,name,...]` | 跑哪些策略，支援多選 |
| `--symbol <SYM>` | 單幣模式 |
| `--symbols <S1,S2,...>` | 多幣明確指定 |
| `--top-n <N>` | 自動拿全市場成交量前 N 大 |
| `--months <N>` | 回測月數 |
| `--max-bars <N>` | NKF 最大持倉根數（預設 48）|
| `--debug-indicators` | 印出篩選 / 訊號診斷 |
| `--exclude-stable` | 排除穩定幣對（預設 true）|
| `--no-regime` | 多幣模式關 regime 模擬 |
| `--masrs-v2-variant fast|slow|both` | MASR Short v2 變體選擇 |
| `--masrs-compare` | 同時跑 v1 + v2 並對照 |
| `--masrs-pool` | 多幣模式輸出每日做空進場池 |
| `--testnet` | 強制用 testnet 抓 K 線（不建議）|

### 內建 cache（無需手動）

回測有兩層 K 線快取，**避免被 Binance IP-ban**：

1. **Memory cache**：同 process 內 instant hit
2. **Disk cache**：`.cache/backtest_klines/<symbol>_<interval>_<months>m.pkl`，TTL 24h

第一次抓會走 API（受 `WeightLimiter` 限速 1800 weight/分），之後重跑同樣參數完全離線。

cache 路徑可由 `BT_KLINE_CACHE_TTL_SEC` 調整。手動清空：刪除 `.cache/backtest_klines/` 即可。

### 輸出格式

回測結果分四部分：

1. **每幣 × 每策略 cell 表**：trades / win% / PnL / MDD / best / worst（★ 推薦、✗ 黑名單候選）
2. **每策略總計**：跨幣彙整
3. **每幣總計**：跨策略彙整，依 PnL 排序
4. **平倉原因分布**：SL / TP1+TP2 / TP1+SL / TP1+BE / TIMEOUT 各自筆數與平均 PnL

每筆 trade 都附 MFE/MAE（`max_favorable_price` / `max_adverse_price`），可由分析腳本讀出做 give-back ratio 等診斷。

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
for t in trades:
    mfe = (getattr(t, "max_favorable_price", t.entry) - t.entry) * t.qty
    # ...計算統計
```

不要改 .env 來測 A/B/C — 用 `config_overrides` 或暫時 monkey-patch `Config`。

---

## Dashboard / Telegram

### Dashboard（FastAPI + Jinja2）

啟動 bot 後 <http://localhost:8089>。

| 區塊 | 內容 |
|---|---|
| KPI 卡片 | 今日 PnL / 勝率 / 報酬率 / 持倉數 / 餘額 |
| 累積 PnL 曲線 | 從 DB 取已平倉淨 PnL，每 5 分鐘刷新 |
| 當前持倉 | 即時 mark price + 未實現 PnL（以 Binance position_information 為真實源）|
| 策略統計卡 | **只顯示 ACTIVE_STRATEGY 內的策略**，自動隱藏其他（每 60 秒重算） |
| 風控參數 | 即時調整 MARGIN_USDT / 緊急全平按鈕 |
| 策略熱切換 | 下拉選 → 寫回 .env，下次排程生效（不重啟）|
| 交易紀錄表 | 最近 50 筆，含 strategy badge、開平倉時間 |

### Telegram

`scripts/notifier.py` 在 4 個事件推送：

1. **開倉**：策略 / 幣 / 方向 / 進場價 / SL / TP 等
2. **平倉**：原因（TP1/TP2/SL/TIMEOUT/MANUAL）/ PnL
3. **每日 23:55 總結**：當日各策略平倉 / 勝率 / 淨 PnL（按策略分項，含開倉中數量）
4. **持倉每小時報**：當前持倉 + 未實現 PnL
5. **異常**：API 失敗、daily_loss_limit 觸發等

策略顯示縮寫對照（`short_map`）：
```
NKF / MR / BD / ML / SMC / MASR / MASRS / GRV
```

需要 `.env` 設 `TG_BOT_TOKEN` 與 `TG_CHAT_ID`，沒設就跳過不發。

---

## .env 結構

```
.env  ← 個人 secret + 自選參數（gitignored，不入版控）
.env.example  ← 模板（committed，加新欄位時兩邊同步）
```

每個策略一塊 config，前綴：`NKF_*` / `MR_*` / `BD_*` / `ML_*` / `SMC_*` / `MASR_*` / `MASR_SHORT_*` / `GRANVILLE_*`。

通用區塊：API、風控、排程、選幣、Telegram、DB、cache 等。

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
