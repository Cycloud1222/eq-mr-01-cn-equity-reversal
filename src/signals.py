from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "processed" / "daily_bars_normalized.csv"
OUTPUT_DIR = ROOT / "data" / "processed"

FORMATION_PERIODS = [1, 5, 20]
FORWARD_HORIZONS = [1, 2, 5]


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"未找到标准化行情文件：{INPUT_FILE}\n"
            "请先运行：python src/normalize_data.py"
        )

    df = pd.read_csv(
        INPUT_FILE,
        dtype={"ts_code": "string"},
        parse_dates=["trade_date"],
    )

    required = ["trade_date", "ts_code", "close"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"输入文件缺少必要字段：{missing}")

    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    if df[["trade_date", "ts_code"]].isna().any().any():
        raise ValueError("交易日期或股票代码存在缺失，请先清洗数据。")

    if df.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("发现重复的股票-日期记录，请先解决重复问题。")

    if df["close"].isna().any() or df["close"].le(0).any():
        raise ValueError("收盘价存在缺失或非正数，请先检查行情数据。")

    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = df.groupby("ts_code", sort=False)["close"]

    # 历史收益率：只使用当日及之前的收盘价
    for period in FORMATION_PERIODS:
        df[f"past_{period}d_raw"] = (
            grouped.pct_change(period, fill_method=None)
        )

    # 未来收益率：仅作为研究标签，不得作为信号输入
    for horizon in FORWARD_HORIZONS:
        df[f"future_{horizon}d_raw"] = (
            grouped.shift(-horizon) / df["close"] - 1
        )

    output_file = OUTPUT_DIR / "reversal_signals_raw.csv"
    df.to_csv(output_file, index=False, encoding="utf-8-sig")

    print("短期反转信号计算完成")
    print(f"输入记录数：{len(df)}")
    print(f"股票数量：{df['ts_code'].nunique()}")
    print(f"输出文件：{output_file}")

    print("\n各字段有效值数量：")
    feature_cols = [
        f"past_{p}d_raw" for p in FORMATION_PERIODS
    ] + [
        f"future_{h}d_raw" for h in FORWARD_HORIZONS
    ]
    print(df[feature_cols].notna().sum().to_string())

    print("\n数据预览：")
    print(
        df[
            ["trade_date", "ts_code", "close"]
            + feature_cols
        ].head(10).to_string(index=False)
    )

    print(
        "\n注意：收益率基于未复权收盘价，只用于验证计算流程，"
        "不能直接视为正式策略收益。"
    )


if __name__ == "__main__":
    main()

