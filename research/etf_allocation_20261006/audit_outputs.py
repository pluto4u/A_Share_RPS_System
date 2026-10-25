"""Independent arithmetic reconstruction from emitted orders and raw prices."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/etf_allocation_20261006"


def main():
    rows = []
    raw = {code + ".SH": pd.read_csv(OUT/"raw"/f"{code}_raw.csv", parse_dates=["date"]).set_index("date")
           for code in ("510300", "510500", "511010", "518880")}
    for path in sorted(OUT.glob("*_equity.csv")):
        prefix = path.name.removesuffix("_equity.csv")
        book = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        orders = pd.read_csv(OUT/f"{prefix}_orders.csv", parse_dates=["date", "signal_date"])
        dp = OUT/f"{prefix}_dividends.csv"
        dividends = pd.read_csv(dp, parse_dates=["date"]) if dp.stat().st_size > 2 else pd.DataFrame()
        reconstructed = pd.DataFrame(0., index=book.index, columns=["cash", *raw])
        reconstructed["cash"] = 1_000_000.
        filled = orders[orders.quantity > 0]
        for symbol in raw:
            group = filled[filled.symbol == symbol].copy()
            change = np.where(group.side == "buy", group.quantity, -group.quantity)
            delta = pd.Series(change, index=group.date).groupby(level=0).sum()
            reconstructed[symbol] = delta.reindex(book.index, fill_value=0).cumsum()
        flow = np.where(filled.side == "buy",
                        -filled.quantity * filled.fill - filled.fee,
                        filled.quantity * filled.fill - filled.fee)
        flow = pd.Series(flow, index=filled.date).groupby(level=0).sum()
        reconstructed.cash += flow.reindex(book.index, fill_value=0).cumsum()
        if len(dividends):
            payout = dividends.groupby("date").cash.sum()
            reconstructed.cash += payout.reindex(book.index, fill_value=0).cumsum()
        mv = sum(reconstructed[s] * raw[s].close.reindex(book.index) for s in raw)
        share_error = abs(reconstructed[list(raw)] - book[list(raw)]).to_numpy().max()
        cash_error = abs(reconstructed.cash - book.cash).max()
        equity_error = abs(reconstructed.cash + mv + book.receivable - book.equity).max()
        delayed = bool((orders.date > orders.signal_date).all())
        passed = share_error == 0 and cash_error < 1e-6 and equity_error < 1e-6 and delayed
        rows.append(dict(run=prefix, shares_error=float(share_error), cash_error=float(cash_error),
                         equity_error=float(equity_error), signal_before_fill=delayed, passed=passed))
    pd.DataFrame(rows).to_csv(OUT/"independent_checks.csv", index=False)
    result = dict(runs=len(rows), passed=sum(row["passed"] for row in rows), all_pass=all(row["passed"] for row in rows),
                  limitation="Independent cash/quantity arithmetic; receivable completeness and real auction fills are not independently certified.")
    (OUT/"verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    if not result["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
