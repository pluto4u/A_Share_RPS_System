---
name: zipline情绪验证工程
overview: 在当前仓库内新增模块化的 `emotion_validation` 分层实现（以 zipline 为主框架），并桥接现有情绪/回测脚本，完成可复现的多实验验证与自动化报告输出。
status: completed (F1-F7因子引擎 + 回测模拟器 + 4类验证实验 + 报告输出)
---

# Zipline情绪验证落地计划

## 目标与验收

- 在现有仓库中实现一套可回测、可复现、可自动运行的情绪验证工程，验证情绪指标是否提升策略统计表现（胜率、回撤、盈亏比、夏普/卡玛）。
- 以 `Emotion(T-1) -> Return(T)` 为时序约束，避免未来函数。
- 形成两类输出：
  - 研究输出：分区对比、开关对比、滞后相关、AUC、稳健性测试。
  - 生产输出：每日情绪分数、regime、策略开关、建议仓位、飞书推送内容。

## 模块化目录与职责

- `emotion_validation/data_layer/`：行情与涨停生态、板块强度、强势股行为数据清洗与对齐。
- `emotion_validation/emotion_engine/`：F1-F7 因子计算、标准化、加权评分、regime 分类。
- `emotion_validation/strategy_layer/`：固定基准策略 S（选股、入场、出场）与策略开关接口。
- `emotion_validation/backtest_engine/`：zipline 适配器、交易记录、绩效指标（含回撤恢复/连亏）。
- `emotion_validation/validation/`：4类核心实验 + 周期验证 + 稳健性测试。
- `emotion_validation/reporting/`：研究报告与飞书数据载荷生成。
- `emotion_validation/config.yaml`：参数、阈值、回测区间、实验开关。

## 实施步骤

1. 数据层与特征字典落地
2. 情绪因子引擎实现（F1-F7）
3. zipline 回测桥接固定策略 S
4. 验证实验实现（regime分组 / A-B对比 / 滞后预测 / ROC-AUC）
5. 稳健性与防过拟合（阈值扰动、时间分段）
6. 自动化与对接现有系统
7. 测试与可复现性

## 关键数据流

```
rawMarketData --> dataLayer --> emotionFactors(F1-F7) --> emotionScore+Regime
                                                              |
fixedBaseStrategyS --> ziplineBacktestEngine <-- strategySwitchAndSizing
                              |
                     validationSuite --> reportOutput+FeishuPayload
```
