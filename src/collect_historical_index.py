"""T3.1：按历史查询日期采集沪深 300 与中证 500 的成分股快照。

运行（先以 3 个交易月末做试验，不直接重建研究样本）：
    python src/collect_historical_index.py --frequency monthly --limit 3

待快照审计通过后再扩大：
    python src/collect_historical_index.py --frequency monthly --limit 0

重要研究约束：
    - 不调用无日期参数的「当前指数成分」接口回填历史。
    - 查询日期不等于数据更新日期，必须分别保存并审核是否超过查询时点。
    - 月末快照只能说明月末的历史成分，不能直接代表整个月的每日成员。
    - 原始行情仍只覆盖原有的 799 只股票；本模块不重算策略或改写已有结果。
"""

from __future__ import annotations

import argparse
from datetime import datetime
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

# 排除深市 B 股（2 开头）、沪市 B 股（900 开头）、北交所等。
# 不枚举过窄的板块前缀，以免把新的 A 股代码段漏掉。
A_SHARE_PATTERN = r"^(?:sh\.6\d{5}|sz\.[03]\d{5})$"

SNAPSHOT_COLUMNS = [
    "查询日期", "指数", "股票代码", "股票名称", "数据更新日期",
    "快照滞后天数", "数据来源", "获取方式",
]


def parse_args() -> argparse.Namespace:
    """历史查询日期、采样频率与试运行数量均由命令行控制。"""
    parser = argparse.ArgumentParser(description="采集并审计历史指数成分快照")
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument(
        "--frequency", choices=("monthly", "weekly", "daily"), default="monthly",
        help="按交易月末、交易周末或每个交易日选择快照日期",
    )
    parser.add_argument(
        "--limit", type=int, default=3,
        help="只取前 N 个查询日期；0 表示全量。默认 3 用于小规模验证",
    )
    parser.add_argument(
        "--max-staleness-days", type=int, default=35,
        help="接口数据更新日距离查询日超过此日数就报错，不静默采用旧快照",
    )
    return parser.parse_args()


