# P12D: Cooldown logic 移植映射 (backtest → live)

_Generated: 2026-04-30_

從 `scripts/backtest.py:run_backtest_masr_short_v2` 抽出的 cooldown 邏輯規格，
作為移植到 `strategies/ma_sr_short.py:MaSrShortStrategy` 的依據。

---

## 規格（來源：backtest.py）

### 1. Cooldown duration 來源

```python
# backtest.py line 58
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "6"))
```

- 環境變數：**`COOLDOWN_BARS`**（**通用**，不是 strategy-specific）
- backtest.py file fallback：6
- **`.env.example` / `.env` 實際值：3**（live/backtest 兩邊都讀這個）
- 即 .env.example 的 `COOLDOWN_BARS=3` 是有效值；6 永遠不會觸發 fallback
- 單位：**bars**（在 v2 short 1H timeframe 下 = 6 小時）

### 2. Cooldown trigger 條件

```python
# backtest.py line 3151-3152 (run_backtest_masr_short_v2)
if "SL" in trade.result and "BE" not in trade.result:
    cooldown_until = trade.close_bar + COOLDOWN_BARS
```

**只在 raw SL 觸發，且 SL 不是 BE (breakeven SL)**：
- ✅ 觸發：`result == "SL"`、`"SL+TP1"`（複合 SL 但非 BE）
- ❌ 不觸發：`result == "TP1+TP2"`、`"TP1+BE"`、`"TIMEOUT"`、`"BE"`、`""`、`"OPEN"`
- 設計理由：raw SL 代表進場時機/方向誤判，需要冷卻避免追殺；BE / TIMEOUT / TP 都不是「策略錯誤」訊號

### 3. Cooldown 套用在迴圈起點

```python
# backtest.py line 2972 onwards
for i in range(warmup, len(df_tf) - 2):
    if i <= cooldown_until:
        dbg["cooldown"] += 1
        continue
    # ... 訊號邏輯
```

每次迭代的第一道檢查是 cooldown。在 cooldown 區間內 `i <= cooldown_until`，整個 bar 跳過，**不評估任何訊號條件**。

### 4. Cooldown 重置（隱式）

`cooldown_until` 初始值為 `-1`。一旦 SL trigger，被設為 `close_bar + COOLDOWN_BARS`。
**沒有顯式 reset**：當 `i > cooldown_until` 自然脫出。

如果在 cooldown 期間又有新 SL（不可能，因為迴圈不會評估），`cooldown_until` 不會「累加延長」。

### 5. Live 移植映射

| backtest 概念 | live 對應 |
|--------------|-----------|
| `cooldown_until` (int bar index) | `_cooldown_until[symbol]` (`pd.Timestamp` per symbol) |
| `i <= cooldown_until` (skip) | `bar_time <= cooldown_until` (return None) |
| `cooldown_until = close_bar + COOLDOWN_BARS` | `cooldown_until = exit_time + COOLDOWN_BARS × timeframe_minutes` |
| `if "SL" in result and "BE" not in result` | `on_position_close(symbol, exit_reason, exit_time)` 內判定 `"SL" in exit_reason and "BE" not in exit_reason` |
| 全 process 共享一個 `cooldown_until` int | 每個 symbol 一個 cooldown_until（live 同時跑多幣） |

### 6. 換算公式

對於 1H timeframe：
```python
cooldown_duration = COOLDOWN_BARS × 60 minutes = 6 × 60 = 360 minutes
cooldown_end = exit_time + pd.Timedelta(minutes=360)
```

通用公式（任何 timeframe）：
```python
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
tf_min = TF_MINUTES[Config.MASR_SHORT_TIMEFRAME]
cooldown_end = exit_time + pd.Timedelta(minutes=COOLDOWN_BARS × tf_min)
```

---

## .env 變數選擇

兩個選項：

**A. 共用 `Config.COOLDOWN_BARS`**（現況、最簡）：
- ✅ 直接跟 backtest 對齊（同一個常數）
- ✅ 不增加 .env 複雜度
- ❌ 改動 `COOLDOWN_BARS` 會影響所有策略（NKF/MR/BD/SMC/Granville/MASR Long/MASR Short）

**B. 加 `MASR_SHORT_COOLDOWN_BARS`，default = `COOLDOWN_BARS`**：
- ✅ 可 strategy-specific 調整
- ❌ 需要兩處同步維護
- ⚠️ 若 user 改 `MASR_SHORT_COOLDOWN_BARS=10` 但 backtest 仍讀 `COOLDOWN_BARS=6`，equivalence drift

**選擇：B，但 default 走 fallback 到 `COOLDOWN_BARS`**——保留 strategy-specific
override 的彈性，但預設行為跟 backtest 一致。`MASR_SHORT_COOLDOWN_BARS=` 留空
時 fallback 至 `COOLDOWN_BARS=6`。

---

## 驗證計劃（Task 2）

P12B 等價驗證 PASS 但 live signal 比 backtest 多 30-60%。差距 100% 由 backtest
內建 cooldown 解釋（live helper 沒有 cooldown 概念）。

P12D 任務 2 驗證：
1. live `_cooldown_until` 套用後，每根 bar 在 cooldown 內 → check_signal return None
2. 模擬 bot 流程：訊號觸發 → 開倉 → 持倉到 backtest 出場時間 → on_position_close → 進入 cooldown → 後續 bar 應 skip
3. 跟 backtest 訊號量 **exact 對齊**（不再是 live > bt + cooldown_acc 模式）

期望結果：
- BTC fast.top3：bt=42, live=42, real_mismatches=0
- ETH fast.top3：bt=55, live=55, real_mismatches=0
- SOL fast.top3：bt=17, live=17, real_mismatches=0
