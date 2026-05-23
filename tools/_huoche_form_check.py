"""对 Huoche 周线持续榜的票跑日线形态核对.

输出:
  - 每只票的 close / MA20 / MA60 / ret20 / vs MA20 / vs MA60
  - 标记 B-1 (回踩 MA20) 候选: abs(close-MA20)/MA20 < 3% AND ret20 < 20% AND close > MA60
  - 标记 B-2 (回踩 MA20 宽松): 3% < abs(close-MA20)/MA20 < 8% AND ret20 < 30%

用法:
  py -3 .\tools\_huoche_form_check.py --date 20260515
"""
import argparse
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rps_analyzer.tdx_reader import load_tdx_market


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="20260515", help="目标日期 YYYYMMDD")
    p.add_argument("--huoche", default=None, help="Huoche codes csv 路径 (默认: output/tdx_screener_<date>_huoche_codes.csv)")
    p.add_argument("--super", default=None, help="Super codes csv 路径 (默认: output/tdx_screener_<date>_super_codes.csv)")
    return p.parse_args()


def main():
    args = parse_args()
    target = pd.Timestamp(args.date)
    huoche_csv = args.huoche or f"output/tdx_screener_{args.date}_huoche_codes.csv"
    super_csv = args.super or f"output/tdx_screener_{args.date}_super_codes.csv"

    if not os.path.exists(huoche_csv):
        print(f"[ERR] Huoche csv 不存在: {huoche_csv}")
        return 1

    huoche_codes = pd.read_csv(huoche_csv, header=None)[0].astype(str).tolist()
    super_codes = []
    if os.path.exists(super_csv):
        super_codes = pd.read_csv(super_csv, header=None)[0].astype(str).tolist()

    print(f"[load] 读 TDX 全市场 ...")
    df = load_tdx_market(r"D:\tdx\vipdoc", min_days=200)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    rows = []
    for code in huoche_codes:
        sub = df[df["ts_code"] == code].sort_values("trade_date").copy()
        if sub.empty or sub["trade_date"].max() < target:
            rows.append({"code": code, "note": "无数据/日期不够"})
            continue
        sub["MA20"] = sub["close"].rolling(20, min_periods=20).mean()
        sub["MA60"] = sub["close"].rolling(60, min_periods=60).mean()
        sub["ret20"] = sub["close"] / sub["close"].shift(20) - 1
        today = sub[sub["trade_date"] == target]
        if today.empty:
            rows.append({"code": code, "note": "目标日无行情"})
            continue
        r = today.iloc[-1]
        close = r["close"]; ma20 = r["MA20"]; ma60 = r["MA60"]; ret20 = r["ret20"]
        vs20 = close / ma20 - 1 if ma20 == ma20 else None
        vs60 = close / ma60 - 1 if ma60 == ma60 else None
        in_super = code in super_codes

        group = "?"
        flag = ""
        if in_super:
            group = "A"
        else:
            abs_vs20 = abs(vs20) if vs20 is not None else 99
            if (abs_vs20 < 0.03 and ret20 < 0.20 and close > ma60 and vs60 < 0.35):
                group = "B-1"
                flag = "** VCP 回踩 MA20 **"
            elif (abs_vs20 < 0.08 and ret20 < 0.30 and close > ma60 and vs60 < 0.40):
                group = "B-2"
                flag = "B-2 接近 MA20"
            elif close > ma60 and vs60 < 0.50:
                group = "B-3"
                flag = "B-3 偏离 MA20"
            else:
                group = "B-X"
                flag = "B-X 形态超阈"

        rows.append({
            "code": code, "close": close, "MA20": ma20, "MA60": ma60,
            "vs20": vs20, "vs60": vs60, "ret20": ret20,
            "in_super": in_super, "group": group, "flag": flag,
        })

    out = pd.DataFrame(rows)
    out = out.sort_values(["group", "vs20"], ascending=[True, True], na_position="last")
    print(f"\n=== Huoche {len(huoche_codes)} 只形态核对 (目标日 {args.date}) ===\n")
    print(f"{'group':<5}{'code':<13}{'close':>9}{'MA20':>9}{'MA60':>9}{'vs20%':>8}{'vs60%':>8}{'ret20%':>8}  flag")
    for _, r in out.iterrows():
        if pd.isna(r.get("close", None)):
            print(f"{r['group']:<5}{r['code']:<13}{'-':>9}{'-':>9}{'-':>9}{'-':>8}{'-':>8}{'-':>8}  {r.get('flag', r.get('note', ''))}")
            continue
        print(f"{r['group']:<5}{r['code']:<13}{r['close']:>9.2f}{r['MA20']:>9.2f}{r['MA60']:>9.2f}"
              f"{r['vs20']*100:>7.1f}%{r['vs60']*100:>7.1f}%{r['ret20']*100:>7.1f}%  {r['flag']}")

    print("\n=== 分组统计 ===")
    print(out.groupby("group").size().to_string())

    out_csv = f"output/huoche_form_{args.date}.csv"
    out.to_csv(out_csv, index=False)
    print(f"\n详细结果: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
