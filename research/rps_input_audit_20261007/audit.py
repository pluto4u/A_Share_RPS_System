"""Bounded read-only source audit; registration precedes result inspection."""
import hashlib
import json
import socket
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_input_audit_20261007/v1"
OLD = ROOT / "output/rps_repair_20261006/v1"
FIELDS = ["open", "high", "low", "close", "vol", "amount"]
SDK_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def save(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def protocol():
    return json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))


def child():
    import baostock as bs
    p = protocol()
    socket.setdefaulttimeout(p["third_source"]["socket_timeout_seconds"])
    login = bs.login()
    save("baostock/login.json", dict(error_code=login.error_code, error_msg=login.error_msg))
    if login.error_code != "0":
        return
    try:
        for code in p["codes"]:
            ticker, exchange = code.split(".")
            args = dict(code=f"{exchange.lower()}.{ticker}", fields=SDK_FIELDS,
                        start_date=p["window"][0], end_date=p["window"][1], frequency="d", adjustflag="3")
            result = bs.query_history_k_data_plus(**args)
            rows = []
            while result.error_code == "0" and result.next():
                rows.append(result.get_row_data())
            save(f"baostock/{code}.json", dict(query=args, fields=result.fields, rows=rows,
                 error_code=result.error_code, error_msg=result.error_msg))
            if result.error_code != "0":
                break
    finally:
        bs.logout()


def read_tdx(code):
    ticker, exchange = code.split(".")
    path = Path(f"C:/new_tdx/vipdoc/{exchange.lower()}/lday/{exchange.lower()}{ticker}.day")
    blob = path.read_bytes()
    if not blob or len(blob) % 32:
        raise ValueError("Empty/truncated TDX records")
    frame = pd.DataFrame(list(struct.iter_unpack("<5If2I", blob)),
        columns=["trade_date", "open", "high", "low", "close", "amount", "vol", "reserved"])
    frame.trade_date = pd.to_datetime(frame.trade_date.astype(str), format="%Y%m%d")
    frame[FIELDS[:4]] = frame[FIELDS[:4]] / 100
    frame["ts_code"] = code
    return frame.drop(columns="reserved"), path


def compare(left, right, field, p):
    tolerance = p["price_tolerance" if field in FIELDS[:4] else "liquidity_tolerance"]
    if pd.isna(left) or pd.isna(right):
        return None
    return bool(np.isclose(left, right, **tolerance))


