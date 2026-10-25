"""Independent reconciliation of exported research books against frozen prices."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def reconcile(output: Path) -> dict[str, Any]:
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    cfg = audit["config"]
    cache = Path(audit["snapshot_manifest"]).parent
    with np.load(cache / "panel.npz", allow_pickle=False) as data:
        codes = {str(c): i for i, c in enumerate(data["codes"])}
        dates = {str(d): j for j, d in enumerate(data["dates"])}
        prices = pd.DataFrame(data["close"].T).ffill().to_numpy().T
        opens = data["open"]
    maximum_cash_error = maximum_value_error = 0.0
    checked = 0
    for daily_path in sorted(output.glob("*_daily.csv")):
        prefix = str(daily_path)[: -len("daily.csv")]
        closed = read_rows(Path(prefix + "trades.csv"))
        opened = read_rows(Path(prefix + "open_positions.csv"))
        positions = closed + opened
        for row in positions:
            price = float(opens[codes[row["code"]], dates[row["entry_date"]]])
            row["units_recomputed"] = str(
                float(row["cost"]) / (price * (1 + cfg["slippage"]) * (1 + cfg["commission"]))
            )
        for day in read_rows(daily_path):
            date = day["date"]
            cash = float(cfg["capital"])
            value = 0.0
            held = 0
            for position in positions:
                if position["entry_date"] > date:
                    continue
                cash -= float(position["cost"])
                if position.get("exit_date", "9999-12-31") <= date:
                    cash += float(position["proceeds"])
                else:
                    value += float(position["units_recomputed"]) * float(
                        prices[codes[position["code"]], dates[date]]
                    )
                    held += 1
            cash_error = abs(cash - float(day["cash"]))
            value_error = abs(value + cash - float(day["equity"]))
            maximum_cash_error = max(maximum_cash_error, cash_error)
            maximum_value_error = max(maximum_value_error, value_error)
            if held != int(day["positions"]) or held > cfg["slots"]:
                raise AssertionError(f"Position mismatch {daily_path.name} {date}")
            if max(cash_error, value_error) > cfg["capital"] * 1e-7:
                raise AssertionError(f"Ledger mismatch {daily_path.name} {date}")
            checked += 1
    if not checked:
        raise AssertionError("No books found")
    return {
        "status": "reconciled",
        "portfolio_sessions_checked": checked,
        "max_cash_error": maximum_cash_error,
        "max_equity_error": maximum_value_error,
        "method": (
            "Independent exported cash flows plus units from frozen open prices; no engine helpers"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = reconcile(args.output)
    (args.output / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
