"""Hold prices and rules fixed; quantify only a registered local data sensitivity."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_input_impact_20261007/v1"
LABELS = dict(universe="共同研究集合", rps_only="仅RPS", pv_only="仅量价", rps_pv="RPS＋量价",
              low_only="仅低位", low_turn="低位转强", oversold_only="仅超跌", oversold_confirm="超跌确认")
spec = importlib.util.spec_from_file_location("frozen_pv_impact", ROOT / "research/rps_pv_20261006/study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def save(name, value):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def activity_masks(close, high, amount, volume, descriptors):
    """Exact amount-dependent clauses; price descriptors are fixed full-panel values."""
    med20 = amount.shift(1).rolling(20, min_periods=20).median()
    eligible = close.rolling(251, min_periods=251).count().eq(251) & (amount > 0) & (volume > 0) & (med20 >= 20_000_000)
    pv = (close > close.shift(60)) & (close < close.shift(5)) & (amount.rolling(5, min_periods=5).mean() < amount.shift(5).rolling(20, min_periods=20).mean()) & (close > high.shift(1))
    rps = descriptors["rps120"] >= 80
    low = descriptors["position250"] <= .30
    turn = low & (descriptors["rps50"] > descriptors["rps50"].shift(20)) & (close > high.shift(1)) & (amount > med20)
    oversold = descriptors["oversold_z5"] <= -2
    return dict(universe=eligible, rps_only=eligible & rps, pv_only=eligible & pv, rps_pv=eligible & pv & rps,
                low_only=eligible & low, low_turn=eligible & turn, oversold_only=eligible & oversold,
                oversold_confirm=eligible & oversold & (close > close.shift(1)) & (amount < amount.shift(1)))


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    p = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    source = ROOT / "output/tdx_stock_daily_with_rps.parquet"
    focus_path = ROOT / p["patch_source"]
    controls_path = ROOT / "output/rps_repair_20261006/v1/controls_export_plan.json"
    paths = [source, focus_path, controls_path, ROOT / "research/rps_pv_20261006/study.py", HERE / "protocol.json", HERE / "impact.py"]
    hashes = {str(path):study.sha(path) for path in paths}
    save("registration.json", dict(registered_at_utc=datetime.now(timezone.utc).isoformat(), protocol=p, hashes=hashes))
    print("Scope and inputs registered; loading frozen panel", flush=True)
    prices, quality = study.load_inputs(source, "2026-09-30", OUT)
    print("Computing unchanged full-population ranks/descriptors", flush=True)
    baseline, descriptors = study.features(prices["close"], prices["high"], prices["amount"], prices["vol"])
    codes = json.loads(controls_path.read_text(encoding="utf-8"))["codes"]
    focus = pd.read_csv(focus_path, parse_dates=["trade_date"])
    assert len(focus) == 33 and focus[["tdx","baostock","hithink"]].notna().all().all()
    assert set(focus.ts_code).issubset(codes)
    assert np.isclose(focus.tdx, focus.baostock, rtol=1e-6, atol=1).all()
    selected = {name:frame.loc[:, codes].copy() for name,frame in prices.items()}
    fixed = {name:frame.loc[:, codes] for name,frame in descriptors.items()}
    old = activity_masks(selected["close"], selected["high"], selected["amount"], selected["vol"], fixed)
    for name, mask in old.items():
        assert mask.equals(baseline[name].loc[:, codes]), name
    before_amount = selected["amount"].copy()
    before_volume = selected["vol"].copy()
    patch_records = []
    for row in focus.itertuples():
        frame = selected[row.field]
        previous = float(frame.loc[row.trade_date, row.ts_code])
        assert np.isclose(previous, row.hithink, rtol=1e-6, atol=1)
        assert row.baostock > 0 and previous > 0
        frame.loc[row.trade_date, row.ts_code] = np.float32(row.baostock)
        patch_records.append(dict(ts_code=row.ts_code, trade_date=str(row.trade_date.date()), field=row.field,
                                  previous=previous, replacement=float(frame.loc[row.trade_date,row.ts_code])))
    assert np.array_equal((before_volume > 0).values, (selected["vol"] > 0).values)
    assert np.array_equal((before_amount > 0).values, (selected["amount"] > 0).values)
    new = activity_masks(selected["close"], selected["high"], selected["amount"], selected["vol"], fixed)
    events, totals = [], []
    for name in study.MODELS:
        removed = old[name] & ~new[name]
        added = new[name] & ~old[name]
        for change, frame in [("removed",removed), ("added",added)]:
            for i,j in np.argwhere(frame.to_numpy()):
                events.append(dict(model=name, signal_date=str(frame.index[i].date()), ts_code=frame.columns[j], change=change))
        totals.append(dict(model=name, label=LABELS[name], added=int(added.values.sum()), removed=int(removed.values.sum()),
                           changed=int((old[name] ^ new[name]).values.sum())))
    events_frame = pd.DataFrame(events, columns=["model","signal_date","ts_code","change"])
    events_frame.to_csv(OUT / "changed_signals.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(totals).to_csv(OUT / "model_sensitivity.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(patch_records).to_csv(OUT / "patch_receipt.csv", index=False, encoding="utf-8-sig")
    selected["amount"].to_parquet(OUT / "amount_sensitivity_20_controls.parquet")
    before_amount.to_parquet(OUT / "amount_original_20_controls.parquet")
    input_guard = all(study.sha(Path(path)) == expected for path,expected in hashes.items())
    assert input_guard
    summary = dict(user_date="2026-10-07", patched_cells=len(focus),
        controls=len(codes), changed_stock_dates=events_frame[["signal_date","ts_code"]].drop_duplicates().shape[0],
        first_changed_date=events_frame.signal_date.min() if len(events_frame) else None,
        last_changed_date=events_frame.signal_date.max() if len(events_frame) else None,
        model_changes=totals, amount_cells=int(focus.field.eq("amount").sum()), volume_cells=int(focus.field.eq("vol").sum()),
        original_inputs_unchanged=input_guard, price_and_rps_values_changed=False,
        label_price_values_or_positive_volume_validity_changed=False, outcomes_recomputed=False,
        scope="33 corroborated cells only, in-memory float32 sensitivity; not full-market impact, portfolio return, official repair, or significance rerun")
    save("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
