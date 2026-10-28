"""初版跨日混池统计（已废弃，仅保留作方法对照）。

这个脚本把所有交易日的数据混在一起做 30%/70% 分位分组。
问题在于：不同日期的市场整体收益水平不同，混池后某几天的极端行情
会主导分组结果，同时把同一天的横截面相关误当成独立样本，
最终导致标准误被低估、显著性被高估。

正式结果请使用 src/evaluate_reversal_daily.py 的逐日横截面口径。
本脚本生成的 reveral_summary.csv 不得写入研究报告、简历或面试材料。
"""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "processed" / "reversal_signals_raw.csv"
OUTPUT_DIR = ROOT / "results" / "tables"

FORMATION_PERIODS = [1, 5, 20]
FORWARD_HORIZONS = [1, 2, 5]

def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"未找到标准化行情文件：{INPUT_FILE}\n"
            "请先运行：python src/normalize_data.py"
        )
    df = pd.read_csv(INPUT_FILE, dtype={"ts_code": "string"}, parse_dates=["trade_date"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ROWS = []

    for formation in FORMATION_PERIODS:
        past_col = f"past_{formation}d_raw"

        
        for horizon in FORWARD_HORIZONS:
            future_col = f"future_{horizon}d_raw"

            sample = df[[past_col, future_col]].dropna().copy()
            
            if sample.empty:
                continue

            #负收益组：形成期收益率最低的30%股票
            #正收益组：形成期收益率最高的30%股票
            low_cut = sample[past_col].quantile(0.3)
            high_cut = sample[past_col].quantile(0.7)
            loser = sample.loc[
                sample[past_col] <= low_cut, future_col
            ]
            winner = sample.loc[
                sample[past_col] >= high_cut, future_col
            ]
            loser_mean = loser.mean()
            winner_mean = winner.mean()

            ROWS.append({
                "formation_period": formation,
                "forward_horizon": horizon,
                "sample_size": len(sample),
                "loser_n": len(loser),
                "winner_n": len(winner),
                "loser_mean_return": loser_mean,
                "winner_mean_return": winner_mean,
                "reversal_return": loser_mean - winner_mean,
                "loser_median_return": loser.median(),
                "winner_median_return": winner.median(),
                })

    result_df = pd.DataFrame(ROWS)
    output_file = OUTPUT_DIR / "reveral_summary.csv"
    result_df.to_csv(output_file,index=False,encoding="utf-8-sig")
    

if __name__ == "__main__":
    main()        