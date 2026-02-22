# OpenClaw 每日交易工作流

完整的自动化交易辅助工作流，由 OpenClaw cron 调度器驱动。

## 一、每日时间线

```
  08:30  ┃ 盘前准备     ┃ 从 TOS 拉取最新数据
         ┃              ┃
  09:30  ┃ 开盘         ┃ ─────────────────────
  09:30  ┃              ┃ ┐
   ...   ┃ 盘中监控     ┃ │ 每 30 分钟情绪快扫
  11:30  ┃              ┃ │ (自动跳过午休)
  13:00  ┃              ┃ │
   ...   ┃              ┃ │
  14:30  ┃              ┃ ┘
         ┃              ┃
  14:40  ┃ 核心决策     ┃ 全量 Pipeline + 操作指令卡片
         ┃              ┃   → 情绪分 + S5仓位 + ETF轮动
         ┃              ┃   → 飞书卡片推送
         ┃              ┃
  14:50  ┃ 人工执行     ┃ 根据卡片指令下单 (14:50-14:57)
         ┃              ┃
  15:00  ┃ 收盘         ┃ ─────────────────────
         ┃              ┃
  15:30  ┃ 盘后归档     ┃ 完整监控 + 数据备份到 TOS
         ┃              ┃
  22:00  ┃ 晚间回顾     ┃ 全量 Pipeline (含TOS同步)
```

## 二、Cron 配置命令

在云端服务器上**一次性执行**以下命令即可配好全部定时任务：

```bash
# ══════════════════════════════════════════════
# 1. 盘前准备 — 每天 08:30 从 TOS 拉取最新数据
# ══════════════════════════════════════════════
openclaw cron add \
  --name "盘前-TOS数据同步" \
  --cron "30 8 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "拉取TOS" \
  --announce \
  --channel feishu

# ══════════════════════════════════════════════
# 2. 盘中监控 — 交易时段每 30 分钟快速情绪扫描
# ══════════════════════════════════════════════
openclaw cron add \
  --name "盘中-情绪监控-每30分钟" \
  --cron "*/30 9-15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "情绪监控" \
  --announce \
  --channel feishu

# ══════════════════════════════════════════════
# 3. 核心决策 — 每天 14:40 更新数据+出操作指令
# ══════════════════════════════════════════════
openclaw cron add \
  --name "每日-操作指令-14:40" \
  --cron "40 14 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "跑今日操作" \
  --announce \
  --channel feishu

# ══════════════════════════════════════════════
# 4. 盘后归档 — 每天 15:30 完整监控+数据备份
# ══════════════════════════════════════════════
openclaw cron add \
  --name "盘后-完整监控+备份" \
  --cron "30 15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "完整监控" \
  --announce \
  --channel feishu

openclaw cron add \
  --name "盘后-TOS备份" \
  --cron "45 15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "同步TOS" \
  --announce \
  --channel feishu

# ══════════════════════════════════════════════
# 5. 晚间回顾 — 每天 22:00 跑全量Pipeline
# ══════════════════════════════════════════════
openclaw cron add \
  --name "晚间-全量Pipeline" \
  --cron "0 22 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "跑全套" \
  --announce \
  --channel feishu
```

## 三、任务详情

### 1. 盘前准备 (08:30)

| 项目 | 说明 |
|------|------|
| 触发词 | 拉取TOS |
| 做什么 | 从对象存储下载最新数据（如本地笔记本曾修改过） |
| 耗时 | 10-30 秒 |
| 输出 | OpenClaw 回复同步结果摘要 |

### 2. 盘中监控 (09:30-15:00, 每30分钟)

| 项目 | 说明 |
|------|------|
| 触发词 | 情绪监控 |
| 做什么 | 快速扫描全市场涨跌比、涨停跌停、赚钱效应、ETF量比 |
| 耗时 | 约 15 秒 |
| 输出 | webhook 推送情绪监控卡片到群 |
| 注意 | `--session-only` 内置交易时段判断，午休和非交易时段自动跳过 |

