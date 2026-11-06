"""T1.2 批量下载：指数成分或股票池 → 每只一个 parquet + 下载日志。

用法：
    # 先小批量试跑
    python src/download_data.py --source index800 --start-date 2020-01-01 --limit 10

    # 正式跑 800 只
    python src/download_data.py --source index800 --start-date 2020-01-01

    # 从股票池跑，指定区间
    python src/download_data.py --source universe --start-date 2015-01-01 --end-date 2025-12-31
\n    # 按历史成分审计生成的缺口清单补齐行情\n    python src/download_data.py --source historical_missing --start-date 2020-01-01 --end-date 2025-12-31\n
设计说明：
1. 每只股票单独存 parquet，失败只影响一只，重跑不会重复下载。
2. 下载日志逐只落盘，记录状态、行数、耗时、错误原因。
3. 默认后复权（adjustflag=1），适合计算历史收益。
4. 指数成分来自 BaoStock 的沪深 300 与中证 500 接口，约 800 只。
5. 变量名用英文，表头用中文；注释解释“为什么这样做”，方便学习。
"""

from __future__ import annotations

import argparse
import socket
import time
from datetime import datetime
from pathlib import Path

import baostock as bs
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

UNIVERSE_FILE = PROJECT_ROOT / "data" / "manifests" / "沪深A股股票池.csv"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "baostock"
MANIFEST_DIR = PROJECT_ROOT / "data" / "manifests"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"

LOG_FILE = MANIFEST_DIR / "download_log.csv"
SUMMARY_FILE = RESULT_DIR / "download_summary.csv"
DEFAULT_MISSING_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "待核查行情缺口_历史指数成分快照_monthly_2020-01-01_2025-12-31_全部.csv"
)

FIELDS = (
    "date,code,open,high,low,close,preclose,"
    "volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
)

ADJUST_FLAG = "1"
ADJUST_LABEL = "后复权"

LOG_COLUMNS = [
    "股票代码",
    "股票名称",
    "数据源",
    "下载状态",
    "行数",
    "起始日期",
    "结束日期",
    "复权方式",
    "耗时秒",
    "重试次数",
    "错误信息",
    "下载时间",
]


def parse_args() -> argparse.Namespace:
    """读取命令行参数。limit=0 表示不限制数量。"""
    parser = argparse.ArgumentParser(description="BaoStock 批量日线下载")
    parser.add_argument(
        "--source",
        choices=["index800", "hs300", "zz500", "universe", "historical_missing"],
        default="index800",
        help="股票来源：当前指数成分、股票池或历史缺口清单",
    )
    parser.add_argument(
        "--missing-file",
        type=Path,
        default=DEFAULT_MISSING_FILE,
        help="historical_missing 来源使用的待补行情 CSV 文件",
    )
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--limit", type=int, default=0, help="最多下载多少只，0=全部")
    parser.add_argument("--offset", type=int, default=0, help="从第几只开始")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="单次接口查询超时秒数，避免网络挂起",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1,
        help="每只股票之间的间隔秒数，降低被限流的概率",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=0,
        help="本次最多运行多少分钟，0=不限制；到时会在当前股票完成后退出",
    )
    parser.add_argument("--force", action="store_true", help="忽略日志，强制重下")
    return parser.parse_args()


def load_index_codes(query_func, source_name: str) -> pd.DataFrame:
    """调用指数成分接口，返回代码和名称。"""
    result = query_func()
    if result.error_code != "0":
        raise RuntimeError(f"{source_name} 成分获取失败：{result.error_msg}")

    rows = []
    while result.next():
        rows.append(result.get_row_data())

    frame = pd.DataFrame(rows, columns=result.fields)
    frame = frame.rename(columns={"code": "股票代码", "code_name": "股票名称"})
    frame["数据源"] = source_name
    return frame[["股票代码", "股票名称", "数据源"]]


