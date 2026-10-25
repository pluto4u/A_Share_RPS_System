from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "tmp"))
from screen_siruipu_right_side_v2 import add_indicators

TARGET = pd.Timestamp("2026-09-18")
UNIVERSE = {
    "301230.SZ": ("泓博医药", "AI药物发现平台", "CADD/AIDD与DiOrion-GPT，AI关联最直接"),
    "688222.SH": ("成都先导", "AI/DEL药物发现", "DEL海量筛选，可与AI/机器学习结合"),
    "301080.SZ": ("百普赛斯", "生物试剂卖铲子", "重组蛋白、抗体及检测服务，服务靶向药研发"),
    "301047.SZ": ("义翘神州", "生物试剂卖铲子", "蛋白、抗体、基因、培养基及CRO服务"),
    "688137.SH": ("近岸蛋白", "生物试剂卖铲子", "重组蛋白、抗体和酶等研发工具"),
    "688105.SH": ("诺唯赞", "生物试剂平台", "生命科学、体外诊断试剂及相关工具"),
    "688293.SH": ("奥浦迈", "生物工艺基础设施", "细胞培养基与CDMO，偏研发生产下游"),
    "688046.SH": ("药康生物", "临床前模型", "模式动物支持靶点验证和临床前研究"),
    "688265.SH": ("南模生物", "临床前模型", "基因修饰动物模型与技术服务"),
    "300725.SZ": ("药石科技", "分子工具/CRO", "分子砌块和药物发现服务，AI间接受益"),
    "688131.SH": ("皓元医药", "分子工具/CRO", "分子砌块、原料药和研发服务"),
    "603259.SH": ("药明康德", "一体化CRO", "创新药研发生产服务，AI是效率工具而非主业"),
    "300759.SZ": ("康龙化成", "一体化CRO", "药物发现、临床前到临床研发服务"),
    "688202.SH": ("美迪西", "临床前CRO", "药效、药代、安全评价等临床前服务"),
}


def phase(m: dict) -> tuple[str, str]:
    clean = m["close"] > m["ma20"] > m["ma60"] and m["ma20_slope"] > 0 and m["rps120"] >= 80
    near = m["close"] > m["ma20"] and m["close"] > m["ma60"] and m["ma20_slope"] > 0 and m["rps120"] >= 80
    if clean and (m["vs_ma20_pct"] > 12 or m["ret5_pct"] > 20):
        return "强势过热", "只等深回踩，不追"
    if clean and m["vs_ma20_pct"] <= 12 and m["ret5_pct"] <= 20:
        return "均线趋势优先", "等MA10/MA20缩量承接"
    if near and m["vs_ma20_pct"] <= 12:
        return "临界转强", "等MA20/MA60转多确认"
    if m["close"] > m["ma20"] and m["ma20_slope"] > 0:
        return "右侧修复", "需重新站稳MA60"
    if m["close"] > m["ma20"]:
        return "反弹未成趋势", "观察，不做趋势仓"
    return "弱势/破位", "剔除"


