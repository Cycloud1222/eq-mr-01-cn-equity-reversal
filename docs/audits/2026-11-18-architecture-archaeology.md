# EQ-MR-01 架构考古与历史包袱审计

> **审计日期**：2026-11-18  
> **审计基线**：`main@9080bc32c769357508e1f08576cec5837f9dbb2a`  
> **审计范围**：`Cycloud1222/eq-mr-01-cn-equity-reversal` 的全部远端引用、提交、PR、文件增删与当前工作树  
> **判定基线**：同时按“冻结为作品集”和“继续研究”判断，避免把未来研究能力误判成包袱

## 0. 执行摘要

这不是一个长期演化后堆满死代码的仓库。它只有约三周历史，却经历了一次完整的研究方向重建、一次横截面未来函数修复、一次 HAC 补充检验和一次历史幸存者偏差审计。真正的问题不是“代码太多”，而是**研究阶段切换后，事实来源、结果血缘和接口边界没有同步收敛**。

本次清点得到：

- 全部引用共 **78 个唯一提交**，其中 `main` 可达 **27 个**，仅存在于非 `main` 分支的提交 **51 个**。
- **10 个 PR**：9 个已合并，PR #1 关闭未合并并被 PR #2 取代。
- **11 个分支**：`main` 加 10 个功能/清理分支；这 10 个分支的 tip SHA 已记入附录。
- 当前主线有 9 个 `src/` 模块、6 个测试文件、8 份 `docs/reports/` 报告、2 个 CI 工作流。
- 只有 1 个显式 merge commit（PR #2），其余合并主要以 squash/单提交方式落到 `main`。因此“PR 已合并”和“分支提交是 `main` 祖先”不是同一个概念。

最重要的结论：

1. **核心研究设计是有意为之，不应在“清理历史包袱”名义下删除。** 逐日横截面、形成日 point-in-time 排序、复权收益、停牌处理、未来函数测试、HAC 推断和数据质量审计，都对应真实且仍存在的统计风险。
2. **现存死代码很少，但有一处明确坏接口。** `download_data.py` 的 `--source universe` 依赖 `data/manifests/沪深A股股票池.csv`；该文件已在 `2d67e08` 删除，所以该来源当前必然失败。
3. **最严重的遗留不是代码，而是结果血缘。** `cross_section_summary.csv` 和横截面报告仍是 PR #2 修复未来标签泄漏之前的结果；HAC 表使用的是修复后的本地逐日明细。两者不能混用。
4. **历史审计代码大多已在 PR #9 主动删除，但文档没有同步。** 三份历史指数报告仍指向已删除的 `audit_historical_coverage.py`、`audit_historical_bars.py` 和已移除的 `--audit-only`。
5. **两个“未接入主流程”的采集器不是普通垃圾。** 历史指数采集器服务于幸存者偏差，行业/市值采集器服务于中性化；冻结作品集时可以归档，继续研究时应保留并补齐下游。
6. **安全清理应聚焦低风险表层。** 删除坏 CLI、冗余 CI、过期引用和 10 个旧分支；保留研究代码、结果表、历史报告和所有测试。

## 1. 方法与证据分级

### 1.1 证据来源

- `git log --all`、`git rev-list`、`git log --name-status`、`git log -S`：确认提交、分支、文件生命周期和功能引入/删除。
- `git diff`、`git show`、`git blame`：确认某段代码是加进来、被替换，还是只留在旧分支。
- GitHub PR 正文、评论、CI 结果：确认“为什么加”“为什么关”“是否被替代”。
- 当前代码搜索与测试：确认某个接口今天是否仍可达、是否仍被调用。
- 报告与提交信息只作为“意图证据”，不能单独证明实现仍然有效。

### 1.2 证据强度

| 等级 | 含义 | 示例 |
|---|---|---|
| A | 代码与提交/PR 双向吻合 | PR #2 的泄漏修复 + `test_cross_section_logic.py` + 当前 `prepare_sample` |
| B | 有明确文字说明，代码也基本支持 | HAC 方法、历史指数研究边界 |
| C | 只能从演化结果推断 | `configs/` 原本想作为统一参数入口，但从未接线 |
| D | 没有足够证据 | 不从提交标题推断未写明的业务意图 |

