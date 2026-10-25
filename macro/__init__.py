"""
================================================================================
宏观状态与板块强度模块 (macro)
================================================================================
三个目标:
    1. 市场是否过热      -> HeatScore   (温度轴)
    2. 牛熊变化          -> TrendScore  (趋势轴)
    3. 每周板块强度      -> SectorScore (横截面)

趋势轴 x 温度轴 构成 3x3 九宫格状态机, 输出 regime + 目标仓位。
板块强度输出周度白名单, 供 run_tdx_screener 过滤 L1/L2 结果。

设计文档: docs/strategy_macro_regime.md
"""

from .factors import (
    build_matrices,
    compute_trend_factors,
    compute_breadth_factors,
    compute_heat_factors,
    compute_all_factors,
)
from .regime import (
    RegimeConfig,
    compute_scores,
    classify_regime,
    build_regime_series,
    REGIME_GRID,
)
from .sector import (
    SectorConfig,
    compute_sector_weekly,
    build_sector_whitelist,
)
from .geopolitics import (
    GeoConfig,
    compute_geo_score,
    sector_tilt,
    apply_event_cap,
    print_geo_report,
    GEO_LEVELS,
)

__all__ = [
    "build_matrices",
    "compute_trend_factors",
    "compute_breadth_factors",
    "compute_heat_factors",
    "compute_all_factors",
    "RegimeConfig",
    "compute_scores",
    "classify_regime",
    "build_regime_series",
    "REGIME_GRID",
    "SectorConfig",
    "compute_sector_weekly",
    "build_sector_whitelist",
    "GeoConfig",
    "compute_geo_score",
    "sector_tilt",
    "apply_event_cap",
    "print_geo_report",
    "GEO_LEVELS",
]
