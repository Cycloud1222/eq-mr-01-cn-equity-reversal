"""T2.1 信号重算：基于后复权日收益，计算形成期与未来收益。

用法：
    python src/build_signals.py

输入：
    data/processed/daily_panel.parquet

输出：
    data/processed/reversal_signals.parquet   信号面板
    results/tables/signal_summary.csv         信号有效性统计（中文表头）

口径（2026-11-01 已定）：
1. 收益用 ret_1d，多日收益按日收益复利累计，不用首尾价格相除，
   因为面板里存在复权因子跳变，价格相除会把跳变算进收益。
2. 停牌日（tradestatus=0）不产生收益，记为缺失，不当成 0 收益。
3. 长期停牌（≥20 个交易日）后的复牌首日收益剔除，该日及其所在窗口记为缺失。
   复牌首日多为事件驱动且不设涨跌幅，保留会主导横截面排序。
4. 信号只用当日及之前的数据；未来收益只作为研究标签，不参与信号计算。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_FILE = PROJECT_ROOT / "data" / "processed" / "daily_panel.parquet"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_FILE = OUTPUT_DIR / "reversal_signals.parquet"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"
SUMMARY_FILE = RESULT_DIR / "signal_summary.csv"

FORMATION_DAYS = [1, 5, 20]
FORWARD_DAYS = [1, 2, 5]

# 连续停牌达到这个交易日数才把复牌首日剔除，短线停牌复牌收益仍保留。
LONG_SUSPENSION_DAYS = 20


def mark_long_resumption(panel: pd.DataFrame) -> pd.DataFrame:
    """标记长期停牌后的第一个交易日。

    参数：
        panel: 已按代码、日期排序的面板，含 tradestatus。
    返回：
        新增“停牌前段长度”“长期停牌复牌首日”两列的面板。

    做法：
        把正常交易记 0、停牌记 1，做游程长度统计；
        每段停牌结束时，紧随其后的正常交易日就是复牌首日。
    """
    panel = panel.copy()
    panel["停牌前段长度"] = 0
    panel["长期停牌复牌首日"] = False

    for code, group in panel.groupby("code", sort=False):
        suspended = (group["tradestatus"] == 0).to_numpy()
        if not suspended.any():
            continue

        run_length = 0
        lengths = np.zeros(len(group), dtype=int)
        for index in range(len(group)):
            if suspended[index]:
                run_length += 1
            else:
                lengths[index] = run_length
                run_length = 0

        panel.loc[group.index, "停牌前段长度"] = lengths
        panel.loc[group.index, "长期停牌复牌首日"] = (
            (lengths >= LONG_SUSPENSION_DAYS) & (group["tradestatus"].to_numpy() == 1)
        )

    return panel


def valid_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """构造可用日收益：停牌日与长期停牌复牌首日记为缺失。

    参数：
        panel: 已标记复牌首日的面板。
    返回：
        新增“可用日收益”列的面板。
    """
    panel = panel.copy()
    usable = (panel["tradestatus"] == 1) & (~panel["长期停牌复牌首日"])
    panel["可用日收益"] = panel["ret_1d"].where(usable)
    return panel


def trailing_return(series: pd.Series, window: int) -> pd.Series:
    """计算过去 window 个交易日的复利收益。

    参数：
        series: 单只股票的对数日收益序列。
        window: 窗口长度。
    返回：
        与输入对齐的复利收益；窗口内有缺失则返回缺失。

    实现说明：
        用对数收益滚动求和再还原，等价于 (1+r) 连乘，
        比逐窗口 prod 快，也避免浮点溢出。
    """
    rolled = series.rolling(window, min_periods=window).sum()
    return np.expm1(rolled)


def forward_return(series: pd.Series, window: int) -> pd.Series:
    """计算未来 window 个交易日的复利收益（仅作研究标签）。

    参数：
        series: 单只股票的对数日收益序列。
        window: 窗口长度。
    返回：
        与输入对齐的未来收益；窗口内有缺失则返回缺失。

    实现说明：
        先算 t 日起向后 N 日的滚动对数收益和，再整体向前移 N 行。
        第 t 行拿到的就是 t+N 行上的滚动值，即 t+1 到 t+window 的收益。
        不能直接倒序滚动：倒序后窗口仍包含当天，会错位一天。
    """
    rolled = series.rolling(window, min_periods=window).sum()
    return np.expm1(rolled.shift(-window))


def add_signal_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """按股票分组，批量生成形成期与未来收益列。

    参数：
        panel: 含“可用日收益”的面板。
    返回：
        新增 past_*d 与 future_*d 列的面板。
    """
    panel = panel.copy()
    panel["对数日收益"] = np.log1p(panel["可用日收益"])

    grouped = panel.groupby("code", sort=False)["对数日收益"]

    for days in FORMATION_DAYS:
        panel[f"past_{days}d"] = grouped.transform(
            lambda series, days=days: trailing_return(series, days)
        )

    for days in FORWARD_DAYS:
        panel[f"future_{days}d"] = grouped.transform(
            lambda series, days=days: forward_return(series, days)
        )

    return panel


def summarize(panel: pd.DataFrame) -> pd.DataFrame:
    """统计各信号列的有效值数量与股票覆盖。

    参数：
        panel: 已生成信号的面板。
    返回：
        中文表头的统计表，每个信号一行。
    """
    rows = []
    # 用 (前缀, 天数) 配对遍历，不能只看天数：1 和 5 同时出现在形成期与未来收益里。
    pairs = [("past", days) for days in FORMATION_DAYS] + [
        ("future", days) for days in FORWARD_DAYS
    ]
    for prefix, days in pairs:
        column = f"{prefix}_{days}d"
        valid = panel[column].notna()
        rows.append({
            "信号列": column,
            "中文含义": (
                f"过去 {days} 日复利收益" if prefix == "past" else f"未来 {days} 日复利收益"
            ),
            "有效记录数": int(valid.sum()),
            "有效占比": f"{valid.mean() * 100:.2f}%",
            "覆盖股票数": int(panel.loc[valid, "code"].nunique()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    """执行信号计算并落盘。"""
    pd.set_option("display.unicode.east_asian_width", True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    panel = pd.read_parquet(PANEL_FILE)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    print(f"面板读取完成：{len(panel):,} 行，{panel['code'].nunique()} 只股票")

    panel = mark_long_resumption(panel)
    panel = valid_returns(panel)
    panel = add_signal_columns(panel)

    resumption_rows = int(panel["长期停牌复牌首日"].sum())
    print(f"剔除长期停牌复牌首日：{resumption_rows} 行")

    summary = summarize(panel)
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    columns = [
        "code", "date", "close", "ret_1d", "可用日收益", "tradestatus", "isST",
        "长期停牌复牌首日",
        "past_1d", "past_5d", "past_20d",
        "future_1d", "future_2d", "future_5d",
    ]
    output = panel[columns]
    output.to_parquet(OUTPUT_FILE, index=False)

    print("\n" + "=" * 80)
    print("信号有效性统计")
    print("=" * 80)
    print(summary.to_string(index=False))
    print(f"\n信号面板已保存：{OUTPUT_FILE}")
    print(f"统计表已保存：{SUMMARY_FILE}")


if __name__ == "__main__":
    main()
