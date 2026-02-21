# 技能: A股情绪监控

当用户提到以下任何关键词时，执行对应操作：
- "情绪监控"、"跑一下监控"、"看看盘面"、"market watch" → 快速模式
- "完整监控"、"full监控"、"全面分析" → 完整模式
- "试运行"、"dry run"、"测试一下" → 试运行模式
- "情绪回测"、"跑回测"、"验证策略" → 情绪验证回测
- "ETF回测"、"轮动回测"、"ETF轮动" → ETF轮动回测
- "今日信号"、"出信号"、"daily signal" → 今日实盘信号
- "跑今日操作"、"14:40任务"、"daily action" → 今日操作指令（含数据更新）
- "全量验证"、"跑全套"、"full pipeline" → 全量Pipeline

## 快速模式（默认，约15秒）

```bash
cd /root/projects/A_Share_RPS_System && python cloud_runner.py --session-only
```

## 完整模式（含广度+RPS，约3-5分钟）

```bash
cd /root/projects/A_Share_RPS_System && python cloud_runner.py --full
```

## 试运行（不推送飞书，仅验证流程）

```bash
cd /root/projects/A_Share_RPS_System && python cloud_runner.py --dry-run
```

## 情绪验证回测（含16因子+S5策略+实验套件）

```bash
cd /root/projects/A_Share_RPS_System && python -m emotion_validation.pipeline
```

结果输出到 `backtest/reports/emotion_validation/` 目录。

## ETF轮动回测（S5仓位+月度轮动+闪崩反转/衰竭反转）

```bash
cd /root/projects/A_Share_RPS_System && python -m emotion_validation.etf_rotation.backtest_rotation
```

结果输出到 `backtest/reports/etf_rotation/` 目录。

## 今日实盘信号（含紧急拦截器+反转信号）

```bash
cd /root/projects/A_Share_RPS_System && python daily_signal.py
```

输出当日操作建议：目标仓位、行动建议、是否触发紧急状态。

## 今日操作指令（14:40 定时任务用，先更新数据再出单）

```bash
cd /root/projects/A_Share_RPS_System && python today_action.py --update
```

输出：情绪分、目标仓位、ETF 持仓、资金分配、操作建议。供 OpenClaw cron 每日 14:40 触发。

## 注意事项

- 交易时段: 9:30-11:30, 13:00-15:00（使用 `--session-only` 时非交易时段自动跳过）
- 结果会自动推送到飞书群（通过 feishu_bot.py + feishu_config.json）
- 如果执行失败，检查 akshare 数据源是否可用: `python -c "import akshare; print(akshare.__version__)"`
- 新增依赖: pyarrow, matplotlib, scipy, scikit-learn（首次需 `pip install -r requirements.txt`）
