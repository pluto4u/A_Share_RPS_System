# Mistery A股趋势交易技能体系

> 从《Mistery趋势交易理论》+《Mistery复盘合集》(共28357行) 蒸馏出 8 个可执行 Claude Code skill。
> 适用于: AI 炒股系统分析股票、看盘面机会、仓位管理、盘后复盘。

## 这是什么

用 [cangjie-skill](https://github.com/kangarooking/cangjie-skill) 方法论, 把交易博主 Mistery (Mi 姐) 的趋势交易体系拆解成 8 个原子化 skill。每个 skill 有明确的触发条件、可执行步骤、边界约束, 可以被 Claude Code agent 在真实交易场景中调用。

## 8 个 Skill

| Skill | 干什么 | 触发词示例 |
|-------|--------|-----------|
| `trend-judgment` | 判断个股/大盘趋势方向 | "趋势怎么样"、"均线排列"、"20日线破没破" |
| `warrior-520` | MA5×MA20 买卖信号 | "金叉了吗"、"回踩20日线"、"什么时候买" |
| `position-management` | 仓位配置+建仓节奏 | "仓位怎么分"、"该买多少"、"金字塔加仓" |
| `sentiment-cycle` | 市场情绪温度 | "涨停多少家"、"炸板率"、"是不是高潮了" |
| `stock-type-classifier` | 情绪票 vs 趋势票分类 | "这票什么类型"、"追涨还是等回调" |
| `smart-money-detector` | 主力行为识别 | "洗盘还是出货"、"放量真假"、"主力意图" |
| `daily-review-sop` | 五层复盘模板 | "今天行情怎么样"、"复盘"、"明天怎么操作" |
| `intraday-signals` | 盘中信号预警 | "量比大不大"、"尾盘急拉"、"盘中信号" |

## 安装

### 方式一: Claude Code 全局安装 (推荐)

```bash
git clone https://github.com/Samuel86-star/trend-judgment.git
cd trend-judgment
cp -r trend-judgment warrior-520 position-management sentiment-cycle \
      stock-type-classifier smart-money-detector daily-review-sop intraday-signals \
      ~/.claude/skills/
```

安装后, 在任何 Claude Code 会话中说 "这票趋势怎么样"、"仓位怎么分"、"帮我复盘" 等, 对应 skill 会自动激活。

### 方式二: 项目级安装

```bash
cd your-project
cp -r /path/to/trend-judgment/{trend-judgment,warrior-520,...} .claude/skills/
```

### 方式三: 直接引用

不需要安装, 把 skill 目录作为参考文档喂给你的 AI agent:
```
参考 skills/trading/INDEX.md 中的技能路由表, 按需调用对应 skill 的 SKILL.md
```

## 推荐调用流程

```
分析个股:  分类 → 趋势 → 买卖点 → 仓位
           stock-type-classifier → trend-judgment → warrior-520 → position-management

盘中看盘:  信号 → 主力意图 → 仓位调整
           intraday-signals → smart-money-detector → position-management

盘后复盘:  五层模板 → 情绪阶段 → 趋势确认 → 仓位计划
           daily-review-sop → sentiment-cycle → trend-judgment → position-management
```

详见 [INDEX.md](INDEX.md) 的引用图。

## 文件结构

```
├── README.md                  # 本文件
├── INDEX.md                   # Skill 总览 + 引用图
├── GLOSSARY.md                # 共享术语词典 (25 条)
├── DIGEST.md                  # 精华长文 (不读全书看这篇)
├── trend-judgment/            # 趋势判断系统
│   ├── SKILL.md
│   └── test-prompts.json
├── warrior-520/               # 520战法
│   ├── SKILL.md
│   └── test-prompts.json
├── position-management/       # 仓位管理
│   ├── SKILL.md
│   └── test-prompts.json
├── sentiment-cycle/           # 情绪周期
│   ├── SKILL.md
│   └── test-prompts.json
├── stock-type-classifier/     # 个股分类
│   ├── SKILL.md
│   └── test-prompts.json
├── smart-money-detector/      # 主力识别
│   ├── SKILL.md
│   └── test-prompts.json
├── daily-review-sop/          # 复盘SOP
│   ├── SKILL.md
│   └── test-prompts.json
├── intraday-signals/          # 盘中信号
│   ├── SKILL.md
│   └── test-prompts.json
├── mistery-trend-trading/     # 阶段0-1审计材料
│   ├── BOOK_OVERVIEW.md
│   ├── verified.md
│   └── candidates/
└── mistery-review-collection/
    └── BOOK_OVERVIEW.md
```

## 每个 SKILL.md 包含什么

- **R (Reading)** — 原文引用 (≤150字)
- **I (Interpretation)** — 方法论骨架 (用自己的话重写)
- **A1 (Past Application)** — 书中实战案例
- **A2 (Future Trigger)** — 什么场景下会用到 + 语言信号
- **E (Execution)** — 可执行步骤 (1-2-3 + 完成标准)
- **B (Boundary)** — 什么时候不适用 + 失败模式 + 作者盲点

## 质量保证

- ✅ 每个 skill 通过三重验证 (跨域佐证 ≥2处 / 预测力 / 独特性)
- ✅ 每个 skill 附带 test-prompts.json (含应调用/不应调用/兄弟skill混淆测试)
- ✅ 保留了完整审计轨迹 (candidates/ + verified.md)

## 限制声明

- 蒸馏自个人交易博主的经验, 非学术研究, 缺乏严格回测数据
- 技术分析有效性在学术界仍有争议 (Fama 1970)
- 内容基于 2025 年 A 股牛市周期, 部分策略有时效性
- **不构成投资建议**, 仅供学习和 AI 系统参考

## 致谢

- 原作者: Mistery (Mi 姐)
- 蒸馏工具: [cangjie-skill](https://github.com/kangarooking/cangjie-skill)
