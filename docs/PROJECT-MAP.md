# A_Share_RPS_System 项目地图

> 最后更新: 2026-02-20

## 架构总览

```
数据采集 --> 情绪分析 --> 仓位决策 --> 选股/轮动 --> 消息推送
   |            |            |            |            |
   v            v            v            v            v
 akshare   emotion_engine   S5仓位    ETF轮动/抖音   feishu_bot
```

## 核心系统 (生产环境)

| 文件 | 说明 |
|------|------|
| `market_gambler_watch.py` | 主程序入口，采集全市场数据，计算情绪指标 |
| `sentiment_engine.py` | 仓位映射 (重仓/标准/轻仓/空仓) |
| `risk_engine.py` | 三层风控：日内止损、回撤限制、连亏管理 |
| `rps_system.py` | RPS 相对强度选股系统 |
| `feishu_bot.py` | 飞书消息推送 + Bitable 同步 |

## 实盘工具 (每日使用)

| 文件 | 说明 | 用法 |
|------|------|------|
| `daily_signal.py` | 每日仓位信号 (S5 策略) | `python daily_signal.py` |

## 情绪验证模块 (emotion_validation/)

### 数据层

| 文件 | 说明 |
|------|------|
| `data_layer/data_warehouse.py` | 全市场数据仓库 (parquet, 5269只股票) |
| `data_layer/historical_ecology_builder.py` | 从日线计算涨停生态特征 |

### 情绪引擎

| 文件 | 说明 |
|------|------|
| `emotion_engine/emotion_factors.py` | 16 个情绪因子定义 |
| `emotion_engine/emotion_score.py` | 加权评分 + regime 分类 |

### 回测与验证

| 文件 | 说明 |
|------|------|
| `backtest_engine/simulator.py` | 情绪择时回测模拟器 |
| `validation/experiment_suite.py` | 4 类核心验证实验 |
| `final_comparison.py` | 6 策略终极对比 (S0-S5) |

### ETF 轮动

| 文件 | 说明 |
|------|------|
| `etf_rotation/etf_data.py` | 12只行业ETF数据管理 |
| `etf_rotation/rotation_strategy.py` | 月度动量轮动 + 情绪过滤 |
| `etf_rotation/backtest_rotation.py` | 4策略回测 (D/C/A/B) |

### 抖音战法

| 文件 | 说明 |
|------|------|
| `strategy_layer/douyin_system/market_breadth.py` | 市场广度信号 |
| `strategy_layer/douyin_system/stock_screener.py` | 4种选股策略 |
| `strategy_layer/douyin_system/trade_simulator.py` | 日线级交易模拟 |
| `strategy_layer/douyin_system/risk_controller.py` | 连亏减仓 + 下跌响应 |
| `strategy_layer/douyin_system/backtest_report.py` | 回测报告生成 |
| `strategy_layer/douyin_system/run_backtest.py` | CLI 入口 |

### 管线与报告

| 文件 | 说明 |
|------|------|
| `pipeline.py` | 一键运行全流程 |
| `market_thermometer.py` | 市场温度仪表盘 |
| `reporting/emotion_report.py` | 验证报告生成 |

## 辅助文件

| 文件 | 说明 |
|------|------|
| `cloud_runner.py` | 云端定时任务入口 |
| `manual_trigger_api.py` | FastAPI HTTP 手动触发 |
| `volcengine_handler.py` | 火山引擎云函数适配 |
| `sentiment_history.py` | 历史情绪数据持久化 |
| `backtest_sentiment_daily.py` | 情绪择时策略回测 |
| `weekly_sentiment_check.py` | 每周情绪复盘 |

## 测试

| 文件 | 测试目标 |
|------|---------|
| `tests/test_sentiment_engine.py` | 仓位映射 |
| `tests/test_risk_engine.py` | 风控逻辑 |
| `tests/test_douyin_backtest.py` | 抖音战法 (32项) |
| `tests/test_*.py` (其余 8 个) | 各模块单测 |

## 文档

| 文件 | 说明 |
|------|------|
| `docs/EMOTION-STRATEGY-GUIDE.md` | 情绪策略实操指南 (最重要) |
| `docs/PROJECT-MAP.md` | 本文件 |
| `docs/plans/*.md` | 5 份设计/实施计划 |

## 命令速查

```bash
# 每日看信号
python daily_signal.py

# ETF 轮动建议 (每月底)
python -m emotion_validation.etf_rotation.rotation_strategy

# 温度仪表盘
python -m emotion_validation.market_thermometer

# 更新数据
python -m emotion_validation.data_layer.data_warehouse --update
python -m emotion_validation.pipeline
python -m emotion_validation.etf_rotation.etf_data

# 回测
python -m emotion_validation.etf_rotation.backtest_rotation
python emotion_validation/final_comparison.py
```
