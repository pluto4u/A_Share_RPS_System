from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "tmp"))
from screen_siruipu_right_side_v2 import add_indicators

TARGET = pd.Timestamp("2026-09-18")
PARQUET = OUT / "tdx_stock_daily_with_rps.parquet"
STOCKS = {
    "688072.SH": "拓荆科技",
    "688630.SH": "芯碁微装",
    "688627.SH": "精智达",
}


def atr20(g: pd.DataFrame) -> pd.Series:
    tr = pd.concat(
        [g.high - g.low, (g.high - g.close.shift()).abs(), (g.low - g.close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(20).mean()


def max_drawdown(close: pd.Series, window: int) -> float:
    s = close.tail(window)
    dd = s / s.cummax() - 1
    return float(dd.min() * 100)


def stage_and_action(m: dict) -> tuple[str, str, str]:
    if m["close"] > m["ma60"] and m["ma20_slope"] > 0:
        if m["ma10"] > m["ma20"]:
            if m["from_ma20_pct"] > 10 or m["ret5_pct"] > 15:
                return "趋势确认后加速", "偏多但过热", "不追；等回踩MA10/MA20或突破后缩量确认"
            return "右侧趋势确认", "偏多", "持有优先；空仓只等回踩或新高确认"
        return "放量启动", "中性偏多", "不追；等MA10上穿MA20或回踩MA60不破"
    if m["close"] > m["ma20"] and m["ma20_slope"] > 0:
        return "右侧修复", "中性偏多", "等待站上MA60或回踩MA20确认"
    if m["close"] > m["ma20"]:
        return "反弹站回MA20", "中性", "均线未转升，不把反弹当主升"
    if m["close"] > m["ma60"]:
        return "高位整理", "中性", "等待重新站上MA20并放量"
    return "弱势整理", "偏弱", "不抄底；等待MA20转升并收复MA60"


def chart(g: pd.DataFrame, name: str) -> str:
    g = g.tail(100).copy().reset_index(drop=True)
    w, h, pad = 980, 350, 45
    ph, vh = 220, 65
    ymin = float(np.nanmin([g.low.min(), g.M20.min(), g.M60.min()]))
    ymax = float(np.nanmax([g.high.max(), g.M20.max(), g.M60.max()]))
    span = max(ymax - ymin, 1)
    xs = np.linspace(pad, w - 18, len(g))

    def y(v: float) -> float:
        return 28 + (ymax - float(v)) / span * ph

    def poly(col: str) -> str:
        return " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, g[col]) if pd.notna(v))

    vmax = float(g.vol.max())
    bars = []
    for x, (_, r) in zip(xs, g.iterrows()):
        color = "#c8373d" if r.close >= r.open else "#06815d"
        bh = float(r.vol) / vmax * vh
        bars.append(f'<rect x="{x-2.6:.1f}" y="{315-bh:.1f}" width="5.2" height="{bh:.1f}" fill="{color}" opacity=".55"/>')
    labels = "".join(
        f'<text x="{xs[i]:.1f}" y="345" text-anchor="middle">{g.at[i,"trade_date"]:%m-%d}</text>'
        for i in [0, len(g)//2, len(g)-1]
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{name}近100个交易日前复权收盘、MA20、MA60和成交量">'
        '<rect width="100%" height="100%" fill="#fff"/>'
        f'<text x="{pad}" y="18" font-weight="700">{name}：近100个交易日（前复权价格）</text>'
        f'<polyline points="{poly("close")}" fill="none" stroke="#17263a" stroke-width="3"/>'
        f'<polyline points="{poly("M20")}" fill="none" stroke="#d04a4f" stroke-width="2"/>'
        f'<polyline points="{poly("M60")}" fill="none" stroke="#2878b5" stroke-width="2"/>'
        f'{"".join(bars)}{labels}'
        f'<text x="{w-230}" y="18" fill="#17263a">收盘</text><text x="{w-155}" y="18" fill="#d04a4f">MA20</text><text x="{w-80}" y="18" fill="#2878b5">MA60</text></svg>'
    )


def main() -> None:
    gate = json.loads((OUT / "qfq_verification.json").read_text(encoding="utf-8"))
    eco = json.loads((OUT / "limit_ecology_latest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUT / "integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"] == "verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"] == "2026-09-18"
    assert eco["as_of_trade_date"] == "2026-09-18"

    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount", "RPS_50", "RPS_120", "RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw.trade_date)
    raw = raw[(raw.ts_code.isin(STOCKS)) & (raw.trade_date <= TARGET)].sort_values(["ts_code", "trade_date"])
    results, charts, groups = [], {}, {}
    for code, name in STOCKS.items():
        g = raw[raw.ts_code == code].copy().reset_index(drop=True)
        assert len(g) >= 260 and g.trade_date.iloc[-1] == TARGET
        z = add_indicators(g)
        z["ATR20"] = atr20(z)
        row, p1 = z.iloc[-1], z.iloc[-2]
        prior5h = float(z.high.iloc[-6:-1].max())
        prior10h = float(z.high.iloc[-11:-1].max())
        prior20h = float(z.high.iloc[-21:-1].max())
        prior60h = float(z.high.iloc[-61:-1].max())
        low10 = float(z.low.tail(10).min())
        low20 = float(z.low.tail(20).min())
        close = float(row.close)
        m = {
            "code": code, "name": name, "date": str(row.trade_date.date()),
            "open": float(row.open), "high": float(row.high), "low": float(row.low), "close": close,
            "ret1_pct": float((close / p1.close - 1) * 100),
            "ret5_pct": float((close / z.close.iloc[-6] - 1) * 100),
            "ret10_pct": float((close / z.close.iloc[-11] - 1) * 100),
            "ret20_pct": float((close / z.close.iloc[-21] - 1) * 100),
            "ma5": float(row.M5), "ma10": float(row.M10), "ma20": float(row.M20),
            "ma60": float(row.M60), "ma120": float(row.M120),
            "ma20_slope": float(row.M20 - p1.M20), "ma60_slope": float(row.M60 - p1.M60),
            "from_ma20_pct": float((close / row.M20 - 1) * 100),
            "from_ma60_pct": float((close / row.M60 - 1) * 100),
            "vol_ratio": float(row.vol / row.VM20), "adv20_yi": float(row.ADV20 / 1e8),
            "atr20": float(row.ATR20), "atr_pct": float(row.ATR20 / close * 100),
            "rps50": float(row.RPS_50), "rps120": float(row.RPS_120), "rps250": float(row.RPS_250),
            "prior5_high": prior5h, "prior10_high": prior10h, "prior20_high": prior20h, "prior60_high": prior60h,
            "low10": low10, "low20": low20,
            "drawdown60_pct": float((close / prior60h - 1) * 100),
            "max_dd60_pct": max_drawdown(z.close, 60),
            "macd_hist": float(row.MH), "macd_rising": bool(row.MH > p1.MH),
            "yj": bool(row.YJ), "qd": bool(row.QD), "qr": bool(row.QR), "qqr": bool(row.QQR),
            "break5": bool(close > prior5h), "break10": bool(close > prior10h), "break20": bool(close > prior20h),
        }
        m["stage"], m["bias"], m["action"] = stage_and_action(m)
        # Next-session confirmation is above the latest day's high. Supports are the nearest rising/long moving averages below price.
        m["confirm"] = float(row.high)
        support_candidates = sorted([float(row.M10), float(row.M20), float(row.M60)], reverse=True)
        support_candidates = [x for x in support_candidates if x < close]
        m["support1"] = support_candidates[0] if support_candidates else low10
        m["support2"] = support_candidates[1] if len(support_candidates) > 1 else low10
        m["hard_fail"] = low20
        # Nearest visible resistance above close from moving average and prior highs.
        candidates = [float(row.M60), prior20h, prior60h]
        above = [x for x in candidates if x > close * 1.01]
        m["resistance"] = min(above) if above else prior60h
        results.append(m)
        charts[code] = chart(z, name)
        groups[code] = z

    # Order by structure, not an additive score.
    stage_order = {"右侧趋势确认": 0, "趋势确认后加速": 1, "放量启动": 2, "右侧修复": 3, "反弹站回MA20": 4, "高位整理": 5, "弱势整理": 6}
    results.sort(key=lambda x: (stage_order[x["stage"]], -x["rps120"]))

    # Resolve current TDX industry grouping and rank for comparison.
    industries = audit["industries"]
    semi = next(x for x in industries if x["name"] == "半导体")
    semi_rank = next(i + 1 for i, x in enumerate(industries) if x["industry"] == semi["industry"])
    market = audit["market"]

    payload = {
        "as_of": "2026-09-18", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "name_mapping": STOCKS, "stocks": results, "industry": {**semi, "rank": semi_rank},
        "market": market, "ecology": {"state": eco.get("cycle_state"), "ST_cycle": eco.get("ST_cycle"), "st_cycle_formal": eco.get("st_cycle_formal")},
        "data_scope": "本地TDX原始价锚；本地v_daily_qfq等比前复权指标；沪深A股，不含北交所",
    }
    (OUT / "a_share_analysis_20260919_688072_688630_688627.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def val(v: float, n=2) -> str:
        return f"{v:.{n}f}"

    rows = "".join(
        f'<tr><td><b>{m["name"]}</b><br><code>{m["code"]}</code></td><td>{m["stage"]}</td><td>{m["close"]:.2f}<br><span class="{("up" if m["ret1_pct"]>=0 else "down")}">{m["ret1_pct"]:+.2f}%</span></td>'
        f'<td>{m["rps50"]:.1f} / {m["rps120"]:.1f} / {m["rps250"]:.1f}</td><td>{m["from_ma20_pct"]:+.1f}% / {m["from_ma60_pct"]:+.1f}%</td><td>{m["vol_ratio"]:.2f}×</td><td>{m["atr_pct"]:.1f}%</td><td>{m["bias"]}</td><td>{m["action"]}</td></tr>'
        for m in results
    )

    cards = []
    for m in results:
        sigs = [k.upper() for k in ("yj", "qd", "qr", "qqr") if m[k]]
        signal = " / ".join(sigs) if sigs else "未触发四阶段公式"
        positives, negatives = [], []
        if m["close"] > m["ma60"]: positives.append("收盘在MA60上方")
        else: negatives.append("仍在MA60下方")
        if m["ma20_slope"] > 0: positives.append("MA20转升")
        else: negatives.append("MA20仍下降")
        if m["macd_hist"] > 0 and m["macd_rising"]: positives.append("MACD红柱增强")
        elif m["macd_hist"] > 0: positives.append("MACD维持红柱")
        else: negatives.append("MACD尚未转强")
        if m["break20"]: positives.append("收盘突破此前20日高")
        elif m["break5"]: positives.append("收盘突破此前5日高")
        else: negatives.append("尚未突破此前5日高")
        if m["from_ma20_pct"] > 10: negatives.append("距MA20超过10%，追高赔率下降")
        if m["vol_ratio"] >= 1.2: positives.append(f'成交量为20日均量{m["vol_ratio"]:.2f}倍')
        else: negatives.append(f'量比仅{m["vol_ratio"]:.2f}，突破确认不足')
        pos_html = "".join(f"<li>{x}</li>" for x in positives) or "<li>没有形成足够的右侧优势</li>"
        neg_html = "".join(f"<li>{x}</li>" for x in negatives) or "<li>主要风险来自科创板20%波动与T+1</li>"
        cards.append(f'''<section id="{m['code'][:6]}"><div class="stock-head"><div><h2>{m['name']} <small>{m['code']}</small></h2><p><b>{m['stage']}｜{m['bias']}</b>｜公式：{signal}</p></div><div class="price">{m['close']:.2f}<span class="{('up' if m['ret1_pct']>=0 else 'down')}">{m['ret1_pct']:+.2f}%</span></div></div>
        <div class="grid"><div class="metric"><span>5/10/20日收益</span><b>{m['ret5_pct']:+.1f}% / {m['ret10_pct']:+.1f}% / {m['ret20_pct']:+.1f}%</b></div><div class="metric"><span>MA20 / MA60</span><b>{m['ma20']:.2f} / {m['ma60']:.2f}</b></div><div class="metric"><span>RPS 50/120/250</span><b>{m['rps50']:.1f} / {m['rps120']:.1f} / {m['rps250']:.1f}</b></div><div class="metric"><span>ATR20 / 量比</span><b>{m['atr_pct']:.1f}% / {m['vol_ratio']:.2f}×</b></div></div>
        {charts[m['code']]}
        <div class="twocol"><div><h3>上涨证据</h3><ul>{pos_html}</ul></div><div><h3>限制与风险</h3><ul>{neg_html}</ul></div></div>
        <div class="levels"><b>确认位：</b>{m['confirm']:.2f}　<b>第一支撑：</b>{m['support1']:.2f}　<b>结构支撑：</b>{m['support2']:.2f}　<b>硬失效：</b>{m['hard_fail']:.2f}　<b>近端压力：</b>{m['resistance']:.2f}</div>
        <p class="plan"><b>动作：</b>{m['action']}。若次日直接高开接近确认位，不把高开本身当确认；科创板T+1下新仓当日无法纠错。</p></section>''')

    by_code = {m["code"]: m for m in results}
    tj, xq, jz = by_code["688072.SH"], by_code["688630.SH"], by_code["688627.SH"]
    # Human-readable relative conclusion; no additive score is used.
    relative = (
        f'拓荆科技动能最强，已放量突破此前20日高；芯碁微装均线结构更完整，但{xq["vol_ratio"]:.2f}倍量比不足且{xq["resistance"]:.2f}元压力很近；'
        f'精智达仍贴近MA60（{jz["ma60"]:.2f}元），ATR达{jz["atr_pct"]:.1f}%，属于高波动修复。三只都不是无条件追价。'
    )
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>拓荆科技、芯碁微装、精智达日K比较</title><style>
    :root{{--navy:#142338;--bg:#f3f6fa;--line:#dfe6ee;--red:#c8373d;--green:#07805d;--amber:#9a5a08;--muted:#657084}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1360px;margin:auto;padding:20px}}header,section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:23px;margin-bottom:16px}}header{{background:var(--navy);color:#fff}}h1{{font-size:29px;margin:4px 0 8px}}h2{{margin:0 0 8px}}h3{{margin:4px 0}}small,.muted{{color:var(--muted)}}header .muted{{color:#cbd5e3}}.badge{{display:inline-block;border:1px solid #7792ad;border-radius:9px;padding:5px 10px;margin:4px 6px 4px 0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#edf2f7}}.up{{color:var(--red)}}.down{{color:var(--green)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.metric{{border:1px solid var(--line);border-radius:12px;padding:12px}}.metric span{{display:block;color:var(--muted)}}.metric b{{font:700 18px ui-monospace,Consolas,monospace}}.stock-head{{display:flex;justify-content:space-between;gap:15px}}.price{{font:700 28px ui-monospace,Consolas,monospace}}.price span{{display:block;font-size:15px;text-align:right}}svg{{display:block;width:100%;height:auto;margin:12px 0}}.twocol{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.levels{{background:#eef3f8;border-radius:11px;padding:13px}}.plan{{border-left:5px solid #d8a03b;background:#fff7e6;padding:12px 15px}}.risk{{color:var(--amber);font-weight:700}}nav a{{color:#d9e9ff;margin-right:15px}}@media(max-width:800px){{main{{padding:9px}}.grid,.twocol{{grid-template-columns:1fr 1fr}}.stock-head{{display:block}}.price span{{display:inline;margin-left:8px}}}}@media(max-width:520px){{.grid,.twocol{{grid-template-columns:1fr}}}}@media print{{body{{background:#fff}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>本地日K比较 · 688072 / 688630 / 688627</div><h1>拓荆动能最强，芯碁结构较完整，精智达仍待确认</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">价格指标：等比前复权</span><span class="badge">置信度：中等</span><span class="badge">当前动作：条件观察</span></p><p>{relative}</p><p class="muted">名称纠正：按“芯碁微装”和“精智达”解析。原始收盘以本地通达信为锚；MA、ATR、RPS使用本地v_daily_qfq。G0为本地运营一致性验证，不是独立外部复权证明。</p><nav><a href="#compare">总表</a><a href="#688072">拓荆科技</a><a href="#688630">芯碁微装</a><a href="#688627">精智达</a></nav></header>
    <section id="compare"><h2>30秒比较</h2><div class="scroll"><table><thead><tr><th>股票</th><th>趋势阶段</th><th>收盘/当日</th><th>RPS 50/120/250</th><th>距MA20/MA60</th><th>量比</th><th>ATR</th><th>倾向</th><th>当前动作</th></tr></thead><tbody>{rows}</tbody></table></div><p>行业环境：半导体在本地行业分组中排名第{semi_rank}，5日中位收益{semi['ret5_pct']:+.2f}%，站上MA20宽度{semi['breadth_ma20_pct']:.1f}%，成交额/20日均额{semi['amount_vs_adv20']:.2f}×。这是共振背景，不替代个股价格确认。</p></section>
    {''.join(cards)}
    <section><h2>统一交易纪律</h2><ol><li><b>空仓：</b>只接受两类触发——缩量回踩MA20/MA10后转强，或收盘突破确认位且次日不快速跌回。三只同属半导体设备链，同时持有不算分散。</li><li><b>已有仓：</b>先看各自MA20；收盘跌破后不能次日收回则降低仓位，跌破“硬失效”退出趋势假设。实际执行还要结合成本价和可卖数量。</li><li><b>风险：</b>三只均为科创板，涨跌幅20%、T+1；有效风险距离至少取计划距离、12%与1×ATR20三者最大。未提供账户权益和成本，因此不输出仓位数字。</li><li><b>机会成本：</b>等待确认可能错过V形上涨的一段，但能避免在均线尚未确认或乖离过大时把反弹误判成趋势。</li></ol></section>
    <section><h2>事实、推断与边界</h2><p><b>事实：</b>OHLC、成交量、均线、RPS与行业宽度均来自本地数据，截至9月18日。</p><p><b>推断：</b>趋势阶段依据MA20斜率、MA10/20/60位置、突破与量比组合判断，没有把各模块相加成总分。趋势结构标签与ZZ1234四阶段公式不是同一个定义；例如芯碁微装虽然均线结构较完整，但尚未突破前高，因此当天未触发YJ/QD/QR/QQR。</p><p><b>交易假设：</b>只有确认位被收盘站稳、或关键均线回踩获得承接，继续上涨的赔率才改善。</p><p>市场情绪状态“{eco.get('cycle_state')}”、ST_cycle={eco.get('ST_cycle')}，st_cycle_formal={str(eco.get('st_cycle_formal')).lower()}，仅作风险上限参考。未使用估算资金流、陈旧概念数据或分钟级CER。覆盖沪深A股，不含北交所。</p></section>
    </main></body></html>'''
    assert "NaN" not in html and "Infinity" not in html and "http://" not in html and "https://" not in html
    report = OUT / "a_share_analysis_20260919_688072_688630_688627_daily_k.html"
    report.write_text(html, encoding="utf-8")
    print(json.dumps({"report": str(report), "stocks": results, "industry_rank": semi_rank}, ensure_ascii=False))


if __name__ == "__main__":
    main()
