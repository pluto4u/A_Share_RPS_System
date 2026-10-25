"""
================================================================================
中美对抗因子 (Geopolitical Confrontation Factor)
================================================================================
为什么需要这个模块:

    原有的宏观体系只有两根轴——趋势与温度——它们都是从价格数据里长出来的,
    因此天然只能描述「市场已经表现出来的状态」。但 2026 年 6 月以来中美摩擦
    从关税转向科技供应链与实体清单, 这类冲击有三个特点让价格因子抓不住:

      1. **离散**：它不是连续变量, 是一次一次的公告。7/28 FCC 禁令、7/31 涉疆
         清单扩容 43 家、8/5 中方一天五项反制——每次都是跳变。
      2. **非对称**：它不像利率那样均匀作用于全市场, 而是精确地打击特定板块,
         同时抬升另一批板块。指数层面可能看不出来, 板块层面天翻地覆。
      3. **有日程**：9 月元首会晤这类事件的时间是**已知**的, 结果是未知的。
         这意味着可以提前管理敞口, 而趋势/温度轴对未来事件完全无知。

设计:

    G1 事件强度 EventIntensity  ← 事件日志 + 指数衰减（人工维护, 显式可审计）
    G2 对抗溢价 MarketPremium   ← 自主可控篮子 vs 出口敞口篮子的相对强度
                                  （从价格算, 反映市场**已经**定价了多少对抗）
    G3 事件窗口 EventWindow     ← 距离已知日程事件的天数

    合成 GeoScore (0-100, 越高越对抗)

作用方式 — 刻意**不**做成九宫格的第三维:

    三维网格 = 27 个状态, 每格样本太少, 无法验证, 实盘也记不住。
    改为两个正交的作用点:

      A. 板块倾斜 (sector tilt)   — 调整板块白名单的打分, 对抗升级时压低出口链、
                                    抬升自主可控。这是**结构性**的, 与仓位无关。
      B. 事件窗口封顶 (event cap) — 已知重大事件前 N 日, 对目标仓位设上限。
                                    这不是预测方向, 而是承认「方向不可预测时
                                    应该降低敞口」。

    G1/G2 的背离本身也是信号: 事件强度高但市场溢价低 = 市场尚未定价, 风险未释放;
    反之 = 已充分定价, 边际利空钝化。

数据: macro/geo_events.json（事件日志与篮子定义）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_EVENTS = Path(__file__).with_name("geo_events.json")


# =============================================================================
# 配置
# =============================================================================

@dataclass
class GeoConfig:
    """对抗因子参数"""

    # --- G1 事件强度 ---
    decay_halflife_days: float = 21.0   # 事件影响的半衰期（约 1 个月）
    #  为什么是 21 天: A 股对单一政策事件的定价通常在 3-6 周内完成。
    #  设太短会让因子变成脉冲噪声, 太长则失去时效性。
    severity_scale: float = 4.0          # severity 1~5 映射到强度的底数
    deescalate_weight: float = -1.0      # 缓和事件按负强度计入
    event_saturation: float = 1.5        # G1 饱和常数, 见 compute_geo_score 中的说明

    # --- G2 市场隐含溢价 ---
    premium_window: int = 20             # 相对强度回看窗口（交易日）
    premium_smooth: int = 5

    # --- G3 事件窗口 ---
    default_pre_window: int = 5          # 日程事件默认提前进入观察窗口的天数

    # --- 合成权重 ---
    w_event: float = 0.45
    w_premium: float = 0.40
    w_window: float = 0.15

    # --- 作用强度 ---
    tilt_max: float = 12.0               # 板块分最大加/减分
    event_cap_position: float = 0.60     # 重大事件窗口内的仓位上限
    #  为什么是 60% 而不是更低: 事件窗口封顶的目的是控制尾部风险, 不是清仓。
    #  9 月峰会大概率是「止损峰会」而非崩盘触发器, 过度降仓会错失缓和情景。

    # --- 分档阈值 ---
    calm_below: float = 35.0
    tense_above: float = 65.0


GEO_LEVELS = {
    "calm": dict(name="缓和", color="#16a34a",
                 note="对抗强度低。板块选择可回归纯基本面, 出口链与自主可控无需区别对待。"),
    "friction": dict(name="摩擦", color="#b45309",
                     note="常态摩擦。出口链适度降权, 自主可控适度加权, 但不构成主要矛盾。"),
    "tense": dict(name="对抗", color="#dc2626",
                  note="对抗升级。出口敞口板块显著降权; 重大事件窗口内对仓位封顶。"),
}


# =============================================================================
# 事件日志
# =============================================================================

def load_events(path: Path = DEFAULT_EVENTS) -> Dict:
    """读取事件日志。文件缺失时返回空结构, 整套体系降级为无地缘因子。"""
    path = Path(path)
    if not path.exists():
        print(f"[Geo] 事件日志不存在: {path}，对抗因子将被跳过")
        return {"events": [], "scheduled": [], "baskets": {}, "stock_lists": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("events", [])
    data.setdefault("scheduled", [])
    data.setdefault("baskets", {})
    data.setdefault("stock_lists", {})
    return data


def event_intensity(
    events: List[Dict],
    dates: pd.DatetimeIndex,
    config: Optional[GeoConfig] = None,
) -> pd.Series:
    """
    G1 — 事件强度时间序列。

    每个事件在发生日注入 severity^? 的冲击, 之后按半衰期指数衰减;
    多个事件叠加。缓和方向的事件（如峰会达成协议）按负强度计入,
    因此一次成功的峰会可以把累积的对抗强度显著压下去。

    返回未归一化的原始强度（>=0 部分有意义, 负值表示净缓和）。
    """
    cfg = config or GeoConfig()
    if not events:
        return pd.Series(0.0, index=dates)

    lam = np.log(2) / max(1e-9, cfg.decay_halflife_days)
    out = pd.Series(0.0, index=dates, dtype="float64")
    day_num = np.array([(d - dates[0]).days for d in dates], dtype="float64")

    for ev in events:
        try:
            ed = pd.Timestamp(ev["date"])
        except Exception:
            continue
        sev = float(ev.get("severity", 2))
        sign = cfg.deescalate_weight if ev.get("direction") == "deescalate" else 1.0
        amp = sign * (sev / cfg.severity_scale) ** 1.5

        e_num = (ed - dates[0]).days
        delta = day_num - e_num
        contrib = np.where(delta >= 0, amp * np.exp(-lam * np.maximum(delta, 0)), 0.0)
        out += contrib

    return out


def market_premium(
    df_daily: pd.DataFrame,
    industry_map: pd.DataFrame,
    baskets: Dict,
    stock_lists: Optional[Dict] = None,
    config: Optional[GeoConfig] = None,
    verbose: bool = True,
) -> pd.Series:
    """
    G2 — 市场隐含对抗溢价。

    = 自主可控篮子 N 日收益 − 出口敞口篮子 N 日收益

    解读:
      上行 = 市场正在为「对抗升级」定价（买国产替代、卖出口链）
      下行 = 市场正在为「缓和」定价

    这个因子的价值在于它是**市场的观点**, 与 G1（事实的强度）形成交叉验证:
      G1 高而 G2 低 → 事件已发生但市场尚未定价, 利空未释放
      G1 高而 G2 高 → 已充分定价, 边际利空钝化, 反而可能是反向布局窗口
    """
    cfg = config or GeoConfig()
    stock_lists = stock_lists or {}

    df = df_daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]

    def _basket_codes(key: str) -> List[str]:
        explicit = stock_lists.get(key) or []
        if explicit:
            return [c for c in explicit if c in set(df["ts_code"])]
        inds = (baskets.get(key) or {}).get("industries") or []
        if not inds:
            return []
        # 支持前缀匹配（T10 匹配 T10xx 二级行业）
        mask = ind.astype(str).str.startswith(tuple(inds))
        return ind[mask].index.tolist()

    a_codes = _basket_codes("autonomy")
    e_codes = _basket_codes("export_exposed")

    if verbose:
        print(f"[Geo] 自主可控篮子 {len(a_codes)} 只 / 出口敞口篮子 {len(e_codes)} 只")

    if not a_codes or not e_codes:
        if verbose:
            print("[Geo] 篮子为空, 市场溢价因子跳过（检查 tdxhy.cfg 与 baskets 配置）")
        dates = pd.DatetimeIndex(sorted(df["trade_date"].unique()))
        return pd.Series(np.nan, index=dates)

    close = df.pivot_table(index="trade_date", columns="ts_code",
                           values="close", aggfunc="last")
    n = cfg.premium_window
    ret = close / close.shift(n) - 1.0
    ret = ret.where(ret.abs() <= 3.0)      # 剔除未复权跳空

    a = ret[[c for c in a_codes if c in ret.columns]].median(axis=1, skipna=True)
    e = ret[[c for c in e_codes if c in ret.columns]].median(axis=1, skipna=True)

    prem = (a - e).rolling(cfg.premium_smooth, min_periods=1).mean()
    prem.name = "G2_market_premium"
    return prem


def event_window(
    scheduled: List[Dict],
    dates: pd.DatetimeIndex,
    config: Optional[GeoConfig] = None,
) -> pd.DataFrame:
    """
    G3 — 已知日程事件窗口。

    返回 DataFrame: in_window(bool), days_to_event(int), next_event(str), kind(str)

    这是整套体系里唯一**前瞻**的部分。趋势轴和温度轴都只描述过去,
    而重大事件的日期是已知的——不利用这个信息是浪费。
    注意: 它不预测事件结果, 只在结果不可预测的窗口内降低敞口。
    """
    cfg = config or GeoConfig()
    out = pd.DataFrame(index=dates)
    out["in_window"] = False
    out["days_to_event"] = np.nan
    out["next_event"] = ""
    out["event_kind"] = ""
    # 关键: 分 kind 单独跟踪「下一个同类事件」。
    # 若只取全局最近的事件, 一个临近的货币政策事件会把 39 天后的元首会晤
    # 完全遮住, 导致地缘窗口因子恒为 0 —— 自测时就踩到了这个坑。
    out["days_to_geo"] = np.nan
    out["next_geo_event"] = ""
    out["in_geo_window"] = False

    if not scheduled:
        return out

    items = []
    for s in scheduled:
        try:
            items.append((pd.Timestamp(s["date"]), s))
        except Exception:
            continue
    items.sort(key=lambda x: x[0])
    geo_items = [(d, s) for d, s in items if s.get("kind") == "geo"]

    for d in dates:
        future = [(ed, s) for ed, s in items if ed >= d]
        if future:
            ed, s = future[0]
            gap = (ed - d).days
            pre = int(s.get("pre_window_days", cfg.default_pre_window))
            out.loc[d, "days_to_event"] = gap
            out.loc[d, "next_event"] = s.get("title", "")
            out.loc[d, "event_kind"] = s.get("kind", "")
            if 0 <= gap <= pre:
                out.loc[d, "in_window"] = True

        gfuture = [(ed, s) for ed, s in geo_items if ed >= d]
        if gfuture:
            ed, s = gfuture[0]
            gap = (ed - d).days
            pre = int(s.get("pre_window_days", cfg.default_pre_window))
            out.loc[d, "days_to_geo"] = gap
            out.loc[d, "next_geo_event"] = s.get("title", "")
            if 0 <= gap <= pre:
                out.loc[d, "in_geo_window"] = True

    return out


# =============================================================================
# 合成
# =============================================================================

def _scale(s: pd.Series, lo: float, hi: float) -> pd.Series:
    if hi == lo:
        return pd.Series(50.0, index=s.index)
    return ((s - lo) / (hi - lo) * 100.0).clip(0, 100)


def compute_geo_score(
    df_daily: pd.DataFrame,
    industry_map: Optional[pd.DataFrame] = None,
    events_path: Path = DEFAULT_EVENTS,
    config: Optional[GeoConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    合成 GeoScore。

    返回 DataFrame, index=trade_date, 列:
        G1_event_intensity  原始事件强度
        G2_market_premium   市场隐含溢价（自主可控 − 出口链, 20日收益差）
        G3_in_window        是否处于重大事件窗口
        g1_score / g2_score / g3_score   各自 0-100
        geo_score           合成 0-100（越高越对抗）
        geo_level           calm / friction / tense
        days_to_event, next_event
    """
    cfg = config or GeoConfig()
    data = load_events(events_path)

    df = df_daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    dates = pd.DatetimeIndex(sorted(df["trade_date"].unique()))

    out = pd.DataFrame(index=dates)
    out.index.name = "trade_date"

    # --- G1 ---
    g1 = event_intensity(data["events"], dates, cfg)
    out["G1_event_intensity"] = g1
    # 用饱和函数而非线性锚点。
    # 线性锚点在这里不合适: 事件强度是叠加量, 密集期（如 2026 年 7-8 月, 三周内
    # 四个 severity-4 事件）可以轻松冲到 4+, 而常态摩擦只有 0.5-0.8。线性映射
    # 会让常态期全挤在 0-20 分、密集期一律截断到 100, 两头都没有分辨率。
    #   0    → 0      (完全平静)
    #   0.7  → 37     (常态摩擦, 一个季度一两件 S3)
    #   1.5  → 63     (明显升级)
    #   3.0  → 86
    #   4.3  → 94     (2026 年 8 月初的水平)
    out["g1_score"] = (100.0 * (1.0 - np.exp(-g1.clip(lower=0) / cfg.event_saturation))
                       ).clip(0, 100)

    # --- G2 ---
    if industry_map is not None:
        g2 = market_premium(df, industry_map, data["baskets"],
                            data.get("stock_lists"), cfg, verbose=verbose)
    else:
        g2 = pd.Series(np.nan, index=dates)
    out["G2_market_premium"] = g2.reindex(dates)
    # 锚点: ±12% 的 20 日收益差已是极端分化
    out["g2_score"] = _scale(out["G2_market_premium"], -0.12, 0.12)

    # --- G3 ---
    win = event_window(data.get("scheduled", []), dates, cfg)
    out["G3_in_window"] = win["in_geo_window"]        # 只有地缘事件窗口才封顶仓位
    out["days_to_event"] = win["days_to_event"]
    out["next_event"] = win["next_event"]
    out["event_kind"] = win["event_kind"]
    out["days_to_geo"] = win["days_to_geo"]
    out["next_geo_event"] = win["next_geo_event"]
    # 距离下一个**地缘**事件越近, 分数越高。用 days_to_geo 而非 days_to_event,
    # 否则临近的货币政策事件会遮住更远的元首会晤。
    gap = win["days_to_geo"]
    out["g3_score"] = np.where(
        gap.notna(),
        100.0 - np.clip(gap.fillna(999), 0, 60) / 60.0 * 100.0,
        0.0,
    )

    # --- 合成（缺失项自动重新归一化权重）---
    parts = {"g1_score": cfg.w_event, "g2_score": cfg.w_premium, "g3_score": cfg.w_window}
    num = pd.Series(0.0, index=dates)
    den = pd.Series(0.0, index=dates)
    for col, w in parts.items():
        v = out[col].astype("float64")
        m = v.notna()
        num += v.fillna(0.0) * w * m
        den += w * m
    out["geo_score"] = num / den.replace(0, np.nan)

    out["geo_level"] = np.where(
        out["geo_score"] < cfg.calm_below, "calm",
        np.where(out["geo_score"] > cfg.tense_above, "tense", "friction"),
    )

    if verbose and len(out.dropna(subset=["geo_score"])):
        last = out.dropna(subset=["geo_score"]).iloc[-1]
        lv = GEO_LEVELS[last["geo_level"]]
        print(f"[Geo] 最新对抗分 {last['geo_score']:.1f} → 【{lv['name']}】  "
              f"事件强度 {last['G1_event_intensity']:.2f}  "
              f"市场溢价 {last['G2_market_premium']:+.2%}"
              if pd.notna(last["G2_market_premium"]) else
              f"[Geo] 最新对抗分 {last['geo_score']:.1f} → 【{lv['name']}】")

    return out


