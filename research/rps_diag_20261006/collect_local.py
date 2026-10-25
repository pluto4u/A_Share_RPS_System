"""Read-only local CLI evidence, full envelopes/SQL saved; no data mutation."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/rps_diag_20261006/local_evidence"
DB = ROOT / "data/hithink/market.duckdb"


def command(name, arguments):
    cli = shutil.which("hithink-finance.cmd") or shutil.which("hithink-finance")
    start = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run([cli, "--db", str(DB), *arguments, "--format", "json"],
                          capture_output=True, timeout=120)
    raw = proc.stdout.decode("utf-8-sig")
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        response = dict(ok=False, data=None, error="CLI did not return JSON",
                        exit_code=proc.returncode, stderr=proc.stderr.decode("utf-8", errors="replace"))
    (OUT / f"{name}.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / f"{name}_receipt.json").write_text(json.dumps(dict(
        invoked_at_utc=start, arguments=arguments, db=str(DB), exit_code=proc.returncode,
        ok=response.get("ok")), ensure_ascii=False, indent=2), encoding="utf-8")
    if proc.returncode or response.get("ok") is not True:
        raise RuntimeError(f"{name}: CLI request failed")
    return response


def query(name, sql):
    (OUT / f"{name}.sql").write_text(sql, encoding="utf-8")
    # Windows .cmd argument parsing does not preserve embedded newlines reliably.
    return command(name, ["db", "query", "--sql", " ".join(sql.split())])


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    status = command("status", ["data", "status"])
    validate = command("validate", ["data", "validate"])
    columns = query("columns", "SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_name IN ('dim_symbol','raw_kline_daily','raw_adjustment_events','stg_symbol') ORDER BY table_name,ordinal_position LIMIT 100")
    print(json.dumps(dict(status=status["data"], validate=validate["data"], columns=columns["data"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