def load_missing_codes(path: Path) -> pd.DataFrame:
    """读取历史行情缺口清单，保留代码字符串并去重。"""
    if not path.exists():
        raise FileNotFoundError(f"缺少历史行情缺口清单：{path}")

    frame = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype={"股票代码": "string"},
    )
    required = {"股票代码", "股票名称"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"历史行情缺口清单缺少字段：{sorted(missing)}")
    if frame.empty:
        raise ValueError("历史行情缺口清单为空")

    # 审计输出的缺口清单通常全是待补项；保留此过滤可防止误传代码并集文件。
    if "待补行情文件" in frame.columns:
        needs_download = frame["待补行情文件"].astype("string").str.lower().isin(
            {"true", "1", "yes", "是"}
        )
        frame = frame.loc[needs_download].copy()

    if frame[["股票代码", "股票名称"]].isna().any().any():
        raise ValueError("历史行情缺口清单存在空股票代码或股票名称")
    frame["股票代码"] = frame["股票代码"].astype("string").str.strip()
    valid_code = frame["股票代码"].str.fullmatch(r"(sh|sz)\.\d{6}", na=False)
    if not valid_code.all():
        invalid = frame.loc[~valid_code, "股票代码"].astype(str).tolist()[:10]
        raise ValueError(f"历史行情缺口清单含无效股票代码：{invalid}")
    if frame.empty:
        raise ValueError("历史行情缺口清单中没有待补行情的有效股票")

    frame = frame.drop_duplicates("股票代码").reset_index(drop=True)
    frame["数据源"] = "historical_missing"
    return frame[["股票代码", "股票名称", "数据源"]]


def load_codes(source: str, missing_file: Path | None = None) -> pd.DataFrame:
    """按来源准备股票列表，统一去重。"""
    if source == "historical_missing":
        return load_missing_codes(missing_file or DEFAULT_MISSING_FILE)

    if source == "universe":
        universe = pd.read_csv(
            UNIVERSE_FILE,
            encoding="utf-8-sig",
            dtype={"股票代码": "string"},
        )
        universe = universe[universe["参考日交易状态"] == "正常交易"].copy()
        universe["数据源"] = "universe"
        return universe[["股票代码", "股票名称", "数据源"]]

    if source == "hs300":
        return load_index_codes(bs.query_hs300_stocks, "hs300")

    if source == "zz500":
        return load_index_codes(bs.query_zz500_stocks, "zz500")

    hs300 = load_index_codes(bs.query_hs300_stocks, "hs300")
    zz500 = load_index_codes(bs.query_zz500_stocks, "zz500")
    combined = pd.concat([hs300, zz500], ignore_index=True)
    combined = combined.drop_duplicates("股票代码").reset_index(drop=True)
    combined["数据源"] = "index800"
    return combined


def read_log() -> pd.DataFrame:
    """读取已有日志；没有日志时返回空表。"""
    if not LOG_FILE.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.read_csv(LOG_FILE, encoding="utf-8-sig", dtype={"股票代码": "string"})


def completed_codes(log: pd.DataFrame) -> set[str]:
    """只把“成功”且 parquet 已存在的股票视为完成。"""
    if log.empty:
        return set()
    ok = log[log["下载状态"] == "成功"].copy()
    codes = set()
    for code in ok["股票代码"].astype(str):
        if (RAW_DIR / f"{code}.parquet").exists():
            codes.add(code)
    return codes


def download_one(code: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, str]:
    """下载单只股票，返回行情表和错误信息。"""
    try:
        result = bs.query_history_k_data_plus(
            code,
            FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=ADJUST_FLAG,
        )
    except Exception as error:
        return pd.DataFrame(), f"查询异常：{error}"

    if result.error_code != "0":
        return pd.DataFrame(), f"接口错误：{result.error_msg}"

    rows = []
    while result.next():
        rows.append(result.get_row_data())

    if not rows:
        return pd.DataFrame(), "无数据（上市晚于起始日期或已退市）"

    return pd.DataFrame(rows, columns=result.fields), ""


def save_parquet(frame: pd.DataFrame, code: str) -> None:
    """单只股票存一个 parquet，代码保持字符串。"""
    frame = frame.copy()
    frame["code"] = frame["code"].astype(str)
    frame.to_parquet(RAW_DIR / f"{code}.parquet", index=False)


