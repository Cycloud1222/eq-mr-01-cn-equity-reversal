"""T3.1：审核历史指数成员与已有行情的覆盖差异，导出待补采名单。

用法：
    python src/audit_historical_coverage.py \
        --snapshots data/processed/历史指数成分快照_monthly_2020-01-01_2025-12-31_前3期.csv

本脚本不会下载或删除任何行情，仅核对：
    1. 历史成分是否出现在旧下载日志的任一「成功」记录里；
    2. 本地 data/raw/baostock/ 下是否实际存在对应 parquet 文件。

注意：本地 parquet 文件存在不代表质量合格或覆盖历史全区间，
      真正可用于研究仍需检查日期范围、复权口径与停牌数据。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = PROJECT_ROOT / "data" / "manifests" / "download_log.csv"
DEFAULT_RAW = PROJECT_ROOT / "data" / "raw" / "baostock"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "processed"

REQUIRED_SNAPSHOT = {"查询日期", "指数", "股票代码", "股票名称"}
REQUIRED_LOG = {"股票代码", "下载状态"}


def parse_args() -> argparse.Namespace:
    """必须显式传入待审核的历史快照文件，避免读到较旧的结果。"""
    parser = argparse.ArgumentParser(description="历史指数成员的行情覆盖率审计")
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def load_snapshots(path: Path) -> pd.DataFrame:
    """读取并验证历史成员快照的唯一性与基本字段。"""
    if not path.exists():
        raise FileNotFoundError(f"缺少历史成员快照：{path}")

    df = pd.read_csv(path, dtype={"股票代码": "string"}, encoding="utf-8-sig")
    missing = REQUIRED_SNAPSHOT - set(df.columns)
    if missing:
        raise ValueError(f"历史成员快照缺少字段：{sorted(missing)}")
    if df.empty:
        raise ValueError("历史成员快照为空")

    if df[list(REQUIRED_SNAPSHOT)].isna().any().any():
        raise ValueError("历史成员快照存在空日期、指数、代码或名称")

    dates = pd.to_datetime(df["查询日期"], errors="raise")
    if dates.isna().any():
        raise ValueError("存在空查询日期")
    df["查询日期"] = dates.dt.strftime("%Y-%m-%d")

    if df.duplicated(["查询日期", "指数", "股票代码"]).any():
        raise ValueError("历史成员快照的日期、指数、代码联合主键重复")
    return df


def successful_log_codes(path: Path) -> set[str]:
    """允许日志中同一股票存在重复尝试，任意一次成功即计为日志已成功。"""
    if not path.exists():
        raise FileNotFoundError(f"缺少原行情下载日志：{path}")
    log = pd.read_csv(path, encoding="utf-8-sig", dtype={"股票代码": "string"})
    missing = REQUIRED_LOG - set(log.columns)
    if missing:
        raise ValueError(f"下载日志缺少字段：{sorted(missing)}")
    return set(log.loc[log["下载状态"] == "成功", "股票代码"].dropna().astype(str))


def local_parquet_codes(raw_dir: Path) -> set[str]:
    """只检查物理文件是否存在，不把下载成功日志当作文件证据。"""
    return {path.stem for path in raw_dir.glob("*.parquet") if path.is_file()}


def calculate_coverage(
    snapshots: pd.DataFrame,
    log_codes: set[str],
    local_codes: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """得到逐查询日覆盖审计和跨查询日历史成员并集详情。

    研究对象是某日期的两个指数并集。同一股票即使同时属于两个指数，
    也只能在该日期计数一次。并集详情跨日期按股票代码去重。
    """
    # 先按证券代码去重：历史成员可以在多个指数或多个查询日出现。
    code_info = (
        snapshots.sort_values(["查询日期", "指数", "股票代码"])
        .groupby("股票代码", sort=True)
        .agg(
            首次出现日期=("查询日期", "min"),
            末次出现日期=("查询日期", "max"),
            历史快照出现记录数=("查询日期", "size"),
            股票名称=("股票名称", "first"),
        )
        .reset_index()
    )
    dates_seen = snapshots.groupby("股票代码")["查询日期"].nunique()
    indexes_seen = snapshots.groupby("股票代码")["指数"].apply(
        lambda series: "、".join(sorted(set(series.astype(str))))
    )

    code_info["出现的查询日期数"] = (
        code_info["股票代码"].map(dates_seen).astype(int)
    )
    code_info["涉及指数"] = code_info["股票代码"].map(indexes_seen)
    code_info["下载日志曾成功"] = code_info["股票代码"].isin(log_codes)
    code_info["本地Parquet存在"] = code_info["股票代码"].isin(local_codes)
    code_info["待补行情文件"] = ~code_info["本地Parquet存在"]

    summary_rows = []
    for query_date, date_group in snapshots.groupby("查询日期", sort=True):
        members = set(date_group["股票代码"].astype(str))
        total = len(members)
        in_log = len(members & log_codes)
        in_local = len(members & local_codes)
        summary_rows.append({
            "查询日期": query_date,
            "历史指数合并去重成分数": total,
            "下载日志成功覆盖数": in_log,
            "下载日志缺口数": total - in_log,
            "下载日志覆盖率": round(in_log / total, 6),
            "本地Parquet覆盖数": in_local,
            "本地Parquet缺口数": total - in_local,
            "本地Parquet覆盖率": round(in_local / total, 6),
        })

    return pd.DataFrame(summary_rows), code_info


def main() -> None:
    """只生成审计结果，未核实实际日期覆盖前不得自动启动下载。"""
    args = parse_args()
    snapshots = load_snapshots(args.snapshots)
    log_codes = successful_log_codes(args.log)
    local_codes = local_parquet_codes(args.raw_dir)
    summary, detail = calculate_coverage(snapshots, log_codes, local_codes)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / f"历史成分覆盖审计_{args.snapshots.stem}.csv"
    detail_path = args.output_dir / f"历史成分代码并集_{args.snapshots.stem}.csv"
    missing_path = args.output_dir / f"待核查行情缺口_{args.snapshots.stem}.csv"

    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    detail.loc[detail["待补行情文件"]].to_csv(
        missing_path, index=False, encoding="utf-8-sig"
    )

    print(f"历史成员代码并集：{len(detail):,} 只")
    print(f"下载成功日志代码数：{len(log_codes):,}")
    print(f"本地 parquet 文件数：{len(local_codes):,}")
    print("\n逐历史日期覆盖审计：")
    print(summary.to_string(index=False))
    print(f"\n逐日期审计：{summary_path}")
    print(f"历史成员并集：{detail_path}")
    print(f"本地文件缺口：{missing_path}")
    print(
        "\n注意：历史成员列表未完成时，此缺口清单仅对当前已采集日期有效；"
        "不能视为完整回测期间的所有缺失证券。"
    )


if __name__ == "__main__":
    main()
