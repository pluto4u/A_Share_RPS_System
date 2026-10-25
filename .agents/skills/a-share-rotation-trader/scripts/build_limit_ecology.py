#!/usr/bin/env python3
"""从本地通达信日线重建 A 股涨停生态，并计算 ST_cycle 与五状态仓位上限。

为什么用本地日线而不是第三方涨停池：
  东财涨停池只能取回约两周历史，炸板池/跌停池接口已失效（rc=206），
  无法满足本技能自身要求的 60 日滚动分位与走样本外回测。
  本地 .day 拥有全历史，重建结果已对齐东财真值（见 --validate）。

口径验证结论（2026-08-15）：
  20260814 重建 63 / 东财 63；20260813 重建 62 / 东财 59，多出 3 只全为 ST；
  连板高度逐股一致（62/62、59/59）。差异来自东财涨停池排除 ST，本脚本保留 ST
  于广度统计、并从接力指标中剔除，与规范一致。

覆盖边界：仅沪深 A 股。北交所（8xx/920xxx）不在本地 vipdoc 中，一并排除，
不得把本口径称为“全 A 股”。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

RECORD_SIZE = 32
RECORD_DTYPE = np.dtype([
    ("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
    ("close", "<u4"), ("amount", "<f4"), ("vol", "<u4"), ("reserved", "<u4"),
])
PREFIX_SH = ("600", "601", "603", "605", "688", "689")
PREFIX_SZ = ("000", "001", "002", "003", "300", "301")
WIDE_BOARD = ("300", "301", "688", "689")
DEFAULT_HQ = Path(r"C:\new_tdx\T0002\hq_cache")

# ST_cycle v1 权重。v0 的 5 因子高度共线（J1/J2/B 同由涨停生态驱动），
# 且完全排除了成交额；v1 把成交额分位提升为正式入分项。
# 任何权重变更都必须重跑 --collinearity 并记录版本。
WEIGHTS_V1 = {
    "J2_shrunk": 0.30,   # 中位接力意愿，短周期转折第一确认指标
    "YLR_close": 0.25,   # 等权追涨策略的实际收益
    "FR_inv": 0.20,      # 承接失败率的反向
    "AMT": 0.15,         # 两市成交额 60 日分位（v1 新增）
    "J1_shrunk": 0.10,   # 低位承接意愿
}
SHRINK_ALPHA = 3.0       # Beta 收缩的伪观测数
POOL_DAYS = 3            # 晋级率汇总天数：分子分母分别累加后再相除

# v1 仓位上限阶梯。v0 的 主升100% -> 高潮30% 是一步 70 个百分点，
# 在 T+1 且有跌停的市场里不可执行。改缓并配合每日最大降仓速率。
STATE_CAPS = {"冰点": 10.0, "修复": 35.0, "主升": 80.0, "高潮": 45.0, "退潮": 15.0}
MAX_DAILY_REDUCTION_PCT = 25.0


def load_tail(tdx_root: Path, n_bars: int, min_bars: int, verbose: bool = True) -> pd.DataFrame:
    """只读每个 .day 文件末尾 n_bars 条记录，纯 numpy 累积后一次性建表。"""
    chunks: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    for market, prefixes in (("sh", PREFIX_SH), ("sz", PREFIX_SZ)):
        lday = tdx_root / market / "lday"
        if not lday.exists():
            continue
        files = [p for p in lday.glob("*.day") if p.name[2:5] in prefixes]
        if verbose:
            print(f"[TDX] {market.upper()} A股 {len(files)} 只，读取末尾 {n_bars} 条", flush=True)
        suffix = market.upper()
        for path in files:
            n_avail = path.stat().st_size // RECORD_SIZE
            if n_avail < min_bars:
                continue
            take = min(n_bars, n_avail)
            with path.open("rb") as handle:
                handle.seek((n_avail - take) * RECORD_SIZE)
                rec = np.frombuffer(handle.read(take * RECORD_SIZE), dtype=RECORD_DTYPE)
            chunks.append(rec)
            codes.append(np.full(take, f"{path.name[2:8]}.{suffix}", dtype=object))
    if not chunks:
        raise RuntimeError(f"未读取到任何数据: {tdx_root}")

    rec = np.concatenate(chunks)
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(rec["date"].astype("int64").astype(str),
                                     format="%Y%m%d", errors="coerce"),
        "ts_code": np.concatenate(codes),
        "open": rec["open"] / 100.0, "high": rec["high"] / 100.0,
        "low": rec["low"] / 100.0, "close": rec["close"] / 100.0,
        "amount": rec["amount"].astype(np.float64), "vol": rec["vol"].astype(np.float64),
    }).dropna(subset=["trade_date"])
    if verbose:
        print(f"[TDX] {out['ts_code'].nunique()} 只 / {len(out)} 行，"
              f"最新 {out['trade_date'].max():%Y-%m-%d}", flush=True)
    return out


def load_name_master(hq_cache: Path) -> dict[str, str]:
    """本地证券名称表。只用于标记 ST，不用于推断涨跌幅限制。"""
    result: dict[str, str] = {}
    for market, filename in (("SH", "shs.tnf"), ("SZ", "szs.tnf")):
        path = hq_cache / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        if len(raw) < 50 or (len(raw) - 50) % 360:
            continue
        for offset in range(50, len(raw), 360):
            record = raw[offset:offset + 360]
            code = record[:6].decode("ascii", errors="ignore")
            name = record[31:63].split(b"\0", 1)[0].decode("gbk", errors="ignore").strip()
            if code.isdigit() and name:
                result[f"{code}.{market}"] = name
    return result


def mark_limits(daily: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """标记逐股逐日的涨跌停价与触板/封板/炸板状态。"""
    work = daily.sort_values(["ts_code", "trade_date"]).copy()
    work["name"] = work["ts_code"].map(names).fillna("")
    work["prev_close"] = work.groupby("ts_code", sort=False)["close"].shift(1)
    work = work.dropna(subset=["prev_close"]).copy()

    compact = work["name"].str.replace(" ", "", regex=False).str.upper()
    work["is_risk"] = compact.str.startswith(("ST", "*ST")) | compact.str.contains("退")
    wide = work["ts_code"].str[:3].isin(WIDE_BOARD)

    # 涨跌幅限制不能只靠名称推断：本地名称表滞后于撤销 ST 公告，
    # 会把已恢复 10% 的股票按 5% 判定而虚增涨停（实测曾多出 4 只）。
    # 改为实测确认：仅当近 60 日最大绝对涨跌幅未超过 6% 才认定 5% 限制。
    work["ret1"] = work["close"] / work["prev_close"] - 1.0
    trailing = (work.groupby("ts_code", sort=False)["ret1"]
                .transform(lambda s: s.abs().rolling(60, min_periods=10).max().shift(1)))
    narrow = (work["is_risk"] & (trailing <= 0.06)).fillna(False)
    work["limit_pct"] = np.where(wide, 0.20, np.where(narrow, 0.05, 0.10))
    work["limit_regime"] = np.where(wide, "20cm", np.where(narrow, "5cm", "10cm"))

    work["limit_up_price"] = np.round(work["prev_close"] * (1 + work["limit_pct"]) + 1e-9, 2)
    work["limit_dn_price"] = np.round(work["prev_close"] * (1 - work["limit_pct"]) + 1e-9, 2)

    tol = 0.004
    traded = work["vol"] > 0
    work["is_limit_up"] = (work["close"] >= work["limit_up_price"] - tol) & traded
    work["is_limit_dn"] = (work["close"] <= work["limit_dn_price"] + tol) & traded
    work["touched_up"] = (work["high"] >= work["limit_up_price"] - tol) & traded
    work["broken"] = work["touched_up"] & ~work["is_limit_up"]
    work["one_word"] = work["is_limit_up"] & (work["low"] >= work["limit_up_price"] - tol)
    # 未复权造成的除权跳空：跌幅超过限制 1.5 倍，剔除以免制造假涨跌停
    work["suspect_adjust"] = work["ret1"] < -(work["limit_pct"] * 1.5)

    up = work["is_limit_up"]
    grp = work["ts_code"]
    csum = up.groupby(grp, sort=False).cumsum()
    base = csum.where(~up).groupby(grp, sort=False).ffill().fillna(0)
    work["board_height"] = (csum - base).astype(int).where(up, 0)
    return work


def daily_ecology(work: pd.DataFrame) -> pd.DataFrame:
    """逐日汇总。晋级率按昨日候选集合逐股对齐，同时保留分子分母原始计数。"""
    rows: list[dict] = []
    prev: dict[str, set[str]] = {"N1": set(), "N2": set(), "NH": set()}
    prev_up: set[str] = set()

    for date, day in work.sort_values(["trade_date", "ts_code"]).groupby("trade_date", sort=True):
        clean = day[~day["suspect_adjust"]]
        # 接力口径剔除 ST 与一字板；广度统计保留全部涨停
        relay = clean[~clean["is_risk"] & ~clean["one_word"]]
        relay_codes = set(relay["ts_code"])

        up_all = set(clean.loc[clean["is_limit_up"], "ts_code"])
        dn_all = set(clean.loc[clean["is_limit_dn"], "ts_code"])
        touched = set(clean.loc[clean["touched_up"], "ts_code"])
        broken = set(clean.loc[clean["broken"], "ts_code"])

        h = relay.set_index("ts_code")["board_height"]
        cur = {"N1": set(h[h == 1].index), "N2": set(h[h == 2].index),
               "N3": set(h[h == 3].index), "NH": set(h[h >= 4].index)}

        def counts(prev_set: set[str], target: set[str]) -> tuple[int, int]:
            avail = prev_set & relay_codes
            return len(avail & target), len(avail)

        j1_s, j1_n = counts(prev["N1"], cur["N2"])
        j2_s, j2_n = counts(prev["N2"], cur["N3"])
        jh_s, jh_n = counts(prev["NH"], set(h[h >= 5].index))

        ylr_codes = prev_up & set(clean["ts_code"])
        if ylr_codes:
            sub = clean[clean["ts_code"].isin(ylr_codes)]
            ylr_close = float(sub["ret1"].mean() * 100)
            ylr_median = float(sub["ret1"].median() * 100)
            ylr_open = float((sub["open"] / sub["prev_close"] - 1).mean() * 100)
        else:
            ylr_close = ylr_median = ylr_open = np.nan

        rows.append({
            "trade_date": date,
            "stocks": len(clean),
            "limit_up_n": len(up_all), "limit_down_n": len(dn_all),
            "touched_up_n": len(touched), "broken_n": len(broken),
            "one_word_n": int(clean["one_word"].sum()),
            "suspect_adjust_n": int(day["suspect_adjust"].sum()),
            "N1": len(cur["N1"]), "N2": len(cur["N2"]),
            "N3": len(cur["N3"]), "NH": len(cur["NH"]),
            "max_board": int(relay["board_height"].max()) if len(relay) else 0,
            "J1_succ": j1_s, "J1_cand": j1_n,
            "J2_succ": j2_s, "J2_cand": j2_n,
            "JH_succ": jh_s, "JH_cand": jh_n,
            "J1_raw": j1_s / j1_n if j1_n else np.nan,
            "J2_raw": j2_s / j2_n if j2_n else np.nan,
            "JH_raw": jh_s / jh_n if jh_n else np.nan,
            "FR": len(broken) / len(touched) if touched else np.nan,
            "YLR_close": ylr_close, "YLR_open": ylr_open, "YLR_median": ylr_median,
            "B": len(up_all) / (len(up_all) + len(dn_all)) if (up_all or dn_all) else np.nan,
            "amount_yi": float(clean["amount"].sum() / 1e8),
        })
        prev = {"N1": cur["N1"], "N2": cur["N2"], "NH": cur["NH"]}
        prev_up = up_all
    return pd.DataFrame(rows)


def pct60(series: pd.Series, window: int = 60, min_obs: int = 40) -> pd.Series:
    """滚动分位，中位秩处理并列值，避免大量相同板数被压到 0 分。"""
    def rank(values: np.ndarray) -> float:
        cur, hist = values[-1], values[:-1]
        hist = hist[~np.isnan(hist)]
        if np.isnan(cur) or len(hist) < min_obs:
            return np.nan
        return 100.0 * (np.sum(hist < cur) + 0.5 * np.sum(hist == cur)) / len(hist)
    return series.rolling(window + 1, min_periods=min_obs + 1).apply(rank, raw=True)


def shrink_rate(succ: pd.Series, cand: pd.Series, alpha: float = SHRINK_ALPHA,
                pool: int = POOL_DAYS) -> pd.Series:
    """晋级率的汇总 + Beta 收缩。

    原始晋级率分母常只有 1–5 只，取值极度离散（0/0.2/0.25/0.33/0.5/1.0），
    把最大权重压在抽样误差最大的指标上不合理。这里先按 pool 日汇总
    分子分母（不是对日比率求均值），再向 60 日整体晋级率收缩。
    """
    s = succ.rolling(pool, min_periods=1).sum()
    n = cand.rolling(pool, min_periods=1).sum()
    prior_s = succ.rolling(60, min_periods=10).sum()
    prior_n = cand.rolling(60, min_periods=10).sum()
    p0 = (prior_s / prior_n.replace(0, np.nan)).fillna(0.15)
    return (s + alpha * p0) / (n + alpha)


def add_st_cycle(eco: pd.DataFrame) -> pd.DataFrame:
    df = eco.copy()
    df["J1_shrunk"] = shrink_rate(df["J1_succ"], df["J1_cand"])
    df["J2_shrunk"] = shrink_rate(df["J2_succ"], df["J2_cand"])

    scores = pd.DataFrame(index=df.index)
    scores["J2_shrunk"] = pct60(df["J2_shrunk"])
    scores["J1_shrunk"] = pct60(df["J1_shrunk"])
    scores["YLR_close"] = pct60(df["YLR_close"])
    scores["FR_inv"] = 100.0 - pct60(df["FR"])
    scores["AMT"] = pct60(df["amount_yi"])
    for name in WEIGHTS_V1:
        df[f"pct_{name}"] = scores[name]

    weights = pd.Series(WEIGHTS_V1)
    avail = scores[list(WEIGHTS_V1)].notna()
    # 缺失时只对可用项重新归一化权重，不用中性值 50 静默填补
    wsum = avail.mul(weights, axis=1).sum(axis=1)
    df["coverage"] = wsum
    df["ST_raw"] = (scores[list(WEIGHTS_V1)].fillna(0).mul(weights, axis=1).sum(axis=1)
                    / wsum.replace(0, np.nan))

    # 分位数的加权平均会向中间压缩并整体抬升：直接用 raw 合成分配阈值 60，
    # 实测会让“主升”占到 49% 的交易日，失去风险区分力。
    # 对合成分再做一次滚动分位，使 20/60/85 的阈值真正对应分布位置。
    df["ST_cycle"] = pct60(df["ST_raw"], window=250, min_obs=120)
    df["delta_ST"] = df["ST_cycle"].diff()
    df["ST_ma3"] = df["ST_cycle"].rolling(3, min_periods=3).mean()
    df["slope3"] = df["ST_ma3"].diff()
    return df


# 常规档位阶梯。高潮/退潮是条件触发的降险态，不参与升档排序。
RUNG = {"冰点": 0, "修复": 1, "主升": 2}
RUNG_STATE = {0: "冰点", 1: "修复", 2: "主升"}


def classify(df: pd.DataFrame) -> pd.DataFrame:
    """五状态判定 + 硬覆盖 + 防抖 + 降仓速率。

    与 v0 的关键差异：v0 在“主升条件未确认”时无条件保持前态，没有任何上下界。
    实测中 ST_cycle 一天从 18 跳到 80，状态仍被前一日的冰点锁住、上限停在 10%。
    v1 改为先由分数确定基础档位，再受“每日最多升一档 + 升档需连续 2 日确认”约束，
    降档与硬覆盖允许直接到位。
    """
    out = df.copy()
    n = len(out)
    fr_p70 = out["FR"].rolling(60, min_periods=40).quantile(0.70)
    fr_p95 = out["limit_up_n"].rolling(60, min_periods=40).quantile(0.95)
    j2_p30 = out["J2_shrunk"].rolling(60, min_periods=40).quantile(0.30)

    states, caps, overrides, notes, bands = [], [], [], [], []
    prev_rung, prev_cap = 1, STATE_CAPS["修复"]
    upgrade_streak = 0

    for i in range(n):
        row = out.iloc[i]
        st, slope, cov = row["ST_cycle"], row["slope3"], row["coverage"]
        ylr, fr, j2 = row["YLR_close"], row["FR"], row["J2_shrunk"]
        ylr_prev = out["YLR_close"].iloc[i - 1] if i else np.nan
        ylr_neg2 = bool(pd.notna(ylr) and pd.notna(ylr_prev) and ylr < 0 and ylr_prev < 0)
        warm = bool(pd.notna(st)) and cov >= 0.80

        if pd.isna(st):
            states.append("预热"); bands.append("预热")
            caps.append(min(prev_cap, STATE_CAPS["修复"]))
            overrides.append(""); notes.append("预热期，禁止升档")
            continue

        # 基础档位只由分数决定，保证状态与 ST_cycle 不再脱节
        if st >= 60:
            band_rung = 2
        elif st < 20 and (pd.isna(slope) or slope <= 0):
            band_rung = 0
        else:
            band_rung = 1
        bands.append(RUNG_STATE[band_rung])

        # 高潮信号：至少满足 2 项
        climax_signals = 0
        if st >= 85 and (pd.isna(row["delta_ST"]) or row["delta_ST"] <= 0):
            climax_signals += 1
        if i >= 2 and pd.notna(fr):
            f1, f2 = out["FR"].iloc[i - 1], out["FR"].iloc[i - 2]
            if pd.notna(f1) and pd.notna(f2) and fr > f1 > f2:
                climax_signals += 1
        if pd.notna(fr_p95.iloc[i]) and row["limit_up_n"] >= fr_p95.iloc[i] \
                and i >= 1 and pd.notna(j2) and j2 <= out["J2_shrunk"].iloc[i - 1]:
            climax_signals += 1

        ebb = (ylr_neg2 and pd.notna(fr) and pd.notna(fr_p70.iloc[i]) and fr >= fr_p70.iloc[i]
               and pd.notna(j2_p30.iloc[i]) and j2 <= j2_p30.iloc[i])

        hard = ""
        if ebb:
            state, rung = "退潮", 0
            hard = "退潮条件成立"
        elif climax_signals >= 2 or (st >= 85 and (pd.isna(row["delta_ST"]) or row["delta_ST"] <= 0)):
            state, rung = "高潮", min(prev_rung, 1)
            hard = f"高潮信号{climax_signals}项"
        elif band_rung > prev_rung:
            # 升档：需连续 2 日确认，且每日最多升一档，覆盖不足不允许升
            upgrade_streak += 1
            if warm and upgrade_streak >= 2:
                rung = prev_rung + 1
                upgrade_streak = 0
            else:
                rung = prev_rung
            state = RUNG_STATE[rung]
        else:
            upgrade_streak = 0
            rung = band_rung
            state = RUNG_STATE[rung]

        cap = STATE_CAPS[state]
        note = ""
        if band_rung > prev_rung and state == RUNG_STATE[prev_rung]:
            note = "升档待连续2日确认"
        if not warm and cap > prev_cap:
            cap, note = prev_cap, "数据覆盖不足，禁止升档"
        # YLR 硬覆盖：不能被高 ST_cycle 或宏观强势覆盖
        if ylr_neg2:
            cap = min(cap, 30.0)
            hard = hard or "YLR连续2日为负"
        # 降仓速率：T+1 且有跌停的市场里，一天要求减掉超过 25 个百分点不可执行；
        # 余量顺延次日并标记，避免输出无法成交的指令
        if prev_cap - cap > MAX_DAILY_REDUCTION_PCT:
            target = cap
            cap = prev_cap - MAX_DAILY_REDUCTION_PCT
            note = (note + "；" if note else "") + f"降仓速率限制，目标{target:.0f}%顺延"

        states.append(state); caps.append(cap)
        overrides.append(hard); notes.append(note)
        prev_rung = RUNG.get(state, prev_rung)
        prev_cap = cap

    out["band_by_score"] = bands
    out["cycle_state"] = states
    out["cycle_position_cap"] = caps
    out["hard_override"] = overrides
    out["cap_note"] = notes
    return out


def collinearity(df: pd.DataFrame) -> pd.DataFrame:
    cols = [f"pct_{k}" for k in WEIGHTS_V1] + ["B"]
    sub = df[cols].dropna()
    if len(sub) < 20:
        return pd.DataFrame()
    corr = sub.corr(method="spearman")
    corr.index = [c.replace("pct_", "") for c in corr.index]
    corr.columns = [c.replace("pct_", "") for c in corr.columns]
    return corr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-dir", default=r"C:\new_tdx\vipdoc")
    ap.add_argument("--hq-cache", default=str(DEFAULT_HQ))
    ap.add_argument("--history", type=int, default=400, help="重建的交易日数")
    ap.add_argument("--min-bars", type=int, default=70)
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--collinearity", action="store_true", help="打印因子相关矩阵")
    args = ap.parse_args()

    names = load_name_master(Path(args.hq_cache))
    print(f"[名称表] {len(names)} 条（仅用于标记 ST）")
    daily = load_tail(Path(args.tdx_dir), n_bars=args.history + 70, min_bars=args.min_bars)

    work = mark_limits(daily, names)
    eco = daily_ecology(work)
    eco = classify(add_st_cycle(eco)).tail(args.history).reset_index(drop=True)

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "limit_ecology.csv"
    eco.to_csv(csv_path, index=False, encoding="utf-8-sig")

    view_cols = ["trade_date", "limit_up_n", "limit_down_n", "broken_n", "FR",
                 "N1", "N2", "N3", "max_board", "J2_shrunk", "YLR_close",
                 "amount_yi", "ST_cycle", "slope3", "coverage",
                 "cycle_state", "cycle_position_cap"]
    view = eco[view_cols].tail(12).copy()
    view["trade_date"] = view["trade_date"].dt.strftime("%m-%d")
    print("\n=== 最近 12 个交易日 ===")
    print(view.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    if args.collinearity:
        corr = collinearity(eco)
        if not corr.empty:
            print("\n=== 因子 Spearman 相关矩阵（决定是否砍因子）===")
            print(corr.to_string(float_format=lambda v: f"{v:+.2f}"))

    print("\n=== 有效性诊断 ===")
    for col in ("J1_shrunk", "J2_shrunk", "JH_raw", "FR", "YLR_close", "ST_cycle"):
        print(f"  {col:12s} 有效 {eco[col].notna().sum():3d}/{len(eco)}"
              f"  缺失 {eco[col].isna().mean() * 100:5.1f}%")
    print(f"  J2 分母为 0 的交易日占比: {(eco['J2_cand'] == 0).mean() * 100:.1f}%")
    print(f"  状态分布: {eco['cycle_state'].value_counts().to_dict()}")

    latest = eco.iloc[-1]
    snapshot = {
        "as_of_trade_date": latest["trade_date"].strftime("%Y-%m-%d"),
        "source": "本地通达信日线重建（沪深A股，不含北交所）",
        "metric_version": "ST_cycle_v1",
        "validated_against": {"20260814": {"reconstructed": 63, "eastmoney": 63},
                              "20260813": {"reconstructed": 62, "eastmoney": 59,
                                           "diff_reason": "东财涨停池排除ST，本口径保留于广度统计"}},
        "ST_cycle": None if pd.isna(latest["ST_cycle"]) else round(float(latest["ST_cycle"]), 2),
        "ST_raw": None if pd.isna(latest["ST_raw"]) else round(float(latest["ST_raw"]), 2),
        "delta_ST": None if pd.isna(latest["delta_ST"]) else round(float(latest["delta_ST"]), 2),
        "slope3": None if pd.isna(latest["slope3"]) else round(float(latest["slope3"]), 2),
        "coverage": round(float(latest["coverage"]), 3),
        "weights": WEIGHTS_V1,
        "band_by_score": latest["band_by_score"],
        "cycle_state": latest["cycle_state"],
        "cycle_position_cap_pct": float(latest["cycle_position_cap"]),
        "hard_override": latest["hard_override"] or None,
        "cap_note": latest["cap_note"] or None,
        "raw": {k: (None if pd.isna(latest[k]) else float(latest[k])) for k in
                ("limit_up_n", "limit_down_n", "broken_n", "FR", "N1", "N2", "N3", "NH",
                 "max_board", "J1_shrunk", "J2_shrunk", "YLR_close", "YLR_open",
                 "amount_yi", "B")},
        "st_cycle_formal": False,
        "formal_blocker": "ST_cycle_v1 阈值尚未完成走样本外回测，仅可用于风险上限参考",
    }
    json_path = outdir / "limit_ecology_latest.json"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出: {csv_path} 与 {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
