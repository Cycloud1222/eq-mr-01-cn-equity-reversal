# A 股短期反转效应研究（EQ-MR-01）

用后复权日线检验 A 股是否存在可交易的短期反转效应：形成期 1/5/20 日，持有期 1/2/5 日，逐日横截面分组，最后看扣掉成本后还剩多少。

## 数据

| 项目 | 设定 |
|---|---|
| 股票池 | 沪深 300 + 中证 500 成分，去重 800 只（当前时点快照） |
| 样本期 | 2020-01-02 至 2025-12-31 |
| 可用股票 | 799 只（1 只上市晚于区间，无数据） |
| 频率 / 复权 | 日线 / 后复权 |
| 主行情数据源 | BaoStock |

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

# 6. 逐日横截面统计：两端 30%、五分组、分年度稳定性
python src/evaluate_cross_section.py

# 7. HAC 稳健性检验（需先完成步骤 6 生成逐日明细）
python src/evaluate_hac.py

# 8. 测试
python -m pytest tests -q
```

## 目录

```text
├── configs/baseline.yaml      研究参数
├── data/raw/baostock/         每只股票一个 parquet
├── data/raw/neutralization/   行业快照与每日市值（本地数据，不进 Git）
├── data/processed/            面板与信号
├── data/manifests/            下载日志
├── src/                       主线脚本
├── tests/                     结构测试与未来函数测试
├── results/tables/            可追溯的结果表
└── docs/                      项目记忆、数据报告
```

## 当前状态

已完成：800 只下载、后复权面板、复权校验、数据质量审计、信号重算、逐日横截面统计、五分组单调性检查及分年度稳定性。HAC 统计代码及独立测试已加入审查分支，GitHub 自动化测试已通过；HAC 的 27 行统计结果已根据本地输出归档。

结果：在当前样本与口径下**没有发现明确短期反转证据**。修复后的九组普通 t 值约在 -0.891 到 +0.192 之间；这些值仍需用 HAC 与其他稳健性检验复核。

待完成：完善历史动态股票池、行业与市值中性化、交易成本与严格样本外检验。已有横截面结果见 [横截面检验报告](docs/reports/cross_section_2026-11-05.md)；HAC 复核见 [HAC 检验结果报告](docs/reports/hac_results_2026-11-05.md)。

### 行业与市值中性化：数据准备

无需 Tushare Pro。数据下载脚本使用两类免费来源：

- 行业：BaoStock `query_stock_industry(date=...)`，按每周最后一个交易日请求历史分类，并保留接口返回的 `updateDate` 作为实际生效日期。
- 规模：AKShare `stock_zh_a_hist_163`，读取每日总市值和流通市值；后续以 `log(流通市值)` 作为规模暴露变量。

先用少量股票和周度快照试跑：

```powershell
python src/download_neutralization_data.py --limit 3 --industry-limit 3
```

确认接口可用后，下载全样本；脚本会复用已有缓存：

```powershell
python src/download_neutralization_data.py
```

若要重新获取数据：

```powershell
python src/download_neutralization_data.py --force
```

输出保存在 `data/raw/neutralization/`，不修改原始日线、信号面板或已有研究结果。行业分类按实际 `updateDate` 向前匹配，禁止把未来分类填回历史。市值来源为未复权历史行情中的每日市值字段，不用后复权收盘价乘当前股本。当前新增内容只负责数据采集；中性化计算与结果解释需在数据下载完成后进行。

## 结论边界

目前能说的是：2020–2025 年、沪深 300 + 中证 500 成分股、日线后复权，在 1/5/20 日形成期与 1/2/5 日持有期下，逐日横截面 30%/70% 与五分组都没有显著反转差。

不能说的是“A 股不存在短期反转”，也不能说“扣费后消失”——后者需要组合回测与成本模型。前期 5 只股票、未复权的结果已随脚本删除，不再引用。

## 已知限制

1. 股票池是当前指数成分快照，样本期内退市或调出的股票不在其中，存在幸存者偏差。
2. 样本期只有 2020–2025，且只覆盖大市值股票，不能外推到更早或更小市值的市场结构。
3. 2 日、5 日持有期存在重叠，原有普通 t 值未做 HAC 修正；它不是 HAC t 值的上界，不能预判修正方向。
4. 涨跌停判定为近似规则，回测成交假设仍需细化。

## 文档

| 文档 | 内容 |
|---|---|
| [PROJECT_MEMORY.md](docs/PROJECT_MEMORY.md) | 决策记录、研究口径、当前状态、技术债 |
| [数据底座报告](docs/reports/data_base_2026-11-01.md) | 下载覆盖、复权校验、修复记录 |
| [数据质量报告](docs/reports/data_quality_v2.md) | 按年覆盖、停牌、涨跌停 |
| [横截面检验报告](docs/reports/cross_section_2026-11-05.md) | 9 组统计、五分组单调性、分年度稳定性 |

## T2.3：HAC 稳健性检验

主程序：`src/evaluate_hac.py`，读取上一阶段生成的 `results/tables/cross_section_daily_detail.csv`，对九组「形成期 × 评价期」依次报告三种 Newey–West/Bartlett 滞后阶数：`max(0, 持有期-1)`（主设定）、5、10。输出为 `results/tables/hac_robustness_summary.csv`（27 行、中文表头、保留数值列便于后续计算）。

```powershell
python src/evaluate_cross_section.py
python src/evaluate_hac.py
python -m pytest tests/test_hac.py -v
python -m pytest tests -q
```

请先运行测试并核对主设定与滞后 5/10 阶的差异，再提交生成表。代码会拒绝有效区间**内部缺失**的收益差序列，防止错误压缩时间间隔；它允许样本开头形成期不足、结尾未来评价期不足的缺失。

**统计边界：** HAC 只调整均值推断的标准误，不改变日均收益差，也不能修复幸存者偏差、遗漏未来收益的非随机选择、样本外验证缺失或不可交易约束。九个参数组合同时检验还需要控制多重比较。详情见 [HAC 方法说明](docs/reports/hac_methodology.md)。

### HAC 实际结果归档

已经根据本地完整逐日明细的输出归档 `results/tables/hac_robustness_summary.csv`（27 行）。全部 27 组的双侧 p 值均大于 0.05，95% HAC 置信区间均包含零；九组主设定中最小双侧 p 值约 0.373。该结果不支持当前样本存在稳健的非零平均短期反转收益差，但不证明其他股票池或其他市场环境不存在反转。

[查看完整 HAC 数值与研究边界](docs/reports/hac_results_2026-11-05.md)。合并此分支之前仍需审查最新 CI 状态以及本地执行日志。
