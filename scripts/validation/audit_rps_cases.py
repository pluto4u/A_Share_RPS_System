"""Independent cache arithmetic and rolling-return bases for six case records."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.dual_system import sha256

ROOT = PROJECT_ROOT
OUT = ROOT / "output/case_rps_review_202606_202609_v1"
CACHE = ROOT / "output/trend_start_cache_20260930"


def main() -> None:
    registration = json.loads((OUT / "registered.json").read_text(encoding="utf-8"))
    daily = pd.read_csv(OUT / "daily.csv", na_values=["缺"], float_precision="round_trip")
    monthly = pd.read_csv(OUT / "month_end.csv", float_precision="round_trip")
    latest = pd.read_csv(OUT / "latest.csv", float_precision="round_trip")
    checks: list[dict[str, Any]] = []

    def check(name: str, result: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(result), "detail": detail})

    for name, fingerprint in registration["hashes"].items():
        check(f"hash:{name}", sha256(ROOT / name) == fingerprint)
    check("daily_unique", not daily.duplicated(["code", "date"]).any())
    check("six_codes", daily.code.nunique() == 6)
    check("window_bounds", daily.date.between("2026-06-01", "2026-09-30").all())
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        dates, codes = z["dates"], z["codes"]
        end = int(np.searchsorted(dates, np.datetime64("2026-09-30"), side="right"))
        dates, close = dates[:end].copy(), z["close"][:, :end].copy()
        volume = z["vol"][:, :end].copy()
    with np.load(CACHE / "rps.npz", allow_pickle=False) as z:
        ranks = {p: z[f"RPS{p}"][:, :end] for p in (50, 120, 250)}
    locations = {str(code): row for row, code in enumerate(codes)}
    basis, missing_sessions = [], []
    for code, group in daily.groupby("code", sort=False):
        row = locations[code]
        cal = np.flatnonzero(np.isfinite(close[row]))
        prices = close[row, cal].astype(float)
        review_cal = np.flatnonzero(
            (dates >= np.datetime64("2026-06-01")) & (dates <= np.datetime64("2026-09-30"))
        )
        for missing in review_cal[~np.isfinite(close[row, review_cal])]:
            missing_sessions.append(
                {
                    "code": code,
                    "date": str(dates[missing]),
                    "reason": "cache_has_no_traded_bar_cause_not_inferred",
                    "filled": False,
                }
            )
        for record in group.itertuples(index=False):
            t = int(np.searchsorted(dates, np.datetime64(record.date)))
            bar = int(np.searchsorted(cal, t))
            check(
                f"daily:{code}:{record.date}",
                close[row, t] == record.close
                and volume[row, t] == record.vol
                and np.isclose(
                    prices[bar - 19 : bar + 1].sum() / 20, record.MA20, atol=1e-10, rtol=0
                )
                and all(ranks[p][row, t] == getattr(record, f"RPS{p}") for p in ranks),
            )
        for point in monthly.loc[monthly.code == code].itertuples(index=False):
            t = int(np.searchsorted(dates, np.datetime64(point.date)))
            bar = int(np.searchsorted(cal, t))
            for period in ranks:
                ref = bar - period
                if ref < 0:
                    raise ValueError("insufficient return basis")
                basis.append(
                    {
                        "code": code,
                        "name": point.name,
                        "date": point.date,
                        "period": period,
                        "close": float(prices[bar]),
                        "comparison_date": str(dates[cal[ref]]),
                        "comparison_close": float(prices[ref]),
                        "interval_return_pct": float((prices[bar] / prices[ref] - 1) * 100),
                        "RPS": float(ranks[period][row, t]),
                    }
                )
        snapshot = latest.loc[latest.code == code].iloc[0]
        last = group.iloc[-1]
        check(
            f"latest:{code}",
            all(snapshot[k] == last[k] for k in ("close", "MA20", "RPS50", "RPS120", "RPS250")),
        )
    pd.DataFrame(basis).to_csv(OUT / "return_basis.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(missing_sessions, columns=["code", "date", "reason", "filled"]).to_csv(
        OUT / "missing_sessions.csv", index=False, encoding="utf-8-sig"
    )
    episodes = pd.read_csv(
        OUT / "ma20_episodes.csv", na_values=["缺"], float_precision="round_trip"
    )
    for episode in episodes.itertuples(index=False):
        row = locations[episode.code]
        cal = np.flatnonzero(np.isfinite(close[row]))
        prices = close[row, cal].astype(float)
        ma20 = pd.Series(prices).rolling(20).mean().to_numpy()
        traded_dates = dates[cal].astype(str)
        start = int(np.flatnonzero(traded_dates == episode.break_date)[0])
        stop = int(np.flatnonzero(traded_dates == episode.last_below_date)[0]) + 1
        floor = start + int(prices[start:stop].argmin())
        actual = (prices[floor] / prices[start - 20 : start].max() - 1) * 100
        check(
            f"episode:{episode.code}:{episode.break_date}",
            (prices[start:stop] < ma20[start:stop]).all()
            and traded_dates[floor] == episode.review_trough_date
            and np.isclose(actual, episode.review_peak_to_trough_pct, atol=1e-12, rtol=0)
            and (stop == len(prices) or prices[stop] >= ma20[stop]),
        )
    rank_rows = []
    t = len(dates) - 1
    for period in ranks:
        returns = np.full(len(codes), np.nan, dtype=np.float32)
        for row in range(len(codes)):
            cal = np.flatnonzero(np.isfinite(close[row]))
            if len(cal) >= 251 and cal[-1] == t:
                prices = close[row, cal].astype(float)
                returns[row] = prices[-1] / prices[-1 - period] - 1
        n = int(np.isfinite(returns).sum())
        expected = (pd.Series(returns).rank(method="average") / n * 100).to_numpy(np.float32)
        actual = ranks[period][:, t]
        error = float(np.nanmax(np.abs(actual.astype(float) - expected)))
        matched = np.array_equal(np.isnan(actual), np.isnan(expected)) and error == 0
        check(f"whole_market_RPS{period}:2026-09-30", matched, f"N={n}, max_error={error}")
        rank_rows.append(
            {"date": "2026-09-30", "period": period, "effective_stocks": n, "max_abs_error": error}
        )
    pd.DataFrame(rank_rows).to_csv(OUT / "rank_checks.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(checks).to_csv(OUT / "independent_checks.csv", index=False, encoding="utf-8-sig")
    if not all(row["passed"] for row in checks):
        raise AssertionError("case audit failed; see independent_checks.csv")
    print(f"{len(checks)} independent checks passed", flush=True)
    print(
        pd.DataFrame(basis)
        .loc[lambda d: (d.period == 50) & (d.date == "2026-08-31")]
        .to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
