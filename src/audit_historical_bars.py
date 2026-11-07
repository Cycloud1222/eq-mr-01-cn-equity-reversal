"""T3.2：按上市/退市日期和真实交易日历审核历史成分日线完整性。

用法：
    python src/audit_historical_bars.py --snapshots "data/processed/历史指数成分快照_monthly_2020-01-01_2025-12-31_全部.csv"

可选传入 BaoStock 交易日历和证券基本资料 CSV，便于离线复核：
    --calendar-file data/processed/交易日历_2020-2025.csv
    --stock-basic-file data/processed/证券基本资料.csv

本脚本只审计，不修改、不下载行情。缺失交易日不能仅凭 Parquet 推断为停牌；
会标记为“可能停牌或数据源漏数，待确认”，避免把未知原因误判为正常。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = PROJECT_ROOT / "data" / "raw" / "baostock"
DEFAULT_LOG = PROJECT_ROOT / "data" / "manifests" / "download_log.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "tables"

REQUIRED_BAR_COLUMNS = {
    "date", "code", "open", "high", "low", "close", "preclose",
    "volume", "amount", "adjustflag", "tradestatus", "pctChg", "isST",
}
PRICE_COLUMNS = ["open", "high", "low", "close"]
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume", "amount"]
NUMERIC_COLUMNS = [
    "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="历史成分股日线完整性与质量审计")
    parser.add_argument("--snapshots", type=Path, required=True, help="历史指数成分快照 CSV")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--calendar-file", type=Path, default=None, help="可选：交易日历 CSV")
    parser.add_argument("--stock-basic-file", type=Path, default=None, help="可选：证券基本资料 CSV")
    return parser.parse_args()


def fetch_baostock_frame(result: Any, label: str) -> pd.DataFrame:
    if result.error_code != "0":
        raise RuntimeError(f"{label} 查询失败：{result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def fetch_reference_data(start_date: str, end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次登录获取交易日历和证券 IPO/退市日期，不逐股请求。"""
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{login.error_msg}")
    try:
        calendar = fetch_baostock_frame(
            bs.query_trade_dates(start_date=start_date, end_date=end_date),
            "交易日历",
        )
        basic = fetch_baostock_frame(
            bs.query_stock_basic(code="", code_name=""),
            "证券基本资料",
        )
    finally:
        bs.logout()
    return calendar, basic


def load_snapshot_universe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"缺少历史成分快照：{path}")
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype={"股票代码": "string"})
    required = {"股票代码", "股票名称"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"历史成分快照缺少字段：{sorted(missing)}")
    if frame.empty or frame[["股票代码", "股票名称"]].isna().any().any():
        raise ValueError("历史成分快照为空，或股票代码/名称存在空值")
    frame["股票代码"] = frame["股票代码"].astype("string").str.strip()
    if frame["股票代码"].eq("").any():
        raise ValueError("历史成分快照存在空股票代码")
    return (
        frame.sort_values("股票代码")
        .groupby("股票代码", as_index=False)
        .agg(股票名称=("股票名称", "first"))
    )


