"""下载行业历史快照与每日市值，供横截面中性化实验使用。

数据来源：
1. BaoStock query_stock_industry(date=...)：按每周最后一个交易日取历史行业快照。
2. AKShare stock_zh_a_hist_163：取未复权日线及每日总市值、流通市值。

用法：
    # 先小规模试跑：前 3 只股票，行业仍按全样本周频采集
    python src/download_neutralization_data.py --limit 3

    # 全量下载；已有缓存会自动跳过
    python src/download_neutralization_data.py

    # 强制重新下载
    python src/download_neutralization_data.py --force

说明：
- 行业快照按 updateDate 记录实际生效日期，后续必须向前匹配，不能向后填充。
- 市值使用未复权行情接口返回的历史流通市值，不与后复权价格相乘。
- 本脚本只下载中性化所需数据，不修改现有行情面板与研究结果。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import baostock as bs
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_FILE = PROJECT_ROOT / "data" / "processed" / "daily_panel.parquet"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "neutralization"
INDUSTRY_CACHE_DIR = RAW_DIR / "industry_cache"
MARKET_CAP_CACHE_DIR = RAW_DIR / "market_cap_cache"
INDUSTRY_FILE = RAW_DIR / "industry_snapshots.parquet"
MARKET_CAP_FILE = RAW_DIR / "market_cap.parquet"
FAILURE_FILE = RAW_DIR / "market_cap_download_failures.csv"

INDUSTRY_COLUMNS = [
    "code", "effective_date", "query_date", "industry",
    "industry_classification", "source",
]
MARKET_CAP_COLUMNS = [
    "code", "date", "total_market_cap", "float_market_cap", "source",
]
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载行业历史快照与每日市值")
    parser.add_argument("--start-date", default="2020-01-02")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="最多下载前 N 只股票；0 表示全部股票",
    )
    parser.add_argument(
        "--industry-limit", type=int, default=0,
        help="最多采集前 N 个周度快照；0 表示全部快照",
    )
    parser.add_argument(
        "--sleep-seconds", type=float, default=0.5,
        help="AKShare 单只股票请求间隔秒数",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="忽略已有缓存并重新下载",
    )
    parser.add_argument(
        "--skip-industry", action="store_true",
        help="跳过行业快照下载",
    )
    parser.add_argument(
        "--skip-market-cap", action="store_true",
        help="跳过每日市值下载",
    )
    return parser.parse_args()


def fetch_baostock_result(result, table_name: str) -> pd.DataFrame:
    """把 BaoStock 查询结果转换成 DataFrame，并保留接口错误信息。"""
    if result.error_code != "0":
        raise RuntimeError(f"{table_name} 查询失败：{result.error_msg}")

    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def select_weekly_query_dates(trade_dates: pd.Series | list) -> list[str]:
    """每个交易周选择最后一个交易日，覆盖周内最新的行业分类更新。"""
    dates = pd.to_datetime(pd.Series(trade_dates), errors="raise")
    dates = dates.dropna().drop_duplicates().sort_values().reset_index(drop=True)
    if dates.empty:
        raise ValueError("没有可用交易日期")

    periods = dates.dt.to_period("W-SUN")
    weekly = dates.groupby(periods).max().sort_values()
    return weekly.dt.strftime("%Y-%m-%d").tolist()


def normalize_industry_snapshot(
    raw_frame: pd.DataFrame,
    query_date: str,
    universe: set[str] | None = None,
) -> pd.DataFrame:
    """标准化行业快照，并阻止把未来分类误配到历史日期。"""
    required = {
        "updateDate", "code", "industry", "industryClassification",
    }
    missing = required - set(raw_frame.columns)
    if missing:
        raise ValueError(f"行业数据缺少字段：{sorted(missing)}")
    if raw_frame.empty:
        raise ValueError(f"{query_date} 返回空行业快照")

    raw = raw_frame.copy()
    raw["code"] = raw["code"].astype("string").str.strip()
    if raw["code"].isna().any() or raw["code"].eq("").any():
        raise ValueError(f"{query_date} 行业快照包含空股票代码")
    if raw["code"].duplicated().any():
        raise ValueError(f"{query_date} 行业快照包含重复股票代码")

    update_dates = pd.to_datetime(raw["updateDate"], errors="coerce")
    if update_dates.isna().any():
        raise ValueError(f"{query_date} 行业快照包含无效更新日期")

    query_stamp = pd.Timestamp(query_date)
    if (update_dates > query_stamp).any():
        raise ValueError(
            f"{query_date} 返回了未来行业分类；不能用于历史中性化"
        )

    if universe is not None:
        raw = raw.loc[raw["code"].isin(universe)].copy()
    if raw.empty:
        raise ValueError(f"{query_date} 行业快照没有覆盖研究股票池")

    industry = raw["industry"].astype("string").str.strip()
    classification = raw["industryClassification"].astype("string").str.strip()
    industry = industry.mask(industry.eq(""), pd.NA)
    classification = classification.mask(classification.eq(""), pd.NA)

    return pd.DataFrame({
        "code": raw["code"].to_numpy(),
        "effective_date": update_dates.loc[raw.index].to_numpy(),
        "query_date": query_stamp,
        "industry": industry.to_numpy(),
        "industry_classification": classification.to_numpy(),
        "source": "BaoStock",
    }, columns=INDUSTRY_COLUMNS)


def normalize_market_cap(
    raw_frame: pd.DataFrame,
    code: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """标准化 AKShare 历史市值字段，保留未复权口径的每日市值。"""
    required = {"日期", "总市值", "流通市值"}
    missing = required - set(raw_frame.columns)
    if missing:
        raise ValueError(f"{code} 市值数据缺少字段：{sorted(missing)}")
    if raw_frame.empty:
        raise ValueError(f"{code} 没有返回历史市值数据")

    frame = raw_frame.copy()
    frame["date"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame["total_market_cap"] = pd.to_numeric(
        frame["总市值"], errors="coerce"
    )
    frame["float_market_cap"] = pd.to_numeric(
        frame["流通市值"], errors="coerce"
    )

    frame = frame.loc[
        frame["date"].between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    ].copy()
    frame.loc[frame["total_market_cap"] <= 0, "total_market_cap"] = pd.NA
    frame.loc[frame["float_market_cap"] <= 0, "float_market_cap"] = pd.NA
    frame = frame.dropna(subset=["date", "float_market_cap"])

    if frame.empty:
        raise ValueError(f"{code} 在指定区间没有有效流通市值")
    if frame["date"].duplicated().any():
        raise ValueError(f"{code} 市值数据存在重复日期")

    return pd.DataFrame({
        "code": code,
        "date": frame["date"].to_numpy(),
        "total_market_cap": frame["total_market_cap"].to_numpy(),
        "float_market_cap": frame["float_market_cap"].to_numpy(),
        "source": "AKShare-网易",
    }, columns=MARKET_CAP_COLUMNS).sort_values("date").reset_index(drop=True)


def load_universe(
    start_date: str,
    end_date: str,
    limit: int,
) -> tuple[list[str], pd.Series]:
    if not PANEL_FILE.exists():
        raise FileNotFoundError(
            f"找不到信号面板：{PANEL_FILE}。请先运行现有主流程。"
        )

    panel = pd.read_parquet(PANEL_FILE, columns=["code", "date"])
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")

    panel = panel.loc[panel["date"].between(start, end)]
    if panel.empty:
        raise ValueError("指定区间内没有行情面板记录")

    codes = sorted(panel["code"].astype(str).unique().tolist())
    if limit:
        codes = codes[:limit]
    trade_dates = panel["date"].drop_duplicates().sort_values()
    return codes, trade_dates


def fetch_industry_snapshots(
    codes: list[str],
    trade_dates: pd.Series,
    limit: int,
    force: bool,
) -> pd.DataFrame:
    """按周查询历史行业分类；缓存保存原始全市场响应，便于后续扩展股票池。"""
    query_dates = select_weekly_query_dates(trade_dates)
    if limit:
        query_dates = query_dates[:limit]

    INDUSTRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    universe = set(codes)
    normalized_frames = []

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{login.error_msg}")

    try:
        for query_date in query_dates:
            cache_path = INDUSTRY_CACHE_DIR / f"{query_date}.parquet"
            if cache_path.exists() and not force:
                raw = pd.read_parquet(cache_path)
                source = "本地缓存"
            else:
                result = bs.query_stock_industry(date=query_date)
                raw = fetch_baostock_result(result, "query_stock_industry")
                if raw.empty:
                    raise RuntimeError(f"{query_date} BaoStock 返回空行业快照")
                raw.to_parquet(cache_path, index=False)
                source = "网络接口"

            normalized = normalize_industry_snapshot(
                raw, query_date, universe=universe
            )
            normalized["source"] = f"BaoStock-{source}"
            normalized_frames.append(normalized)
            print(
                f"行业快照 {query_date}：覆盖 {len(normalized)} 只股票，"
                f"数据生效日 {normalized['effective_date'].max():%Y-%m-%d}"
            )
    finally:
        bs.logout()

    result = pd.concat(normalized_frames, ignore_index=True)
    result = result.drop_duplicates(
        ["code", "effective_date"], keep="last"
    ).sort_values(["code", "effective_date"]).reset_index(drop=True)
    return result


def fetch_market_cap(
    codes: list[str],
    start_date: str,
    end_date: str,
    sleep_seconds: float,
    force: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """逐只下载历史市值；每只股票单独缓存，失败后可从中断处继续。"""
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError(
            "下载历史市值需要 AKShare，请在项目环境运行：python -m pip install akshare"
        ) from error

    if not hasattr(ak, "stock_zh_a_hist_163"):
        raise RuntimeError(
            "当前 AKShare 版本没有 stock_zh_a_hist_163；"
            "该接口负责返回历史总市值和流通市值，请先更新 AKShare。"
        )

    MARKET_CAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    range_tag = f"{start_date}_{end_date}"
    frames = []
    failures = []

    for index, code in enumerate(codes, start=1):
        cache_path = MARKET_CAP_CACHE_DIR / f"{code}_{range_tag}.parquet"
        if cache_path.exists() and not force:
            frame = pd.read_parquet(cache_path)
            frames.append(frame)
            print(f"[{index}/{len(codes)}] {code}：使用缓存")
            continue

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = ak.stock_zh_a_hist_163(
                    symbol=code.replace(".", ""),
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                )
                frame = normalize_market_cap(
                    raw, code, start_date, end_date
                )
                frame.to_parquet(cache_path, index=False)
                frames.append(frame)
                last_error = None
                break
            except Exception as error:
                last_error = error
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_SLEEP_SECONDS)

        if last_error is not None:
            failures.append({
                "股票代码": code,
                "错误信息": str(last_error),
            })
            print(f"[{index}/{len(codes)}] {code}：下载失败")
        else:
            print(f"[{index}/{len(codes)}] {code}：已下载 {len(frame)} 行")

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if not frames:
        raise RuntimeError("没有下载到任何历史市值数据")

    result = pd.concat(frames, ignore_index=True)
    if result.duplicated(["code", "date"]).any():
        raise ValueError("合并后的市值数据存在重复股票-日期记录")
    result = result.sort_values(["code", "date"]).reset_index(drop=True)
    failure_frame = pd.DataFrame(failures, columns=["股票代码", "错误信息"])
    return result, failure_frame


def main() -> None:
    args = parse_args()
    if args.limit < 0 or args.industry_limit < 0:
        raise ValueError("--limit 和 --industry-limit 不能为负数")
    if args.sleep_seconds < 0:
        raise ValueError("--sleep-seconds 不能为负数")
    if args.skip_industry and args.skip_market_cap:
        raise ValueError("不能同时跳过行业和市值下载")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    codes, trade_dates = load_universe(
        args.start_date, args.end_date, args.limit
    )
    print(
        f"研究股票：{len(codes)} 只；交易日：{len(trade_dates)} 天；"
        f"区间：{args.start_date} 至 {args.end_date}"
    )

    if not args.skip_industry:
        industry = fetch_industry_snapshots(
            codes, trade_dates, args.industry_limit, args.force
        )
        industry.to_parquet(INDUSTRY_FILE, index=False)
        print(f"行业快照已保存：{INDUSTRY_FILE}（{len(industry):,} 行）")

    if not args.skip_market_cap:
        market_cap, failures = fetch_market_cap(
            codes,
            args.start_date,
            args.end_date,
            args.sleep_seconds,
            args.force,
        )
        market_cap.to_parquet(MARKET_CAP_FILE, index=False)
        print(f"历史市值已保存：{MARKET_CAP_FILE}（{len(market_cap):,} 行）")

        if not failures.empty:
            failures.to_csv(FAILURE_FILE, index=False, encoding="utf-8-sig")
            print(
                f"有 {len(failures)} 只股票未取得市值数据，"
                f"明细保存：{FAILURE_FILE}"
            )
        elif FAILURE_FILE.exists():
            FAILURE_FILE.unlink()

    print("下载结束。原始日线面板和既有研究结果未被修改。")


if __name__ == "__main__":
    main()
