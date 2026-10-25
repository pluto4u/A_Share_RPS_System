"""Inventory the alternative TDX root explicitly configured in tdx_reader.py."""
import json
import struct
from pathlib import Path

import pandas as pd

from check_alternatives import OUT
from diagnostic import ROOT, sha

TDX = Path("C:/new_tdx")


def main():
    if (OUT / "configured_raw_coverage.json").exists():
        raise FileExistsError("Configured raw-root evidence already exists")
    target = pd.read_csv(OUT / "study_window_terminated_coverage.csv",dtype={"ticker":str},parse_dates=["terminal_date"])
    records = []
    for r in target.itertuples():
        market = r.exchange.lower()
        path = TDX / "vipdoc" / market / "lday" / f"{market}{r.ticker}.day"
        record = dict(ts_code=r.ts_code,path=str(path),exists=path.is_file(),rows=0,window_rows=0,
            invalid_rows=0,impossible_ohlc=0,post_terminal_positive_bars=0)
        if path.is_file():
            blob = path.read_bytes()
            assert len(blob)%32 == 0
            parsed = list(struct.iter_unpack("<5If2I",blob))
            if parsed:
                dates = pd.to_datetime([str(p[0]) for p in parsed],format="%Y%m%d",errors="raise")
                invalid = sum(not all(v>0 for v in p[1:7]) for p in parsed)
                impossible = sum(p[2]<max(p[1],p[4]) or p[3]>min(p[1],p[4]) for p in parsed)
                record.update(rows=len(parsed),window_rows=int(((dates >= "2018-01-01") & (dates <= "2026-09-30")).sum()),
                    first_date=str(dates.min().date()),last_date=str(dates.max().date()),invalid_rows=invalid,
                    impossible_ohlc=impossible,duplicate_dates=int(dates.duplicated().sum()),
                    post_terminal_positive_bars=sum(d>r.terminal_date and all(v>0 for v in p[1:7]) for d,p in zip(dates,parsed)),sha256=sha(path))
        records.append(record)
    frame = pd.DataFrame(records)
    frame.to_csv(OUT / "configured_raw_code_coverage.csv",index=False)
    gbbq = TDX / "T0002/hq_cache/gbbq"
    result = dict(configured_root=str(TDX),configuration_source="rps_analyzer/tdx_reader.py",
        configuration_sha256=sha(ROOT / "rps_analyzer/tdx_reader.py"),target_codes=len(frame),
        raw_present=int(frame.exists.sum()),codes_with_window_rows=int((frame.window_rows>0).sum()),
        window_rows=int(frame.window_rows.sum()),invalid_rows=int(frame.invalid_rows.sum()),
        impossible_ohlc=int(frame.impossible_ohlc.sum()),post_terminal_positive_bars=int(frame.post_terminal_positive_bars.sum()),
        corporate_action_file_exists=gbbq.is_file(),corporate_action_sha256=sha(gbbq) if gbbq.is_file() else None,
        promoted_into_rps_input=False,usable_for_profit_certification=False,
        caveats=["Presence and raw OHLC checks only; corporate-action contents are not reconciled here.",
            "Do not silently replace frozen results or promote raw/history without point-in-time security status."])
    (OUT / "configured_raw_coverage.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
