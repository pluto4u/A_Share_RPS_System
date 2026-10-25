#!/usr/bin/env python3
"""对思瑞浦右侧名单复算交易所「涨跌幅偏离值」风控，不是官方认定。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "output" / "siruipu_right_side_20260821.csv"
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
IDX_DIR = ROOT / "tmp" / "reg_idx"
OUT_CSV = ROOT / "output" / "siruipu_reg_abnormal_20260821.csv"
OUT_JSON = ROOT / "output" / "siruipu_reg_abnormal_20260821.json"
OUT_HTML = ROOT / "output" / "a_share_analysis_20260821_reg_abnormal.html"
ANCHOR = "688536.SH"


def board_of(code: str) -> dict:
    prefix = code[:3]
    if prefix in ("300", "301"):
        return {"board": "创业板", "d3": 30.0, "times": 3, "index": "399102.SZ", "index_name": "创业板综"}
    if prefix in ("688", "689"):
        return {"board": "科创板", "d3": 30.0, "times": 3, "index": "000688.SH", "index_name": "科创综指"}
    if code.endswith(".SH"):
        return {"board": "沪主板", "d3": 20.0, "times": 4, "index": "000001.SH", "index_name": "上证指数"}
    return {"board": "深主板", "d3": 20.0, "times": 4, "index": "399001.SZ", "index_name": "深证成指"}


def load_index(path: Path) -> pd.Series:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw["data"]["item"]
    df = pd.DataFrame(items)
    df["trade_date"] = (
        pd.to_datetime(df["date_ms"], unit="ms", utc=True)
        .dt.tz_convert("Asia/Shanghai")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    return df.set_index("trade_date")["close_price"].astype(float).sort_index()


def n_ret(s: pd.Series, n: int) -> pd.Series:
    return s / s.shift(n) - 1.0


def flag_row(d3: float, d10: float, d30: float, thr3: float) -> str:
    tags = []
    if d10 >= 100:
        tags.append("触及10日严重异常")
    elif d10 >= 80:
        tags.append("接近10日100%")
    elif d10 >= 60:
        tags.append("观察10日")
    if d30 >= 200:
        tags.append("触及30日严重异常")
    elif d30 >= 160:
        tags.append("接近30日200%")
    elif d30 >= 120:
        tags.append("观察30日")
    if d3 >= thr3:
        tags.append("触及3日异常波动")
    elif d3 >= thr3 * 0.8:
        tags.append("接近3日异常波动")
    return "；".join(tags) if tags else "未接近"


def main() -> int:
    screen = pd.read_csv(SCREEN)
    codes = screen["ts_code"].tolist()
    extra = [ANCHOR] if ANCHOR not in codes else []
    want = codes + extra

    px = pd.read_parquet(PARQUET, columns=["trade_date", "ts_code", "close"])
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    px = px[px["ts_code"].isin(want)].sort_values(["ts_code", "trade_date"])
    asof = px["trade_date"].max()

    idx = {
        "000001.SH": load_index(IDX_DIR / "000001.SH.json"),
        "399001.SZ": load_index(IDX_DIR / "399001.SZ.json"),
        "399102.SZ": load_index(IDX_DIR / "399102.SZ.json"),
        "000688.SH": load_index(IDX_DIR / "000688.SH.json"),
    }
    idx_ret = {k: pd.DataFrame({"close": v}) for k, v in idx.items()}
    for k, df in idx_ret.items():
        df["r3"] = n_ret(df["close"], 3)
        df["r10"] = n_ret(df["close"], 10)
        df["r20"] = n_ret(df["close"], 20)
        df["r30"] = n_ret(df["close"], 30)

    rows = []
    for code, g in px.groupby("ts_code", sort=False):
        meta = board_of(code)
        g = g.set_index("trade_date")["close"].astype(float)
        if asof not in g.index:
            continue
        stock_r = {
            3: n_ret(g, 3).loc[asof],
            10: n_ret(g, 10).loc[asof],
            20: n_ret(g, 20).loc[asof],
            30: n_ret(g, 30).loc[asof],
        }
        ix = idx_ret[meta["index"]]
        if asof not in ix.index:
            continue
        dev = {n: (stock_r[n] - ix.loc[asof, f"r{n}"]) * 100.0 for n in (3, 10, 20, 30)}
        name = ""
        industry = ""
        primary = ""
        if code in set(screen["ts_code"]):
            hit = screen.loc[screen["ts_code"] == code].iloc[0]
            name = str(hit["name"])
            industry = str(hit["industry_name"])
            primary = str(hit["primary"])
        elif code == ANCHOR:
            name = "思瑞浦"
            industry = "半导体"
            primary = "未入选"
        row = {
            "ts_code": code,
            "name": name,
            "industry_name": industry,
            "primary": primary,
            "in_xg": code in set(screen["ts_code"]),
            "board": meta["board"],
            "index_code": meta["index"],
            "index_name": meta["index_name"],
            "thr3": meta["d3"],
            "times_rule": meta["times"],
            "stock_r3": stock_r[3] * 100.0,
            "stock_r10": stock_r[10] * 100.0,
            "stock_r20": stock_r[20] * 100.0,
            "stock_r30": stock_r[30] * 100.0,
            "idx_r3": float(ix.loc[asof, "r3"]) * 100.0,
            "idx_r10": float(ix.loc[asof, "r10"]) * 100.0,
            "idx_r20": float(ix.loc[asof, "r20"]) * 100.0,
            "idx_r30": float(ix.loc[asof, "r30"]) * 100.0,
            "dev3": dev[3],
            "dev10": dev[10],
            "dev20": dev[20],
            "dev30": dev[30],
            "flag": flag_row(dev[3], dev[10], dev[30], meta["d3"]),
        }
        rows.append(row)

    out = pd.DataFrame(rows)
    xg = out[out["in_xg"]].copy()
    xg["_rank"] = np.select(
        [
            xg["dev10"] >= 100,
            xg["dev30"] >= 200,
            xg["dev10"] >= 80,
            xg["dev30"] >= 160,
            xg["dev3"] >= xg["thr3"],
            xg["dev10"] >= 60,
        ],
        [0, 1, 2, 3, 4, 5],
        default=9,
    )
    xg = xg.sort_values(["_rank", "dev10"], ascending=[True, False]).drop(columns="_rank")
    num_cols = [c for c in xg.columns if c.startswith(("stock_r", "idx_r", "dev"))]
    xg[num_cols] = xg[num_cols].astype(float).round(2)
    xg.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    def nflag(mask) -> int:
        return int(mask.sum())

    counts = {
        "xg": int(len(xg)),
        "hit_10": nflag(xg["dev10"] >= 100),
        "near_10": nflag((xg["dev10"] >= 80) & (xg["dev10"] < 100)),
        "watch_10": nflag((xg["dev10"] >= 60) & (xg["dev10"] < 80)),
        "hit_30": nflag(xg["dev30"] >= 200),
        "near_30": nflag((xg["dev30"] >= 160) & (xg["dev30"] < 200)),
        "hit_3": nflag(xg["dev3"] >= xg["thr3"]),
        "any_flag": nflag(xg["flag"] != "未接近"),
        "dev10_median": round(float(xg["dev10"].median()), 2),
        "dev20_median": round(float(xg["dev20"].median()), 2),
        "dev30_median": round(float(xg["dev30"].median()), 2),
    }
    anchor = out[out["ts_code"] == ANCHOR]
    anchor_rec = None if anchor.empty else json.loads(anchor.round(2).iloc[0].to_json(force_ascii=False))
    tz = timezone(timedelta(hours=8))
    meta = {
        "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": asof.strftime("%Y-%m-%d"),
        "formula": "偏离值=(个股N日涨跌幅-对应指数N日涨跌幅)×100",
        "stock_price": "hithink 本地 v_daily_qfq（窗口内除权除息按规则需调整收盘价）",
        "index_price": "hithink index history 未复权收盘",
        "note_20d": "20日不是交易所门槛；官方是10日+100%/-50%与30日+200%/-70%。",
        "not_official": "本地复算，不是交易所公告认定；不重建10日内3次/4次同向异常波动次数。",
        "counts": counts,
        "anchor": anchor_rec,
        "disclaimer": "研究观察，不是买入指令，不构成投资建议。",
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html(meta, xg)
    print(json.dumps({"as_of": meta["as_of"], "counts": counts, "csv": str(OUT_CSV), "html": str(OUT_HTML)}, ensure_ascii=False, indent=2))
    return 0


def write_html(meta: dict, table: pd.DataFrame) -> None:
    def esc(v) -> str:
        return ("" if v is None else str(v)).replace("&", "&amp;").replace("<", "&lt;")

    def num(v, d=1):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "—"
        if not np.isfinite(x):
            return "—"
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.{d}f}"

    c = meta["counts"]
    flagged = table[table["flag"] != "未接近"]
    rows = []
    for _, r in flagged.iterrows():
        rows.append(
            f"<tr><td>{esc(r['ts_code'])}</td><td>{esc(r['name'])}</td>"
            f"<td>{esc(r['board'])}</td><td>{esc(r['primary'])}</td>"
            f"<td>{num(r['dev3'])}</td><td>{num(r['dev10'])}</td>"
            f"<td>{num(r['dev20'])}</td><td>{num(r['dev30'])}</td>"
            f"<td>{esc(r['flag'])}</td></tr>"
        )
    if not rows:
        rows = ["<tr><td colspan='9'>77 只均未接近 3/10/30 日门槛。</td></tr>"]
    top = table.sort_values("dev10", ascending=False).head(15)
    top_rows = []
    for _, r in top.iterrows():
        top_rows.append(
            f"<tr><td>{esc(r['ts_code'])}</td><td>{esc(r['name'])}</td>"
            f"<td>{esc(r['board'])}</td><td>{num(r['stock_r10'])}</td>"
            f"<td>{num(r['dev10'])}</td><td>{num(r['dev20'])}</td>"
            f"<td>{num(r['dev30'])}</td></tr>"
        )
    a = meta.get("anchor") or {}
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>监管偏离值风控 · {esc(meta['as_of'])}</title>
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
<div class="eyebrow">交易所规则复算 · 非官方认定 · {esc(meta['as_of'])}</div>
<h1>右侧名单的 10/20/30 日偏离值风控</h1>
<p>同花顺没有现成「监管10日涨幅」接口。官方门槛是 10 日偏离 +100% / 30 日 +200%，不是 20 日。
本页用本地前复权个股收益减对应指数，对 77 只做观察层，不是买单。</p>
</section>
<div class="grid">
<div class="kv"><small>触及 10 日 100%</small><b>{c['hit_10']}</b></div>
<div class="kv"><small>接近 10 日（80–100）</small><b>{c['near_10']}</b></div>
<div class="kv"><small>触及 3 日异常波动</small><b>{c['hit_3']}</b></div>
<div class="kv"><small>任何警示</small><b>{c['any_flag']}</b></div>
</div>
<article class="card">
<p class="warn"><b>口径：</b>{esc(meta['formula'])}。个股用同花顺本地 v_daily_qfq（规则要求窗口内除权除息调整收盘价）；
指数用同花顺 index history。20 日只作观察，不是监管门槛。未重建「10 日内 3 次/4 次同向异常波动」。
停牌核查以交易所公告为准。G0 未通过。非投资建议。</p>
<p>10 日偏离中位 {c['dev10_median']}% · 20 日中位 {c['dev20_median']}% · 30 日中位 {c['dev30_median']}% ·
触及 30 日 200%：{c['hit_30']} · 接近 30 日：{c['near_30']}</p>
<p><b>模板股思瑞浦：</b>
{'未在指数窗口内' if not a else
 f"{esc(a.get('ts_code'))} 10日偏离 {num(a.get('dev10'))}，20日 {num(a.get('dev20'))}，30日 {num(a.get('dev30'))}，3日 {num(a.get('dev3'))}。{esc(a.get('flag'))}。"}</p>
</article>
<article class="card">
<h2>已接近或触及门槛</h2>
<div style="overflow:auto"><table>
<thead><tr><th>代码</th><th>名称</th><th>板块</th><th>阶段</th>
<th>3日偏离</th><th>10日偏离</th><th>20日偏离</th><th>30日偏离</th><th>标记</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
</article>
<article class="card">
<h2>77 只里 10 日偏离最高的 15 只</h2>
<div style="overflow:auto"><table>
<thead><tr><th>代码</th><th>名称</th><th>板块</th><th>个股10日%</th>
<th>10日偏离</th><th>20日偏离</th><th>30日偏离</th></tr></thead>
<tbody>{''.join(top_rows)}</tbody></table></div>
</article>
<footer>生成 {esc(meta['generated_at'])} · {esc(str(OUT_CSV.as_posix()))} · 非投资建议</footer>
</main></body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
