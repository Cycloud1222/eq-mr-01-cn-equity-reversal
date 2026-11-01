"""面板结构测试：确保合并后的日线面板字段、类型、唯一性符合下游约定。

运行：
    pytest tests/test_schema.py -v

测试对象：
    data/processed/daily_panel.parquet（由 src/build_panel.py 生成）

设计说明：
1. 只测“结构契约”，不测策略结论，避免数据更新时测试天天失败。
2. 面板不存在时跳过而不是报错，方便在没跑数据的新环境里只跑单元测试。
"""

from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_FILE = PROJECT_ROOT / "data" / "processed" / "daily_panel.parquet"

REQUIRED_COLUMNS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "adjustflag",
    "turn",
    "tradestatus",
    "pctChg",
    "isST",
    "prev_close",
    "ret_1d",
    "ret_by_pre",
]


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    """读取面板一次，整个模块共用，避免重复读大文件。"""
    if not PANEL_FILE.exists():
        pytest.skip(f"面板不存在，先运行 src/build_panel.py：{PANEL_FILE}")
    return pd.read_parquet(PANEL_FILE)


def test_required_columns(panel: pd.DataFrame) -> None:
    """必要字段必须齐全，否则下游信号脚本会直接报错。"""
    missing = [col for col in REQUIRED_COLUMNS if col not in panel.columns]
    assert not missing, f"缺少字段：{missing}"


def test_code_and_date_not_null(panel: pd.DataFrame) -> None:
    """代码和日期是主键，不允许缺失。"""
    assert not panel["code"].isna().any(), "股票代码存在缺失"
    assert not panel["date"].isna().any(), "交易日期存在缺失"


def test_no_duplicate_code_date(panel: pd.DataFrame) -> None:
    """同一只股票同一天只能有一条记录，重复会让横截面统计翻倍计数。"""
    duplicated = int(panel.duplicated(["code", "date"]).sum())
    assert duplicated == 0, f"存在 {duplicated} 条重复的股票-日期记录"


def test_price_positive_and_high_low(panel: pd.DataFrame) -> None:
    """收盘价必须为正，最高价不能低于最低价，否则收益计算会出现荒谬值。"""
    assert (panel["close"] > 0).all(), "存在非正收盘价"
    assert (panel["high"] >= panel["low"]).all(), "存在最高价低于最低价的记录"


def test_adjusted_only(panel: pd.DataFrame) -> None:
    """面板只允许后复权数据，混入其它口径会污染所有收益。"""
    assert (panel["adjustflag"] == 1).all(), "面板中存在非后复权记录"


def test_first_row_return_is_null(panel: pd.DataFrame) -> None:
    """每只股票第一行没有上一交易日，收益必须为空而不是 0。"""
    first_rows = panel.groupby("code", sort=False).head(1)
    assert first_rows["ret_1d"].isna().all(), "存在首行收益被填成 0 的情况"
