import glob
import re

path = sorted(glob.glob("output/a_share_analysis_20260814_*.html"))[-1]
c = open(path, encoding="utf-8").read()
print(f"file: {path}\nsize: {len(c):,} chars")

bad = ["NaN", "Infinity", "undefined", "None", "{{", "}}", "TODO", "TBD", "占位",
       "<script src", 'rel="stylesheet"', "@import", "//cdn", "http://", "https://"]
print("\n--- 禁止出现的内容 ---")
for p in bad:
    n = c.count(p)
    print(f"  {p:22s} {n}{'  <-- 需检查' if n else ''}")

print("\n--- 关键内容存在性 ---")
for p in ["第一主线 · 可执行", "仅观察", "研究观察", "verified_qfq", "跌停不可成交下限",
          "ATR 下限", "已删除的功能", "事实", "推断", "交易假设", "audit-snapshot",
          "风险 ·"]:
    print(f"  {p:22s} {c.count(p)}")

print("\n--- 表格行数 ---")
for name, pat in [("行业行", r'<tr><td>\d+</td><td>'), ("候选行", r"主线 · ")]:
    print(f"  {name}: {len(re.findall(pat, c))}")

m = re.search(r'<script id="audit-snapshot" type="application/json">(.*?)</script>', c, re.S)
print(f"\n内嵌审计 JSON: {'存在' if m else '缺失'}，{len(m.group(1)) if m else 0:,} 字符")
if m:
    import json
    try:
        json.loads(m.group(1).replace("<\\/", "</"))
        print("内嵌 JSON 可解析: 是")
    except ValueError as exc:
        print(f"内嵌 JSON 可解析: 否 -> {exc}")
