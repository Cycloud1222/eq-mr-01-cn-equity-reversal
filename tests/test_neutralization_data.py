"""中性化数据下载模块的纯函数测试，不访问外部数据源。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.download_neutralization_data import (
    normalize_industry_snapshot,
    normalize_market_cap,
    select_weekly_query_dates,
)


def test_weekly_query_dates_use_last_available_trading_day() -> None:
    dates = pd.to_datetime([
        "2020-01-02", "2020-01-03",
        "2020-01-06", "2020-01-07", "2020-01-10",
        "2020-01-13",
    ])
    assert select_weekly_query_dates(dates) == [
        "2020-01-03", "2020-01-10", "2020-01-13"
    ]


def test_industry_snapshot_uses_actual_update_date() -> None:
    raw = pd.DataFrame({
        "updateDate": ["2020-01-06", "2020-01-06"],
        "code": ["sh.600000", "sz.000001"],
        "industry": ["银行", "银行"],
        "industryClassification": ["申万一级行业", "申万一级行业"],
    })

    result = normalize_industry_snapshot(
        raw, "2020-01-10", universe={"sh.600000"}
    )

    assert len(result) == 1
    assert result.loc[0, "code"] == "sh.600000"
    assert result.loc[0, "effective_date"] == pd.Timestamp("2020-01-06")
    assert result.loc[0, "query_date"] == pd.Timestamp("2020-01-10")


def test_industry_snapshot_rejects_future_classification() -> None:
    raw = pd.DataFrame({
        "updateDate": ["2020-01-06"],
        "code": ["sh.600000"],
        "industry": ["银行"],
        "industryClassification": ["申万一级行业"],
    })

    with pytest.raises(ValueError, match="未来"):
        normalize_industry_snapshot(raw, "2020-01-03")


def test_industry_snapshot_rejects_duplicate_codes() -> None:
    raw = pd.DataFrame({
        "updateDate": ["2020-01-06", "2020-01-06"],
        "code": ["sh.600000", "sh.600000"],
        "industry": ["银行", "银行"],
        "industryClassification": ["申万一级行业", "申万一级行业"],
    })

    with pytest.raises(ValueError, match="重复"):
        normalize_industry_snapshot(raw, "2020-01-10")


def test_market_cap_normalizes_daily_total_and_float_values() -> None:
    raw = pd.DataFrame({
        "日期": ["2020-01-02", "2020-01-03", "2019-12-31"],
        "总市值": ["1000000", "bad", "900000"],
        "流通市值": ["500000", "450000", "400000"],
    })

    result = normalize_market_cap(
        raw, "sh.600000", "2020-01-01", "2020-01-31"
    )

    assert len(result) == 2
    assert result.loc[0, "code"] == "sh.600000"
    assert result.loc[0, "total_market_cap"] == 1_000_000
    assert result.loc[0, "float_market_cap"] == 500_000
    assert pd.isna(result.loc[1, "total_market_cap"])
    assert result.loc[1, "float_market_cap"] == 450_000


def test_market_cap_rejects_missing_float_market_value() -> None:
    raw = pd.DataFrame({
        "日期": ["2020-01-02"],
        "总市值": ["1000000"],
        "流通市值": ["0"],
    })

    with pytest.raises(ValueError, match="有效流通市值"):
        normalize_market_cap(raw, "sh.600000", "2020-01-01", "2020-01-31")
