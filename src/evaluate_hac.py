"""T2.3：用 Newey–West/HAC 标准误复核横截面反转收益差。

用法：
    python src/evaluate_cross_section.py
    python src/evaluate_hac.py

输入：
    results/tables/cross_section_daily_detail.csv

输出：
    results/tables/hac_robustness_summary.csv

研究口径：
    1. 一个观测值是「一个形成日的输家组未来平均收益 − 赢家组未来平均收益」。
       不把每天的个股收益直接堆在一起做 t 检验。
    2. 用常数项 OLS 估计该时间序列均值，再用 Bartlett 权重的 HAC 标准误
       计算 t 值、双侧 p 值及 95% 置信区间。
    3. 主设定 maxlags = max(0, 持有期 - 1)，另外报告 5、10 两种滞后阶数。
       这些滞后数是敏感性检验，不保证足以覆盖全部序列相关。
    4. 不得删除内部缺失日后假装时间仍连续。本模块在内部日期有缺口时
       拒绝计算，要求先核查缺失机制并另行选择缺口处理方法。
    5. HAC 只修正均值推断的标准误；无法修复当前指数成分样本的幸存者偏差，
       也不能替代交易成本、组合可执行性和多重检验控制。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = PROJECT_ROOT / "results" / "tables" / "cross_section_daily_detail.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "tables" / "hac_robustness_summary.csv"

FORMATION_DAYS = (1, 5, 20)
FORWARD_DAYS = (1, 2, 5)
SENSITIVITY_LAGS = (5, 10)
MIN_OBSERVATIONS = 3
ALPHA = 0.05


def load_daily_detail(file_path: Path) -> pd.DataFrame:
    """读取逐日收益差，并验证一日一组参数只保留一条记录。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"找不到逐日明细：{file_path}。请先运行 src/evaluate_cross_section.py"
        )

    detail = pd.read_csv(file_path, encoding="utf-8-sig")

    required_columns = {"交易日期", "形成期", "持有期", "反转收益差"}
    missing_columns = required_columns - set(detail.columns)
    if missing_columns:
        raise ValueError(f"逐日明细缺少必要字段：{sorted(missing_columns)}")

    # 日期和参数是研究主键：不允许用无效日期或重复行参与 HAC 估计。
    detail["交易日期"] = pd.to_datetime(detail["交易日期"], errors="raise")
    if detail["交易日期"].isna().any():
        raise ValueError("逐日明细包含空交易日期")

    for column in ("形成期", "持有期"):
        detail[column] = pd.to_numeric(detail[column], errors="raise")
        if detail[column].isna().any():
            raise ValueError(f"{column} 包含缺失值")
        if not (detail[column] == detail[column].astype(int)).all():
            raise ValueError(f"{column} 必须是整数")
        detail[column] = detail[column].astype(int)

    detail["反转收益差"] = (
        pd.to_numeric(detail["反转收益差"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
    )

    if detail.duplicated(subset=["交易日期", "形成期", "持有期"]).any():
        raise ValueError("同一交易日期、形成期和持有期出现了重复记录")

    expected_pairs = {
        (formation, horizon)
        for formation in FORMATION_DAYS
        for horizon in FORWARD_DAYS
    }
    actual_pairs = set(zip(detail["形成期"], detail["持有期"]))
    if actual_pairs != expected_pairs:
        raise ValueError(
            f"参数组合不完整或出现意外组合；缺少 {sorted(expected_pairs - actual_pairs)}，"
            f"多余 {sorted(actual_pairs - expected_pairs)}"
        )

    return detail.sort_values(["形成期", "持有期", "交易日期"]).reset_index(drop=True)


def check_internal_gaps(group: pd.DataFrame, formation: int, horizon: int) -> None:
    """拒绝内部缺失日，避免把非相邻的交易日误认作 HAC 的相邻滞后。

    允许样本最前端因形成期不足而缺失，以及最末端因未来窗口不足而缺失；
    一旦首个与最后一个有效日之间存在无效评价日，应先单独分析缺失原因。
    """
    observed = group["反转收益差"].notna().to_numpy()
    valid_indices = np.flatnonzero(observed)
    if not len(valid_indices):
        return

    start, end = int(valid_indices[0]), int(valid_indices[-1])
    missing_indices = np.flatnonzero(~observed[start : end + 1]) + start
    if len(missing_indices):
        dates = group.iloc[missing_indices]["交易日期"].dt.strftime("%Y-%m-%d")
        examples = "、".join(dates.head(5))
        raise ValueError(
            f"形成期 {formation} 日、持有期 {horizon} 日在有效区间内部"
            f"有 {len(missing_indices)} 个缺失交易日（例如 {examples}）。"
            "不能简单删除后套用按相邻交易日计算的 HAC；请先核查缺口。"
        )


def estimate_mean_hac(series: pd.Series, maxlags: int) -> dict[str, float]:
    """使用常数项 OLS + HAC 协方差估计均值的标准误和显著性。

    参数：
        series：按真实交易日排序且内部无缺失的收益差序列。
        maxlags：Bartlett 核的最大自相关滞后阶数，0 表示只使用零阶协方差。

    返回：
        日均收益差、普通 t 值、HAC 标准误、HAC t 值、双侧 p 值与
        正态近似 95% 置信区间。数值不足或方差为零时，推断字段返回 NaN。

    说明：
        use_correction=False 与手算 Newey–West 长期方差使用 n 为分母一致。
        use_t=False 使用大样本正态近似，不把 HAC t 值解释为有限样本精确 t 分布。
    """
    if not isinstance(maxlags, int) or maxlags < 0:
        raise ValueError("maxlags 必须为非负整数")

    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("HAC 输入序列不得包含空值或非有限数值")

    n = len(values)
    mean_value = float(values.mean()) if n else np.nan
    std_value = float(values.std(ddof=1)) if n >= 2 else np.nan
    ordinary_t = (
        mean_value / (std_value / np.sqrt(n))
        if n >= 2 and np.isfinite(std_value) and std_value > 1e-14
        else np.nan
    )

    result = {
        "日均收益差": mean_value,
        "普通t值": ordinary_t,
        "HAC标准误": np.nan,
        "HAC t值": np.nan,
        "HAC双侧p值": np.nan,
        "HAC置信区间下限": np.nan,
        "HAC置信区间上限": np.nan,
    }

    if n < MIN_OBSERVATIONS or maxlags >= n or not np.isfinite(ordinary_t):
        return result

    # 回归模型只有一个常数项，因此系数就是每日收益差的样本均值。
    design = np.ones((n, 1), dtype=float)
    model = sm.OLS(values, design).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": maxlags, "use_correction": False},
        use_t=False,
    )

    se = float(model.bse[0])
    if not np.isfinite(se) or se <= 0:
        return result

    interval = model.conf_int(alpha=ALPHA)[0]
    result.update({
        "日均收益差": float(model.params[0]),
        "HAC标准误": se,
        "HAC t值": float(model.tvalues[0]),
        "HAC双侧p值": float(model.pvalues[0]),
        "HAC置信区间下限": float(interval[0]),
        "HAC置信区间上限": float(interval[1]),
    })
    return result


def analyze_hac(detail: pd.DataFrame) -> pd.DataFrame:
    """对全部九组参数分别估计三种 HAC 滞后阶数，共输出 27 行。"""
    rows = []
    for formation in FORMATION_DAYS:
        for horizon in FORWARD_DAYS:
            group = detail.loc[
                (detail["形成期"] == formation) & (detail["持有期"] == horizon)
            ].sort_values("交易日期")

            check_internal_gaps(group, formation, horizon)

            valid = group["反转收益差"].dropna()
            n = len(valid)
            skipped = len(group) - n

            for label, maxlags in (
                ("主设定：持有期减一", max(0, horizon - 1)),
                ("敏感性：滞后五阶", SENSITIVITY_LAGS[0]),
                ("敏感性：滞后十阶", SENSITIVITY_LAGS[1]),
            ):
                metrics = estimate_mean_hac(valid, maxlags)
                rows.append({
                    "形成期": formation,
                    "持有期": horizon,
                    "HAC设定": label,
                    "滞后阶数": maxlags,
                    "有效交易日数": n,
                    "跳过交易日数": skipped,
                    **metrics,
                })

    return pd.DataFrame(rows)


def main() -> None:
    """计算 HAC 结果，保存中文表头 CSV，终端展示主设定。"""
    detail = load_daily_detail(INPUT_FILE)
    summary = analyze_hac(detail)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 数值列保留小数形式，以便程序继续计算；终端报告另行显示百分比。
    summary.to_csv(
        OUTPUT_FILE, index=False, encoding="utf-8-sig", float_format="%.9f"
    )

    primary = summary.loc[
        summary["HAC设定"] == "主设定：持有期减一"
    ].copy()
    primary["日均收益差"] = primary["日均收益差"].map(
        lambda value: f"{value * 100:+.4f}%" if pd.notna(value) else "—"
    )
    for column in ("普通t值", "HAC t值"):
        primary[column] = primary[column].map(
            lambda value: f"{value:+.3f}" if pd.notna(value) else "—"
        )

    print("\n短期反转收益差 HAC 稳健性检验（主设定）")
    print(primary[
        ["形成期", "持有期", "滞后阶数", "有效交易日数",
         "日均收益差", "普通t值", "HAC t值"]
    ].to_string(index=False))
    print("\n注意：表内不是组合可交易收益，HAC 不修正历史股票池偏差或交易成本。")
    print(f"\n结果已保存：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
