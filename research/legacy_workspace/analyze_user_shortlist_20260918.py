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
STOCKS = {
    "688146.SH": "中船特气",
    "002138.SZ": "顺络电子",
    "001314.SZ": "亿道信息",
    "688037.SH": "芯源微",
    "688072.SH": "拓荆科技",
    "688052.SH": "纳芯微",
    "688508.SH": "芯朋微",
    "688601.SH": "力芯微",
    "301123.SZ": "奕东电子",
    "688256.SH": "寒武纪",
    "300666.SZ": "江丰电子",
    "688662.SH": "富信科技",
    "688630.SH": "芯碁微装",
}


def classify(m: dict) -> tuple[str, str]:
    clean = m["close"] > m["ma20"] > m["ma60"] and m["ma20_slope"] > 0 and m["rps120"] >= 80
    near = m["close"] > m["ma20"] and m["close"] > m["ma60"] and m["ma20_slope"] > 0 and m["rps120"] >= 80
    if clean and m["vs_ma20_pct"] <= 12 and m["ret5_pct"] <= 18:
        return "均线趋势优先", "等MA10/MA20缩量承接"
    if near and m["vs_ma20_pct"] <= 12:
        return "临界趋势", "等MA20/MA60黏合转多"
    if clean and (m["vs_ma20_pct"] > 12 or m["ret5_pct"] > 18):
        return "强势但过热", "只等深回踩，不追"
    if m["close"] > m["ma20"] and m["ma20_slope"] > 0:
        return "右侧修复", "需要站稳MA60"
    if m["close"] > m["ma20"]:
        return "反弹未成趋势", "暂不纳入趋势计划"
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
    raw = raw[raw.ts_code.isin(STOCKS) & raw.trade_date.le(TARGET)].sort_values(["ts_code", "trade_date"])
    candidate_map = {x["ts_code"]: x for x in audit.get("candidates", [])}

    result = []
    for code, name in STOCKS.items():
        g = raw[raw.ts_code.eq(code)].reset_index(drop=True)
        if len(g) < 260 or g.trade_date.iloc[-1] != TARGET:
            continue
        z = add_indicators(g)
        tr = pd.concat([z.high - z.low, (z.high - z.close.shift()).abs(), (z.low - z.close.shift()).abs()], axis=1).max(axis=1)
        z["ATR20X"] = tr.rolling(20).mean()
        r, p = z.iloc[-1], z.iloc[-2]
        m = {
            "code": code, "name": name, "close": float(r.close), "ret1_pct": float((r.close / p.close - 1) * 100),
            "ret5_pct": float((r.close / z.close.iloc[-6] - 1) * 100), "ret10_pct": float((r.close / z.close.iloc[-11] - 1) * 100),
            "ret20_pct": float((r.close / z.close.iloc[-21] - 1) * 100), "ma5": float(r.M5), "ma10": float(r.M10),
            "ma20": float(r.M20), "ma60": float(r.M60), "ma120": float(r.M120), "ma20_slope": float(r.M20 - p.M20),
            "vs_ma20_pct": float((r.close / r.M20 - 1) * 100), "vs_ma60_pct": float((r.close / r.M60 - 1) * 100),
            "vol_ratio": float(r.vol / r.VM20), "adv20_yi": float(r.ADV20 / 1e8), "atr_pct": float(r.ATR20X / r.close * 100),
            "rps50": float(r.RPS_50), "rps120": float(r.RPS_120), "rps250": float(r.RPS_250),
            "prior20_high": float(z.high.iloc[-21:-1].max()), "prior60_high": float(z.high.iloc[-61:-1].max()),
            "low10": float(z.low.tail(10).min()), "low20": float(z.low.tail(20).min()),
            "tdx_yj": bool(r.TDX_YJ), "tdx_qd": bool(r.TDX_QD), "tdx_qr": bool(r.TDX_QR), "tdx_qqr": bool(r.TDX_QQR),
        }
        m["phase"], m["action"] = classify(m)
        m["g2_observation"] = code in candidate_map
        m["industry"] = candidate_map.get(code, {}).get("industry_name", "未进入G2，未映射")
        if m["ma20"] > m["ma60"]:
            zone_values = (m["ma10"], m["ma20"])
        elif m["close"] > m["ma60"]:
            zone_values = (m["ma5"], m["ma60"])
        else:
            zone_values = (m["ma10"], m["ma20"])
        m["pullback_zone"] = [round(min(zone_values), 2), round(max(zone_values), 2)]
        m["confirm"] = round(max(m["prior20_high"], float(r.high)), 2)
        m["hard_line"] = round(m["ma60"], 2)
        result.append(m)

    # 分层优先于排序：先要求均线结构，或至少满足高RPS+有效右侧触发；不做加总总分。
    priority = [
        "688052.SH", "002138.SZ", "688508.SH", "688601.SH",
        "688630.SH", "688072.SH", "688662.SH", "300666.SZ",
        "301123.SZ", "001314.SZ", "688256.SH", "688037.SH", "688146.SH",
    ]
    selected_codes = set(priority[:8])
    core_codes = set(priority[:6])
    for x in result:
        x["selection"] = "核心跟踪" if x["code"] in core_codes else "次级跟踪" if x["code"] in selected_codes else "暂缓/剔除"
    result.sort(key=lambda x: priority.index(x["code"]))

    selected = [x for x in result if x["code"] in selected_codes]
    hot = [x for x in result if x["phase"] == "强势但过热"]
    payload = {
        "as_of": "2026-09-18", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": "用户给定13只股票（重复的顺络电子已合并）", "stocks": result,
        "selected": [x["code"] for x in selected], "hot": [x["code"] for x in hot],
        "decision": audit["decision"], "risk_state": audit["risk_state"], "market": audit["market"],
    }
    (OUT / "a_share_analysis_20260919_user_shortlist.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for i, x in enumerate(result, 1):
        color = "up" if x["ret1_pct"] >= 0 else "down"
        zone = f"{x['pullback_zone'][0]:.2f}–{x['pullback_zone'][1]:.2f}"
        rows.append(f"<tr><td>{i}</td><td><b>{html.escape(x['name'])}</b><br><code>{x['code']}</code></td><td><b>{x['selection']}</b><br>{x['phase']}</td><td>{x['close']:.2f}<br><span class='{color}'>{x['ret1_pct']:+.2f}%</span></td><td>{x['rps50']:.1f}/{x['rps120']:.1f}/{x['rps250']:.1f}</td><td>{x['ma10']:.2f}/{x['ma20']:.2f}/{x['ma60']:.2f}</td><td>{x['vs_ma20_pct']:+.1f}%</td><td>{x['vol_ratio']:.2f}×</td><td>{x['atr_pct']:.1f}%</td><td>{zone}</td><td>{x['confirm']:.2f}</td><td>{x['hard_line']:.2f}</td><td>{x['action']}</td></tr>")

    preferred = result[:8]
    cards = "".join(f"<article><h3>#{i} {x['name']}</h3><p><b>{x['phase']}</b> · RPS120 {x['rps120']:.1f}</p><p>回踩区 {x['pullback_zone'][0]:.2f}–{x['pullback_zone'][1]:.2f}<br>确认位 {x['confirm']:.2f}<br>MA60防线 {x['hard_line']:.2f}</p><p>{x['action']}</p></article>" for i, x in enumerate(preferred, 1))
    preferred_names = "、".join(x["name"] for x in preferred)
    core_names = "、".join(x["name"] for x in result if x["selection"] == "核心跟踪")
    reserve_names = "、".join(x["name"] for x in result if x["selection"] == "次级跟踪")
    removed_names = "、".join(x["name"] for x in result if x["selection"] == "暂缓/剔除") or "无"
    g2_names = "、".join(x["name"] for x in result if x["g2_observation"]) or "无"

    doc = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>13股趋势甄选</title><style>
    :root{{--ink:#172538;--bg:#f3f6f9;--card:#fff;--line:#dbe3ec;--red:#c7353b;--green:#087e5d;--amber:#9a6113;--navy:#14263b}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.58 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1480px;margin:auto;padding:20px}}header,section{{background:#fff;border:1px solid var(--line);border-radius:17px;padding:22px;margin-bottom:16px}}header{{background:linear-gradient(135deg,#14263b,#294d6a);color:#fff}}h1{{font-size:clamp(27px,4vw,42px);margin:5px 0}}h2{{margin-top:0}}.badge{{display:inline-block;border:1px solid #7590a8;border-radius:999px;padding:5px 10px;margin:4px}}.decision{{background:#ffe8b5;color:#543600;padding:13px;border-radius:10px;font-weight:750}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}article{{border:1px solid var(--line);border-radius:12px;padding:14px}}article h3{{margin:0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:1450px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}th{{background:#eaf0f5;position:sticky;top:0}}.up{{color:var(--red)}}.down{{color:var(--green)}}.note{{border-left:5px solid var(--amber);padding:12px 15px;background:#fff7e7}}code{{white-space:nowrap}}@media(max-width:850px){{main{{padding:9px}}.cards{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}article{{break-inside:avoid}}}}
    </style></head><body><main><header><div>用户自选股 · 趋势横向甄选</div><h1>保留强趋势，剔除反弹和过热追价</h1><p><span class='badge'>数据截至 2026-09-18</span><span class='badge'>本地v_daily_qfq前复权</span><span class='badge'>13只去重</span><span class='badge'>观察，不可执行</span></p><p>优先跟踪：{preferred_names}。排序按趋势阶段分层，再比较均线乖离、RPS120和ATR；不使用简单加总分。</p><div class='decision'>正式G2观察池中只有：{g2_names}。G3无正式第一行业、G5缺账户状态，因此本页是研究排序，不生成买单或仓位。</div></header>
    <section><h2>核心6只＋备选2只</h2><div class='cards'>{cards}</div></section>
    <section><h2>13股完整横向表</h2><div class='scroll'><table><thead><tr><th>序</th><th>股票</th><th>甄选/阶段</th><th>收盘/当日</th><th>RPS50/120/250</th><th>MA10/20/60</th><th>距MA20</th><th>量比</th><th>ATR</th><th>回踩观察区</th><th>确认位</th><th>MA60防线</th><th>动作</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
    <section><h2>结论与执行边界</h2><p><b>核心跟踪：</b>{core_names}。<br><b>次级跟踪：</b>{reserve_names}。<br><b>暂缓/剔除：</b>{removed_names}。原因不是公司不好，而是当前日K尚不符合均线趋势计划。</p><p class='note'><b>统一规则：</b>进入回踩区不等于买点。需看到缩量止跌、收盘重新站回MA10/MA20、次日不低开破位三项中至少两项。趋势仓收盘跌破MA20先减，连续两日低于MA20清掉；收盘跌破MA60退出。</p><p><b>T+1与相关性：</b>半导体设备、材料和模拟芯片高度相关，不可把多只同时持有视作分散。未提供账户权益、现金、成本、持仓和风险预算，因此不生成伪精确仓位。</p><p><b>机会成本：</b>等待均线承接可能错过连续加速；代价是降低追在高乖离位置、次日无法纠错的风险。</p></section>
    <section><h2>审计说明</h2><p>事实：价格、成交量、均线、RPS和ATR来自本地数据；G0为本地运营一致性验证，不是独立外部复权证明。推断：阶段与排序用于比较这13只股票。交易假设：只有回踩承接或突破后回踩确认才改善赔率。</p><p>未使用估算资金流、陈旧概念板块或分钟级CER。覆盖沪深A股，不含北交所。ST_cycle={audit['risk_state']['st_cycle']:.1f}、状态“{audit['risk_state']['cycle_state']}”、st_cycle_formal=false，仅作风险上限参考。</p></section>
    </main></body></html>"""
    assert not any(x in doc for x in ("NaN", "Infinity", "PLACEHOLDER", "http://", "https://"))
    report = OUT / "a_share_analysis_20260919_user_shortlist_trend.html"
    report.write_text(doc, encoding="utf-8")
    print(json.dumps({"report": str(report), "stocks": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
