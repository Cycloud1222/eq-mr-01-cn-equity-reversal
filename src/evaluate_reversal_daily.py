
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# 一、项目路径与研究参数
# ============================================================

# 当前脚本位于 src 文件夹，向上一级即为项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 输入文件由前面的信号计算脚本生成，只读取，不覆盖。
INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "reversal_signals_raw.csv"
)

# 统计结果统一保存到 results/tables 文件夹。
OUTPUT_DIR = PROJECT_ROOT / "results" / "tables"

# 形成期：观察股票过去 1、5、20 个交易日的收益表现。
FORMATION_PERIODS = [1, 5, 20]

# 未来期限：考察形成期结束后的 1、2、5 个交易日收益。
FORWARD_HORIZONS = [1, 2, 5]

# 每个交易日至少需要多少只有效数据的股票，才进行横截面分组。
# 目前的 5 只股票仅用于验证代码流程，不足以支持可靠的市场结论。
MIN_STOCKS_PER_DAY = 5

# 输家组与赢家组分别取当日过去收益率分布的底部 30% 和顶部 30%。
LOSER_QUANTILE = 0.30
WINNER_QUANTILE = 0.70


def validate_input(data):
    """检查输入数据的字段、重复记录和基本可用性。"""

    required_columns = [
        "trade_date",
        "ts_code",
        *[f"past_{period}d_raw" for period in FORMATION_PERIODS],
        *[f"future_{horizon}d_raw" for horizon in FORWARD_HORIZONS],
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in data.columns
    ]

    # 如果缺少字段，立即指出缺失列名，避免后续出现难以定位的 KeyError。
    if missing_columns:
        raise ValueError(
            "输入数据缺少必要字段："
            + "、".join(missing_columns)
            + "\n请检查信号文件的实际表头。"
        )

    # 日期统一转换为 pandas 日期格式；无法识别的日期将变为缺失值。
    data["trade_date"] = pd.to_datetime(
        data["trade_date"],
        errors="coerce",
    )

    if data["trade_date"].isna().any():
        raise ValueError("交易日期中存在无法识别的值，请先检查原始数据。")

    # 股票代码统一转换为字符串，避免读取 CSV 时发生类型不一致。
    data["ts_code"] = data["ts_code"].astype(str).str.strip()

    if data["ts_code"].eq("").any():
        raise ValueError("股票代码中存在空值，请先检查原始数据。")

    # 同一股票同一交易日必须只有一条记录，否则分组统计可能重复计数。
    duplicate_count = data.duplicated(
        subset=["trade_date", "ts_code"]
    ).sum()

    if duplicate_count > 0:
        raise ValueError(
            f"发现 {duplicate_count} 条重复的股票—交易日期记录，"
            "请先排查数据。"
        )

    # 收益率字段必须是数值；无法转换的异常内容会被标记为缺失。
    return_columns = [
        *[f"past_{period}d_raw" for period in FORMATION_PERIODS],
        *[f"future_{horizon}d_raw" for horizon in FORWARD_HORIZONS],
    ]

    for column in return_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    return data


def calculate_daily_stats(data, formation_period, forward_horizon):
    """
    按交易日进行横截面分组，并计算每日输家组与赢家组的未来收益差。

    每个交易日单独计算分位数，不将不同日期的股票混在一起排名。
    输家组是过去收益率较低的一组；赢家组是过去收益率较高的一组。

    注意：这里仍使用未复权价格计算的收益率，尚未扣除交易成本。
    """

    past_column = f"past_{formation_period}d_raw"
    future_column = f"future_{forward_horizon}d_raw"

    # 只保留本次分析所需字段，并排除形成期或未来收益缺失的记录。
    sample = data[
        ["trade_date", "ts_code", past_column, future_column]
    ].dropna(
        subset=[past_column, future_column]
    ).copy()

    daily_records = []

    # 每个交易日独立划分输家组和赢家组。
    for trade_date, daily_data in sample.groupby(
        "trade_date",
        sort=True,
    ):
        stock_count = len(daily_data)

        # 有效股票数不足时跳过当天，避免在极小横截面中强行分组。
        if stock_count < MIN_STOCKS_PER_DAY:
            continue

        loser_threshold = daily_data[past_column].quantile(
            LOSER_QUANTILE
        )
        winner_threshold = daily_data[past_column].quantile(
            WINNER_QUANTILE
        )

        # 收益率低于或等于底部阈值的股票进入输家组。
        loser_returns = daily_data.loc[
            daily_data[past_column] <= loser_threshold,
            future_column,
        ]

        # 收益率高于或等于顶部阈值的股票进入赢家组。
        winner_returns = daily_data.loc[
            daily_data[past_column] >= winner_threshold,
            future_column,
        ]

        if loser_returns.empty or winner_returns.empty:
            continue

        loser_mean = loser_returns.mean()
        winner_mean = winner_returns.mean()

        daily_records.append({
            "trade_date": trade_date,
            "stock_count": stock_count,
            "loser_count": len(loser_returns),
            "winner_count": len(winner_returns),
            "loser_mean_return": loser_mean,
            "winner_mean_return": winner_mean,
            "reversal_spread": loser_mean - winner_mean,
        })

    return pd.DataFrame(daily_records)