def normalize_calendar(calendar: pd.DataFrame) -> list[str]:
    required = {"calendar_date", "is_trading_day"}
    missing = required - set(calendar.columns)
    if missing:
        raise ValueError(f"交易日历缺少字段：{sorted(missing)}")
    frame = calendar.copy()
    dates = pd.to_datetime(frame["calendar_date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("交易日历存在空日期或无法解析的日期")
    status = frame["is_trading_day"].astype("string").str.strip()
    if not status.isin(["0", "1"]).all():
        raise ValueError("交易日历 is_trading_day 只能为 0 或 1")
    normalized = dates.dt.strftime("%Y-%m-%d")
    if normalized.duplicated().any():
        raise ValueError("交易日历存在重复日期")
    return sorted(normalized.loc[status.eq("1")].tolist())


def normalize_stock_basic(stock_basic: pd.DataFrame) -> dict[str, dict[str, Any]]:
    required = {"code", "ipoDate", "outDate"}
    missing = required - set(stock_basic.columns)
    if missing:
        raise ValueError(f"证券基本资料缺少字段：{sorted(missing)}")
    frame = stock_basic.copy()
    frame["code"] = frame["code"].astype("string").str.strip()
    if frame["code"].isna().any() or frame["code"].eq("").any():
        raise ValueError("证券基本资料存在空代码")
    if frame["code"].duplicated().any():
        raise ValueError("证券基本资料存在重复股票代码")
    if "code_name" not in frame.columns:
        frame["code_name"] = ""
    metadata: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        ipo_raw = str(row.get("ipoDate", "") or "").strip()
        out_raw = str(row.get("outDate", "") or "").strip()
        ipo = pd.to_datetime(ipo_raw, errors="coerce")
        out = pd.to_datetime(out_raw, errors="coerce") if out_raw.lower() not in {"", "none", "nan", "null"} else pd.NaT
        metadata[str(row["code"])] = {
            "ipo": ipo,
            "out": out,
            "name": str(row.get("code_name", "") or ""),
        }
    return metadata


def load_download_status(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    log = pd.read_csv(path, encoding="utf-8-sig", dtype={"股票代码": "string"})
    if not {"股票代码", "下载状态"}.issubset(log.columns):
        raise ValueError("下载日志缺少股票代码或下载状态字段")
    if "下载时间" in log.columns:
        log = log.sort_values("下载时间")
    latest = log.dropna(subset=["股票代码"]).drop_duplicates("股票代码", keep="last")
    return dict(zip(latest["股票代码"].astype(str), latest["下载状态"].astype(str)))


def _issue(
    issues: list[dict[str, Any]],
    code: str,
    name: str,
    date: str,
    issue_type: str,
    field: str = "",
    value: Any = "",
    detail: str = "",
) -> None:
    issues.append({
        "股票代码": code,
        "股票名称": name,
        "日期": date,
        "问题类型": issue_type,
        "字段": field,
        "值": "" if pd.isna(value) else str(value),
        "说明": detail,
    })


def audit_one_stock(
    code: str,
    name: str,
    frame: pd.DataFrame | None,
    trading_dates: list[str],
    metadata: dict[str, Any] | None,
    start_date: str,
    end_date: str,
    download_status: str = "",
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """审核单只股票，返回股票汇总、缺失交易日和逐项异常。"""
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")

    issues: list[dict[str, Any]] = []
    missing_rows: list[dict[str, str]] = []
    has_metadata = metadata is not None and pd.notna(metadata.get("ipo", pd.NaT))
    ipo = metadata.get("ipo", pd.NaT) if metadata else pd.NaT
    out = metadata.get("out", pd.NaT) if metadata else pd.NaT
    if has_metadata:
        effective_start = max(start, pd.Timestamp(ipo))
        effective_end = min(end, pd.Timestamp(out)) if pd.notna(out) else end
        expected_dates = [
            d for d in trading_dates
            if effective_start <= pd.Timestamp(d) <= effective_end
        ] if effective_start <= effective_end else []
    else:
        expected_dates = []
        _issue(issues, code, name, "", "缺少上市日期元数据", "ipoDate", "", "无法严谨确定该股票应覆盖的交易日区间")

    exists = frame is not None
    data = frame.copy().reset_index(drop=True) if frame is not None else pd.DataFrame()
    row_count = len(data)
    missing_columns = sorted(REQUIRED_BAR_COLUMNS - set(data.columns)) if exists else []
    for column in missing_columns:
        _issue(issues, code, name, "", "行情文件缺少字段", column, "", "无法完成该字段质量检查")

    if exists and "date" in data.columns:
        parsed_dates = pd.to_datetime(data["date"], errors="coerce")
        invalid_date_mask = parsed_dates.isna()
        for idx in data.index[invalid_date_mask]:
            _issue(issues, code, name, "", "日期无法解析", "date", data.at[idx, "date"], "该行不能参与交易日覆盖统计")
        data["_audit_date"] = parsed_dates.dt.strftime("%Y-%m-%d")
    else:
        data["_audit_date"] = pd.Series(dtype="string")

    valid_dates = data.loc[data["_audit_date"].notna(), "_audit_date"].astype(str)
    duplicate_mask = data["_audit_date"].notna() & data["_audit_date"].duplicated(keep="first")
    duplicate_count = int(duplicate_mask.sum())
    for idx in data.index[duplicate_mask]:
        _issue(issues, code, name, str(data.at[idx, "_audit_date"]), "重复股票日期", "date", data.at[idx, "_audit_date"], "同一股票同一交易日出现多行")

    if "code" in data.columns:
        mismatch = data["code"].astype(str).ne(code)
        for idx in data.index[mismatch]:
            _issue(issues, code, name, str(data.at[idx, "_audit_date"] or ""), "文件内股票代码不一致", "code", data.at[idx, "code"], "Parquet 文件名与内部 code 字段不一致")

    calendar_set = set(trading_dates)
    non_trading_mask = data["_audit_date"].notna() & ~data["_audit_date"].isin(calendar_set)
    non_trading_count = int(non_trading_mask.sum())
    for idx in data.index[non_trading_mask]:
        _issue(issues, code, name, str(data.at[idx, "_audit_date"]), "非交易日行情记录", "date", data.at[idx, "_audit_date"], "该日期不在交易日历中")

    if has_metadata and data["_audit_date"].notna().any():
        for idx in data.index[data["_audit_date"].notna()]:
            d = pd.Timestamp(data.at[idx, "_audit_date"])
            if d < start or d > end:
                _issue(issues, code, name, str(data.at[idx, "_audit_date"]), "样本区间外记录", "date", data.at[idx, "_audit_date"], "记录超出命令行指定样本区间")
            elif d < pd.Timestamp(ipo):
                _issue(issues, code, name, str(data.at[idx, "_audit_date"]), "上市前行情记录", "date", data.at[idx, "_audit_date"], "记录早于证券基本资料中的上市日期")
            elif pd.notna(out) and d > pd.Timestamp(out):
                _issue(issues, code, name, str(data.at[idx, "_audit_date"]), "退市后行情记录", "date", data.at[idx, "_audit_date"], "记录晚于证券基本资料中的退市日期")

    numeric = data.copy()
    for column in NUMERIC_COLUMNS:
        if column in numeric.columns:
            original = numeric[column]
            converted = pd.to_numeric(original, errors="coerce")
            invalid = original.notna() & converted.isna()
            for idx in numeric.index[invalid]:
                _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "数值字段无法解析", column, original.iloc[idx], "字段不是有效数值")
            numeric[column] = converted

    status = numeric["tradestatus"] if "tradestatus" in numeric.columns else pd.Series(index=numeric.index, dtype=float)
    status_known = status.isin([0, 1])
    invalid_status = ~status_known
    for idx in numeric.index[invalid_status]:
        _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "交易状态异常", "tradestatus", status.iloc[idx], "应为 1（正常交易）或 0（停牌）")

    if "isST" in numeric.columns:
        bad_st = ~numeric["isST"].isin([0, 1])
        for idx in numeric.index[bad_st]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "ST状态异常", "isST", numeric.at[idx, "isST"], "应为 0 或 1")

    ohlcv_null_cells = sum(int(numeric[c].isna().sum()) for c in OHLCV_COLUMNS if c in numeric.columns)
    normal_trade = status.eq(1)
    normal_trade_null_cells = 0
    for column in OHLCV_COLUMNS:
        if column not in numeric.columns:
            continue
        null_mask = normal_trade & numeric[column].isna()
        normal_trade_null_cells += int(null_mask.sum())
        for idx in numeric.index[null_mask]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "正常交易日OHLCV空值", column, "", "tradestatus=1 时该字段应有有效值")

    for column in PRICE_COLUMNS:
        if column not in numeric.columns:
            continue
        bad = numeric[column].notna() & numeric[column].le(0)
        for idx in numeric.index[bad]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "价格非正", column, numeric.at[idx, column], "OHLC 价格必须大于 0")

    if {"high", "low"}.issubset(numeric.columns):
        bad = numeric["high"].notna() & numeric["low"].notna() & numeric["high"].lt(numeric["low"])
        for idx in numeric.index[bad]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "最高价低于最低价", "high/low", numeric.at[idx, "high"], "high 不得小于 low")

    if {"high", "low", "open", "close"}.issubset(numeric.columns):
        bad_high = numeric["high"].notna() & numeric[["open", "close"]].notna().all(axis=1) & numeric["high"].lt(numeric[["open", "close"]].max(axis=1))
        bad_low = numeric["low"].notna() & numeric[["open", "close"]].notna().all(axis=1) & numeric["low"].gt(numeric[["open", "close"]].min(axis=1))
        for idx in numeric.index[bad_high | bad_low]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "OHLC价格关系异常", "open/high/low/close", "", "high 应不低于 open/close，low 应不高于 open/close")

    volume_amount_anomalies = 0
    for column in ["volume", "amount"]:
        if column in numeric.columns:
            bad = numeric[column].notna() & numeric[column].lt(0)
            volume_amount_anomalies += int(bad.sum())
            for idx in numeric.index[bad]:
                _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "成交量额为负", column, numeric.at[idx, column], "成交量和成交额不能为负")
    if "volume" in numeric.columns:
        zero_volume = normal_trade & numeric["volume"].notna() & numeric["volume"].le(0)
        volume_amount_anomalies += int(zero_volume.sum())
        for idx in numeric.index[zero_volume]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "正常交易日成交量非正", "volume", numeric.at[idx, "volume"], "tradestatus=1 但成交量不大于 0")
    if "amount" in numeric.columns and "volume" in numeric.columns:
        zero_amount = normal_trade & numeric["volume"].gt(0) & numeric["amount"].notna() & numeric["amount"].le(0)
        volume_amount_anomalies += int(zero_amount.sum())
        for idx in numeric.index[zero_amount]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "正常交易日成交额非正", "amount", numeric.at[idx, "amount"], "有成交量但成交额不大于 0")

    non_adjusted = 0
    if "adjustflag" in numeric.columns:
        bad_adjust = numeric["adjustflag"].notna() & numeric["adjustflag"].ne(1)
        non_adjusted = int(bad_adjust.sum())
        for idx in numeric.index[bad_adjust]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "非后复权记录", "adjustflag", numeric.at[idx, "adjustflag"], "项目行情口径要求 adjustflag=1")

    extreme_return_count = 0
    if "pctChg" in numeric.columns:
        extreme = numeric["pctChg"].notna() & numeric["pctChg"].abs().gt(50)
        extreme_return_count = int(extreme.sum())
        for idx in numeric.index[extreme]:
            _issue(issues, code, name, str(numeric.at[idx, "_audit_date"] or ""), "极端涨跌幅需复核", "pctChg", numeric.at[idx, "pctChg"], "绝对涨跌幅超过 50%，可能是新股、复牌或公司行动，不自动判错")

    present_expected = set(valid_dates) & set(expected_dates)
    missing_dates = sorted(set(expected_dates) - present_expected)
    for missing_date in missing_dates:
        if download_status == "失败":
            reason = "下载日志为失败，需重试并核对接口错误"
        elif download_status == "无数据":
            reason = "下载日志为无数据，需核对 IPO/退市日期或数据源覆盖"
        else:
            reason = "行情缺行（可能停牌或数据源漏数，待确认）；不能仅凭缺行认定停牌"
        missing_rows.append({
            "股票代码": code,
            "股票名称": name,
            "缺失交易日": missing_date,
            "上市日期": "" if pd.isna(ipo) else pd.Timestamp(ipo).strftime("%Y-%m-%d"),
            "退市日期": "" if pd.isna(out) else pd.Timestamp(out).strftime("%Y-%m-%d"),
            "下载日志状态": download_status,
            "缺失原因初判": reason,
        })

    expected_count = len(expected_dates) if has_metadata else pd.NA
    observed_count = len(present_expected) if has_metadata else pd.NA
    missing_count = len(missing_dates) if has_metadata else pd.NA
    coverage = round(len(present_expected) / len(expected_dates), 6) if has_metadata and expected_dates else (1.0 if has_metadata else pd.NA)
    if not has_metadata:
        conclusion = "无法验证：缺少上市日期元数据"
    elif not expected_dates:
        conclusion = "样本期内无有效上市交易日"
    elif missing_dates:
        conclusion = "存在交易日缺口，需逐日核实"
    elif duplicate_count or missing_columns or normal_trade_null_cells or non_adjusted or volume_amount_anomalies:
        conclusion = "日期覆盖完整，但存在行情质量异常"
    elif issues:
        conclusion = "日期覆盖完整，存在需人工复核项"
    else:
        conclusion = "日期覆盖完整，未发现结构异常"

    summary = {
        "股票代码": code,
        "股票名称": name,
        "文件存在": exists,
        "文件行数": row_count,
        "文件最早日期": valid_dates.min() if len(valid_dates) else "",
        "文件最晚日期": valid_dates.max() if len(valid_dates) else "",
        "上市日期": "" if pd.isna(ipo) else pd.Timestamp(ipo).strftime("%Y-%m-%d"),
        "退市日期": "" if pd.isna(out) else pd.Timestamp(out).strftime("%Y-%m-%d"),
        "预期交易日数": expected_count,
        "已覆盖预期交易日数": observed_count,
        "缺失交易日数": missing_count,
        "交易日覆盖率": coverage,
        "日期重复行数": duplicate_count,
        "日期解析失败行数": int(data["_audit_date"].isna().sum()) if exists else 0,
        "非交易日记录数": non_trading_count,
        "行情文件缺失字段数": len(missing_columns),
        "OHLCV空值单元格数": ohlcv_null_cells,
        "正常交易日OHLCV空值单元格数": normal_trade_null_cells,
        "非后复权记录数": non_adjusted,
        "成交量额异常数": volume_amount_anomalies,
        "极端涨跌幅复核数": extreme_return_count,
        "下载日志状态": download_status,
        "审计结论": conclusion,
    }
    return summary, pd.DataFrame(missing_rows), pd.DataFrame(issues)


