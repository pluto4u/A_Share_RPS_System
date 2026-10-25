"""
================================================================================
宏观状态 + 板块强度 — 一键执行脚本
================================================================================
读取通达信本地数据 -> 计算 17 个宏观因子 -> 双轴打分 -> 九宫格状态机
                  -> 周度板块强度 -> 板块白名单 -> HTML 仪表盘

用法:
    # 日常运行 (读 TDX, 近 5 年)
    py -3 run_macro_regime.py

    # 复用选股脚本已保存的 parquet, 秒级出结果
    py -3 run_tdx_screener.py --save-data
    py -3 run_macro_regime.py --parquet output/tdx_stock_daily_with_rps.parquet

    # 指定日期回看 (检查历史某天的状态)
    py -3 run_macro_regime.py --date 20260417

    # 跑满历史做状态机验证
    py -3 run_macro_regime.py --start 20150101 --backtest

    # 跳过联网资金流
    py -3 run_macro_regime.py --skip-moneyflow

    # 用白名单过滤已有选股结果
    py -3 run_macro_regime.py --filter-picks output/tdx_screener_20260717.csv

输出 (默认 output/):
    macro_dashboard_{date}.html      仪表盘 (双击即开, 无需联网)
    macro_regime_{date}.csv          每日状态与目标仓位时间序列
    macro_factors_{date}.csv         17 个因子原始值
    sector_weekly_{date}.csv         板块强度截面
    sector_whitelist_{date}.csv      板块白名单
    sector_history_{date}.csv        板块强度周度历史
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.industry_rps import load_industry_map, DEFAULT_INDUSTRY_CFG

from macro.factors import compute_all_factors, build_matrices
from macro.regime import (
    RegimeConfig, build_regime_series, print_regime_report, REGIME_GRID,
)
from macro.sector import (
    SectorConfig, compute_sector_weekly, build_sector_whitelist,
    print_sector_report, filter_stocks_by_whitelist, load_industry_names,
    resolve_industry_name,
)
from macro.geopolitics import (
    GeoConfig, compute_geo_score, sector_tilt, apply_event_cap,
    print_geo_report, DEFAULT_EVENTS,
)
from macro.dashboard import build_dashboard, save_dashboard

OUTPUT_DIR = Path("output")


# =============================================================================
# 数据加载
# =============================================================================

def load_data(args) -> pd.DataFrame:
    """优先用 parquet 缓存, 否则读 TDX 原始数据并算 RPS"""
    if args.parquet:
        p = Path(args.parquet)
        if not p.exists():
            sys.exit(f"[错误] parquet 不存在: {p}")
        print(f"[数据] 读取缓存 {p.name} ...")
        t0 = time.time()
        df = pd.read_parquet(p)
        print(f"[数据] {len(df):,} 行, {df['ts_code'].nunique()} 只股票, "
              f"耗时 {time.time()-t0:.1f}s")
        return df

    print(f"[数据] 读取通达信本地数据 {args.tdx_dir} ...")
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    print(f"[数据] 读取耗时 {time.time()-t0:.1f}s")

    if "RPS_120" not in df.columns:
        print("[数据] 计算 RPS ...")
        t1 = time.time()
        df = calculate_rps(df, periods=[120], verbose=False)
        print(f"[数据] RPS 耗时 {time.time()-t1:.1f}s")

    if args.save_data:
        out = OUTPUT_DIR / "macro_input_with_rps.parquet"
        OUTPUT_DIR.mkdir(exist_ok=True)
        df.to_parquet(out, index=False)
        print(f"[数据] 已缓存 {out}")
    return df


def load_industries(args) -> Optional[pd.DataFrame]:
    """加载行业映射, 失败时返回 None (板块模块自动跳过)"""
    cfg_path = Path(args.industry_cfg)
    if not cfg_path.exists():
        print(f"[行业] tdxhy.cfg 不存在: {cfg_path}")
        print("[行业] 板块强度模块将被跳过。用 --industry-cfg 指定正确路径, "
              "常见位置: <通达信>/T0002/hq_cache/tdxhy.cfg")
        return None
    try:
        im = load_industry_map(cfg_path)
        print(f"[行业] 载入 {len(im)} 条归属, {im['industry'].nunique()} 个行业")
        return im
    except Exception as e:
        print(f"[行业] 读取失败: {e}")
        return None


# =============================================================================
# 状态机历史验证
# =============================================================================

def backtest_regime(regime_df: pd.DataFrame, factors: pd.DataFrame) -> None:
    """
    验证状态机: 每个状态在 未来 20 / 60 日的等权指数收益。

    这不是策略回测, 而是**状态机的有效性检验** —— 如果"熊市诱多"格的
    未来收益不显著为负, 或"主升行情"格不显著为正, 说明阈值需要重标定。
    """
    if "_ewi" not in factors.columns:
        print("[回测] 缺少等权指数, 跳过")
        return

    df = regime_df.dropna(subset=["trend_score"]).copy()
    ewi = factors["_ewi"].reindex(df.index)
    for h in (20, 60):
        df[f"fwd{h}"] = ewi.shift(-h) / ewi - 1.0

    print("\n" + "=" * 86)
    print("状态机验证 — 各状态的未来收益 (等权指数)")
    print("=" * 86)
    print(f"{'状态':<12}{'天数':>7}{'占比':>7}{'未来20日':>11}{'胜率':>7}"
          f"{'未来60日':>11}{'胜率':>7}{'基准仓位':>9}")
    print("-" * 86)

    order = [c["code"] for c in REGIME_GRID.values()]
    name_of = {c["code"]: c["name"] for c in REGIME_GRID.values()}
    pos_of = {c["code"]: c["position"] for c in REGIME_GRID.values()}
    total = len(df)

    for code in order:
        sub = df[df["regime"] == code]
        if sub.empty:
            print(f"{name_of[code]:<12}{0:>7}{'—':>7}{'—':>11}{'—':>7}"
                  f"{'—':>11}{'—':>7}{pos_of[code]:>8.0%}")
            continue
        r20, r60 = sub["fwd20"].dropna(), sub["fwd60"].dropna()
        print(f"{name_of[code]:<12}{len(sub):>7}{len(sub)/total*100:>6.1f}%"
              f"{r20.mean()*100 if len(r20) else np.nan:>10.2f}%"
              f"{(r20>0).mean()*100 if len(r20) else np.nan:>6.0f}%"
              f"{r60.mean()*100 if len(r60) else np.nan:>10.2f}%"
              f"{(r60>0).mean()*100 if len(r60) else np.nan:>6.0f}%"
              f"{pos_of[code]:>8.0%}")

    print("-" * 86)
    base20 = df["fwd20"].dropna()
    print(f"{'全样本基准':<12}{total:>7}{100.0:>6.1f}%"
          f"{base20.mean()*100:>10.2f}%{(base20>0).mean()*100:>6.0f}%")

    # 分组秩相关 — 基准仓位是否与未来收益同向
    grp = df.groupby("regime")["fwd20"].mean()
    pos = pd.Series({k: pos_of[k] for k in grp.index})
    if len(grp) >= 3:
        rho = grp.rank().corr(pos.rank())
        print(f"\n  基准仓位 vs 未来20日收益 的秩相关: {rho:+.2f}"
              f"   ({'方向正确' if rho > 0.3 else '需要重新标定阈值'})")

    # 仓位加权收益 vs 满仓
    dfx = df.dropna(subset=["fwd20"])
    if len(dfx):
        daily = ewi.pct_change().reindex(dfx.index)
        timed = (daily * dfx["target_position"].shift(1)).fillna(0)
        cum_timed = (1 + timed).prod() - 1
        cum_hold = (1 + daily.fillna(0)).prod() - 1
        dd_timed = _max_dd((1 + timed).cumprod())
        dd_hold = _max_dd((1 + daily.fillna(0)).cumprod())
        print(f"\n  择时(按目标仓位) 累计 {cum_timed:+.1%}  最大回撤 {dd_timed:.1%}")
        print(f"  满仓持有         累计 {cum_hold:+.1%}  最大回撤 {dd_hold:.1%}")
        print("  注: 未计交易成本, 仅用于判断状态机方向是否正确, 不代表策略收益。")
    print("=" * 86 + "\n")


def _max_dd(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1).min())


def calibrate_anchors(factors: pd.DataFrame) -> None:
    """
    锚点标定报告 — 在**你自己的真实数据**上检查每个因子的经验分布,
    以及当前锚点把它映射成了什么分数区间。

    为什么必须做这一步: macro/regime.py 里的锚点是按 A 股 2010 年以来的
    经验分布设的, 但你的 TDX 数据范围、股票池 (是否含科创/北交)、是否
    剔除 ST 都会影响分布。如果某个因子的 P5~P95 全落在锚点之外, 它就
    退化成了常数 0 或 100, 等于白给了一份权重。

    判读方法:
      - "占满程度" 应在 60%~95% 之间。低于 40% 说明锚点太宽 (因子被压在
        中间, 区分度不够); 高于 98% 说明锚点太窄 (大量截断, 丢失信息)。
      - 建议把锚点调到约等于该因子的 P5 / P95。
    """
    from macro.regime import TREND_ANCHORS, HEAT_ANCHORS, STRESS_ANCHORS

    all_anchors = {}
    all_anchors.update({k: ("趋势", v) for k, v in TREND_ANCHORS.items()})
    all_anchors.update({k: ("温度", v) for k, v in HEAT_ANCHORS.items()})
    all_anchors.update({k: ("应激", v) for k, v in STRESS_ANCHORS.items()})

    print("\n" + "=" * 100)
    print("锚点标定报告 — 检查锚点是否匹配你的数据分布")
    print("=" * 100)
    print(f"{'因子':<22}{'轴':<5}{'P5':>10}{'P50':>10}{'P95':>10}"
          f"{'锚点低':>10}{'锚点高':>10}{'占满':>7}  建议")
    print("-" * 100)

    for name, (axis, (lo, hi)) in all_anchors.items():
        if name not in factors.columns:
            print(f"{name:<22}{axis:<5}  [数据中缺失]")
            continue
        s = factors[name].dropna()
        if s.empty:
            print(f"{name:<22}{axis:<5}  [全为空]")
            continue
        p5, p50, p95 = s.quantile([0.05, 0.5, 0.95])

        # 占满程度 = 实际 P5~P95 区间与锚点区间的重叠比例
        span = hi - lo
        overlap = max(0.0, min(hi, p95) - max(lo, p5))
        fill = overlap / span if span else 0.0

        if fill < 0.40:
            advice = f"锚点过宽, 建议 ({p5:.4g}, {p95:.4g})"
        elif fill > 0.98 and (p5 < lo or p95 > hi):
            advice = f"大量截断, 建议 ({p5:.4g}, {p95:.4g})"
        else:
            advice = "OK"

        print(f"{name:<22}{axis:<5}{p5:>10.4g}{p50:>10.4g}{p95:>10.4g}"
              f"{lo:>10.4g}{hi:>10.4g}{fill*100:>6.0f}%  {advice}")

    print("-" * 100)
    print("修改方式: 编辑 macro/regime.py 中的 TREND_ANCHORS / HEAT_ANCHORS / "
          "STRESS_ANCHORS 字典。")
    print("=" * 100 + "\n")


# =============================================================================
# 主流程
# =============================================================================

def main():
    p = argparse.ArgumentParser(description="A股宏观状态与板块强度")

    # 数据
    p.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 或 lday 路径")
    p.add_argument("--parquet", default=None, help="复用已保存的日线 parquet (最快)")
    p.add_argument("--industry-cfg", default=str(DEFAULT_INDUSTRY_CFG),
                   help="通达信 tdxhy.cfg 路径")
    p.add_argument("--incon", default=None, help="通达信 incon.dat, 用于行业中文名")
    p.add_argument("--min-days", type=int, default=250)
    p.add_argument("--start", default=None,
                   help="因子计算起始日 YYYYMMDD (默认近 5 年, 越长越占内存)")
    p.add_argument("--date", default=None, help="目标日 YYYYMMDD (默认最新)")
    p.add_argument("--save-data", action="store_true")

    # 状态机
    p.add_argument("--trend-weak", type=float, default=38.0)
    p.add_argument("--trend-strong", type=float, default=62.0)
    p.add_argument("--heat-cold", type=float, default=35.0)
    p.add_argument("--heat-hot", type=float, default=65.0)
    p.add_argument("--confirm-days", type=int, default=3,
                   help="状态切换确认天数, 调大更稳但更滞后")

    # 板块
    p.add_argument("--skip-sector", action="store_true")
    p.add_argument("--sector-weeks", type=int, default=26, help="热力图回看周数")
    p.add_argument("--sector-min-score", type=float, default=62.0)
    p.add_argument("--sector-top", type=int, default=None, help="白名单最多取前 N 个行业")
    p.add_argument("--skip-moneyflow", action="store_true", help="不联网抓资金流")
    p.add_argument("--moneyflow-window", default="5日",
                   choices=["今日", "3日", "5日", "10日"])

    # 地缘对抗因子
    p.add_argument("--skip-geo", action="store_true", help="跳过中美对抗因子")
    p.add_argument("--geo-events", default=str(DEFAULT_EVENTS),
                   help="中美对抗事件日志 JSON 路径")
    p.add_argument("--geo-halflife", type=float, default=21.0,
                   help="事件影响半衰期（交易日），调大更钝更稳")
    p.add_argument("--geo-event-cap", type=float, default=0.60,
                   help="重大地缘事件窗口内的仓位上限")

    # 输出
    p.add_argument("--backtest", action="store_true", help="打印状态机历史验证表")
    p.add_argument("--calibrate", action="store_true",
                   help="打印锚点标定报告 (首次在真实数据上运行时务必看一次)")
    p.add_argument("--filter-picks", default=None,
                   help="用白名单过滤指定的选股结果 CSV")
    p.add_argument("--no-open", action="store_true", help="不自动打开 HTML")
    args = p.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    t_start = time.time()

    # --- Step 1: 数据 ---
    print("=" * 74)
    print("Step 1: 加载数据")
    print("=" * 74)
    df = load_data(args)

    start = args.start
    if start is None and not args.backtest:
        # 默认近 5 年, 兼顾 MA200/52 周新高的预热需求与内存
        latest = pd.to_datetime(df["trade_date"]).max()
        start = (latest - pd.DateOffset(years=5)).strftime("%Y%m%d")
        print(f"[数据] 未指定 --start, 默认取近 5 年 (自 {start})")

    # --- Step 2: 因子 ---
    print("\n" + "=" * 74)
    print("Step 2: 计算宏观因子")
    print("=" * 74)
    t0 = time.time()
    factors = compute_all_factors(df, start=start, verbose=True)
    if args.date:
        factors = factors[factors.index <= pd.Timestamp(args.date)]
    print(f"[因子] 耗时 {time.time()-t0:.1f}s")

    if args.calibrate:
        calibrate_anchors(factors)

    # --- Step 3: 状态机 ---
    print("\n" + "=" * 74)
    print("Step 3: 双轴打分与九宫格状态机")
    print("=" * 74)
    rcfg = RegimeConfig(
        trend_weak_below=args.trend_weak,
        trend_strong_above=args.trend_strong,
        heat_cold_below=args.heat_cold,
        heat_hot_above=args.heat_hot,
        confirm_days=args.confirm_days,
    )
    regime_df = build_regime_series(factors, rcfg, verbose=True)
    print_regime_report(regime_df, factors)

    if args.backtest:
        backtest_regime(regime_df, factors)

    # --- Step 3.5: 中美对抗因子 ---
    # 放在板块之前, 因为板块打分需要用 geo_score 做倾斜。
    geo_df = None
    geo_cfg = GeoConfig(decay_halflife_days=args.geo_halflife,
                        event_cap_position=args.geo_event_cap)
    industry_map = None
    if not args.skip_geo:
        print("=" * 74)
        print("Step 3.5: 中美对抗因子")
        print("=" * 74)
        industry_map = load_industries(args)
        try:
            geo_df = compute_geo_score(
                df, industry_map=industry_map,
                events_path=Path(args.geo_events),
                config=geo_cfg, verbose=True,
            )
            if args.date:
                geo_df = geo_df[geo_df.index <= pd.Timestamp(args.date)]
            print_geo_report(geo_df, Path(args.geo_events))
            regime_df = apply_event_cap(regime_df, geo_df, geo_cfg)
            n_cap = int(regime_df["in_geo_window"].sum())
            if n_cap:
                print(f"[Geo] {n_cap} 个交易日处于地缘事件窗口, 仓位已封顶至 "
                      f"{geo_cfg.event_cap_position:.0%}")
        except Exception as e:
            print(f"[Geo] 计算失败, 跳过 ({type(e).__name__}: {e})")
            geo_df = None

    # --- Step 4: 板块 ---
    sector = None
    whitelist = None
    if not args.skip_sector:
        print("=" * 74)
        print("Step 4: 周度板块强度")
        print("=" * 74)
        if industry_map is None:
            industry_map = load_industries(args)
        if industry_map is not None:
            name_map = load_industry_names(Path(args.incon)) if args.incon else {}

            # 资金流 (联网, 可选)
            moneyflow = None
            if not args.skip_moneyflow:
                try:
                    from macro.moneyflow import get_industry_moneyflow
                    m_tmp = build_matrices(df, start=start, verbose=False)
                    from macro.sector import _industry_sum
                    ind_s = (industry_map.drop_duplicates("ts_code")
                             .set_index("ts_code")["industry"])
                    ind_s = ind_s[ind_s.index.isin(m_tmp.amount.columns)]
                    ind_amt = _industry_sum(m_tmp.amount, ind_s).iloc[-5:].mean()
                    moneyflow = get_industry_moneyflow(
                        industry_map, industry_amount=ind_amt,
                        indicator=args.moneyflow_window, verbose=True,
                    )
                    del m_tmp
                except Exception as e:
                    print(f"[资金流] 跳过 ({e})")
                    moneyflow = None

            scfg = SectorConfig(min_score=args.sector_min_score)
            sector = compute_sector_weekly(
                df, industry_map, target_date=args.date,
                weeks=args.sector_weeks, config=scfg,
                moneyflow=moneyflow, name_map=name_map,
                start=start, verbose=True,
            )
            # --- 地缘倾斜: 对抗升级时压低出口链、抬升自主可控 ---
            if geo_df is not None and len(geo_df.dropna(subset=["geo_score"])):
                gs = float(geo_df["geo_score"].dropna().iloc[-1])
                tilted = sector_tilt(sector["latest"], gs, geo_cfg)
                sector["latest_pre_geo"] = sector["latest"]
                sector["latest"] = tilted.assign(score=tilted["score_geo"])
                sector["latest"].attrs = tilted.attrs
                moved = tilted[tilted["geo_tilt"].abs() >= 2.0]
                if len(moved):
                    print(f"\n[Geo] 对抗分 {gs:.1f} 对 {len(moved)} 个行业做了倾斜:")
                    for c, r in moved.head(8).iterrows():
                        print(f"    {r['name']:<10} {r['geo_tilt']:+5.1f} 分  "
                              f"(敏感度 {r['geo_sensitivity']:+.1f})")

            whitelist = build_sector_whitelist(sector["latest"], scfg,
                                               top_n=args.sector_top)
            print_sector_report(sector["latest"], whitelist)

    # --- Step 5: 输出 ---
    print("=" * 74)
    print("Step 5: 输出")
    print("=" * 74)
    tag = (args.date or pd.Timestamp(regime_df.index[-1]).strftime("%Y%m%d"))

    reg_path = OUTPUT_DIR / f"macro_regime_{tag}.csv"
    keep = ["trend_score", "heat_score", "stress_score", "regime", "regime_name",
            "base_position", "vol_mult", "dd_mult", "leader_cap",
            "target_position", "divergence", "ewi_drawdown_pct",
            "event_cap", "in_geo_window", "next_event", "days_to_event",
            "target_position_geo"]
    regime_df[[c for c in keep if c in regime_df.columns]].to_csv(
        reg_path, encoding="utf-8-sig")

    fac_path = OUTPUT_DIR / f"macro_factors_{tag}.csv"
    factors.drop(columns=[c for c in factors.columns if c.startswith("_")],
                 errors="ignore").to_csv(fac_path, encoding="utf-8-sig")

    print(f"  状态序列: {reg_path}")
    print(f"  因子明细: {fac_path}")

    if sector is not None:
        sp = OUTPUT_DIR / f"sector_weekly_{tag}.csv"
        sector["latest"].to_csv(sp, encoding="utf-8-sig")
        hp = OUTPUT_DIR / f"sector_history_{tag}.csv"
        sector["history"].to_csv(hp, encoding="utf-8-sig")
        print(f"  板块强度: {sp}")
        print(f"  板块历史: {hp}")
        if whitelist is not None and not whitelist.empty:
            wp = OUTPUT_DIR / f"sector_whitelist_{tag}.csv"
            whitelist.to_csv(wp, encoding="utf-8-sig")
            print(f"  板块白名单: {wp}")

    # --- Step 6: 用白名单过滤选股结果 ---
    if args.filter_picks and whitelist is not None and industry_map is not None:
        pick_path = Path(args.filter_picks)
        if pick_path.exists():
            picks = pd.read_csv(pick_path)
            kept = filter_stocks_by_whitelist(picks, industry_map, whitelist)
            dropped = kept.attrs.get("dropped", pd.DataFrame())
            out_path = pick_path.with_name(pick_path.stem + "_whitelisted.csv")
            kept.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n  选股过滤: {len(picks)} -> {len(kept)} 只 (剔除 {len(dropped)})")
            print(f"  {out_path}")
            if not dropped.empty and "industry_name" in dropped.columns:
                bad = dropped["industry_name"].value_counts().head(5)
                print(f"  被剔除最多的行业: "
                      f"{', '.join(f'{k}({v})' for k, v in bad.items())}")
        else:
            print(f"\n  [警告] 选股文件不存在: {pick_path}")

    # --- Step 7: 仪表盘 ---
    html_text = build_dashboard(regime_df, factors, sector, whitelist, rcfg,
                                geo_df=geo_df)
    html_path = OUTPUT_DIR / f"macro_dashboard_{tag}.html"
    save_dashboard(html_text, html_path)
    print(f"  仪表盘: {html_path.resolve()}")

    if not args.no_open:
        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:
            pass

    print(f"\n总耗时 {time.time()-t_start:.1f}s")


if __name__ == "__main__":
    main()
