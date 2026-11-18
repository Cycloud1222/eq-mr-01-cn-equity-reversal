"""下载入口来源校验：不访问网络，防止已删除的本地股票池入口被静默恢复。"""

from __future__ import annotations

import pytest

from src.download_data import load_codes


def test_universe_source_is_rejected_without_network() -> None:
    """旧 `universe` 来源的支撑文件已删除，必须显式失败而不是回退到 index800。"""
    with pytest.raises(ValueError, match="不支持的股票来源：universe"):
        load_codes("universe")
