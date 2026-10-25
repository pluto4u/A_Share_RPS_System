"""
================================================================================
周度板块强度 — 基于通达信行业归属 (tdxhy.cfg)
================================================================================
核心设计: **强度不等于可买**。

单看板块涨幅排名会系统性地在退潮期站错边 — 一个板块能排到 RPS 前 10%,
往往意味着它已经涨了两个月。所以本模块把板块拆成两个正交维度:

    水平 (Level)        = 中期相对强度 RPS_60
    方向 (Acceleration) = 短期强度 - 中期强度 = RPS_20 - RPS_60

两维切出四象限:

                     加速 (S3 > 0)          减速 (S3 <= 0)
    强 (RPS60>=60)   [主升 LEADING]         [退潮 FADING]
    弱 (RPS60< 60)   [启动 EMERGING]        [沉寂 LAGGING]

白名单只收 LEADING + EMERGING, 明确排除 FADING —— 那是"看起来很强但钱
正在撤"的陷阱, 也是 RPS 类策略最常见的亏损来源。

输出:
    - 周度板块强度表 (每个行业一行, 含 6 个分项和总分)
    - 板块白名单 (供 run_tdx_screener 过滤个股结果)
    - 板块强度历史序列 (供热力图)

数据源:
    行业归属  : C:/new_tdx/T0002/hq_cache/tdxhy.cfg (离线, 复用 rps_analyzer.industry_rps)
    行业名称  : 可选, incon.dat 或自带映射表, 缺失则显示行业代码
    估算资金流: 可选, macro.moneyflow (联网), 默认只作诊断证据, 不进入综合分
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List

import numpy as np
import pandas as pd

from .factors import Matrices, build_matrices


@dataclass
class SectorConfig:
    """板块强度参数"""

    # --- 强度回看 (交易日) ---
    period_short: int = 20      # 短期, 约 1 个月
    period_mid: int = 60        # 中期, 约 3 个月
    period_long: int = 120      # 长期, 约半年

    # --- 结构分项权重 ---
    w_rps_mid: float = 0.26     # S1 中期强度
    w_accel: float = 0.24       # S3 加速度  ← 与强度近乎同等重要
    w_breadth: float = 0.16     # S2 板块内部宽度
    w_leader: float = 0.14      # S4 龙头密度
    w_volume: float = 0.10      # S5 量能确认
    # 网页“主力净流入”是成交方向模型估算。默认 diagnostic，避免与
    # RPS/宽度/量价结构粗暴合成；只有样本外回测时才显式改为 weighted。
    moneyflow_mode: str = "diagnostic"  # diagnostic | weighted
    w_moneyflow: float = 0.10            # weighted 实验模式权重

    # --- 白名单门槛 ---
    min_score: float = 62.0         # 综合分下限
    min_stocks: int = 8             # 行业内最少个股数, 过滤袖珍行业
    require_volume: float = 0.95    # 量能比下限 (5日均/60日均)
    exclude_fading: bool = True     # 排除"强但减速"象限

    # --- 四象限切分 ---
    level_strong_above: float = 60.0
    accel_positive_above: float = 0.0


# 通达信行业代码 -> 中文名 的兜底映射 (常用一级行业)
# 完整映射可从 incon.dat 读取; 此表仅保证输出可读, 缺失时回退显示代码。
TDX_INDUSTRY_FALLBACK: Dict[str, str] = {
    "T01": "煤炭", "T02": "石油", "T03": "有色", "T04": "钢铁",
    "T05": "基础化工", "T06": "建材", "T07": "建筑", "T08": "机械",
    "T09": "电力设备", "T10": "国防军工", "T11": "汽车", "T12": "家电",
    "T13": "轻工制造", "T14": "纺织服装", "T15": "食品饮料", "T16": "农林牧渔",
    "T17": "医药", "T18": "商贸零售", "T19": "社会服务", "T20": "交通运输",
    "T21": "房地产", "T22": "银行", "T23": "非银金融", "T24": "综合",
    "T25": "电子", "T26": "计算机", "T27": "传媒", "T28": "通信",
    "T29": "公用事业", "T30": "环保",
}


def resolve_industry_name(code: str,
                          name_map: Optional[Dict[str, str]] = None) -> str:
    """行业代码 -> 可读名称, 逐级回退"""
    if name_map and code in name_map:
        return name_map[code]
    if code in TDX_INDUSTRY_FALLBACK:
        return TDX_INDUSTRY_FALLBACK[code]
    prefix = code[:3] if isinstance(code, str) else ""
    return TDX_INDUSTRY_FALLBACK.get(prefix, str(code))


def load_industry_names(incon_path: Optional[Path] = None) -> Dict[str, str]:
    """
    从通达信 incon.dat 读取行业代码 -> 名称映射。

    incon.dat 是 GBK 编码的分节文本, 行业段形如:
        #TDXNHY
        T01|煤炭
        ...
    文件不存在或格式不符时返回空字典, 由 resolve_industry_name 回退。
    """
    if incon_path is None or not Path(incon_path).exists():
        return {}
    mapping: Dict[str, str] = {}
    try:
        with open(incon_path, "r", encoding="gbk", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 2 and parts[0].startswith("T"):
                    mapping[parts[0]] = parts[1]
    except Exception:
        return {}
    return mapping


# =============================================================================
# 核心计算
# =============================================================================

def _industry_mean(mat: pd.DataFrame, ind: pd.Series) -> pd.DataFrame:
    """
    把 date x code 矩阵按行业等权聚合成 date x industry。

    用等权而非市值加权 — 市值加权会让一两只权重股绑架整个板块读数,
    而我们要判断的是"这个板块里随便买一只票的赚钱概率"。
    """
    cols = mat.columns.intersection(ind.index)
    sub = mat[cols]
    groups = ind.reindex(cols)
    return sub.T.groupby(groups).mean().T


def _industry_sum(mat: pd.DataFrame, ind: pd.Series) -> pd.DataFrame:
    cols = mat.columns.intersection(ind.index)
    sub = mat[cols]
    groups = ind.reindex(cols)
    return sub.T.groupby(groups).sum().T


def _cross_rank(df: pd.DataFrame) -> pd.DataFrame:
    """按行 (每个日期) 做横截面百分位排名 -> 0~100"""
    return df.rank(axis=1, pct=True) * 100.0


def compute_sector_panel(
    m: Matrices,
    industry_map: pd.DataFrame,
    config: Optional[SectorConfig] = None,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """
    计算板块强度的全部分项时间序列 (日频, 后续按周采样)。

    参数
    ----
    m : Matrices
        宽表矩阵 (来自 factors.build_matrices)
    industry_map : DataFrame
        含 ts_code / industry 两列 (来自 rps_analyzer.industry_rps.load_industry_map)

    返回
    ----
    dict, 每个值都是 date x industry 的 DataFrame:
        rps_short / rps_mid / rps_long : 横截面强度排名 0~100
        accel                          : rps_short - rps_mid
        breadth                        : 行业内站上 MA20 占比
        leader                         : 行业内 RPS120>=90 占比
        vol_ratio                      : 行业成交额 5日均/60日均
        n_stocks                       : 行业内有效个股数
    """
    cfg = config or SectorConfig()
    close = m.close

    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    ind = ind[ind.index.isin(close.columns)]
    if ind.empty:
        raise ValueError("行业映射与行情数据没有交集, 请检查 tdxhy.cfg 路径与代码格式")

    if verbose:
        print(f"[SECTOR] 行业映射覆盖 {len(ind)} 只股票 / "
              f"{ind.nunique()} 个行业")

    out: Dict[str, pd.DataFrame] = {}

    # --- 各周期收益 -> 行业等权 -> 横截面排名 ---
    for label, n in [("short", cfg.period_short),
                     ("mid", cfg.period_mid),
                     ("long", cfg.period_long)]:
        ret = close / close.shift(n) - 1.0
        ret = ret.where(ret.abs() <= 5.0)          # 剔除未复权跳空
        ind_ret = _industry_mean(ret, ind)
        out[f"rps_{label}"] = _cross_rank(ind_ret)
        out[f"ret_{label}"] = ind_ret

    out["accel"] = out["rps_short"] - out["rps_mid"]

    # --- 行业内部宽度 ---
    ma20 = close.rolling(20, min_periods=20).mean()
    above = (close > ma20).astype("float32").where(close.notna() & ma20.notna())
    out["breadth"] = _industry_mean(above, ind)

    # --- 龙头密度 ---
    if m.rps120 is not None:
        strong = (m.rps120 >= 90).astype("float32").where(m.rps120.notna())
        out["leader"] = _industry_mean(strong, ind)
    else:
        out["leader"] = pd.DataFrame(np.nan, index=close.index,
                                     columns=out["rps_mid"].columns)

    # --- 量能确认 ---
    amt = m.amount
    ind_amt = _industry_sum(amt, ind)
    out["vol_ratio"] = (
        ind_amt.rolling(5, min_periods=3).mean()
        / ind_amt.rolling(60, min_periods=40).mean()
    )
    out["amount"] = ind_amt

    # --- 有效个股数 ---
    alive = close.notna().astype("float32")
    out["n_stocks"] = _industry_sum(alive, ind)

    return out


def _quadrant(level: float, accel: float, cfg: SectorConfig) -> str:
    strong = level >= cfg.level_strong_above
    up = accel > cfg.accel_positive_above
    if strong and up:
        return "LEADING"
    if strong and not up:
        return "FADING"
    if not strong and up:
        return "EMERGING"
    return "LAGGING"


QUADRANT_LABEL = {
    "LEADING": "主升",
    "EMERGING": "启动",
    "FADING": "退潮",
    "LAGGING": "沉寂",
}

QUADRANT_NOTE = {
    "LEADING": "强且在加速 — 主攻方向, 可在板块内选 L1/L2 个股",
    "EMERGING": "弱但在加速 — 潜伏方向, 只配早期启涨型个股, 小仓位",
    "FADING": "强但在减速 — 危险区, 已持有的减仓, 不新开仓",
    "LAGGING": "弱且减速 — 回避",
}


def compute_sector_weekly(
    df_daily: pd.DataFrame,
    industry_map: pd.DataFrame,
    target_date: Optional[str] = None,
    weeks: int = 26,
    config: Optional[SectorConfig] = None,
    moneyflow: Optional[pd.DataFrame] = None,
    name_map: Optional[Dict[str, str]] = None,
    start: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """
    板块周度强度主函数。

    参数
    ----
    df_daily : DataFrame
        全市场日线长表 (最好已含 RPS_120)
    industry_map : DataFrame
        ts_code / industry
    target_date : str, optional
        目标日 YYYYMMDD, None 取最新
    weeks : int
        输出多少周的历史 (供热力图)
    moneyflow : DataFrame, optional
        索引为 industry, 含 net_inflow_ratio 列 (0~1 越大越好)。
        由 macro.moneyflow 抓取, 缺失时该分项权重自动分摊。
    name_map : dict, optional
        行业代码 -> 中文名

    返回
    ----
    dict:
        "latest"  : 目标周的板块强度表 (按 score 降序)
        "history" : date x industry 的综合分历史 (周频, 供热力图)
        "panel"   : 全部分项的日频面板
    """
    cfg = config or SectorConfig()

    m = build_matrices(df_daily, start=start, verbose=verbose)
    panel = compute_sector_panel(m, industry_map, cfg, verbose=verbose)

    # --- 综合分 (日频) ---
    parts = {
        "rps_mid": (panel["rps_mid"], cfg.w_rps_mid),
        # accel 原始范围约 ±60, 映射到 0~100
        "accel": (((panel["accel"] + 40.0) / 80.0 * 100.0).clip(0, 100), cfg.w_accel),
        "breadth": (panel["breadth"] * 100.0, cfg.w_breadth),
        "leader": (_cross_rank(panel["leader"]), cfg.w_leader),
        "volume": (((panel["vol_ratio"] - 0.7) / 0.9 * 100.0).clip(0, 100), cfg.w_volume),
    }

    num = None
    den = None
    for _, (mat, w) in parts.items():
        contrib = mat * w
        valid = mat.notna() * w
        num = contrib.fillna(0.0) if num is None else num + contrib.fillna(0.0)
        den = valid if den is None else den + valid
    score_daily = num / den.replace(0, np.nan)

    # --- 采样到周 (每周最后一个交易日) ---
    idx = score_daily.index
    week_key = pd.Series(idx, index=idx).dt.to_period("W")
    last_of_week = pd.Series(idx, index=idx).groupby(week_key).max()
    weekly_dates = pd.DatetimeIndex(sorted(last_of_week.values))

    if target_date is not None:
        td = pd.Timestamp(target_date)
        weekly_dates = weekly_dates[weekly_dates <= td]
        cand = idx[idx <= td]
        if len(cand) == 0:
            raise ValueError(f"{target_date} 之前无数据")
        as_of = cand.max()
    else:
        as_of = idx.max()

    history = score_daily.reindex(weekly_dates).tail(weeks)

    # --- 目标日截面 ---
    latest = pd.DataFrame({
        "industry": panel["rps_mid"].columns,
    }).set_index("industry")

    def _row(key: str) -> pd.Series:
        return panel[key].reindex([as_of]).iloc[0]

    latest["n_stocks"] = _row("n_stocks")
    latest["ret_short"] = _row("ret_short")
    latest["ret_mid"] = _row("ret_mid")
    latest["S1_rps_mid"] = _row("rps_mid")
    latest["rps_short"] = _row("rps_short")
    latest["rps_long"] = _row("rps_long")
    latest["S3_accel"] = _row("accel")
    latest["S2_breadth"] = _row("breadth")
    latest["S4_leader"] = _row("leader")
    latest["S5_vol_ratio"] = _row("vol_ratio")
    latest["score"] = score_daily.reindex([as_of]).iloc[0]

    # --- 估算资金流分项 (可选，默认只诊断不改写结构分) ---
    if cfg.moneyflow_mode not in ("diagnostic", "weighted"):
        raise ValueError("moneyflow_mode 必须是 diagnostic 或 weighted")

    if moneyflow is not None and not moneyflow.empty:
        mf = moneyflow.reindex(latest.index)
        latest["S6_moneyflow"] = mf.get("net_inflow_ratio", np.nan)
        mf_score = latest["S6_moneyflow"].rank(pct=True) * 100.0
        latest["S6_moneyflow_score"] = mf_score
        if cfg.moneyflow_mode == "weighted":
            # 仅限显式实验模式；线上默认不用估算资金流改写结构排序。
            base_w = (cfg.w_rps_mid + cfg.w_accel + cfg.w_breadth
                      + cfg.w_leader + cfg.w_volume)
            total_w = base_w + cfg.w_moneyflow
            latest["score"] = np.where(
                mf_score.notna(),
                (latest["score"] * base_w + mf_score * cfg.w_moneyflow) / total_w,
                latest["score"],
            )
            if verbose:
                print(f"[SECTOR] 估算资金流已按实验模式并入 "
                      f"({mf_score.notna().sum()}/{len(latest)} 个行业有数据)")
        elif verbose:
            print(f"[SECTOR] 估算资金流仅作诊断，不改写综合分 "
                  f"({mf_score.notna().sum()}/{len(latest)} 个行业有数据)")
    else:
        latest["S6_moneyflow"] = np.nan
        latest["S6_moneyflow_score"] = np.nan
        if verbose:
            print("[SECTOR] 无估算资金流数据，结构分照常计算")

    # --- 四象限 ---
    latest["quadrant"] = [
        _quadrant(lv, ac, cfg)
        for lv, ac in zip(latest["S1_rps_mid"], latest["S3_accel"])
    ]
    latest["quadrant_cn"] = latest["quadrant"].map(QUADRANT_LABEL)
    latest["note"] = latest["quadrant"].map(QUADRANT_NOTE)
    latest["name"] = [resolve_industry_name(c, name_map) for c in latest.index]

    latest = latest.sort_values("score", ascending=False)
    latest.attrs["as_of"] = as_of

    if verbose:
        print(f"[SECTOR] 目标日 {pd.Timestamp(as_of).date()}, "
              f"{len(latest)} 个行业, "
              f"主升 {(latest['quadrant']=='LEADING').sum()} / "
              f"启动 {(latest['quadrant']=='EMERGING').sum()} / "
              f"退潮 {(latest['quadrant']=='FADING').sum()}")

    return {"latest": latest, "history": history, "panel": panel}


# =============================================================================
# 白名单
# =============================================================================

def build_sector_whitelist(
    latest: pd.DataFrame,
    config: Optional[SectorConfig] = None,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """
    从板块强度表生成白名单。

    规则 (全部满足):
      1. score >= min_score
      2. n_stocks >= min_stocks           (排除袖珍行业, 样本太少排名不可信)
      3. vol_ratio >= require_volume      (有量才算真强)
      4. quadrant 属于 LEADING / EMERGING (排除退潮陷阱)

    top_n 若给定, 再截取前 N 个。
    """
    cfg = config or SectorConfig()

    ok = (
        (latest["score"] >= cfg.min_score)
        & (latest["n_stocks"] >= cfg.min_stocks)
        & (latest["S5_vol_ratio"].fillna(0) >= cfg.require_volume)
    )
    if cfg.exclude_fading:
        ok &= latest["quadrant"].isin(["LEADING", "EMERGING"])

    wl = latest[ok].copy()
    if top_n:
        wl = wl.head(top_n)
    return wl


def filter_stocks_by_whitelist(
    picks: pd.DataFrame,
    industry_map: pd.DataFrame,
    whitelist: pd.DataFrame,
    keep_column: str = "ts_code",
) -> pd.DataFrame:
    """
    用板块白名单过滤选股结果 (L1/L2/Super 等)。

    给 picks 增加 industry / industry_name / in_whitelist 三列, 并返回
    只保留白名单内个股的子集。原表可通过 .attrs["dropped"] 取回被剔除的部分。
    """
    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    out = picks.copy()
    out["industry"] = out[keep_column].map(ind)
    name_of = whitelist["name"].to_dict() if "name" in whitelist.columns else {}
    out["industry_name"] = out["industry"].map(
        lambda c: name_of.get(c, resolve_industry_name(c) if pd.notna(c) else "未分类")
    )
    wl_set = set(whitelist.index)
    out["in_whitelist"] = out["industry"].isin(wl_set)

    kept = out[out["in_whitelist"]].copy()
    kept.attrs["dropped"] = out[~out["in_whitelist"]].copy()
    return kept


def print_sector_report(latest: pd.DataFrame, whitelist: pd.DataFrame,
                        top_n: int = 20) -> None:
    """终端可读的板块强度报告"""
    as_of = latest.attrs.get("as_of")
    print("\n" + "=" * 96)
    print(f"周度板块强度    {pd.Timestamp(as_of).date() if as_of is not None else ''}")
    print("=" * 96)

    show = latest.head(top_n).copy()
    print(f"{'行业':<10}{'总分':>6}{'中期RPS':>8}{'加速':>7}{'宽度':>7}"
          f"{'龙头':>7}{'量比':>7}{'20日涨':>8}  象限")
    print("-" * 96)
    for code, r in show.iterrows():
        print(f"{str(r['name'])[:9]:<10}"
              f"{r['score']:>6.1f}"
              f"{r['S1_rps_mid']:>8.1f}"
              f"{r['S3_accel']:>+7.1f}"
              f"{r['S2_breadth']*100 if pd.notna(r['S2_breadth']) else 0:>6.0f}%"
              f"{r['S4_leader']*100 if pd.notna(r['S4_leader']) else 0:>6.0f}%"
              f"{r['S5_vol_ratio']:>7.2f}"
              f"{r['ret_short']*100:>7.1f}%"
              f"  {r['quadrant_cn']}")

    print("-" * 96)
    print(f"\n白名单 ({len(whitelist)} 个行业):")
    if whitelist.empty:
        print("  当前无板块满足白名单条件 —— 这本身是一个减仓信号。")
    else:
        for code, r in whitelist.iterrows():
            print(f"  [{r['quadrant_cn']}] {r['name']:<10} "
                  f"分 {r['score']:.1f}  {r['note']}")
    print()
