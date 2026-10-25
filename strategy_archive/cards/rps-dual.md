# 双周期RPS选股

- ID：rps-dual
- 组件：signals
- 状态：implemented_requires_data
- 规则：50日RPS>90，120日RPS>85；沪深300 MA20门控；市值至少30亿；历史至少120日
- 限制：入口使用akshare网络数据；本地通达信工作流目前仅提供宽度，不宣称已接通RPS输入。
- 来源：rps_system.py
- 源码SHA256：ca8e1e717f178a39878b1c9d98089dcc650037a44b980919f96bfb0ac8f1c5cf

本次不把代码存在、单测通过或旧收益文字视为实盘有效性证明。
