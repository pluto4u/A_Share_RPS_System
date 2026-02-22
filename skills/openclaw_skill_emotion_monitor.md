# 技能: A股情绪监控 + 数据维护

当用户提到以下任何关键词时，执行对应操作：
- "情绪监控"、"跑一下监控"、"看看盘面"、"market watch" → 快速模式
- "完整监控"、"full监控"、"全面分析" → 完整模式
- "试运行"、"dry run"、"测试一下" → 试运行模式
- "情绪回测"、"跑回测"、"验证策略" → 情绪验证回测
- "ETF回测"、"轮动回测"、"ETF轮动" → ETF轮动回测
- "今日信号"、"出信号"、"daily signal" → 今日实盘信号
- "跑今日操作"、"14:40任务"、"daily action" → 今日操作指令（含数据更新）
- "全量验证"、"跑全套"、"full pipeline" → 全量Pipeline（含TOS同步）
- "同步TOS"、"上传TOS"、"备份数据" → 上传数据到TOS
- "拉取TOS"、"下载TOS"、"同步云端" → 从TOS下载数据
- "TOS状态"、"查看数据状态"、"数据在哪" → 查看数据同步状态

## 快速模式（默认，约15秒）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --session-only > /tmp/rps_runner.log 2>&1 && echo "✅ 情绪监控完成，卡片已推送到群" || echo "❌ 执行失败，日志: $(tail -3 /tmp/rps_runner.log)"
```

## 完整模式（含广度+RPS，约3-5分钟）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --full > /tmp/rps_runner.log 2>&1 && echo "✅ 完整监控完成，卡片已推送到群" || echo "❌ 执行失败，日志: $(tail -3 /tmp/rps_runner.log)"
```

## 试运行（不推送飞书，仅验证流程）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --dry-run
```

## 情绪验证回测（含16因子+S5策略+实验套件）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python -m emotion_validation.pipeline > /tmp/rps_backtest.log 2>&1 && echo "✅ 回测完成，结果在 backtest/reports/emotion_validation/" || echo "❌ 回测失败，日志: $(tail -3 /tmp/rps_backtest.log)"
```

## ETF轮动回测（S5仓位+月度轮动+闪崩反转/衰竭反转）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python -m emotion_validation.etf_rotation.backtest_rotation > /tmp/rps_etf.log 2>&1 && echo "✅ ETF轮动回测完成，结果在 backtest/reports/etf_rotation/" || echo "❌ 回测失败，日志: $(tail -3 /tmp/rps_etf.log)"
```

## 今日实盘信号（含紧急拦截器+反转信号）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python daily_signal.py --feishu > /tmp/rps_signal.log 2>&1 && echo "✅ 实盘信号已通过卡片推送到群" || echo "❌ 信号生成失败，日志: $(tail -3 /tmp/rps_signal.log)"
```

## 今日操作指令（14:40 定时任务用，先更新数据再出单）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python today_action.py --update --feishu > /tmp/rps_action.log 2>&1 && echo "✅ 操作指令已通过卡片推送到群" || echo "❌ 操作指令失败，日志: $(tail -3 /tmp/rps_action.log)"
```

## 全量Pipeline（含TOS数据同步，约5-8分钟）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --pipeline --tos > /tmp/rps_pipeline.log 2>&1 && echo "✅ 全量Pipeline完成，数据已同步TOS，卡片已推送" || echo "❌ Pipeline失败，日志: $(tail -5 /tmp/rps_pipeline.log)"
```

## 上传数据到TOS（将云端数据同步到对象存储）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python tos_storage.py sync-up --force 2>&1 | tail -5
```

## 从TOS下载数据（从对象存储拉取最新数据到云端）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python tos_storage.py sync-down 2>&1 | tail -5
```

## 查看数据同步状态

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python tos_storage.py status
```

## 注意事项

- 交易时段: 9:30-11:30, 13:00-15:00（使用 `--session-only` 时非交易时段自动跳过）
- 结果会自动推送到飞书群（通过 webhook 互动卡片）
- 所有命令的详细日志保存在 `/tmp/rps_*.log`，出错时自动显示最后几行
- 如果执行失败，检查 akshare 数据源是否可用: `python -c "import akshare; print(akshare.__version__)"`
- 新增依赖: pyarrow, matplotlib, scipy, scikit-learn, tos（首次需 `pip install -r requirements.txt`）
- TOS 环境变量文件: `tos.env`（含 TOS_ACCESS_KEY, TOS_SECRET_KEY, TOS_ROLE=cloud_ecs 等）
- 云端角色为 cloud_ecs（主写入方），本地笔记本为 local_laptop（写入需 --force 确认）
