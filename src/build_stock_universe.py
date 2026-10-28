"""构建沪深 A 股普通股票的基础股票池。"""

from pathlib import Path
import re
import sys

import baostock as bs
import pandas as pd


# 项目根目录：脚本放在 src/ 下，因此向上一级即为项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 原始股票池保存在 manifests 目录，与行情数据分开管理。
OUTPUT_DIR = PROJECT_ROOT / "data" / "manifests"
OUTPUT_FILE = OUTPUT_DIR / "沪深A股股票池.csv"

# 股票池的参考日期。必须是交易日，否则接口可能返回空列表。
# 本阶段用最近一个已完成的交易日建立初版股票池。
REFERENCE_DATE = "2026-10-27"

# 沪深普通 A 股的常见代码前缀。
# 这只是初筛规则，不应取代正式证券类别字段。
SHARE_CODE_PATTERN = re.compile(
    r"^(sh\.(600|601|603|605|688)\d{3}|"
    r"sz\.(000|001|002|003|300|301)\d{3})$"
)


def fetch_stock_list(reference_date: str) -> pd.DataFrame:
    """从 BaoStock 获取指定日期的证券列表。"""
    login_result = bs.login()

    if login_result.error_code != "0":
        raise RuntimeError(
            f"BaoStock 登录失败：{login_result.error_msg}"
        )

    try:
        result = bs.query_all_stock(day=reference_date)

        if result.error_code != "0":
            raise RuntimeError(
                f"获取证券列表失败：{result.error_msg}"
            )
        rows = []
        while result.next():
            rows.append(result.get_row_data())

        return pd.DataFrame(rows, columns=result.fields)

    finally:
        # 即使查询发生异常，也尽量退出 BaoStock 会话。
        bs.logout()
        print("读取结束")


def build_universe(stock_df: pd.DataFrame) -> pd.DataFrame:
    """初筛沪深普通 A 股，并添加可复核的筛选字段。"""
    required_columns = {"code", "tradeStatus", "code_name"}
    missing_columns = required_columns - set(stock_df.columns)

    if missing_columns:
        raise ValueError(
            f"证券列表缺少必要字段：{sorted(missing_columns)}"
        )

    universe = stock_df.copy()

    # 清理文本字段，避免空格造成代码或名称匹配失败。
    for column in ["code", "tradeStatus", "code_name"]:
        universe[column] = universe[column].astype("string").str.strip()

    # 仅保留沪深市场普通 A 股常见代码段。
    # 该规则属于初筛，后续仍需人工核查特殊证券及历史代码变化。
    universe = universe[
        universe["code"].str.match(SHARE_CODE_PATTERN, na=False)
    ].copy()

    # 将交易状态转换成字符串，避免接口返回类型变化造成比较失败。
    universe["tradeStatus"] = universe["tradeStatus"].astype(str)

    # 保留状态字段，暂不把非交易状态股票直接删除。
    # 停牌股票在指定日期可能不交易，但它仍可能属于研究股票池。
    universe["参考日交易状态"] = universe["tradeStatus"].map(
        {"1": "正常交易", "0": "非交易状态"}
    ).fillna("未知")

    universe["参考日期"] = REFERENCE_DATE
    universe["股票代码"] = universe["code"]
    universe["股票名称"] = universe["code_name"]

    # 先检查重复代码，避免同一股票重复进入股票池。
    duplicated_codes = universe["code"].duplicated(keep=False)
    if duplicated_codes.any():
        duplicate_list = universe.loc[
            duplicated_codes, "code"
        ].tolist()
        raise ValueError(f"股票代码重复：{duplicate_list[:20]}")

    # 以中文列名输出，方便直接用 Excel 检查。
    output_columns = [
        "参考日期",
        "股票代码",
        "股票名称",
        "参考日交易状态",
    ]

    return (
        universe[output_columns]
        .sort_values("股票代码")
        .reset_index(drop=True)
    )


def main() -> None:
    """运行股票池构建流程并输出基本统计。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"正在获取 {REFERENCE_DATE} 的证券列表……")
    stock_df = fetch_stock_list(REFERENCE_DATE)

    if stock_df.empty:
        raise RuntimeError(
            "证券列表为空。请检查参考日期是否为交易日，"
            "并确认 BaoStock 接口返回正常。"
        )

    universe = build_universe(stock_df)
    universe.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print("\n股票池构建完成")
    print(f"接口返回证券数：{len(stock_df):,}")
    print(f"初筛股票数：{len(universe):,}")
    print(f"正常交易：{(universe['参考日交易状态'] == '正常交易').sum():,}")
    print(f"非交易状态：{(universe['参考日交易状态'] == '非交易状态').sum():,}")
    print(f"输出文件：{OUTPUT_FILE}")

    print("\n前 10 条记录：")
    print(
        universe.head(10).to_string(
            index=False,
            col_space=12,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\n程序运行失败：{error}", file=sys.stderr)
        raise
