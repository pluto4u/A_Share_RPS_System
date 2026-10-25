"""One-shot: classify root Python entry scripts into scripts/<category>/ and rewrite refs."""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.organize_project import entry_category  # noqa: E402

STAY = {"project_paths.py"}
FOLDER = {
    "daily_workflow": "daily",
    "screening_and_core": "screening",
    "research": "research",
    "validation": "validation",
    "reporting": "reporting",
    "integration": "integration",
}
OVERRIDE = {
    "run_forward_log.py": "daily_workflow",
}
SKIP_REWRITE = (
    "strategy_archive/sources/",
    "tools/_relocate_root_scripts.py",
    ".git/",
    "apps/",
    "outputs/",
    "output/",
)
BOOTSTRAP = """from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()
"""


def classify(name: str) -> str:
    return OVERRIDE.get(name, entry_category(Path(name).stem))


def insert_bootstrap(text: str) -> str:
    if "ensure_script_imports()" in text and "_BOOTSTRAP_ROOT" in text:
        return text
    lines = text.splitlines(keepends=True)
    idx = 0
    if lines and lines[0].startswith("#!"):
        idx = 1
    if idx < len(lines) and re.match(r"^#.*coding[:=]", lines[idx]):
        idx += 1
    # Skip module docstring
    rest = "".join(lines[idx:])
    try:
        tree = ast.parse(text)
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            end = tree.body[0].end_lineno
            idx = end
            while idx < len(lines) and lines[idx].strip() == "":
                idx += 1
    except SyntaxError:
        pass
    if idx < len(lines) and lines[idx].startswith("from __future__ import"):
        idx += 1
        while idx < len(lines) and lines[idx].startswith("from __future__ import"):
            idx += 1
        while idx < len(lines) and lines[idx].strip() == "":
            idx += 1
    block = BOOTSTRAP if BOOTSTRAP.endswith("\n") else BOOTSTRAP + "\n"
    return "".join(lines[:idx]) + block + "\n" + "".join(lines[idx:])


def rewrite_file_paths(text: str) -> str:
    text = text.replace("ROOT = Path(__file__).resolve().parent", "ROOT = PROJECT_ROOT")
    text = text.replace("r=Path(__file__).resolve().parent", "r=PROJECT_ROOT")
    text = text.replace(
        'MON_DIR = Path(__file__).parent / "output" / "monitor"',
        'MON_DIR = PROJECT_ROOT / "output" / "monitor"',
    )
    text = text.replace(
        'MON_CACHE = Path(__file__).parent / "output" / "monitor" / "cache"',
        'MON_CACHE = PROJECT_ROOT / "output" / "monitor" / "cache"',
    )
    text = text.replace(
        'CONFIG_PATH = Path(__file__).resolve().parent / "tos_config.json"',
        'CONFIG_PATH = PROJECT_ROOT / "tos_config.json"',
    )
    text = text.replace(
        'CONFIG_EXAMPLE = Path(__file__).resolve().parent / "tos_config.json.example"',
        'CONFIG_EXAMPLE = PROJECT_ROOT / "tos_config.json.example"',
    )
    text = text.replace(
        "PROJECT_ROOT = Path(__file__).resolve().parent",
        "PROJECT_ROOT = PROJECT_ROOT",
    )
    return text


def mapping() -> dict[str, str]:
    items = {}
    for path in sorted(ROOT.glob("*.py")):
        if path.name in STAY:
            continue
        items[path.name] = FOLDER[classify(path.name)]
    return items


def move_scripts(items: dict[str, str]) -> None:
    for name, folder in items.items():
        dest_dir = ROOT / "scripts" / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "__init__.py").write_text("", encoding="utf-8")
        source = ROOT / name
        dest = dest_dir / name
        if dest.exists():
            raise FileExistsError(dest)
        shutil.move(str(source), str(dest))
        text = dest.read_text(encoding="utf-8-sig")
        text = insert_bootstrap(text)
        text = rewrite_file_paths(text)
        dest.write_text(text, encoding="utf-8")
    (ROOT / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    catalog = {name: f"scripts/{folder}/{name}" for name, folder in items.items()}
    (ROOT / "scripts" / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def patch_references(items: dict[str, str]) -> list[str]:
    names = sorted(items, key=len, reverse=True)
    changed = []
    skip_suffixes = {".parquet", ".png", ".jpg", ".zip", ".duckdb", ".pyc"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in skip_suffixes:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel.startswith(prefix) for prefix in SKIP_REWRITE):
            continue
        if path.suffix.lower() not in {".py", ".md", ".sh", ".bat", ".ps1", ".yml", ".yaml", ".json", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        if path.suffix == ".py":
            for name in names:
                stem_path = f'script_file("{name}")'
                text = text.replace(f'ROOT / "{name}"', stem_path)
                text = text.replace(f"ROOT / '{name}'", stem_path)
                text = text.replace(f'ROOT/"{name}"', stem_path)
                text = text.replace(f"ROOT/'{name}'", stem_path)
            if text != original and "script_file(" in text and "from project_paths import" not in text:
                if "ensure_script_imports()" not in text:
                    text = "from project_paths import script_file\n" + text
        else:
            for name, folder in items.items():
                new = f"scripts/{folder}/{name}"
                text = text.replace(f"python {name}", f"python {new}")
                text = text.replace(f"python3 {name}", f"python3 {new}")
                text = text.replace(f"py -3 {name}", f"py -3 {new}")
                text = text.replace(f"$PYTHON_EXEC {name}", f"$PYTHON_EXEC {new}")
                text = text.replace(f"`{name}`", f"`{new}`")
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(rel)
    return changed


def main() -> None:
    items = mapping()
    move_scripts(items)
    changed = patch_references(items)
    print(json.dumps({"moved": items, "rewritten": changed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
