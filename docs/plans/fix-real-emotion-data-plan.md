---
name: 接入真实情绪数据修复回测
overview: 把涨停/跌停/炸板/涨跌比/量比等市场情绪特征从全市场历史日线数据中逐日计算（而非依赖只有2周深度的涨停池API），消除情绪恒定42.5的问题。
status: completed (515个交易日生态特征, 含涨停/跌停/炸板/连板/次日溢价)
---

# 接入真实历史情绪数据，修复回测"常数42.5"问题

## 问题根因

当前 7 个情绪因子所需的输入字段（limit_up_count, broken_limit_count, max_board_height 等）在回测时全部为 0 默认值，导致 emotion_score 恒定 42.5，仓位恒定 20%。

原因：akshare 的 stock_zt_pool_em 等接口只保留近 2 周数据，无法回溯。

## 修复方案

从全市场日线自行计算历史情绪特征：

- limit_up_count: 当日涨跌幅 >= 9.8% 的股票数
- limit_down_count: 当日涨跌幅 <= -9.8% 的股票数
- attempt_limit_up_count: 最高价触涨停但收盘未封住
- broken_limit_count: attempt - limit_up 差值估算
- big_loss_count: 当日跌幅 <= -8% 的股票数
- up_count / down_count / ad_ratio: 涨跌家数及比例
- max_board_height: 滚动涨停追踪法近似

## 实施步骤

1. 新增 `emotion_validation/data_layer/historical_ecology_builder.py`
2. 修改 pipeline 和 backtest 入口自动调用历史构建器
3. 补充连板追踪算法
4. 缓存与增量更新机制
5. 重跑 2024/2025 验证
