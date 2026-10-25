"""验证复权闸门的负向路径：报告结论为 unadjusted / unknown 时必须零交易。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

QFQ = Path("output/qfq_verification.json")
BAK = Path("output/_qfq_test.bak")
CMD = [sys.executable, "-u", "skills/a-share-rotation-trader/scripts/run_integrated_decision.py",
       "--date", "20260814", "--account-state", "empty", "--equity", "1000000",
       "--available-cash", "1000000", "--single-trade-risk-pct", "0.50"]


def run(label: str) -> None:
    proc = subprocess.run(CMD, capture_output=True)
    out = proc.stdout.decode("gbk", errors="replace")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    action = next((ln for ln in lines if ln.startswith("动作")), "?")
    g0 = next((ln for ln in lines if " G0 " in ln), "?")
    g2 = next((ln for ln in lines if " G2 " in ln), "?")
    print(f"\n[{label}]\n  {action}\n {g0}\n {g2}")


shutil.copy(QFQ, BAK)
try:
    base = json.loads(QFQ.read_text(encoding="utf-8"))

    for status in ("unadjusted", "unknown"):
        d = dict(base)
        d["price_adjustment"] = status
        d["blocker"] = f"测试注入：{status}"
        QFQ.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        run(f"price_adjustment = {status}")

    # 缺失关键字段
    d = dict(base)
    d.pop("price_adjustment", None)
    QFQ.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    run("price_adjustment 字段缺失")
finally:
    shutil.move(BAK, QFQ)
    run("恢复为 verified_qfq")
