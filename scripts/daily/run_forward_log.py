"""Forward paper ledger for the frozen combo-F engines (pool-ice > pattern-ice > chase, 20 slots).

Record only: no orders, no position advice. The first real run writes freeze.json (rules + code hashes);
later runs refuse to record if any frozen file changed. Sessions on/before the freeze date are never
recorded, and each session is processed once, in order. Exits only fill empty exit fields.

    python run_forward_log.py                          # record every new TDX session since the last run
    python run_forward_log.py --dry-run --date 2026-10-23   # rehearsal, writes nothing to the ledger
Ledger: outputs/forward/ice_chase_v1/{freeze.json, days.csv, trades.csv}
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = PROJECT_ROOT
sys.path[:0] = [str(ROOT), str(ROOT / "skills/a-share-rotation-trader/scripts")]
from run_daily_rps import names_from_tnf, read_day  # noqa: E402
from trend_start.daily_board import build_recent_panel  # noqa: E402
from trend_start.forward_log import (HOLD, PRIO, SLOTS, allocate, candidates, code_hashes,  # noqa: E402
                                     sessions_to_process, settle)

BOOK = ROOT / "outputs/forward/ice_chase_v1"
TRADE_COLS = ["trade_id", "engine", "code", "name", "signal_date", "entry_px", "hold_bars",
              "exit_date", "exit_px_adj", "ret", "held_bars", "status"]
RULES = {"engines": "pool_ice(0) > pattern_ice(1) > chase(2), shared slots", "slots": SLOTS, "hold_bars": HOLD,
         "entry": "tail buy at the signal-day close; one-price limit-up skipped",
         "exit": "close of the h-th traded bar after entry; one-price limit-down rolls (pattern_backtest._sell_close)",
         "gates": "ice a0.22_s2_w1_none & not overheat(>=85%); chase = repair & amount/prev5>=1.05 & zt>=20 & streak>2",
         "source": "trend_start.forward_log / daily_board, TDX daily + gbbq ratio forward adjustment"}


def load(path: Path, cols: list[str]) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"code": str}) if path.exists() else pd.DataFrame(columns=cols)


def ensure_freeze(today: pd.Timestamp) -> dict:
    fp = BOOK / "freeze.json"
    hashes = code_hashes(ROOT)
    if not fp.exists():
        BOOK.mkdir(parents=True, exist_ok=True)
        freeze = {"version": "ice_chase_v1", "freeze_date": str(today.date()), "rules": RULES, "code_sha256": hashes,
                  "note": "Sessions on or before freeze_date are never recorded. Changing any frozen file requires a new version."}
        fp.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[FREEZE] created {fp}; first recorded session is after {freeze['freeze_date']}")
        return freeze
    freeze = json.loads(fp.read_text(encoding="utf-8"))
    changed = [f for f, h in freeze["code_sha256"].items() if hashes.get(f) != h]
    if changed:
        raise SystemExit(f"[FREEZE] frozen files changed, refusing to record: {changed}. Start a new version instead.")
    return freeze


def process(session: pd.Timestamp, tdx: Path, names: dict, trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    panel, audit = build_recent_panel(tdx, session)
    open_mask = trades.status == "open"
    exits = settle(trades.loc[open_mask], panel)
    for r in exits.itertuples():
        k = trades.index[trades.trade_id == r.trade_id][0]
        if pd.notna(trades.at[k, "exit_date"]):
            continue  # exits are written once
        trades.loc[k, ["exit_date", "exit_px_adj", "ret", "held_bars", "status"]] = \
            [r.exit_date, r.exit_px_adj, r.ret, r.held_bars, "closed"]
    held = set(trades.loc[trades.status == "open", "code"])
    cand, state = candidates(panel, trades)
    picks = allocate(cand, held, SLOTS - len(held))
    new = pd.DataFrame([{"trade_id": f"{session:%Y%m%d}-{p.engine}-{p.code}", "engine": p.engine, "code": p.code,
                         "name": names.get(p.code, ""), "signal_date": str(session.date()), "entry_px": p.close,
                         "hold_bars": HOLD[p.engine], "exit_date": None, "exit_px_adj": None, "ret": None,
                         "held_bars": None, "status": "open"} for p in picks.itertuples()], columns=TRADE_COLS)
    trades = pd.concat([trades, new], ignore_index=True) if len(new) else trades
    day = {"date": str(session.date()), **state, "candidates": len(cand),
           **{f"cand_{e}": int((cand.engine == e).sum()) if len(cand) else 0 for e in PRIO},
           "entered": len(new), "exited": len(exits), "open_after": int((trades.status == "open").sum()),
           "panel_kept": audit["kept"], "recorded_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")}
    return trades, day


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", help="dry-run session (default: latest TDX session)")
    args = ap.parse_args()
    cal = pd.DatetimeIndex(read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date)
    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    if args.dry_run:
        session = pd.Timestamp(args.date) if args.date else cal[-1]
        panel, _ = build_recent_panel(args.tdx_root, session)
        cand, state = candidates(panel, pd.DataFrame(columns=TRADE_COLS))
        picks = allocate(cand, set(), SLOTS)
        out = {"session": str(session.date()), "state": state, "candidates_by_engine": cand.engine.value_counts().to_dict() if len(cand) else {},
               "would_enter": [{"engine": p.engine, "code": p.code, "name": names.get(p.code, ""), "close": p.close}
                               for p in picks.itertuples()]}
        (BOOK / "dryrun").mkdir(parents=True, exist_ok=True)
        (BOOK / "dryrun" / f"dryrun_{session:%Y%m%d}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    today = pd.Timestamp(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    freeze = ensure_freeze(today)
    days = load(BOOK / "days.csv", ["date"])
    trades = load(BOOK / "trades.csv", TRADE_COLS)
    last_done = pd.Timestamp(days.date.max()) if len(days) else None
    todo = sessions_to_process(cal, pd.Timestamp(freeze["freeze_date"]), last_done)
    if not todo:
        print(f"[FORWARD] nothing to record: latest TDX session {cal[-1].date()}, freeze {freeze['freeze_date']}, "
              f"last recorded {last_done.date() if last_done is not None else '—'}. Update TDX daily data after the close, then rerun.")
        return 0
    for s in todo:
        print(f"[FORWARD] session {s.date()}", flush=True)
        trades, day = process(s, args.tdx_root, names, trades)
        days = pd.concat([days, pd.DataFrame([day])], ignore_index=True)
        trades.to_csv(BOOK / "trades.csv", index=False, encoding="utf-8-sig")
        days.to_csv(BOOK / "days.csv", index=False, encoding="utf-8-sig")
    closed = trades.loc[trades.status == "closed"]
    print(json.dumps({"recorded_sessions": len(days), "open": int((trades.status == "open").sum()), "closed": len(closed),
                      "closed_mean_ret_pct": round(float(closed.ret.astype(float).mean() * 100), 3) if len(closed) else None},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
