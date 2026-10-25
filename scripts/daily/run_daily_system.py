"""Daily pattern book: buy the next open, sell the next open, review day and week.

Open output/daily_system/复盘.md after each run. Signal at the close, order at
the next open. A close back through MA20 or through the base low is the sell.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

from pathlib import Path

import numpy as np
import pandas as pd

from run_daily_rps import names_from_tnf
from trend_start.daily_book import assign_slots, horizon_summary
from trend_start.engine import LIMIT_TOL, limit_pct
from trend_start.pattern import _roll_mean, phase_flags

ROOT = PROJECT_ROOT
SLOTS = 20
TDX = Path("C:/new_tdx")
OUT = ROOT / "output" / "daily_system"


def _price(value: float) -> float:
    if not np.isfinite(value):
        return np.nan
    return float(np.round(value + 1e-8, 2))


def _limit_up(code: str, close: float, day: np.datetime64) -> float:
    pct = float(limit_pct(code, np.array([day]))[0])
    return _price(close * (1 + pct))


def _latest_session() -> pd.Timestamp:
    raw = (TDX / "vipdoc/sh/lday/sh000001.day").read_bytes()[-32:]
    return pd.Timestamp(str(int.from_bytes(raw[:4], "little")))


def _cache(as_of: pd.Timestamp) -> Path:
    cache = ROOT / "output" / f"trend_start_cache_{as_of:%Y%m%d}"
    if not (cache / "panel.npz").exists():
        from trend_start.panel import build_panel
        print(f"[DAILY] build panel {as_of.date()}", flush=True)
        build_panel(TDX, as_of, cache)
    if not (cache / "rps.npz").exists():
        from run_trend_start_backtest import compute_rps
        from trend_start.panel import load_panel
        print("[DAILY] build rps", flush=True)
        compute_rps(load_panel(cache), cache)
    return cache


def _scan(cache: Path) -> tuple[pd.DataFrame, np.ndarray, int]:
    panel = np.load(cache / "panel.npz")
    rps = np.load(cache / "rps.npz")
    dates = np.asarray(panel["dates"])
    codes = np.asarray(panel["codes"]).astype(str)
    close = np.asarray(panel["close"])
    open_ = np.asarray(panel["open"])
    high = np.asarray(panel["high"])
    low = np.asarray(panel["low"])
    vol = np.asarray(panel["vol"])
    r120_all = np.asarray(rps["RPS120"])
    last = len(dates) - 1
    rows: list[dict] = []
    for i, code in enumerate(codes):
        if i % 1000 == 0:
            print(f"[DAILY] {i}/{len(codes)}", flush=True)
        traded = np.flatnonzero(~np.isnan(close[i]))
        if len(traded) < 120:
            continue
        c = close[i, traded].astype(np.float64)
        o = open_[i, traded].astype(np.float64)
        h = high[i, traded].astype(np.float64)
        lo = low[i, traded].astype(np.float64)
        v = vol[i, traded].astype(np.float64)
        flags = phase_flags(o, h, lo, c, v)
        ma20 = flags["ma20"]
        r120 = r120_all[i, traded].astype(np.float64)
        for s in np.flatnonzero(flags["pattern"]):
            rows.append(_signal(code, i, s, traded, dates, o, c, v, lo, ma20, flags, r120, last))
    panel.close()
    rps.close()
    return pd.DataFrame(rows), dates, last


def _signal(code, i, s, traded, dates, o, c, v, lo, ma20, flags, r120, last) -> dict:
    sig_cal = int(traded[s])
    day = dates[sig_cal]
    base_low = float(flags["base_low"][s])
    e = s + 1
    buyable = False
    entry_px = np.nan
    entry_cal = np.nan
    if e < len(c) and int(traded[e]) == sig_cal + 1:
        lim = float(limit_pct(code, dates[sig_cal + 1:sig_cal + 2])[0])
        buyable = o[e] / c[s] - 1 < lim - LIMIT_TOL
        entry_px = float(o[e]) if buyable else np.nan
        entry_cal = sig_cal + 1 if buyable else np.nan
    exit_cal = np.nan
    exit_px = np.nan
    reason = ""
    sell_next = False
    if buyable:
        after = np.arange(e, len(c))
        hit = after[(c[after] < ma20[after]) | (c[after] < base_low)]
        if len(hit):
            j = int(hit[0])
            bits = []
            if c[j] < ma20[j]:
                bits.append("收盘跌破MA20")
            if c[j] < base_low:
                bits.append("收盘跌破横盘低点")
            reason = "、".join(bits)
            if j + 1 < len(c):
                exit_cal = int(traded[j + 1])
                exit_px = float(o[j + 1])
            else:
                sell_next = True
    nxt = sig_cal + 1
    week = sig_cal + 5
    return {
        "code": code,
        "sig_cal": sig_cal,
        "信号日": pd.Timestamp(day).date().isoformat(),
        "信号收盘": _price(c[s]),
        "横盘低点": _price(base_low),
        "rps120": float(r120[s]) if np.isfinite(r120[s]) else 0.0,
        "量比": float(v[s] / np.nanmean(v[s - 40:s])) if s >= 40 else np.nan,
        "涨停价": _limit_up(code, c[s], dates[min(nxt, len(dates) - 1)]),
        "可买": buyable,
        "entry_cal": entry_cal,
        "买入开盘": _price(entry_px),
        "exit_cal": exit_cal,
        "卖出开盘": _price(exit_px),
        "卖出原因": reason,
        "明日卖": sell_next,
        "明日买": bool(sig_cal == last),
        "最新收盘": _price(c[-1]) if int(traded[-1]) == last else np.nan,
        "今日MA20": _price(ma20[-1]) if int(traded[-1]) == last and np.isfinite(ma20[-1]) else np.nan,
        "明日MA20失效收盘": _price(c[-19:].sum() / 19) if int(traded[-1]) == last and len(c) >= 19 else np.nan,
        "隔日收盘": _at(c, traded, nxt),
        "隔日开盘": _at(o, traded, nxt),
        "隔周收盘": _at(c, traded, week),
        "隔周日期": pd.Timestamp(dates[week]).date().isoformat() if week < len(dates) else "",
        "row": i,
    }


def _at(series_traded: np.ndarray, traded: np.ndarray, cal: int) -> float:
    pos = np.flatnonzero(traded == cal)
    if len(pos) == 0:
        return np.nan
    return _price(series_traded[int(pos[0])])


def _pct(later: pd.Series, base: pd.Series) -> pd.Series:
    with np.errstate(invalid="ignore", divide="ignore"):
        return later / base - 1


def _write(book: pd.DataFrame, names: dict[str, str], dates: np.ndarray) -> None:
    as_of = pd.Timestamp(dates[-1]).date().isoformat()
    book = book.sort_values(["sig_cal", "code"]).reset_index(drop=True)
    book["名称"] = book["code"].map(names).fillna("")
    st = book["名称"].str.contains("ST|退", regex=True) | book["名称"].eq("")
    book["隔日涨跌"] = _pct(book["隔日收盘"], book["信号收盘"])
    book["隔周涨跌"] = _pct(book["隔周收盘"], book["信号收盘"])
    book["隔日相对买入"] = _pct(book["隔日收盘"], book["买入开盘"])
    book["隔周相对买入"] = _pct(book["隔周收盘"], book["买入开盘"])
    tradable = book.loc[book["可买"] & book["entry_cal"].notna() & ~st].copy()
    tradable["entry_cal"] = tradable["entry_cal"].astype(int)
    taken = assign_slots(tradable, SLOTS)
    held_idx = taken.loc[taken["成交"] & taken["exit_cal"].isna()].index
    held = taken.loc[held_idx].copy()
    sell = held.loc[held["明日卖"]].copy()
    keep = held.loc[~held["明日卖"]].copy()
    pending = book.loc[book["明日买"] & ~st & ~book["code"].isin(set(keep["code"]))].sort_values(
        "rps120", ascending=False)
    pending = pending.head(max(SLOTS - len(keep), 0))
    OUT.mkdir(parents=True, exist_ok=True)
    review_cols = ["信号日", "code", "名称", "信号收盘", "隔日开盘", "隔日收盘", "隔日涨跌", "隔周日期", "隔周收盘", "隔周涨跌", "可买", "成交"]
    day = horizon_summary(book, "隔日涨跌")
    week = horizon_summary(book.dropna(subset=["隔周收盘"]), "隔周涨跌")
    show = book.copy()
    show["成交"] = False
    if len(taken):
        show.loc[show.index.isin(taken.index[taken["成交"].to_numpy()]), "成交"] = True
    show[review_cols].to_csv(OUT / "历史对照.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    _orders(pending, sell, keep).to_csv(OUT / "今日买卖.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    text = _markdown(as_of, pending, sell, keep, day, week, show)
    (OUT / "复盘.md").write_text(text, encoding="utf-8")
    print(f"[DAILY] {OUT / '复盘.md'}", flush=True)


def _orders(pending: pd.DataFrame, sell: pd.DataFrame, keep: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if len(pending):
        buy = pending.head(SLOTS)[["code", "名称", "信号收盘", "涨停价", "横盘低点", "明日MA20失效收盘", "量比", "rps120"]].copy()
        buy.insert(0, "动作", "下一开盘买入")
        frames.append(buy)
    if len(sell):
        out = sell[["code", "名称", "买入开盘", "最新收盘", "卖出原因", "横盘低点", "明日MA20失效收盘"]].copy()
        out.insert(0, "动作", "下一开盘卖出")
        frames.append(out)
    if len(keep):
        out = keep[["code", "名称", "买入开盘", "最新收盘", "横盘低点", "明日MA20失效收盘"]].copy()
        out.insert(0, "动作", "继续持有")
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["动作", "code", "名称"])


def _pct_text(summary: dict) -> str:
    lines = []
    for label in ("近端", "全部"):
        item = summary[label]
        if item["次数"] == 0:
            lines.append(f"- {label}：还没有可对照的记录")
            continue
        lines.append(
            f"- {label} {item['次数']} 次，上涨 {item['上涨占比'] * 100:.1f}%，平均 {item['平均涨跌'] * 100:+.2f}%"
        )
    return "\n".join(lines)


def _markdown(as_of, pending, sell, keep, day, week, show) -> str:
    def table(frame: pd.DataFrame, cols: list[str]) -> str:
        if frame.empty:
            return "无\n"
        view = frame[cols].copy()
        for col in view.columns:
            if pd.api.types.is_float_dtype(view[col]):
                view[col] = view[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
        header = "| " + " | ".join(view.columns) + " |"
        sep = "| " + " | ".join("---" for _ in view.columns) + " |"
        body = "\n".join("| " + " | ".join(map(str, row)) + " |" for row in view.to_numpy())
        return header + "\n" + sep + "\n" + body + "\n"

    recent = show.dropna(subset=["隔日收盘"]).tail(15)
    recent_week = show.dropna(subset=["隔周收盘"]).tail(15)
    buy_cols = ["code", "名称", "信号收盘", "涨停价", "横盘低点", "明日MA20失效收盘"]
    sell_cols = ["code", "名称", "买入开盘", "最新收盘", "卖出原因", "横盘低点", "明日MA20失效收盘"]
    keep_cols = ["code", "名称", "买入开盘", "最新收盘", "横盘低点", "明日MA20失效收盘"]
    day_cols = ["信号日", "code", "名称", "信号收盘", "隔日开盘", "隔日收盘", "隔日涨跌"]
    week_cols = ["信号日", "code", "名称", "信号收盘", "隔周日期", "隔周收盘", "隔周涨跌"]
    recent_day = recent[day_cols].copy()
    recent_week_view = recent_week[week_cols].copy()
    for frame in (recent_day, recent_week_view):
        for col in ("隔日涨跌", "隔周涨跌"):
            if col in frame:
                frame[col] = frame[col].map(lambda x: "" if pd.isna(x) else f"{x * 100:+.2f}%")
    return f"""# {as_of} 收盘后的执行单

