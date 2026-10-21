from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PRICE_COLS = ["open", "high", "low", "close", "preclose"]
NUMERIC_COLS = PRICE_COLS + [
    "volume", "amount", "turn", "pctChg", "adjustflag", "tradestatus", "isST"
]


def main():
    files = sorted(
        [
            *RAW_DIR.glob("stock_data_*.csv"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(f"未找到原始行情文件：{RAW_DIR}")

    source = files[0]
    df = pd.read_csv(source, dtype={"code": "string"})
    print(f"读取原始文件：{source.name}")

    required = ["date", "code", *PRICE_COLS]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要字段：{missing}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.rename(
        columns={
            "date": "trade_date",
            "code": "ts_code",
        }
    )

    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # 不静默删除重复数据；发现重复时停止，先调查原因
    duplicates = df.duplicated(["ts_code", "trade_date"], keep=False)
    if duplicates.any():
        raise ValueError(
            f"发现 {duplicates.sum()} 条重复的股票-日期记录，请先调查。"
        )

    # 初步诊断收益率：未复权收盘价收益率
    # 不能把它直接当作最终回测收益率
    df["raw_close_return"] = (
        df.groupby("ts_code")["close"].pct_change()
    )

    # 记录数据来源，便于追溯
    df["source_file"] = source.name

    output = OUT_DIR / "daily_bars_normalized.csv"
    df.to_csv(output, index=False, encoding="utf-8-sig")

    print(f"标准化数据已保存：{output}")
    print(f"记录数：{len(df)}")
    print(f"股票数：{df['ts_code'].nunique()}")
    print(
        "重复的股票-日期记录：",
        df.duplicated(["ts_code", "trade_date"]).sum(),
    )
    print("\n字段预览：")
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    main()