# =============================================================================
# 作用点 A: 板块倾斜
# =============================================================================

# 板块关键词 → 对抗敏感度 (-1 受损 ~ +1 受益)
#
# 光通信链的敏感度是**沿产业链单调变化**的，这是本表最需要注意的一段：
#
#   InP衬底/稀土   +0.7  中方手里的出口管制筹码，对抗升级 = 涨价 + 国产替代双驱动
#   光子封装设备   +0.2  海外扩产的卖铲人，与中国对美出货无关，受损最小
#   光器件/光芯片  -0.3  隔了一层，但下游是中国模块厂 —— **不是免疫，只是缓冲**
#   光模块成品     -0.5  FCC 草案的直接标的
#
# 常见的错误是把「光器件」当成对抗的避风港。天孚通信的客户是 Fabrinet、
# 中际旭创、新易盛、光迅——中国模块厂对美出货受限，器件厂的国内订单同样掉。
SECTOR_SENSITIVITY: Dict[str, float] = {
    # --- 光通信链（沿链递减）---
    "InP": +0.7, "磷化铟": +0.7, "衬底": +0.5,
    "光子封装设备": +0.2,
    "光器件": -0.3, "光芯片": -0.3,
    "光模块": -0.5, "CPO": -0.5, "光通信": -0.4,
    # 受益：国产替代、管制筹码、军工
    "国防军工": +0.9, "军工": +0.9,
    "计算机": +0.5, "软件": +0.4, "信创": +0.9,
    "半导体设备": +0.8, "半导体": +0.3,
    "有色": +0.4, "稀土": +0.9, "小金属": +0.6,
    "通信": +0.2,
    # 受损：出口敞口、被列清单
    "纺织服装": -0.8, "服装家纺": -0.8,
    "家用电器": -0.6, "家电": -0.6,
    "轻工制造": -0.5,
    "汽车": -0.4, "汽车零部件": -0.5,
    "机器人": -0.6,          # FCC 7/28 禁令直接命中
    "光伏": -0.5, "光伏设备": -0.5,   # 8/7 多晶硅关税
    "医药": -0.2, "医疗服务": -0.4,   # 药明康德在 1260H 名单
    "交通运输": -0.3, "交运": -0.3,
    "食品饮料": -0.1,
    "农林牧渔": +0.2,        # 峰会缓和时的采购受益方向
}


