"""Read-only inventory of retained TDX history for officially terminated codes."""
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd

from diagnostic import sha

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/rps_diag_20261006/exchange_evidence"
CACHE = Path("F:/88888.投资/08.选股/07.investment_research/right_side_strategy_bundle_20260924_221054/right_side_strategy/output/trend_start_cache_20260930")


def main():
    if (OUT / "alternative_coverage.json").exists():
        raise FileExistsError("Alternative coverage already checked")
    rows = pd.read_csv(OUT / "study_window_terminated_coverage.csv",dtype={"ticker":str})
    audit = json.loads((CACHE / "panel_audit.json").read_text(encoding="utf-8"))
    tdx_root = Path(audit["tdx_root"])
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        codes = set(map(str,z["codes"]))
    rejected = {v["code"]:v["reason"] for v in audit["excluded_detail"]}
    records = []
    for r in rows.itertuples():
        exchange = r.exchange.lower()
        path = tdx_root / "vipdoc" / exchange / "lday" / f"{exchange}{r.ticker}.day"
        result = dict(ts_code=r.ts_code,in_retained_adjusted_cache=r.ts_code in codes,
            rejected_adjustment_reason=rejected.get(r.ts_code),raw_path=str(path),raw_file_exists=path.is_file())
        if path.is_file():
            blob = path.read_bytes()
            if len(blob) % 32:
                raise ValueError(f"Malformed daily record length: {path}")
            dates = [struct.unpack_from("<I",blob,i)[0] for i in range(0,len(blob),32)]
            if dates:
                parsed = pd.to_datetime(pd.Series(dates).astype(str), format="%Y%m%d",errors="raise")
                result.update(raw_rows=len(dates),first_date=str(parsed.min().date()),last_date=str(parsed.max().date()),
                    study_window_rows=int(parsed.between("2018-01-01","2026-09-30").sum()),sha256=sha(path))
        records.append(result)
    frame = pd.DataFrame(records)
    frame.to_csv(OUT / "alternative_tdx_coverage.csv",index=False)
    report = dict(retained_cache=str(CACHE),cache_sha256=sha(CACHE / "panel.npz"),
        tdx_root=str(tdx_root),official_study_window_codes=len(frame),
        in_retained_adjusted_cache=int(frame.in_retained_adjusted_cache.sum()),
        rejected_adjustment_codes=int(frame.rejected_adjustment_reason.notna().sum()),
        raw_file_exists=int(frame.raw_file_exists.sum()),
        raw_with_study_window_rows=int((frame.get("study_window_rows",pd.Series(dtype=float)).fillna(0)>0).sum()),
        usable_for_study=False,caveats=["Inventory only: raw file presence is not validated corporate-action or security-status coverage.",
            "Do not concatenate adjusted TDX history with hithink qfq prices without basis/lineage reconciliation.",
            "Missing file does not prove no external data source exists."])
    (OUT / "alternative_coverage.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
