from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

files = sorted(
    [
        *RAW_DIR.glob("stock_data_*.csv"),
    ],
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)

if not files:
    raise FileNotFoundError(f"未找到行情文件：{RAW_DIR}")

df = pd.read_csv(files[0], dtype={"code": "string"})
print(f"读取文件：{files[0].name}")

required = ["date", "code", "open", "high", "low", "close", "preclose"]
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f"缺少字段：{missing}")

df["date"] = pd.to_datetime(df["date"], errors="coerce")

for col in ["open", "high", "low", "close", "preclose"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df = df.sort_values(["code", "date"]).copy()

# 当日收盘价相对前一交易日收盘价的简单收益率
# 注意：这是未复权价格的初步检查，不是正式研究收益率
df["daily_return_raw"] = (
    df.groupby("code")["close"].pct_change()
)

print("\n各股票收益率概况：")
summary = (
    df.groupby("code")
    .agg(
        rows=("date", "size"),
        first_date=("date", "min"),
        last_date=("date", "max"),
        missing_close=("close", lambda x: x.isna().sum()),
        min_close=("close", "min"),
        max_close=("close", "max"),
        return_count=("daily_return_raw", "count"),
        min_daily_return=("daily_return_raw", "min"),
        max_daily_return=("daily_return_raw", "max"),
    )
)
print(summary.to_string())

# 提示极端收益，不自动认定为错误
extreme = df.loc[
    df["daily_return_raw"].abs() > 0.15,
    ["date", "code", "preclose", "close", "daily_return_raw"],
]

print("\n绝对值超过 15% 的简单日收益记录：")
if extreme.empty:
    print("未发现")
else:
    print(extreme.to_string(index=False))

# 检查各股票的日期集合是否完全一致
date_sets = {
    code: set(group["date"].dropna())
    for code, group in df.groupby("code")
}
if date_sets:
    common_dates = set.intersection(*date_sets.values())
    union_dates = set.union(*date_sets.values())
    print(f"\n所有股票共同拥有的日期数：{len(common_dates)}")
    print(f"所有股票日期并集的日期数：{len(union_dates)}")
    print(f"是否完全对齐：{common_dates == union_dates}")