def sector_tilt(
    latest: pd.DataFrame,
    geo_score: float,
    config: Optional[GeoConfig] = None,
    name_col: str = "name",
) -> pd.DataFrame:
    """
    作用点 A — 按对抗强度倾斜板块打分。

    tilt = 敏感度 × (geo_score − 50)/50 × tilt_max

    geo_score = 50 时不做任何调整; 对抗升级时压低负敏感度板块、抬升正敏感度板块;
    缓和时反向。**倾斜幅度上限 ±12 分**, 刻意做得温和——地缘是风险调整项,
    不该盖过基本面强度本身。

    返回增加 geo_sensitivity / geo_tilt / score_geo 三列的副本, 并按 score_geo 重排。
    """
    cfg = config or GeoConfig()
    out = latest.copy()

    def _sens(name: str) -> float:
        if not isinstance(name, str):
            return 0.0
        best = 0.0
        best_len = 0
        for kw, v in SECTOR_SENSITIVITY.items():
            if kw in name and len(kw) > best_len:   # 最长匹配优先
                best, best_len = v, len(kw)
        return best

    names = out[name_col] if name_col in out.columns else pd.Series(out.index, index=out.index)
    out["geo_sensitivity"] = [_sens(n) for n in names]

    k = (float(geo_score) - 50.0) / 50.0 if pd.notna(geo_score) else 0.0
    out["geo_tilt"] = out["geo_sensitivity"] * k * cfg.tilt_max
    out["score_geo"] = (out["score"] + out["geo_tilt"]).clip(0, 100)

    out = out.sort_values("score_geo", ascending=False)
    out.attrs.update(latest.attrs)
    out.attrs["geo_score"] = geo_score
    return out


