"""Inspect retained adjusted cache only; no promotion into the signal input."""
import json

import numpy as np
import pandas as pd

from check_alternatives import CACHE, OUT
from diagnostic import sha


def main():
    if (OUT / "retained_cache_quality.json").exists():
        raise FileExistsError("Cache inventory already exists")
    target = pd.read_csv(OUT / "study_window_terminated_coverage.csv",parse_dates=["terminal_date"])
    with np.load(CACHE / "panel.npz",allow_pickle=False) as z:
        codes = list(map(str,z["codes"]))
        dates = pd.DatetimeIndex(z["dates"])
        lookup = {code:i for i,code in enumerate(codes)}
        selected = [lookup[c] for c in target.ts_code if c in lookup]
        arrays = {f:z[f][selected] for f in ("open","high","low","close","vol","amount")}
    cache_codes = [c for c in target.ts_code if c in lookup]
    terminal = target.set_index("ts_code").terminal_date
    records = []
    for i,code in enumerate(cache_codes):
        row = {f:a[i] for f,a in arrays.items()}
        present = np.isfinite(row["close"])
        positive = np.logical_and.reduce([np.isfinite(v)&(v>0) for v in row.values()])
        impossible = present & ((row["high"]<np.maximum(row["open"],row["close"])) |
                                (row["low"]>np.minimum(row["open"],row["close"])))
        historical = present & (dates >= "2018-01-01") & (dates <= "2026-09-30")
        traded_after_terminal = positive & (dates > terminal[code])
        records.append(dict(ts_code=code,present_bars=int(present.sum()),
            study_window_bars=int(historical.sum()),invalid_present_bars=int((present & ~positive).sum()),
            impossible_ohlc=int(impossible.sum()),post_terminal_positive_bars=int(traded_after_terminal.sum()),
            first_date=str(dates[present].min().date()),last_date=str(dates[present].max().date()),
            terminal_date=str(terminal[code].date())))
    frame = pd.DataFrame(records)
    frame.to_csv(OUT / "retained_cache_code_quality.csv",index=False)
    report = dict(cache_sha256=sha(CACHE / "panel.npz"),cache_codes=len(frame),
        codes_with_study_window_bars=int((frame.study_window_bars>0).sum()),
        study_window_bars=int(frame.study_window_bars.sum()),invalid_present_bars=int(frame.invalid_present_bars.sum()),
        impossible_ohlc=int(frame.impossible_ohlc.sum()),post_terminal_positive_bars=int(frame.post_terminal_positive_bars.sum()),
        codes_with_post_terminal_bars=int((frame.post_terminal_positive_bars>0).sum()),
        promoted_into_rps_input=False,
        acceptance_gaps=["Adjusted cache cannot substitute for raw executable prices or a security master.",
            "Original TDX directory in the retained receipt is unavailable at the checked path.",
            "Full exchange/publish-time corporate-action reconciliation is not complete.",
            "Overlap price-basis reconciliation and historic trading limits/ST status remain required.",
            "111 of the 239 official terminal codes are still absent from this cache."])
    (OUT / "retained_cache_quality.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
