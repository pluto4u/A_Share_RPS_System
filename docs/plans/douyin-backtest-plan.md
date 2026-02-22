---
name: 抖音战法回测验证
overview: 把抖音老哥的完整交易系统提炼成可量化的规则，用仓库里 4660 只股票的 2024-2025 真实日线数据进行独立回测验证，输出真实胜率、收益和回撤。
status: completed (所有模块已实现，已修复数据兼容性和路径问题，32项单元测试全部通过)
---

# 抖音实战派交易系统 - 独立回测验证

## 核心思路

把交易系统拆成 5 个可量化模块，逐一回测验证，最后组合成完整策略跑一遍。

## 数据基础

- 个股日线: `backtest/data/stock_daily.csv` (支持 csv/parquet, 字段: ts_code, trade_date, open, high, low, close, vol, pct_chg, amount)
- 指数日线: `backtest/data/index_daily.parquet`
- 生态缓存: `backtest/data/ecology_cache.csv` (字段: trade_date, limit_up_count, limit_down_count, attempt_limit_up_count)

## 模块设计

### 模块1: 市场广度计算器 (已完成)
`emotion_validation/strategy_layer/douyin_system/market_breadth.py`
- 逐日统计上涨/下跌/持平家数
- 合并涨停生态数据
- 输出买入/半仓/清仓信号

### 模块2: 选股引擎 (已完成)
`stock_screener.py` - 4种选股策略:
- 策略A: 放量长上影
- 策略B: 一进二
- 策略C: 横盘突破涨停
- 策略D: 强势股回调确认

### 模块3: 交易模拟器 (已完成)
`trade_simulator.py` - 日线级交易模拟:
- 买入价估算: (open + high) / 2 * 0.95
- 卖出规则: 止损/三根K线/放量滞涨/盈利了结

### 模块4: 风控引擎 (已完成)
`risk_controller.py`:
- 连亏2天减仓50%、连亏3天清仓暂停3天
- 3000家下跌砍半仓、4000家清仓

### 模块5: 回测报告 (已完成)
`backtest_report.py`:
- 逐笔交易明细、整体统计、按策略分类统计
- 与情绪策略和指数的对比

## 文件结构

```
emotion_validation/strategy_layer/douyin_system/
  __init__.py          # 已完成
  market_breadth.py    # 已完成
  stock_screener.py    # 已完成
  trade_simulator.py   # 已完成
  risk_controller.py   # 已完成
  backtest_report.py   # 已完成
  run_backtest.py      # 已完成 (CLI入口)
```

## 测试覆盖

`tests/test_douyin_backtest.py` - 32 项 pytest 单元测试:
- MarketBreadth: 6 tests (加载csv/parquet、广度计算、信号生成、日期过滤)
- StockScreener: 7 tests (加载数据、4种策略、综合选股)
- TradeSimulator: 8 tests (初始化、买卖操作、参数设置、重置)
- RiskController: 8 tests (阈值设置、市场风控、连亏监控、交易暂停)
- BacktestReport: 3 tests (目录创建、数据加载)

## 修复记录 (2026-02-19)

1. **market_breadth.py**: 字段名 `code/date/pct_change` 改为 `ts_code/trade_date/pct_chg`，支持 csv 格式
2. **run_backtest.py**: 修复路径计算错误 (4级`..`改为3级)，修复 `sys.path` 添加路径
3. **requirements.txt**: 补充 `matplotlib>=3.7.0` 和 `seaborn>=0.12.0`
4. **清理**: 删除4个调用错误方法名的旧测试文件，替换为正确的 pytest 测试