# =============================================================================
# 作用点 B: 事件窗口仓位封顶
# =============================================================================

def apply_event_cap(
    regime_df: pd.DataFrame,
    geo_df: pd.DataFrame,
    config: Optional[GeoConfig] = None,
) -> pd.DataFrame:
    """
    作用点 B — 在已知重大事件窗口内对目标仓位设上限。

    逻辑不是「预测事件会利空」, 而是「事件结果二元且不可预测时,
    期望收益不变但方差骤增, 应当降低敞口」。这是纯粹的风险管理,
    不含方向判断。

    只对 kind == "geo" 的事件生效——货币政策事件已经由波动率乘数覆盖,
    重复施加会过度降仓。

    返回增加 event_cap / target_position_geo 两列的副本。
    """
    cfg = config or GeoConfig()
    out = regime_df.copy()

    g = geo_df.reindex(out.index)
    in_geo_window = g["G3_in_window"].fillna(False).astype(bool)

    out["event_cap"] = np.where(in_geo_window, cfg.event_cap_position, 1.0)
    out["in_geo_window"] = in_geo_window
    out["next_event"] = g.get("next_geo_event", g.get("next_event"))
    out["days_to_event"] = g.get("days_to_geo", g.get("days_to_event"))

    base = out["target_position"] if "target_position" in out.columns else 1.0
    out["target_position_geo"] = np.minimum(base, out["event_cap"])
    return out


