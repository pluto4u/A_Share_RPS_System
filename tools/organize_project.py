"""Inventory and reversibly organize this repository without changing strategy rules.

python tools/organize_project.py scan
python tools/organize_project.py apply
python tools/organize_project.py restore --journal <absolute-journal-path>
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "outputs/catalog"
SKIP = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
ALIASES = {
    "output": "outputs/strategies",
    "logs": "outputs/logs/legacy",
    "reports": "apps/reports",
    "tmp": "research/legacy_workspace",
    "reference_doc": "docs/references",
    "learn": "docs/learning",
    "08.tdx_qushi": "research_inputs/tdx_blocks",
    "serenity-skill": "skills/serenity",
}


def safe(path: Path, base: Path = ROOT) -> Path:
    absolute = Path(os.path.abspath(path))
    if (not absolute.is_relative_to(base.absolute()) or absolute == base.absolute()
            or not absolute.resolve().is_relative_to(base.resolve())):
        raise ValueError(f"Path outside intended workspace: {absolute}")
    return absolute


def files(root: Path = ROOT):
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP
                   and not Path(parent, name).is_symlink()
                   and not (hasattr(os.path, "isjunction") and os.path.isjunction(Path(parent, name)))]
        for name in names:
            p = Path(parent, name)
            if not p.is_symlink():
                yield p


def category(relative: str) -> str:
    parts = Path(relative).parts
    head, suffix = parts[0], Path(relative).suffix.lower()
    if head.startswith("."):
        return "project_metadata"
    if any(part.startswith(".venv-") or part == "site-packages" for part in parts):
        return "local_environment"
    if head in ("tests",):
        return "tests"
    if head in ("config", "backtest") and suffix in (".yaml", ".yml", ".json", ".ini"):
        return "configuration"
    if head in ("data",) or suffix in (".duckdb", ".parquet", ".db"):
        return "data_and_cache"
    if head in ("output", "outputs", "logs"):
        return "generated_artifacts"
    if head in ("reports", "apps"):
        return "report_app"
    if head == "strategy_archive":
        return "strategy_dossier"
    if head in ("skills", ".agents", "serenity-skill"):
        return "agent_skills"
    if head in ("research_inputs", "08.tdx_qushi", "reference_doc"):
        return "research_inputs"
    if head in ("docs", "learn") or suffix == ".md":
        return "documentation"
    if head == "tmp":
        return "legacy_research_workspace"
    if head == "research" or Path(relative).name.startswith("exp_"):
        return "research_experiments"
    if head in ("deploy", ".github") or suffix in (".sh", ".bat", ".ps1"):
        return "deployment_and_operations"
    if suffix == ".py":
        return "source_and_entrypoints"
    return "reference_or_other"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan() -> dict:
    CATALOG.mkdir(parents=True, exist_ok=True)
    rows, sizes, commands = [], defaultdict(list), []
    for path in files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("outputs/catalog/"):
            continue
        stat = path.stat()
        rows.append({"path": rel, "category": category(rel), "bytes": stat.st_size})
        if stat.st_size > 0:
            sizes[stat.st_size].append(path)
        if path.suffix == ".py" and (path.parent == ROOT or rel.startswith("research/experiments/")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                doc = (ast.get_docstring(tree) or "").splitlines()
                has_main = any(isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
                               for node in tree.body)
                commands.append({"id": path.stem, "path": rel, "category": entry_category(path.stem),
                                 "description": next((x.strip() for x in doc if x.strip() and set(x.strip()) != {'='}), ""),
                                 "has_main_guard": has_main,
                                 "command": (f"python -m research.experiments.{path.stem}" if rel.startswith("research/experiments/") else f"python {rel}")})
            except (SyntaxError, UnicodeError):
                pass
    duplicate_groups = []
    for size, candidates in sizes.items():
        if len(candidates) < 2:
            continue
        groups = defaultdict(list)
        for path in candidates:
            groups[file_hash(path)].append(path.relative_to(ROOT).as_posix())
        for digest, paths in groups.items():
            if len(paths) > 1:
                required = any("strategy_archive/" in p or "/site-packages/" in p
                               or p.startswith(("apps/", "reports/")) for p in paths)
                duplicate_groups.append({"sha256": digest, "bytes_each": size,
                                         "paths": paths, "extra_bytes": (len(paths) - 1) * size,
                                         "action": "keep_required_snapshot_or_runtime" if required else "keep_historical_context"})
    summary = {"schema_version": "project-inventory/v1", "root": str(ROOT),
               "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
               "file_count": len(rows), "bytes": sum(r["bytes"] for r in rows),
               "categories": dict(Counter(r["category"] for r in rows)),
               "duplicate_groups": len(duplicate_groups),
               "duplicate_extra_bytes": sum(g["extra_bytes"] for g in duplicate_groups),
               "compatibility_directories": {a:b for a,b in ALIASES.items() if (ROOT/a).exists()}}
    for name, content in (("files.json", {"summary": summary, "files": rows}),
                          ("duplicates.json", duplicate_groups), ("entrypoints.json", commands)):
        (CATALOG / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    with (CATALOG / "files.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("path", "category", "bytes"))
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / "docs/ENTRYPOINTS.md").write_text(
        "# 运行入口索引\n\n由 `python tools/organize_project.py scan` 从实际源码生成。只列入口与类别，不代表收益验收。\n\n"
        + "\n".join(f"- `{r['category']}` · `{r['command']}` — {r['description']}" for r in commands) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return summary


def entry_category(name: str) -> str:
    if name.startswith("exp_"):
        return "research"
    if name.startswith(("audit_", "validate_", "verify_", "diagnose_")):
        return "validation"
    if name.startswith(("present_", "render_", "export_", "plot_", "write_")):
        return "reporting"
    if any(x in name for x in ("daily", "session", "monitor", "signal", "action")):
        return "daily_workflow"
    if any(x in name for x in ("backtest", "research", "optimiz", "study", "eval", "review", "compare")):
        return "research"
    if any(x in name for x in ("cloud", "tos_", "feishu", "handler", "api", "refresh")):
        return "integration"
    return "screening_and_core"


def organize() -> None:
    marker = CATALOG / "organization.json"
    if marker.exists():
        print("Layout already organized; use scan to refresh the inventory.")
        return
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    backup = ROOT.parent / f"{ROOT.name}_cleanup_{stamp}"
    if backup.exists() or backup.is_relative_to(ROOT):
        raise ValueError("Backup must be new and outside project")
    backup.mkdir()
    journal = {"root": str(ROOT), "backup": str(backup), "operations": [], "state": "in_progress"}
    journal_path = backup / "journal.json"
    def record():
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
    def save(path):
        rel = path.relative_to(ROOT)
        dest = backup / "originals" / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            journal["operations"].append({"type": "edit", "path": rel.as_posix(), "sha256_before": file_hash(path)})
            record()
    def move(source, destination):
        safe(source); safe(destination)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        journal["operations"].append({"type": "move", "source": source.relative_to(ROOT).as_posix(),
                                       "destination": destination.relative_to(ROOT).as_posix()})
        record()
        # Same-volume atomic rename only: never copy/delete on a Windows sharing violation.
        os.rename(source, destination)
    (backup / "tracked_changes.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT))
    (backup / "git-status.txt").write_bytes(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    mapping = {}
    for path in ROOT.glob("exp_*.py"):
        mapping[path.stem] = f"research.experiments.{path.stem}"
        save(path)
        move(path, ROOT / "research/experiments" / path.name)
    # Consolidate imports as part of the mechanical package relocation.
    for path in files():
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix != ".py" or rel.startswith(("strategy_archive/", "apps/", "reports/", "tmp/")):
            continue
        text = path.read_text(encoding="utf-8-sig")
        replaced = re.sub(r"(?m)^(\s*from )(exp_[A-Za-z_0-9]+)( import )",
                          lambda m: m[1] + mapping.get(m[2], m[2]) + m[3], text)
        if replaced != text:
            save(path)
            path.write_text(replaced, encoding="utf-8")
    doc_moves = {}
    for path in list(ROOT.glob("*.md")):
        if path.name in ("README.md", "STRATEGY_WORKSPACE.md"):
            continue
        destination = ROOT / "docs/guides" / path.name
        save(path)
        doc_moves[path.relative_to(ROOT).as_posix()] = destination.relative_to(ROOT).as_posix()
        move(path, destination)
    # Rewrite only local Markdown links, rebasing links inside relocated documents.
    for path in files():
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix != ".md" or rel.startswith(("strategy_archive/sources/", "reports/", "apps/")):
            continue
        original_rel = next((old for old, new in doc_moves.items() if new == rel), rel)
        text = path.read_text(encoding="utf-8-sig")
        def link(match):
            label, url = match.groups()
            if re.match(r"[a-zA-Z][\w+.-]*:", url) or url.startswith(("#", "/", "<")):
                return match[0]
            location, sep, anchor = url.partition("#")
            old_absolute = (ROOT / Path(original_rel).parent / location).resolve()
            if not old_absolute.is_relative_to(ROOT):
                return match[0]
            old_rel = old_absolute.relative_to(ROOT).as_posix()
            dest = ROOT / doc_moves.get(old_rel, old_rel)
            new_url = os.path.relpath(dest, path.parent).replace("\\", "/")
            return f"[{label}]({new_url}{sep}{anchor})"
        replaced = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, text)
        if replaced != text:
            save(path)
            path.write_text(replaced, encoding="utf-8")
    for name, folder in (("_sop_clean_stdout.txt", "outputs/logs/root"),
                         ("_sop_run_output.txt", "outputs/logs/root"),
                         ("daily_signal_output.txt", "outputs/signals/legacy")):
        source = ROOT / name
        if source.exists():
            move(source, ROOT / folder / name)
    finish(journal_path)


def restore(journal_path: Path):
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if Path(journal["root"]).resolve() != ROOT:
        raise ValueError("Journal belongs to another project")
    backup = Path(journal["backup"])
    for item in reversed(journal["operations"]):
        if item["type"] == "junction":
            p = safe(ROOT / item["path"])
            if os.path.isjunction(p):
                os.rmdir(p)  # Removes the link only, never traverses its target.
        elif item["type"] == "move":
            source, dest = safe(ROOT / item["source"]), safe(ROOT / item["destination"])
            if dest.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.rename(dest, source)
        elif item["type"] == "edit":
            dest = safe(ROOT / item["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "originals" / item["path"], dest)
        elif item["type"] == "cache":
            cached = backup / "caches" / item["source"]
            dest = safe(ROOT / item["source"])
            if cached.exists() and not dest.exists():
                os.rename(cached, dest)
    print("Restored original layout; newly authored documentation and indexes remain.")


def finish(journal_path: Path):
    """Complete directory grouping; use reverse aliases for directories held open."""
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if Path(journal["root"]).resolve() != ROOT:
        raise ValueError("Wrong project journal")
    backup = Path(journal["backup"])
    def record():
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
    # The sharing-violation move was recovered before this resumable phase.
    journal["operations"] = [op for op in journal["operations"] if not (
        op["type"] == "move" and op.get("source") == "output"
        and (ROOT / "output").exists() and not os.path.isjunction(ROOT / "output"))]
    for old, new in ALIASES.items():
        source, destination = safe(ROOT / old), safe(ROOT / new)
        if not source.exists() or os.path.isjunction(source) or os.path.isjunction(destination):
            continue
        if destination.exists():
            if any(destination.iterdir()):
                raise FileExistsError(f"Nonempty destination: {destination}")
            os.rmdir(destination)  # Explicitly verified empty; never recursive.
        destination.parent.mkdir(parents=True, exist_ok=True)
        reverse = False
        try:
            os.rename(source, destination)
        except PermissionError:
            reverse = True
        link, target = (destination, source) if reverse else (source, destination)
        command = "New-Item -ItemType Junction -Path '{}' -Target '{}' | Out-Null".format(
            str(link).replace("'", "''"), str(target).replace("'", "''"))
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True)
        except Exception:
            if not reverse:
                os.rename(destination, source)
            raise
        if not reverse:
            journal["operations"].append({"type":"move", "source":old, "destination":new})
        journal["operations"].append({"type":"junction", "path":link.relative_to(ROOT).as_posix(),
                                       "target":target.relative_to(ROOT).as_posix()})
        record()
    for parent, dirs, _ in os.walk(ROOT, followlinks=False):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv")
                   and not os.path.isjunction(Path(parent, d))]
        for name in list(dirs):
            if name not in ("__pycache__", ".pytest_cache"):
                continue
            path = safe(Path(parent, name))
            rel = path.relative_to(ROOT)
            if rel.parts[0] == "apps":
                continue
            dest = backup / "caches" / rel
            if not dest.absolute().is_relative_to(backup.absolute()):
                raise ValueError("Invalid cache backup")
            if dest.exists():
                dirs.remove(name)
                continue  # A new test run may have regenerated this cache.
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(path, dest)
            except PermissionError:
                continue
            journal["operations"].append({"type":"cache", "source":rel.as_posix()})
            dirs.remove(name)
            record()
    journal["state"] = "complete"
    record()
    CATALOG.mkdir(parents=True, exist_ok=True)
    (CATALOG / "organization.json").write_text(json.dumps({
        "schema_version": "project-organization/v1", "journal": str(journal_path),
        "state": "complete", "aliases": ALIASES,
        "operation_counts": dict(Counter(op["type"] for op in journal["operations"]))
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Organization journal: {journal_path}")
    scan()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "apply", "finish", "restore"), default="scan", nargs="?")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    if args.command == "apply":
        organize()
    elif args.command in ("restore", "finish"):
        if not args.journal:
            parser.error("restore requires --journal")
        (restore if args.command == "restore" else finish)(args.journal)
    else:
        scan()


if __name__ == "__main__":
    main()
