"""One bounded anonymous public SDK probe; no retries, new credentials or ingestion."""
import importlib.metadata
import json
import socket
import subprocess
import sys
from pathlib import Path

from common import HERE, OUT, guard, now, save

FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"


def collect(result):
    rows = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    return dict(error_code=result.error_code, error_msg=result.error_msg, fields=result.fields, rows=rows)


def child():
    socket.setdefaulttimeout(10)
    import baostock as bs
    specification = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))["alternative_probe"]
    logged_in = False
    try:
        login = bs.login()
        save("baostock_probe/login.json", dict(captured_at_utc=now(), error_code=login.error_code, error_msg=login.error_msg))
        if login.error_code != "0":
            return
        logged_in = True
        for code in specification["codes"]:
            ticker, market = code.split(".")
            sdk_code = f"{market.lower()}.{ticker}"
            basic = collect(bs.query_stock_basic(code=sdk_code))
            save(f"baostock_probe/{code}_basic.json", dict(captured_at_utc=now(), query=dict(function="query_stock_basic", code=sdk_code), **basic))
            if basic["error_code"] != "0":
                # In particular, do not evade authorization/rate-limit errors.
                break
            query = dict(code=sdk_code, fields=FIELDS, start_date=specification["window"][0],
                         end_date=specification["window"][1], frequency="d", adjustflag="3")
            historical = collect(bs.query_history_k_data_plus(**query))
            save(f"baostock_probe/{code}_history.json", dict(captured_at_utc=now(), query=dict(function="query_history_k_data_plus", **query), **historical))
            if historical["error_code"] != "0":
                break
    finally:
        if logged_in:
            bs.logout()


def main():
    protocol = guard()
    folder = OUT / "baostock_probe"
    folder.mkdir(exist_ok=False)
    invoked = now()
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"],
                                capture_output=True, timeout=protocol["alternative_probe"]["overall_timeout_seconds"])
        execution = dict(exit_code=result.returncode, timed_out=False,
            stdout=result.stdout.decode("utf-8", errors="replace"), stderr=result.stderr.decode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired as error:
        execution = dict(exit_code=None, timed_out=True,
            stdout=(error.stdout or b"").decode("utf-8", errors="replace"),
            stderr=(error.stderr or b"").decode("utf-8", errors="replace"))
    save("baostock_probe/execution.json", dict(invoked_at_utc=invoked, completed_at_utc=now(),
         sdk_version=importlib.metadata.version("baostock"), **execution))
    records = []
    for code in protocol["alternative_probe"]["codes"]:
        path = folder / f"{code}_history.json"
        record = dict(ts_code=code, attempted=path.exists(), rows=None, usable_for_research=False)
        if path.exists():
            response = json.loads(path.read_text(encoding="utf-8"))
            record.update(error_code=response["error_code"], error_msg=response["error_msg"], rows=len(response["rows"]))
        records.append(record)
    summary = dict(captured_at_utc=now(), timed_out=execution["timed_out"], requests=records,
        promoted_into_rps_input=False,
        qualification="Two-code feasibility probe only; timeout, error and empty results remain distinct; no broad coverage claim.")
    save("baostock_probe/summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    child() if "--child" in sys.argv else main()
