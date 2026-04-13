---
name: naked-k-fib-trading-bot
description: |
    建立一套完整的幣安合約交易機器人，整合裸K（Naked Candlestick）+ 斐波那契（Fibonacci）策略，
    具備自動選幣、自動開單、風險控管、倉位同步、分批止盈、即時 Dashboard。
    觸發時機：
    - 使用者提到「裸K」「斐波那契」「Fib」「合約機器人」「自動開單」
    - 想要幣安合約的自動化交易策略
    - 需要帶有 Dashboard 的加密貨幣量化機器人
    - 提到「2x 槓桿」「合約」「期貨」搭配「自動」「機器人」
    即使使用者只問部分功能（如「幫我建 Dashboard」或「只要選幣模組」），也請載入此 Skill。
---

# 裸K + Fib 幣安合約機器人 Skill (v2)

## 架構概覽

```
Binance Futures API
        │
    Scheduler (掃幣 / 訊號檢查 / 倉位同步)
        │
  ┌─────┴──────────────────────────────┐
Coin         Signal        Risk
Screener     Engine      Manager
(v2 選幣)  (Fractal+Fib) (含手續費)
  └─────┬──────────────────────────────┘
        │
  Order Executor (分批止盈 / breakeven stop)
        │
  ┌─────┴─────────┐
Position    State     Dashboard
Syncer    Manager    (FastAPI)
(NEW)    (v2 DB)
```

## 快速決策樹

```
使用者需求
├── 「幫我選幣」        → python scripts/coin_screener.py --top 10
├── 「建立訊號引擎」    → 參考 scripts/signal_engine.py
├── 「風控怎麼設」      → 參考 scripts/risk_manager.py
├── 「建 Dashboard」    → 參考 dashboard/server.py
├── 「完整機器人」      → python scripts/bot_main.py
└── 「部署上線」        → 參考 references/deployment.md
```

---

## 安全守則（每次執行前必讀）

1. API Key 只開「合約交易」，**絕對不開提幣**
2. 先在幣安 **Testnet** 跑 48 小時
3. 每筆倉位不超過帳戶餘額 **20%** 保證金
4. 永遠保留 `cancel_all_orders()` 緊急撤單邏輯
5. 考慮手續費後 R:R < 1.2 的訊號自動跳過

---

## Step 1：環境安裝與 API 設定

```bash
pip install python-binance pandas pandas-ta fastapi uvicorn \
            aiofiles jinja2 python-dotenv schedule sqlalchemy numpy
```

**`.env` 結構：**

```env
BINANCE_API_KEY=your_key_here
BINANCE_SECRET=your_secret_here
BINANCE_TESTNET=true          # 改 false 才是真實交易
RISK_PER_TRADE=0.03           # 每筆最大風險 3%（可從 Dashboard 調整）
MAX_NOTIONAL_PCT=0.20         # 單筆最大保證金 20%
MAX_POSITIONS=5               # 最大同時持倉數
RESCAN_MIN=15                 # 選幣掃描間隔（分鐘）
SIGNAL_CHECK_MIN=5            # 訊號檢查間隔（分鐘）
SYNC_SEC=30                   # 倉位同步間隔（秒）
COOLDOWN_BARS=6               # 止損後冷卻 K 棒數
MIN_SIGNAL_SCORE=3            # 最低訊號強度
# MAX_LEVERAGE 固定 2x（程式內硬定，不從 .env 讀取）
```

---

## Step 2：Coin Screener v2（選幣模組）

**設計理念**：為裸K+Fib 量身選幣，不是選「低波動」幣，而是選「Fib 有效」的幣。

**評分標準（滿分 12 分，≥ 8 才入選）：**

| 維度         | 條件                                     | 滿分 |
| ------------ | ---------------------------------------- | ---- |
| 流動性品質   | USDT 成交量（用 qav）+ Funding Rate 中性 | 3 分 |
| 趨勢結構     | ADX 20-45 + 有清晰 swing + ATR 1.2-4%    | 3 分 |
| K 棒品質     | 實體占比 ≥ 35% + 方向一致性 ≥ 45%        | 3 分 |
| Fib 歷史回測 | 過去 Fib 位有 ≥ 40% 反應率               | 3 分 |

**額外過濾**：

- 排除上線不到 30 天的新幣
- 使用 `qav` 欄位（真正的 USDT 成交量）而非 `volume * close`
- Funding Rate > 0.1% 的極端幣不加分

```bash
python scripts/coin_screener.py --top 10 --min-score 8
python scripts/coin_screener.py --top 20 --json    # JSON 輸出
```

---

## Step 3：Signal Engine v2（訊號引擎）

### 核心改進

**3.1 — Swing Point 用 Fractal 辨識**

不再簡單取 max/min，而是用 fractal 方法找真正的結構性轉折點：
左邊 N 根和右邊 N 根都比它低/高，才算 swing point。

**3.2 — 多時間框架方向確認**

- 日線 EMA20 > EMA50 + Swing 趨勢向上 → 才做 LONG
- 日線 EMA20 < EMA50 + Swing 趨勢向下 → 才做 SHORT
- 兩者不一致 → 不交易

**3.3 — K 棒收盤確認**

