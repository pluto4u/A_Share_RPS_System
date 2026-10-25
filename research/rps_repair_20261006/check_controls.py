"""Finite, governed read-only raw export for the preselected 20 active controls."""
import json
import shutil
import subprocess

import numpy as np
import pandas as pd

from common import OUT, PRICE, ROOT, comparison, guard, now, read_raw, save, sha


def main():
    protocol = guard()
    if (OUT / "control_vendor_comparison.json").exists():
        raise FileExistsError("Vendor control check already exists")
    database = ROOT / "data/hithink/market.duckdb"
    output = OUT / "hithink_raw_controls.csv"
    codes = [r["ts_code"] for r in protocol["controls"]]
    literals = ",".join(repr(code) for code in codes)
    sql = (f"SELECT thscode AS ts_code,date AS trade_date,open,high,low,close,volume AS vol,amount "
           f"FROM raw_kline_daily WHERE thscode IN ({literals}) "
           "AND date >= DATE '2018-01-01' AND date <= DATE '2026-09-30' ORDER BY thscode,date")
    (OUT / "hithink_raw_controls.sql").write_text(sql, encoding="utf-8")
    db_hash = sha(database)
    save("controls_export_plan.json", dict(registered_at_utc=now(), db_sha256=db_hash, codes=codes, sql=sql,
         mode="hithink-finance db export, read-only", tolerance_source="registered protocol, unchanged"))
    cli = shutil.which("hithink-finance.cmd")
    arguments = [cli, "--db", str(database), "db", "export", "--sql", sql,
                 "--output", str(output), "--file-format", "csv", "--format", "json"]
    invoked = now()
    result = subprocess.run(arguments, capture_output=True, timeout=55)
    # CLI errors may be JSON on stderr, not stdout. Empty output is never 'no data'.
    stdout = result.stdout.decode("utf-8-sig", errors="replace")
    stderr = result.stderr.decode("utf-8-sig", errors="replace")
    envelope = json.loads(stdout if stdout.strip() else stderr)
    save("hithink_export_receipt.json", dict(invoked_at_utc=invoked, completed_at_utc=now(),
         command_arguments=arguments[1:], exit_code=result.returncode, response=envelope,
         db_sha256_before=db_hash, db_sha256_after=sha(database)))
    if result.returncode or envelope.get("ok") is not True:
        raise RuntimeError("Governed read-only export failed; see receipt")
    hithink = pd.read_csv(output, parse_dates=["trade_date"])
    if hithink.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("Duplicate source code/date keys")
    frozen = pd.read_parquet(ROOT / "output/tdx_stock_daily_with_rps.parquet",
                            columns=["ts_code", "trade_date", *PRICE], filters=[("ts_code", "in", codes)])
    frozen.trade_date = pd.to_datetime(frozen.trade_date)
    frozen = frozen.loc[frozen.trade_date.between("2018-01-01", "2026-09-30")]
    local = pd.read_csv(OUT / "reconciliation_by_code.csv")
    records = []
    for control in protocol["controls"]:
        code = control["ts_code"]
        raw = read_raw(control["path"]).set_index("trade_date").loc["2018-01-01":"2026-09-30"]
        other = hithink[hithink.ts_code == code].set_index("trade_date")
        common = raw.index.intersection(other.index)
        record = dict(ts_code=code, raw_rows=len(raw), hithink_rows=len(other), common_rows=len(common),
            tdx_only_dates=len(raw.index.difference(other.index)), hithink_only_dates=len(other.index.difference(raw.index)))
        for label, fields, spec in [("raw_price", PRICE, protocol["price_tolerance"]),
            ("volume", ["vol"], protocol["volume_tolerance"]), ("amount", ["amount"], protocol["amount_tolerance"])]:
            record.update({f"{label}_{key}": value for key, value in comparison(raw.loc[common], other.loc[common], fields, **spec).items()})
        positive = (other.loc[common, "vol"] > 0) & (raw.loc[common, "vol"] > 0)
        ratios = raw.loc[common, "vol"][positive] / other.loc[common, "vol"][positive]
        record["median_tdx_to_hithink_volume_ratio"] = float(ratios.median()) if len(ratios) else None
        records.append(record)
    frame = pd.DataFrame(records)
    frame.to_csv(OUT / "control_vendor_comparison_by_code.csv", index=False)
    # Compare adjusted price *basis* using original cache values, without joining
    # vendors into one panel or normalizing away disagreement.
    from common import CACHE
    with np.load(CACHE, allow_pickle=False) as z:
        lookup = {str(c): i for i, c in enumerate(z["codes"])}
        dates = pd.DatetimeIndex(z["dates"])
        chosen = [lookup[c] for c in codes]
        arrays = {field: z[field][chosen] for field in PRICE}
    basis = []
    for i, code in enumerate(codes):
        cache = pd.DataFrame({f: arrays[f][i] for f in PRICE}, index=dates)
        cache = cache.loc[np.isfinite(cache.close)].loc["2018-01-01":"2026-09-30"]
        qfq = frozen[frozen.ts_code == code].set_index("trade_date")
        common = cache.index.intersection(qfq.index)
        row = dict(ts_code=code, common_rows=len(common))
        row.update(comparison(cache.loc[common], qfq.loc[common], PRICE, **protocol["price_tolerance"]))
        ratio = cache.loc[common, "close"] / qfq.loc[common, "close"]
        row.update(median_close_ratio=float(ratio.median()), min_close_ratio=float(ratio.min()), max_close_ratio=float(ratio.max()))
        basis.append(row)
    pd.DataFrame(basis).to_csv(OUT / "control_adjusted_basis.csv", index=False)
    summary = dict(completed_at_utc=now(), codes=len(frame), common_rows=int(frame.common_rows.sum()),
        raw_price_mismatched_cells=int(frame.raw_price_mismatched_cells.sum()),
        volume_mismatched_cells=int(frame.volume_mismatched_cells.sum()),
        amount_mismatched_cells=int(frame.amount_mismatched_cells.sum()),
        raw_price_agree_codes=int((frame.raw_price_mismatched_cells == 0).sum()),
        tdx_only_dates=int(frame.tdx_only_dates.sum()), hithink_only_dates=int(frame.hithink_only_dates.sum()),
        adjusted_basis_agree_codes=sum(r["mismatched_cells"] == 0 for r in basis),
        db_unchanged=sha(database) == db_hash,
        qualifications=["Controls are the fixed first 20 code-sorted intersections, not a representative random whole-market sample.",
            "Raw prices, volume and amount are compared directly in their declared units, without rescaling.",
            "Adjusted-price disagreements do not by themselves establish which provider is correct.",
            "No vendor prices are spliced into the signal input."])
    save("control_vendor_comparison.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
