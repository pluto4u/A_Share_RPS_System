import re
from pathlib import Path

root = Path("skills/a-share-rotation-trader")
md = sorted(root.rglob("*.md"))
print(f"{'file':52s} {'chars':>7s}")
for p in md:
    print(f"  {str(p.relative_to(root)):50s} {len(p.read_text(encoding='utf-8')):7,d}")

print("\n--- 内部链接检查 ---")
bad = 0
for p in md:
    for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", p.read_text(encoding="utf-8")):
        if target.startswith(("http", "#")):
            continue
        resolved = (p.parent / target).resolve()
        if not resolved.exists():
            print(f"  BROKEN  {p.relative_to(root)} -> {target}")
            bad += 1
print(f"  断链: {bad}")

print("\n--- 脚本存在性（SKILL.md 注册表）---")
skill = (root / "SKILL.md").read_text(encoding="utf-8")
for name in re.findall(r"`scripts/([\w_]+\.py)`", skill):
    ok = (root / "scripts" / name).exists()
    print(f"  {'OK  ' if ok else 'MISS'} {name}")

print("\n--- 反向检查：scripts 目录中未注册的脚本 ---")
for p in sorted((root / "scripts").glob("*.py")):
    if f"`scripts/{p.name}`" not in skill:
        print(f"  未注册: {p.name}")