本次清理动作只采用 A/B 级证据；C 级只写入改进建议，不据此删除代码。

### 1.3 限制

- 工作区没有研究机器的原始 parquet，无法重新生成旧横截面结果。本报告只修血缘标注，不伪造新数值。
- 公开仓库之外可能还有本地脚本或未推送数据；本报告不假设它们存在，也不把它们当作保留理由。
- 分支上的中间提交在 squash 合并后未必是 `main` 祖先；本报告把“PR 已合并”与“Git 祖先关系”分开描述。

## 2. 架构演变时间线

```mermaid
flowchart LR
    A["2026-10-20—10-28<br/>5 只股票原型<br/>混池统计 + 候选股票池"] --> B["2026-11-01<br/>2d67e08<br/>800 只指数成分重建<br/>面板/信号/审计"]
    B --> C["2026-11-05<br/>PR #1/#2<br/>逐日横截面 + point-in-time 修复"]
    C --> D["2026-11-05<br/>PR #3<br/>HAC 稳健性推断"]
    D --> E["2026-11-05—11-08<br/>PR #4—#8<br/>历史成分/缺口/日线审计"]
    E --> F["2026-11-08<br/>PR #9<br/>删除一次性审计与补数"]
    F --> G["2026-11-08<br/>PR #10<br/>行业与市值数据采集"]
```

| 阶段 | 时间 | 关键提交/PR | 研究目标 | 结果 | 留下的东西 |
|---|---|---|---|---|---|
| P0 原型 | 10-20—10-28 | `c5bcf9c`—`25b91bc` | 5 只股票、2025 年、未复权跑通流程 | 验证了下载、标准化、混池统计 | 原型脚本和结果已在 `2d67e08` 删除；`configs`、部分文档叙事仍在 |
| P1 重建 | 11-01 | `2d67e08` | 800 只、2020—2025、后复权、面板/信号/审计 | 主线脚本形成 | `--source universe` 的支撑文件被删，但选项没删 |
| P2 统计修正 | 11-05 | `ed53761`、PR #1/#2、`288fc79`、`1259f5b` | 逐日横截面；禁止未来标签影响形成日分组 | 代码和测试修复完成 | 旧结果表/报告没有重跑，血缘开始分叉 |
| P3 稳健性 | 11-05 | PR #3、`9c97737` | HAC 处理持有期重叠 | 27 组结果归档 | HAC 方法与代码保留；README 仍有“审查分支”式旧状态 |
| P4 幸存者偏差审计 | 11-05—11-08 | PR #4—#8 | 历史成分快照、覆盖审计、缺口回补、日线完整性 | 确认当前 799 只不是历史完整股票池 | 一次性审计/补数代码在 PR #9 删除；文档和分支残留 |
| P5 扩展准备 | 11-08 | PR #10、`9080bc3` | 行业快照与每日市值采集 | 只完成数据下载，无中性化计算 | 采集器保留；主流程未消费 |

### 2.1 为什么这段历史值得审计

三个阶段的目标不同：

- P0 的目标是“先跑通”；
- P1/P2 的目标是“让统计口径站得住”；
- P4 的目标是“证明当前股票池存在偏差”；
- P5 的目标是“为下一阶段准备数据”。

每次目标切换都留下了上一阶段的接口、文档或结果，但没有一次统一清理。因此历史包袱主要是**阶段边界没有闭合**，而不是大量死代码。

## 3. PR 与分支地图

### 3.1 PR 账本

