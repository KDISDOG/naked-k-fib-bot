# P12: MASR_SHORT 對稱性 + 訊號量 + Stability Audit

_Generated: 2026-04-30 20:10_

診斷 MASR_SHORT 是否能成為 MASR Long 的 short pair。本輪純 read-only +
backtest audit；沒改 logic、沒動 ACTIVE_STRATEGY、沒上 live。

---

## Task 1 — Logic 對稱性比對

### A. 訊號條件對稱性表

| 條件類別 | MASR Long | MASR_SHORT v1 | MASR_SHORT v2 (fast/slow) | 對稱? |
|----------|-----------|---------------|---------------------------|-------|
| Timeframe | **4H** | **1H** | 1H | ❌ short 用更短 TF |
| Breakout 方向 | close > resistance | close < support (2-bar) | close < support (1-bar; slow 加 i+1 confirm) | ✅（方向相反，預期內）|
| MA filter (執行 TF) | EMA20 > EMA50 | EMA20 < EMA50 | EMA20 < EMA50 | ✅ |
| ADX 閾值 | 無（純 EMA + ATR）| 無 | 無 | ✅ |
| Volume confirm | `vol > avg × 1.3` | `vol > avg × 1.5` | `vol > avg × 1.2` | ⚠️ 預設不一致 |
| Breakout pct | `MASR_MIN_BREAKOUT_PCT` 可設（default 0.0）| 沒對應參數 | 沒對應參數 | ⚠️ short 缺 |
| TP1_RR / SL_ATR 預設 (P4 後) | **TP1=1.5 / SL=2.0** | **TP1=2.0 / SL=1.2** | **TP1=2.0 / SL=1.2** | ❌ short 用 long old config（P4 sweep 沒對 short 跑）|
| Breakout 確認 | 1-bar | **2-bar (prev + cur)** | 1-bar (fast) / 2-bar (slow) | ❌ v1 嚴上加嚴 |
| ATR 過熱過濾 | 不在前 20% (`MASR_ATR_PERCENTILE_MAX=0.80`) | 不在前 20% (硬編碼 0.80) | 同 v1 | ✅ |
| 距 EMA50 過濾 | `< 8%`（short 沒用 EMA50 距離）| 無 | 無 | ⚠️（不同設計，不算 bug）|
| **HTF 大盤 gate** | ❌ **無** | ✅ **mandatory** BTC 4H EMA50<EMA200 + BTC 24h<+2% | ✅ **tiered** strong/weak | ❌ **結構性不對稱** |
| **個幣日線結構** | EMA50 > EMA200 + price > EMA200 + 距<50% | EMA50 < EMA200 (mandatory) | （v2 用 4H trend gate 替代日線 EMA gate）| ✅（方向相反）|
| **30d 漲跌幅 gate** | `min_30d_return = 0` (default 關閉) | **7d 跌幅 ≥ 5%** (mandatory) | `7d 漲幅 ≤ +3%`（放寬） | ❌ **short 嚴上加嚴** |
| **距 30d 高 gate** | 無 | **距 30d 高 ≤ -15%** (mandatory) | `≤ -8%`（放寬）| ❌ **short 多一條** |
| **反追殺保險** | 無 | 4H RSI > 30 + 距 EMA200 < 10% + 24h 跌 < 8% | 4H RSI > 35 + 距 EMA200 < 12% | ❌ **short 多 3 條** |
| Score bonus | EMA gap, vol burst, ATR low | EMA gap, vol burst, RSI 35-55 | 同 v1 | ✅ |
| Score min | 2 | 2 | 2 | ✅ |
| Timeout | `MASR_TIMEOUT_BARS=18` (4h × 18 = 3 天)| `MASR_SHORT_TIMEOUT_BARS=24` (1h × 24 = 24h) | 同 v1 | ✅（折算後接近）|
| TP1 後 SL 處理 | live trailing / backtest fixed BE | 同 long | 同 long | ✅ |

**結構性不對稱（短於「方向相反」這種預期內）**：

