from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"output"
sys.path.insert(0,str(ROOT/"tmp"))
from screen_siruipu_right_side_v2 import add_indicators

TARGET=pd.Timestamp("2026-09-18")
PEERS={
    "300661.SZ":"圣邦股份","688536.SH":"思瑞浦","688052.SH":"纳芯微","688798.SH":"艾为电子",
    "688508.SH":"芯朋微","688381.SH":"帝奥微","688484.SH":"南芯科技","688601.SH":"力芯微",
    "688368.SH":"晶丰明源","688141.SH":"杰华特","688209.SH":"英集芯","688045.SH":"必易微",
    "688325.SH":"赛微微电","688173.SH":"希荻微","600171.SH":"上海贝岭","300671.SZ":"富满微",
}


def phase(m:dict)->tuple[str,str]:
    trend=m["close"]>m["ma60"] and m["ma10"]>m["ma20"] and m["ma20_slope"]>0
    if trend and (m["vs_ma20_pct"]>10 or m["ret5_pct"]>15): return "加速高乖离","持仓管理，不追"
    if trend and m["rps120"]>=80: return "成熟上升趋势","趋势领先，等回踩"
    if m["close"]>m["ma20"] and m["ma20_slope"]>0 and m["vs_ma60_pct"]>=-5: return "临界启动","等待突破/回踩确认"
    if m["close"]>m["ma20"] and m["ma20_slope"]>0: return "右侧修复","尚缺MA60确认"
    if m["close"]>m["ma20"]: return "反弹","均线未转升"
    return "弱势整理","不抄底"


