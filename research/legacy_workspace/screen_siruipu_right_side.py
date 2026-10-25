#!/usr/bin/env python3
"""思瑞浦同形右侧趋势发现器：通达信公式 → 同花顺前复权日线。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "a-share-rotation-trader" / "scripts"))
from build_limit_ecology import load_name_master


def load_industry_map(cfg_path: Path) -> pd.DataFrame:
    market_map = {"0": "SZ", "1": "SH", "2": "BJ"}
    records = []
    with cfg_path.open("r", encoding="gbk", errors="ignore") as fh:
        for line in fh:
            parts = line.strip().split("|")
            if len(parts) < 3 or not parts[2]:
                continue
            market = market_map.get(parts[0])
            if market in (None, "BJ"):
                continue
            records.append({"ts_code": f"{parts[1]}.{market}", "industry": parts[2]})
    return pd.DataFrame(records).drop_duplicates("ts_code")


def load_industry_names(incon: Path) -> dict[str, str]:
    if not incon.exists():
        return {}
    mapping: dict[str, str] = {}
    with incon.open("r", encoding="gbk", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].startswith("T"):
                mapping[parts[0]] = parts[1]
    return mapping


def industry_label(code: str, names: dict[str, str]) -> str:
    for width in (5, 4, 3, 2):
        hit = names.get(code[:width])
        if hit:
            return hit
    return code

PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
OUT_CSV = ROOT / "output" / "siruipu_right_side_20260821.csv"
OUT_JSON = ROOT / "output" / "siruipu_right_side_20260821.json"
OUT_HTML = ROOT / "output" / "a_share_analysis_20260821_siruipu_right_side.html"
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
ANCHOR = "688536.SH"


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def llvbars(values: np.ndarray, n: int = 30) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        out[i] = int(np.argmin(window[::-1]))
    return out


def stage_one(g: pd.DataFrame) -> pd.DataFrame:
    c = g["close"]
    h = g["high"]
    l = g["low"]
    v = g["vol"]
    g = g.copy()
    g["M5"] = c.rolling(5, min_periods=5).mean()
    g["M10"] = c.rolling(10, min_periods=10).mean()
    g["M20"] = c.rolling(20, min_periods=20).mean()
    g["M60"] = c.rolling(60, min_periods=60).mean()
    g["M120"] = c.rolling(120, min_periods=120).mean()
    g["VM20"] = v.rolling(20, min_periods=20).mean()
    dif = ema(c, 12) - ema(c, 26)
    dea = ema(dif, 9)
    g["DIF"] = dif
    g["DEA"] = dea
    g["MH"] = 2 * (dif - dea)
    hhv60 = h.rolling(60, min_periods=60).max()
    llv30 = l.rolling(30, min_periods=30).min()
    g["DD"] = (hhv60 - llv30) / hhv60 >= 0.28
    g["LOWNEAR"] = llvbars(l.to_numpy(dtype=float), 30) <= 15
    g["hhv5_ref"] = h.rolling(5, min_periods=5).max().shift(1)
    g["hhv10_ref"] = h.rolling(10, min_periods=10).max().shift(1)
    g["hhv20_ref"] = h.rolling(20, min_periods=20).max().shift(1)
    g["M5_ref2"] = g["M5"].shift(2)
    g["MH_ref1"] = g["MH"].shift(1)
    return g


def main() -> int:
    names = load_name_master(HQ)
    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    print("[1] 读取同花顺前复权日线")
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
            "RPS_50", "RPS_120", "RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    asof = raw["trade_date"].max()
    bars_n = raw.groupby("ts_code", sort=False).size()
    work = raw.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False).tail(320)
    print(f"    asof={asof.date()} stocks={work['ts_code'].nunique()} rows={len(work)}")

    print("[2] 计算公式字段")
    parts = [stage_one(g) for _, g in work.groupby("ts_code", sort=False)]
    df = pd.concat(parts, ignore_index=True)
    df["name"] = df["ts_code"].map(names).fillna("")
    compact = df["name"].str.replace(" ", "", regex=False).str.upper()
    is_st = compact.str.startswith(("ST", "*ST")) | compact.str.contains("退", regex=False)
    df["BASE"] = (df["ts_code"].map(bars_n) > 250) & (~is_st)

    yj = (
        df["DD"] & df["LOWNEAR"]
        & (df["close"] > df["M20"])
        & (df["M5"] > df["M10"])
        & (df["M5"] > df["M5_ref2"])
        & (df["close"] > df["hhv5_ref"])
        & (df["MH"] > 0)
        & (df["MH"] > df["MH_ref1"])
        & (df["close"] / df["M20"] <= 1.12)
    )
    qd = (
        df["DD"] & df["LOWNEAR"]
        & (df["close"] > df["M20"])
        & (df["M5"] > df["M10"])
        & (df["close"] > df["M120"])
        & (df["close"] > df["hhv10_ref"])
        & (df["vol"] >= df["VM20"] * 1.10)
        & (df["MH"] > 0)
    )
    qr = (
        df["DD"]
        & (df["close"] > df["M60"])
        & (df["M10"] > df["M20"])
        & (df["close"] > df["hhv20_ref"])
        & (df["MH"] > 0)
    )
    qqr = qr & (df["vol"] >= df["VM20"] * 1.80) & (df["DIF"] > 0)
    df["YJ"] = yj.fillna(False)
    df["QD"] = qd.fillna(False)
    df["QR"] = qr.fillna(False)
    df["QQR"] = qqr.fillna(False)
    df["XG"] = df["BASE"] & (df["YJ"] | df["QD"] | df["QR"] | df["QQR"])

    day = df[(df["trade_date"] == asof) & df["XG"]].copy()

    def stages(row) -> str:
        tags = [k for k in ("YJ", "QD", "QR", "QQR") if row[k]]
        return "+".join(tags)

    day["stages"] = day.apply(stages, axis=1)
    priority = np.select(
        [day["QQR"], day["QR"], day["QD"], day["YJ"]],
        ["QQR", "QR", "QD", "YJ"],
        default="",
    )
    day["primary"] = priority
    day["vs_ma20_pct"] = (day["close"] / day["M20"] - 1.0) * 100.0
    day["vol_ratio"] = day["vol"] / day["VM20"]
    day = day.merge(imap, on="ts_code", how="left")
    day["industry_name"] = [
        industry_label(str(c), inames) if pd.notna(c) else ""
        for c in day["industry"]
    ]
    order = {"QQR": 0, "QR": 1, "QD": 2, "YJ": 3}
    day["_ord"] = day["primary"].map(order)
    day = day.sort_values(["_ord", "RPS_120"], ascending=[True, False])

    cols_out = [
        "ts_code", "name", "industry_name", "primary", "stages", "close",
        "RPS_50", "RPS_120", "RPS_250", "vs_ma20_pct", "vol_ratio",
        "M20", "M60", "M120", "MH", "DIF",
    ]
    out = day[cols_out].copy()
    for c in ["close", "M20", "M60", "M120", "MH", "DIF", "vs_ma20_pct", "vol_ratio",
              "RPS_50", "RPS_120", "RPS_250"]:
        out[c] = out[c].astype(float).round(4)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    counts = {k: int(day[k].sum()) for k in ("YJ", "QD", "QR", "QQR")}
    counts["XG"] = int(len(day))
    primary_counts = day["primary"].value_counts().to_dict()
    industry_top = (
        day.groupby("industry_name", dropna=False)
        .size().sort_values(ascending=False).head(12)
    )
    asof_all = df[df["trade_date"] == asof]
    anchor_row = asof_all[asof_all["ts_code"] == ANCHOR]
    anchor = None
    if not anchor_row.empty:
        r = anchor_row.iloc[0]
        anchor = {
            "ts_code": ANCHOR,
            "name": str(r.get("name") or ""),
            "close": None if pd.isna(r["close"]) else round(float(r["close"]), 4),
            "XG": bool(r["XG"]),
            "YJ": bool(r["YJ"]),
            "QD": bool(r["QD"]),
            "QR": bool(r["QR"]),
            "QQR": bool(r["QQR"]),
            "BASE": bool(r["BASE"]),
            "DD": bool(r["DD"]) if pd.notna(r["DD"]) else False,
            "LOWNEAR": bool(r["LOWNEAR"]) if pd.notna(r["LOWNEAR"]) else False,
            "RPS_120": None if pd.isna(r["RPS_120"]) else round(float(r["RPS_120"]), 2),
            "RPS_250": None if pd.isna(r["RPS_250"]) else round(float(r["RPS_250"]), 2),
        }
    tz = timezone(timedelta(hours=8))
    meta = {
        "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": asof.strftime("%Y-%m-%d"),
        "formula": "思瑞浦同形右侧趋势发现器 ZZ1234",
        "price": "hithink v_daily_qfq / output/tdx_stock_daily_with_rps.parquet",
        "adjust": "forward",
        "universe": "沪深A股，排除 ST/*ST/退，BARSCOUNT>250",
        "counts": counts,
        "primary_counts": {k: int(v) for k, v in primary_counts.items()},
        "industry_top": {str(k): int(v) for k, v in industry_top.items()},
        "anchor": anchor,
        "csv": str(OUT_CSV.as_posix()),
        "disclaimer": "条件选股结果，不是买入指令，不构成投资建议。",
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html(meta, out)
    print(json.dumps({
        "as_of": meta["as_of"],
        "counts": counts,
        "primary": meta["primary_counts"],
        "csv": str(OUT_CSV),
        "html": str(OUT_HTML),
    }, ensure_ascii=False, indent=2))
    return 0


def write_html(meta: dict, table: pd.DataFrame) -> None:
    def esc(v) -> str:
        return ("" if v is None else str(v)).replace("&", "&amp;").replace("<", "&lt;")

    def num(v, d=2):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "—"
        if not np.isfinite(x):
            return "—"
        return f"{x:,.{d}f}"

    label = {"YJ": "右侧预警", "QD": "放量启动", "QR": "趋势确认", "QQR": "强确认"}
    rows = []
    for _, r in table.iterrows():
        rows.append(
            f"<tr><td>{esc(r['ts_code'])}</td><td>{esc(r['name'])}</td>"
            f"<td>{esc(r.get('industry_name',''))}</td>"
            f"<td>{esc(r['primary'])} {esc(label.get(r['primary'],''))}</td>"
            f"<td>{esc(r['stages'])}</td><td>{num(r['close'])}</td>"
            f"<td>{num(r['RPS_120'],1)}</td><td>{num(r['RPS_250'],1)}</td>"
            f"<td>{num(r['vs_ma20_pct'])}</td><td>{num(r['vol_ratio'])}</td></tr>"
        )
    pc = meta["primary_counts"]
    cards = "".join(
        f"<div class='kv'><small>{k} {label.get(k,k)}</small><b>{pc.get(k,0)}</b></div>"
        for k in ("YJ", "QD", "QR", "QQR")
    )
    anchor = meta.get("anchor") or {}
    if anchor:
        hit = "命中" if anchor.get("XG") else "未命中"
        flags = "/".join(k for k in ("YJ", "QD", "QR", "QQR") if anchor.get(k)) or "无阶段"
        anchor_html = (
            f"<p><b>模板股 {esc(anchor.get('ts_code'))} {esc(anchor.get('name'))}：</b>"
            f"{hit} · {esc(flags)} · 收盘 {num(anchor.get('close'))} · "
            f"RPS120 {num(anchor.get('RPS_120'),1)} · RPS250 {num(anchor.get('RPS_250'),1)}。"
            f"DD={'是' if anchor.get('DD') else '否'} · "
            f"LOWNEAR={'是' if anchor.get('LOWNEAR') else '否'}。</p>"
        )
    else:
        anchor_html = "<p>模板股 688536.SH 思瑞浦不在当日截面。</p>"
    ind = meta.get("industry_top") or {}
    ind_html = "".join(
        f"<span style='display:inline-block;margin:0 10px 6px 0'>{esc(k)} {v}</span>"
        for k, v in ind.items()
    )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>思瑞浦同形右侧 · {esc(meta['as_of'])}</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
.wrap{{max-width:1200px;margin:auto;padding:18px}}
.hero{{background:#06182a;color:#fff;border-radius:16px;padding:24px}}
.eyebrow{{color:#9ecbeb;font-size:12px;font-weight:700;letter-spacing:.08em}}
h1{{margin:8px 0 10px;font-size:26px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}}
.kv{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:22px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:14px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left}}
thead th{{background:#f6f8fa;color:var(--muted)}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:12px;border-radius:8px}}
footer{{color:var(--muted);font-size:12px;padding:14px 4px}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main class="wrap">
<section class="hero">
<div class="eyebrow">通达信公式 ZZ1234 · 同花顺前复权 · {esc(meta['as_of'])}</div>
<h1>思瑞浦同形右侧趋势发现器</h1>
<p>选中 {meta['counts']['XG']} 只。BASE：上市&gt;250 日且非 ST。XG = YJ 或 QD 或 QR 或 QQR。条件选股，不是买单。</p>
</section>
<div class="grid">{cards}</div>
<article class="card">
<p class="warn"><b>口径：</b>价格与均线来自同花顺 v_daily_qfq（前复权）；成交量用原始量。
EMA 按通达信 span=N。最新交易日 {esc(meta['as_of'])}（今日周日）。RPS 仅作并列观察，不参与本公式。
本页不构成投资建议。</p>
{anchor_html}
<p><b>行业分布（前 12）：</b> {ind_html}</p>
<div style="overflow:auto;max-height:640px"><table>
<thead><tr><th>代码</th><th>名称</th><th>行业</th><th>主阶段</th><th>命中</th><th>收盘</th>
<th>RPS120</th><th>RPS250</th><th>vs MA20%</th><th>量比</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
</article>
<footer>生成 {esc(meta['generated_at'])} · {esc(meta['csv'])} · 非投资建议</footer>
</main></body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