| PR | 分支 | 状态 | 架构作用 | 遗留 |
|---|---|---|---|---|
| #1 | `fix/point-in-time-cross-section` | 关闭未合并 | 先尝试修复未来标签泄漏 | 被 PR #2 取代；分支仍有 4 个专属提交 |
| #2 | `review/fix-cross-section-leakage-20261105` | 已合并 | 最终修复形成日排序、并列秩和跳过日统计 | 代码进主线；旧结果未重跑 |
| #3 | `研究/HAC稳健性检验-20261105` | 已合并 | 增加 HAC 推断、27 组结果和 CI | 方法与结果保留 |
| #4 | `feature/historical-index-snapshots-20261105` | 已合并 | 历史指数快照与覆盖审计 | 审计脚本后被 #9 删除 |
| #5 | `fix/historical-index-audit-mode-20261105` | 已合并 | 超龄快照/499 只异常审计模式 | `--audit-only` 后被 #9 删除 |
| #6 | `feat/historical-missing-price-backfill` | 已合并 | 按缺口清单一次性补行情 | 入口后被 #9 删除 |
| #7 | `feature/historical-daily-bar-audit` | 已合并 | 历史成分日线完整性审计 | 脚本后被 #9 删除 |
| #8 | `fix/historical-audit-edge-cases` | 已合并 | 日历范围、损坏 parquet、字段空值边界 | 脚本后被 #9 删除 |
| #9 | `cleanup/remove-historical-audits` | 已合并 | 主动删除一次性审计和补数 | 文档引用未同步 |
| #10 | `feature/industry-size-neutralization-data-20261108` | 已合并 | 行业/市值数据采集与测试 | 缺少下游中性化实现 |

### 3.2 分支状态

- 9 个分支对应已合并 PR；1 个分支对应关闭未合并的 PR #1。
- `review/fix-cross-section-leakage-20261105` 的 tip 是 `main` 的祖先；其余已合并分支因 squash 合并不再是祖先，但 PR 页面仍保留完整 patch 和讨论。
- `fix/point-in-time-cross-section` 的 tip 为 `fc23ad1`，内容是 PR #2 的早期版本；PR #1 评论明确说明它“基于早期横截面版本”，并会覆盖主分支更完善的平均秩与逐日明细功能。
- 因此 10 个分支都可在本轮清理中删除；`main` 永久保留。

## 4. 有意为之的设计

### I1. 逐日横截面，而不是跨日混池

**证据**

- `docs/PROJECT_MEMORY.md` D6 明确记录：混池会让少数极端行情日主导分组。
- `evaluate_cross_section.py` 的 `prepare_sample` 按 `groupby("date")` 排名。
- 早期 `evaluate_reverals.py` 和混池结果在 `2d67e08` 被删除。

**判断**：有意设计，且问题真实存在。冻结作品集时保留；继续研究时继续保留。

### I2. 形成日 point-in-time 排序

**证据**

- `288fc79` 把未来收益移出形成日排序条件。
- `1259f5b` 增加 `test_cross_section_logic.py`，覆盖未来标签缺失、并列秩、跳过日。
- 当前 `prepare_sample` 只按 `past_*d` 筛选和排名，`future_*d` 只用于事后评价。

**判断**：这是对真实未来函数风险的修复，不是历史包袱。测试必须保留。

### I3. 后复权收益与复权跳变修复

**证据**

- `build_panel.py` 在 `close / prev_close` 与 `pctChg` 差异超过 0.1 个百分点时用 `pctChg` 修复，并输出 `panel_repaired_returns.csv`。
- 文档记录 10 行、7 只股票的复权异常。
- `tests/test_schema.py` 要求面板只含后复权记录。

**判断**：有意设计。没有原始数据无法验证每一次修复，但代码和留档逻辑合理，不能作为清理对象。

### I4. 停牌与长期停牌复牌首日处理

**证据**

- `build_signals.py` 把停牌日收益记为缺失，并剔除连续停牌 ≥20 个交易日后的复牌首日。
- `test_no_lookahead.py` 与信号构建逻辑覆盖该口径。

**判断**：有意设计，对应 A 股停牌和复牌涨跌幅特殊规则。保留。

### I5. HAC 稳健性检验

**证据**

- PR #3 明确讨论重叠持有期、Newey–West/Bartlett 标准误、三种滞后阶数。
- `evaluate_hac.py` 拒绝有效区间内部缺失，避免把非相邻交易日当成相邻观测。
- `hac_robustness_summary.csv` 有 27 行，全部 p > 0.05。

**判断**：不是“没意义的统计装饰”。2 日、5 日持有期确实重叠，HAC 解决真实推断问题。保留代码、结果和报告。

