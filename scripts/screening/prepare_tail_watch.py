"""Freeze W1 plans from the previous session's V2 watchlist, without minute claims."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
from pytdx.reader import GbbqReader
from rps_analyzer.tail_watch import TailPlan, tdx_formula
from rps_analyzer.tdx_reader import read_day_file
from rps_analyzer.watchlist_view import watchlist
from run_local_tdx_compare import adjust


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit_bytes = (args.report_dir / "audit.json").read_bytes()
    audit = json.loads(audit_bytes)
    prior = date.fromisoformat(audit["as_of"])
    if audit["version"] != "right_confirmation_v2" or prior >= args.session:
        raise ValueError("requires a prior-session V2 report")
    table = pd.read_csv(args.report_dir / "all_evaluated.csv")
    if not table.as_of.eq(prior.isoformat()).all() or table.ts_code.duplicated().any():
        raise ValueError("report dates or security grain invalid")
    # Observed index sessions are an offline calendar check, not weekday arithmetic.
    index_path = args.tdx_root / "vipdoc/sh/lday/sh000001.day"
    calendar_ok = False
    if index_path.exists():
        index = read_day_file(str(index_path))
        if index is not None:
            dates = index.trade_date.dt.date
            previous = dates[dates < args.session]
            calendar_ok = bool(
                args.session in set(dates) and len(previous) and previous.max() == prior
            )
    cache = args.tdx_root / "T0002/hq_cache/gbbq"
    events = GbbqReader().get_df(str(cache))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]
    args.output.mkdir(parents=True, exist_ok=True)
    plans, excluded = [], []
    for _, row in watchlist(table).iterrows():
        try:
            code, market = str(row.ts_code).split(".")
            path = (
                args.tdx_root / "vipdoc" / market.lower() / "lday" / f"{market.lower()}{code}.day"
            )
            raw = read_day_file(str(path))
            if raw is None or not raw.trade_date.eq(pd.Timestamp(prior)).any():
                raise ValueError("missing prior-session daily bar")
            ev = grouped.get(str(row.ts_code), empty)
            d, _ = adjust(raw, ev, pd.Timestamp(prior), bars=None)
            later = ev[(ev.date > pd.Timestamp(prior)) & (ev.date <= pd.Timestamp(args.session))]
            tr = pd.concat(
                [d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()],
                axis=1,
            ).max(axis=1)
            price_ok = bool(later.empty and abs(float(d.close.iloc[-1]) - float(row.close)) < 0.001)
            source = hashlib.sha256(
                audit_bytes + path.read_bytes() + cache.read_bytes()
            ).hexdigest()
            plan = TailPlan(
                ts_code=str(row.ts_code),
                prior_session=prior,
                session=args.session,
                previous_session_verified=calendar_ok,
                price_basis_verified=price_ok,
                eligible=True,
                pivot=float(d.high.tail(20).max()),
                atr20=float(tr.tail(20).mean()),
                ma20=float(d.close.tail(20).mean()),
                evidence_sha256=source,
            )
            stem = str(row.ts_code)
            (args.output / f"{stem}.plan.json").write_text(
                plan.model_dump_json(indent=2), encoding="utf-8"
            )
            (args.output / f"{stem}.tdx.txt").write_text(tdx_formula(plan), encoding="utf-8")
            plans.append(
                {
                    "ts_code": stem,
                    "name": str(row["name"]),
                    "enabled": bool(calendar_ok and price_ok),
                    "state": "缺分钟数据，未评估W1",
                    "pivot": plan.pivot,
                    "atr20": plan.atr20,
                    "ma20": plan.ma20,
                }
            )
        except (ValueError, FileNotFoundError) as exc:
            excluded.append({"ts_code": str(row.ts_code), "reason": str(exc)})
    result = {
        "prior_session": prior.isoformat(),
        "session": args.session.isoformat(),
        "calendar_verified_from_index": calendar_ok,
        "plans": plans,
        "excluded": excluded,
        "minute_replay_performed": False,
        "historical_performance_validated": False,
    }
    (args.output / "plans_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "plans": len(plans),
                "enabled": sum(p["enabled"] for p in plans),
                "excluded": len(excluded),
                "calendar_ok": calendar_ok,
            }
        )
    )


if __name__ == "__main__":
    main()
