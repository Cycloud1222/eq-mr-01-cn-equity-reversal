"""历史行情缺口清单回补入口的单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.download_data import load_missing_codes


def test_load_missing_codes_filters_and_deduplicates(tmp_path) -> None:
    """传入代码并集时，只保留待补项，并按股票代码去重。"""
    path = tmp_path / "missing.csv"
    pd.DataFrame(
        {
            "股票代码": ["sh.600006", "sh.600007", "sz.000001", "sz.000001"],
            "股票名称": ["东风汽车", "中国国贸", "平安银行", "平安银行"],
            "待补行情文件": [True, False, True, True],
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")

    result = load_missing_codes(path)

    assert result["股票代码"].tolist() == ["sh.600006", "sz.000001"]
    assert result["股票名称"].tolist() == ["东风汽车", "平安银行"]
    assert result["数据源"].eq("historical_missing").all()


def test_load_missing_codes_rejects_invalid_stock_code(tmp_path) -> None:
    """无效代码不能被静默跳过，否则会造成缺口清单不完整。"""
    path = tmp_path / "missing.csv"
    pd.DataFrame(
        {
            "股票代码": ["sh.6000"],
            "股票名称": ["错误代码"],
            "待补行情文件": [True],
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="无效股票代码"):
        load_missing_codes(path)


def test_load_missing_codes_rejects_empty_gap_list(tmp_path) -> None:
    """误传没有待补证券的并集文件时，应明确报错。"""
    path = tmp_path / "union.csv"
    pd.DataFrame(
        {
            "股票代码": ["sh.600000"],
            "股票名称": ["浦发银行"],
            "待补行情文件": [False],
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="没有待补行情"):
        load_missing_codes(path)
