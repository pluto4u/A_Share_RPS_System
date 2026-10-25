"""Right-side pool plus a dry pullback to MA20. Turtle breakout is not used.

The trend gates are the existing run_daily_rps rules. The extra gates describe
the marked bar on the reference charts: the volume expansion already happened,
and the current bar has come back to a rising MA20 on lighter volume.
Thresholds below were fixed before reading the 2026-09-29 list.
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
from trend_start.panel import load_panel

ROOT = PROJECT_ROOT
RPS_MIN = 87.0
BIAS20_MAX = 0.05
PULLBACK_MIN = 0.02
TAG_ABOVE_MA = 1.02
IMPULSE_MULT = 1.5
DRY_VS_IMPULSE = 0.75
LIQ_AMOUNT = 1e8


def _screen_one(c: np.ndarray, h: np.ndarray, lo: np.ndarray, v: np.ndarray,
                amt: np.ndarray, r120: float, r250: float) -> dict[str, bool | float]:
    n = len(c)
    ma20 = c[-20:].mean()
    ma60 = c[-60:].mean()
    ma120 = c[-120:].mean()
    ma60_prev = c[-65:-5].mean()
    year = c[-250:]
    recent = c[-20:]
    dd20 = float((1 - recent / np.maximum.accumulate(recent)).max())
    delta = np.diff(c[-21:])
    down, up = delta < 0, delta > 0
    vol20 = v[-20:]
    down_mean = float(vol20[down].mean()) if down.any() else np.nan
    up_mean = float(vol20[up].mean()) if up.any() else np.nan
    bias = float(c[-1] / ma20 - 1)
    off_high = float(1 - c[-1] / recent.max())
    # Impulse is the heaviest bar in the 27 sessions before the last 3.
    impulse_vol = float(v[-30:-3].max())
    impulse_at = int(np.argmax(v[-30:-3]))
    impulse_up = bool(c[-30:-3][impulse_at] > c[-31:-4][impulse_at])
    base_vol = float(v[-50:-30].mean())
    dry = float(v[-5:].mean())
    vol20_mean = float(v[-20:].mean())
    tagged = bool(lo[-3:].min() <= ma20 * TAG_ABOVE_MA)
    gates = {
        "均线多头": bool(c[-1] > ma20 > ma60 > ma120),
        "MA60向上": bool(ma60 > ma60_prev),
        "距52周高点": bool(1 - c[-1] / year.max() <= 0.25),
        "距52周低点": bool(c[-1] / year.min() - 1 >= 0.30),
        "20日回撤": bool(dd20 <= 0.15),
        "缩量回调": bool(down_mean < up_mean),
        "RPS": bool(r120 >= RPS_MIN and r250 >= RPS_MIN),
        "成交额": bool(amt[-20:].mean() >= LIQ_AMOUNT),
        "回到MA20": bool(0 <= bias <= BIAS20_MAX and tagged and off_high >= PULLBACK_MIN),
        "此前放量阳线": bool(impulse_up and impulse_vol >= IMPULSE_MULT * base_vol),
        "回踩缩量": bool(dry <= DRY_VS_IMPULSE * impulse_vol and dry < vol20_mean),
    }
    return {
        **gates,
        "入选": all(gates.values()),
        "现价": float(c[-1]),
        "MA20": float(ma20),
        "MA60": float(ma60),
        "乖离MA20": bias,
        "距20日高点": off_high,
        "量比_5日对冲击": dry / impulse_vol if impulse_vol else np.nan,
        "RPS120": float(r120),
        "RPS250": float(r250),
        "次日MA20临界": float(c[-19:].sum() / 19),
    }


def main() -> int:
    cache = ROOT / "output" / "trend_start_cache_20260929"
    panel = load_panel(cache)
    rps = np.load(cache / "rps.npz")
    names = names_from_tnf(Path("C:/new_tdx/T0002/hq_cache"))
    last = len(panel.dates) - 1
    rows = []
    close, high, low = panel.field("close"), panel.field("high"), panel.field("low")
    vol, amount = panel.field("vol"), panel.field("amount")
    for i, code in enumerate(panel.codes):
        cols = np.flatnonzero(~np.isnan(close[i]))
        if len(cols) < 251 or int(cols[-1]) != last:
            continue
        tail = cols[-260:]
        r120 = float(rps["RPS120"][i, last])
        r250 = float(rps["RPS250"][i, last])
        if not np.isfinite(r120) or not np.isfinite(r250):
            continue
        row = _screen_one(
            close[i, tail].astype(float), high[i, tail].astype(float),
            low[i, tail].astype(float), vol[i, tail].astype(float),
            amount[i, tail].astype(float), r120, r250,
        )
        name = names.get(str(code), "")
        row["代码"] = str(code)
        row["名称"] = name
        row["ST或退"] = ("ST" in name) or ("退" in name) or (name == "")
        rows.append(row)
    df = pd.DataFrame(rows)
    out = ROOT / "output" / "pullback_screen_20260929"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "all_traded.csv", index=False, encoding="utf-8-sig")
    pool_cols = ["均线多头", "MA60向上", "距52周高点", "距52周低点", "20日回撤", "缩量回调", "RPS", "成交额"]
    live = df.loc[~df["ST或退"]]
    pool = live.loc[live[pool_cols].all(axis=1)].copy()
    picked = pool.loc[pool["入选"]].sort_values(["乖离MA20", "RPS250"], ascending=[True, False])
    pool.sort_values("乖离MA20").to_csv(out / "right_side_pool.csv", index=False, encoding="utf-8-sig")
    picked.to_csv(out / "pullback_picked.csv", index=False, encoding="utf-8-sig")
    print(f"traded {len(df)} live {len(live)} pool {len(pool)} picked {len(picked)}")
    print("pool gate fails among live non-ST:")
    for col in pool_cols:
        print(f"  {col} fail {int((~live[col]).sum())}")
    if len(pool):
        print("pool bias20 median", round(float(pool["乖离MA20"].median()), 3),
              "within 5%", int((pool["乖离MA20"] <= BIAS20_MAX).sum()))
        print(pool.loc[~pool["入选"], ["回到MA20", "此前放量阳线", "回踩缩量"]].sum().to_string())
    print(picked[["代码", "名称", "现价", "MA20", "乖离MA20", "距20日高点", "RPS120", "RPS250", "次日MA20临界"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
