"""历史成分日线覆盖与质量审计的无网络单元测试。"""

from __future__ import annotations

import pandas as pd

from src.audit_historical_bars import (
    audit_one_stock,
    normalize_calendar,
    normalize_stock_basic,
)


def make_bars(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "date": "2020-01-02",
        "code": "sh.600000",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "preclose": 10.0,
        "volume": 1000.0,
        "amount": 10000.0,
        "adjustflag": 1,
        "tradestatus": 1,
        "pctChg": 2.0,
        "isST": 0,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_calendar_uses_only_unique_trading_days() -> None:
    calendar = pd.DataFrame({
        "calendar_date": ["2020-01-01", "2020-01-02", "2020-01-03"],
        "is_trading_day": ["0", "1", "1"],
    })
    assert normalize_calendar(calendar) == ["2020-01-02", "2020-01-03"]


def test_suspension_row_counts_as_covered_but_missing_day_is_not_assumed_suspension() -> None:
    frame = make_bars([
        {"date": "2020-01-02"},
        {
            "date": "2020-01-03",
            "open": None, "high": None, "low": None, "close": None,
            "preclose": None, "volume": 0, "amount": 0,
            "tradestatus": 0, "pctChg": None,
        },
    ])
    metadata = {"ipo": pd.Timestamp("2019-01-01"), "out": pd.NaT}
    summary, missing, issues = audit_one_stock(
        "sh.600000", "浦发银行", frame,
        ["2020-01-02", "2020-01-03", "2020-01-06"],
        metadata, "2020-01-01", "2020-01-31",
    )

    assert summary["预期交易日数"] == 3
    assert summary["已覆盖预期交易日数"] == 2
    assert summary["缺失交易日数"] == 1
    assert summary["交易日覆盖率"] == 0.666667
    assert missing["缺失交易日"].tolist() == ["2020-01-06"]
    assert "可能停牌或数据源漏数" in missing.iloc[0]["缺失原因初判"]
    assert summary["正常交易日OHLCV空值单元格数"] == 0
    assert issues.empty


def test_expected_dates_are_bounded_by_ipo_and_delisting_dates() -> None:
    frame = make_bars([
        {"date": "2020-01-02"},
        {"date": "2020-01-06"},
    ])
    metadata = {
        "ipo": pd.Timestamp("2020-01-06"),
        "out": pd.Timestamp("2020-01-06"),
    }
    summary, missing, issues = audit_one_stock(
        "sh.600000", "测试股票", frame,
        ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"],
        metadata, "2020-01-01", "2020-01-31",
    )

    assert summary["预期交易日数"] == 1
    assert summary["已覆盖预期交易日数"] == 1
    assert summary["缺失交易日数"] == 0
    assert missing.empty
    assert "上市前行情记录" in set(issues["问题类型"])


def test_duplicate_dates_bad_ohlcv_and_adjustment_are_reported() -> None:
    frame = make_bars([
        {"date": "2020-01-02", "adjustflag": 3, "high": 9.0, "low": 10.0, "volume": -1},
        {"date": "2020-01-02", "volume": None},
    ])
    metadata = {"ipo": pd.Timestamp("2019-01-01"), "out": pd.NaT}
    summary, _, issues = audit_one_stock(
        "sh.600000", "浦发银行", frame,
        ["2020-01-02"], metadata, "2020-01-01", "2020-01-31",
    )

    issue_types = set(issues["问题类型"])
    assert summary["日期重复行数"] == 1
    assert summary["非后复权记录数"] == 1
    assert summary["正常交易日OHLCV空值单元格数"] == 1
    assert "重复股票日期" in issue_types
    assert "最高价低于最低价" in issue_types
    assert "成交量额为负" in issue_types
    assert "非后复权记录" in issue_types
    assert "正常交易日OHLCV空值" in issue_types


def test_missing_listing_metadata_is_not_reported_as_full_coverage() -> None:
    summary, missing, issues = audit_one_stock(
        "sh.600000", "浦发银行", None,
        ["2020-01-02"], None, "2020-01-01", "2020-01-31",
    )

    assert pd.isna(summary["预期交易日数"])
    assert pd.isna(summary["交易日覆盖率"])
    assert summary["审计结论"] == "无法验证：缺少上市日期元数据"
    assert missing.empty
    assert "缺少上市日期元数据" in set(issues["问题类型"])


def test_stock_basic_normalizes_open_ended_delisting_date() -> None:
    basic = pd.DataFrame({
        "code": ["sh.600000"],
        "ipoDate": ["1999-11-10"],
        "outDate": [""],
        "code_name": ["浦发银行"],
    })
    result = normalize_stock_basic(basic)
    assert result["sh.600000"]["ipo"] == pd.Timestamp("1999-11-10")
    assert pd.isna(result["sh.600000"]["out"])