def summarize_daily_stats(
    daily_stats,
    formation_period,
    forward_horizon,
):
    """
    将每日反转收益差汇总为统计指标。

    初步 t 统计量 = 每日收益差均值 / 每日收益差均值的标准误。

    该统计量暂未修正时间序列自相关。尤其是未来 2 日和 5 日收益，
    相邻交易日的持有期可能重叠，因此不能把该统计量直接当作正式显著性结论。
    """

    if daily_stats.empty:
        return None

    spreads = daily_stats["reversal_spread"].dropna()
    day_count = len(spreads)

    if day_count == 0:
        return None

    mean_spread = spreads.mean()

    # 只有至少两天的数据，才可以计算样本标准差。
    spread_std = (
        spreads.std(ddof=1)
        if day_count > 1
        else np.nan
    )

    # 标准差为零或有效天数不足时，t 统计量没有可靠定义。
    if (
        day_count > 1
        and pd.notna(spread_std)
        and spread_std > 0
    ):
        t_statistic = mean_spread / (
            spread_std / np.sqrt(day_count)
        )
    else:
        t_statistic = np.nan

    return {
        "形成期（交易日）": formation_period,
        "未来期限（交易日）": forward_horizon,
        "有效交易日数": day_count,
        "输家组平均未来收益率": daily_stats[
            "loser_mean_return"
        ].mean(),
        "赢家组平均未来收益率": daily_stats[
            "winner_mean_return"
        ].mean(),
        "平均每日反转收益差": mean_spread,
        "每日收益差标准差": spread_std,
        "初步t统计量": t_statistic,
        "收益差为正的交易日占比": (spreads > 0).mean(),
    }


def format_summary_table(summary):
    """将统计结果转换为适合中文终端阅读的格式。"""

    display_table = summary.copy()

    # 收益率以百分比显示，统一保留两位小数。
    return_columns = [
        "输家组平均未来收益率",
        "赢家组平均未来收益率",
        "平均每日反转收益差",
        "每日收益差标准差",
    ]

    for column in return_columns:
        display_table[column] = display_table[column].map(
            lambda value: (
                f"{value:.2%}"
                if pd.notna(value)
                else "—"
            )
        )

    # t 统计量保留三位小数；正收益差占比显示为百分比。
    display_table["初步t统计量"] = display_table[
        "初步t统计量"
    ].map(
        lambda value: (
            f"{value:.3f}"
            if pd.notna(value)
            else "—"
        )
    )

    display_table["收益差为正的交易日占比"] = display_table[
        "收益差为正的交易日占比"
    ].map(
        lambda value: f"{value:.1%}"
    )

    return display_table


def main():
    """执行数据检查、逐日分组、汇总统计和结果导出。"""

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"找不到输入文件：{INPUT_FILE}\n"
            "请先运行 src/signals.py 生成信号数据。"
        )

    # 使用 UTF-8 编码读取 CSV；日期字段在校验函数中统一处理。
    data = pd.read_csv(INPUT_FILE)
    data = validate_input(data)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_records = []
    detail_records = []

    for formation_period in FORMATION_PERIODS:
        for forward_horizon in FORWARD_HORIZONS:

            daily_stats = calculate_daily_stats(
                data,
                formation_period,
                forward_horizon,
            )

            if daily_stats.empty:
                print(
                    f"提示：形成期 {formation_period} 日、"
                    f"未来期限 {forward_horizon} 日没有足够的有效交易日。"
                )
                continue

            # 为每日明细添加参数，方便后续筛选、画图和复核。
            daily_stats.insert(
                0,
                "forward_horizon",
                forward_horizon,
            )
            daily_stats.insert(
                0,
                "formation_period",
                formation_period,
            )

            detail_records.append(daily_stats)

            summary_record = summarize_daily_stats(
                daily_stats,
                formation_period,
                forward_horizon,
            )

            if summary_record is not None:
                summary_records.append(summary_record)

    if not summary_records:
        print(
            "没有生成可用的统计结果。请检查数据完整性、"
            "股票数量和有效收益记录。"
        )
        return

    # 汇总表使用中文列名，便于直接用 Excel 阅读。
    summary_table = pd.DataFrame(summary_records)

    summary_file = OUTPUT_DIR / "逐日反转效应统计.csv"
    summary_table.to_csv(
        summary_file,
        index=False,
        encoding="utf-8-sig",
        float_format="%.6f",
    )

    # 每日明细保留英文内部字段名，便于后续程序化分析。
    if detail_records:
        detail_table = pd.concat(
            detail_records,
            ignore_index=True,
        )

        # 将每日明细列名翻译成中文，方便人工检查。
        detail_table = detail_table.rename(columns={
            "formation_period": "形成期（交易日）",
            "forward_horizon": "未来期限（交易日）",
            "trade_date": "交易日期",
            "stock_count": "有效股票数",
            "loser_count": "输家组股票数",
            "winner_count": "赢家组股票数",
            "loser_mean_return": "输家组平均未来收益率",
            "winner_mean_return": "赢家组平均未来收益率",
            "reversal_spread": "每日反转收益差",
        })

        detail_file = OUTPUT_DIR / "逐日反转收益差明细.csv"
        detail_table.to_csv(
            detail_file,
            index=False,
            encoding="utf-8-sig",
            float_format="%.6f",
        )

        print(f"\n逐日明细文件：{detail_file}")

    # 终端输出专门进行格式化，原始 CSV 仍保留数值，方便进一步计算。
    display_table = format_summary_table(summary_table)

    print("\n" + "=" * 100)
    print("A股短期反转效应：逐日横截面统计结果")
    print("=" * 100)
    print(display_table.to_string(index=False, justify="center"))
    print("\n结果文件：")
    print(f"1. 汇总统计：{summary_file}")
    print(f"2. 每日明细：{OUTPUT_DIR / '逐日反转收益差明细.csv'}")

    print(
        "\n研究注意事项：当前样本只用于验证流程；"
        "尚未处理复权、交易成本、停牌、涨跌停、"
        "持有期重叠引起的自相关及多重检验问题。"
    )


if __name__ == "__main__":
    main()
