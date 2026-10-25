"""Normalize observed official snapshots and check code-level history coverage."""
import json
from pathlib import Path

import pandas as pd

from collect_local import query

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/rps_diag_20261006/exchange_evidence"


def main():
    if (OUT / "coverage.json").exists():
        raise FileExistsError("coverage evidence already exists")
    j = json.loads((OUT / "sse_page1.json").read_text(encoding="utf-8"))
    assert not j["actionErrors"] and j["pageHelp"]["pageCount"] == 1
    assert len(j["result"]) == j["pageHelp"]["total"]
    sse = pd.DataFrame(j["result"])
    # A+B companies occur twice; B rows carry a different listing date even
    # though A_STOCK_CODE is populated. Do not silently deduplicate them.
    sse = sse.loc[sse.STOCK_TYPE.isin(["1", "8"]) &
                  sse.A_STOCK_CODE.str.match(r"^(600|601|603|605|688|689)\d{3}$")].copy()
    sse = sse.rename(columns={"A_STOCK_CODE":"ticker", "COMPANY_ABBR":"name",
        "LIST_DATE":"listing_date", "DELIST_DATE":"terminal_date"})
    sse["exchange"] = "SH"
    sz = pd.read_excel(OUT / "szse_terminated.xlsx", dtype=str)
    sz = sz.rename(columns={"证券代码":"ticker", "证券简称":"name", "上市日期":"listing_date",
        "终止上市日期":"terminal_date"})
    sz = sz.loc[sz.ticker.str.match(r"^(000|001|002|003|300|301)\d{3}$")].copy()
    sz["exchange"] = "SZ"
    columns = ["ticker", "name", "exchange", "listing_date", "terminal_date"]
    for f in (sse, sz):
        # Separate formats, no silent coercion of malformed exchange dates.
        fmt = "%Y%m%d" if f.exchange.iloc[0] == "SH" else "%Y-%m-%d"
        for field in ("listing_date", "terminal_date"):
            f[field] = pd.to_datetime(f[field], format=fmt, errors="raise")
    all_a = pd.concat([sse[columns], sz[columns]], ignore_index=True)
    all_a["ts_code"] = all_a.ticker + "." + all_a.exchange
    assert not all_a.duplicated(["ts_code", "terminal_date"]).any()
    all_a.to_csv(OUT / "official_terminated_a_snapshot.csv", index=False)
    target = all_a.loc[all_a.terminal_date.between("2018-01-01", "2026-09-30")].copy()
    observed = set(pd.read_parquet(ROOT / "output/tdx_stock_daily_with_rps.parquet", columns=["ts_code"]).ts_code.unique())
    target["in_frozen_panel"] = target.ts_code.isin(observed)
    codes = sorted(target.ts_code.unique())
    records = []
    for part, offset in enumerate(range(0, len(codes), 80)):
        batch = codes[offset:offset+80]
        values = ",".join(f"('{code}')" for code in batch)
        sql = f"""WITH requested(thscode) AS (VALUES {values})
        SELECT r.thscode,COUNT(k.date) AS observations,MIN(k.date) AS first_date,MAX(k.date) AS last_date
        FROM requested r LEFT JOIN raw_kline_daily k ON r.thscode=k.thscode
        GROUP BY r.thscode ORDER BY r.thscode LIMIT 80"""
        result = query(f"official_code_coverage_{part+1}", sql)
        assert result["meta"].get("truncated") is False and len(result["data"]) == len(batch)
        records.extend(result["data"])
    raw = pd.DataFrame(records).rename(columns={"thscode":"ts_code"})
    raw["observations"] = pd.to_numeric(raw.observations)
    checked = target.merge(raw, on="ts_code", how="left", validate="one_to_one")
    checked.to_csv(OUT / "study_window_terminated_coverage.csv", index=False)
    report = dict(snapshot_at_utc=json.loads((OUT / "sse_page1_receipt.json").read_text(encoding="utf-8"))["invoked_at_utc"],
        exchanges="Shanghai/Shenzhen A only", window=["2018-01-01","2026-09-30"],
        sse_all_snapshot_rows=j["pageHelp"]["total"], szse_all_snapshot_rows=len(pd.read_excel(OUT / "szse_terminated.xlsx")),
        official_a_snapshot_rows=len(all_a), window_terminal_codes=len(checked),
        by_exchange={k:dict(codes=len(g), in_frozen_panel=int(g.in_frozen_panel.sum()),
            with_any_raw_history=int((g.observations>0).sum())) for k,g in checked.groupby("exchange")},
        in_frozen_panel=int(checked.in_frozen_panel.sum()), with_any_raw_history=int((checked.observations>0).sum()),
        missing_from_frozen_panel=int((~checked.in_frozen_panel).sum()),
        caveats=["Current terminal-list snapshot, not a complete point-in-time security master.",
                 "Code-level check; relisting, merger and ticker/security-id reuse need validity intervals.",
                 "Not all terminated securities would satisfy the frozen signal eligibility.",
                 "Demonstrates a historical-universe gap, does not quantify the direction or magnitude of bias."])
    (OUT / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