# =============================================================================
# 报告
# =============================================================================

def print_geo_report(geo_df: pd.DataFrame, events_path: Path = DEFAULT_EVENTS,
                     n_events: int = 6) -> None:
    valid = geo_df.dropna(subset=["geo_score"])
    if valid.empty:
        print("无有效地缘数据")
        return
    last = valid.iloc[-1]
    lv = GEO_LEVELS[last["geo_level"]]
    data = load_events(events_path)

    print("\n" + "=" * 78)
    print(f"中美对抗因子    {pd.Timestamp(valid.index[-1]).date()}")
    print("=" * 78)
    print(f"  G1 事件强度   : {last['G1_event_intensity']:6.2f}  → {last['g1_score']:5.1f} 分")
    prem = last["G2_market_premium"]
    prem_txt = f"{prem:+.2%}" if pd.notna(prem) else "  N/A "
    print(f"  G2 市场溢价   : {prem_txt:>6}  → "
          f"{last['g2_score']:5.1f} 分   (自主可控 − 出口链, 20日收益差)")
    print(f"  G3 事件窗口   : {'是' if last['G3_in_window'] else '否':>6}  → {last['g3_score']:5.1f} 分")
    print("-" * 78)
    print(f"  综合对抗分 GeoScore: {last['geo_score']:.1f}  →  【{lv['name']}】")
    print(f"  {lv['note']}")

    # G1/G2 背离诊断
    if pd.notna(last["g2_score"]):
        gap = last["g1_score"] - last["g2_score"]
        if gap > 25:
            print("\n  [!] 背离: 事件强度高但市场溢价低 —— 冲击尚未被充分定价, "
                  "受损板块的利空可能还没走完")
        elif gap < -25:
            print("\n  [!] 背离: 市场溢价高但事件强度低 —— 已过度定价对抗, "
                  "若峰会缓和则受损板块存在修复弹性")

    if pd.notna(last.get("days_to_event")):
        print(f"\n  下一个日程事件: {last['next_event']}  (还有 {int(last['days_to_event'])} 天)")
    if pd.notna(last.get("days_to_geo")):
        flag = "  ← 已进入窗口, 仓位封顶" if last["G3_in_window"] else ""
        print(f"  下一个地缘事件: {last['next_geo_event']}  "
              f"(还有 {int(last['days_to_geo'])} 天){flag}")

    print("\n  近期事件:")
    for ev in data["events"][-n_events:]:
        arrow = "↑" if ev.get("direction") != "deescalate" else "↓"
        side = {"US": "美", "CN": "中", "BOTH": "双"}.get(ev.get("side"), "?")
        print(f"    {ev['date']}  [{side}]{arrow} S{ev.get('severity','?')}  {ev['title']}")
    print("=" * 78 + "\n")