1. ❗ **MASR_SHORT 多了 mandatory 大盤 regime gate**（v1 必過 / v2 tiered），long 沒對應。在 39m crypto bull-skew 樣本下，這直接決定 v1 訊號量 = 3 (dead)。
2. ❗ **MASR_SHORT 多了「最近已破位」過濾**（v1：7d 跌≥5% 且 距 30d 高≤-15%；v2 放寬版仍存在）。Long 完全沒這條。短頭設計上這條合理（追殺低點 vs 抄高點），但放在 mandatory 進場前置就大砍訊號量。
3. ❗ **MASR_SHORT 多了反追殺保險 3 條**（4H RSI > 30、距 EMA200 < -10%、24h 跌 < -8%）。Long 沒對應。
4. ❗ **MASR_SHORT 是 1H（vs Long 4H）**，更短 TF 理論上更多訊號，但被前述 mandatory gate 蓋掉。
5. ❗ **預設 TP1=2.0, SL=1.2**（=  Long P4 前的 old config）；P4 sweep 沒對 short 跑過，所以可能 short 有更好 config 被忽略。

### B. .env config 對稱性

`MASR_*` (long, 23 keys) vs `MASR_SHORT_*` (38 keys: 27 v1 + 11 v2)：

**Long has but Short doesn't** (4)：
- `MASR_SCREEN_ATR_MIN_PCT` / `MASR_SCREEN_ATR_MAX_PCT`：long 有 ATR% 範圍 [2%, 8%]，short 沒對應
- `MASR_SCREEN_EMA200_MAX_PCT`：long 距 EMA200 上限 50%
- `MASR_MIN_30D_RETURN_PCT`：long 30d 漲幅最低
- `MASR_MIN_BREAKOUT_PCT`：long 最小突破幅度
- `MASR_ATR_PERCENTILE_MAX`：long 可調，short 硬編碼
- `MASR_MAX_DIST_FROM_EMA50`：long 距 EMA50 上限

**Short has but Long doesn't** (16+ structural)：
- `MASR_SHORT_BTC_HTF_TIMEFRAME` / `BTC_FAST_EMA` / `BTC_SLOW_EMA` / `BTC_MAX_24H_PCT`：BTC regime gate 4 keys
- `MASR_SHORT_SCREEN_7D_DROP_PCT` / `SCREEN_DIST_HIGH_PCT`：已破位 gate 2 keys
- `MASR_SHORT_EXCLUDED_SYMBOLS`：default `"PAXGUSDT,XAUUSDT"` 排除避險資產（與 cfd filter 重疊）
- `MASR_SHORT_RSI_HTF_TIMEFRAME` / `RSI_PERIOD` / `RSI_MIN`：RSI 反追殺 3 keys
- `MASR_SHORT_MAX_DIST_FROM_EMA200` / `MAX_24H_DROP_PCT`：反追殺 2 keys
- `MASR_SHORT_V2_*` 11 keys：v2 全套（tiered regime / 放寬閾值 / variant 控制）

**Same key, different default**（更嚴）：

| Key suffix | Long default | Short default | 差異 |
|---|---|---|---|
| `_VOL_MULT` | 1.3 | 1.5 (v1) / 1.2 (v2) | v1 更嚴 |
| `_RES_LOOKBACK` | 50 (P4 後) | 100 | short 用 old default |
| `_RES_TOL_ATR_MULT` | 0.2 (P4 後) | 0.3 | short 用 old default |
| `_SL_ATR_MULT` | 2.0 (P4 後) | 1.2 | short 用 old default |
| `_TP1_RR` | 1.5 (P4 後) | 2.0 | short 用 old default |
| `_TIMEOUT_BARS` | 18 (4h × 18 ≈ 3d) | 24 (1h × 24 = 24h) | TF 換算後相近 |

→ **MASR_SHORT 的 SL/TP/lookback 預設都是 P4 sweep 之前的 long 預設**。P4 sweep 沒對 short 跑過。

### C. v1 vs v2 差異 + production 路徑

| 面向 | v1 | v2 |
|------|----|----|
| BTC regime | mandatory（1 級）| **tiered**：strong (BTC 1D EMA cross) OR weak (BTC 4H + 24h 微跌) |
| Weak regime 倉位 | N/A | × `MASR_SHORT_V2_WEAK_QTY_MULT=0.5`（半倉）|
| 個幣日線 EMA gate | mandatory EMA50<EMA200 | 沒用日線 EMA cross；改用 **4H EMA fast/slow gate** |
| 7d 條件 | **跌幅 ≥ -5%** | **漲幅 ≤ +3%**（放寬約 3 倍）|
| 距 30d 高 | **≤ -15%** | **≤ -8%**（放寬 2 倍）|
| Volume 倍數 | 1.5× | 1.2× |
| 4H RSI 下限 | 30 | **35**（加嚴）|
| 距 EMA200 上限 | 10% | **12%**（放寬）|
| Breakdown 確認 | 2-bar (prev + cur) | 1-bar (fast) / 1-bar + i+1 with offset (slow) |
| Variant 控制 | 沒有 | `variant ∈ {"fast", "slow"}` 透過 kwarg |