### I6. 数据质量审计与明确边界

**证据**

- `audit_quality.py` 检查覆盖率、停牌、涨跌停、ST 和长期停牌。
- 报告反复声明当前股票池是当前时点快照，不是历史成员。
- README 明确不能把零结果外推为“A 股不存在反转”。

**判断**：有意设计，也是作品集可信度的一部分。保留。

### I7. 一脚本一阶段、raw/processed 分离

**证据**

- `download_data → build_panel → validate_adjustment → audit_quality → build_signals → evaluate_cross_section → evaluate_hac`。
- 原始行情、处理后数据和结果表分层，数据文件不入 Git。

**判断**：有意设计，但实现是手动编排。冻结作品集可接受；继续研究应补 orchestrator，而不是删除阶段脚本。

## 5. 演化债务与声明不一致

这些不是“可以直接删掉”的东西，而是下一阶段需要修正的设计问题。

### E1. `configs/baseline.yaml` 是声明式规格，不是运行时配置

**证据**

- 全仓库没有任何 Python 读取 `baseline.yaml`；只有 README 和 `PROJECT_MEMORY.md` 提到它。
- `PROJECT_MEMORY.md` D8 写“`configs/` 管参数”，但实际参数硬编码在 `build_signals.py`、`evaluate_cross_section.py`、`evaluate_hac.py`。
- 配置的 `start_date` 是 2020-01-01，而记忆文档仍写“初始区间 2015-01-01”。

**判断**：演化债务。冻结时保留为研究规格并标注“未接线”；继续研究时应把参数加载成单一入口，或正式删除“配置管理”这一承诺。

### E2. `next_open` 与 `allow_short` 只写在配置里

**证据**

- `configs/baseline.yaml` 声明 `execution_time: next_open` 和 `allow_short: false`。
- `build_signals.py` 的 `future_*d` 是收盘到收盘的复利收益。
- `evaluate_cross_section.py` 的收益差是“过去输家组 − 过去赢家组”，属于研究性多空差。

**判断**：声明边界没有体现在可执行回测里。零结果下不是紧急修复项，但报告不能把该收益差称为可成交收益。

### E3. 单一事实来源已经被文档扩张打破

**证据**

- `PROJECT_MEMORY.md` D9 说只保留项目记忆、`RESUME.md` 和两份数据报告。
- 当前有 8 份 `docs/reports/` 报告，`RESUME.md` 已在 `e5fbc00` 删除。
- 项目记忆最后更新停在 2026-11-05，仍称 HAC 未做、`|t|` 都小于 1.4、修正只会降低显著性。

**判断**：历史遗留文档状态。不能删除项目记忆，但必须更新，否则它会主动误导新会话。

### E4. 结果血缘断裂

**证据**

- `cross_section_summary.csv` 首次且只在 `ed53761` 生成；`b5eb1e5` 的报告也早于 PR #2 的修复。
- `288fc79`/`1259f5b` 修改的是代码和测试，没有重生成横截面表。
- `hac_results_2026-11-05.md` 明确说输入是“修复形成日排序后的 `cross_section_daily_detail.csv`”。
- 旧表与 HAC 表的同组数值不同，例如 1/1 日均收益差为 -0.000149 vs -0.000146648，5/5 普通 t 为 0.199 vs 0.192。

**判断**：高风险演化债务。冻结时应加显著血缘标签；继续研究时应重跑并重写报告。

### E5. 重复常量与手动步骤

**证据**

- `FORMATION_DAYS`、`FORWARD_DAYS` 在 `build_signals.py`、`evaluate_cross_section.py`、`evaluate_hac.py` 各写一遍。
- README 要求手动执行 8 步，没有 orchestrator；项目记忆 T7 已承认。
- 配置文件没有参与这些常量。

**判断**：演化债务。当前不构成可安全删除的代码；继续研究时集中参数化。

### E6. 数据契约写了，日志没有完全实现

**证据**

- `PROJECT_MEMORY.md` 的数据规则要求记录文件哈希和缺失数。
- `download_log.csv` 的实际列只有代码、名称、来源、状态、行数、日期、复权、耗时、重试、错误和下载时间，没有文件哈希或缺失数。
- `download_data.py` 也没有计算哈希。

