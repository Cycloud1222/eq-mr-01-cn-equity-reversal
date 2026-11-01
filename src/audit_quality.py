"""T1.4 数据质量审计：按年覆盖率、涨跌停、停牌连续性、上市天数。

用法：
    python src/audit_quality.py

输入：
    data/processed/daily_panel.parquet

输出：
    results/tables/data_quality_v2.csv       按年质量指标（中文表头）
    results/tables/long_suspensions.csv      连续停牌 ≥20 个交易日的段清单

设计说明：
1. 涨跌停按板块规则近似判定：科创板、CDR、创业板 20%，主板 10%，ST 5%，
   新股上市前 5 个交易日不设限。规则是近似值，统计结果只作数量级参考。
2. 停牌用 tradestatus=0 判定，连续段按交易日计数；长期停牌会直接影响
   信号形成期与未来收益的可计算性，所以单独列表。
3. 只统计不改数据；清洗规则写在报告里，避免“悄悄处理”。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_FILE = PROJECT_ROOT / "data" / "processed" / "daily_panel.parquet"
RESULT_DIR = PROJECT_ROOT / "results" / "tables"
YEARLY_FILE = RESULT_DIR / "data_quality_v2.csv"
SUSPENSION_FILE = RESULT_DIR / "long_suspensions.csv"

# 涨跌停判定容差：接口涨跌幅保留 4 位小数，留 0.5 个百分点避免边界误判。
LIMIT_TOLERANCE = 0.005

# 连续停牌达到这个交易日数就单独列出，便于检查对信号的影响。
LONG_SUSPENSION_DAYS = 20


def limit_by_board(panel: pd.DataFrame) -> np.ndarray:
    """按板块和 ST 状态给出每日涨跌幅上限。

    参数：
        panel: 含 code、date、isST 的面板。
    返回：
        与面板等长的上限数组，单位为小数（0.20 表示 20%）。

    规则：
        科创板 688/689 与创业板 300/301/302 为 20%；
        创业板在 2020-08-24 注册制改革后才是 20%，之前仍是 10%；
        主板 10%；ST 股票 5%（近似处理，不区分板块细则）。
    """
    code = panel["code"].astype(str)
    star = code.str.startswith(("sh.688", "sh.689"))
    chinext = code.str.startswith(("sz.300", "sz.301", "sz.302")) & (
        panel["date"] >= pd.Timestamp("2020-08-24")
    )

    limit = np.where(star | chinext, 0.20, 0.10)
    limit = np.where(panel["isST"] == 1, 0.05, limit)
    return limit


def mark_limits(panel: pd.DataFrame) -> pd.DataFrame:
    """标记涨停、跌停、涨跌停豁免（新股前 5 日）。

    参数：
        panel: 已按代码和日期排序的面板。
    返回：
        新增“涨跌幅上限”“样本序号”“涨停”“跌停”四列的面板。
    """
    panel = panel.copy()
    panel["样本序号"] = panel.groupby("code", sort=False).cumcount()
    panel["涨跌幅上限"] = limit_by_board(panel)

    limit = panel["涨跌幅上限"]
    pct = panel["pct_change"]

    # 新股上市前 5 个交易日没有涨跌幅限制，不参与判定。
    free = panel["样本序号"] < 5
    panel["涨停"] = (~free) & (pct >= limit - LIMIT_TOLERANCE)
    panel["跌停"] = (~free) & (pct <= -limit + LIMIT_TOLERANCE)
    return panel


def yearly_metrics(panel: pd.DataFrame) -> pd.DataFrame:
    """按年统计覆盖率与异常数量。

    参数：
        panel: 已标记涨跌停的面板。
    返回：
        每年一行、中文表头的指标表。
    """
    panel = panel.copy()
    panel["年份"] = panel["date"].dt.year
    panel["成交量为零"] = panel["volume"].fillna(0) == 0
    panel["涨跌幅缺失"] = panel["pct_change"].isna()

    rows = []
    for year, group in panel.groupby("年份", sort=True):
        rows.append({
            "年份": int(year),
            "交易日数": int(group["date"].nunique()),
            "有记录股票数": int(group["code"].nunique()),
            "平均每日股票数": round(group.groupby("date")["code"].count().mean(), 1),
            "记录数": int(len(group)),
            "停牌记录数": int((group["tradestatus"] == 0).sum()),
            "停牌占比": f"{(group['tradestatus'] == 0).mean() * 100:.2f}%",
            "涨停记录数": int(group["涨停"].sum()),
            "跌停记录数": int(group["跌停"].sum()),
            "ST记录数": int((group["isST"] == 1).sum()),
            "成交量为零记录数": int(group["成交量为零"].sum()),
            "涨跌幅缺失记录数": int(group["涨跌幅缺失"].sum()),
        })
    return pd.DataFrame(rows)


def suspension_runs(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """统计每只股票的最长连续停牌，并列出长期停牌段。

    参数：
        panel: 已排序面板，至少含 code、date、tradestatus。
    返回：
        (每只股票停牌概况, 连续停牌 ≥20 个交易日的段清单)
    """
    records = []
    runs = []

    # 逐只股票做游程统计：把 tradestatus 连续相同的行合并成一段。
    for code, group in panel.groupby("code", sort=False):
        status = (group["tradestatus"] == 0).to_numpy()
        if not status.any():
            records.append({
                "股票代码": code,
                "最长连续停牌": 0,
                "停牌段数": 0,
            })
            continue

        lengths = []
        run_start = 0
        for index in range(1, len(status) + 1):
            if index == len(status) or status[index] != status[run_start]:
                if status[run_start]:
                    length = index - run_start
                    lengths.append(length)
                    runs.append({
                        "股票代码": code,
                        "起始日期": group["date"].iloc[run_start].strftime("%Y-%m-%d"),
                        "结束日期": group["date"].iloc[index - 1].strftime("%Y-%m-%d"),
                        "连续交易日数": length,
                    })
                run_start = index

        records.append({
            "股票代码": code,
            "最长连续停牌": max(lengths),
            "停牌段数": len(lengths),
        })

    summary = pd.DataFrame(records)
    long_runs = pd.DataFrame(runs)
    if not long_runs.empty:
        long_runs = long_runs.loc[long_runs["连续交易日数"] >= LONG_SUSPENSION_DAYS]
        long_runs = long_runs.sort_values("连续交易日数", ascending=False)

    return summary, long_runs


def main() -> None:
    """执行审计并落盘。"""
    pd.set_option("display.unicode.east_asian_width", True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    panel = pd.read_parquet(PANEL_FILE)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    print(f"面板读取完成：{len(panel):,} 行，{panel['code'].nunique()} 只股票")

    panel = mark_limits(panel)
    yearly = yearly_metrics(panel)
    yearly.to_csv(YEARLY_FILE, index=False, encoding="utf-8-sig")

    suspension, long_runs = suspension_runs(panel)
    if not long_runs.empty:
        long_runs.to_csv(SUSPENSION_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 100)
    print("按年质量指标")
    print("=" * 100)
    print(yearly.to_string(index=False))

    print("\n停牌概况")
    print(f"出现过停牌的股票：{int((suspension['停牌段数'] > 0).sum())} 只")
    print(f"停牌段数合计：{int(suspension['停牌段数'].sum())} 段")
    print(
        f"连续停牌 ≥{LONG_SUSPENSION_DAYS} 个交易日的段："
        f"{len(long_runs)} 段，涉及 {long_runs['股票代码'].nunique() if not long_runs.empty else 0} 只"
    )
    if not long_runs.empty:
        print("\n最长 10 段：")
        print(long_runs.head(10).to_string(index=False))
        print(f"\n长期停牌清单已保存：{SUSPENSION_FILE}")

    print(f"\n按年指标已保存：{YEARLY_FILE}")


if __name__ == "__main__":
    main()