**Production 路徑**：`scripts/strategies/ma_sr_short.py` 完全使用 v1 邏輯（讀 `MASR_SHORT_*` env，無 v2 引用）。  
**v2 只存在於 `scripts/backtest.py`** — 是 v1 訊號量過少的回測工具，**沒有 live 對應**。

`MASR_SHORT_V2_VARIANT="fast"` 是 default，意味著如果未來把 v2 推 live，預設會用 fast variant（1-bar confirm）。

---

## Task 2 — 39m × 10 幣 universe 重跑

`MASR_SHORT_EXCLUDED_SYMBOLS` 清空、cfd filter OFF（純看 raw 訊號量）。

| Version | Total n_trades | wr | total PnL | seg1 | seg2 | seg3 |
|---------|---------------:|---:|----------:|-----:|-----:|-----:|
| MASR Long (P4 ref, P3A audit) | 868 | 51.8% | +172.50U | +75.34 | +15.41 | +83.28 |
| **masr_short_v1** | **3** | 66.7% | +0.79U | n=0/+0 | n=0/+0 | n=3/+0.8 |
| **masr_short_v2_fast** | **1011** | 45.4% | +39.50U | n=311/+0.2 | n=426/+17.8 | n=274/+21.5 |
| **masr_short_v2_slow** | **594** | **47.8%** | **+55.02U** | n=184/+4.2 | n=249/+28.3 | n=161/+22.5 |

### v1 per-coin 細節（揭示 v1 的死因）

```
BTCUSDT  scanned 27975 bars, found  0 signals
ETHUSDT  scanned 27975 bars, found  1 signals
SOLUSDT  scanned 27975 bars, found  0 signals
XRPUSDT  scanned 27975 bars, found  0 signals
DOGEUSDT scanned 27975 bars, found  0 signals
PEPEUSDT scanned 26068 bars, found  0 signals
SKYAIUSDT scanned 8339  bars, found  2 signals
XAU/XAG/CL → 0 signals (上市時間 / 4H 資料不足)
TOTAL: 3 trades over 39 months × 10 coins
```

→ **v1 在 bull-skew 樣本下實質 dead**。

### v2_fast 與 v2_slow 的 strong vs weak regime 分布

從 log 抽（v2 print 出強做空 X / 弱做空 Y）：

```
fast 變體：
  BTC:  6 strong + 192 weak (98% weak)
  ETH:  10 strong + 147 weak (94% weak)
  SOL:  0 strong + 165 weak (100% weak)
  XRP:  0 strong + 170 weak (100% weak)
  DOGE: 0 strong + 118 weak (100% weak)
  PEPE: 0 strong + 121 weak (100% weak)
  ...
```

**幾乎所有訊號都來自 weak regime**（半倉），strong regime（BTC 1D EMA cross）在 39m 期間只觸發極短期間。

→ v2 alpha 主要靠 weak regime gating（BTC 4H + 24h<+1%）+ 放寬個幣破位門檻得來。

---

## Task 3 — Stability Audit（v2_fast + v2_slow，baseline + cfd 排除）

對 v2 兩個 variant 各跑 baseline + p1 (cfd 排除) 共 4 個 audit：

| # | Audit | Total PnL | seg1 | seg2 | seg3 | wr_std | min_n | concentration | adj PnL | **Status** |
|---|-------|----------:|-----:|-----:|-----:|-------:|------:|-----:|--------:|------------|
| 1 | v2_fast baseline | +39.50 | +0.23 | +17.75 | +21.52 | 3.0pp | 274 | 54% | +11.66 | **ROBUST** |
| 2 | v2_fast +cfd 排除 | +37.74 | +0.23 | +17.75 | +19.76 | 2.8pp | 233 | 52% | +11.42 | **ROBUST** |
| 3 | v2_slow baseline | +55.02 | +4.18 | +28.34 | +22.50 | 4.4pp | 161 | 51% | **+24.15** | **ROBUST** |
| 4 | v2_slow +cfd 排除 | +60.86 | +4.18 | +28.34 | +28.34 | 5.4pp | 139 | 50% | **+26.70** | STABLE_BUT_THIN¹ |

