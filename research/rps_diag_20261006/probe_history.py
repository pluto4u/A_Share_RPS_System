"""Two finite missing-terminal-code history probes; no DB sync or insertion."""
import json
import shutil
import subprocess
from datetime import datetime,timezone,timedelta

from diagnostic import ROOT

OUT = ROOT / "output/rps_diag_20261006/history_probe"


def main():
    if (OUT / "summary.json").exists():
        raise FileExistsError("Completed probe cannot be overwritten")
    OUT.mkdir(parents=True,exist_ok=True)
    cli = shutil.which("hithink-finance.cmd") or shutil.which("hithink-finance")
    schema_proc = subprocess.run([cli,"schema","market.history","--format","json"],capture_output=True,timeout=30)
    schema = json.loads(schema_proc.stdout.decode("utf-8-sig"))
    (OUT / "schema.json").write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding="utf-8")
    assert schema.get("ok") is True
    tz = timezone(timedelta(hours=8))
    start = int(datetime(2023,6,1,tzinfo=tz).timestamp()*1000)
    end = int(datetime(2023,7,1,tzinfo=tz).timestamp()*1000)-1
    probes = []
    for code in ["601258.SH","000540.SZ"]:
        output = OUT / f"{code}_raw_202306.json"
        arguments = ["market","history","--thscode",code,"--start-ms",str(start),"--end-ms",str(end),
                     "--adjust","none","--output",str(output),"--format","json"]
        invoked = datetime.now(timezone.utc).isoformat()
        proc = subprocess.run([cli,*arguments],capture_output=True,timeout=75)
        stdout = proc.stdout.decode("utf-8-sig")
        # This CLI writes errors to stderr, not stdout. Empty stdout is not
        # proof of an empty market-data response.
        raw_response = stdout.strip() or proc.stderr.decode("utf-8-sig",errors="replace").strip()
        try:
            response = json.loads(raw_response)
        except json.JSONDecodeError:
            response = dict(ok=False,error=dict(code="NO_JSON_ENVELOPE",message=raw_response[:2000]))
        (OUT / f"{code}_stdout.json").write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding="utf-8")
        envelope = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else response
        data = envelope.get("data")
        receipt = dict(invoked_at_utc=invoked,arguments=arguments,exit_code=proc.returncode,
            stdout_ok=response.get("ok"),envelope_ok=envelope.get("ok"),meta=envelope.get("meta"),
            data_type=type(data).__name__,data_keys=list(data) if isinstance(data,dict) else None,
            records=len(data) if isinstance(data,list) else None,error=envelope.get("error"),
            inserted_into_database=False,promoted_into_signal_input=False)
        (OUT / f"{code}_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
        probes.append(receipt)
        error = envelope.get("error") or {}
        if any(word in str(error.get("code", "")).lower() for word in ["auth","key","permission"]):
            break
    (OUT / "summary.json").write_text(json.dumps(dict(
        purpose="bounded historic-data source feasibility, not full repair or new strategy",window_shanghai=["2023-06-01","2023-06-30"],
        probes=probes,database_mutations=False),ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(probes=len(probes),results=[dict(code=r["arguments"][3],ok=r["envelope_ok"],
        records=r["records"],data_keys=r["data_keys"],error=r["error"]) for r in probes]),ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