def main() -> None:
    gate = json.loads((OUT / "qfq_verification.json").read_text(encoding="utf-8"))
    eco = json.loads((OUT / "limit_ecology_latest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUT / "integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"] == "verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"] == "2026-09-18"

    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount", "RPS_50", "RPS_120", "RPS_250"]
    raw = pd.read_parquet(OUT / "tdx_stock_daily_with_rps.parquet", columns=cols)
    raw["trade_date"] = pd.to_datetime(raw.trade_date)
    raw = raw[raw.ts_code.isin(UNIVERSE) & raw.trade_date.le(TARGET)].sort_values(["ts_code", "trade_date"])
    formal_candidates = {x["ts_code"]: x for x in audit.get("candidates", [])}
    records = []
    for code, (name, layer, relation) in UNIVERSE.items():
        g = raw[raw.ts_code.eq(code)].reset_index(drop=True)
        if len(g) < 260 or g.trade_date.iloc[-1] != TARGET:
            continue
        z = add_indicators(g)
        tr = pd.concat([z.high - z.low, (z.high - z.close.shift()).abs(), (z.low - z.close.shift()).abs()], axis=1).max(axis=1)
        z["ATR20X"] = tr.rolling(20).mean()
        r, p = z.iloc[-1], z.iloc[-2]
        m = {
            "code": code, "name": name, "layer": layer, "relation": relation,
            "close": float(r.close), "ret1_pct": float((r.close / p.close - 1) * 100),
            "ret5_pct": float((r.close / z.close.iloc[-6] - 1) * 100), "ret20_pct": float((r.close / z.close.iloc[-21] - 1) * 100),
            "ma5": float(r.M5), "ma10": float(r.M10), "ma20": float(r.M20), "ma60": float(r.M60),
            "ma20_slope": float(r.M20 - p.M20), "vs_ma20_pct": float((r.close / r.M20 - 1) * 100),
            "vs_ma60_pct": float((r.close / r.M60 - 1) * 100), "vol_ratio": float(r.vol / r.VM20),
            "adv20_yi": float(r.ADV20 / 1e8), "atr_pct": float(r.ATR20X / r.close * 100),
            "rps50": float(r.RPS_50), "rps120": float(r.RPS_120), "rps250": float(r.RPS_250),
            "prior20_high": float(z.high.iloc[-21:-1].max()), "prior60_high": float(z.high.iloc[-61:-1].max()),
            "tdx_yj": bool(r.TDX_YJ), "tdx_qd": bool(r.TDX_QD), "tdx_qr": bool(r.TDX_QR), "tdx_qqr": bool(r.TDX_QQR),
        }
        m["phase"], m["action"] = phase(m)
        if m["ma20"] > m["ma60"]:
            zone = (m["ma10"], m["ma20"])
        elif m["close"] > m["ma60"]:
            zone = (m["ma5"], m["ma60"])
        else:
            zone = (m["ma10"], m["ma20"])
        m["pullback_zone"] = [round(min(zone), 2), round(max(zone), 2)]
        m["confirm"] = round(max(m["prior20_high"], float(r.high)), 2)
        m["hard_line"] = round(m["ma60"], 2)
        m["g2_observation"] = code in formal_candidates
        records.append(m)

    order = {"均线趋势优先": 0, "临界转强": 1, "强势过热": 2, "右侧修复": 3, "反弹未成趋势": 4, "弱势/破位": 5}
    records.sort(key=lambda x: (order[x["phase"]], x["vs_ma20_pct"], -x["rps120"], x["atr_pct"]))
    for x in records:
        if x["phase"] in ("均线趋势优先", "临界转强") and x["adv20_yi"] >= 1.5 and x["rps250"] >= 60:
            x["selection"] = "优先跟踪"
        elif x["phase"] == "强势过热":
            x["selection"] = "高位观察"
        elif x["phase"] in ("均线趋势优先", "临界转强"):
            x["selection"] = "流动性降级"
        else:
            x["selection"] = "暂缓/剔除"

    payload = {"as_of": "2026-09-18", "generated_at": datetime.now().isoformat(timespec="seconds"), "stocks": records,
               "decision": audit["decision"], "risk_state": audit["risk_state"], "market": audit["market"]}
    (OUT / "a_share_analysis_20260919_ai_drug_basket.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for i, x in enumerate(records, 1):
        cls = "up" if x["ret1_pct"] >= 0 else "down"
        rows.append(f"<tr><td>{i}</td><td><b>{html.escape(x['name'])}</b><br><code>{x['code']}</code></td><td>{x['layer']}</td><td><b>{x['selection']}</b><br>{x['phase']}</td><td>{x['close']:.2f}<br><span class='{cls}'>{x['ret1_pct']:+.2f}%</span></td><td>{x['rps50']:.1f}/{x['rps120']:.1f}/{x['rps250']:.1f}</td><td>{x['ma10']:.2f}/{x['ma20']:.2f}/{x['ma60']:.2f}</td><td>{x['vs_ma20_pct']:+.1f}%</td><td>{x['vol_ratio']:.2f}×</td><td>{x['atr_pct']:.1f}%</td><td>{x['pullback_zone'][0]:.2f}–{x['pullback_zone'][1]:.2f}</td><td>{x['confirm']:.2f}</td><td>{x['hard_line']:.2f}</td></tr>")
    top = [x for x in records if x["selection"] in ("优先跟踪", "高位观察")]
    cards = "".join(f"<article><h3>#{i} {x['name']}</h3><p><b>{x['selection']} · {x['phase']}</b></p><p>{x['layer']}：{x['relation']}</p><p>回踩 {x['pullback_zone'][0]:.2f}–{x['pullback_zone'][1]:.2f}<br>确认 {x['confirm']:.2f} · MA60 {x['hard_line']:.2f}</p></article>" for i, x in enumerate(top, 1))
    top_names = "、".join(x["name"] for x in top) or "无"
    direct_names = "、".join(x["name"] for x in records if x["layer"] in ("AI药物发现平台", "AI/DEL药物发现"))
    g2_names = "、".join(x["name"] for x in records if x["g2_observation"]) or "无"

    doc = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>A股AI创新药趋势篮子</title><style>
    :root{{--ink:#172538;--bg:#f3f6f9;--line:#dbe3ec;--red:#c7353b;--green:#087e5d;--amber:#9a6113;--navy:#15263c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.58 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1480px;margin:auto;padding:20px}}header,section{{background:#fff;border:1px solid var(--line);border-radius:17px;padding:22px;margin-bottom:16px}}header{{background:linear-gradient(135deg,#15263c,#365270);color:#fff}}h1{{font-size:clamp(27px,4vw,42px);margin:5px 0}}h2{{margin-top:0}}.badge{{display:inline-block;border:1px solid #7993aa;border-radius:999px;padding:5px 10px;margin:4px}}.decision{{background:#ffe7b4;color:#553700;padding:13px;border-radius:10px;font-weight:750}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}article{{border:1px solid var(--line);border-radius:12px;padding:14px}}article h3{{margin:0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:1550px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}th{{background:#eaf0f5;position:sticky;top:0}}.up{{color:var(--red)}}.down{{color:var(--green)}}.note{{border-left:5px solid var(--amber);padding:12px 15px;background:#fff7e7}}code{{white-space:nowrap}}@media(max-width:1000px){{.cards{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{main{{padding:9px}}.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}article{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>A股 · AI创新药与研发基础设施</div><h1>真正的AI平台不多，更多是创新药“卖铲子”</h1><p><span class='badge'>数据截至 2026-09-18</span><span class='badge'>本地v_daily_qfq前复权</span><span class='badge'>14只手工研究篮子</span><span class='badge'>观察，不可执行</span></p><p>直接AI药物发现代表为{direct_names}；百普赛斯、义翘神州属于重组蛋白/抗体/CRO工具供应商，受益逻辑是实验验证需求增加，而不是拥有AI算法本身。</p><div class='decision'>值得继续跟踪：{top_names}；其中只有皓元医药、药康生物、美迪西属于低于12%乖离的均线趋势组，其余为高位观察。正式G2观察池：{g2_names}；G3无正式第一行业、G5缺账户数据，因此不生成买单或仓位。</div></header>
    <section><h2>产业链定位</h2><p><b>直接AI发现：</b>泓博医药、成都先导。<br><b>生物试剂与实验工具：</b>百普赛斯、义翘神州、近岸蛋白、诺唯赞、奥浦迈、药康生物、南模生物。<br><b>分子工具与CRO：</b>药石科技、皓元医药、药明康德、康龙化成、美迪西。</p><p class='note'>AI可以增加候选分子生成速度，但实验验证、重组蛋白、抗体、动物模型和临床前服务仍是刚需；因此“卖铲子”受益更偏研发投入传导，并不等于短期业绩必然增长。</p></section>
    <section><h2>核心趋势3只＋高位观察5只</h2><div class='cards'>{cards}</div></section>
    <section><h2>14股趋势横向表</h2><div class='scroll'><table><thead><tr><th>序</th><th>股票</th><th>产业链</th><th>甄选/阶段</th><th>收盘/当日</th><th>RPS50/120/250</th><th>MA10/20/60</th><th>距MA20</th><th>量比</th><th>ATR</th><th>回踩区</th><th>确认位</th><th>MA60线</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
    <section><h2>执行边界</h2><p><b>空仓：</b>只等待均线承接或突破后回踩；不因“AI创新药”标签追涨。<b>已有仓：</b>收盘跌破MA20先减，连续两日低于MA20退出；收盘跌破MA60清掉趋势仓。</p><p><b>相关性：</b>试剂、CRO、模式动物都受创新药融资与研发景气影响，多只同时持有并不等于分散。T+1下，当日新仓无法纠错。</p><p><b>机会成本：</b>等待确认可能错过连续反弹，但可避免把行业叙事当作价格确认。</p></section>
    <section><h2>事实、推断与审计</h2><p><b>事实：</b>百普赛斯和义翘神州以生物试剂/CRO为主；泓博医药披露CADD/AIDD与DiOrion-GPT；成都先导以DEL药物发现为核心。<b>推断：</b>AI研发扩张会增加部分工具与验证需求。<b>交易假设：</b>只有RPS与均线结构同步转强，产业逻辑才进入趋势观察。</p><p>价格、成交量、均线、RPS与ATR来自本地数据。G0为本地运营一致性验证，不是独立外部复权证明。未使用估算资金流、陈旧概念板块或分钟级CER。覆盖沪深A股，不含北交所。ST_cycle={eco.get('ST_cycle')}、状态“{eco.get('cycle_state')}”、st_cycle_formal=false，仅作风险上限参考。</p></section>
    </main></body></html>"""
    assert not any(x in doc for x in ("NaN", "Infinity", "PLACEHOLDER", "http://", "https://"))
    report = OUT / "a_share_analysis_20260919_ai_drug_trend.html"
    report.write_text(doc, encoding="utf-8")
    print(json.dumps({"report": str(report), "stocks": records}, ensure_ascii=False))


if __name__ == "__main__":
    main()
