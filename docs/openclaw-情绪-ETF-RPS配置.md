# OpenClaw 情绪判断 + ETF轮动 + RPS选股 配置

## 一、功能与命令对应

| 功能 | 命令 | 输出 |
|------|------|------|
| **情绪判断** | `cloud_runner.py --session-only` | 情绪监控卡片 |
| **ETF轮动** | `today_action.py` 内含 | ETF 持仓建议 |
| **RPS选股** | `today_action.py` 内含（龙回头低吸） | 龙回头候选 + 买点/止损 |
| **独立RPS选股** | `rps_system.py --feishu` | RPS_50/120 选股表 |

**核心命令**：`today_action.py --update --feishu` 一次输出：
- 情绪分 + S5 仓位
- ETF 轮动持仓（消费50ETF、证券ETF 等）
- 龙回头低吸候选（成交额前 1000 池，缩量踩 MA10/MA20）

---

## 二、OpenClaw 配置步骤

### 1. 前置条件

```bash
# OpenClaw Gateway 运行中
openclaw gateway status

# 飞书已配对
openclaw pairing list

# 项目路径（按实际修改）
export PROJECT_DIR="/root/workspace/a_-share_-rps_-system"
# 或 Windows: D:\project\BYD\code\A_Share_RPS_System
```

### 2. 技能文件

技能文件 `skills/openclaw_skill_emotion_monitor.md` 已包含全部触发词，`git pull` 后即可用。

**关键触发词**：

| 你说的话 | 执行 |
|----------|------|
| 跑今日操作 | 情绪+ETF+龙回头，推送到飞书 |
| 情绪监控 | 快速情绪扫描 |
| ETF回测 | ETF 轮动回测 |
| RPS选股 | 独立 RPS_50/120 选股 |

### 3. 定时任务（Cron）

在服务器上执行（**注意修改项目路径**）：

```bash
# 每日 14:40：情绪+ETF轮动+龙回头（核心决策）
openclaw cron add \
  --name "每日-情绪ETF龙回头-14:40" \
  --cron "40 14 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "跑今日操作" \
  --announce \
  --channel feishu

# 盘中每 30 分钟：快速情绪监控
openclaw cron add \
  --name "盘中-情绪监控-每30分钟" \
  --cron "*/30 9-15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "情绪监控" \
  --announce \
  --channel feishu
```

### 4. 项目路径说明

技能文件内命令使用固定路径：`/root/workspace/a_-share_-rps_-system`

- **云端服务器**：确保项目在 `/root/workspace/a_-share_-rps_-system`，或修改技能文件中的路径
- **本地**：OpenClaw workspace 需指向项目根目录

---

## 三、手动触发（飞书对 OpenClaw 说）

| 触发词 | 效果 |
|--------|------|
| 跑今日操作 | 更新数据 → 情绪+ETF+龙回头 → 推送卡片 |
| 出信号 | 今日实盘信号（不更新数据） |
| 看看盘面 | 快速情绪监控 |
| 全面分析 | 完整监控（含广度+RPS） |
| 跑全套 | 全量 Pipeline + TOS 同步 |
| RPS选股 | 独立 RPS 选股表 |
| ETF回测 | ETF 轮动回测 |

---

## 四、管理命令

```bash
openclaw cron list          # 查看所有任务
openclaw cron run <id> --force   # 立即执行测试
openclaw cron edit <id> --enabled false   # 禁用
openclaw cron runs --id <id> --limit 10   # 执行历史
```
