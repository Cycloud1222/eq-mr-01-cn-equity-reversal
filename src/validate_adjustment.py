"""T1.3 复权交叉验证：抽几只股票，用 AKShare 后复权日线与本地面板比对日收益率。

用法：
    python src/validate_adjustment.py

输入：
    data/processed/daily_panel.parquet    本地面板（BaoStock 后复权）

输出：
    results/tables/adjust_cross_check.csv  抽样比对结果（中文表头）

为什么比收益率而不是比价格：
    两家数据商的后复权基准日不同，同一只股票的价格水平可以差好几倍，
    但相邻交易日的收益率必须一致。收益率对不上，才说明复权或数据有问题。

为什么用新浪源（stock_zh_a_daily）而不是东财源（stock_zh_a_hist）：
    实测东财后复权序列与本地面板的平均日收益差异约 0.1%–0.3%，
    新浪源在同一批股票上只有约 0.003%，接近四舍五入误差，
    说明新浪与 BaoStock 的复权口径一致，东财的复权算法不同。
    这里要验证的是“本地复权算得对不对”，因此选口径一致的对照源。

为什么只抽样：
    逐个请求既慢又容易被限流；抽样覆盖主板、创业板、科创板各若干只，
    足以发现系统性口径问题。
"""

from __future__ import annotations

import time
from pathlib import Path

import akshare as ak
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_FILE = PROJECT_ROOT / "data" / "processed" / "daily_panel.parquet"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"
OUTPUT_FILE = RESULT_DIR / "adjust_cross_check.csv"

START_DATE = "2020-01-01"
END_DATE = "2025-12-31"

# 抽样覆盖不同板块：沪市主板、深市主板、创业板、科创板。
# 代码换手率、科创板 20% 涨跌幅都不同，能顺带验证价格序列没有错位。
SAMPLE_CODES = [
    "sh.600000",
    "sh.600519",
    "sh.601318",
    "sz.000001",
    "sz.000651",
    "sz.300750",
    "sz.300059",
    "sh.688981",
    "sh.688111",
    "sh.688036",
]

# 判定阈值：收益率最大绝对差异超过 0.5 个百分点就认为口径有问题。
RETURN_TOLERANCE = 0.005

# 新浪接口偶尔断连，重试几次再放弃，避免整批结果被网络问题带偏。
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 2


def to_akshare_symbol(code: str) -> str:
    """把 sh.600000 转成新浪接口需要的 sh600000。

    参数：
        code: BaoStock 风格代码，形如 sh.600000 或 sz.000001。
    返回：
        交易所前缀加六位数字，不带点。
    """
    return code.replace(".", "")


def fetch_akshare_returns(symbol: str) -> pd.Series:
    """取 AKShare 后复权日线，返回按日期索引的日收益率。

    参数：
        symbol: 交易所前缀加六位数字，例如 sh600000。
    返回：
        日期为索引、日收益率为值的序列；接口无数据时返回空序列。
    异常：
        重试 MAX_ATTEMPTS 次仍失败时抛出最后一次的错误。
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            frame = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=START_DATE.replace("-", ""),
                end_date=END_DATE.replace("-", ""),
                adjust="hfq",
            )
            break
        except Exception as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
    else:
        raise RuntimeError(f"重试 {MAX_ATTEMPTS} 次仍失败：{last_error}")

    if frame.empty:
        return pd.Series(dtype="float64")

    frame = frame[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.sort_values("date").set_index("date")
    return frame["close"].pct_change()


def compare_one(code: str, panel: pd.DataFrame) -> dict:
    """比对单只股票的两套日收益率。

    参数：
        code: BaoStock 风格代码。
        panel: 全样本面板，函数内部按代码切片。
    返回：
        一行中文表头的结果字典。
    """
    local = panel.loc[panel["code"] == code, ["date", "ret_1d"]].copy()
    local = local.dropna(subset=["ret_1d"]).set_index("date")["ret_1d"]

    try:
        remote = fetch_akshare_returns(to_akshare_symbol(code))
    except Exception as error:
        return {
            "股票代码": code,
            "共同交易日数": 0,
            "收益相关系数": "",
            "最大绝对差异": "",
            "平均绝对差异": "",
            "结论": f"AKShare 请求失败：{error}",
        }

    if remote.empty:
        return {
            "股票代码": code,
            "共同交易日数": 0,
            "收益相关系数": "",
            "最大绝对差异": "",
            "平均绝对差异": "",
            "结论": "AKShare 无数据",
        }

    joined = pd.concat(
        [local.rename("本地收益"), remote.rename("外部收益")],
        axis=1,
        join="inner",
    ).dropna()

    if joined.empty:
        return {
            "股票代码": code,
            "共同交易日数": 0,
            "收益相关系数": "",
            "最大绝对差异": "",
            "平均绝对差异": "",
            "结论": "日期完全不对齐",
        }

    diff = (joined["本地收益"] - joined["外部收益"]).abs()
    max_diff = float(diff.max())
    conclusion = "通过" if max_diff <= RETURN_TOLERANCE else "需复核"

    return {
        "股票代码": code,
        "共同交易日数": int(len(joined)),
        "收益相关系数": round(float(joined["本地收益"].corr(joined["外部收益"])), 4),
        "最大绝对差异": f"{max_diff:.4%}",
        "平均绝对差异": f"{float(diff.mean()):.4%}",
        "结论": conclusion,
    }


def main() -> None:
    """跑完抽样比对并把结果写成 CSV。"""
    pd.set_option("display.unicode.east_asian_width", True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    panel = pd.read_parquet(PANEL_FILE, columns=["code", "date", "ret_1d"])
    print(f"面板读取完成：{len(panel):,} 行，抽样 {len(SAMPLE_CODES)} 只")

    records = []
    for code in SAMPLE_CODES:
        record = compare_one(code, panel)
        records.append(record)
        print(f"{code}：{record['结论']}")
        # 主动间隔一下，新浪接口连续请求容易被断连。
        time.sleep(1)

    result = pd.DataFrame(records)
    result.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 80)
    print("复权交叉验证结果")
    print("=" * 80)
    print(result.to_string(index=False))
    print(f"\n结果已保存：{OUTPUT_FILE}")

    failed = result.loc[result["结论"] != "通过", "股票代码"].tolist()
    if failed:
        print(f"\n需要复核：{failed}")


if __name__ == "__main__":
    main()
