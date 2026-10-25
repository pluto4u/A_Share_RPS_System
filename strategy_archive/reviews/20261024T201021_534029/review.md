# 策略卡源码同步审阅

审阅时间：2026-10-24T20:10:21.534029+08:00

旧卡、注册表保持原样。变更源码另存内容哈希快照；本报告不是收益验收。
只核对每张卡的主源文件，不覆盖全部依赖、配置和数据变更。

## 卡片源码状态

- market-breadth / 通达信市场宽度与内容出图：unchanged
- rps-dual / 双周期RPS选股：unchanged
- sentiment-gate / 实时情绪评分与仓位门控：unchanged
- risk-overlay / 三层风险控制：unchanged
- daily-s5 / S5每日信号：unchanged
- s0 / S0持有基准：unchanged
- s1 / S1区间切换：unchanged
- s2 / S2极恐退出：unchanged
- s3 / S3极恐与反转：unchanged
- s4 / S4情绪均线趋势：unchanged
- s5 / S5线性节流对比版：unchanged
- etf-rotation / ETF动量与情绪过滤：unchanged
- etf-a / A纯ETF轮动：unchanged
- etf-b / B沪深300ETF持有基准：unchanged
- etf-c / C S5仓位×沪深300ETF：unchanged
- etf-d / D S5仓位×ETF轮动：unchanged
- dragon / 龙回头双均线缩量回踩：unchanged
- pocket-v1 / 口袋支点v1：unchanged
- pocket-v2 / 口袋支点v2：unchanged
- trend-pullback / 强趋势均线缩量回踩：unchanged
- cup-v1 / 杯柄形态v1：unchanged
- cup-v2 / 杯柄形态v2：unchanged
- douyin-a / 短线A 放量突破前高：unchanged
- douyin-b / 短线B 一进二：unchanged
- douyin-c / 短线C 横盘突破涨停：unchanged
- douyin-d / 短线D 强势股回调确认：unchanged
- dragon-search / 龙回头V0–V7及深度参数搜索：unchanged
- panel-active_limit_gene / 涨停基因活跃股：unchanged
- panel-boll_breakout / 布林突破：unchanged
- panel-breakout_new_high_60d / 放量创60日新高：unchanged
- panel-broken_board_recovery / 断板反包：unchanged
- panel-bullish_alignment / 均线多头：unchanged
- panel-consecutive_limit_ups / 连板股：unchanged
- panel-factor_rank_research / 因子排名研究：unchanged
- panel-high_turnover_surge / 高换手拉升：unchanged
- panel-limit_up_momentum / 连板接力：unchanged
- panel-long_lower_shadow_reversal / 长下影反击：unchanged
- panel-low_volatility_leader / 低波动龙头：unchanged
- panel-ma_convergence_breakout / 均线粘合突破：unchanged
- panel-ma_golden_cross / MA 金叉：unchanged
- panel-macd_below_zero_revival / MACD 零下回升：unchanged
- panel-macd_golden / MACD 金叉放量：unchanged
- panel-n_day_low_reversal / 新低反转：unchanged
- panel-near_limit_up / 逼近涨停：unchanged
- panel-oversold_bounce / 超跌反弹：unchanged
- panel-oversold_reversal / 超跌反转：unchanged
- panel-platform_consolidation_breakout / 平台整理突破：unchanged
- panel-pullback_ma20_bounce / 均线回踩反弹：unchanged
- panel-pullback_to_support / 缩量回踩：unchanged
- panel-rsi_midline_pullback / RSI 中轴回踩：unchanged
- panel-strong_open / 强势高开：unchanged
- panel-trend_breakout / 趋势突破：unchanged
- panel-volume_price_surge / 量价齐升：unchanged

## 需新增或单独审阅

- tdx-rps-local：旧rps-dual卡对应网络入口，不代表本地120/250日RPS；需独立卡。
- market-timing：属于环境层；事件统计不等同可交易组合回测。
- industry-rps：当前行业映射不是历史成分；历史回测需时点行业或明确标记偏差。
- trend-start：可复用信号和执行框架，仍须逐日前缀和交易约束验收。

## 人工规则复核重点

- daily-s5：实现已调用Kelly波动率缩放与回撤弹性，旧卡的纯S5描述不足。
- rps-dual：网络50/120日入口与本地120/250日入口分开，不沿用同一策略身份。
- 杯柄：居中窗口须延迟到形态真正可知日，逐日前缀测试通过后再回测。
- 市场宽度出图与择时研究的市场范围、价格口径和指标分母不同，禁止混拼序列。

回测协议见 docs/REBACKTEST-PLAN-20261024.md。
