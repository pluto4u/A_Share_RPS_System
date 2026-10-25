"""用本地通达信日线比较 AI 硬件方向的量价与次日博弈价值。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rps_analyzer.ma20_screener import Ma20Config, _add_indicators, _quality_checked_window, resolve_target_date
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from run_ma20_screener import DEFAULT_HQ_CACHE, _is_risk_name, _load_tdx_name_master


SECTOR_POOLS: dict[str, dict[str, str]] = {
    "光模块与光器件": {
        "300308.SZ": "中际旭创", "300502.SZ": "新易盛", "300394.SZ": "天孚通信",
        "000988.SZ": "华工科技", "002281.SZ": "光迅科技", "603083.SH": "剑桥科技",
        "300570.SZ": "太辰光", "688498.SH": "源杰科技", "688313.SH": "仕佳光子",
        "300620.SZ": "光库科技", "301205.SZ": "联特科技", "688205.SH": "德科立",
        "300548.SZ": "长芯博创",
    },
    "PCB与材料": {
        "600183.SH": "生益科技", "002463.SZ": "沪电股份", "300476.SZ": "胜宏科技",
        "002916.SZ": "深南电路", "603228.SH": "景旺电子", "688183.SH": "生益电子",
        "002815.SZ": "崇达技术", "600601.SH": "方正科技", "002384.SZ": "东山精密",
        "002636.SZ": "金安国纪", "603186.SH": "华正新材", "688519.SH": "南亚新材",
        "301217.SZ": "铜冠铜箔", "688388.SH": "嘉元科技",
    },
    "存储与接口": {
        "001309.SZ": "德明利", "688525.SH": "佰维存储", "301308.SZ": "江波龙",
        "300475.SZ": "香农芯创", "688766.SH": "普冉股份", "603986.SH": "兆易创新",
        "688008.SH": "澜起科技", "300223.SZ": "北京君正", "688110.SH": "东芯股份",
    },
    "半导体设备": {
        "688012.SH": "中微公司", "002371.SZ": "北方华创", "688072.SH": "拓荆科技",
        "688120.SH": "华海清科", "688082.SH": "盛美上海", "688037.SH": "芯源微",
        "300567.SZ": "精测电子", "300604.SZ": "长川科技",
    },
}


@dataclass(frozen=True)
class RotationConfig:
    target_date: str = "2026-08-12"
    min_history: int = 130
    min_amount_yi: float = 10.0
    min_vr20: float = 1.05
    max_vr20: float = 1.80
    min_clv: float = 0.65
    min_rps20: float = 70.0
    max_ret1_pct: float = 7.0
    max_ret5_pct: float = 15.0
    max_vs_ma20_pct: float = 10.0


def _score(row: pd.Series, cfg: RotationConfig) -> float:
    score = 0.0
    score += 5 if row["close"] > row["ma5"] else 0
    score += 5 if row["ma5"] > row["ma10"] else 0
    score += 5 if row["ma10"] > row["ma20"] else 0
    score += 5 if row["ma20_slope5_pct"] > 0 else 0
    score += 5 if 0 <= row["close_vs_ma20_pct"] <= cfg.max_vs_ma20_pct else 0

    # Separate fresh rebound strength (5/10d) from medium-term trend strength
    # (20/60d).  This prevents a deep July drawdown from mechanically rejecting
    # every improving August setup, while still penalising weak larger trends.
    score += float(np.clip((row["rps5"] - 50) / 50, 0, 1) * 8)
    score += float(np.clip((row["rps10"] - 50) / 50, 0, 1) * 8)
    score += float(np.clip((row["rps20"] - 50) / 50, 0, 1) * 4)
    score += float(np.clip((row["rps60"] - 50) / 50, 0, 1) * 5)

    vr = row["vr20_prior"]
    score += 10 if cfg.min_vr20 <= vr <= cfg.max_vr20 else (5 if 0.80 <= vr < cfg.min_vr20 else 0)
    score += 5 if row["clv"] >= cfg.min_clv else 0
    score += 5 if row["upper_shadow_pct"] <= 3.0 else 0

    score += 5 if 1.0 <= row["ret1_pct"] <= cfg.max_ret1_pct else (2 if -1 <= row["ret1_pct"] < 1 else 0)
    score += 5 if row["ret5_pct"] <= cfg.max_ret5_pct else 0
    score += 5 if row["close_vs_ma20_pct"] <= cfg.max_vs_ma20_pct else 0

    score += 10 if row["amount_yi"] >= cfg.min_amount_yi else (
        7 if row["amount_yi"] >= 5 else (4 if row["amount_yi"] >= 1 else 0)
    )
    score += 5 if row["sector_breadth_above_ma5"] >= 0.60 else (
        3 if row["sector_breadth_above_ma5"] >= 0.50 else 0
    )
    return round(float(np.clip(score, 0, 100)), 1)


def analyze_rotation(
    daily: pd.DataFrame,
    cfg: RotationConfig,
    *,
    risk_codes: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    target = resolve_target_date(daily, cfg.target_date, coverage_ratio=0.90, lookback=10)
    if target != pd.Timestamp(cfg.target_date):
        raise ValueError(f"{cfg.target_date} 数据覆盖不足，最新可靠交易日为 {target:%Y-%m-%d}")

    ma_cfg = Ma20Config(min_bars=cfg.min_history)
    work, audit = _quality_checked_window(daily, target, ma_cfg)
    risk_codes = risk_codes or set()
    work = work[~work["ts_code"].isin(risk_codes)].copy()
    work = _add_indicators(work, ma_cfg)
    group = work.groupby("ts_code", sort=False)
    close_group = group["close"]
    vol_group = group["vol"]

    work["ma5"] = close_group.transform(lambda x: x.rolling(5, min_periods=5).mean())
    work["ma10"] = close_group.transform(lambda x: x.rolling(10, min_periods=10).mean())
    work["ret3"] = close_group.pct_change(3, fill_method=None)
    work["ret10"] = close_group.pct_change(10, fill_method=None)
    work["ret20"] = close_group.pct_change(20, fill_method=None)
    work["ret60"] = close_group.pct_change(60, fill_method=None)
    work["vol20_prior"] = group["vol"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=20).mean()
    )
    work["vr20_prior"] = work["vol"] / work["vol20_prior"].replace(0, np.nan)
    work["prior20_high"] = group["high"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=20).max()
    )
    work["prior60_high"] = group["high"].transform(
        lambda x: x.shift(1).rolling(60, min_periods=60).max()
    )
    work["ma20_slope5_pct"] = work["ma20_slope"] * 100
    work["close_vs_ma20_pct"] = work["vs_ma20"] * 100

    target_rows = work[work["trade_date"].eq(target)].copy()
    target_rows["rps5"] = target_rows["ret5"].rank(pct=True) * 100
    target_rows["rps10"] = target_rows["ret10"].rank(pct=True) * 100
    target_rows["rps20"] = target_rows["ret20"].rank(pct=True) * 100
    target_rows["rps60"] = target_rows["ret60"].rank(pct=True) * 100

    code_to_sector = {
        code: sector for sector, codes in SECTOR_POOLS.items() for code in codes
    }
    selected_codes = set(code_to_sector)
    selected = target_rows[target_rows["ts_code"].isin(selected_codes)].copy()
    selected["sector"] = selected["ts_code"].map(code_to_sector)
    fallback_names = {code: name for codes in SECTOR_POOLS.values() for code, name in codes.items()}
    selected["pool_name"] = selected["ts_code"].map(fallback_names)

    recent = work[work["ts_code"].isin(selected_codes)].copy()
    recent["ret_daily"] = recent.groupby("ts_code")["close"].pct_change(fill_method=None)
    extra_rows: list[dict] = []
    for code, stock in recent.groupby("ts_code", sort=False):
        stock = stock.sort_values("trade_date")
        today = stock.iloc[-1]
        last10 = stock.tail(10)
        up_vol = float(last10.loc[last10["ret_daily"] > 0, "vol"].sum())
        down_vol = float(last10.loc[last10["ret_daily"] < 0, "vol"].sum())
        extra_rows.append(
            {
                "ts_code": code,
                "vol_contraction3": float(stock.tail(3)["vol"].mean() / stock.iloc[-23:-3]["vol"].mean()),
                "up_down_vol_ratio10": min(up_vol / down_vol, 5.0) if down_vol > 0 else 5.0,
                "min_ret60_pct": float(stock.tail(60)["ret_daily"].min() * 100),
            }
        )
    selected = selected.merge(pd.DataFrame(extra_rows), on="ts_code", how="left", validate="one_to_one")

    day_range = (selected["high"] - selected["low"]).replace(0, np.nan)
    selected["ret1_pct"] = selected["ret1"] * 100
    selected["ret3_pct"] = selected["ret3"] * 100
    selected["ret5_pct"] = selected["ret5"] * 100
    selected["ret10_pct"] = selected["ret10"] * 100
    selected["ret20_pct"] = selected["ret20"] * 100
    selected["ret60_pct"] = selected["ret60"] * 100
    selected["vr20_inclusive"] = selected["vol_ratio20"]
    selected["clv"] = (selected["close"] - selected["low"]) / day_range
    selected["upper_shadow_pct"] = (
        (selected["high"] - selected[["open", "close"]].max(axis=1)) / selected["close"] * 100
    )
    selected["amount_yi"] = selected["amount"] / 1e8
    selected["amount20_yi"] = selected["amount20"] / 1e8
    selected["breakout20_pct"] = (selected["close"] / selected["prior20_high"] - 1) * 100
    selected["below_60d_high_pct"] = (selected["close"] / selected["prior60_high"] - 1) * 100
    selected["ma5_above_ma10_pct"] = (selected["ma5"] / selected["ma10"] - 1) * 100
    selected["ma10_above_ma20_pct"] = (selected["ma10"] / selected["ma20"] - 1) * 100
    selected["suspected_action"] = selected["min_ret60_pct"] < -22

    sector_summary = (
        selected.groupby("sector")
        .agg(
            stocks=("ts_code", "size"),
            median_ret1_pct=("ret1_pct", "median"),
            median_ret5_pct=("ret5_pct", "median"),
            median_ret20_pct=("ret20_pct", "median"),
            median_vr20_prior=("vr20_prior", "median"),
            median_rps20=("rps20", "median"),
            breadth_positive=("ret1_pct", lambda x: float((x > 0).mean())),
            breadth_above_ma5=("close", lambda x: 0.0),
        )
        .reset_index()
    )
    breadth_ma5 = selected.groupby("sector").apply(
        lambda x: float((x["close"] > x["ma5"]).mean()), include_groups=False
    )
    breadth_ma20 = selected.groupby("sector").apply(
        lambda x: float((x["close"] > x["ma20"]).mean()), include_groups=False
    )
    sector_summary["breadth_above_ma5"] = sector_summary["sector"].map(breadth_ma5)
    sector_summary["breadth_above_ma20"] = sector_summary["sector"].map(breadth_ma20)
    sector_summary = sector_summary.sort_values(
        ["median_ret1_pct", "median_rps20"], ascending=[False, False]
    ).reset_index(drop=True)
    selected["sector_breadth_above_ma5"] = selected["sector"].map(breadth_ma5)

    formula_pass = (
        (selected["close"] > selected["open"])
        & (selected["close"] > selected["ma5"])
        & (selected["ma5"] > selected["ma10"])
        & selected["ret1_pct"].between(1.0, cfg.max_ret1_pct, inclusive="both")
        & selected["vr20_prior"].between(cfg.min_vr20, cfg.max_vr20, inclusive="both")
        & (selected["clv"] >= cfg.min_clv)
        & (selected["upper_shadow_pct"] <= 3.0)
        & (selected["rps20"] >= cfg.min_rps20)
        & (selected["amount_yi"] >= cfg.min_amount_yi)
    )
    selected["formula_pass"] = formula_pass
    selected["hard_overextended"] = (
        (selected["ret5_pct"] > cfg.max_ret5_pct)
        | (selected["close_vs_ma20_pct"] > cfg.max_vs_ma20_pct)
        | (selected["ret1_pct"] > cfg.max_ret1_pct)
    )
    selected["trade_score"] = selected.apply(_score, axis=1, cfg=cfg)
    selected["trade_grade"] = np.select(
        [
            selected["hard_overextended"],
            selected["formula_pass"] & (selected["trade_score"] >= 75),
            (selected["trade_score"] >= 65) & ~selected["hard_overextended"],
        ],
        ["C_过热不追", "A_量价通过", "B_等待触发"],
        default="C_暂不博弈",
    )
    selected["trigger_price"] = selected["high"] * 1.002
    # Tactical invalidation uses the nearer of MA10 and the signal-day low.
    # A wider structural stop is not suitable for a T+1 next-day entry plan.
    selected["invalidation_price"] = selected[["ma10", "low"]].max(axis=1) * 0.995
    selected["trigger_risk_pct"] = (
        selected["trigger_price"] / selected["invalidation_price"] - 1
    ) * 100
    selected["name"] = selected["pool_name"]
    selected = selected.sort_values(
        ["trade_grade", "trade_score", "rps20"], ascending=[True, False, False]
    ).reset_index(drop=True)

    audit.update(
        {
            "strategy_config": asdict(cfg),
            "target_rows": int(len(target_rows)),
            "pool_expected": int(len(selected_codes)),
            "pool_matched": int(len(selected)),
            "pool_missing_codes": sorted(selected_codes - set(selected["ts_code"])),
            "risk_codes_excluded": int(len(risk_codes & set(target_rows["ts_code"]))),
            "formula_pass": int(selected["formula_pass"].sum()),
            "grade_a": int(selected["trade_grade"].eq("A_量价通过").sum()),
            "grade_b": int(selected["trade_grade"].eq("B_等待触发").sum()),
            "overextended": int(selected["hard_overextended"].sum()),
        }
    )
    return selected, sector_summary, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--target-date", default="2026-08-12")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE))
    args = parser.parse_args()

    cfg = RotationConfig(target_date=args.target_date)
    daily = load_tdx_market(args.tdx_dir, min_days=cfg.min_history, verbose=True)
    names = _load_tdx_name_master(Path(args.hq_cache))
    risk_codes = {code for code, name in names.items() if _is_risk_name(name)}
    stocks, sectors, audit = analyze_rotation(daily, cfg, risk_codes=risk_codes)
    stocks["name"] = stocks["ts_code"].map(names).fillna(stocks["name"])

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = args.target_date.replace("-", "")
    stock_path = out / f"ai_hardware_rotation_{stamp}.csv"
    sector_path = out / f"ai_hardware_rotation_{stamp}_SECTORS.csv"
    audit_path = out / f"ai_hardware_rotation_{stamp}_audit.json"
    stocks.to_csv(stock_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    sectors.to_csv(sector_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    cols = [
        "sector", "ts_code", "name", "trade_grade", "trade_score", "ret1_pct",
        "ret5_pct", "vr20_prior", "vr20_inclusive", "clv", "amount_yi", "rps5",
        "rps10", "rps20",
        "close_vs_ma20_pct", "trigger_price", "invalidation_price",
    ]
    print("\n板块汇总")
    print(sectors.to_string(index=False))
    print("\n候选Top 20")
    print(stocks.head(20)[cols].to_string(index=False))
    print("\n审计")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
