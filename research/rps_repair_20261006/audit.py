"""Record source disagreements and distinguish returned suspension rows from trades."""
import json

import numpy as np
import pandas as pd

from common import OUT, ROOT, comparison, guard, now, read_raw, save


def main():
    protocol = guard()
    if (OUT / "source_quality.json").exists():
        raise FileExistsError("Quality evidence already exists")
    hithink = pd.read_csv(OUT / "hithink_raw_controls.csv", parse_dates=["trade_date"])
    discrepancies = []
    for row in protocol["controls"]:
        raw = read_raw(row["path"])
        other = hithink[hithink.ts_code == row["ts_code"]]
        merged = raw.merge(other, on="trade_date", suffixes=("_tdx", "_hithink"), validate="one_to_one")
        for field, tolerance in [("vol", protocol["volume_tolerance"]), ("amount", protocol["amount_tolerance"])]:
            bad = ~np.isclose(merged[f"{field}_tdx"], merged[f"{field}_hithink"], **tolerance)
            for r in merged[bad].itertuples():
                a, b = float(getattr(r, f"{field}_tdx")), float(getattr(r, f"{field}_hithink"))
                discrepancies.append(dict(ts_code=row["ts_code"], trade_date=str(r.trade_date.date()),
                    field=field, tdx_value=a, hithink_value=b, tdx_to_hithink_ratio=a / b if b > 0 else None))
    differences = pd.DataFrame(discrepancies)
    differences.to_csv(OUT / "liquidity_disagreements.csv", index=False)
    # A descriptive large-difference flag only; not a new signal/eligibility rule.
    material = differences[(differences.tdx_to_hithink_ratio < .5) | (differences.tdx_to_hithink_ratio > 2)].copy()
    material.to_csv(OUT / "large_liquidity_disagreements.csv", index=False)
    codes = list(material.ts_code.unique())
    frozen = pd.read_parquet(ROOT / "output/tdx_stock_daily_with_rps.parquet",
                            columns=["ts_code", "trade_date", "vol", "amount"], filters=[("ts_code", "in", codes)])
    frozen.trade_date = pd.to_datetime(frozen.trade_date)
    impacted = material.assign(trade_date=pd.to_datetime(material.trade_date)).merge(frozen,
        on=["ts_code", "trade_date"], validate="many_to_one")
    impacted["frozen_value"] = np.where(impacted.field == "vol", impacted.vol, impacted.amount)
    impacted["same_as_hithink_raw"] = np.isclose(impacted.frozen_value, impacted.hithink_value, rtol=1e-6, atol=1)
    impacted.drop(columns=["vol", "amount"]).to_csv(OUT / "frozen_input_liquidity_disagreements.csv", index=False)
    probes = []
    for code in protocol["alternative_probe"]["codes"]:
        response = json.loads((OUT / f"baostock_probe/{code}_history.json").read_text(encoding="utf-8"))
        frame = pd.DataFrame(response["rows"], columns=response["fields"])
        volume = pd.to_numeric(frame.volume, errors="coerce")
        amount = pd.to_numeric(frame.amount, errors="coerce")
        probes.append(dict(ts_code=code, returned_rows=len(frame),
            positive_volume_rows=int((volume > 0).sum()), tradestatus_0_rows=int((frame.tradestatus == "0").sum()),
            tradestatus_1_rows=int((frame.tradestatus == "1").sum()),
            unknown_trade_status_rows=int((~frame.tradestatus.isin(["0", "1"])).sum()),
            missing_volume_rows=int(volume.isna().sum()), missing_amount_rows=int(amount.isna().sum()),
            usable_traded_rows=int(((volume > 0) & (amount > 0) & (frame.tradestatus == "1")).sum()),
            interpretation="Returned June rows establish archival response only; suspension/blank rows are not executable quotes."))
    summary = dict(completed_at_utc=now(), liquidity_disagreement_cells=len(differences),
        large_liquidity_disagreement_cells=len(material),
        large_disagreement_code_dates=int(len(material[["ts_code", "trade_date"]].drop_duplicates())),
        large_disagreement_codes=len(codes), large_disagreement_dates=sorted(material.trade_date.unique().tolist()),
        frozen_panel_rows_checked=len(impacted), frozen_input_reproduces_raw_disagreements=int(impacted.same_as_hithink_raw.sum()),
        baostock_probes=probes, promoted_into_rps_input=False,
        provider_fault_established=False,
        qualifications=["Large means a descriptive TDX/hithink ratio outside [0.5,2], not a strategy threshold.",
            "A cross-source mismatch does not identify which feed or ingestion stage is wrong; do not multiply values to repair by guesswork.",
            "The observed mismatches occur in the frozen strategy input, but their effect on signal counts and returns has not been quantified.",
            "Do not classify a successful API response or suspension placeholder as recovered tradable history."])
    save("source_quality.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
