from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
REPORT_DIR = PROJECT_ROOT / "results" / "tables"

PRICE_COLS = ["open", "high", "low", "close", "preclose"]
NUMERIC_COLS = PRICE_COLS + ["volume", "amount", "turn", "pctChg"]


def main():
    files = sorted(
        RAW_DIR.glob("stock_data_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(
            f"没有找到行情 CSV 文件：{RAW_DIR}"
        )

    input_file = files[0]
    print(f"检查文件：{input_file}")

    df = pd.read_csv(input_file, dtype={"code": "string"})
    if df.empty:
        raise ValueError("CSV 文件为空，没有可检查的记录。")

    required_cols = [
        "date", "code", "open", "high", "low",
        "close", "preclose", "volume", "amount",
    ]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValueError(f"缺少必要字段：{missing_cols}")

    # 保留原始数据，只在内存副本上进行类型转换
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")

    for col in NUMERIC_COLS:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")

    issues = []

    def record_issue(mask, issue_name):
        """把每一类异常记录下来。"""
        mask = pd.Series(mask, index=data.index).fillna(False)
        if mask.any():
            bad = data.loc[mask].copy()
            bad["issue"] = issue_name
            issues.append(bad)

    # 1. 日期、股票代码
    record_issue(data["date"].isna(), "invalid_date")
    record_issue(
        data["code"].isna() | data["code"].str.strip().eq(""),
        "missing_code",
    )

    # 2. 同一股票同一日期重复
    record_issue(
        data.duplicated(["code", "date"], keep=False),
        "duplicate_code_date",
    )

    # 3. 关键价格为空或非正数
    for col in PRICE_COLS:
        if col in data.columns:
            record_issue(data[col].isna(), f"missing_or_non_numeric_{col}")
            record_issue(data[col].le(0), f"non_positive_{col}")

    # 4. 价格之间的逻辑关系
    record_issue(
        data["high"].notna()
        & data["low"].notna()
        & data["high"].lt(data["low"]),
        "high_below_low",
    )

    for col in ["open", "close"]:
        record_issue(
            data["high"].notna()
            & data["low"].notna()
            & data[col].notna()
            & (
                data[col].gt(data["high"])
                | data[col].lt(data["low"])
            ),
            f"{col}_outside_high_low",
        )

    # 5. 成交量和成交额
    for col in ["volume", "amount"]:
        if col in data.columns:
            record_issue(data[col].isna(), f"missing_or_non_numeric_{col}")
            record_issue(data[col].lt(0), f"negative_{col}")

    # 6. 日期排序：检查原始文件中是否存在倒序
    valid_dates = data.dropna(subset=["date", "code"]).copy()
    sorted_data = valid_dates.sort_values(["code", "date"])
    out_of_order = (
        valid_dates.index.to_series().groupby(valid_dates["code"]).apply(
            lambda idx: pd.Series(idx, index=idx).diff().lt(0)
        )
    )
    # 另行按每只股票的原始行顺序检查日期是否递增
    order_bad_indices = set()
    for _, group in valid_dates.groupby("code", sort=False):
        dates = group["date"]
        bad = dates.diff().lt(pd.Timedelta(0))
        order_bad_indices.update(group.index[bad])

    if order_bad_indices:
        record_issue(
            data.index.isin(order_bad_indices),
            "date_out_of_order",
        )

    # 7. 汇总：每只股票的记录数、日期范围、重复数量
    summary = (
        valid_dates.groupby("code", dropna=False)
        .agg(
            rows=("date", "size"),
            first_date=("date", "min"),
            last_date=("date", "max"),
            unique_dates=("date", "nunique"),
        )
        .reset_index()
    )
    summary["duplicate_rows"] = summary["rows"] - summary["unique_dates"]

    # 汇总异常
    if issues:
        issue_df = pd.concat(issues, ignore_index=True)
        issue_summary = (
            issue_df.groupby("issue")
            .size()
            .rename("count")
            .reset_index()
            .sort_values("count", ascending=False)
        )
    else:
        issue_df = pd.DataFrame()
        issue_summary = pd.DataFrame(columns=["issue", "count"])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = REPORT_DIR / "data_quality_by_stock.csv"
    issue_summary_path = REPORT_DIR / "data_quality_issues_summary.csv"
    issue_detail_path = REPORT_DIR / "data_quality_issues_detail.csv"

    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    issue_summary.to_csv(
        issue_summary_path, index=False, encoding="utf-8-sig"
    )
    if not issue_df.empty:
        issue_df.to_csv(
            issue_detail_path, index=False, encoding="utf-8-sig"
        )

    print("\n========== 数据质量报告 ==========")
    print(f"总记录数：{len(data)}")
    print(f"股票数量：{data['code'].nunique()}")
    print(f"日期解析失败：{data['date'].isna().sum()}")
    print(
        "重复的股票-日期记录："
        f"{data.duplicated(['code', 'date'], keep=False).sum()}"
    )
    print(f"异常记录分类数量：{len(issue_summary)}")

    if issue_summary.empty:
        print("未发现本脚本定义的异常。")
    else:
        print("\n异常分类：")
        print(issue_summary.to_string(index=False))

    print("\n各股票数据覆盖：")
    print(summary.to_string(index=False))

    print("\n报告保存位置：")
    print(summary_path)
    print(issue_summary_path)
    if not issue_df.empty:
        print(issue_detail_path)

    print(
        "\n注意：异常提示不一定代表真实错误，"
        "需结合停牌、交易状态和数据源规则复核。"
    )


if __name__ == "__main__":
    main()