def main():
    p = protocol()
    OUT.mkdir(parents=True, exist_ok=False)
    prior_codes = json.loads((OLD / "controls_export_plan.json").read_text(encoding="utf-8"))["codes"]
    assert p["codes"] == prior_codes
    package = Path("C:/Users/pluto4u/AppData/Roaming/npm/node_modules/@hithink-tech/hithink-finance-cli")
    importer = package / "dist/infrastructure/duckdb/importer.js"
    paths = [HERE / "protocol.json", HERE / "audit.py", OLD / "hithink_raw_controls.csv",
             OLD / "large_liquidity_disagreements.csv", Path(p["cache"]), importer,
             ROOT / "tmp/refresh_rps_hithink.py", ROOT / "data/hithink/market.duckdb"]
    for code in p["codes"]:
        ticker, exchange = code.split(".")
        paths.append(Path(f"C:/new_tdx/vipdoc/{exchange.lower()}/lday/{exchange.lower()}{ticker}.day"))
    registration = dict(registered_at_utc=datetime.now(timezone.utc).isoformat(),
                        protocol=p, hashes={str(path): sha(path) for path in paths})
    save("registration.json", registration)
    print("Registered fixed active controls/window/tolerances before result inspection", flush=True)
    try:
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"],
            capture_output=True, timeout=p["third_source"]["overall_timeout_seconds"])
        execution = dict(exit_code=run.returncode, timed_out=False,
            stdout=run.stdout.decode("utf-8", errors="replace"), stderr=run.stderr.decode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired:
        execution = dict(exit_code=None, timed_out=True)
    save("execution.json", execution)

    start, end = map(pd.Timestamp, p["window"])
    raw = pd.read_csv(OLD / "hithink_raw_controls.csv", parse_dates=["trade_date"])
    raw = raw.loc[raw.trade_date.between(start, end)].copy()
    tdx = pd.concat([read_tdx(code)[0] for code in p["codes"]], ignore_index=True)
    tdx = tdx.loc[tdx.trade_date.between(start, end)].copy()
    # Same documented importer UTC+8 conversion; bound millisecond filtering broadly,
    # then use exact local calendar dates. Never shift or align by row position.
    cache = pd.read_parquet(p["cache"], filters=[("thscode", "in", p["codes"]),
        ("date_ms", ">=", int((start - pd.Timedelta(days=1)).value // 1_000_000)),
        ("date_ms", "<=", int((end + pd.Timedelta(days=1)).value // 1_000_000))])
    cache["trade_date"] = pd.to_datetime(cache.date_ms + 28_800_000, unit="ms").dt.normalize()
    cache = cache.rename(columns=dict(thscode="ts_code", open_price="open", high_price="high",
        low_price="low", close_price="close", volume="vol", turnover="amount"))
    cache = cache.loc[cache.trade_date.between(start, end), ["ts_code", "trade_date", *FIELDS]]
    bao_frames, requests = [], []
    for code in p["codes"]:
        path = OUT / f"baostock/{code}.json"
        if not path.exists():
            requests.append(dict(ts_code=code, attempted=False, error_code=None, rows=None))
            continue
        response = json.loads(path.read_text(encoding="utf-8"))
        requests.append(dict(ts_code=code, attempted=True, error_code=response["error_code"], rows=len(response["rows"])))
        if response["error_code"] != "0":
            continue
        frame = pd.DataFrame(response["rows"], columns=response["fields"])
        if frame.empty:
            continue
        frame = frame.rename(columns=dict(date="trade_date", volume="vol"))
        frame["ts_code"] = code
        frame.trade_date = pd.to_datetime(frame.trade_date)
        for field in FIELDS:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        frame = frame.loc[frame.tradestatus.eq("1") & frame.vol.gt(0) & frame.amount.gt(0)]
        bao_frames.append(frame[["ts_code", "trade_date", *FIELDS]])
    bao = pd.concat(bao_frames, ignore_index=True) if bao_frames else pd.DataFrame(columns=["ts_code", "trade_date", *FIELDS])
    keys = ["ts_code", "trade_date"]
    tables = {}
    for name, frame in dict(hithink=raw, tdx=tdx, dump=cache, baostock=bao).items():
        assert not frame.duplicated(keys).any(), name
        frame.to_csv(OUT / f"{name}_window.csv", index=False, encoding="utf-8-sig")
        tables[name] = frame.set_index(keys)
    records = []
    for code, date in tables["hithink"].index:
        for field in FIELDS:
            row = dict(ts_code=code, trade_date=str(date.date()), field=field)
            for name, frame in tables.items():
                value = frame.loc[(code, date), field] if (code, date) in frame.index else np.nan
                row[name] = float(value) if pd.notna(value) else None
            for other in ["tdx", "dump", "baostock"]:
                row[f"hithink_matches_{other}"] = compare(row["hithink"], row[other], field, p)
            row["tdx_matches_baostock"] = compare(row["tdx"], row["baostock"], field, p)
            records.append(row)
    cells = pd.DataFrame(records)
    cells.to_csv(OUT / "all_cells.csv", index=False, encoding="utf-8-sig")
    large = pd.read_csv(OLD / "large_liquidity_disagreements.csv", dtype={"trade_date": str})
    focus = large[keys + ["field"]].merge(cells, on=keys + ["field"], how="left", validate="one_to_one")
    focus.to_csv(OUT / "prior_large_cells_recheck.csv", index=False, encoding="utf-8-sig")
    changed = [path for path, expected in registration["hashes"].items() if sha(path) != expected]
    assert not changed, changed
    summary = dict(user_date=p["user_date"], window=p["window"], codes=len(p["codes"]),
        rows_by_source={name: len(frame) for name, frame in tables.items()}, requests=requests,
        prior_large_cells=len(focus), large_cells_bao_available=int(focus.baostock.notna().sum()),
        large_cells_tdx_matches_bao=int(focus.tdx_matches_baostock.eq(True).sum()),
        large_cells_hithink_matches_bao=int(focus.hithink_matches_baostock.eq(True).sum()),
        large_cells_hithink_matches_dump=int(focus.hithink_matches_dump.eq(True).sum()),
        hithink_dump_mismatches=int(cells.hithink_matches_dump.eq(False).sum()),
        original_input_hashes_unchanged=True, original_db_modified=False, strategy_results_recomputed=False,
        root_cause="Not established: current downloaded dump/cache and installed importer do not prove historical upstream payload or historical ingestion version.")
    save("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    child() if "--child" in sys.argv else main()
