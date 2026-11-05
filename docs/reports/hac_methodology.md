# T2.3：短期反转收益差的 Newey–West / HAC 检验方法

## 1. 研究对象

T2.2 已对每个形成日按过去收益分组，并产生「当日形成的过去输家组未来平均收益 − 过去赢家组未来平均收益」。记为：

\[
D_t = \overline{R}_{\mathrm{loser},t,t+h}
    - \overline{R}_{\mathrm{winner},t,t+h}
\]

我们检验的原假设是 \(H_0: E[D_t]=0\)，不是直接检验单只股票未来收益，也不是已扣除交易成本的可交易组合收益。

读取字段：`交易日期`、`形成期`、`持有期`、`反转收益差`。每个参数组合必须在全样本形成日历上只出现一次。

## 2. 为什么普通 t 值可能有偏差

普通 t 值使用样本标准差 \(s_D\)：

\[
t_{\mathrm{ordinary}} = \frac{\overline D}{s_D/\sqrt n}
\]

但重叠持有期的不同日期评价窗口分享未来行情。即使持有期为 1 日，市场状态也可能带来其他自相关。若序列相关非零，普通的均值标准误不再充分反映时间依赖。

## 3. Newey–West（Bartlett）估计

定义残差 \(e_t = D_t - \overline D\)，滞后 \(k\) 的样本协方差为：

\[
\hat\gamma_k = \frac{1}{n}\sum_{t=k+1}^{n}e_te_{t-k}
\]

Bartlett 加权长期方差为：

\[
\hat\Omega_L =
\hat\gamma_0 + 2\sum_{k=1}^{L}
\left(1-\frac{k}{L+1}\right)\hat\gamma_k
\]

于是：

\[
SE_{\mathrm{HAC}} = \sqrt{\frac{\hat\Omega_L}{n}},
\qquad
t_{\mathrm{HAC}} = \frac{\overline D}{SE_{\mathrm{HAC}}}
\]

代码采用 `statsmodels.OLS(y, ones).fit(cov_type="HAC", cov_kwds={"maxlags": L, "use_correction": False}, use_t=False)`。
其中 `use_correction=False` 与上面除以 \(n\) 的手算口径一致；p 值及置信区间使用大样本正态近似。

**容易误解的一点：** HAC 改变的是标准误，不改变均值。根据序列相关的方向和大小，HAC t 值的绝对值可能变大，也可能变小，不能事先声称普通 t 是上界。

## 4. 为什么同时展示三种滞后阶数

- **主设定：** \(L = \max(0,h-1)\)，其中 \(h\) 是评价窗口长度。它针对直接重叠的窗口，但不保证覆盖其他相关性。
- **稳健性：** \(L=5\) 和 \(L=10\)，查看结论是否敏感于有限带宽选择。

三种设定的结果统一保存；不应事后只挑最显著的滞后阶数报告。真实策略若存在更长周期的相关性，仍可能需要其他带宽选择与区块 Bootstrap 校验。

## 5. 关于缺失日和样本边界

对于形成期初始不足、样本结尾未来窗口不完整的日期，允许 `反转收益差` 为空。

但如果**首个有效日期和最后一个有效日期之间**存在缺失，当前模块会停止估计并提示日期。这是有意设计的保守检查：简单 `dropna` 会把被隔开的两个交易日挤成相邻观测，从而改变 HAC 的时间滞后含义。

后续若存在大量内部缺失，应制定基于完整交易日历的协方差估计或缺失机制处理方案，而不是绕开检查。

## 6. 输出、验证与解释

运行：

```powershell
python src/evaluate_cross_section.py
python src/evaluate_hac.py
python -m pytest tests/test_hac.py -v
```

`results/tables/hac_robustness_summary.csv` 应有 **9 个组合 × 3 个滞后设定 = 27 行**。关键列包括有效交易日数、跳过交易日数、普通 t 值、HAC 标准误、HAC t 值、双侧 p 值和 95% 置信区间。

独立测试 `tests/test_hac.py` 使用五个可手算观测值 `[1,2,4,3,5]`：
\(\bar D=3\)、\(\hat\gamma_0=2\)、\(\hat\gamma_1=0.2\)，当 \(L=1\) 时
\(\hat\Omega_1=2.2\)，因此 \(SE_{\mathrm{HAC}}=\sqrt{2.2/5}\)。
手算与 statsmodels 结果必须一致。

**注意：** 这个阶段的输出属于探索性收益标签的统计推断，不是交易策略收益证明。当前研究还存在：当前指数成分快照的幸存者偏差、缺失标签的样本选择、行业和市值暴露、未计交易成本、多个形成期和评价期共同检验等问题。单个未校正 p 值不足以宣称发现稳定效应。

## 7. 版本状态

本方法文件与代码、测试一起提供。只有在拥有完整本地 CSV 的研究机器上重新运行后，才能把最终 27 行的数值作为新版本正式结果；不应把此前使用六位小数导出文件得到的初步 HAC 估计当作最终复核值。
