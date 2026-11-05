"""T2.3 HAC 稳健性检验测试：不依赖实际市场行情。

运行：
    python -m pytest tests/test_hac.py -v

覆盖：
    1. 与可以手算的 Bartlett 长期方差核对；
    2. 零阶 HAC 与独立同分布下的均值标准误核对；
    3. 内部缺失日不能被悄悄压缩；
    4. 九组参数 × 三种滞后阶数，结果结构应完整；
    5. 恒定序列不应该虚构极大的 t 值。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluate_hac import (
    analyze_hac,
    check_internal_gaps,
    estimate_mean_hac,
    load_daily_detail,
)


def test_hac_matches_hand_calculated_bartlett_variance() -> None:
    """五个收益差的长期方差可手算，检验 HAC 算法核心数值。

    样本 [1, 2, 4, 3, 5]，均值为 3，残差为 [-2,-1,1,0,2]。
    gamma_0 = 10 / 5 = 2
    gamma_1 = (2 - 1 + 0 + 0) / 5 = 0.2
    当 maxlags=1 时 Bartlett 权重为 0.5；
    长期方差 = 2 + 2 * 0.5 * 0.2 = 2.2。
    样本均值的 HAC 标准误为 sqrt(2.2 / 5)。
    """
    values = pd.Series([1.0, 2.0, 4.0, 3.0, 5.0])
    result = estimate_mean_hac(values, maxlags=1)

    assert result["日均收益差"] == pytest.approx(3.0)
    assert result["HAC标准误"] == pytest.approx(np.sqrt(2.2 / 5))
    assert result["HAC t值"] == pytest.approx(3 / np.sqrt(2.2 / 5))
    assert 0 < result["HAC双侧p值"] < 1


def test_zero_lag_uses_zero_order_covariance() -> None:
    """maxlags=0 时用 n 分母的零阶方差，与 ddof=1 的普通 t 略有区别。"""
    values = pd.Series([1.0, 2.0, 4.0, 3.0, 5.0])
    result = estimate_mean_hac(values, maxlags=0)
    assert result["HAC标准误"] == pytest.approx(np.sqrt(2 / 5))
    assert result["普通t值"] == pytest.approx(3 / np.sqrt(2.5 / 5))


def test_constant_series_has_undefined_t() -> None:
    """全为相同数值时标准差为 0，不得给出伪显著统计量。"""
    result = estimate_mean_hac(pd.Series([0.2] * 30), maxlags=4)
    assert result["日均收益差"] == pytest.approx(0.2)
    assert np.isnan(result["HAC t值"])
    assert np.isnan(result["HAC双侧p值"])


def test_hac_rejects_internal_missing_day() -> None:
    """允许样本两端没有标签，但内部缺失会使压缩序列改变真实时间间隔。"""
    group = pd.DataFrame({
        "交易日期": pd.bdate_range("2025-01-02", periods=7),
        "反转收益差": [np.nan, 0.01, 0.02, np.nan, -0.01, 0.03, np.nan],
    })
    with pytest.raises(ValueError, match="内部"):
        check_internal_gaps(group, formation=5, horizon=5)


def make_detail(days: int = 30) -> pd.DataFrame:
    """构造九组参数的独立日期序列，含首尾缺失值。"""
    dates = pd.bdate_range("2025-01-02", periods=days)
    rows = []
    for formation in (1, 5, 20):
        for horizon in (1, 2, 5):
            for index, date in enumerate(dates):
                # 形成日与最后评价日没有有效标签；中间观测值带有变化。
                spread = (
                    np.nan
                    if index in (0, days - 1)
                    else 0.001 + 0.002 * np.sin(index + formation + horizon)
                )
                rows.append({
                    "交易日期": date,
                    "形成期": formation,
                    "持有期": horizon,
                    "反转收益差": spread,
                })
    return pd.DataFrame(rows)


def test_analyze_all_nine_pairs_and_three_lags() -> None:
    """九组参数各有主设定与两种敏感性阶数，共 27 行。"""
    summary = analyze_hac(make_detail())
    assert len(summary) == 27
    assert (summary["有效交易日数"] == 28).all()
    assert (summary["跳过交易日数"] == 2).all()
    assert summary["HAC t值"].notna().all()
    assert set(summary["HAC设定"]) == {
        "主设定：持有期减一", "敏感性：滞后五阶", "敏感性：滞后十阶"
    }

    for horizon in (1, 2, 5):
        primary = summary.loc[
            (summary["持有期"] == horizon)
            & (summary["HAC设定"] == "主设定：持有期减一")
        ]
        assert set(primary["滞后阶数"]) == {horizon - 1}


def test_input_rejects_duplicate_date_pair(tmp_path) -> None:
    """输入重复的形成日与参数组合会让该日被重复计入 t 值。"""
    detail = make_detail()
    detail = pd.concat([detail, detail.head(1)], ignore_index=True)
    file_path = tmp_path / "重复日期.csv"
    detail.to_csv(file_path, index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="重复"):
        load_daily_detail(file_path)


def test_hac_rejects_nonfinite_values() -> None:
    """函数不能把非有限收益差悄悄当成有效样本。"""
    with pytest.raises(ValueError, match="有限"):
        estimate_mean_hac(pd.Series([0.01, np.inf, 0.03]), maxlags=1)
