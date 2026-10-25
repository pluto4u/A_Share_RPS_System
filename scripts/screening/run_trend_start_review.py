"""右侧趋势启动：选股 → 用 Mistery 技能规则 + 同花顺数据逐只分析。只读，不产生订单，不含买卖建议。

    python scripts/screening/run_trend_start_review.py                  # 通达信最新交易日
    python scripts/screening/run_trend_start_review.py --date 2026-10-23 --top 40

1) 用 trend_start.right_trend 计算你的公式（YJ 预警 / QD 启动 / QR 确认 / QQR 强确认；V2 = 加结构与容量过滤）；
   放量因子只作为标签列出，不作为过滤条件（回测里它们没有带来可证明的改善，见 research/right_trend_volume_20261025）。
2) 同花顺：估值（PE/PB）、近 5 日涨停原因、当前最强 12 个概念的成分股归属。
3) 技能读数（trend_start.skill_reading）：个股分类、趋势判断、520 状态、主力行为；市况来自宏观模块的九宫格。
输出 outputs/trend_start/<日期>/selection.csv 与 report.html。
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import LEGACY_OUTPUT_DIR, OUTPUT_ROOT, PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = PROJECT_ROOT
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))
from macro.sector import load_industry_names  # noqa: E402
from rps_analyzer.industry_rps import load_industry_map  # noqa: E402
from run_daily_rps import names_from_tnf, read_day  # noqa: E402
from trend_start.daily_board import build_recent_panel, industry_of  # noqa: E402
from trend_start.engine import limit_pct  # noqa: E402
from trend_start.right_trend import VF_LABELS, VOLUME_FACTORS, compute  # noqa: E402
from trend_start.skill_reading import Bars, classify_type, judge_trend, read_520, read_smart_money  # noqa: E402
from trend_start.theme_layer import _items, concept_catalog, concept_strength, ensure_concept_closes, ensure_limit_pools, ths  # noqa: E402

THEME_CACHE = ROOT / "data/theme_cache"
STAGES = ("QQR2", "QR2", "QD2", "YJ2")
STAGE_NAME = {"QQR2": "强确认", "QR2": "趋势确认", "QD2": "放量启动", "YJ2": "右侧预警"}
E = html.escape


def stage_of(flags: dict[str, bool]) -> str | None:
    """取最高阶段：强确认 > 趋势确认 > 放量启动 > 右侧预警。"""
    return next((s for s in STAGES if flags.get(s)), None)


def read_out(name: str) -> Path:
    new = OUTPUT_ROOT / name
    return new if new.exists() else LEGACY_OUTPUT_DIR / name


def regime_of(as_of: pd.Timestamp, tdx_root: Path) -> dict:
    tag = f"{as_of:%Y%m%d}"
    if not read_out(f"macro_regime_{tag}.csv").exists():
        print("[REVIEW] macro regime missing -> running it", flush=True)
        subprocess.run([sys.executable, str(script_file("run_macro_regime.py")), "--date", tag, "--tdx-dir", str(tdx_root / "vipdoc"),
                        "--incon", str(tdx_root / "incon.dat"), "--skip-moneyflow", "--skip-geo", "--no-open"], check=True)
    r = pd.read_csv(read_out(f"macro_regime_{tag}.csv")).iloc[-1]
    return {"regime_name": str(r["regime_name"]), "target_position": float(r["target_position"])}


def hot_concepts(as_of: pd.Timestamp, cal: pd.DatetimeIndex, top: int = 12) -> tuple[dict[str, list[str]], list[dict]]:
    """最强 top 个概念（近5日）及其成分股；成分股缓存一周。返回 代码->概念名列表。"""
    closes, _ = ensure_concept_closes(THEME_CACHE, as_of, cal, cal[-1])
    cs = concept_strength(closes, concept_catalog(THEME_CACHE)).head(top)
    folder = THEME_CACHE / "constituents"
    folder.mkdir(parents=True, exist_ok=True)
    member: dict[str, list[str]] = {}
    for r in cs.itertuples():
        fp = folder / f"{r.code}.json"
        if fp.exists() and datetime.now().timestamp() - fp.stat().st_mtime < 7 * 86400:
            items = json.loads(fp.read_text(encoding="utf-8"))
        else:
            items = _items(ths(["index", "constituents", "--thscode", r.code], folder / "_tmp.json"))
            fp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        for it in items:
            member.setdefault(it["thscode"], []).append(r.name)
    return member, cs.to_dict("records")


def valuation(codes: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for k in range(0, len(codes), 100):
        try:
            for it in _items(ths(["valuation", "snapshot", "--thscodes", ",".join(codes[k:k + 100])], THEME_CACHE / "_val.json")):
                out[it["thscode"]] = it
        except RuntimeError as exc:
            print(f"[REVIEW] valuation batch failed: {str(exc)[:120]}")
    return out


def fmt(v, n=1, suffix=""):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{n}f}{suffix}"


def fmt_pe(v, n=0):
    """市盈率为负表示亏损，数值本身没有意义。"""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return "亏损" if v <= 0 else f"{v:.{n}f}"


def dominant_label_note(rows: list[dict], share: float = 0.7) -> str:
    """某个主力行为标签覆盖了绝大多数入选股票时，说明它主要由市况/当日普涨决定，缺乏区分度。"""
    if not rows:
        return ""
    labels = pd.Series([r["money"]["label"].split("：")[0] for r in rows]).value_counts()
    top, n = labels.index[0], int(labels.iloc[0])
    if n / len(rows) >= share and top != "无明显主力行为信号":
        return f"“{top}”覆盖了 {n}/{len(rows)} 只入选股票：它主要由市况（宏观九宫格）和当日普涨决定，在这些股票之间几乎没有区分度，请当作市况提示而不是个股结论。"
    return ""


def build(as_of: pd.Timestamp, tdx_root: Path, top: int) -> dict:
    regime = regime_of(as_of, tdx_root)
    panel, audit = build_recent_panel(tdx_root, as_of, sessions=520)
    names = names_from_tnf(tdx_root / "T0002/hq_cache")
    codes = panel.codes.astype(str)
    st = np.array(["ST" in names.get(c, "").upper() or "退" in names.get(c, "") for c in codes])
    rt = compute(panel, st)
    t = len(panel.dates) - 1
    flags = {"YJ2": rt.yj2[:, t], "QD2": rt.qd2[:, t], "QR2": rt.qr2[:, t], "QQR2": rt.qqr2[:, t]}
    v1 = rt.yj1[:, t] | rt.qd1[:, t] | rt.qr1[:, t] | rt.qqr1[:, t]
    sel_idx = np.flatnonzero(np.any(list(flags.values()), axis=0))
    counts = {s: int(flags[s].sum()) for s in STAGES}
    print(f"[REVIEW] {as_of.date()} 公式V1命中 {int(v1.sum())} 只；V2 阶段计数 {counts}；入选 {len(sel_idx)} 只", flush=True)

    cal = pd.DatetimeIndex(read_day(tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date)
    try:
        member, concepts = hot_concepts(as_of, cal)
    except Exception as exc:  # noqa: BLE001
        member, concepts = {}, []
        print(f"[REVIEW] hot concepts unavailable: {str(exc)[:120]}")
    try:
        pools = ensure_limit_pools(THEME_CACHE, list(cal[cal <= as_of][-5:]))
    except Exception as exc:  # noqa: BLE001
        pools = {}
        print(f"[REVIEW] limit-up pools unavailable: {str(exc)[:120]}")
    reasons: dict[str, list[str]] = {}
    for d, rows in sorted(pools.items()):
        for r in rows:
            reasons.setdefault(r["thscode"], []).append(f"{d[5:]} {r.get('limit_up_reason') or ''}")
    val = valuation([codes[i] for i in sel_idx])
    ind_map = load_industry_map(tdx_root / "T0002/hq_cache/tdxhy.cfg")
    incon = load_industry_names(tdx_root / "incon.dat")
    ind = industry_of(pd.Series(codes[sel_idx]), ind_map)

    rows = []
    c_all, o_all, h_all, l_all, v_all = (panel.field(f).astype(float) for f in ("close", "open", "high", "low", "vol"))
    for k, i in enumerate(sel_idx):
        code = codes[i]
        cols = np.flatnonzero(np.isfinite(c_all[i]))
        cols = cols[cols <= t]
        b = Bars(o_all[i, cols], h_all[i, cols], l_all[i, cols], c_all[i, cols], v_all[i, cols], float(limit_pct(code, np.array([panel.dates[t]]))[0]))
        hot = bool(member.get(code)) or bool(reasons.get(code))
        typ, trend = classify_type(b, hot), judge_trend(b)
        s520, money = read_520(b, trend["direction"]), read_smart_money(b, regime["regime_name"])
        stage = stage_of({s: bool(flags[s][i]) for s in STAGES})
        iname = ind.iloc[k]
        v = val.get(code, {})
        rows.append({"code": code, "name": names.get(code, ""), "stage": stage, "stage_name": STAGE_NAME[stage],
                     "volume_tags": [kf for kf in VOLUME_FACTORS if rt.vf[kf][i, t]],
                     "formula_v1": bool(v1[i]), "industry": incon.get(iname, iname) if isinstance(iname, str) else "",
                     "adv20_yi": float(rt.aux["adv20"][i, t] / 1e8), "close": float(c_all[i, t]), "ret1_pct": b.ret1() * 100,
                     "off_low30_pct": money["off_low30_pct"], "pe_ttm": v.get("pe_ttm"), "pb": v.get("pb_mrq"),
                     "concepts": member.get(code, []), "zt_reasons": reasons.get(code, []),
                     "type": typ, "trend": trend, "s520": s520, "money": money})
    order = {s: n for n, s in enumerate(STAGES)}
    rows.sort(key=lambda r: (order[r["stage"]], -r["adv20_yi"]))
    return {"asof": str(as_of.date()), "regime": regime, "counts": counts, "v1_hits": int(v1.sum()), "rows": rows,
            "concepts": concepts, "audit": audit, "top": top}


def render(r: dict) -> str:
    rows, top = r["rows"], r["top"]
    reg = r["regime"]
    c = r["counts"]
    head = "".join(
        f"<tr><td>{E(x['code'])}</td><td>{E(x['name'])}</td><td>{E(x['stage_name'])}</td><td>{E(x['industry'])}</td>"
        f"<td>{fmt(x['ret1_pct'], 1, '%')}</td><td>{fmt(x['off_low30_pct'], 0, '%')}</td><td>{fmt(x['adv20_yi'], 1)}</td>"
        f"<td>{fmt_pe(x['pe_ttm'])}</td><td>{E(x['type']['type'])}</td>"
        f"<td>{E(x['trend']['direction'])}（{E(x['trend']['confidence'])}）</td><td>{E(x['s520']['state'])}</td>"
        f"<td>{E(x['money']['label'].split('：')[0])}</td><td>{E('、'.join(x['concepts'][:3]) or '—')}</td></tr>" for x in rows)
    cards = ""
    for x in rows[:top]:
        t, s, m, ty = x["trend"], x["s520"], x["money"], x["type"]
        reasons = "；".join(x["zt_reasons"][-2:]) or "近5日无涨停"
        tags = "、".join(VF_LABELS[k].split("：")[0] for k in x["volume_tags"]) or "无"
        cards += f"""<details><summary><b>{E(x['code'])} {E(x['name'])}</b> · {E(x['stage_name'])} · {E(x['industry'])} · {E(t['direction'])}（置信{E(t['confidence'])}）</summary>
