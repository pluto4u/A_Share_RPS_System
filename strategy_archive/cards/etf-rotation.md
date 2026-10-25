# ETF动量与情绪过滤

- ID：etf-rotation
- 组件：signals
- 状态：implemented_requires_data
- 规则：实际默认快5日/慢10日，权重各50%，Top2；情绪收窄候选池
- 限制：注释中的20/60日不是当前默认；需ETF行情和行业分类。
- 来源：emotion_validation/etf_rotation/rotation_strategy.py
- 源码SHA256：9a00955bb10094a4eae5bc52dc8661c75cb45308ca6380c6e1d4c6a93ff3cb37

本次不把代码存在、单测通过或旧收益文字视为实盘有效性证明。
