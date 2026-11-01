"""T1.3 合并与标准化：把每只股票的 parquet 合成一张全样本日线面板。

用法：
    python src/build_panel.py

输入：
    data/raw/baostock/*.parquet     每只股票一个文件，后复权（adjustflag=1）

输出：
    data/processed/daily_panel.parquet          全样本日线面板
    results/tables/panel_quality_summary.csv    面板质量汇总（中文表头）

本轮做的事：
1. 统一字段顺序、类型、股票代码格式。
2. 检查重复记录、复权标识、价格逻辑（收盘价非正、最高价低于最低价）。
3. 用后复权收盘价计算日收益率，并保留另一种算法做交叉验证。
4. 只做结构性检查，详细质量报告（停牌、ST、涨跌停、按年覆盖率）留给 T1.4。

为什么收益率用 close / 上一交易日 close，而不是 close / preclose：
   后复权序列已经把分红送股折算进价格，相邻收盘价之比就是真实的持有收益；
   preclose 是行情接口给出的前收盘，个别除权日可能与后复权序列不一致，
   所以把它只用于诊断，不作为正式收益。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "baostock"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"

PANEL_FILE = PROCESSED_DIR / "daily_panel.parquet"
SUMMARY_FILE = RESULT_DIR / "panel_quality_summary.csv"

# BaoStock 日线字段顺序。合并时按这个顺序重排，保证所有股票结构一致。
COLUMN_ORDER = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "adjustflag",
    "turn",
    "tradestatus",
    "pctChg",
    "isST",
]

# 需要转成浮点的列。volume 是股数，可能很大，用 float 足够做研究。
FLOAT_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "turn",
    "pctChg",
]

# 需要转成整数的列，保留接口原始编码：tradestatus 1=正常交易 0=停牌，isST 1=ST。
INT_COLUMNS = ["adjustflag", "tradestatus", "isST"]


def read_one(path: Path) -> pd.DataFrame:
    """读取单只股票的数据，并做类型转换。

    参数：
        path: 一个 parquet 文件路径，文件名形如 sh.600000.parquet。
    返回：
        字段齐全、类型统一的行情表；缺少必要字段时抛出异常并指明文件。
    """
    frame = pq.read_table(path).to_pandas()

    missing = [col for col in COLUMN_ORDER if col not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} 缺少字段：{missing}")

    frame = frame[COLUMN_ORDER].copy()

    # 日期统一成 datetime，后续排序和 shift 都依赖它。
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    # 代码存成字符串，避免 sh.000001 被当成数字或丢失前导零。
    frame["code"] = frame["code"].astype("string")

    for col in FLOAT_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    for col in INT_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("Int64")

    return frame


def load_all() -> tuple[pd.DataFrame, pd.DataFrame]:
    """遍历原始目录，合并成一张长表，并剔除非后复权记录。

    参数：无。
    返回：
        (全样本面板, 被剔除记录明细)
        面板列顺序固定，已按股票代码和日期排序；明细用于质量报告追溯。

    为什么剔除而不是保留：
        BaoStock 对少数股票（例如代码变更后的 sz.302132）即使请求后复权，
        仍会返回 adjustflag=3 的未复权行。未复权序列在除权日会出现假跌幅，
        混进反转信号会制造虚假的极端收益，所以统一剔除，不做口径拼接。
    """
    files = sorted(RAW_DIR.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"未找到原始行情文件：{RAW_DIR}")

    frames = []
    for path in files:
        frames.append(read_one(path))

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)

    # 先记录再剔除：质量报告需要知道丢了哪些行、涉及哪些股票。
    dropped = panel.loc[panel["adjustflag"] != 1, ["code", "date", "adjustflag"]].copy()
    panel = panel.loc[panel["adjustflag"] == 1].reset_index(drop=True)

    # 剔除后一只股票都不剩，说明它整份都不是后复权，后续无法使用。
    empty_codes = set(dropped["code"]) - set(panel["code"])
    if empty_codes:
        print(f"注意：以下股票剔除后无可用数据：{sorted(empty_codes)}")

    return panel, dropped



def check_structure(panel: pd.DataFrame, dropped: pd.DataFrame) -> dict:
    """做结构性检查，返回可直接写入中文汇总表的字典。

    参数：
        panel: 合并后的全样本面板，已剔除非后复权行。
        dropped: 被剔除的非后复权记录明细。
    返回：
        键为中文列名的检查结果。
    """
    duplicated = int(panel.duplicated(["code", "date"]).sum())
    if duplicated:
        raise ValueError(f"发现 {duplicated} 条重复的股票-日期记录，请先调查。")

    # 剔除后这里应当恒为 0；留着是为了防止以后改动 load_all 时悄悄混入口径。
    bad_adjust = int((panel["adjustflag"] != 1).sum())
    if bad_adjust:
        raise ValueError(f"面板中仍有 {bad_adjust} 条非后复权记录。")

    price_not_positive = int((panel["close"] <= 0).sum())
    high_below_low = int((panel["high"] < panel["low"]).sum())

    # 停牌日通常没有成交量和成交额，这里只统计比例，不做删除。
    suspended = int((panel["tradestatus"] == 0).sum())
    st_rows = int((panel["isST"] == 1).sum())

    trade_dates = panel["date"].nunique()
    return {
        "股票数": int(panel["code"].nunique()),
        "记录数": int(len(panel)),
        "交易日数": int(trade_dates),
        "起始日期": panel["date"].min().strftime("%Y-%m-%d"),
        "结束日期": panel["date"].max().strftime("%Y-%m-%d"),
        "每只股票平均行数": round(len(panel) / panel["code"].nunique(), 1),
        "重复记录数": duplicated,
        "剔除非后复权记录数": int(len(dropped)),
        "非后复权涉及股票数": int(dropped["code"].nunique()),
        "收盘价非正记录数": price_not_positive,
        "最高价低于最低价记录数": high_below_low,
        "停牌记录数": suspended,
        "停牌记录占比": f"{suspended / len(panel) * 100:.2f}%",
        "ST记录数": st_rows,
        "ST记录占比": f"{st_rows / len(panel) * 100:.2f}%",
    }


def add_returns(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """计算日收益率，并用 pctChg 修复复权因子跳变造成的假收益。

    参数：
        panel: 已完成结构检查的全样本面板。
    返回：
        (加上收益列的面板, 收益校验结果字典, 被修复记录明细)

    计算说明：
        ret_1d      正式收益，等于后复权收盘价 / 上一交易日收盘价 - 1。
        ret_by_pre  诊断用，等于收盘价 / preclose - 1，用来发现除权日口径错位。
        pct_change  接口给出的涨跌幅（按未复权价计算），可视为“真实日收益”。

    为什么需要修复：
        BaoStock 的后复权因子偶尔会在某一天跳变（例如 sz.000001 在
        2020-12-31 因子从 119.96 变成 99.79），导致后复权价凭空跌 16%，
        而未复权价当天其实上涨。pctChg 不依赖复权因子，因此当两者差异
        超过 0.1 个百分点时，以 pctChg 为准，并把这些行留档备查。
        每只股票第一行没有上一交易日，ret_1d 为空，不能填 0。
    """
    panel = panel.copy()
    grouped = panel.groupby("code", sort=False)

    panel["prev_close"] = grouped["close"].shift(1)
    panel["ret_1d"] = panel["close"] / panel["prev_close"] - 1
    panel["ret_by_pre"] = panel["close"] / panel["preclose"] - 1
    panel["pct_change"] = panel["pctChg"] / 100

    # 只在两种算法都有值、且差异明显时才算异常；停牌日 pctChg 为空，跳过。
    both = panel["pct_change"].notna() & panel["ret_1d"].notna()
    gap = (panel["pct_change"] - panel["ret_1d"]).abs()
    broken = both & (gap > 0.001)
    repaired = panel.loc[broken, ["code", "date", "close", "prev_close", "ret_1d", "pct_change"]].copy()

    panel.loc[broken, "ret_1d"] = panel.loc[broken, "pct_change"]

    checks = {
        "复权异常修复记录数": int(broken.sum()),
        "修复涉及股票数": int(repaired["code"].nunique()) if len(repaired) else 0,
        "可比较收益记录数": int(both.sum()),
        "收益绝对值超过20%记录数": int((panel["ret_1d"].abs() > 0.20).sum()),
    }

    if len(repaired):
        print(f"修复复权异常 {len(repaired)} 行，涉及 {checks['修复涉及股票数']} 只股票")
        print(repaired.to_string(index=False))

    return panel, checks, repaired


def main() -> None:
    """执行合并、检查、收益计算并落盘。"""
    pd.set_option("display.unicode.east_asian_width", True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"读取原始文件：{RAW_DIR}")
    panel, dropped = load_all()
    print(f"合并完成，保留 {len(panel):,} 行，剔除非后复权 {len(dropped):,} 行")

    structure = check_structure(panel, dropped)
    panel, returns, repaired = add_returns(panel)

    summary = {**structure, **returns}
    summary_frame = pd.DataFrame([summary])
    summary_frame.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

    # 被剔除的行单独留档，方便在质量报告里逐条追溯，也方便以后换数据源时复核。
    if not dropped.empty:
        dropped_path = RESULT_DIR / "panel_dropped_rows.csv"
        dropped.to_csv(dropped_path, index=False, encoding="utf-8-sig")
        print(f"剔除明细已保存：{dropped_path}")

    # 被修复的收益同样留档：这是复权因子跳变的证据链。
    if not repaired.empty:
        repaired_path = RESULT_DIR / "panel_repaired_returns.csv"
        repaired.to_csv(repaired_path, index=False, encoding="utf-8-sig")
        print(f"修复明细已保存：{repaired_path}")

    # 不写索引，读回时不会多出一列无用的行号；面板本身已按代码和日期排序。
    panel.to_parquet(PANEL_FILE, index=False)

    print("\n" + "=" * 70)
    print("面板质量汇总")
    print("=" * 70)
    print(summary_frame.T.to_string(header=False))
    print(f"\n面板已保存：{PANEL_FILE}")
    print(f"汇总表已保存：{SUMMARY_FILE}")

    print("\n各年记录数：")
    yearly = panel.groupby(panel["date"].dt.year)["code"].count()
    print(yearly.to_string())


if __name__ == "__main__":
    main()
