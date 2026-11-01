# A 股短期反转效应研究（EQ-MR-01）

用后复权日线检验 A 股是否存在可交易的短期反转效应：形成期 1/5/20 日，持有期 1/2/5 日，逐日横截面分组，最后看扣掉成本后还剩多少。

## 数据

| 项目 | 设定 |
|---|---|
| 股票池 | 沪深 300 + 中证 500 成分，去重 800 只（当前时点快照） |
| 样本期 | 2020-01-02 至 2025-12-31 |
| 可用股票 | 799 只（1 只上市晚于区间，无数据） |
| 频率 / 复权 | 日线 / 后复权 |
| 数据源 | BaoStock |

面板 1,086,970 行、1,455 个交易日。数据说明见 [数据底座报告](docs/reports/data_base_2026-11-01.md) 与 [数据质量报告](docs/reports/data_quality_v2.md)。

## 主流程

```powershell
# 1. 下载（支持断点续传，已下载的股票自动跳过）
python src/download_data.py --source index800 --start-date 2020-01-01 --end-date 2025-12-31

# 2. 合并面板：统一字段、剔除非后复权行、修复复权因子跳变、计算日收益
python src/build_panel.py

# 3. 复权交叉验证（抽样比对新浪后复权收益）
python src/validate_adjustment.py

# 4. 数据质量审计（按年覆盖、涨跌停、停牌连续性）
python src/audit_quality.py

# 5. 信号：过去 1/5/20 日与未来 1/2/5 日复利收益
python src/build_signals.py

# 6. 测试
python -m pytest tests -q
```

## 目录

```text
├── configs/baseline.yaml      研究参数
├── data/raw/baostock/         每只股票一个 parquet
├── data/processed/            面板与信号
├── data/manifests/            下载日志
├── src/                       主线脚本
├── tests/                     结构测试与未来函数测试
├── results/tables/            可追溯的结果表
└── docs/                      项目记忆、简历素材、数据报告
```

## 当前状态

已完成：800 只下载、后复权面板、复权校验（10 只抽样全部通过）、数据质量审计、信号重算、17 项测试。

待完成：逐日横截面统计（`src/evaluate_cross_section.py`）、HAC / 按日期聚类 Bootstrap、组合与成本、样本外检验、报告。

## 结论边界

目前没有任何“反转有效”的结论。前期 5 只股票、未复权的结果已随脚本一起删除，不再引用。正式结论要等全样本统计、成本与样本外跑完。

## 已知限制

1. 股票池是当前指数成分快照，样本期内退市或调出的股票不在其中，存在幸存者偏差。
2. 样本期只有 2020–2025，不能外推到更早的市场结构。
3. 涨跌停判定为近似规则，回测成交假设仍需细化。

## 文档

| 文档 | 内容 |
|---|---|
| [PROJECT_MEMORY.md](docs/PROJECT_MEMORY.md) | 决策记录、研究口径、当前状态、技术债 |
| [RESUME.md](docs/RESUME.md) | 简历 bullet 与面试素材 |
| [数据底座报告](docs/reports/data_base_2026-11-01.md) | 下载覆盖、复权校验、修复记录 |
| [数据质量报告](docs/reports/data_quality_v2.md) | 按年覆盖、停牌、涨跌停 |
