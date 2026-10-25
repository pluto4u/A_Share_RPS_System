"""Inspect raw/cache/action consistency, without promoting data or running signals."""
import json
from collections import Counter

import numpy as np
import pandas as pd
from pytdx.reader import GbbqReader

from common import CACHE, FIELDS, GBBQ, OLD, OUT, PRICE, comparison, guard, load_registered_adjust, now, read_raw, save


def main():
    protocol = guard()
    if (OUT / "reconciliation.json").exists():
        raise FileExistsError("Reconciliation already exists")
    adjust = load_registered_adjust(protocol)
    print("Reading registered corporate-action file", flush=True)
    events = GbbqReader().get_df(str(GBBQ))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d", errors="raise")
    events["ts_code"] = events.code.astype(str).str.zfill(6) + events.market.map({0: ".SZ", 1: ".SH"})
    target = pd.read_csv(OLD / "exchange_evidence/repair_backlog.csv", dtype={"ticker": str}, parse_dates=["terminal_date"])
    raw_inventory = pd.read_csv(OLD / "exchange_evidence/configured_raw_code_coverage.csv")
    targets = [dict(ts_code=r.ts_code, path=r.path, group="terminated") for r in raw_inventory[raw_inventory.exists].itertuples()]
    targets += [dict(**r, group="active_control") for r in protocol["controls"]]
    selected_codes = [r["ts_code"] for r in targets]
    with np.load(CACHE, allow_pickle=False) as z:
        lookup = {str(c): i for i, c in enumerate(z["codes"])}
        calendar = pd.DatetimeIndex(z["dates"])
        selected = [lookup[c] for c in selected_codes]
        arrays = {f: z[f][selected] for f in FIELDS}
    terminal = target.set_index("ts_code").terminal_date
    selected_events = events[events.ts_code.isin(selected_codes)].copy()
    selected_events.to_csv(OUT / "corporate_actions.csv", index=False)
    categories = events.groupby("category").size().to_dict()
    records, examples, gaps = [], [], []
    start, end = map(pd.Timestamp, protocol["study_window"])
    for i, row in enumerate(targets):
        code = row["ts_code"]
        raw = read_raw(row["path"])
        cache = pd.DataFrame({f: arrays[f][i] for f in FIELDS}, index=calendar)
        cache = cache.loc[np.isfinite(cache.close)]
        r = raw.set_index("trade_date").loc[start:end]
        c = cache.loc[start:end]
        common = r.index.intersection(c.index)
        raw_only = r.index.difference(c.index)
        cache_only = c.index.difference(r.index)
        ev = events[events.ts_code == code]
        post = ev[ev.date > raw.trade_date.max()]
        record = dict(ts_code=code, group=row["group"], raw_rows=len(raw), raw_window_rows=len(r),
            cache_window_rows=len(c), common_rows=len(common), raw_only_dates=len(raw_only), cache_only_dates=len(cache_only),
            raw_last_date=str(raw.trade_date.max().date()), cache_last_date=str(cache.index.max().date()),
            corporate_actions_total=len(ev), actions_after_raw_last_date=len(post),
            unsupported_action_categories=json.dumps(sorted(set(ev.category) - set(range(1, 11)))),
            duplicate_action_dates=int(ev[ev.category == 1].date.duplicated().sum()),
            adjusted_reconstruction_status="not_attempted", accepted_for_research=False)
        if code in terminal.index:
            record["raw_positive_bars_after_terminal"] = int(((raw.trade_date > terminal[code]) & (raw.vol > 0)).sum())
        for direction, missing_dates in [("raw_only", raw_only), ("cache_only", cache_only)]:
            for date in missing_dates:
                gaps.append(dict(ts_code=code, group=row["group"], direction=direction, trade_date=str(date.date())))
        if len(common):
            for field, spec in [("vol", protocol["volume_tolerance"]), ("amount", protocol["amount_tolerance"])]:
                result = comparison(c.loc[common], r.loc[common], [field], **spec)
                record.update({f"{field}_{key}": value for key, value in result.items()})
            # Each cached candle should share one adjustment factor across all OHLC.
            factors = c.loc[common, PRICE].to_numpy(float) / r.loc[common, PRICE].to_numpy(float)
            spread = np.max(factors, axis=1) / np.min(factors, axis=1) - 1
            record["inconsistent_ohlc_factor_dates"] = int((spread > protocol["price_tolerance"]["rtol"]).sum())
            record["max_ohlc_factor_spread"] = float(spread.max())
        try:
            rebuilt, action_count = adjust(raw, ev, pd.Timestamp(protocol["adjustment_target"]), bars=None)
            rebuilt = rebuilt.set_index("trade_date")
            if not len(common):
                raise ValueError("No overlapping observations")
            result = comparison(c.loc[common], rebuilt.loc[common], PRICE, **protocol["price_tolerance"])
            record.update(adjusted_reconstruction_status="completed", adjustment_events_applied=action_count,
                          **{f"price_{key}": value for key, value in result.items()})
            numeric_equal = result["mismatched_cells"] == 0 and record["vol_mismatched_cells"] == 0 and record["amount_mismatched_cells"] == 0
            record["numeric_overlap_agrees"] = numeric_equal
            record["window_date_sets_agree"] = not len(raw_only) and not len(cache_only)
            record["local_reconciliation_agrees"] = numeric_equal and record["window_date_sets_agree"]
            if not numeric_equal:
                a, b = c.loc[common, PRICE].to_numpy(float), rebuilt.loc[common, PRICE].to_numpy(float)
                bad = np.any(~np.isclose(a, b, **protocol["price_tolerance"]), axis=1)
                for date in common[bad][:3]:
                    examples.append(dict(ts_code=code, trade_date=str(date.date()),
                        cache_close=float(c.loc[date, "close"]), reconstructed_close=float(rebuilt.loc[date, "close"]),
                        raw_close=float(r.loc[date, "close"]), caveat="reconstruction reuses original algorithm; disagreement is not assigned to a vendor"))
        except ValueError as error:
            record.update(adjusted_reconstruction_status="rejected", rejection_reason=str(error),
                          numeric_overlap_agrees=False, local_reconciliation_agrees=False)
        records.append(record)
        if (i + 1) % 20 == 0:
            print(f"Reconciled {i + 1}/{len(targets)} codes", flush=True)
    results = pd.DataFrame(records)
    results.to_csv(OUT / "reconciliation_by_code.csv", index=False)
    pd.DataFrame(gaps, columns=["ts_code", "group", "direction", "trade_date"]).to_csv(OUT / "date_gaps.csv", index=False)
    pd.DataFrame(examples).to_csv(OUT / "price_disagreement_examples.csv", index=False)
    summaries = {}
    for group, frame in results.groupby("group"):
        summaries[group] = dict(codes=len(frame), common_rows=int(frame.common_rows.sum()),
            reconstruct_completed=int((frame.adjusted_reconstruction_status == "completed").sum()),
            reconstruct_rejected=int((frame.adjusted_reconstruction_status == "rejected").sum()),
            rejected_reasons=dict(Counter(frame.rejection_reason.dropna())) if "rejection_reason" in frame else {},
            numeric_overlap_agrees=int(frame.numeric_overlap_agrees.sum()),
            date_sets_agree=int(((frame.raw_only_dates == 0) & (frame.cache_only_dates == 0)).sum()),
            local_reconciliation_agrees=int(frame.local_reconciliation_agrees.sum()),
            codes_with_raw_only_dates=int((frame.raw_only_dates > 0).sum()), raw_only_dates=int(frame.raw_only_dates.sum()),
            codes_with_cache_only_dates=int((frame.cache_only_dates > 0).sum()), cache_only_dates=int(frame.cache_only_dates.sum()),
            volume_mismatched_cells=int(frame.vol_mismatched_cells.fillna(0).sum()),
            amount_mismatched_cells=int(frame.amount_mismatched_cells.fillna(0).sum()),
            price_mismatched_cells=int(frame.price_mismatched_cells.fillna(0).sum()),
            accepted_for_research=0)
    save("reconciliation.json", dict(completed_at_utc=now(), groups=summaries,
        corporate_action_file=dict(total_records=len(events), earliest=str(events.date.min().date()),
            latest=str(events.date.max().date()), category_counts={str(k): int(v) for k, v in categories.items()},
            announcement_timestamp_available=False),
        promoted_into_rps_input=False, cleared_for_investment=False,
        qualifications=protocol["qualifications"]))
    print(json.dumps(summaries, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
