# TDX / RPS 文档索引

本仓库与通达信（TDX）日线 RPS 选股相关的说明文档如下，文件名统一为英文便于检索与 `@` 引用。

| 文档 | 内容 |
|------|------|
| [tdx_rps_screener_guide.md](tdx_rps_screener_guide.md) | 主入口：数据、脚本、输出 CSV、`csv_to_tdx_blk` 导出板块 |
| [tdx_rps_extension_zhutu.md](tdx_rps_extension_zhutu.md) | RPS 扩展数据（`EXTDATA_USER`）与主图辅助 |
| **[strategy_macro_regime.md](strategy_macro_regime.md)** ★ | **宏观状态与板块强度**（过热判断 / 牛熊九宫格 / 周度板块白名单 → 决定「多少仓位·哪个方向」） |
| **[strategy_daily_sop.md](strategy_daily_sop.md)** ★ | **实战策略 + 每日操作手册**（仓位/买卖规则/决策树） |
| **[portfolio_tracking.md](portfolio_tracking.md)** ★ | **当前持仓 + 候补清单 + 排除清单 + 跟踪日志**（持续更新） |
| [strategy_newcomer.md](strategy_newcomer.md) | 次新股独立策略（上市 < 120 天，量价框架，独立于 RPS） |
| [strategy_early_launch.md](strategy_early_launch.md) | 早期启涨（Early Launch + L1）策略说明 |
| [strategy_breakout_pivot.md](strategy_breakout_pivot.md) | 左边高点突破 / 即将突破（平台突破买点） |
| [strategy_quiet_base.md](strategy_quiet_base.md) | 静默筑底·低位再启动（深跌后缩量横盘 + 站上 MA20 + RPS 拐头，**与 L1 方向相反的低位池**） |
| [strategy_ma20.md](strategy_ma20.md) | 独立 MA20 趋势策略（放量收复 / 缩量回踩 / 数据质量审计） |
| [strategy_hong_zhanfa.md](strategy_hong_zhanfa.md) | 洪战法量化定义（与 Python 实现对应） |
| [tdx_formulas_hong_zhanfa.md](tdx_formulas_hong_zhanfa.md) | 通达信条件选股公式（三套战法 + 融合） |
| [tdx_formulas_hong_chaotianjiao.md](tdx_formulas_hong_chaotianjiao.md) | 朝天椒战法自包含公式 |
| [../learn/tdx_taoboshi_reference.md](../learn/tdx_taoboshi_reference.md) | 淘博士类 8 大条件与公式参考（L1 逻辑对齐） |

运行入口：`run_macro_regime.py`（宏观状态 + 板块强度 + HTML 仪表盘）；`run_tdx_screener.py`（每日扫描）；`run_persistence_scan.py`（窗口聚合 / 月度榜）；`run_early_only.py`（仅早期启涨）；`run_quiet_base.py`（静默筑底低位池）；`run_ma20_screener.py`（独立 MA20 策略）。
