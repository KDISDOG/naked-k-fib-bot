"""
set_mode.py — 快速切換機器人風控模式

使用方式:
  python scripts/set_mode.py loose    # 切換到寬鬆模式
  python scripts/set_mode.py strict   # 切換到嚴格模式（預設值）
  python scripts/set_mode.py show     # 顯示目前數值

寬鬆模式會調整 .env 中的風控參數，讓機器人更積極進場：
  - 更高槓桿、更高風險比例、更多同時倉位
  - 更低訊號門檻、更短冷卻期
"""

import argparse
import re
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# ── 模式定義 ─────────────────────────────────────────────────────
MODES = {
    "strict": {
        # 風控參數
        "MAX_LEVERAGE":            "3",
        "MARGIN_USDT":             "50",
        "MAX_POSITIONS":           "5",
        "MIN_SIGNAL_SCORE":        "3",
        "COOLDOWN_BARS":           "6",
        # 選幣參數
        "SCREEN_MIN_SCORE":        "8",
        "SCREEN_MIN_VOL_M":        "10",
        "SCREEN_ADX_MIN":          "20",
        "SCREEN_ADX_MAX":          "45",
        "SCREEN_ATR_MAX_LONG":     "4.0",
        "SCREEN_ATR_MAX_SHORT":    "8.0",
        # 入場參數
        "SIGNAL_FIB_TOL":          "0.005",
        "SIGNAL_VOL_RATIO":        "1.3",
        "SIGNAL_VOL_RISING":       "true",
        "SIGNAL_FIB_MAX_TOUCHES":  "1",
        "SIGNAL_FRACTAL_LR":       "5",
    },
    "loose": {
        # 風控參數
        "MAX_LEVERAGE":            "5",
        "MARGIN_USDT":             "100",
        "MAX_POSITIONS":           "10",
        "MIN_SIGNAL_SCORE":        "2",
        "COOLDOWN_BARS":           "3",
        # 選幣參數（更低門檻 → 選更多幣）
        "SCREEN_MIN_SCORE":        "6",     # 8 → 6，更多幣通過
        "SCREEN_MIN_VOL_M":        "5",     # 10M → 5M，中小型幣也能進
        "SCREEN_ADX_MIN":          "15",    # 20 → 15，弱趨勢也接受
        "SCREEN_ADX_MAX":          "55",    # 45 → 55，強趨勢也接受
        "SCREEN_ATR_MAX_LONG":     "6.0",   # 4% → 6%，更高波動也接受
        "SCREEN_ATR_MAX_SHORT":    "12.0",  # 8% → 12%
        # 入場參數（更寬鬆 → 更容易觸發訊號）
        "SIGNAL_FIB_TOL":          "0.015", # ±0.5% → ±1.5%，Fib 容忍更大
        "SIGNAL_VOL_RATIO":        "1.0",   # 1.3x → 1.0x，不強制放量
        "SIGNAL_VOL_RISING":       "false", # 不要求當根量 > 前一根
        "SIGNAL_FIB_MAX_TOUCHES":  "3",     # 1 → 3，允許已被觸碰的 Fib 位
        "SIGNAL_FRACTAL_LR":       "3",     # 5 → 3，swing 判定更敏感
    },
}

DESCRIPTIONS = {
    # 風控
    "MAX_LEVERAGE":           "最大槓桿倍率",
    "MARGIN_USDT":            "每筆固定保證金（USDT）",
    "MAX_POSITIONS":          "同時最多開倉數",
    "MIN_SIGNAL_SCORE":       "最低訊號強度（1-5）",
    "COOLDOWN_BARS":          "止損後冷卻 K 棒數",
    # 選幣
    "SCREEN_MIN_SCORE":       "選幣最低分（滿分 12）",
    "SCREEN_MIN_VOL_M":       "24h 最低成交量（百萬 USDT）",
    "SCREEN_ADX_MIN":         "ADX 下限（趨勢強度）",
    "SCREEN_ADX_MAX":         "ADX 上限",
    "SCREEN_ATR_MAX_LONG":    "ATR% 上限（做多）",
    "SCREEN_ATR_MAX_SHORT":   "ATR% 上限（做空）",
    # 入場
    "SIGNAL_FIB_TOL":         "Fib 貼近容忍度（±%）",
    "SIGNAL_VOL_RATIO":       "成交量確認倍率（vs 20均）",
    "SIGNAL_VOL_RISING":      "要求當根量 > 前一根",
    "SIGNAL_FIB_MAX_TOUCHES": "Fib 近 20 根最多觸碰次數",
    "SIGNAL_FRACTAL_LR":      "Fractal swing 左右確認根數",
}

# ── 工具函式 ──────────────────────────────────────────────────────
def read_env() -> list[str]:
    if not ENV_PATH.exists():
        sys.exit(f"找不到 .env 檔案：{ENV_PATH}")
    return ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)


def get_current_values(lines: list[str]) -> dict[str, str]:
    current: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([A-Z_]+)\s*=\s*(\S+)", line)
        if m:
            current[m.group(1)] = m.group(2)
    return current


def apply_mode(lines: list[str], params: dict[str, str]) -> list[str]:
    result = []
    for line in lines:
        replaced = False
        for key, val in params.items():
            # 只匹配未被注釋的行
            m = re.match(rf"^({re.escape(key)}\s*=\s*)(\S+)(.*)", line)
            if m:
                result.append(f"{m.group(1)}{val}{m.group(3)}\n")
                replaced = True
                break
        if not replaced:
            result.append(line)
    return result


def detect_current_mode(current: dict[str, str]) -> str:
    for mode_name, params in MODES.items():
        if all(current.get(k) == v for k, v in params.items()):
            return mode_name
    return "custom"


def show_status(lines: list[str]) -> None:
    current = get_current_values(lines)
    mode = detect_current_mode(current)
    print(f"\n目前模式：【{mode.upper()}】\n")
    print(f"{'參數':<26} {'目前值':>10}  {'嚴格':>8}  {'寬鬆':>8}  說明")
    print("-" * 80)
    for key, desc in DESCRIPTIONS.items():
        cur = current.get(key, "N/A")
        s   = MODES["strict"][key]
        l   = MODES["loose"][key]
        print(f"{key:<26} {cur:>10}  {s:>8}  {l:>8}  {desc}")
    print()


# ── 主邏輯 ───────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="切換機器人風控模式（loose / strict）"
    )
    parser.add_argument(
        "mode",
        choices=["loose", "strict", "show"],
        help="loose=寬鬆  strict=嚴格  show=只顯示",
    )
    args = parser.parse_args()

    lines = read_env()

    if args.mode == "show":
        show_status(lines)
        return

    target = MODES[args.mode]
    current = get_current_values(lines)
    current_mode = detect_current_mode(current)

    if current_mode == args.mode:
        print(f"\n已經是【{args.mode.upper()}】模式，無需變更。\n")
        show_status(lines)
        return

    new_lines = apply_mode(lines, target)
    ENV_PATH.write_text("".join(new_lines), encoding="utf-8")

    label = "寬鬆" if args.mode == "loose" else "嚴格"
    print(f"\n已切換到【{label}模式（{args.mode.upper()}）】✓")
    show_status(ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True))
    print("提醒：若機器人正在運行，需重新啟動才會生效。\n")


if __name__ == "__main__":
    main()