**判断**：文档超前于实现。不是本次清理对象，继续研究时应补或降低承诺。

### E7. 研究文档混合了“历史结果”和“当前口径”

**证据**

- 横截面报告写着“当天信号或未来收益缺失的股票先剔除”，而当前代码已不再这样做。
- README 说“修复后的九组普通 t 值”，但表仍为修复前输出。

**判断**：文档状态必须显式分层；不能只靠读者猜日期。

## 6. 已失效问题的遗留方案

这一节回答“哪些是为了解决已经不存在的问题而引入的”。

### O1. `--source universe`

- 引入：初始候选股票池 `e8febcd`，配套 `data/manifests/沪深A股股票池.csv` 和 `build_stock_universe.py`。
- 失效：`2d67e08` 把研究范围改为“当前指数成分 800 只”，并删除股票池文件与构建脚本。
- 当前：`download_data.py` 仍保留 `universe` choice、`UNIVERSE_FILE` 和读取分支；一运行就会因文件不存在失败。
- 结论：**同时满足冻结/继续两种基线的可删除项**。删除接口，而不是恢复旧股票池。

### O2. 历史缺口回补模式

- 引入：PR #6，针对历史指数成员并集中的 463 只缺口一次性补行情。
- 失效：PR #9 明确删除 `--source historical_missing`、默认缺口 CSV 参数和相关测试。
- 当前：主线已无此入口；报告与 PR 保留历史记录。
- 结论：已经清理，不需要再删代码；只在报告中保留其“一次性”性质。

### O3. 历史成分覆盖/日线审计脚本

- 引入：PR #4、#7、#8。
- 失效：PR #9 删除 `audit_historical_coverage.py`、`audit_historical_bars.py` 及三份测试。
- 当前：三份历史报告仍把已删除脚本写成可运行步骤。
- 结论：代码已清理；文档需要历史横幅和不可运行标注。

### O4. `--audit-only`

- 引入：PR #5，处理 2021 年超龄快照与中证 500 返回 499 只。
- 失效：PR #9 简化采集器后移除该参数。
- 当前：`historical_index_methodology.md` 仍把 `--audit-only` 写成当前用法。
- 结论：文档历史化，不恢复该参数；当前采集器只记录滞后天数，不再输出审计摘要。

### O5. `historical-index-tests.yml`

- 引入：PR #4 为历史采集器单独建立工作流。
- 失效：PR #3 已建立 `research-tests.yml` 运行完整 `tests`，其中包含 `test_historical_index.py`；PR #9 又把前者的路径触发缩小到三个文件。
- 当前：两个工作流并存，完整测试工作流覆盖面更大。
- 结论：**可安全删除**，不会减少现行 PR 校验范围。

### O6. `RESUME.md`

- 引入：`e5aa93a`。
- 失效：`e5fbc00` 删除。
- 当前：`PROJECT_MEMORY.md` 仍有 4 处链接或完成状态引用。
- 结论：删除引用，不重建简历文件；项目叙事已由 README 和报告承担。

### O7. 早期 5 只股票流程与 PM 流程

- 早期脚本、混池结果、`PROJECT_PLAN.md`、周报、`.trae` PM skill 均在 `2d67e08` 删除。
- 当前没有可运行入口或文档引用。
- 结论：已经完整清理，只需在报告中留档，不需要再做代码动作。

## 7. 双基线判定表

| 对象 | 冻结为作品集 | 继续研究 | 本次动作 |
|---|---|---|---|
| `--source universe` 与 `UNIVERSE_FILE` | 删除 | 删除 | 删除，并加测试 |
| `historical-index-tests.yml` | 删除 | 删除 | 删除 |
| 历史指数报告的旧命令 | 标注历史 | 标注历史 | 加横幅、移除不可运行命令 |
| `PROJECT_MEMORY.md` 过期状态 | 修正 | 修正 | 更新 |
| 横截面结果表 | 保留并标注血缘 | 重跑后替换 | 仅加说明，不改数值 |
| HAC 代码/结果 | 保留 | 保留 | 不动 |
| 数据质量与未来函数测试 | 保留 | 保留 | 不动 |
| `collect_historical_index.py` 与历史报告 | 可归档 | 保留并补下游 | 不删 |
| `download_neutralization_data.py` | 可归档 | 保留并补中性化计算 | 不删 |
| `configs/baseline.yaml` | 保留为规格 | 接线或删除承诺 | 仅加声明 |
| 10 个旧功能分支 | 删除 | 删除 | 删除 |