只在 K 棒收盤後才確認裸K形態，避免 K 棒進行中的假訊號。

**3.4 — 裸K 形態方向修正**

TA-Lib / pandas-ta 回傳值：+100 = 看漲，-100 = 看跌。
現在正確檢查回傳值正負來區分多空，而不是只看 ≠ 0。

**3.5 — Fib 結構 TP/SL**

止損止盈基於 Fib 結構，不再用固定 ATR 倍數：

- 進場在 61.8% → 止損放 78.6% 之上，TP1 在 38.2%，TP2 在 0%
- 進場在 38.2% → 止損放 61.8% 之下，TP1 在 23.6%，TP2 在 0%

**3.6 — 入場條件（全部缺一不可）**

1. 價格在 Fib 關鍵位 ± 0.5%
2. 日線趨勢和 Swing 結構方向一致
3. 已收盤的 K 棒出現確認形態（方向正確）
4. 當根成交量 ≥ 20 日均量的 1.3 倍

---

## Step 4：Risk Manager v2（風控模組）

**改進**：

- 手續費計算：taker 0.04%，計入開平倉來回
- 有效止損幅度 = 原始止損 + 2 × 手續費率
- 考慮手續費後 R:R < 1.2 → 自動跳過
- 分批止盈：50% 在 TP1，50% 在 TP2

**護欄規則**：

- 單筆保證金 ≤ 帳戶 20%
- 同時最多 5 個開倉
- 槓桿固定 2x
- 止損幅度範圍：0.3% – 12%

---

## Step 5：Order Executor v2（下單執行）

**改進**：

- 分批止盈下單：TP1（50% qty）+ TP2（剩餘 qty）
- Breakeven Stop：TP1 觸發後自動移止損到入場價
- 精度處理：根據幣安 symbol info 自動調整數量和價格精度
- 緊急撤單：逐幣種取消，避免 API 錯誤

---

## Step 6：Position Syncer（倉位同步）— 新增

**解決的問題**：機器人開倉後 TP/SL 在幣安端觸發，本地 DB 不知道。

**工作流程**（每 30 秒）：

1. 查詢 DB 所有 open/partial 交易
2. 查詢幣安實際倉位
3. 比對差異：
    - 倉位消失 → 完全平倉，更新 DB + 計算 PnL
    - 倉位減少 → 部分平倉（TP1），觸發 breakeven stop
    - 倉位不變 → 無動作

---

## Step 7：State Manager v2（狀態資料庫）

**新增欄位**：

- `tp1` / `tp2`：分批止盈價位
- `qty_closed`：已平倉數量
- `fee` / `net_pnl`：手續費和淨損益
- `breakeven`：是否已移至保本
- `order_id` / `sl_order_id` / `tp1_order_id` / `tp2_order_id`：幣安訂單追蹤

**新增功能**：

- `has_open_position(symbol)` — 防重複開倉
- `in_cooldown(symbol)` — 冷卻期檢查（止損後 N 根 K 棒不開新倉）

---

## Step 8：Dashboard（監控介面）

```bash
python dashboard/server.py --port 8089
# 或隨機器人一起啟動：
python scripts/bot_main.py  # Dashboard 自動啟動在 8089
```

Dashboard 功能：

- 即時 P&L 曲線圖（含手續費，每 5 分鐘自動更新）
- KPI 卡片：今日P&L、總P&L、淨P&L、勝率、帳戶餘額（每 30 秒 API 刷新）
- **當前持倉面板**：即時價格、開倉價、TP1/TP2/止損價、未實現損益、R:R、手動停單按鈕（每 15 秒刷新）
- 歷史交易紀錄表格（最近 50 筆）
- 風控參數調整：每筆風險 %（槓桿固定 2x）
- 一鍵緊急全平按鈕

---

## 啟動完整機器人

```bash
# 1. 設定 .env
cp .env.example .env   # 填入 API Key

# 2. 初始化資料庫
python scripts/init_db.py

# 3. 啟動（含 Dashboard）
python scripts/bot_main.py

# 或不啟動 Dashboard
python scripts/bot_main.py --no-dashboard

# 跳過等待 K 棒收盤（測試用）
python scripts/bot_main.py --skip-wait
```

---

## 腳本索引

| 腳本                         | 用途                                     |
| ---------------------------- | ---------------------------------------- |
| `scripts/bot_main.py`        | 主機器人（Scheduler + 整合所有模組）     |
| `scripts/coin_screener.py`   | v2 選幣：Fib回測+K棒品質+流動性+趨勢結構 |
| `scripts/signal_engine.py`   | v2 訊號：Fractal Swing + Fib TP/SL       |
| `scripts/risk_manager.py`    | v2 風控：含手續費 + 分批止盈 + net R:R   |
| `scripts/order_executor.py`  | v2 下單：分批TP + breakeven stop         |
| `scripts/position_syncer.py` | 倉位同步：偵測平倉 + 觸發 breakeven      |
| `scripts/state_manager.py`   | v2 DB：防重複 + 冷卻期 + 手續費欄位      |
| `scripts/init_db.py`         | 初始化資料庫 Schema                      |
| `dashboard/server.py`        | FastAPI Dashboard 伺服器                 |
