"""横截面形成日排序回归测试：用合成数据保证与未来标签缺失状态无关。

运行：
    python -m pytest tests/test_cross_section_point_in_time.py -q

测试不需要下载行情或读取本地 parquet，适合在 CI 中独立运行。
"""

import numpy as np
import pandas as pd

from src.evaluate_cross_section import (
    daily_quintile_returns,
    daily_spread,
    prepare_sample,
)


def make_panel() -> pd.DataFrame:
    """构造四个日期，分别覆盖未来缺失、全日缺失和股票数不足。"""
    rows = []
    for date, size in [
        ("2025-01-02", 40),
        ("2025-01-03", 40),
        ("2025-01-06", 40),
        ("2025-01-07", 29),
    ]:
        for i in range(size):
            # 第二个交易日所有未来收益缺失；第三个交易日所有信号缺失。
            future = float(i) / 1000 if date != "2025-01-03" else np.nan
            past = float(i) / 100 if date != "2025-01-06" else np.nan
            # 关键样本：形成日排名最低的股票没有未来收益，不应因此被移出排名。
            if date == "2025-01-02" and i == 0:
                future = np.nan
            rows.append({
                "date": pd.Timestamp(date),
                "code": f"sh.{600000+i}",
                "past_5d": past,
                "future_5d": future,
            })
    return pd.DataFrame(rows)


def test_formation_ranking_does_not_drop_missing_future() -> None:
    """未来标签缺失不能改变某日的形成期排序和股票总数。"""
    sample, skipped = prepare_sample(make_panel(), 5, 5)
    first = sample[sample["date"] == pd.Timestamp("2025-01-02")]
    assert len(first) == 40
    assert first["code"].nunique() == 40
    assert first.loc[first["code"] == "sh.600000", "rank_pct"].iloc[0] == 1 / 40
    assert np.isnan(first.loc[first["code"] == "sh.600000", "future_5d"].iloc[0])
    # 有效形成日共两天；另一日完全缺信号，末日有效股票不足 30。
    assert skipped == 2


def test_spread_counts_all_unusable_dates() -> None:
    """全日没有未来标签时，跳过天数不能被漏算。"""
    spread, skipped = daily_spread(make_panel(), 5, 5)
    assert len(spread) == 1
    assert spread.index[0] == pd.Timestamp("2025-01-02")
    assert skipped == 3
    assert np.isfinite(spread.iloc[0])


def test_quintile_requires_five_observed_groups() -> None:
    """单调性分析只保留五组未来收益都可评价的日期。"""
    wide, skipped = daily_quintile_returns(make_panel(), 5, 5)
    assert len(wide) == 1
    assert list(wide.columns) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert wide.notna().all().all()
    assert skipped == 3


def test_future_availability_cannot_change_rank() -> None:
    """把未来收益置空后，同一天的形成期排名应保持不变。"""
    original = make_panel()
    modified = original.copy()
    modified.loc[
        (modified["date"] == pd.Timestamp("2025-01-02"))
        & (modified["code"] == "sh.600039"),
        "future_5d",
    ] = np.nan

    left, _ = prepare_sample(original, 5, 5)
    right, _ = prepare_sample(modified, 5, 5)

    left_ranks = left.set_index(["date", "code"])["rank_pct"].sort_index()
    right_ranks = right.set_index(["date", "code"])["rank_pct"].sort_index()
    pd.testing.assert_series_equal(left_ranks, right_ranks)
