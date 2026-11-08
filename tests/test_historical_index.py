"""历史指数成分快照采集逻辑测试，无需网络或 BaoStock 账户。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.collect_historical_index import normalize_snapshot, select_query_dates


def sample_snapshot(update_date: str = "2020-01-24") -> pd.DataFrame:
    return pd.DataFrame({
        "updateDate": [update_date, update_date, update_date],
        "code": ["sh.600000", "sz.000001", "sz.302132"],
        "code_name": ["股票甲", "股票乙", "股票丙"],
    })


def test_trade_calendar_uses_real_month_end() -> None:
    frame = pd.DataFrame({
        "calendar_date": [
            "2020-01-29", "2020-01-30", "2020-01-31",
            "2020-02-01", "2020-02-03", "2020-02-04",
        ],
        "is_trading_day": ["1", "0", "1", "0", "1", "1"],
    })
    assert select_query_dates(
        frame, "2020-01-01", "2020-02-28", "monthly"
    ) == ["2020-01-31", "2020-02-04"]


def test_daily_queries_skip_closed_days() -> None:
    frame = pd.DataFrame({
        "calendar_date": ["2020-01-29", "2020-01-30", "2020-01-31"],
        "is_trading_day": ["1", "0", "1"],
    })
    assert select_query_dates(
        frame, "2020-01-29", "2020-01-31", "daily"
    ) == ["2020-01-29", "2020-01-31"]


def test_normalize_snapshot_preserves_update_date_and_new_code_prefix() -> None:
    result = normalize_snapshot(
        sample_snapshot(), "沪深300", "2020-01-31", "网络接口"
    )
    assert len(result) == 3
    assert result["查询日期"].eq("2020-01-31").all()
    assert result["数据更新日期"].eq("2020-01-24").all()
    assert result["快照滞后天数"].eq(7).all()
    assert "sz.302132" in result["股票代码"].tolist()
    assert "审核状态" not in result.columns


def test_stale_snapshot_is_kept_with_update_lag_recorded() -> None:
    result = normalize_snapshot(
        sample_snapshot("2021-11-15"), "沪深300", "2021-12-31", "网络接口"
    )
    assert result["快照滞后天数"].eq(46).all()


def test_reject_future_snapshot() -> None:
    with pytest.raises(ValueError, match="未来"):
        normalize_snapshot(
            sample_snapshot("2020-02-03"), "中证500", "2020-01-31", "网络接口"
        )


def test_reject_duplicate_membership() -> None:
    raw = sample_snapshot()
    raw.loc[2, "code"] = "sh.600000"
    with pytest.raises(ValueError, match="重复"):
        normalize_snapshot(raw, "沪深300", "2020-01-31", "网络接口")


def test_reject_non_a_share_code() -> None:
    raw = sample_snapshot()
    raw.loc[0, "code"] = "sh.900901"
    with pytest.raises(ValueError, match="非沪深 A 股"):
        normalize_snapshot(raw, "沪深300", "2020-01-31", "网络接口")