## 8. 结果血缘

| 结果/报告 | 生成路径 | 生成时点 | 当前状态 |
|---|---|---|---|
| `adjust_cross_check.csv` | `validate_adjustment.py` | 复权校验 | 与当前面板口径一致 |
| `panel_quality_summary.csv` | `build_panel.py` | 面板构建 | 与当前脚本一致 |
| `panel_dropped_rows.csv` | `build_panel.py` | 剔除非后复权 | 证据留档 |
| `panel_repaired_returns.csv` | `build_panel.py` | 复权跳变修复 | 证据留档 |
| `data_quality_v2.csv`、`long_suspensions.csv` | `audit_quality.py` | 数据审计 | 与当前脚本一致 |
| `signal_summary.csv` | `build_signals.py` | 信号构建 | 与当前脚本一致 |
| `cross_section_summary.csv`、`quantile_*`、`subsample_stability.csv` | `evaluate_cross_section.py` | `ed53761`，PR #2 之前 | **旧口径，不能作为修复后结果引用** |
| `docs/reports/cross_section_2026-11-05.md` | 手工整理 | `b5eb1e5`，PR #2 之前 | **旧口径，需重跑** |
| `hac_robustness_summary.csv` | `evaluate_hac.py` | PR #3 | 使用修复后本地逐日明细；输入 CSV 未入库 |
| `docs/reports/hac_results_2026-11-05.md` | 手工整理 | PR #3 | 与 HAC 表一致，但依赖未入库输入 |

**关键边界**：HAC 表可以用于“HAC 复核”的统计叙事，但不能反过来证明旧横截面表已被重跑。当前仓库没有 `cross_section_daily_detail.csv`，因此 HAC 的完整输入无法从 Git 独立重建；这是继续研究时必须补的可复现性缺口。

## 9. 安全移除分级

### S0：本次已执行的零行为风险清理

- 删除坏掉的 `--source universe` 接口。
- 删除冗余的历史测试工作流。
- 修正过期项目记忆、README 和横截面/历史报告引用。
- 删除 10 个已合并或已被取代的远端分支。

### S1：冻结作品集时可以考虑

- 将 `collect_historical_index.py`、`test_historical_index.py` 移到归档分支或 `archive/` 区域。
- 将 `download_neutralization_data.py`、`test_neutralization_data.py` 和 README 中性化章节标记为“未完成扩展”。
- 将三份历史指数报告归档，但保留其审计结论作为研究边界证据。

### S2：继续研究时必须保留并补齐

- 历史指数采集器：补逐日/周度资格、官方公告核验、行情缺口与可交易性。
- 行业/市值采集器：补中性化计算、缺失处理和暴露诊断。
- `configs/baseline.yaml`：接入运行时，或删除“配置管理”承诺。
- 横截面与 HAC 的完整输入：保存 `cross_section_daily_detail.csv` 或可重建脚本。

### S3：不要删除

- `evaluate_cross_section.py`、`build_signals.py`、`build_panel.py`。
- `evaluate_hac.py` 与 HAC 测试/结果。
- `audit_quality.py`、`validate_adjustment.py`。
- 数据、信号、横截面和 HAC 结果表的现有留档。
- 所有未来函数、并列秩、跳过日、HAC 和 schema 测试。

## 10. 本次清理 PR 的范围

### 代码

- `src/download_data.py`：移除 `universe` 来源、`UNIVERSE_FILE` 和旧文档示例；未知来源显式报错，避免静默回退。
- 新增 `tests/test_download_cli.py`：验证 `load_codes("universe")` 被拒绝且不访问网络。

### CI

- 删除 `.github/workflows/historical-index-tests.yml`。
- 保留 `.github/workflows/research-tests.yml`。

