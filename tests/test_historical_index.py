"""历史指数成分快照的纯函数测试，无需 BaoStock 账户或网络。

运行：
    python -m pytest tests/test_historical_index.py -v
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.collect_historical_index import (
    audit_snapshots,
    select_query_dates,
    validate_snapshot,
)


def sample_snapshot(update_date: str = "2020-01-24") -> pd.DataFrame:
    """构造历史成分：包含新的深圳 302 代码前缀，防止过窄正则漏掉 A 股。"""
    return pd.DataFrame({
        "updateDate": [update_date, update_date, update_date],
        "code": ["sh.600000", "sz.000001", "sz.302132"],
        "code_name": ["股票甲", "股票乙", "股票丙"],
    })


def test_trade_calendar_uses_real_month_end() -> None:
    """交易月末是月内最后交易日，不是自然月最后一日。"""
    frame = pd.DataFrame({
        "calendar_date": [
            "2020-01-29", "2020-01-30", "2020-01-31",
            "2020-02-01", "2020-02-03", "2020-02-04",
        ],
        "is_trading_day": ["1", "0", "1", "0", "1", "1"],
    })
    result = select_query_dates(frame, "2020-01-01", "2020-02-28", "monthly")
    assert result == ["2020-01-31", "2020-02-04"]


def test_daily_queries_skip_closed_days() -> None:
    """日频也必须来自交易日历，不能用自然日或简单工作日替代。"""
    frame = pd.DataFrame({
        "calendar_date": ["2020-01-29", "2020-01-30", "2020-01-31"],
        "is_trading_day": ["1", "0", "1"],
    })
    assert select_query_dates(frame, "2020-01-29", "2020-01-31", "daily") == [
        "2020-01-29", "2020-01-31",
    ]


def test_prepare_snapshot_preserves_update_timestamp() -> None:
    """必须保留供应商数据更新日期，并检查没有未来时点。"""
    result = validate_snapshot(
        sample_snapshot(), "沪深300", "2020-01-31", 35, "网络接口"
    )
    assert len(result) == 3
    assert result["查询日期"].eq("2020-01-31").all()
    assert result["数据更新日期"].eq("2020-01-24").all()
    assert result["快照滞后天数"].eq(7).all()
    assert "sz.302132" in result["股票代码"].tolist()


def test_reject_future_snapshot() -> None:
    """即便查询历史日期，只要返回未来更新日就禁止用于回测。"""
    with pytest.raises(ValueError, match="未来"):
        validate_snapshot(
            sample_snapshot("2020-02-03"), "中证500", "2020-01-31", 35, "网络接口"
        )


def test_reject_stale_snapshot() -> None:
    """若接口返回旧日期成分，不能静默顶替新的历史快照。"""
    with pytest.raises(ValueError, match="过旧"):
        validate_snapshot(
            sample_snapshot("2019-10-01"), "沪深300", "2020-01-31", 35, "网络接口"
        )


def test_reject_duplicate_membership() -> None:
    """同一指数同一日期一只股票最多出现一条成分记录。"""
    raw = sample_snapshot()
    raw.loc[2, "code"] = "sh.600000"
    with pytest.raises(ValueError, match="重复"):
        validate_snapshot(raw, "沪深300", "2020-01-31", 35, "网络接口")


def test_reject_non_a_share_code() -> None:
    """不允许 B 股与其他证券类型直接混入 A 股指数成分。"""
    raw = sample_snapshot()
    raw.loc[0, "code"] = "sh.900901"
    with pytest.raises(ValueError, match="非沪深 A 股"):
        validate_snapshot(raw, "沪深300", "2020-01-31", 35, "网络接口")


def test_audit_snapshots_separates_query_date_and_update_date() -> None:
    """审计中仍需报告每只指数的实际覆盖数及更新日期。"""
    hs300 = validate_snapshot(
        sample_snapshot(), "沪深300", "2020-01-31", 35, "本地缓存"
    )
    zz500 = validate_snapshot(
        sample_snapshot(), "中证500", "2020-01-31", 35, "本地缓存"
    )
    audit = audit_snapshots(pd.concat([hs300, zz500], ignore_index=True))
    assert len(audit) == 2
    assert audit["成分股数"].eq(3).all()
    assert audit["最大快照滞后天数"].eq(7).all()
    assert audit["最早数据更新日"].eq("2020-01-24").all()
