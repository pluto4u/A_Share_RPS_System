---
name: 实盘整合 — 情绪择时 × 选股
overview: 将已验证的 S5 情绪仓位信号与两条选股路径（ETF 轮动 / 个股抖音战法）整合为完整的实盘决策流水线。
status: planning
---

# 实盘整合计划: 情绪择时 × 选股决策

> **REQUIRED SUB-SKILL:** Read and follow `~/.cursor/skills/executing-plans/SKILL.md` to implement task-by-task.

**Goal:** 每个交易日早晨运行一次，输出：今日建议仓位 % + 建议持有的标的列表。

**Architecture:**
```
情绪因子 (pipeline) → S5仓位%
                              ↘
                               整合决策 → 实盘指令
                              ↗
选股引擎 → 候选标的列表
```

**Tech Stack:** pandas, akshare, pyarrow; 已有 emotion_validation/ 各模块

---

## 核心设计原则

- **分离关注点**: 情绪决定"投多少"，选股决定"投什么"，不混用
- **ETF 优先**: 新手/无精力看盘者用 ETF 轮动路径（流动性好、无踩雷风险）
- **个股可选**: 有能力者叠加抖音战法选股，ETF 作为保底持仓
- **S5 是唯一调仓触发**: 不因选股换标的就随意改变总仓位

---

## 两条选股路径对比

| 维度 | ETF 轮动 | 抖音战法个股 |
|------|---------|------------|
| 难度 | 低 | 中高 |
| 持仓数量 | 1-3 只 ETF | 3-8 只个股 |
| 换仓频率 | 月/季 | 周/次 |
| 风险 | 板块风险 | 个股风险 |
| 适合场景 | 主力底仓 | 增强 alpha |
| 实现状态 | 数据层完成，策略层待建 | 模块完整，回测待跑 |

---

## Task 1: ETF 轮动策略层

**Files:**
- Create: `emotion_validation/etf_rotation/rotation_strategy.py`
- Modify: `emotion_validation/etf_rotation/__init__.py`

**轮动规则:**

```
每月最后一个交易日:
  1. 计算所有 ETF 近 20/60 日动量评分
     momentum_score = 0.6 * return_20d + 0.4 * return_60d
  2. 情绪过滤:
     - 情绪 FREEZE (score < 12.5): 全部切换至沪深300ETF或现金
     - 情绪 COLD   (score < 37.5): 只选防御/宽基类 ETF
     - 情绪 NORMAL+: 全池选动量前 N 名
  3. 选出前 2 名 ETF，等权持有
  4. 换仓条件: 新排名 ETF 与当前持仓差异 >= 1 只才换
```

**Step 1: 写核心逻辑**

```python
# emotion_validation/etf_rotation/rotation_strategy.py

class ETFRotationStrategy:
    """
    月度 ETF 动量轮动 + 情绪过滤

    Args:
        top_n: 持有 ETF 数量 (默认 2)
        momentum_fast: 短期动量窗口 (默认 20 天)
        momentum_slow: 长期动量窗口 (默认 60 天)
    """

    def __init__(self, top_n=2, momentum_fast=20, momentum_slow=60):
        self.top_n = top_n
        self.momentum_fast = momentum_fast
        self.momentum_slow = momentum_slow

    def compute_momentum(self, etf_data: dict, as_of_date) -> pd.DataFrame:
        """计算截止 as_of_date 的各 ETF 动量得分"""
        ...

    def filter_by_emotion(self, candidates: pd.DataFrame, emotion_score: float) -> pd.DataFrame:
        """根据情绪分数过滤候选池"""
        ...

    def get_holdings(self, etf_data: dict, emotion_score: float, as_of_date) -> list:
        """返回建议持仓 ETF 列表 [{symbol, name, sector, weight}]"""
        ...
```

**Step 2: 回测跑一遍 2024-2026**

```python
# emotion_validation/etf_rotation/backtest_rotation.py
# 输出: backtest/reports/etf_rotation/rotation_backtest.csv
# 指标: 年化、最大回撤、Sharpe，与沪深300 ETF 纯买持对比
```

**Step 3: 整合进 daily_signal.py**

在 `daily_signal.py` 末尾加入 `--etf` 参数，输出今日建议持有的 ETF。

---

## Task 2: 跑抖音战法完整回测

**Files:**
- Run: `python -m emotion_validation.strategy_layer.douyin_system.run_backtest`
- Output: `backtest/reports/douyin/`

**执行命令:**
```bash
python -m emotion_validation.strategy_layer.douyin_system.run_backtest \
  --start-date 2024-01-01 \
  --end-date 2026-02-14 \
  --strategies A,B,C,D \
  --initial-capital 1000000
```

**验收指标:**
- 总收益 vs 沪深300 (-15.7% max DD baseline)
- 胜率 (目标 > 55%)
- 盈亏比 (目标 > 1.5)
- 最优单策略 (A/B/C/D 哪个最强)

---

## Task 3: 整合决策脚本

**Files:**
- Create: `live_decision.py` (项目根目录)

**每日一键运行输出格式:**

```
============================================================
  实盘决策参考  |  2026-02-19
============================================================

  【仓位】S5 建议: 75%  (当前: 75%，无需调仓)

  【ETF 底仓】 (占总仓位 75%)
    1. 512480 半导体ETF  —  动量评分 8.3  (权重 50%)
    2. 159915 创业板ETF  —  动量评分 7.1  (权重 50%)

  【个股增强】 (可选，叠加在 ETF 底仓之上)
    今日抖音战法信号: [无信号] / [策略B: 000001.SZ 一进二]

  【操作摘要】
    今日无需调仓
    ETF 持仓无变化 (上次换仓: 2026-02-05)

============================================================
```

---

## Task 4: 飞书推送整合

**Files:**
- Modify: `feishu_bot.py` — 加入 S5 仓位 + ETF 建议卡片

**推送内容:**
- 情绪分 + 档位 + S5 仓位建议
- ETF 轮动持仓建议 (若换仓则高亮)
- 个股信号 (若有)

---

## 优先级与时间估计

| Task | 优先级 | 工作量 | 状态 |
|------|--------|--------|------|
| Task 1: ETF 轮动策略层 | P0 | ~3小时 | 待开始 |
| Task 2: 抖音回测执行 | P0 | ~1小时 | 待执行 |
| Task 3: 整合决策脚本 | P1 | ~2小时 | 待开始 |
| Task 4: 飞书推送整合 | P2 | ~2小时 | 待开始 |

**建议执行顺序:** Task 2 → Task 1 → Task 3 → Task 4
(先拿到两条路径的数据，再整合输出)
