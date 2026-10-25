# 功能与产物目录

## 功能归类

| 类别 | 生产入口 / 核心模块 | 说明 |
|---|---|---|
| 市场监控 | `scripts/screening/market_gambler_watch.py`、`scripts/daily/intraday_emergency_monitor.py`、`scripts/screening/weekly_sentiment_check.py` | 盘中、盘后与周度情绪监控 |
| 信号与风控 | `scripts/daily/daily_signal.py`、`scripts/daily/today_action.py`、`scripts/screening/sentiment_engine.py`、`scripts/screening/risk_engine.py` | 仓位信号与风险约束 |
| RPS 选股 | `scripts/screening/rps_system.py`、`rps_analyzer/`、`config/dragon_strategy.py` | RPS、龙回头、口袋支点与扫描器 |
| 数据与验证 | `emotion_validation/`、`scripts/daily/backtest_sentiment_daily.py` | 行情仓库、情绪因子、ETF 轮动和回测 |
| 集成与调度 | `scripts/integration/feishu_bot.py`、`scripts/integration/cloud_runner.py`、`scripts/integration/manual_trigger_api.py`、`scripts/integration/tos_storage.py`、`scripts/integration/volcengine_handler.py` | 消息、API、云调度和对象存储 |
| 研究工具 | `run_*verification.py`、`run_*optimization.py`、`scripts/research/compare_freq.py`、`scripts/research/feb_review.py`、`scripts/research/monthly_review.py` | 离线验证、参数搜索和复盘；不属于线上入口 |

## 目录规则

| 目录 | 内容 | 是否进入 Git |
|---|---|---|
| `config/`、`backtest/configs/` | 可审阅的策略配置 | 是 |
| `data/market/` | 行情、指数、生态与 ETF 缓存 | 否 |
| `outputs/reports/` | 回测和研究报告 | 否 |
| `outputs/exports/` | Excel/CSV 人工交付物 | 否 |
| `outputs/signals/` | 每日信号、情绪历史、任务文本 | 否 |
| `outputs/logs/` | 运行日志 | 否 |
| `docs/` | 架构、使用与历史计划 | 是 |
| `tests/` | 自动化测试 | 是 |

本表原为迁入功能的索引，不涵盖主仓库全部策略。主工程整理后的完整目录与运行入口见
[PROJECT-ORGANIZATION.md](PROJECT-ORGANIZATION.md) 和 [ENTRYPOINTS.md](ENTRYPOINTS.md)。
迁入功能的数据与结果使用 `data/market/`、`outputs/`；原主仓库TDX入口仍按既有契约读写
`output/`，现在也可通过 `outputs/strategies/` 访问同一目录。`backtest/` 的历史数据和报告
读取兼容仍保留。

