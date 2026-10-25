# 实时情绪评分与仓位门控

- ID：sentiment-gate
- 组件：signals
- 状态：implemented_requires_data
- 规则：评分映射0/10/30/60/80%，取趋势门控与宽度上限的最小值
- 限制：与S5仓位模型不同，不能混用评分阈值。
- 来源：sentiment_engine.py
- 源码SHA256：67aa312828dacf6945a8add6c4cf8c563b4e5c22af5a2990345db7df15f92f7f

本次不把代码存在、单测通过或旧收益文字视为实盘有效性证明。
