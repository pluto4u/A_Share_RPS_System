import json

d = json.load(open("output/integrated_decision_20260814_audit.json", encoding="utf-8"))

print("--- meta ---")
for k, v in d["meta"].items():
    print(f"  {k}: {v}")

print("--- risk_state ---")
for k, v in d["risk_state"].items():
    if k != "raw":
        print(f"  {k}: {v}")
print("  raw:", json.dumps(d["risk_state"]["raw"], ensure_ascii=False))

print("--- market ---")
for k, v in d["market"].items():
    print(f"  {k}: {v}")

print("--- top6 industries ---")
for r in d["industries"][:6]:
    print(f"  {r['name']:14s} n={r['members']:3d} ret5={r['ret5_pct']:+6.2f} "
          f"ret20={r['ret20_pct']:+6.2f} breadth={r['breadth_ma20_pct']:5.1f}% "
          f"rps120={r['median_rps120']:5.1f} amt/adv={r['amount_vs_adv20']:.2f} "
          f"res={r['resonance']:.0f} top1={r['top1_amount_share_pct']:.0f}%")

print("--- candidates ---")
for r in d["candidates"]:
    print(f"  {r['ts_code']} {r['name']:9s} {r['industry_name']:10s} "
          f"close={r['close']:8.2f} RPS50={r['RPS_50']:5.1f} RPS120={r['RPS_120']:5.1f} "
          f"RPS250={r['RPS_250']:5.1f} vsMA20={r['vs_ma20_pct']:+5.1f}% "
          f"ATR={r['atr_pct']:4.1f}% adv={r['adv20'] / 1e8:5.1f}yi "
          f"d60h={r['dist_high60_pct']:+6.1f}%")

print("--- sizing ---")
for k, v in (d["decision"]["sizing"] or {}).items():
    print(f"  {k}: {v}")

print("--- plan ---")
for k, v in (d["decision"]["main_plan"] or {}).items():
    print(f"  {k}: {v}")
