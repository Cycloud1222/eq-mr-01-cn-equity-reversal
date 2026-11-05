"""历史指数成分与原行情覆盖率的可复现单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.audit_historical_coverage import (
    calculate_coverage,
    load_snapshots,
    successful_log_codes,
)


def fake_snapshots() -> pd.DataFrame:
    """模拟两个月份及两个指数，测试跨日并集和同日去重。"""
    return pd.DataFrame([
        {"查询日期": "2020-01-23", "指数": "沪深300", "股票代码": "sh.600000", "股票名称": "甲"},
        {"查询日期": "2020-01-23", "指数": "沪深300", "股票代码": "sh.600001", "股票名称": "乙"},
        {"查询日期": "2020-01-23", "指数": "中证500", "股票代码": "sh.600001", "股票名称": "乙"},
        {"查询日期": "2020-02-28", "指数": "沪深300", "股票代码": "sh.600001", "股票名称": "乙"},
        {"查询日期": "2020-02-28", "指数": "中证500", "股票代码": "sz.000001", "股票名称": "丙"},
    ])


def test_coverage_deduplicates_index_overlap_and_logs() -> None:
    """同一日期重叠指数成员计一次，下载名单和文件存在状态独立判断。"""
    snapshots = fake_snapshots()
    summary, detail = calculate_coverage(
        snapshots, log_codes={"sh.600000", "sh.600001"}, local_codes={"sh.600000"}
    )

    assert len(summary) == 2
    assert summary["历史指数合并去重成分数"].tolist() == [2, 2]
    assert summary["下载日志成功覆盖数"].tolist() == [2, 1]
    assert summary["本地Parquet覆盖数"].tolist() == [1, 0]
    assert len(detail) == 3

    b = detail.set_index("股票代码").loc["sh.600001"]
    assert b["历史快照出现记录数"] == 3
    assert b["出现的查询日期数"] == 2
    assert b["涉及指数"] == "中证500、沪深300"
    assert bool(b["下载日志曾成功"])
    assert not bool(b["本地Parquet存在"])
    assert bool(b["待补行情文件"])


def test_history_gap_codes_includes_missing_local_file() -> None:
    """下载日志标记成功，但 parquet 不存在时，仍应报告待补行情。"""
    summary, detail = calculate_coverage(
        fake_snapshots(),
        log_codes={"sh.600000", "sh.600001", "sz.000001"},
        local_codes={"sh.600000", "sz.000001"},
    )
    missing = set(detail.loc[detail["待补行情文件"], "股票代码"])
    assert missing == {"sh.600001"}
    assert summary["下载日志缺口数"].eq(0).all()


def test_log_success_deduplicates_retries(tmp_path) -> None:
    """相同证券先失败再成功，按证券代码只算一次覆盖。"""
    path = tmp_path / "download_log.csv"
    pd.DataFrame({
        "股票代码": ["sh.600000", "sh.600000", "sh.600001"],
        "下载状态": ["失败", "成功", "无数据"],
    }).to_csv(path, index=False, encoding="utf-8-sig")

    assert successful_log_codes(path) == {"sh.600000"}


def test_snapshot_duplicate_key_rejected(tmp_path) -> None:
    """重复快照主键会让研究成分覆盖虚增，必须报错。"""
    raw = fake_snapshots()
    raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    path = tmp_path / "snapshot.csv"
    raw.to_csv(path, index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="联合主键重复"):
        load_snapshots(path)