def audit_universe(
    universe: pd.DataFrame,
    raw_dir: Path,
    trading_dates: list[str],
    metadata_map: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
    download_status: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries = []
    missing_frames = []
    issue_frames = []
    statuses = download_status or {}
    for row in universe.to_dict("records"):
        code = str(row["股票代码"])
        name = str(row["股票名称"])
        path = raw_dir / f"{code}.parquet"
        frame = pd.read_parquet(path) if path.is_file() else None
        summary, missing, issues = audit_one_stock(
            code=code,
            name=name,
            frame=frame,
            trading_dates=trading_dates,
            metadata=metadata_map.get(code),
            start_date=start_date,
            end_date=end_date,
            download_status=statuses.get(code, ""),
        )
        summaries.append(summary)
        if not missing.empty:
            missing_frames.append(missing)
        if not issues.empty:
            issue_frames.append(issues)

    detail = pd.DataFrame(summaries)
    missing_detail = pd.concat(missing_frames, ignore_index=True) if missing_frames else pd.DataFrame(
        columns=["股票代码", "股票名称", "缺失交易日", "上市日期", "退市日期", "下载日志状态", "缺失原因初判"]
    )
    issue_detail = pd.concat(issue_frames, ignore_index=True) if issue_frames else pd.DataFrame(
        columns=["股票代码", "股票名称", "日期", "问题类型", "字段", "值", "说明"]
    )

    expected = pd.to_numeric(detail["预期交易日数"], errors="coerce")
    observed = pd.to_numeric(detail["已覆盖预期交易日数"], errors="coerce")
    valid_expected = expected.notna()
    expected_total = int(expected.loc[valid_expected].sum())
    observed_total = int(observed.loc[valid_expected].sum())
    global_summary = pd.DataFrame([
        {"指标": "历史成分股票数", "数值": len(detail)},
        {"指标": "本地Parquet文件数", "数值": int(detail["文件存在"].sum())},
        {"指标": "文件覆盖率", "数值": round(detail["文件存在"].mean(), 6) if len(detail) else 0},
        {"指标": "缺少上市日期元数据股票数", "数值": int((~valid_expected).sum())},
        {"指标": "预期股票-交易日总数（仅元数据完整股票）", "数值": expected_total},
        {"指标": "已覆盖股票-交易日数", "数值": observed_total},
        {"指标": "缺失股票-交易日数", "数值": int(pd.to_numeric(detail["缺失交易日数"], errors="coerce").sum())},
        {"指标": "交易日记录覆盖率（仅元数据完整股票）", "数值": round(observed_total / expected_total, 6) if expected_total else pd.NA},
        {"指标": "存在交易日缺口股票数", "数值": int(pd.to_numeric(detail["缺失交易日数"], errors="coerce").fillna(0).gt(0).sum())},
        {"指标": "重复日期行数", "数值": int(detail["日期重复行数"].sum())},
        {"指标": "正常交易日OHLCV空值单元格数", "数值": int(detail["正常交易日OHLCV空值单元格数"].sum())},
        {"指标": "非后复权记录数", "数值": int(detail["非后复权记录数"].sum())},
        {"指标": "成交量额异常数", "数值": int(detail["成交量额异常数"].sum())},
        {"指标": "极端涨跌幅复核数", "数值": int(detail["极端涨跌幅复核数"].sum())},
        {"指标": "逐项异常记录数", "数值": len(issue_detail)},
    ])
    return detail, missing_detail, issue_detail, global_summary


def main() -> None:
    args = parse_args()
    if pd.Timestamp(args.start_date) > pd.Timestamp(args.end_date):
        raise ValueError("--start-date 不得晚于 --end-date")
    universe = load_snapshot_universe(args.snapshots)

    if args.calendar_file and args.stock_basic_file:
        calendar = pd.read_csv(args.calendar_file, encoding="utf-8-sig", dtype=str)
        stock_basic = pd.read_csv(args.stock_basic_file, encoding="utf-8-sig", dtype=str)
    else:
        fetched_calendar, fetched_basic = fetch_reference_data(args.start_date, args.end_date)
        calendar = (
            pd.read_csv(args.calendar_file, encoding="utf-8-sig", dtype=str)
            if args.calendar_file else fetched_calendar
        )
        stock_basic = (
            pd.read_csv(args.stock_basic_file, encoding="utf-8-sig", dtype=str)
            if args.stock_basic_file else fetched_basic
        )

    trading_dates = normalize_calendar(calendar)
    metadata_map = normalize_stock_basic(stock_basic)
    statuses = load_download_status(args.log)
    detail, missing, issues, global_summary = audit_universe(
        universe=universe,
        raw_dir=args.raw_dir,
        trading_dates=trading_dates,
        metadata_map=metadata_map,
        start_date=args.start_date,
        end_date=args.end_date,
        download_status=statuses,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.snapshots.stem}_{args.start_date}_{args.end_date}"
    outputs = {
        "detail": args.output_dir / f"历史日线覆盖审计_{suffix}.csv",
        "missing": args.output_dir / f"历史日线缺失交易日_{suffix}.csv",
        "issues": args.output_dir / f"历史日线异常明细_{suffix}.csv",
        "summary": args.output_dir / f"历史日线审计汇总_{suffix}.csv",
    }
    detail.to_csv(outputs["detail"], index=False, encoding="utf-8-sig")
    missing.to_csv(outputs["missing"], index=False, encoding="utf-8-sig")
    issues.to_csv(outputs["issues"], index=False, encoding="utf-8-sig")
    global_summary.to_csv(outputs["summary"], index=False, encoding="utf-8-sig")

    print("历史日线审计汇总：")
    print(global_summary.to_string(index=False))
    print("\n股票级审计结论：")
    print(detail["审计结论"].value_counts(dropna=False).to_string())
    print("\n输出文件：")
    for path in outputs.values():
        print(path)
    print(
        "\n注意：缺失交易日只表示本地行情没有该日期的记录；"
        "若无独立停复牌记录佐证，不能自动归类为停牌。"
    )


if __name__ == "__main__":
    main()
