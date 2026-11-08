"""按历史日期采集沪深300与中证500的成分股快照。

示例：
    python src/collect_historical_index.py --frequency monthly --limit 3
    python src/collect_historical_index.py --frequency monthly --limit 0

月末/周末快照只代表所查询日期，不等同于期间内每日动态成分。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import baostock as bs
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "historical_index"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

INDEX_APIS = {
    "沪深300": "query_hs300_stocks",
    "中证500": "query_zz500_stocks",
}
A_SHARE_PATTERN = r"^(?:sh\.6\d{5}|sz\.[03]\d{5})$"
SNAPSHOT_COLUMNS = [
    "查询日期", "指数", "股票代码", "股票名称", "数据更新日期",
    "快照滞后天数", "数据来源", "获取方式",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集历史指数成分快照")
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument(
        "--frequency", choices=("monthly", "weekly", "daily"), default="monthly",
        help="按交易月末、交易周末或每个交易日采集",
    )
    parser.add_argument(
        "--limit", type=int, default=3,
        help="最多采集前 N 个查询日期；0 表示全部",
    )
    return parser.parse_args()


def fetch_result(result, table_name: str) -> pd.DataFrame:
    if result.error_code != "0":
        raise RuntimeError(f"{table_name} 查询失败：{result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def select_query_dates(
    raw_calendar: pd.DataFrame,
    start_date: str,
    end_date: str,
    frequency: str,
) -> list[str]:
    required = {"calendar_date", "is_trading_day"}
    if not required.issubset(raw_calendar.columns):
        raise ValueError(f"交易日历缺少字段：{sorted(required - set(raw_calendar.columns))}")

    calendar = raw_calendar.copy()
    status = calendar["is_trading_day"].astype(str).str.strip()
    if not status.isin(["0", "1"]).all():
        raise ValueError("交易日历交易状态出现非 0/1 值")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"], errors="raise")

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")

    calendar = calendar.loc[
        status.eq("1") & calendar["calendar_date"].between(start, end)
    ].copy()
    dates = calendar["calendar_date"].drop_duplicates().sort_values()
    if dates.empty:
        raise ValueError("所选日期范围内没有交易日")

    if frequency == "daily":
        chosen = dates
    elif frequency in ("weekly", "monthly"):
        periods = dates.dt.to_period("W-SUN" if frequency == "weekly" else "M")
        chosen = dates.groupby(periods).max().sort_values()
    else:
        raise ValueError(f"不支持的频率：{frequency}")
    return chosen.dt.strftime("%Y-%m-%d").tolist()


def normalize_snapshot(
    raw_frame: pd.DataFrame,
    index_name: str,
    query_date: str,
    source_kind: str,
) -> pd.DataFrame:
    """统一字段并阻止明显错误的证券代码、重复成员和未来数据。"""
    if index_name not in INDEX_APIS:
        raise ValueError(f"未知指数：{index_name}")
    required = {"updateDate", "code", "code_name"}
    if not required.issubset(raw_frame.columns):
        raise ValueError(
            f"{index_name} 历史快照缺少字段：{sorted(required - set(raw_frame.columns))}"
        )
    if raw_frame.empty:
        raise ValueError(f"{query_date} 的 {index_name} 返回空成分")

    raw = raw_frame.copy()
    raw["code"] = raw["code"].astype(str).str.strip()
    raw["code_name"] = raw["code_name"].astype(str).str.strip()
    valid_code = raw["code"].str.fullmatch(A_SHARE_PATTERN)
    if not valid_code.all():
        invalid = raw.loc[~valid_code, "code"].head(5).tolist()
        raise ValueError(f"发现非沪深 A 股证券代码：{invalid}")
    if raw["code"].duplicated().any():
        raise ValueError(f"{index_name} 在 {query_date} 出现重复股票代码")

    update_dates = pd.to_datetime(raw["updateDate"], errors="coerce")
    if update_dates.isna().any():
        raise ValueError(f"{index_name} 数据更新日期缺失或无法解析")
    query_stamp = pd.Timestamp(query_date)
    ages = (query_stamp - update_dates).dt.days
    if (ages < 0).any():
        raise ValueError(f"{index_name} 在 {query_date} 返回了未来更新的数据")

    return pd.DataFrame({
        "查询日期": query_stamp.strftime("%Y-%m-%d"),
        "指数": index_name,
        "股票代码": raw["code"].to_numpy(),
        "股票名称": raw["code_name"].to_numpy(),
        "数据更新日期": update_dates.dt.strftime("%Y-%m-%d").to_numpy(),
        "快照滞后天数": ages.to_numpy(),
        "数据来源": "BaoStock",
        "获取方式": source_kind,
    }, columns=SNAPSHOT_COLUMNS)


def get_snapshot(index_name: str, query_date: str) -> pd.DataFrame:
    path = CACHE_DIR / f"{query_date}_{'hs300' if index_name == '沪深300' else 'zz500'}.csv"
    if path.exists():
        raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        source_kind = "本地缓存"
    else:
        api_name = INDEX_APIS[index_name]
        raw = fetch_result(getattr(bs, api_name)(date=query_date), api_name)
        # 先统一字段，成功后再缓存原始接口结果。
        normalize_snapshot(raw, index_name, query_date, "网络接口")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(path, index=False, encoding="utf-8-sig")
        source_kind = "网络接口"
    return normalize_snapshot(raw, index_name, query_date, source_kind)


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise ValueError("--limit 不能为负数")

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{login.error_msg}")

    try:
        calendar = fetch_result(
            bs.query_trade_dates(start_date=args.start_date, end_date=args.end_date),
            "交易日历",
        )
        query_dates = select_query_dates(
            calendar, args.start_date, args.end_date, args.frequency
        )
        if args.limit:
            query_dates = query_dates[:args.limit]

        snapshots = []
        for date in query_dates:
            for index_name in INDEX_APIS:
                frame = get_snapshot(index_name, date)
                snapshots.append(frame)
                print(f"已采集：{date} {index_name}，{len(frame)} 只")
    finally:
        bs.logout()

    full = pd.concat(snapshots, ignore_index=True)
    if full.duplicated(["查询日期", "指数", "股票代码"]).any():
        raise ValueError("历史快照存在重复成分记录")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{args.frequency}_{args.start_date}_{args.end_date}_"
        f"{'全部' if args.limit == 0 else '前' + str(args.limit) + '期'}"
    )
    output_path = OUTPUT_DIR / f"历史指数成分快照_{suffix}.csv"
    full.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n历史快照保存：{output_path}")
    print(f"采集日期数：{len(query_dates)}；总记录数：{len(full):,}")
    print("注意：月度/周度快照不等同于每日动态股票池。")


if __name__ == "__main__":
    main()
