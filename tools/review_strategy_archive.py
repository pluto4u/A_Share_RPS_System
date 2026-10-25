"""Audit existing cards without overwriting their historical rules.

Run from any directory: python tools/review_strategy_archive.py
Hashes follow strategy_archive.py: decoded UTF-8, universal newlines, no BOM.
This checks source drift, NOT rule correctness, dependency completeness or returns.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Explicit review queue, not automatic discovery of every possible strategy.
REVIEW_QUEUE = [
    ("tdx-rps-local", "本地TDX RPS多层选股", "rps_analyzer/tdx_stock_screener.py",
     "旧rps-dual卡对应网络入口，不代表本地120/250日RPS；需独立卡。"),
    ("market-timing", "宽度与成交额择时研究", "rps_analyzer/market_timing.py",
     "属于环境层；事件统计不等同可交易组合回测。"),
    ("industry-rps", "行业RPS过滤", "rps_analyzer/industry_rps.py",
     "当前行业映射不是历史成分；历史回测需时点行业或明确标记偏差。"),
    ("trend-start", "趋势启动研究框架", "trend_start/signals.py",
     "可复用信号和执行框架，仍须逐日前缀和交易约束验收。"),
]


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8-sig").encode()).hexdigest()


def audit(registry: dict, root: Path) -> list[dict]:
    rows = []
    for card in registry["strategies"]:
        source = Path(card["source"])
        source = source if source.is_absolute() else root / source
        digest = source_hash(source) if source.is_file() else None
        state = "missing" if digest is None else (
            "unchanged" if digest == card["source_sha256"] else "changed")
        rows.append({"id": card["id"], "name": card["name"],
                     "source": str(source), "source_state": state,
                     "archived_sha256": card["source_sha256"], "current_sha256": digest,
                     "performance_verified": False,
                     "rule_review": "required" if state != "unchanged" else "not_assessed"})
    return rows


def main() -> None:
    archive = ROOT / "strategy_archive"
    registry = json.loads((archive / "registry.json").read_text(encoding="utf-8"))
    rows = audit(registry, ROOT)
    now = datetime.now().astimezone()
    run_id = now.strftime("%Y%m%dT%H%M%S_%f")
    dest = archive / "reviews" / run_id
    dest.mkdir(parents=True, exist_ok=False)
    queue = []
    for identity, name, relative, note in REVIEW_QUEUE:
        source = ROOT / relative
        queue.append({"id": identity, "name": name, "source": relative,
                      "current_sha256": source_hash(source) if source.is_file() else None,
                      "status": "pending_rule_review", "note": note})
    # Preserve changed/new evidence; never rewrite registry, old cards or old snapshots.
    for row in [r for r in rows if r["source_state"] == "changed"] + queue:
        digest = row["current_sha256"]
        if digest:
            source = Path(row["source"])
            source = source if source.is_absolute() else ROOT / source
            snapshot = archive / "sources" / f"{digest}.py"
            snapshot.parent.mkdir(exist_ok=True)
            if not snapshot.exists():
                snapshot.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    payload = {"schema_version": "strategy-review/v1", "reviewed_at": now.isoformat(),
               "git_head": git.stdout.strip() if git.returncode == 0 else None,
               "registry_generated_at": registry.get("generated_at"),
               "scope": "primary source only; unchanged does not mean validated",
               "counts": dict(Counter(r["source_state"] for r in rows)),
               "cards": rows, "additional_review_queue": queue}
    (dest / "review.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 策略卡源码同步审阅", "", f"审阅时间：{now.isoformat()}", "",
             "旧卡、注册表保持原样。变更源码另存内容哈希快照；本报告不是收益验收。",
             "只核对每张卡的主源文件，不覆盖全部依赖、配置和数据变更。", "",
             "## 卡片源码状态", ""]
    lines += [f"- {r['id']} / {r['name']}：{r['source_state']}" for r in rows]
    lines += ["", "## 需新增或单独审阅", ""]
    lines += [f"- {r['id']}：{r['note']}" for r in queue]
    lines += ["", "## 人工规则复核重点", "",
              "- daily-s5：实现已调用Kelly波动率缩放与回撤弹性，旧卡的纯S5描述不足。",
              "- rps-dual：网络50/120日入口与本地120/250日入口分开，不沿用同一策略身份。",
              "- 杯柄：居中窗口须延迟到形态真正可知日，逐日前缀测试通过后再回测。",
              "- 市场宽度出图与择时研究的市场范围、价格口径和指标分母不同，禁止混拼序列。",
              "", "回测协议见 docs/REBACKTEST-PLAN-20261024.md。", ""]
    (dest / "review.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"review": str(dest), "counts": payload["counts"], "queue": len(queue)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