<ul><li><b>个股分类</b>：{E(ty['type'])}（情绪票判据命中 {ty['emotion_votes']}/4：{E('、'.join(ty['hit']) or '无')}）；5日均振幅 {fmt(ty['amp5_pct'], 1, '%')}，20日涨停 {ty['zt20']} 次。不可用：{E('、'.join(ty['unavailable']))}</li>
<li><b>趋势判断</b>：{E(t.get('ma_state', ''))}；量价 {E(t.get('volume_price', ''))}；{E(t.get('ma20_state', ''))}（偏离 {fmt(t.get('dev_ma20_pct'), 1, '%')}）；MACD {E(t.get('macd', ''))}。{E(t.get('note', ''))}</li>
<li><b>520</b>：{E(s['state'])}；放量确认 {E(str(s.get('volume_confirm', '—')))}；强度 {E(s.get('strength', '—'))}；参考位 MA20 {fmt(s.get('stop_ref_ma20'), 2)}、近10日低点 {fmt(s.get('stop_ref_low10'), 2)}</li>
<li><b>主力行为</b>（市况：{E(m['regime'])}）：{E(m['label'])}；今日 {fmt(m['ret1_pct'], 1, '%')}，量/20日均 {fmt(m['vol_ratio20'], 2)}，离30日低点 {fmt(m['off_low30_pct'], 0, '%')}</li>
<li><b>同花顺</b>：PE(TTM) {fmt_pe(x['pe_ttm'], 1)}，PB {fmt(x['pb'], 2) if x['pb'] else '—'}；热点概念 {E('、'.join(x['concepts']) or '无')}；涨停原因 {E(reasons)}</li>
<li><b>放量因子标签</b>（仅标注，不是过滤）：{E(tags)}</li></ul></details>"""
    concept_line = "、".join(f"{E(k['name'])}（{k['ret5']:+.1f}%）" for k in r["concepts"][:12]) or "未取得"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>右侧趋势启动复盘</title><style>
body{{margin:0;background:#f2f5f8;color:#23364a;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1300px;margin:auto;padding:20px 16px}}
header{{background:#162f46;color:#fff;border-radius:12px;padding:22px}}section{{background:#fff;border-radius:10px;padding:18px;margin-top:16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid #e0e7ee;white-space:nowrap}}th{{color:#687c8f}}
.s{{overflow-x:auto}}details{{border-bottom:1px solid #e0e7ee;padding:6px 0}}summary{{cursor:pointer}}.note{{background:#fff1dc;padding:10px 14px;border-radius:6px}}
</style></head><body><main><header><div style="color:#d2deea;font-size:13px">截至 {E(r['asof'])} 收盘 · 只读 · 不产生订单</div><h1 style="margin:6px 0">右侧趋势启动：选股 + 技能读数</h1>
<div>市况（宏观九宫格）：{E(reg['regime_name'])}，模型目标仓位 {reg['target_position']:.0%} · 公式 V1 命中 {r['v1_hits']} 只 · V2 阶段：强确认 {c['QQR2']}、趋势确认 {c['QR2']}、放量启动 {c['QD2']}、右侧预警 {c['YJ2']}</div></header>
<section><div class="note">回测提醒（2018–2026，同流动性基准）：这套公式各阶段的信号，平均没有跑赢同流动性股票；在公式预警上加放量因子也没有改善。本页是“用技能规则读一遍”的分析框架，不是推荐名单。技能自述没有严格回测，主力行为判断只作旁证。</div>
<p>当前最强概念（近5日）：{concept_line}</p>{f'<div class="note">{E(dominant_label_note(rows))}</div>' if dominant_label_note(rows) else ''}</section>
<section class="s"><h2>入选一览（按阶段，再按20日均成交额；顺序仅为阅读方便，不代表质量）</h2><table><tr><th>代码</th><th>名称</th><th>阶段</th><th>行业</th><th>今日</th><th>离30日低点</th><th>日均成交(亿)</th><th>PE</th><th>分类</th><th>趋势</th><th>520</th><th>主力行为</th><th>热点概念</th></tr>{head}</table></section>
<section><h2>逐只读数（前 {min(top, len(rows))} 只）</h2>{cards}</section></main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--top", type=int, default=40, help="逐只读数的数量")
    args = ap.parse_args()
    as_of = pd.Timestamp(args.date) if args.date else pd.Timestamp(read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date.iloc[-1])
    res = build(as_of, args.tdx_root, args.top)
    out = OUTPUT_ROOT / "trend_start" / f"{as_of:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    flat = []
    for x in res["rows"]:
        flat.append({"代码": x["code"], "名称": x["name"], "阶段": x["stage_name"], "行业": x["industry"], "今日涨幅%": round(x["ret1_pct"], 2),
                     "离30日低点%": round(x["off_low30_pct"], 1), "日均成交亿": round(x["adv20_yi"], 2), "PE_TTM": x["pe_ttm"] if (x["pe_ttm"] or 0) > 0 else None, "PB": x["pb"],
                     "分类": x["type"]["type"], "趋势": x["trend"]["direction"], "置信": x["trend"]["confidence"], "均线": x["trend"].get("ma_state", ""),
                     "量价": x["trend"].get("volume_price", ""), "520": x["s520"]["state"], "主力行为": x["money"]["label"],
                     "放量因子标签": "|".join(x["volume_tags"]), "热点概念": "|".join(x["concepts"]), "涨停原因": "|".join(x["zt_reasons"][-2:]),
                     "公式V1命中": x["formula_v1"]})
    pd.DataFrame(flat).to_csv(out / "selection.csv", index=False, encoding="utf-8-sig")
    (out / "report.html").write_text(render(res), encoding="utf-8")
    (out / "result.json").write_text(json.dumps({k: res[k] for k in ("asof", "regime", "counts", "v1_hits")}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"asof": res["asof"], "selected": len(res["rows"]), "counts": res["counts"], "report": str(out / "report.html")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