### 文档

- README 增加结果血缘、审计报告入口和配置声明。
- 项目记忆更新到 2026-11-18。
- 横截面报告加“修复前结果”横幅。
- 三份历史指数报告加“历史阶段/脚本已删除”横幅，移除或隔离不可运行命令。
- `configs/baseline.yaml` 增加“声明式规格、当前未接线”注释。

### 明确不做

- 不删除 `collect_historical_index.py` 或 `download_neutralization_data.py`。
- 不修改 HAC 数值、横截面数值、行情数据或研究结论。
- 不重写 Git 历史，不执行 `filter-repo`，不合并 PR。

## 11. 验证记录

- 清理前：`python -m pytest tests -q` → 26 passed, 17 skipped。
- 清理后：应至少保持全部纯单元测试通过，新增 CLI 测试通过。
- `python src/download_data.py --help`：`--source` 只剩 `index800`、`hs300`、`zz500`。
- 全仓搜索：活动文档不再引用 `audit_historical_coverage.py`、`audit_historical_bars.py`、`RESUME.md`、`--source universe`。
- `git diff --check`：无空白错误。
- GitHub Actions：PR 的“无行情依赖单元测试”通过。

## 附录 A：分支 tip 与清理状态

| 分支 | Tip SHA | PR | 处理 |
|---|---|---|---|
| `研究/HAC稳健性检验-20261105` | `db2b1ee` | #3 已合并 | 删除 |
| `cleanup/remove-historical-audits` | `fc0fc12` | #9 已合并 | 删除 |
| `feat/historical-missing-price-backfill` | `2b2ac8c` | #6 已合并 | 删除 |
| `feature/historical-daily-bar-audit` | `03f6c95` | #7 已合并 | 删除 |
| `feature/historical-index-snapshots-20261105` | `b112c50` | #4 已合并 | 删除 |
| `feature/industry-size-neutralization-data-20261108` | `f4a55d3` | #10 已合并 | 删除 |
| `fix/historical-audit-edge-cases` | `fa97d3a` | #8 已合并 | 删除 |
| `fix/historical-index-audit-mode-20261105` | `9b81bbc` | #5 已合并 | 删除 |
| `review/fix-cross-section-leakage-20261105` | `1259f5b` | #2 已合并 | 删除 |
| `fix/point-in-time-cross-section` | `fc23ad1` | #1 关闭未合并 | 删除；PR 保留证据 |

## 附录 B：已删除文件账本

| 文件 | 引入 | 删除 | 性质 |
|---|---|---|---|
| `data/manifests/沪深A股股票池.csv` | `e8febcd` | `2d67e08` | 早期全市场候选池；留下坏 `universe` 接口 |
| `src/build_stock_universe.py` | `e8febcd` | `2d67e08` | 早期股票池构建 |
| `src/audit_historical_coverage.py` | PR #4 | PR #9 | 一次性覆盖审计 |
| `src/audit_historical_bars.py` | PR #7 | PR #9 | 一次性日线质量审计 |
| `tests/test_download_missing.py` | PR #6 | PR #9 | 历史缺口补数测试 |
| `tests/test_historical_coverage.py` | PR #4 | PR #9 | 覆盖审计测试 |
| `tests/test_historical_bars.py` | PR #7 | PR #9 | 日线审计测试 |
| `docs/RESUME.md` | `e5aa93a` | `e5fbc00` | 简历素材；引用未清 |
| `docs/PROJECT_PLAN.md`、周报、`.trae` skill | `e5aa93a` | `2d67e08` | 早期项目管理流程 |

## 附录 C：判断原则

1. **问题仍存在，但一次性解决方案已完成**：删除流程，保留结论和限制说明。
2. **问题仍存在，解决方案仍需要**：保留代码和测试，即使它暂时不在主线。
3. **问题已消失，代码仍可达**：删除接口或归档代码。
4. **文档描述的是旧状态**：先加历史横幅，再决定是否重写；不能把历史报告删掉以掩盖旧结论。
5. **历史应保留，垃圾应清除**：分支可以删，PR 页面、报告和 SHA 记录应保留。