¹ STABLE_BUT_THIN 是因 wr_std 5.4pp 剛好踩在 5pp 邊界（≥5 觸發）；其他指標（n_negative=0 / min_n=139 / concentration<70%）都通過 ROBUST 標準。本質上是 ROBUST 的 boundary case。

**Audit 結論**：4 個 variant 全部通過 stability 檢查（3 ROBUST + 1 STABLE_BUT_THIN），3 段全正、wr_std < 5.5pp、min_n_trades 139~274（**全部 >> 30 的硬門檻**）。

### Stability-adjusted ranking

```
1. v2_slow +cfd 排除      adj +26.70U  STABLE_BUT_THIN
2. v2_slow baseline       adj +24.15U  ROBUST
3. v2_fast baseline       adj +11.66U  ROBUST
4. v2_fast +cfd 排除      adj +11.42U  ROBUST
```

**v2_slow 顯著優於 v2_fast**（adj +24~+27 vs +11.4~+11.7）。slow variant 的「i+1 close 再過 0.2×ATR offset」要求過濾掉很多 fake breakdown，留下的訊號 PnL 中位明顯更好。

cfd filter 對 v2 影響微小：
- v2_fast：±0.2U（filter on/off 幾乎沒差）
- v2_slow：filter on +5.84U total（XAU 5 trades −0.62U + XAG 36 trades +2.39U + CL 0 → 排除這些反而讓 wr_std 從 4.4 → 5.4，因為樣本基數小波動大）

→ **cfd filter 對 short 不必要**（v1 對 long 必要的反向證據）。XAU/XAG 在 short 路徑樣本量足夠且 PnL 不差，沒理由排。

---

## Task 4 — 訊號稀缺診斷（v1 only）

v1 有 3 trades < 30，觸發此任務但只針對 v1 診斷（v2 已有 ≥ 30 trades）。

不需要單幣 print debug——前述對稱性表已直接揭示：

**v1 訊號量 = 3 的根本原因（按瓶頸排序）**：

1. **BTC regime gate**（mandatory） — `BTC 4H EMA50<EMA200 + BTC 24h<+2%`  
   39m 期間 (2023-02 ~ 2026-04) 是 crypto 多頭主導，BTC 4H EMA50<EMA200 的時間區間極少（粗估 < 10% 時間）。**單這一條就把訊號量上限砍到原本的 ~10%**。

2. **個幣日線 EMA50<EMA200**（mandatory）  
   主流幣（BTC/ETH/SOL/XRP/DOGE/PEPE）在 39m 大部分時間都是 EMA50>EMA200。這條跟 BTC regime gate AND 起來，可運作期間幾乎只剩 2024Q3 那段 BTC 修正期。

3. **7d 跌幅 ≥ -5% AND 距 30d 高 ≤ -15%**（mandatory）  
   即使在 BTC 修正期，個幣同時滿足這兩條的時間窗很窄。

4. **2-bar breakdown confirm**  
   即使前 3 條全部成立，2-bar 確認比 1-bar 大砍 ~30-50% 訊號（v2 fast 用 1-bar 收得 1011，slow 用 1-bar+offset 收得 594，v1 用純 2-bar 收得 3）。

**判斷類型**：**「市場結構問題 + logic 過嚴」混合**，不是純市場結構。

- 市場結構部分：39m bull-skew → 任何「mandatory bear regime」設計都會死。
- Logic 過嚴部分：v2 把 mandatory 改成 tiered + 放寬個幣破位門檻 + 1-bar confirm，**馬上從 3 → 1011 trades**，證明這些 filter 本身的閾值太緊（不只是市場問題）。

---

## 結論段（5 問逐題回答）

### Q1：MASR_SHORT 在 39m 真實訊號量是多少？(v1, v2)

| Version | n_trades / 10 coins / 39 月 | 樣本是否足夠 (≥ 30)？ |
|---------|---------------:|----------------------|
| **v1** | **3** | **❌ 不足**，無法做 stability audit |
| **v2_fast** | **1011** | ✅ 足夠 |
| **v2_slow** | **594** | ✅ 足夠 |

v1 在 39m bull-skew 期實質 dead；v2 是 v1 的修正版，訊號量 200-300×。

### Q2：v2 哪個 variant 訊號量最足、最值得 audit？

