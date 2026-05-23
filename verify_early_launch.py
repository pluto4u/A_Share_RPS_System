"""
================================================================================
早期启涨 召回率验证 (反向回放)
================================================================================
目的: 用 20260417 火车轨入选的 22 只 (target_codes) 当作"标签"，
     回放它们各自的"启涨日"，看当天的 EarlyLaunchConfig 能不能命中。

逻辑:
    1. 读 huoche.csv 拿到 22 只 + 它们的启涨日 (从 huoche_picks_anatomy.csv 已算出)
    2. 对每只 stock 在其启涨日 + N 天 (N=3,5,7) 这几个时间点跑 early_launch
    3. 命中 = 那个时间点 EL_ALL=True
    4. 输出召回率表 + 未命中的票"启涨日"位面分析

用法:
    py -3 verify_early_launch.py --date 20260417
"""

import argparse
import pandas as pd
import numpy as np
from pathlib import Path

from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig, _add_indicators, _scan_one,
)

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260417")
    parser.add_argument("--anatomy",
                        default=str(OUTPUT_DIR / "huoche_picks_anatomy_20260417.csv"))
    args = parser.parse_args()

    target_dt = pd.Timestamp(args.date)
    df = load_data()

    if not Path(args.anatomy).exists():
        raise FileNotFoundError(
            f"启涨日明细不存在: {args.anatomy} —— 先跑 analyze_huoche_picks.py"
        )
    anatomy = pd.read_csv(args.anatomy)
    print(f"标签集: {len(anatomy)} 只火车轨入选股")

    # 解析启涨日 (anatomy 里 launch_date 是 MM-DD 格式，需补年份)
    target_year = target_dt.year
    def parse_launch(d):
        try:
            return pd.Timestamp(f"{target_year}-{d}")
        except Exception:
            return pd.NaT
    anatomy["launch_dt"] = anatomy["launch_date"].apply(parse_launch)

    # ========================================================================
    # 多组配置 + 多个观察日 (启涨后第 N 天) 做交叉
    # ========================================================================
    configs = {
        "default": EarlyLaunchConfig(),
        "loose":   EarlyLaunchConfig(rps_min=60, rps_max=95, seg_ret_min=0.05, seg_ret_max=0.35,
                                     vs_ma60_max=0.30, bo_vol_x=1.3,
                                     near_high_min=0.80, pre_vol_ratio_max=1.30),
        "strict":  EarlyLaunchConfig(rps_min=75, rps_max=92, seg_ret_min=0.10, seg_ret_max=0.25,
                                     vs_ma60_max=0.20, bo_vol_x=1.7),
        # 基于诊断: 去 RPS 上限 + 放宽段涨幅/放量/控位
        "tunedA":  EarlyLaunchConfig(rps_min=70, rps_max=100,
                                     seg_ret_min=0.05, seg_ret_max=0.45,
                                     vs_ma60_max=0.40, bo_vol_x=1.3, bo_pct_min=0.025,
                                     near_high_min=0.85, pre_vol_ratio_max=1.30),
        # 进一步保守: RPS 抬高到 80 (火车轨候选 RPS 启涨前都 >=80) + 控位 0.35
        "tunedB":  EarlyLaunchConfig(rps_min=80, rps_max=100,
                                     seg_ret_min=0.05, seg_ret_max=0.40,
                                     vs_ma60_max=0.35, bo_vol_x=1.3, bo_pct_min=0.025,
                                     near_high_min=0.85, pre_vol_ratio_max=1.30),
    }

    offsets = [0, 3, 5, 7, 10]   # 启涨后第 N 个交易日

    rows = []
    print("\n" + "=" * 110)
    print(f"{'代码':<11}{'启涨':<8}{'config':<10}", end="")
    for o in offsets:
        print(f"{'D+'+str(o):<7}", end="")
    print()
    print("=" * 110)

    for cfg_name, cfg in configs.items():
        for _, row in anatomy.iterrows():
            code = row["ts_code"]
            ldt = row["launch_dt"]
            if pd.isna(ldt):
                continue
            sdf_full = df[df["ts_code"] == code].copy()
            if sdf_full.empty:
                continue
            sdf_ind = _add_indicators(sdf_full)
            dates = sdf_ind["trade_date"].sort_values().unique()
            launch_idx = np.searchsorted(dates, ldt)
            if launch_idx >= len(dates):
                continue

            hits = {}
            for o in offsets:
                idx = launch_idx + o
                if idx >= len(dates):
                    hits[o] = None
                    continue
                obs_dt = dates[idx]
                if obs_dt > target_dt:
                    hits[o] = None
                    continue
                rec = _scan_one(sdf_ind, obs_dt, cfg)
                hits[o] = bool(rec["EL_ALL"]) if rec else False

            rows.append({"ts_code": code, "config": cfg_name, "launch_dt": ldt, **hits})

    res = pd.DataFrame(rows)

    # ========================================================================
    # 召回率表
    # ========================================================================
    print("\n召回率 (各 config × 启涨后第 D 天命中比例)")
    print("-" * 70)
    print(f"{'config':<12}", end="")
    for o in offsets:
        print(f"  D+{o:<5}", end="")
    print(f"  {'any_hit':<8}")
    for cfg_name in configs.keys():
        sub = res[res["config"] == cfg_name]
        n = len(sub)
        line = f"{cfg_name:<12}"
        any_hit = pd.Series([False] * n, index=sub.index)
        for o in offsets:
            valid = sub[o].notna()
            hit = (sub[o] == True) & valid
            rate = hit.sum() / valid.sum() if valid.sum() > 0 else 0
            line += f"  {rate*100:5.1f}%"
            any_hit = any_hit | hit
        any_rate = any_hit.sum() / n if n > 0 else 0
        line += f"   {any_rate*100:5.1f}%"
        print(line)

    # ========================================================================
    # 漏选分析 (default config 下 any_hit=False 的票, 看为啥不命中)
    # ========================================================================
    print("\n" + "=" * 110)
    print("漏选诊断 (default config, 启涨日 D+0~D+10 都不命中的票)")
    print("=" * 110)
    sub = res[res["config"] == "default"].copy()
    sub["any_hit"] = sub[offsets].apply(lambda r: any(v == True for v in r if v is not None), axis=1)
    miss = sub[~sub["any_hit"]]
    print(f"漏选数: {len(miss)} / {len(sub)}")

    if len(miss):
        print("\n漏选诊断 — 取启涨日 +5 时刻的各 EL 条件:")
        cfg = configs["default"]
        diag_rows = []
        for _, m in miss.iterrows():
            code = m["ts_code"]
            ldt = m["launch_dt"]
            sdf_full = df[df["ts_code"] == code].copy()
            sdf_ind = _add_indicators(sdf_full)
            dates = sdf_ind["trade_date"].sort_values().unique()
            li = np.searchsorted(dates, ldt)
            obs_idx = min(li + 5, len(dates) - 1)
            obs_dt = dates[obs_idx]
            if obs_dt > target_dt:
                continue
            rec = _scan_one(sdf_ind, obs_dt, cfg)
            if rec:
                diag_rows.append({
                    "ts_code": code,
                    "obs": obs_dt.strftime("%m-%d"),
                    "RPS": round(rec["RPS_120"], 1),
                    "streak": rec["streak_above_ma20"],
                    "seg_ret%": round(rec["seg_ret"] * 100, 1),
                    "bo_n": rec["bo_count"],
                    "vs60%": round(rec["vs_ma60"] * 100, 1),
                    "near_h%": round(rec["near_high_ratio"] * 100, 1),
                    "pre_vol": round(rec["pre_vol_ratio"], 2) if pd.notna(rec["pre_vol_ratio"]) else None,
                    "EL1": rec["EL1_rps"], "EL2": rec["EL2_streak"],
                    "EL3": rec["EL3_seg_ret"], "EL4": rec["EL4_bo_volume"],
                    "EL5": rec["EL5_pos"], "EL6": rec["EL6_near_high"],
                    "EL7": rec["EL7_pre_quiet"],
                })
        diag_df = pd.DataFrame(diag_rows)
        if not diag_df.empty:
            print(diag_df.to_string(index=False))
            print("\n各 EL 条件的失败计数 (越高越关键):")
            for c in ["EL1", "EL2", "EL3", "EL4", "EL5", "EL6", "EL7"]:
                fail = (~diag_df[c]).sum()
                print(f"  {c}_fail: {fail}/{len(diag_df)}")

    # 保存
    out = OUTPUT_DIR / f"early_recall_{args.date}.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  明细保存: {out}")


if __name__ == "__main__":
    main()
