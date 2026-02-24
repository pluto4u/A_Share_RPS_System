# 技能: A股情绪监控 + ETF轮动 + RPS选股

当用户提到以下任何关键词时，执行对应操作：

## 三项核心功能

| 功能 | 触发词 | 执行内容 |
|------|--------|----------|
| **情绪判断** | 情绪监控、看看盘面 | 快速情绪扫描 (cloud_runner --session-only) |
| **ETF轮动** | ETF回测、轮动回测 | ETF 月度轮动回测 |
| **RPS选股** | RPS选股、龙回头选股 | 龙回头低吸候选 (today_action 内含) |

## 一键全量（情绪+ETF+RPS）

| 触发词 | 执行 |
|--------|------|
| "跑今日操作"、"14:40任务"、"daily action" | today_action.py --update --feishu（情绪+ETF轮动+龙回头低吸） |
| "全量验证"、"跑全套"、"full pipeline" | 全量 Pipeline（含 TOS 同步） |

## 🤖 OpenClaw 回复格式规范 (重要)

当你执行以上任务后，向用户汇报时，**必须严格遵守** 以下 Markdown 模板（保持列表和空行，不要挤在一起）：

```markdown
### 📊 市场情绪核心结论
- **情绪评分**：[提取日志中的分数]
- **建议仓位**：[提取日志中的仓位]

### 📈 市场亮点
- **指数表现**：[提取日志中的大盘表现，如上涨/下跌等]
- **量能分析**：[提取日志中的量比、量能情况]
- **连板梯队**：[提取日志中的连板情况]

### 💡 操作建议
- **行动**：[提取加仓/减仓/等待等建议]
- **ETF池**：[提取底仓建议]
- **个股/龙回头**：[如果有扫描到龙回头候选，列出1-2个；如果没有则说耐心等待]
```

请根据执行命令产生的终端标准输出日志 (stdout) 来填充上述模板，**绝对不要自己编造数据（幻觉）**！

## 🧠 记忆错乱防止 (必读)

- **仅用本次输出**：回复内容必须来自本次命令的 stdout，不要混用历史对话、其他日期、其他任务的数据。
- **无数据时诚实说明**：若本次执行无可用输出、日志为空或解析失败，明确回复「本次执行无可用输出，请检查日志或重试」，**不要猜测或编造**。
- **日期与任务一一对应**：情绪分、仓位、ETF 等必须与本次执行的日期和任务类型一致，不要张冠李戴。

## 其他

- "情绪监控"、"跑一下监控"、"看看盘面"、"market watch" → 快速模式
- "完整监控"、"full监控"、"全面分析" → 完整模式（含广度+RPS）
- "试运行"、"dry run"、"测试一下" → 试运行模式
- "情绪回测"、"跑回测"、"验证策略" → 情绪验证回测
- "ETF回测"、"轮动回测"、"ETF轮动" → ETF轮动回测
- "今日信号"、"出信号"、"daily signal" → 今日实盘信号
- "RPS选股"、"跑RPS"、"RPS筛选" → rps_system.py --feishu（独立 RPS_50/120 选股）
- "同步TOS"、"上传TOS"、"备份数据" → 上传数据到TOS
- "拉取TOS"、"下载TOS"、"同步云端" → 从TOS下载数据
- "TOS状态"、"查看数据状态"、"数据在哪" → 查看数据同步状态

## 快速模式（默认，约15秒）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --session-only 2>&1 | tee /tmp/rps_runner.log && echo "✅ 情绪监控完成，卡片已推送到群" || echo "❌ 执行失败，日志: $(tail -3 /tmp/rps_runner.log)"
```

## 完整模式（含广度+RPS，约3-5分钟）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --full 2>&1 | tee /tmp/rps_runner.log && echo "✅ 完整监控完成，卡片已推送到群" || echo "❌ 执行失败，日志: $(tail -3 /tmp/rps_runner.log)"
```

## 试运行（不推送飞书，仅验证流程）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --dry-run
```

## 情绪验证回测（含16因子+S5策略+实验套件）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python -m emotion_validation.pipeline 2>&1 | tee /tmp/rps_backtest.log && echo "✅ 回测完成，结果在 backtest/reports/emotion_validation/" || echo "❌ 回测失败，日志: $(tail -3 /tmp/rps_backtest.log)"
```

## ETF轮动回测（S5仓位+月度轮动+闪崩反转/衰竭反转）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python -m emotion_validation.etf_rotation.backtest_rotation 2>&1 | tee /tmp/rps_etf.log && echo "✅ ETF轮动回测完成，结果在 backtest/reports/etf_rotation/" || echo "❌ 回测失败，日志: $(tail -3 /tmp/rps_etf.log)"
```

## 今日实盘信号（含紧急拦截器+反转信号）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python daily_signal.py --feishu 2>&1 | tee /tmp/rps_signal.log && echo "✅ 实盘信号已通过卡片推送到群" || echo "❌ 信号生成失败，日志: $(tail -3 /tmp/rps_signal.log)"
```

## 今日操作指令（14:40 定时任务用，含情绪+ETF轮动+龙回头低吸）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python today_action.py --update --feishu 2>&1 | tee /tmp/rps_action.log && echo "✅ 操作指令已通过卡片推送到群" || echo "❌ 操作指令失败，日志: $(tail -3 /tmp/rps_action.log)"
```

> 内含：情绪判断 → S5 仓位 → ETF 轮动持仓 → 龙回头低吸候选（成交额前 1000 池）

## RPS选股（独立 RPS_50/120 选股，可选）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python rps_system.py --feishu 2>&1 | tee /tmp/rps_select.log && echo "✅ RPS选股已推送到群" || echo "❌ RPS选股失败，日志: $(tail -3 /tmp/rps_select.log)"
```

## 全量Pipeline（含TOS数据同步，约5-8分钟）

```bash
cd /root/workspace/a_-share_-rps_-system && set -a && source tos.env && set +a && source venv/bin/activate && python cloud_runner.py --pipeline --tos 2>&1 | tee /tmp/rps_pipeline.log && echo "✅ 全量Pipeline完成，数据已同步TOS，卡片已推送" || echo "❌ Pipeline失败，日志: $(tail -5 /tmp/rps_pipeline.log)"
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