### 3. 核心决策 (14:40) — 最重要

| 项目 | 说明 |
|------|------|
| 触发词 | 跑今日操作 |
| 做什么 | 更新情绪数据 → 计算 S5 仓位 → ETF 轮动选择 → 推送操作指令卡片 |
| 耗时 | 2-5 分钟 |
| 输出 | webhook 推送「今日实盘信号」互动卡片，含仓位建议和 ETF 配置 |
| 人工操作 | 收到卡片后，在 **14:50-14:57** 根据指令完成下单 |

### 4. 盘后归档 (15:30-15:45)

| 项目 | 说明 |
|------|------|
| 15:30 完整监控 | 含广度+RPS 的完整情绪分析，推送汇总卡片 |
| 15:45 TOS备份 | 将今日产生的所有数据上传到对象存储 |

### 5. 晚间回顾 (22:00)

| 项目 | 说明 |
|------|------|
| 触发词 | 跑全套 |
| 做什么 | TOS下载 → 情绪Pipeline → ETF数据 → 轮动回测 → 操作指令 → TOS上传 |
| 耗时 | 5-8 分钟 |
| 目的 | 用完整收盘数据跑一遍，确保次日盘前数据是最新的 |

## 四、手动触发（随时可用）

在飞书对 OpenClaw 说以下关键词即可手动触发：

| 你说的话 | 执行的操作 |
|----------|-----------|
| 出信号 | 出一张实盘信号卡片（不更新数据） |
| 跑今日操作 | 先更新数据再出操作指令卡片 |
| 看看盘面 | 快速情绪监控 |
| 全面分析 | 完整监控（含广度+RPS） |
| 跑全套 | 全量 Pipeline（含 TOS 同步） |
| 同步TOS | 上传当前数据到对象存储 |
| 拉取TOS | 从对象存储下载最新数据 |
| TOS状态 | 查看本地 vs 远端数据对比 |

## 五、管理命令

```bash
# 查看所有定时任务
openclaw cron list

# 立即执行某任务（测试用）
openclaw cron run <job-id> --force

# 暂停某任务
openclaw cron edit <job-id> --enabled false

# 恢复某任务
openclaw cron edit <job-id> --enabled true

# 查看执行历史
openclaw cron runs --id <job-id> --limit 10

# 删除任务
openclaw cron remove <job-id>
```

## 六、前置条件检查清单

在配 cron 之前，确保以下都已就绪：

```bash
# 1. OpenClaw Gateway 运行中
openclaw gateway status

# 2. 飞书已配对
openclaw pairing list

# 3. 技能文件存在
ls ~/workspace/a_-share_-rps_-system/skills/

# 4. 环境变量文件存在
cat ~/workspace/a_-share_-rps_-system/tos.env

# 5. Python 环境可用
cd ~/workspace/a_-share_-rps_-system
source venv/bin/activate
python -c "import akshare; print('akshare', akshare.__version__)"
python -c "from feishu_bot import send_card; print('feishu_bot OK')"

# 6. Webhook 可达
set -a && source tos.env && set +a
python -c "
from feishu_bot import send_text
ok = send_text('🔧 工作流配置测试 — 如果看到这条消息说明 webhook 正常')
print('webhook OK' if ok else 'webhook FAILED')
"
```

全部通过后，复制第二节的 cron 命令执行即可。

## 七、故障排查

| 现象 | 排查 |
|------|------|
| OpenClaw 回复 ❌ | 查看 `/tmp/rps_*.log` 最新日志 |
| 不出卡片 | 检查 `tos.env` 中 `FEISHU_WEBHOOK_URL` 是否正确 |
| 非交易时段也在跑 | 正常，`--session-only` 会自动跳过，秒退 |
| 节假日也在跑 | cron `1-5` 只排除周末，节假日需脚本内 `is_trading_day` 判断 |
| 数据不是最新 | 手动说 "拉取TOS" 从对象存储同步 |