def chart(g:pd.DataFrame)->str:
    g=g.tail(110).reset_index(drop=True)
    w,h,p=1040,390,52; ph,vh=250,70
    lo=float(np.nanmin([g.low.min(),g.M20.min(),g.M60.min()])); hi=float(np.nanmax([g.high.max(),g.M20.max(),g.M60.max()])); span=max(hi-lo,1)
    xs=np.linspace(p,w-20,len(g))
    def y(v): return 25+(hi-float(v))/span*ph
    def poly(c): return " ".join(f"{x:.1f},{y(v):.1f}" for x,v in zip(xs,g[c]) if pd.notna(v))
    vmax=float(g.vol.max()); bars=[]
    for x,(_,r) in zip(xs,g.iterrows()):
        bh=float(r.vol)/vmax*vh; col="#c7353b" if r.close>=r.open else "#087e5d"
        bars.append(f'<rect x="{x-2.4:.1f}" y="{355-bh:.1f}" width="4.8" height="{bh:.1f}" fill="{col}" opacity=".55"/>')
    labels="".join(f'<text x="{xs[i]:.1f}" y="385" text-anchor="middle">{g.at[i,"trade_date"]:%m-%d}</text>' for i in [0,len(g)//2,len(g)-1])
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="圣邦股份近110日前复权收盘、MA20、MA60和成交量"><rect width="100%" height="100%" fill="#fff"/><text x="{p}" y="18" font-weight="700">圣邦股份：近110日收盘 / MA20 / MA60 / 成交量</text><polyline points="{poly("close")}" fill="none" stroke="#16253a" stroke-width="3"/><polyline points="{poly("M20")}" fill="none" stroke="#d14b50" stroke-width="2"/><polyline points="{poly("M60")}" fill="none" stroke="#2878b5" stroke-width="2"/>{"".join(bars)}{labels}</svg>'


def main()->None:
    gate=json.loads((OUT/"qfq_verification.json").read_text(encoding="utf-8")); eco=json.loads((OUT/"limit_ecology_latest.json").read_text(encoding="utf-8")); audit=json.loads((OUT/"integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"]=="verified_qfq" and gate["full_market_checks"]["latest_anchor"]["max_date"]=="2026-09-18"
    cols=["trade_date","ts_code","open","high","low","close","vol","amount","RPS_50","RPS_120","RPS_250"]
    raw=pd.read_parquet(OUT/"tdx_stock_daily_with_rps.parquet",columns=cols); raw["trade_date"]=pd.to_datetime(raw.trade_date)
    raw=raw[raw.ts_code.isin(PEERS)&raw.trade_date.le(TARGET)].sort_values(["ts_code","trade_date"])
    data=[]; series={}
    for code,name in PEERS.items():
        g=raw[raw.ts_code.eq(code)].reset_index(drop=True)
        if len(g)<260 or g.trade_date.iloc[-1]!=TARGET: continue
        z=add_indicators(g); tr=pd.concat([z.high-z.low,(z.high-z.close.shift()).abs(),(z.low-z.close.shift()).abs()],axis=1).max(axis=1); z["ATR20X"]=tr.rolling(20).mean()
        r,p=z.iloc[-1],z.iloc[-2]
        m={"code":code,"name":name,"close":float(r.close),"open":float(r.open),"high":float(r.high),"low":float(r.low),
           "ret1_pct":float((r.close/p.close-1)*100),"ret5_pct":float((r.close/z.close.iloc[-6]-1)*100),"ret10_pct":float((r.close/z.close.iloc[-11]-1)*100),"ret20_pct":float((r.close/z.close.iloc[-21]-1)*100),
           "ma5":float(r.M5),"ma10":float(r.M10),"ma20":float(r.M20),"ma60":float(r.M60),"ma120":float(r.M120),"ma20_slope":float(r.M20-p.M20),"ma60_slope":float(r.M60-p.M60),
           "vs_ma20_pct":float((r.close/r.M20-1)*100),"vs_ma60_pct":float((r.close/r.M60-1)*100),"vol_ratio":float(r.vol/r.VM20),"adv20_yi":float(r.ADV20/1e8),"atr_pct":float(r.ATR20X/r.close*100),
           "rps50":float(r.RPS_50),"rps120":float(r.RPS_120),"rps250":float(r.RPS_250),"macd":float(r.MH),"macd_rising":bool(r.MH>p.MH),
           "prior20_high":float(z.high.iloc[-21:-1].max()),"prior60_high":float(z.high.iloc[-61:-1].max()),"low10":float(z.low.tail(10).min()),"low20":float(z.low.tail(20).min()),
           "tdx_yj":bool(r.TDX_YJ),"tdx_qd":bool(r.TDX_QD),"tdx_qr":bool(r.TDX_QR),"tdx_qqr":bool(r.TDX_QQR)}
        m["phase"],m["action"]=phase(m)
        supports=sorted([x for x in [m["ma5"],m["ma10"],m["ma20"],m["ma60"]] if x<m["close"]],reverse=True)
        m["support1"]=supports[0] if supports else m["low10"]; m["support2"]=supports[1] if len(supports)>1 else m["low10"]
        m["confirm"]=m["high"]; m["resistance"]=m["prior60_high"] if m["prior60_high"]>m["close"] else m["high"]
        data.append(m); series[code]=z
    phase_order={"成熟上升趋势":0,"临界启动":1,"右侧修复":2,"加速高乖离":3,"反弹":4,"弱势整理":5}
    ordered=sorted(data,key=lambda x:(phase_order[x["phase"]],-x["rps120"],x["vs_ma20_pct"]))
    sb=next(x for x in data if x["code"]=="300661.SZ")
    above20=sum(x["close"]>x["ma20"] for x in data)/len(data)*100; above60=sum(x["close"]>x["ma60"] for x in data)/len(data)*100
    basket={"members":len(data),"above_ma20_pct":above20,"above_ma60_pct":above60,"median_ret5_pct":float(np.median([x["ret5_pct"] for x in data])),"median_ret20_pct":float(np.median([x["ret20_pct"] for x in data])),"median_rps120":float(np.median([x["rps120"] for x in data]))}
    inds=audit["industries"]; semi=next(x for x in inds if x["name"]=="半导体"); semi_rank=next(i+1 for i,x in enumerate(inds) if x["industry"]==semi["industry"])
    payload={"as_of":"2026-09-18","generated_at":datetime.now().isoformat(timespec="seconds"),"basket_definition":"手工固定的A股模拟/电源管理IC研究篮子，不是本地概念板块","saintbond":sb,"peers":ordered,"basket":basket,"industry":{**semi,"rank":semi_rank},"decision":audit["decision"],"ecology":{"state":eco.get("cycle_state"),"ST_cycle":eco.get("ST_cycle"),"st_cycle_formal":eco.get("st_cycle_formal")}}
    (OUT/"a_share_analysis_20260919_analog_chip.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    rows="".join(f'<tr><td><b>{html.escape(m["name"])}</b><br><code>{m["code"]}</code></td><td>{m["phase"]}</td><td>{m["close"]:.2f}<br><span class="{("up" if m["ret1_pct"]>=0 else "down")}">{m["ret1_pct"]:+.2f}%</span></td><td>{m["rps50"]:.1f}/{m["rps120"]:.1f}/{m["rps250"]:.1f}</td><td>{m["ret5_pct"]:+.1f}%/{m["ret20_pct"]:+.1f}%</td><td>{m["vs_ma20_pct"]:+.1f}%/{m["vs_ma60_pct"]:+.1f}%</td><td>{m["vol_ratio"]:.2f}×</td><td>{m["atr_pct"]:.1f}%</td><td>{m["action"]}</td></tr>' for m in ordered)
    leaders=[m for m in ordered if m["phase"] in ("成熟上升趋势","加速高乖离")][:6]
    early=[m for m in ordered if m["phase"] in ("临界启动","右侧修复")][:6]
    leader_names="、".join(m["name"] for m in leaders) or "无"; early_names="、".join(m["name"] for m in early) or "无"
    svg=chart(series["300661.SZ"])
    reward=(sb["resistance"]/sb["close"]-1)*100; rr=reward/max(12,sb["atr_pct"])
    stage="QQR" if sb["tdx_qqr"] else "QR" if sb["tdx_qr"] else "QD" if sb["tdx_qd"] else "YJ" if sb["tdx_yj"] else "未触发"
    html_doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>圣邦股份与模拟芯片走势</title><style>
    :root{{--navy:#142338;--bg:#f3f6fa;--line:#dce4ed;--red:#c7353b;--green:#087e5d;--amber:#9b5d0b;--muted:#657084}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1450px;margin:auto;padding:20px}}header,section{{background:white;border:1px solid var(--line);border-radius:18px;padding:23px;margin-bottom:16px}}header{{background:var(--navy);color:#fff}}h1{{font-size:29px;margin:4px 0 8px}}h2{{margin:0 0 10px}}.badge{{display:inline-block;border:1px solid #7692ae;border-radius:9px;padding:5px 10px;margin:3px 6px 3px 0}}.muted{{color:var(--muted)}}header .muted{{color:#cbd5e1}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}}.card{{border:1px solid var(--line);border-radius:13px;padding:14px}}.num{{font:700 22px ui-monospace,Consolas,monospace}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#edf2f7}}.up{{color:var(--red)}}.down{{color:var(--green)}}.risk{{color:var(--amber);font-weight:700}}svg{{width:100%;height:auto}}.callout{{background:#fff7e7;border-left:5px solid #d9a43d;padding:13px 16px}}@media(max-width:800px){{main{{padding:9px}}.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:480px){{.grid{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>300661.SZ · 圣邦股份 · 模拟芯片研究篮子</div><h1>圣邦放量加速，板块内部已出现强弱分层</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">圣邦阶段：{stage}/{sb['phase']}</span><span class="badge">半导体行业排名：#{semi_rank}</span><span class="badge">动作：观察，不可执行</span></p><p>圣邦股份完成放量启动并突破20日平台，但5日涨幅{sb['ret5_pct']:+.1f}%、距MA20 {sb['vs_ma20_pct']:+.1f}%，已接近反追高边界。模拟芯片篮子不是全体普涨：站上MA20的比例为{basket['above_ma20_pct']:.1f}%，站上MA60为{basket['above_ma60_pct']:.1f}%。</p><p class="muted">“模拟芯片”是手工固定研究篮子，不是本地概念板块，也不参与正式行业门控。价格锚为本地TDX；指标为本地v_daily_qfq等比前复权。</p></header>
    <section><h2>圣邦股份核心数据</h2><div class="grid"><div class="card"><div>收盘 / 当日</div><div class="num">{sb['close']:.2f} / <span class="up">{sb['ret1_pct']:+.2f}%</span></div></div><div class="card"><div>5日 / 10日</div><div class="num">{sb['ret5_pct']:+.1f}% / {sb['ret10_pct']:+.1f}%</div></div><div class="card"><div>RPS 50/120/250</div><div class="num">{sb['rps50']:.1f}/{sb['rps120']:.1f}/{sb['rps250']:.1f}</div></div><div class="card"><div>量比 / ATR</div><div class="num">{sb['vol_ratio']:.2f}× / {sb['atr_pct']:.1f}%</div></div></div><p>MA20={sb['ma20']:.2f}、MA60={sb['ma60']:.2f}；收盘距MA20 {sb['vs_ma20_pct']:+.2f}%、距MA60 {sb['vs_ma60_pct']:+.2f}%。</p></section>
    <section>{svg}</section>
    <section><h2>圣邦走势结论</h2><ul><li><b>优势：</b>RPS120={sb['rps120']:.1f}、RPS250={sb['rps250']:.1f}，MA20转升，放量{sb['vol_ratio']:.2f}倍突破前20日高{sb['prior20_high']:.2f}元。</li><li><b>风险：</b>5日上涨{sb['ret5_pct']:+.1f}%，距MA20接近12%，量比超过2倍，属于启动后的加速而不是低位确认。</li><li><b>压力：</b>{sb['confirm']:.2f}元是当日高点，{sb['resistance']:.2f}元是60日可见压力；从当前到压力约{reward:+.1f}%，按12%风险下限仅{rr:.2f}R。</li><li><b>支撑：</b>{sb['support1']:.2f}元第一支撑，{sb['support2']:.2f}元第二支撑；MA20 {sb['ma20']:.2f}元为趋势防线。</li></ul><p class="callout"><b>结论：</b>持仓可以跟随，但空仓不追。优先等回踩113–112元区域缩量企稳；若继续突破{sb['confirm']:.2f}元，也需防高开后回落。跌破MA20后不能快速收回，趋势假设降级。</p></section>
    <section><h2>模拟芯片内部结构</h2><p><b>趋势领先/加速：</b>{leader_names}。<br><b>临界启动/右侧修复：</b>{early_names}。</p><div class="scroll"><table><thead><tr><th>股票</th><th>阶段</th><th>收盘/当日</th><th>RPS 50/120/250</th><th>5日/20日</th><th>距MA20/MA60</th><th>量比</th><th>ATR</th><th>动作</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    <section><h2>篮子判断</h2><ul><li>篮子5日收益中位{basket['median_ret5_pct']:+.2f}%、20日收益中位{basket['median_ret20_pct']:+.2f}%，RPS120中位{basket['median_rps120']:.1f}。</li><li>若圣邦、思瑞浦上涨而站上MA20比例不能继续扩大，则属于核心股抱团，不宜追后排。</li><li>若临界启动股开始站上MA60、同时篮子宽度继续提升，才说明模拟芯片从个股行情转为内部扩散。</li><li>半导体正式行业排名第{semi_rank}，5日中位收益{semi['ret5_pct']:+.2f}%、MA20宽度{semi['breadth_ma20_pct']:.1f}%，大行业背景偏强；但当前仍无正式第一行业。</li></ul></section>
    <section><h2>执行边界</h2><p><b>空仓：</b>不追圣邦、思瑞浦等高乖离核心，优先等待缩量回踩或关注临界启动股的收盘确认。<b>已有仓：</b>按MA20/MA60管理，不因同板块补涨同时加仓多只；这些股票高度相关，应视为一笔风险。</p><p>G3没有正式第一行业，G5缺账户权益、现金、持仓成本和风险预算，因此不生成仓位。ST_cycle={eco.get('ST_cycle')}、状态“{eco.get('cycle_state')}”、st_cycle_formal={str(eco.get('st_cycle_formal')).lower()}，仅作风险上限参考。</p><p><b>机会成本：</b>等待确认可能错过一次连续拉升；代价换来的是避免在2倍量能、接近12%乖离时承担T+1隔夜回撤。</p></section>
    <section><h2>审计边界</h2><p>事实：价格、量、RPS、均线与正式半导体行业宽度来自本地数据，截至9月18日。推断：篮子内部领涨、启动、修复标签只用于研究比较。交易假设：回踩承接或板块宽度扩大后，风险收益比才改善。</p><p>未使用估算资金流、陈旧概念板块或分钟级CER。覆盖沪深A股，不含北交所。G0为本地运营一致性验证，不是独立外部复权证明。</p></section>
    </main></body></html>'''
    assert "NaN" not in html_doc and "Infinity" not in html_doc and "http://" not in html_doc and "https://" not in html_doc
    report=OUT/"a_share_analysis_20260919_300661_analog_chip.html"; report.write_text(html_doc,encoding="utf-8")
    print(json.dumps({"report":str(report),"saintbond":sb,"basket":basket,"ordered":ordered},ensure_ascii=False))


if __name__=="__main__": main()