def fetch_result(result, table_name: str) -> pd.DataFrame:
    """统一取出 BaoStock 的游标结果，保留服务端原始字段名。"""
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
    """使用真正的沪深交易日历选取月末/周末日期，避免把节假日当交易日。"""
    required = {"calendar_date", "is_trading_day"}
    if not required.issubset(raw_calendar.columns):
        raise ValueError(f"交易日历缺少字段：{sorted(required - set(raw_calendar.columns))}")

    calendar = raw_calendar.copy()
    status = calendar["is_trading_day"].astype(str).str.strip()
    if not status.isin(["0", "1"]).all():
        raise ValueError("交易日历交易状态出现非 0/1 值")

    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"], errors="raise")
    if calendar["calendar_date"].isna().any():
        raise ValueError("交易日历存在空日期")

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")

    calendar = calendar.loc[
        status.eq("1")
        & calendar["calendar_date"].between(start, end)
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


def validate_snapshot(
    raw_frame: pd.DataFrame,
    index_name: str,
    query_date: str,
    max_staleness_days: int,
    source_kind: str,
) -> pd.DataFrame:
    """把一次指数接口结果标准化，并拒绝时点错误或过旧的数据。

    参数：
        raw_frame：BaoStock 返回的原始成分表。
        index_name：沪深300或中证500。
        query_date：研究者要求的历史快照日。
        max_staleness_days：查询日与更新日的最大允许差。
        source_kind：网络接口或本地缓存。
    返回：
        标准化中文字段的历史成分快照。

    注意：
        检查 updateDate <= 查询日期，是防止明确的未来数据。
        即便检查通过，仍需确认供应商历史成分与官方生效日期一致。
    """
    if index_name not in INDEX_APIS:
        raise ValueError(f"未知指数：{index_name}")
    if max_staleness_days < 0:
        raise ValueError("允许滞后天数不得为负")

    required = {"updateDate", "code", "code_name"}
    if not required.issubset(raw_frame.columns):
        raise ValueError(
            f"{index_name} 历史快照缺少字段：{sorted(required - set(raw_frame.columns))}"
        )
    if raw_frame.empty:
        raise ValueError(f"{query_date} 的 {index_name} 返回空成分，不能用最近快照冒充")

    raw = raw_frame.copy()
    raw["code"] = raw["code"].astype(str).str.strip()
    raw["code_name"] = raw["code_name"].astype(str).str.strip()

    if not raw["code"].str.fullmatch(A_SHARE_PATTERN).all():
        invalid = raw.loc[~raw["code"].str.fullmatch(A_SHARE_PATTERN), "code"].head(5)
        raise ValueError(f"发现非沪深 A 股证券代码：{invalid.tolist()}")

    if raw["code"].duplicated().any():
        raise ValueError(f"{index_name} 在 {query_date} 出现重复股票代码")

    actual_update = pd.to_datetime(raw["updateDate"], errors="coerce")
    query_stamp = pd.Timestamp(query_date)
    if actual_update.isna().any():
        raise ValueError(f"{index_name} 数据更新日期缺失或无法解析，不能验证历史时点")

    ages = (query_stamp - actual_update).dt.days
    if (ages < 0).any():
        raise ValueError(
            f"{index_name} 在 {query_date} 查询却返回未来更新的快照，禁止用于历史回测"
        )
    if (ages > max_staleness_days).any():
        raise ValueError(
            f"{index_name} 在 {query_date} 返回了过旧快照，最大滞后"
            f" {int(ages.max())} 天，超过阈值 {max_staleness_days} 天"
        )

    return pd.DataFrame({
        "查询日期": query_stamp.strftime("%Y-%m-%d"),
        "指数": index_name,
        "股票代码": raw["code"].to_numpy(),
        "股票名称": raw["code_name"].to_numpy(),
        "数据更新日期": actual_update.dt.strftime("%Y-%m-%d").to_numpy(),
        "快照滞后天数": ages.to_numpy(),
        "数据来源": "BaoStock",
        "获取方式": source_kind,
    }, columns=SNAPSHOT_COLUMNS)


def get_snapshot(
    index_name: str, query_date: str, max_staleness_days: int
) -> pd.DataFrame:
    """按历史日期查询接口；缓存原始字段，重跑时可以复用。"""
    path = CACHE_DIR / f"{query_date}_{'hs300' if index_name == '沪深300' else 'zz500'}.csv"

    if path.exists():
        raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        source_kind = "本地缓存"
    else:
        api_name = INDEX_APIS[index_name]
        query_function = getattr(bs, api_name)
        raw = fetch_result(query_function(date=query_date), api_name)

        # 先校验再缓存：不能让错误日期或空快照进入本地数据底座。
        validate_snapshot(raw, index_name, query_date, max_staleness_days, "网络接口")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(path, index=False, encoding="utf-8-sig")
        source_kind = "网络接口"

    return validate_snapshot(
        raw, index_name, query_date, max_staleness_days, source_kind
    )


def audit_snapshots(snapshots: pd.DataFrame) -> pd.DataFrame:
    """逐日期逐指数生成审计摘要，帮助发现漏查、过旧、数量异常。"""
    keys = ["查询日期", "指数"]
    audit = snapshots.groupby(keys, as_index=False).agg(
        成分股数=("股票代码", "nunique"),
        最早数据更新日=("数据更新日期", "min"),
        最晚数据更新日=("数据更新日期", "max"),
        最大快照滞后天数=("快照滞后天数", "max"),
    )
    return audit.sort_values(keys).reset_index(drop=True)


def main() -> None:
    """先构建交易日历，再逐历史日期查询两只指数并导出审计结果。"""
    args = parse_args()
    if args.limit < 0:
        raise ValueError("--limit 不能为负数")
    if args.max_staleness_days < 0:
        raise ValueError("--max-staleness-days 不能为负数")

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
                frame = get_snapshot(index_name, date, args.max_staleness_days)
                snapshots.append(frame)
                print(
                    f"历史快照：{date} {index_name}，{len(frame)} 只，"
                    f"实际更新日最早 {frame['数据更新日期'].min()}"
                )
    finally:
        bs.logout()

    full = pd.concat(snapshots, ignore_index=True)
    if full.duplicated(["查询日期", "指数", "股票代码"]).any():
        raise ValueError("历史快照主键重复")
    audit = audit_snapshots(full)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.frequency}_{args.start_date}_{args.end_date}_{'全部' if args.limit == 0 else '前'+str(args.limit)+'期'}"
    data_path = OUTPUT_DIR / f"历史指数成分快照_{suffix}.csv"
    audit_path = OUTPUT_DIR / f"历史指数成分审计_{suffix}.csv"
    full.to_csv(data_path, index=False, encoding="utf-8-sig")
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")

    print(f"\n历史快照保存：{data_path}")
    print(f"审计汇总保存：{audit_path}")
    print(f"采集日期数：{len(query_dates)}；总记录数：{len(full):,}")
    print(audit.to_string(index=False))
    print(
        "\n警告：这是所查询日期的历史指数快照，不是完整的每日动态股票池。"
        "月度、周度查询不能直接为期间内每一天补写指数成员资格。"
    )


if __name__ == "__main__":
    main()
