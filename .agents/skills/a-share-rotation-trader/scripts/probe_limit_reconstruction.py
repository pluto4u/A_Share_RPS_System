#!/usr/bin/env python3
"""决定性探测：能否用本地 TDX 日线重建涨停生态（涨停/跌停/连板/晋级率）。

对齐东财涨停池真实值作为校验集：
  20260814 -> 63, 20260813 -> 59, 20260731 -> 99

若重建误差可接受，则涨停生态拥有全历史本地源，ST_cycle 可保留并可回测；
若不可接受，按“数据拿不到就摒弃”原则删除 ST_cycle。
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd

GROUND_TRUTH = {"20260814": 63, "20260813": 59, "20260731": 99}
DEFAULT_HQ = Path(r"C:\new_tdx\T0002\hq_cache")

RECORD_SIZE = 32
RECORD_DTYPE = np.dtype([
    ("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
    ("close", "<u4"), ("amount", "<f4"), ("vol", "<u4"), ("reserved", "<u4"),
])
PREFIX_SH = ("600", "601", "603", "605", "688", "689")
PREFIX_SZ = ("000", "001", "002", "003", "300", "301")


def load_tail(tdx_root: Path, n_bars: int, min_bars: int) -> pd.DataFrame:
    """只读每个 .day 文件末尾 n_bars 条记录，纯 numpy 累积后一次性建表。"""
    chunks: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    for market, prefixes in (("sh", PREFIX_SH), ("sz", PREFIX_SZ)):
        lday = tdx_root / market / "lday"
        if not lday.exists():
            continue
        files = [p for p in lday.glob("*.day") if p.name[2:5] in prefixes]
        print(f"[TDX] {market.upper()} A股文件 {len(files)} 个，读取末尾 {n_bars} 条",
              flush=True)
        suffix = market.upper()
        for i, path in enumerate(files):
            n_avail = path.stat().st_size // RECORD_SIZE
            if n_avail < min_bars:
                continue
            take = min(n_bars, n_avail)
            with path.open("rb") as handle:
                handle.seek((n_avail - take) * RECORD_SIZE)
                rec = np.frombuffer(handle.read(take * RECORD_SIZE), dtype=RECORD_DTYPE)
            chunks.append(rec)
            codes.append(np.full(take, f"{path.name[2:8]}.{suffix}", dtype=object))
            if (i + 1) % 2000 == 0:
                print(f"  [{suffix}] {i + 1}/{len(files)}", flush=True)
    if not chunks:
        raise RuntimeError(f"未读取到任何数据: {tdx_root}")

    rec = np.concatenate(chunks)
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(rec["date"].astype("int64").astype(str),
                                     format="%Y%m%d", errors="coerce"),
        "ts_code": np.concatenate(codes),
        "open": rec["open"] / 100.0,
        "high": rec["high"] / 100.0,
        "low": rec["low"] / 100.0,
        "close": rec["close"] / 100.0,
        "amount": rec["amount"].astype(np.float64),
        "vol": rec["vol"].astype(np.float64),
    }).dropna(subset=["trade_date"])
    print(f"[TDX] 共 {out['ts_code'].nunique()} 只 / {len(out)} 行，"
          f"最新 {out['trade_date'].max():%Y-%m-%d}", flush=True)
    return out


def load_name_master(hq_cache: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for market, filename in (("SH", "shs.tnf"), ("SZ", "szs.tnf")):
        path = hq_cache / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        if len(raw) < 50 or (len(raw) - 50) % 360:
            continue
        for offset in range(50, len(raw), 360):
            record = raw[offset : offset + 360]
            code = record[:6].decode("ascii", errors="ignore")
            name = record[31:63].split(b"\0", 1)[0].decode("gbk", errors="ignore").strip()
            if code.isdigit() and name:
                result[f"{code}.{market}"] = name
    return result


def is_risk_name(name: str) -> bool:
    compact = str(name).replace(" ", "").upper()
    return compact.startswith(("ST", "*ST")) or "退" in compact


def build(daily: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    work = daily.sort_values(["ts_code", "trade_date"]).copy()
    work["name"] = work["ts_code"].map(names).fillna("")
    work["prev_close"] = work.groupby("ts_code", sort=False)["close"].shift(1)
    work = work.dropna(subset=["prev_close"])

    compact = work["name"].str.replace(" ", "", regex=False).str.upper()
    work["is_risk"] = compact.str.startswith(("ST", "*ST")) | compact.str.contains("退")
    wide = work["ts_code"].str[:3].isin(("300", "301", "688", "689"))

    # 涨跌幅限制不能只靠名称推断：本地名称表会滞后于撤销 ST 的公告，
    # 会把已恢复 10% 的股票按 5% 判定而虚增涨停。改用实测回退：
    # 只有近 60 个交易日最大绝对涨跌幅未超过 6% 时，才认定处于 5% 限制。
    work["ret1_raw"] = work["close"] / work["prev_close"] - 1.0
    trailing_max = (
        work.groupby("ts_code", sort=False)["ret1_raw"]
        .transform(lambda s: s.abs().rolling(60, min_periods=10).max().shift(1))
    )
    narrow_confirmed = work["is_risk"] & (trailing_max <= 0.06)
    # 历史不足无法实测时保守按 10%，宁可漏判也不虚增涨停
    pct = np.where(wide, 0.20, np.where(narrow_confirmed.fillna(False), 0.05, 0.10))
    work["limit_pct"] = pct
    work["limit_regime"] = np.where(wide, "20cm", np.where(narrow_confirmed.fillna(False), "5cm", "10cm"))
    # 交易所限价：前收 × (1±pct) 四舍五入到分
    work["limit_up_price"] = np.round(work["prev_close"] * (1 + pct) + 1e-9, 2)
    work["limit_dn_price"] = np.round(work["prev_close"] * (1 - pct) + 1e-9, 2)
    work["ret1"] = work["close"] / work["prev_close"] - 1.0

    tol = 0.004
    work["is_limit_up"] = (work["close"] >= work["limit_up_price"] - tol) & (work["vol"] > 0)
    work["is_limit_dn"] = (work["close"] <= work["limit_dn_price"] + tol) & (work["vol"] > 0)
    # 盘中触及涨停（用于炸板判定）
    work["touched_up"] = (work["high"] >= work["limit_up_price"] - tol) & (work["vol"] > 0)
    work["broken"] = work["touched_up"] & (~work["is_limit_up"])
    # 一字板：开=高=低=收=涨停价
    work["one_word"] = work["is_limit_up"] & (work["low"] >= work["limit_up_price"] - tol)
    # 未复权导致的异常跳空：跌幅超过限制 1.5 倍，视为除权/异常
    work["suspect_adjust"] = work["ret1"] < -(work["limit_pct"] * 1.5)
    return work


def consecutive_boards(work: pd.DataFrame) -> pd.DataFrame:
    """连板高度：当日涨停则=昨日高度+1，否则 0。按股票分组向量化。"""
    work = work.sort_values(["ts_code", "trade_date"]).copy()
    up = work["is_limit_up"]
    grp = work["ts_code"]
    csum = up.groupby(grp, sort=False).cumsum()
    # 每段非涨停处记录当时的 cumsum 作为基线，向前填充到后续涨停段
    base = csum.where(~up).groupby(grp, sort=False).ffill().fillna(0)
    work["board_height"] = (csum - base).astype(int).where(up, 0)
    return work


def daily_ecology(work: pd.DataFrame) -> pd.DataFrame:
    """逐日汇总涨停生态；晋级率按昨日候选集合逐股对齐。"""
    work = work.sort_values(["trade_date", "ts_code"])
    rows: list[dict] = []
    prev_sets: dict[str, set[str]] = {"N1": set(), "N2": set(), "NH": set()}
    prev_limit_up: set[str] = set()

    for date, day in work.groupby("trade_date", sort=True):
        tradable = day[~day["suspect_adjust"]]
        # 接力口径：剔除 ST 与一字板，保留全量广度统计
        relay = tradable[(~tradable["is_risk"]) & (~tradable["one_word"])]

        up_all = set(tradable.loc[tradable["is_limit_up"], "ts_code"])
        dn_all = set(tradable.loc[tradable["is_limit_dn"], "ts_code"])
        touched = set(tradable.loc[tradable["touched_up"], "ts_code"])
        broken = set(tradable.loc[tradable["broken"], "ts_code"])

        h = relay.set_index("ts_code")["board_height"]
        cur = {
            "N1": set(h[h == 1].index),
            "N2": set(h[h == 2].index),
            "N3": set(h[h == 3].index),
            "NH": set(h[h >= 4].index),
        }

        def rate(prev: set[str], target: set[str]) -> float | None:
            avail = prev & set(relay["ts_code"])
            if not avail:
                return None
            return len(avail & target) / len(avail)

        j1 = rate(prev_sets["N1"], cur["N2"])
        j2 = rate(prev_sets["N2"], cur["N3"])
        jh = rate(prev_sets["NH"], set(h[h >= 5].index))

        # 昨日涨停股今日表现（信息可见日归属到今日）
        ylr_set = prev_limit_up & set(tradable["ts_code"])
        if ylr_set:
            sub = tradable[tradable["ts_code"].isin(ylr_set)]
            ylr_close = float(sub["ret1"].mean() * 100)
            ylr_median = float(sub["ret1"].median() * 100)
            ylr_open = float((sub["open"] / sub["prev_close"] - 1).mean() * 100)
        else:
            ylr_close = ylr_median = ylr_open = None

        fr = len(broken) / len(touched) if touched else None

        rows.append({
            "trade_date": date,
            "stocks": int(len(tradable)),
            "limit_up_n": len(up_all),
            "limit_down_n": len(dn_all),
            "touched_up_n": len(touched),
            "broken_n": len(broken),
            "one_word_n": int(tradable["one_word"].sum()),
            "suspect_adjust_n": int(day["suspect_adjust"].sum()),
            "N1": len(cur["N1"]), "N2": len(cur["N2"]),
            "N3": len(cur["N3"]), "NH": len(cur["NH"]),
            "max_board": int(relay["board_height"].max()) if len(relay) else 0,
            "J1": j1, "J2": j2, "JH": jh,
            "FR": fr,
            "YLR_close": ylr_close, "YLR_open": ylr_open, "YLR_median": ylr_median,
            "B": len(up_all) / (len(up_all) + len(dn_all)) if (up_all or dn_all) else None,
            "total_amount_yi": float(tradable["amount"].sum() / 1e8),
        })
        prev_sets = {"N1": cur["N1"], "N2": cur["N2"], "NH": cur["NH"]}
        prev_limit_up = up_all

    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-dir", default=r"C:\new_tdx\vipdoc")
    ap.add_argument("--hq-cache", default=str(DEFAULT_HQ))
    ap.add_argument("--min-days", type=int, default=30)
    ap.add_argument("--tail", type=int, default=70, help="只汇总最近 N 个交易日")
    ap.add_argument("--output", default="output/limit_ecology_probe.csv")
    args = ap.parse_args()

    names = load_name_master(Path(args.hq_cache))
    print(f"[名称表] {len(names)} 条")

    daily = load_tail(Path(args.tdx_dir), n_bars=args.tail + 10, min_bars=args.min_days)

    work = consecutive_boards(build(daily, names))
    eco = daily_ecology(work).tail(args.tail).reset_index(drop=True)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    eco.to_csv(args.output, index=False, encoding="utf-8-sig")

    print("\n=== 与东财涨停池真实值对齐 ===")
    check = {}
    for ds, truth in GROUND_TRUTH.items():
        stamp = pd.Timestamp(ds)
        row = eco[eco["trade_date"] == stamp]
        if row.empty:
            print(f"  {ds}: 本地无该交易日")
            continue
        got = int(row.iloc[0]["limit_up_n"])
        err = (got - truth) / truth * 100
        check[ds] = {"reconstructed": got, "eastmoney": truth, "err_pct": round(err, 1)}
        print(f"  {ds}: 重建={got:3d}  东财={truth:3d}  误差={err:+.1f}%")

    print("\n=== 最近 12 个交易日生态 ===")
    cols = ["trade_date", "limit_up_n", "limit_down_n", "broken_n", "FR",
            "N1", "N2", "N3", "NH", "max_board", "J1", "J2", "YLR_close", "total_amount_yi"]
    view = eco[cols].tail(12).copy()
    view["trade_date"] = view["trade_date"].dt.strftime("%Y-%m-%d")
    print(view.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print("\n=== J2 缺失率诊断（决定 coverage 陷阱是否真实存在）===")
    for col in ("J1", "J2", "JH", "FR", "YLR_close"):
        miss = eco[col].isna().mean() * 100
        print(f"  {col:10s} 缺失 {miss:5.1f}%  有效 {eco[col].notna().sum():3d}/{len(eco)}")
    zero_n2 = (eco["N2"] == 0).mean() * 100
    print(f"  N2==0 的交易日占比: {zero_n2:.1f}%")

    Path("output/limit_reconstruction_check.json").write_text(
        json.dumps({"ground_truth_check": check,
                    "rows": len(eco),
                    "date_range": [str(eco['trade_date'].min()), str(eco['trade_date'].max())]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出: {args.output} 与 output/limit_reconstruction_check.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
