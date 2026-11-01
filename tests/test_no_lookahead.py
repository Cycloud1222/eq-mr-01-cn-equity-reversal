"""信号口径测试：验证形成期与未来收益的窗口方向、停牌过滤、复利等价。

运行：
    pytest tests/test_no_lookahead.py -v

测试对象：
    data/processed/reversal_signals.parquet（由 src/build_signals.py 生成）

设计说明：
1. 不追求覆盖所有股票，抽 3 只不同板块的股票即可暴露窗口方向写反的问题。
2. 未来收益标签必须等于“下一交易日的可用收益”，这条能直接抓住错位一天的未来函数。
3. 停牌日与长期停牌复牌首日必须为空，不能被当成 0 收益。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_FILE = PROJECT_ROOT / "data" / "processed" / "reversal_signals.parquet"

# 抽样覆盖沪市主板、创业板、科创板。
SAMPLE_CODES = ["sh.600000", "sz.300750", "sh.688111"]

# 浮点比较容差：复利计算经过对数与指数往返，允许极小误差。
RTOL = 1e-9
ATOL = 1e-12


@pytest.fixture(scope="module")
def signals() -> pd.DataFrame:
    """读取信号面板一次，模块内共用。"""
    if not SIGNAL_FILE.exists():
        pytest.skip(f"信号文件不存在，先运行 src/build_signals.py：{SIGNAL_FILE}")
    return pd.read_parquet(SIGNAL_FILE)


def stock_slice(signals: pd.DataFrame, code: str) -> pd.DataFrame:
    """取单只股票并按日期排序，函数内不改原表。"""
    return (
        signals.loc[signals["code"] == code]
        .sort_values("date")
        .reset_index(drop=True)
    )


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_past_1d_uses_same_day_return(signals: pd.DataFrame, code: str) -> None:
    """过去 1 日收益必须等于当天的可用收益，不能取到未来数据。"""
    frame = stock_slice(signals, code)
    valid = frame["past_1d"].notna()
    assert valid.sum() > 0, f"{code} 没有可用的 past_1d"

    np.testing.assert_allclose(
        frame.loc[valid, "past_1d"].to_numpy(),
        frame.loc[valid, "可用日收益"].to_numpy(),
        rtol=RTOL,
        atol=ATOL,
    )


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_future_1d_equals_next_day_return(signals: pd.DataFrame, code: str) -> None:
    """未来 1 日收益必须等于下一交易日的可用收益，错位一天即为未来函数错误。"""
    frame = stock_slice(signals, code)
    next_return = frame["可用日收益"].shift(-1)
    valid = next_return.notna()

    np.testing.assert_allclose(
        frame.loc[valid, "future_1d"].to_numpy(),
        next_return[valid].to_numpy(),
        rtol=RTOL,
        atol=ATOL,
    )


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_future_5d_is_compound_of_next_five(signals: pd.DataFrame, code: str) -> None:
    """未来 5 日收益必须等于后 5 个可用收益的复利，且任一日缺失即为空。"""
    frame = stock_slice(signals, code)
    growth = (1.0 + frame["可用日收益"]).astype("float64")

    # 手工连乘后 5 日收益：第 t 行的值来自第 t+1 到 t+5 行。
    manual = pd.Series(np.nan, index=frame.index, dtype="float64")
    for start in range(len(frame)):
        window = growth.iloc[start + 1 : start + 6]
        if len(window) == 5 and window.notna().all():
            manual.iloc[start] = float(window.prod()) - 1.0

    valid = manual.notna()
    assert valid.sum() > 0, f"{code} 没有可比较的 future_5d"
    np.testing.assert_allclose(
        frame.loc[valid, "future_5d"].to_numpy(),
        manual[valid].to_numpy(),
        rtol=1e-8,
        atol=ATOL,
    )


def test_suspension_rows_have_no_return(signals: pd.DataFrame) -> None:
    """停牌日不能有可用收益，否则会把停牌当成 0 收益交易日。"""
    suspended = signals.loc[signals["tradestatus"] == 0, "可用日收益"]
    assert suspended.isna().all(), "停牌日出现了非空收益"


def test_long_resumption_rows_have_no_return(signals: pd.DataFrame) -> None:
    """长期停牌复牌首日的收益必须剔除，避免 +306% 这类事件主导排序。"""
    resumption = signals.loc[signals["长期停牌复牌首日"], "可用日收益"]
    assert resumption.isna().all(), "长期停牌复牌首日仍有收益"
