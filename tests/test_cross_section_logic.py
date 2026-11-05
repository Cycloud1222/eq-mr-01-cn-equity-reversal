"""横截面评价回归测试：不依赖 799 只股票的本地行情文件。

重点测试研究规则，而不是只验证脚本能正常执行：
1. 形成日的排名不能随未来收益是否缺失而改变；
2. 全部标签缺失或信号不足的日期必须计入跳过数；
3. 相同过去收益不应按股票代码的先后被任意拆开；
4. 五分组应使用相同的可评价日期进行比较。

运行：
    python -m pytest tests/test_cross_section_logic.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.evaluate_cross_section import (
    daily_quintile_returns,
    daily_spread,
    daily_spread_details,
    prepare_sample,
)


def make_panel(dates: list[str], stock_count: int = 40) -> pd.DataFrame:
    """生成可手工验证的两列收益面板：每个日期均有相同数量的股票。"""
    rows = []
    for date in dates:
        for index in range(stock_count):
            rows.append({
                "date": pd.Timestamp(date),
                "code": f"TEST{index:03d}",
                "past_5d": float(index - 20) / 1000,
                "future_1d": float(index - 20) / 2000,
            })
    return pd.DataFrame(rows)


def test_future_missing_does_not_change_formation_rank() -> None:
    """不能在形成组合前删去未来收益缺失的输家，让其他股票补位。"""
    panel = make_panel(["2025-01-02"])
    panel.loc[panel["code"] == "TEST000", "future_1d"] = np.nan

    sample, skipped = prepare_sample(panel, formation=5, horizon=1)
    assert skipped == 0
    assert len(sample) == 40

    # 第一只股票的过去收益最差；未来标签缺失不应让它退出输家组。
    first = sample.loc[sample["code"] == "TEST000"].iloc[0]
    assert first["rank_pct"] == 1 / 40

    details = daily_spread_details(panel, 5, 1)
    assert details["输家组股票数"].iloc[0] == 12
    assert details["输家组有效标签数"].iloc[0] == 11
    assert details["赢家组股票数"].iloc[0] == 12
    assert details["输家组标签覆盖率"].iloc[0] == 11 / 12


def test_day_with_no_future_labels_is_counted_as_skipped() -> None:
    """完整信号日期没有任何未来标签时，不得从样本分母中消失。"""
    panel = make_panel(["2025-01-02", "2025-01-03"])
    panel.loc[panel["date"] == pd.Timestamp("2025-01-03"), "future_1d"] = np.nan

    spread, skipped = daily_spread(panel, 5, 1)
    assert len(spread) == 1
    assert skipped == 1


def test_day_with_no_valid_signal_is_counted_as_skipped() -> None:
    """整天没有过去收益时不能靠丢掉日期掩盖覆盖缺口。"""
    panel = make_panel(["2025-01-02", "2025-01-03"])
    panel.loc[panel["date"] == pd.Timestamp("2025-01-03"), "past_5d"] = np.nan

    details = daily_spread_details(panel, 5, 1)
    assert len(details) == 2
    assert details["有效信号股票数"].loc[pd.Timestamp("2025-01-03")] == 0

    spread, skipped = daily_spread(panel, 5, 1)
    assert len(spread) == 1
    assert skipped == 1


def test_same_signal_has_same_rank() -> None:
    """同信号股票不能仅凭股票代码顺序被拆到不同分位。"""
    panel = make_panel(["2025-01-02"])
    panel.loc[panel["code"].isin([f"TEST{i:03d}" for i in range(5)]), "past_5d"] = -0.5

    sample, _ = prepare_sample(panel, 5, 1)
    tied = sample.loc[sample["code"].isin([f"TEST{i:03d}" for i in range(5)])]
    assert tied["rank_pct"].nunique() == 1


def test_quintile_requires_comparable_dates() -> None:
    """Q1 缺失时不能拿 Q2～Q5 的当天结果与其他日期比较。"""
    panel = make_panel(["2025-01-02", "2025-01-03"])
    # 最低 8 只恰好位于 Q1，整组未来标签缺失。
    missing = (
        (panel["date"] == pd.Timestamp("2025-01-03"))
        & panel["code"].isin([f"TEST{i:03d}" for i in range(8)])
    )
    panel.loc[missing, "future_1d"] = np.nan

    wide, skipped = daily_quintile_returns(panel, 5, 1)
    assert len(wide) == 1
    assert skipped == 1
    assert wide.notna().all().all()


def test_no_cross_date_ranking() -> None:
    """即使不同日期的信号整体水平变化，也必须各自拥有完整分位。"""
    panel = make_panel(["2025-01-02", "2025-01-03"])
    panel.loc[panel["date"] == pd.Timestamp("2025-01-03"), "past_5d"] += 2.0

    sample, skipped = prepare_sample(panel, 5, 1)
    assert skipped == 0
    assert sample.groupby("date")["rank_pct"].min().eq(1 / 40).all()
    assert sample.groupby("date")["rank_pct"].max().eq(1.0).all()