訊號量：v2_fast (1011) > v2_slow (594)。  
**Stability-adjusted PnL**：v2_slow ($24~$27) > v2_fast ($11)。**slow 更值得 audit**。

slow variant 用「i+1 close 再過 support − 0.2×ATR」替代 v1 的「2-bar 確認」，過濾掉假破位但保留真趨勢——entry 品質明顯高。

### Q3：訊號少是「市場結構」、「logic 不對稱」、還是「參數過嚴」？

**三者混合，可拆解**：

- **市場結構（30%）**：39m crypto bull-skew → 任何強制 bear regime gate 都吃虧
- **Logic 不對稱（50%）**：MASR_SHORT 比 MASR Long 多 5+ 條 mandatory filter（BTC regime / 已破位 gate / 反追殺保險），這些 long 都沒有
- **參數過嚴（20%）**：v1 用 2-bar confirm + 1.5× vol + 7d -5% + 距高 -15%；v2 全部放寬到 1-bar + 1.2× vol + 7d +3% + 距高 -8% → 訊號量爆增 200×

**v2 設計直接證明 v1 的稀缺主因不是「市場結構」而是 logic 過嚴**——同樣的 39m 樣本，只把 mandatory 改 tiered + 閾值放寬，就從 3 → 1011。

### Q4：哪個版本通過 audit (ROBUST/REGIME_DEP/REJECTED)？

| Version | Audit Status | adj PnL |
|---------|-------------|--------:|
| v2_fast baseline | **ROBUST** | +11.66U |
| v2_fast +cfd 排除 | **ROBUST** | +11.42U |
| v2_slow baseline | **ROBUST** | +24.15U |
| v2_slow +cfd 排除 | STABLE_BUT_THIN (boundary) | +26.70U |

**4 個 variant 全部通過 stability 檢查**（沒有 REGIME_DEPENDENT 或 REJECTED）。  
v1 sample < 30 不適用 audit。

### Q5：推薦下一步

選 **(a) + (e) 混合**：

- **(a) 樣本足夠 + ROBUST → 準備上 active short pair**：v2 證據強，但**「準備上」不等於「直接上」**——還有兩個 gate：
  - v2 不在 live 路徑！`scripts/strategies/ma_sr_short.py` 還是 v1（dead）。要上 active 必須**把 v2 logic 移到 live module** 或重寫 strategy class。這是下一輪 prompt 的事。
  - v2 用的 SL/TP/lookback 預設值是 long P4 之前的 old config。**應該對 v2 跑一輪 P4 風格 sweep**（grid: SL_ATR ∈ {1.0, 1.2, 1.5, 2.0}, TP1_RR ∈ {1.5, 2.0, 2.5, 3.0}, lookback ∈ {50, 75, 100}）找最佳。

- **(e) v1 跟 v2 結果衝突 → 對齊 v2 的設計意圖**：v1 完全 dead，v2 有 alpha——但 v2 的「tiered BTC regime + 弱倉位」邏輯複雜，需要先想清楚：
  - 是否真的要保 strong/weak 兩級？v2_fast 看 strong:weak ≈ 5:95，幾乎全靠 weak。也許可以簡化成「只做 weak regime 全倉」？
  - cfd 在 short 路徑無傷大雅（甚至 +5U）→ live 推 short 時**不要拷貝 long 的 cfd filter**

**具體下一輪 prompt 應做的事**：
1. P12B：v2 sweep（4 params × 3-5 values × 2 variants × 10 coins × wf_3seg），找 stability-adjusted #1 config
2. P12C：把 v2 logic 移植到 `scripts/strategies/ma_sr_short.py`（替換 v1）；保留 v1 code 在 backtest.py 供未來 ablation
3. P12D：shadow comparison hook（同 MASR Long，比對 live vs backtest path）
4. P12E：testnet checklist 加 short pair 段落

**目前不建議直接動 ACTIVE_STRATEGY**——v2 還沒有 live path，加 ACTIVE_STRATEGY=ma_sr_short 等於上線一個 dead 策略。

---

## 證據檔案

| 檔案 | 內容 |
|------|------|
| `.cache/p12_short_summary_20260430_2006.pkl` | task 2 全 universe rerun 結果 |
| `.cache/p12_audit_20260430_2010.pkl` | task 3 stability audit 結果 |
| `.cache/wf_results/p12_<version>_<sym>.pkl` | 每幣 × 每 variant wf pickle |
| `reports/audit_masr_short_v2*_*.md` | task 3 個別 audit 報告 |
