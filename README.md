# EQ-MR-01: A-Share Short-Term Reversal Research

## 1. Project Overview

This project studies short-term reversal effects in China's A-share equity market.

The objective is to determine whether stocks with relatively poor recent returns subsequently outperform stocks with strong recent returns, and whether the effect remains meaningful after accounting for market conditions, trading constraints, and transaction costs.

This is a quantitative research project, not an assumption that the strategy will be profitable.

## 2. Research Questions

- Does short-term reversal exist in A-share equities?
- How does the effect vary across formation and holding periods?
- Does industry adjustment change the results?
- Does market volatility affect the signal?
- Does the effect survive realistic execution assumptions and transaction costs?

## 3. Initial Research Scope

- Market: Shanghai and Shenzhen A-share equities
- Frequency: Daily
- Initial sample period: 2015-01-01 to 2025-12-31
- Formation periods: 1, 5, and 20 trading days
- Holding periods: 1, 2, and 5 trading days
- Primary tools: Python, pandas, NumPy, SciPy, statsmodels

The final sample period and security universe depend on data availability and quality checks.

## 4. Research Principles

- Avoid look-ahead bias.
- Document survivorship bias and historical universe limitations.
- Preserve raw data separately from processed data.
- Account for corporate actions and trading restrictions.
- Separate research signals from executable trading strategies.
- Report out-of-sample results and transaction-cost sensitivity.
- Preserve reproducible configurations and experiment logs.

## 5. Repository Structure

- `src/`: reusable research code
- `notebooks/`: exploratory analysis
- `configs/`: experiment configurations
- `tests/`: automated validation
- `docs/`: research notes and data documentation
- `results/`: approved summary tables and figures

## 6. Current Status

Phase 1: Data infrastructure and quality validation.

No profitability claim is made until the research and out-of-sample evaluation are completed.

## 7. Reproducibility

The project will record dependency versions, data-source metadata, experiment parameters, and validation results.

Raw market data is excluded from the repository by default. Users must obtain data through permitted sources and follow the applicable data-provider terms.
