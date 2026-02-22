# OpenClaw 定时任务配置方案

根据 OpenClaw 手册，使用 Gateway 内置 Cron 调度器实现定时执行。

## 需求

| 任务 | 执行时刻 | 说明 |
|------|----------|------|
| 每日情绪判断 + 选股 | 每天 14:40 | 固定时刻，跑完整 pipeline |
| 交易时段选股 | 交易时间每 10 分钟 | 9:30-11:30、13:00-15:00 |

## 前置条件

1. OpenClaw Gateway 已启动：`openclaw gateway` 或 systemd 服务运行中
2. 飞书已配对：`openclaw pairing approve feishu <code>`
3. 技能已配置：`skills/openclaw_skill_emotion_monitor.md` 在项目根目录下（git 管理）
4. 项目路径：`/root/workspace/a_-share_-rps_-system`
5. TOS 环境变量：`/etc/a_share_rps/tos.env` 已创建并包含 AK/SK/ROLE 等

## 技能关键词映射（需与 cron 消息一致）

| 消息内容 | 触发命令 |
|----------|----------|
| 跑全套 / 全量验证 / full pipeline | cloud_runner.py --pipeline --tos（含TOS同步） |
| 今日信号 / 出信号 / daily signal | daily_signal.py |
| 情绪监控 / 跑一下监控 / 看看盘面 | cloud_runner.py --session-only（快速，约 15 秒） |
| 同步TOS / 上传TOS / 备份数据 | tos_storage.py sync-up --force |
| 拉取TOS / 下载TOS / 同步云端 | tos_storage.py sync-down |
| TOS状态 / 查看数据状态 / 数据在哪 | tos_storage.py status |

---

## 配置步骤

在**服务器**上执行以下命令（时区 `Asia/Shanghai`）。

### 1. 每日 14:40：情绪判断 + 选股

**前提**：技能中需有「跑今日操作」触发词（见下方「技能配置」）。

```bash
openclaw cron add \
  --name "每日情绪选股-14:40" \
  --cron "40 14 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "跑今日操作" \
  --announce \
  --channel feishu
```

> 消息「跑今日操作」由 Agent 匹配技能后执行 `today_action.py --update`。若技能尚未配置，可先用「跑全套」触发 pipeline，再手动运行 `today_action.py`。

### 2. 交易时段每 10 分钟：选股监控

```bash
openclaw cron add \
  --name "交易时段选股-每10分钟" \
  --cron "*/10 9-15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "情绪监控" \
  --announce \
  --channel feishu
```

说明：
- `*/10 9-15 * * 1-5`：工作日 9:00–15:50 每 10 分钟执行
- `cloud_runner.py --session-only` 内部会判断 `is_trading_session`，非交易时段会直接跳过
- 9:00、9:10、9:20 及 11:40–12:50 等非交易时段会快速退出，无实际计算

---

## 管理命令

```bash
# 查看所有任务
openclaw cron list

# 立即执行某任务（测试用）
openclaw cron run <job-id> --force

# 编辑任务
openclaw cron edit <job-id> --message "新消息"

# 查看运行历史
openclaw cron runs --id <job-id> --limit 20

# 禁用任务
openclaw cron edit <job-id> --enabled false
```

---

## 技能配置

`skills/openclaw_skill_emotion_monitor.md` 已包含以下触发词：
- 「跑今日操作」→ `today_action.py --update`
- 「跑全套」→ `cloud_runner.py --pipeline --tos`（含 TOS 数据同步）
- 「同步TOS」→ `tos_storage.py sync-up --force`
- 「拉取TOS」→ `tos_storage.py sync-down`
- 「TOS状态」→ `tos_storage.py status`

技能文件位于项目 `skills/` 目录，git pull 后即可用。OpenClaw workspace 指向项目根目录时会自动识别。所有命令已内置 `source /etc/a_share_rps/tos.env` 和 `source venv/bin/activate`。

---

## Cron 表达式参考（Asia/Shanghai）

| 需求 | 表达式 | 说明 |
|------|--------|------|
| 每天 14:40 | `40 14 * * 1-5` | 工作日 14:40 |
| 每 10 分钟（9–15 点） | `*/10 9-15 * * 1-5` | 工作日 9:00–15:50 |
| 仅上午每 10 分钟 | `*/10 9-11 * * 1-5` | 9:00–11:50 |
| 仅下午每 10 分钟 | `*/10 13-15 * * 1-5` | 13:00–15:50 |

---

## 注意事项

1. **投递目标**：`--channel feishu` 需在 OpenClaw 中已配置 feishu 插件，`--to` 不填时使用默认会话。
2. **隔离执行**：`--session isolated` 表示每次在独立会话中运行，不影响主会话。
3. **并发**：Gateway 默认 `maxConcurrentRuns: 1`，同一时刻只跑一个 cron 任务。
4. **交易日**：`1-5` 为周一到周五，节假日不自动排除，需在脚本内用 `is_trading_day` 判断。
