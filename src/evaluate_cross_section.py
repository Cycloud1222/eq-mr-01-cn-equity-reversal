"""T2.2 逐日横截面反转统计：形成期 1/5/20 日 × 持有期 1/2/5 日。

用法：
    先激活项目虚拟环境，再运行：
    python src/evaluate_cross_section.py

输入：
    data/processed/reversal_signals.parquet

输出：
    results/tables/cross_section_summary.csv    9 组参数的两端 30% 统计表
    results/tables/quantile_summary.csv         45 行五分组明细（Q1 最低，Q5 最高）
    results/tables/quantile_monotonicity.csv    9 组参数的单调性汇总
    results/tables/subsample_stability.csv      分年度稳定性（2 组关键参数）

口径（2026-11-04）：
1. 每个交易日单独分组，禁止把不同日期的股票混在一起排序。
   混池会让少数极端行情日主导分位，还会把横截面相关当成独立样本。
2. 两端分组：组内按过去收益升序排名，最低 30% 为空头组，最高 30% 为多头组；
   五分组：同一天按 20% 切成 Q1（最低）到 Q5（最高）。
3. 当日收益差 = 空头组未来收益均值 − 多头组未来收益均值，反转成立时为正。
4. 当天信号或未来收益缺失的股票先剔除；剩余股票少于 30 只则整天跳过，
   因为小样本的分位边界不稳定，结论只能算探索。
5. t 值用“每日收益差”的时间序列计算：均值 /（标准差 / √有效天数）。
   不能把每天所有股票堆在一起算，那会把横截面相关误当成独立样本，t 值虚高。
6. 分年度稳定性按信号日期归属自然年切分。12 月下旬的信号，其未来收益可能落在下一年，
   这类样本不截断：持有窗口必须完整，否则收益会被腰斩，反而制造假信号。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


# 项目根目录：本文件在 src/ 下，向上取一级
PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = PROJECT_ROOT / "data" / "processed" / "reversal_signals.parquet"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"
SUMMARY_FILE = RESULT_DIR / "cross_section_summary.csv"
QUINTILE_FILE = RESULT_DIR / "quantile_summary.csv"
MONOTONICITY_FILE = RESULT_DIR / "quantile_monotonicity.csv"
SUBSAMPLE_FILE = RESULT_DIR / "subsample_stability.csv"

# 实验矩阵：3 个形成期 × 3 个持有期，共 9 组，全部报告，不只挑最好看的
FORMATION_DAYS = [1, 5, 20]
FORWARD_DAYS = [1, 2, 5]

# 单个交易日的股票数下限，低于此值的日期整天跳过
MIN_STOCKS_PER_DAY = 30

# 两端分组分位：最低 30% 为空头，最高 30% 为多头，中间 40% 不参与当日收益差
LOWER_QUANTILE = 0.3
UPPER_QUANTILE = 0.7

# 五分组组数：用于检查 Q1→Q5 的单调性，判断是否只有极端组异常
QUANTILE_GROUPS = 5

# 秩相关系数绝对值达到这个值才认为存在明确单调性，避免把噪声当成趋势
MONOTONIC_CORRELATION = 0.9

# 分年度稳定性只跑两组关键参数，控制表格规模：
# 5 日形成 / 5 日持有代表主检验，20 日形成 / 1 日持有代表较长形成期
SUBSAMPLE_PAIRS = [(5, 5), (20, 1)]


def signal_columns() -> list[str]:
    """按实验矩阵生成需要的信号列名。

    返回：
        past_1d、past_5d、past_20d、future_1d、future_2d、future_5d 共 6 个列名。

    说明：
        列名从上面的天数常量推导，不手写字符串，改天数时只改一处。
    """
    past = [f"past_{days}d" for days in FORMATION_DAYS]
    future = [f"future_{days}d" for days in FORWARD_DAYS]
    return past + future


def load_signals(file_path: Path) -> pd.DataFrame:
    """读取信号面板并检查横截面分析需要的字段。

    参数：
        file_path: 信号面板 parquet 路径。
    返回：
        已按日期排序的面板；日期列统一为 datetime。
    """

    panel = pd.read_parquet(file_path)

    required_columns = {"code", "date", *signal_columns()}
    missing_columns = required_columns - set(panel.columns)
    if missing_columns:
        raise ValueError(f"信号面板缺少必要字段：{sorted(missing_columns)}")

    # 一天一只股票只应有一条记录，重复会让同一只股票被重复计入分位
    if panel.duplicated(subset=["date", "code"]).any():
        raise ValueError("存在重复的日期和股票代码组合")

    panel["date"] = pd.to_datetime(panel["date"])
    return panel.sort_values(["date", "code"]).reset_index(drop=True)


def prepare_sample(
    panel: pd.DataFrame,
    formation: int,
    horizon: int,
) -> tuple[pd.DataFrame, int]:
    """构造某一组参数的逐日可用样本，并给出当天的横截面排名。

    参数：
        panel: 信号面板。
        formation: 形成期天数，用于取排序信号列。
        horizon: 持有期天数，用于取未来收益标签列。
    返回：
        (样本表, 被跳过的交易日数)。样本表含 date、code、排名分位三列左右的中间结果。

    做法：
        1. 只保留信号与未来收益都非空的记录；
        2. 统计每日样本数，少于下限的日期整体剔除；
        3. 每天独立按过去收益升序排名，得到 0 到 1 的分位。
    """

    past_column = f"past_{formation}d"
    future_column = f"future_{horizon}d"

    # 两列任一缺失都不要：缺未来收益无法计算，缺信号无法排序
    sample = panel[["date", "code", past_column, future_column]].dropna().copy()

    daily_count = sample.groupby("date")[past_column].size()
    skipped_days = int((daily_count < MIN_STOCKS_PER_DAY).sum())
    keep_dates = daily_count[daily_count >= MIN_STOCKS_PER_DAY].index
    sample = sample[sample["date"].isin(keep_dates)]

    # 每天独立排名：groupby("date") 保证分位只在同一天内比较；
    # method="first" 让同值按出现顺序断开，避免并列把分组边界撑宽。
    sample["rank_pct"] = sample.groupby("date")[past_column].rank(pct=True, method="first")
    return sample, skipped_days


def daily_spread(
    panel: pd.DataFrame,
    formation: int,
    horizon: int,
) -> tuple[pd.Series, int]:
    """计算某一组参数下的每日反转收益差（两端 30% 分组）。

    参数：
        panel: 信号面板。
        formation: 形成期天数，用于排序的信号列。
        horizon: 持有期天数，用于计算未来收益的标签列。
    返回：
        (每日收益差序列, 被跳过的交易日数)。

    做法：
        在逐日排名的基础上取最低 30% 与最高 30%，
        当日收益差 = 空头组均值 − 多头组均值。
    """

    future_column = f"future_{horizon}d"
    sample, skipped_days = prepare_sample(panel, formation, horizon)
    rank = sample["rank_pct"]

    # where 把不属于该组的股票置为缺失，随后按日均值自动忽略缺失
    loser_return = sample[future_column].where(rank <= LOWER_QUANTILE)
    winner_return = sample[future_column].where(rank > UPPER_QUANTILE)

    grouped = pd.DataFrame({
        "date": sample["date"],
        "空头组未来收益": loser_return,
        "多头组未来收益": winner_return,
    }).groupby("date").mean()

    spread = grouped["空头组未来收益"] - grouped["多头组未来收益"]

    # 正常情况下有效日期都有两端分组；留 dropna 兜底，防止空组产生 NaN 混进 t 值
    return spread.dropna(), skipped_days


def daily_quintile_returns(
    panel: pd.DataFrame,
    formation: int,
    horizon: int,
) -> tuple[pd.DataFrame, int]:
    """计算某一组参数下每日 Q1 到 Q5 的未来收益均值。

    参数：
        panel: 信号面板。
        formation: 形成期天数。
        horizon: 持有期天数。
    返回：
        (宽表, 被跳过的交易日数)。宽表以日期为索引，列为 Q1（过去收益最低）到 Q5（最高）。

    做法：
        把每日分位排名乘以组数后向上取整，得到 1..5 的组号。
        rank 为 1.0 的股票落在第 5 组；由于用的是 method="first"，各组约各占 20%。
    """

    future_column = f"future_{horizon}d"
    sample, skipped_days = prepare_sample(panel, formation, horizon)

    # ceil 之后做裁剪，防止浮点误差把 rank=1.0 映射到 6
    group_id = np.ceil(sample["rank_pct"] * QUANTILE_GROUPS)
    group_id = group_id.clip(1, QUANTILE_GROUPS).astype(int)

    wide = (
        sample.assign(分组=group_id)
        .groupby(["date", "分组"])[future_column]
        .mean()
        .unstack("分组")
        .reindex(columns=range(1, QUANTILE_GROUPS + 1))
    )
    wide.columns = [f"Q{int(group)}" for group in wide.columns]
    return wide, skipped_days


def mean_std_t(series: pd.Series) -> tuple[int, float, float, float]:
    """计算样本数、均值、样本标准差与单样本 t 值。

    参数：
        series: 按日期排列的收益或收益差序列。
    返回：
        (有效样本数, 均值, 标准差, t 值)；样本不足时均值和 t 值返回 NaN。
    """

    values = series.dropna()
    valid_days = len(values)
    mean_value = float(values.mean()) if valid_days else np.nan
    std_value = float(values.std(ddof=1)) if valid_days >= 2 else np.nan

    if valid_days >= 2 and std_value > 0:
        t_value = mean_value / (std_value / np.sqrt(valid_days))
    else:
        t_value = np.nan  # 样本不足或序列恒定，t 值无定义

    return valid_days, mean_value, std_value, t_value


def summarize_spread(
    spread: pd.Series,
    skipped_days: int,
    formation: int,
    horizon: int,
) -> dict:
    """把每日收益差序列汇总成一行统计结果。

    参数：
        spread: 每日收益差序列。
        skipped_days: 因样本不足被跳过的交易日数。
        formation: 形成期天数，用于中文表头。
        horizon: 持有期天数，用于中文表头。
    返回：
        单组参数的中文统计字典。

    说明：
        t 值的分母用“每日收益差的标准差 / √有效天数”，即对时间序列做单样本检验。
        这里尚未修正持有期重叠（2 日、5 日持有期的样本自相关），
        得到的 t 值只能看作上界，后续需用 HAC 或按日期聚类 Bootstrap 复核。
    """

    valid_days, mean_spread, std_spread, t_value = mean_std_t(spread)
    win_rate = (spread.dropna() > 0).mean() if valid_days else np.nan

    return {
        "形成期": f"{formation} 日",
        "持有期": f"{horizon} 日",
        "有效交易日数": valid_days,
        "跳过交易日数": skipped_days,
        "日均收益差": round(mean_spread, 6),
        "收益差标准差": round(std_spread, 6),
        "t值": f"{t_value:.3f}" if np.isfinite(t_value) else "—",
        "正收益天数占比": f"{win_rate * 100:.2f}%" if valid_days else "—",
    }


def summarize_quintile(
    series: pd.Series,
    skipped_days: int,
    formation: int,
    horizon: int,
    label: str,
) -> dict:
    """把某一分组的每日未来收益序列汇总成一行。

    参数：
        series: 该分组的每日未来收益均值序列。
        skipped_days: 因样本不足被跳过的交易日数。
        formation: 形成期天数。
        horizon: 持有期天数。
        label: 分组名，如 Q1。
    返回：
        五分组明细表的一行。
    """

    valid_days, mean_value, std_value, t_value = mean_std_t(series)

    return {
        "形成期": f"{formation} 日",
        "持有期": f"{horizon} 日",
        "分组": label,
        "有效交易日数": valid_days,
        "跳过交易日数": skipped_days,
        "日均未来收益": round(mean_value, 6),
        "收益标准差": round(std_value, 6),
        "t值": f"{t_value:.3f}" if np.isfinite(t_value) else "—",
    }


def summarize_monotonicity(
    wide: pd.DataFrame,
    skipped_days: int,
    formation: int,
    horizon: int,
) -> dict:
    """检查 Q1 到 Q5 的日均收益是否单调，以及 Q1−Q5 的显著性。

    参数：
        wide: daily_quintile_returns 返回的宽表，列为 Q1..Q5。
        skipped_days: 因样本不足被跳过的交易日数。
        formation: 形成期天数。
        horizon: 持有期天数。
    返回：
        单调性汇总表的一行。

    说明：
        秩相关系数衡量“组序号越大、收益越高”的单调程度。
        Q1−Q5 与两端 30% 的收益差方向一致，但分组更细，可用来判断极端组是否拖累结果。
    """

    columns = list(wide.columns)
    group_means = pd.Series([wide[column].mean() for column in columns])
    group_index = pd.Series(range(1, len(columns) + 1))

    # 五组均值恒定或缺失时，秩相关无定义，避免除零和误导性数值
    if group_means.notna().all() and group_means.nunique() > 1:
        rank_correlation = float(group_means.corr(group_index, method="spearman"))
    else:
        rank_correlation = np.nan

    if not np.isfinite(rank_correlation):
        direction = "无法判断"
    elif rank_correlation <= -MONOTONIC_CORRELATION:
        direction = "单调递减（反转）"
    elif rank_correlation >= MONOTONIC_CORRELATION:
        direction = "单调递增（动量）"
    else:
        direction = "无序"

    # 同一天的 Q1−Q5 才是配对差；先按日相减，再对时间序列做检验
    quintile_spread = wide["Q1"] - wide["Q5"]
    valid_days, mean_spread, _, t_value = mean_std_t(quintile_spread)

    row = {
        "形成期": f"{formation} 日",
        "持有期": f"{horizon} 日",
        "有效交易日数": valid_days,
        "跳过交易日数": skipped_days,
    }
    for column, mean_value in zip(columns, group_means):
        row[f"{column}日均收益"] = round(float(mean_value), 6)

    row.update({
        "Q1减Q5": round(mean_spread, 6),
        "t值(Q1减Q5)": f"{t_value:.3f}" if np.isfinite(t_value) else "—",
        "秩相关系数": f"{rank_correlation:.3f}" if np.isfinite(rank_correlation) else "—",
        "单调方向": direction,
    })
    return row


def yearly_segments(panel: pd.DataFrame) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """按自然年切分样本区间，并在最前面加上全样本。

    参数：
        panel: 信号面板。
    返回：
        [(区间名, 开始日期, 结束日期), ...]，先全样本、后各年份升序。

    说明：
        起止日期取该区间内实际有数据的首尾交易日，不用 1 月 1 日和 12 月 31 日，
        避免把节假日当成缺失样本。
    """

    segments = [("全样本", panel["date"].min(), panel["date"].max())]
    for year, group in panel.groupby(panel["date"].dt.year, sort=True):
        segments.append((f"{year} 年", group["date"].min(), group["date"].max()))
    return segments


def main() -> None:
    """跑完 9 组参数的两端分组、五分组与分年度稳定性，打印并落盘。"""

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    panel = load_signals(INPUT_FILE)

    print(f"数据总行数：{len(panel):,}")
    print(f"股票数量：{panel['code'].nunique():,}")
    print(f"交易日期数量：{panel['date'].nunique():,}")
    print(f"日期范围：{panel['date'].min():%Y-%m-%d} 至 {panel['date'].max():%Y-%m-%d}")

    # 以 past_1d 非空为基准，给出每日样本量的均值与最小值，作为跳过判定的参考
    daily_count = panel.dropna(subset=["past_1d"]).groupby("date").size()
    print(
        f"每日有效股票数（基准 past_1d）：均值 {daily_count.mean():.1f} 只，"
        f"最少 {daily_count.min()} 只"
    )

    spread_rows = []
    quintile_rows = []
    monotonicity_rows = []

    for formation in FORMATION_DAYS:
        for horizon in FORWARD_DAYS:
            spread, skipped_days = daily_spread(panel, formation, horizon)
            spread_rows.append(summarize_spread(spread, skipped_days, formation, horizon))

            wide, skipped_days = daily_quintile_returns(panel, formation, horizon)
            for label in wide.columns:
                quintile_rows.append(
                    summarize_quintile(
                        wide[label], skipped_days, formation, horizon, label
                    )
                )
            monotonicity_rows.append(
                summarize_monotonicity(wide, skipped_days, formation, horizon)
            )

    # 分年度稳定性：只跑 SUBSAMPLE_PAIRS 里的两组关键参数，逐年加全样本对照
    stability_rows = []
    segments = yearly_segments(panel)
    for formation, horizon in SUBSAMPLE_PAIRS:
        for label, start_date, end_date in segments:
            subset = panel[(panel["date"] >= start_date) & (panel["date"] <= end_date)]
            spread, skipped_days = daily_spread(subset, formation, horizon)
            stability_rows.append({
                "区间": label,
                **summarize_spread(spread, skipped_days, formation, horizon),
            })

    summary = pd.DataFrame(spread_rows)
    quintile_summary = pd.DataFrame(quintile_rows)
    monotonicity = pd.DataFrame(monotonicity_rows)
    stability = pd.DataFrame(stability_rows)

    # 统一按 6 位小数落盘，避免 1e-06 这类科学计数法出现在报告表里
    summary.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig", float_format="%.6f")
    quintile_summary.to_csv(
        QUINTILE_FILE, index=False, encoding="utf-8-sig", float_format="%.6f"
    )
    monotonicity.to_csv(
        MONOTONICITY_FILE, index=False, encoding="utf-8-sig", float_format="%.6f"
    )
    stability.to_csv(
        SUBSAMPLE_FILE, index=False, encoding="utf-8-sig", float_format="%.6f"
    )

    print("\n" + "=" * 90)
    print("逐日横截面反转统计（空头组 − 多头组，单位为小数收益）")
    print("=" * 90)
    print(summary.to_string(index=False))

    print("\n" + "=" * 90)
    print("五分组单调性（Q1 过去收益最低，Q5 最高）")
    print("=" * 90)
    print(monotonicity.to_string(index=False))

    print("\n" + "=" * 90)
    print("分年度稳定性（空头组 − 多头组，按信号日期归属年份）")
    print("=" * 90)
    print(stability.to_string(index=False))

    print(f"\n两端统计表已保存：{SUMMARY_FILE}")
    print(f"五分组明细已保存：{QUINTILE_FILE}")
    print(f"单调性汇总已保存：{MONOTONICITY_FILE}")
    print(f"分年度稳定性已保存：{SUBSAMPLE_FILE}")


if __name__ == "__main__":
    main()