def main() -> None:
    """执行批量下载，逐只写日志。"""
    args = parse_args()

    # 给底层 socket 设置超时。BaoStock 查询在服务端卡住时，
    # 默认会一直等下去；设置超时后可以抛异常、重试、跳过。
    socket.setdefaulttimeout(args.timeout)

    pd.set_option("display.unicode.east_asian_width", True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    if args.source == "historical_missing":
        # 历史缺口清单是本地文件，不需要为读取股票列表调用 BaoStock。
        codes = load_codes(args.source, args.missing_file)
    else:
        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败：{login_result.error_msg}")

        try:
            codes = load_codes(args.source, args.missing_file)
        finally:
            # 指数成分查询完成后即可登出，下载阶段重新登录。
            bs.logout()

    codes = codes.sort_values("股票代码").reset_index(drop=True)
    if args.offset:
        codes = codes.iloc[args.offset :].reset_index(drop=True)
    if args.limit:
        codes = codes.iloc[: args.limit].reset_index(drop=True)

    log = read_log()
    done = set() if args.force else completed_codes(log)

    print(f"来源：{args.source}，候选 {len(codes)} 只，已完成 {len(done)} 只")
    print(f"区间：{args.start_date} 至 {args.end_date}，{ADJUST_LABEL}")

    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{login_result.error_msg}")

    batch_start = time.time()
    new_records = []
    consecutive_failures = 0

    try:
        for index, row in codes.iterrows():
            # 到时间上限就停，已完成的股票已经写入日志，下次可续传。
            if args.max_minutes and (time.time() - batch_start) / 60 >= args.max_minutes:
                print(f"达到本次时间上限 {args.max_minutes} 分钟，提前结束。")
                break

            code = str(row["股票代码"])
            name = str(row["股票名称"])

            if code in done:
                continue

            status = "失败"
            error = ""
            frame = pd.DataFrame()
            retries_used = 0
            single_start = time.time()

            for attempt in range(1, args.retries + 1):
                retries_used = attempt
                frame, error = download_one(
                    code, args.start_date, args.end_date
                )
                if not error:
                    status = "成功"
                    break
                if attempt < args.retries:
                    time.sleep(2)

            elapsed = time.time() - single_start

            if status == "成功":
                save_parquet(frame, code)
                first_date = frame["date"].iloc[0]
                last_date = frame["date"].iloc[-1]
            else:
                first_date = last_date = ""
                if "无数据" in error:
                    status = "无数据"

            # 连续失败太多通常说明被限流或接口异常，先停下来，避免继续请求。
            if status == "失败":
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            record = {
                "股票代码": code,
                "股票名称": name,
                "数据源": row["数据源"],
                "下载状态": status,
                "行数": len(frame),
                "起始日期": first_date,
                "结束日期": last_date,
                "复权方式": ADJUST_LABEL,
                "耗时秒": round(elapsed, 2),
                "重试次数": retries_used,
                "错误信息": error,
                "下载时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            new_records.append(record)

            # 每只都落盘，异常中断也不用从头再来。
            log = pd.concat([log, pd.DataFrame([record])], ignore_index=True)
            log.to_csv(LOG_FILE, index=False, encoding="utf-8-sig")

            if consecutive_failures >= 5:
                print("连续 5 只失败，可能被限流，先停止本次任务。")
                break

            # 主动留一点间隔，降低服务端限流的概率。
            time.sleep(args.sleep)

            done_count = len(new_records)
            if done_count % 10 == 0 or index == len(codes) - 1:
                used = time.time() - batch_start
                average = used / done_count
                remaining = average * (len(codes) - len(done) - done_count)
                print(
                    f"进度 {done_count}/{len(codes) - len(done)}，"
                    f"已用 {used / 60:.1f} 分钟，均速 {average:.2f} 秒/只，"
                    f"预计剩余 {remaining / 60:.1f} 分钟"
                )

    finally:
        bs.logout()
        print("BaoStock 已登出")

    if not new_records:
        print("没有新增记录，全部股票都已完成。")
        return

    batch = pd.DataFrame(new_records)
    total_seconds = batch["耗时秒"].sum()
    success = int((batch["下载状态"] == "成功").sum())
    no_data = int((batch["下载状态"] == "无数据").sum())
    failed = len(batch) - success - no_data

    summary = pd.DataFrame([{
        "数据源": args.source,
        "区间": f"{args.start_date} 至 {args.end_date}",
        "本次尝试": len(batch),
        "成功": success,
        "无数据": no_data,
        "失败": failed,
        "总行数": int(batch["行数"].sum()),
        "总耗时分钟": round(total_seconds / 60, 2),
        "均速秒每只": round(total_seconds / len(batch), 2),
        "日志文件": str(LOG_FILE),
    }])
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 90)
    print("批量下载汇总")
    print("=" * 90)
    print(summary.to_string(index=False))

    if failed or no_data:
        print("\n未成功清单：")
        print(
            batch.loc[
                batch["下载状态"] != "成功",
                ["股票代码", "股票名称", "下载状态", "错误信息"],
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
