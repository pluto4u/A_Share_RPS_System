# OpenCode 实战使用指南

> 环境：2GB 内存云服务器 + 火山引擎 CodingPlan Lite + A_Share_RPS_System 项目

## 一、两种启动方式

已配好的两个别名：

- **`oc`** -- TUI 交互模式，有界面，适合多轮对话、复杂任务
- **`ocr '你的问题'`** -- 单次问答模式，无 TUI，更省内存，适合快速提问

在 2GB 服务器上，**优先用 `ocr`**，只在需要多轮对话时才用 `oc`。

---

## 二、两个核心 Agent

按 `Tab` 在 Build 和 Plan 之间切换：

### Build Agent（动手干活）

- 能读写文件、执行命令、搜索代码
- 适合：写功能、修 bug、跑测试、重构
- 示例：

```
oc                # 进入 TUI
> 在 risk_engine.py 中添加最大回撤计算函数，包含单元测试
```

### Plan Agent（先想后做）

- 只读分析，输出实施计划，不直接改代码
- 适合：大型重构、不确定怎么改的场景
- 示例：

```
oc                # 进入 TUI，Tab 切到 Plan
> 分析 market_gambler_watch.py，规划拆分成独立模块的方案
```

Plan 给出方案后，你确认，再 Tab 切回 Build 让它执行。

---

## 三、常用命令

在 TUI 内输入以下命令（以 `/` 开头）：

| 命令 | 作用 |
|------|------|
| `/models` | 切换模型 |
| `/clear` | 清空当前会话（释放内存） |
| `/compact` | 手动压缩上下文（对话太长时用） |
| `/exit` | 退出 |

快捷键：

| 操作 | 快捷键 |
|------|--------|
| 切换 Build / Plan | `Tab` |
| 中断当前回答 | `Esc` |
| 退出 | `Ctrl+C` |
| 强制退出（卡住时） | `Ctrl+\` |
| 查看子任务 | `Ctrl+X` 然后 `→` |

---

## 四、2GB 服务器避坑指南

### 规则 1：给具体指令，不给开放指令

```
BAD:  "分析这个项目"          --> 会触发 Explore Task 遍历所有文件，吃内存
GOOD: "读取 risk_engine.py 并解释 calculate_risk 函数的逻辑"
```

### 规则 2：对话别太长，定期清理

```
> /compact          # 压缩上下文
> /clear            # 或直接清空开新会话
```

### 规则 3：一次只做一件事

```
BAD:  "重构所有文件并添加测试和文档"
GOOD: "给 sentiment_engine.py 的 calculate_sentiment 函数添加单元测试"
```

### 规则 4：卡住了别慌

1. 先按 `Esc`
2. 不行按 `Ctrl+C`
3. 还不行按 `Ctrl+\`
4. 实在不行：新终端 SSH 上去 `pkill -9 -f opencode`

---

## 五、实用工作流

### 工作流 1：用 `ocr` 做代码审查（最省资源）

```bash
ocr "审查以下代码并指出问题: $(cat risk_engine.py)"
```

### 工作流 2：用 Build 写功能

```bash
oc
> 在 backtest_sentiment_daily.py 中添加夏普比率计算，输出到回测报告
```

### 工作流 3：用 Plan 做架构设计

```bash
oc          # Tab 切到 Plan
> market_gambler_watch.py 有 1141 行太长了，规划如何拆分成多个模块
```

### 工作流 4：结合 Cursor 使用

```
Cursor (本地)  -- 日常编码、浏览代码、用 AI 辅助编辑
OpenCode (云端) -- 需要执行 shell 命令的任务（跑测试、部署、数据处理）
```

两者不冲突，OpenCode 在服务器上可以直接操作你的运行环境。

---

## 六、与 Cursor 的分工

| 场景 | 用 Cursor | 用 OpenCode |
|------|-----------|-------------|
| 日常编码 | 是 | - |
| 需要跑命令/测试 | - | 是 |
| 代码审查 | 是 | `ocr` |
| 服务器部署/运维 | - | 是 |
| 大型重构规划 | Plan 模式也行 | Plan Agent |
| 多文件编辑 | 是（更方便） | Build Agent |

---

## 七、配置参考

配置文件位置：`~/.config/opencode/opencode.json`
认证文件位置：`~/.local/share/opencode/auth.json`

当前配置的关键参数：

| 参数 | 值 | 作用 |
|------|-----|------|
| `Swap 2G` | 系统层 | 内存不够时用磁盘兜底，防止 OOM 崩溃 |
| `max-old-space-size=512` | Node.js 层 | 限制 OpenCode 最多吃 512MB 内存 |
| `timeout: 30000` | API 层 | 单个请求最多等 30 秒，超时自动断开 |
| `context: 32000` | 模型层 | 限制上下文窗口，减少内存占用 |
| `output: 8000` | 模型层 | 限制输出长度，避免超长回复撑爆内存 |
| `compaction.prune: true` | OpenCode 层 | 自动清理旧的工具输出，节省 token |
| `autoupdate: false` | OpenCode 层 | 关闭自动更新，启动时少一次网络请求 |
