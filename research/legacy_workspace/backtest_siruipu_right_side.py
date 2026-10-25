#!/usr/bin/env python3
"""思瑞浦同形右侧方案：V1/V2 事件研究回测。

信号在 t 日收盘后形成，统一按 t+1 开盘进入；分别观察第 5/10/20
个交易日收盘收益。每个标的同一变体 20 个交易日内只保留第一次信号，
避免把一段行情连续多日重复计为独立样本。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
OUT_DIR = ROOT / "output"
ROUND_TRIP_COST = 0.002  # 佣金、印花税与保守滑点的合计研究假设


def ema(s: pd.Series, n: int) -> pd.Series:
    # 通达信 EMA 的递推形式对应 adjust=False；不设置 min_periods。
    return s.ewm(span=n, adjust=False).mean()


def bars_since_extreme(values: np.ndarray, n: int, mode: str) -> np.ndarray:
    out = np.full(len(values), np.nan)
    fn = np.argmin if mode == "min" else np.argmax
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        out[i] = int(fn(window[::-1]))
    return out


def no_prior_signal(signal: pd.Series, n: int = 20) -> pd.Series:
    prior = signal.shift(1).rolling(n, min_periods=1).max().fillna(False).astype(bool)
    return signal & ~prior


def board_limit(code: str) -> float:
    return 0.20 if code[:3] in ("300", "301", "688", "689") else 0.10


def primary_stage(yj: pd.Series, qd: pd.Series, qr: pd.Series, qqr: pd.Series) -> np.ndarray:
    return np.select([qqr, qr, qd, yj], ["QQR", "QR", "QD", "YJ"], default="")


def prepare_one(code: str, g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("trade_date").copy()
    c, h, l, v, amount = g["close"], g["high"], g["low"], g["vol"], g["amount"]
    for n in (5, 10, 20, 60, 120):
        g[f"M{n}"] = c.rolling(n, min_periods=n).mean()
    g["VM20"] = v.rolling(20, min_periods=20).mean()
    g["ADV20"] = amount.rolling(20, min_periods=20).mean()
    g["DIF"] = ema(c, 12) - ema(c, 26)
    g["DEA"] = ema(g["DIF"], 9)
    g["MH"] = 2 * (g["DIF"] - g["DEA"])
    hhv60 = h.rolling(60, min_periods=60).max()
    llv30 = l.rolling(30, min_periods=30).min()
    g["DD"] = (hhv60 - llv30) / hhv60 >= 0.28
    g["LOWAGE"] = bars_since_extreme(l.to_numpy(float), 30, "min")
    g["PEAKAGE"] = bars_since_extreme(h.to_numpy(float), 60, "max")
    g["LOWNEAR"] = g["LOWAGE"] <= 15
    g["ORDER_OK"] = g["PEAKAGE"] > g["LOWAGE"]
    g["HH5_REF"] = h.rolling(5, min_periods=5).max().shift(1)
    g["HH10_REF"] = h.rolling(10, min_periods=10).max().shift(1)
    g["HH20_REF"] = h.rolling(20, min_periods=20).max().shift(1)
    g["M5_REF2"] = g["M5"].shift(2)
    g["MH_REF1"] = g["MH"].shift(1)
    # RPS250 非空意味着当天已有至少 250 根自身历史，比用全样本总根数更适合历史回测。
    g["BASE_BT"] = g["RPS_250"].notna()

    yj1 = (
        g["DD"] & g["LOWNEAR"] & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (g["M5"] > g["M5_REF2"]) & (c > g["HH5_REF"])
        & (g["MH"] > 0) & (g["MH"] > g["MH_REF1"]) & (c / g["M20"] <= 1.12)
    )
    qd1 = (
        g["DD"] & g["LOWNEAR"] & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (c > g["M120"]) & (c > g["HH10_REF"]) & (v >= g["VM20"] * 1.10)
        & (g["MH"] > 0)
    )
    qr1 = (
        g["DD"] & (c > g["M60"]) & (g["M10"] > g["M20"])
        & (c > g["HH20_REF"]) & (g["MH"] > 0)
    )
    qqr1 = qr1 & (v >= g["VM20"] * 1.80) & (g["DIF"] > 0)

    # V2：修正“低点/高点顺序”、QR 低点过期、确认阶段追高和极端爆量。
    structural = g["ORDER_OK"] & g["LOWNEAR"]
    yj2 = yj1 & structural & (g["ADV20"] >= 1.0e8)
    qd2 = (
        qd1 & structural & (c / g["M20"] <= 1.15)
        & (v <= g["VM20"] * 3.0) & (g["ADV20"] >= 1.0e8)
    )
    qr2 = (
        qr1 & structural & (c / g["M20"] <= 1.18)
        & (g["ADV20"] >= 1.0e8)
    )
    qqr2 = qr2 & (v >= g["VM20"] * 1.80) & (g["DIF"] > 0)
    yj_final = yj2 & (c / g["M20"] <= 1.04)

    variants = {
        "V1_DISCOVERY": (yj1, qd1, qr1, qqr1, yj1 | qd1 | qr1 | qqr1),
        "V1_CONFIRM": (yj1, qd1, qr1, qqr1, qd1 | qr1 | qqr1),
        "V2_CORE": (yj2, qd2, qr2, qqr2, yj2 | qd2 | qr2 | qqr2),
        "V2_YJ": (yj2, qd2, qr2, qqr2, yj2),
        "V2_EARLY": (yj2, qd2, qr2, qqr2, yj2 | qd2),
        "V2_FINAL": (yj_final, qd2, qr2, qqr2, yj_final),
        "V2_PRIORITY": (yj_final, qd2, qr2, qqr2, yj_final & (g["RPS_120"] >= 60)),
        # 研究执行层：短中期相对强度与成交容量只做门控，不改变形态定义。
        "V2_GATED": (
            yj2, qd2, qr2, qqr2,
            (qd2 | qr2 | qqr2) & (g["RPS_50"] >= 60) & (g["RPS_120"] >= 60)
            & (g["ADV20"] >= 1.5e8),
        ),
        # V2_ENTRY：只把“启动”当作次日开盘研究信号；确认/强确认仅作持仓标签。
        # 中短 RPS 处于修复中段、MA20 乖离不高、容量不过热，阈值均在训练段固定。
        "V2_ENTRY": (
            yj2, qd2, qr2, qqr2,
            qd2 & g["RPS_50"].between(40, 80, inclusive="both")
            & (c / g["M20"] <= 1.10)
            & g["ADV20"].between(1.5e8, 8.0e9, inclusive="both"),
        ),
    }

    entry_open = g["open"].shift(-1)
    entry_gap = entry_open / c - 1.0
    limit_gap = board_limit(code) - 0.005
    executable = entry_open.notna() & (entry_gap < limit_gap)
    future_low10 = pd.concat([l.shift(-i) for i in range(1, 11)], axis=1).min(axis=1)
    future_high10 = pd.concat([h.shift(-i) for i in range(1, 11)], axis=1).max(axis=1)

    records = []
    for variant, (yj, qd, qr, qqr, trigger) in variants.items():
        trigger = (g["BASE_BT"] & trigger.fillna(False)).astype(bool)
        event = no_prior_signal(trigger, 20)
        if not event.any():
            continue
        cols = ["trade_date", "ts_code", "close", "RPS_50", "RPS_120", "RPS_250",
                "M20", "ADV20", "LOWAGE", "PEAKAGE"]
        part = g.loc[event, cols].copy()
        part["variant"] = variant
        part["stage"] = primary_stage(yj, qd, qr, qqr)[event.to_numpy()]
        part["entry_open"] = entry_open[event].to_numpy()
        part["entry_gap"] = entry_gap[event].to_numpy()
        part["executable"] = executable[event].to_numpy()
        part["vs_ma20"] = c[event].to_numpy() / g.loc[event, "M20"].to_numpy() - 1.0
        part["vol_ratio"] = v[event].to_numpy() / g.loc[event, "VM20"].to_numpy()
        for n in (5, 10, 20):
            gross = c.shift(-n)[event].to_numpy() / part["entry_open"].to_numpy() - 1.0
            part[f"ret{n}_gross"] = gross
            part[f"ret{n}_net"] = gross - ROUND_TRIP_COST
        part["mae10"] = future_low10[event].to_numpy() / part["entry_open"].to_numpy() - 1.0
        part["mfe10"] = future_high10[event].to_numpy() / part["entry_open"].to_numpy() - 1.0
        records.append(part)
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()


def summarize(events: pd.DataFrame, train_end: pd.Timestamp) -> list[dict]:
    rows: list[dict] = []
    splits = {
        "TRAIN": events[events["trade_date"] <= train_end],
        "TEST": events[events["trade_date"] > train_end],
        "ALL": events,
    }
    for split, frame in splits.items():
        for variant, g0 in frame.groupby("variant"):
            g = g0[g0["executable"]].copy()
            for horizon in (5, 10, 20):
                s = g[f"ret{horizon}_net"].dropna()
                if s.empty:
                    continue
                rows.append({
                    "split": split,
                    "variant": variant,
                    "horizon": horizon,
                    "n": int(len(s)),
                    "mean": float(s.mean()),
                    "median": float(s.median()),
                    "win_rate": float((s > 0).mean()),
                    "p10": float(s.quantile(0.10)),
                    "p90": float(s.quantile(0.90)),
                    "mae10_median": float(g.loc[s.index, "mae10"].median()),
                    "mfe10_median": float(g.loc[s.index, "mfe10"].median()),
                })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--start", default="2021-08-23")
    ap.add_argument("--train-end", default="2024-12-31")
    args = ap.parse_args()

    source = Path(args.input)
    start = pd.Timestamp(args.start)
    warmup = start - pd.Timedelta(days=550)
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
            "RPS_50", "RPS_120", "RPS_250"]
    print(f"[1] 读取 {source}")
    try:
        raw = pd.read_parquet(source, columns=cols, filters=[("trade_date", ">=", warmup)])
    except Exception:
        raw = pd.read_parquet(source, columns=cols)
        raw["trade_date"] = pd.to_datetime(raw["trade_date"])
        raw = raw[raw["trade_date"] >= warmup]
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    raw = raw.sort_values(["ts_code", "trade_date"])
    print(f"    {len(raw):,} 行，{raw['ts_code'].nunique():,} 只，{raw.trade_date.min().date()} → {raw.trade_date.max().date()}")

    print("[2] 逐股生成信号与次日开盘事件")
    parts = []
    for i, (code, g) in enumerate(raw.groupby("ts_code", sort=False), 1):
        part = prepare_one(code, g)
        if not part.empty:
            parts.append(part)
        if i % 500 == 0:
            print(f"    {i}/{raw['ts_code'].nunique()}")
    events = pd.concat(parts, ignore_index=True)
    events = events[events["trade_date"] >= start].sort_values(["trade_date", "ts_code", "variant"])

    asof = raw["trade_date"].max()
    tag = asof.strftime("%Y%m%d")
    event_path = OUT_DIR / f"siruipu_right_side_backtest_events_{tag}.parquet"
    summary_path = OUT_DIR / f"siruipu_right_side_backtest_{tag}.json"
    events.to_parquet(event_path, index=False)
    summary = summarize(events, pd.Timestamp(args.train_end))
    latest = events[events["trade_date"] == asof]
    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(source.as_posix()),
        "source_adjustment": "hithink local v_daily_qfq; volume/amount remain raw",
        "window": [start.strftime("%Y-%m-%d"), asof.strftime("%Y-%m-%d")],
        "train_end": args.train_end,
        "entry": "signal close t, enter next trading day open",
        "dedup": "same stock and same variant: first signal in a 20-trading-day episode",
        "cost": ROUND_TRIP_COST,
        "execution_filter": "exclude next open gap >= 9.5% main board or >=19.5% ChiNext/STAR",
        "historical_st_limit": "historical ST names unavailable; backtest omits the name-based ST exclusion",
        "event_rows": int(len(events)),
        "latest_counts": {str(k): int(v) for k, v in latest["variant"].value_counts().items()},
        "summary": summary,
        "events": str(event_path.as_posix()),
        "disclaimer": "neutral event study, not a portfolio return and not investment advice",
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "window": payload["window"], "event_rows": payload["event_rows"],
        "latest_counts": payload["latest_counts"], "events": str(event_path),
        "summary": str(summary_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