规则：收盘同时满足下跌、横盘缩量、右侧放量，下一交易日开盘买入。开盘涨停不买。最多同时持有 {SLOTS} 只，同一天按 RPS120 从高到低占名额。持仓后，收盘跌破 MA20，或收盘跌破买入时的横盘低点，下一交易日开盘卖出。

## 下一开盘买入

{table(pending.head(SLOTS), buy_cols)}
涨停价是买不到的价格。明日 MA20 失效收盘是下一交易日收盘跌破则卖出的价格。

## 下一开盘卖出

{table(sell, sell_cols)}
## 继续持有

{table(keep, keep_cols)}
持有期间每天收盘只看两个价：跌破「明日MA20失效收盘」，或跌破「横盘低点」。

## 隔日对照

涨跌 = 下一交易日收盘 / 信号日收盘 - 1。近端是最近 60 次。

{_pct_text(day)}

{table(recent_day, day_cols)}
## 隔周对照

涨跌 = 第 5 个交易日收盘 / 信号日收盘 - 1。近端是最近 60 次。

{_pct_text(week)}

{table(recent_week_view, week_cols)}
完整历史在同一目录的 `历史对照.csv` 和 `今日买卖.csv`。
"""


def main() -> int:
    book, dates, _last = _scan(_cache(_latest_session()))
    names = names_from_tnf(Path("C:/new_tdx/T0002/hq_cache"))
    _write(book, names, dates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
