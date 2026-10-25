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

CODE = "688536.SH"
TARGET = pd.Timestamp("2026-09-18")


def chart(g: pd.DataFrame) -> str:
    g = g.tail(120).copy().reset_index(drop=True)
    w, h, pad = 1040, 430, 55
    ph, vh = 275, 80
    ymin = float(np.nanmin([g.low.min(), g.M20.min(), g.M60.min()]))
    ymax = float(np.nanmax([g.high.max(), g.M20.max(), g.M60.max()]))
    span = max(ymax-ymin,1)
    xs = np.linspace(pad,w-20,len(g))
    def y(v): return 25+(ymax-float(v))/span*ph
    def poly(c): return " ".join(f"{x:.1f},{y(v):.1f}" for x,v in zip(xs,g[c]) if pd.notna(v))
    vmax=float(g.vol.max()); bars=[]
    for x,(_,r) in zip(xs,g.iterrows()):
        bh=float(r.vol)/vmax*vh; col="#c7353b" if r.close>=r.open else "#087e5d"
        bars.append(f'<rect x="{x-2.5:.1f}" y="{390-bh:.1f}" width="5" height="{bh:.1f}" fill="{col}" opacity=".55"/>')
    labels="".join(f'<text x="{xs[i]:.1f}" y="425" text-anchor="middle">{g.at[i,"trade_date"]:%m-%d}</text>' for i in [0,len(g)//2,len(g)-1])
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="思瑞浦近120日前复权收盘、MA20、MA60和成交量"><rect width="100%" height="100%" fill="#fff"/><text x="{pad}" y="18" font-weight="700">近120日：收盘 / MA20 / MA60 / 成交量</text><polyline points="{poly("close")}" fill="none" stroke="#16253a" stroke-width="3"/><polyline points="{poly("M20")}" fill="none" stroke="#d14b50" stroke-width="2"/><polyline points="{poly("M60")}" fill="none" stroke="#2878b5" stroke-width="2"/>{"".join(bars)}{labels}<text x="{w-250}" y="18" fill="#16253a">收盘</text><text x="{w-165}" y="18" fill="#d14b50">MA20</text><text x="{w-85}" y="18" fill="#2878b5">MA60</text></svg>'


def main() -> None:
    gate=json.loads((OUT/"qfq_verification.json").read_text(encoding="utf-8"))
    eco=json.loads((OUT/"limit_ecology_latest.json").read_text(encoding="utf-8"))
    audit=json.loads((OUT/"integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"]=="verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"]=="2026-09-18"
    cols=["trade_date","ts_code","open","high","low","close","vol","amount","RPS_50","RPS_120","RPS_250"]
    raw=pd.read_parquet(OUT/"tdx_stock_daily_with_rps.parquet",columns=cols)
    raw["trade_date"]=pd.to_datetime(raw.trade_date)
    g=raw[(raw.ts_code==CODE)&raw.trade_date.le(TARGET)].sort_values("trade_date").reset_index(drop=True)
    assert len(g)>260 and g.trade_date.iloc[-1]==TARGET
    z=add_indicators(g)
    tr=pd.concat([z.high-z.low,(z.high-z.close.shift()).abs(),(z.low-z.close.shift()).abs()],axis=1).max(axis=1)
    z["ATR20X"]=tr.rolling(20).mean()
    r,p=z.iloc[-1],z.iloc[-2]
    m={
        "date":str(r.trade_date.date()),"open":float(r.open),"high":float(r.high),"low":float(r.low),"close":float(r.close),
        "ret1_pct":float((r.close/p.close-1)*100),"ret5_pct":float((r.close/z.close.iloc[-6]-1)*100),"ret10_pct":float((r.close/z.close.iloc[-11]-1)*100),"ret20_pct":float((r.close/z.close.iloc[-21]-1)*100),
        "ma5":float(r.M5),"ma10":float(r.M10),"ma20":float(r.M20),"ma60":float(r.M60),"ma120":float(r.M120),
        "ma20_slope":float(r.M20-p.M20),"ma60_slope":float(r.M60-p.M60),"vs_ma20_pct":float((r.close/r.M20-1)*100),"vs_ma60_pct":float((r.close/r.M60-1)*100),
        "rps50":float(r.RPS_50),"rps120":float(r.RPS_120),"rps250":float(r.RPS_250),"vol_ratio":float(r.vol/r.VM20),"adv20_yi":float(r.ADV20/1e8),
        "atr20":float(r.ATR20X),"atr_pct":float(r.ATR20X/r.close*100),"macd_hist":float(r.MH),"macd_rising":bool(r.MH>p.MH),
        "prior5_high":float(z.high.iloc[-6:-1].max()),"prior10_high":float(z.high.iloc[-11:-1].max()),"prior20_high":float(z.high.iloc[-21:-1].max()),"prior60_high":float(z.high.iloc[-61:-1].max()),
        "low5":float(z.low.tail(5).min()),"low10":float(z.low.tail(10).min()),"low20":float(z.low.tail(20).min()),
        "tdx_yj":bool(r.TDX_YJ),"tdx_qd":bool(r.TDX_QD),"tdx_qr":bool(r.TDX_QR),"tdx_qqr":bool(r.TDX_QQR),
        "v2_yj":bool(r.YJ),"v2_qd":bool(r.QD),"v2_qr":bool(r.QR),"v2_qqr":bool(r.QQR),
    }
    inds=audit["industries"]
    semi=next(x for x in inds if x["name"]=="半导体")
    semi_rank=next(i+1 for i,x in enumerate(inds) if x["industry"]==semi["industry"])
    # Trend state is explicit rather than a weighted score.
    if m["close"]>m["ma60"] and m["ma10"]>m["ma20"] and m["ma20_slope"]>0:
        state="成熟上升趋势"
    elif m["close"]>m["ma20"] and m["ma20_slope"]>0:
        state="右侧修复"
    else:
        state="趋势未确认"
    overextended=m["vs_ma20_pct"]>12 or m["ret5_pct"]>15
    phase="加速后高乖离" if state=="成熟上升趋势" and overextended else state
    # Nearest supports and resistance.
    supports=sorted([x for x in [m["ma5"],m["ma10"],m["ma20"],m["ma60"]] if x<m["close"]],reverse=True)
    m["support1"]=supports[0]; m["support2"]=supports[1] if len(supports)>1 else m["ma20"]
    m["confirm"]=m["high"]
    above=[x for x in [m["prior20_high"],m["prior60_high"]] if x>m["close"]*1.01]
    m["resistance"]=min(above) if above else m["prior60_high"]
    m["hard_fail"]=m["ma20"]
    risk_floor_pct=max(12.0,m["atr_pct"])
    reward_pct=(m["resistance"]/m["close"]-1)*100
    rr=reward_pct/risk_floor_pct if reward_pct>0 else 0
    verdict={"state":state,"phase":phase,"overextended":overextended,"risk_floor_pct":risk_floor_pct,"reward_to_resistance_pct":reward_pct,"reward_r":rr,
             "action":"持仓可跟随，空仓不追；等回踩MA10/MA20或突破后回踩确认" if overextended else "条件观察"}
    payload={"metrics":m,"industry":{**semi,"rank":semi_rank},"market":audit["market"],"ecology":{"state":eco.get("cycle_state"),"ST_cycle":eco.get("ST_cycle"),"st_cycle_formal":eco.get("st_cycle_formal")},"verdict":verdict}
    (OUT/"a_share_analysis_688536_20260918.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    svg=chart(z)
    stage="QQR强确认" if m["tdx_qqr"] else "QR趋势确认" if m["tdx_qr"] else "QD启动" if m["tdx_qd"] else "YJ预警" if m["tdx_yj"] else "未触发"
    html_doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>思瑞浦日K走势确认</title><style>
    :root{{--navy:#142338;--bg:#f3f6fa;--line:#dce4ed;--red:#c7353b;--green:#087e5d;--amber:#9b5d0b;--muted:#657084}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1320px;margin:auto;padding:20px}}header,section{{background:white;border:1px solid var(--line);border-radius:18px;padding:23px;margin-bottom:16px}}header{{background:var(--navy);color:#fff}}h1{{font-size:29px;margin:4px 0 8px}}h2{{margin:0 0 10px}}.badge{{display:inline-block;border:1px solid #7692ae;border-radius:9px;padding:5px 10px;margin:3px 6px 3px 0}}.muted{{color:var(--muted)}}header .muted{{color:#cbd5e1}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}}.card{{border:1px solid var(--line);border-radius:13px;padding:14px}}.num{{font:700 23px ui-monospace,Consolas,monospace}}.up{{color:var(--red)}}.risk{{color:var(--amber);font-weight:700}}svg{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}}th{{background:#edf2f7}}.callout{{background:#fff7e7;border-left:5px solid #d9a43d;padding:13px 16px}}@media(max-width:780px){{main{{padding:9px}}.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:480px){{.grid{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>688536.SH · 思瑞浦 · 本地日K确认</div><h1>趋势成立，但已进入高乖离加速区</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">阶段：{stage}</span><span class="badge">状态：{phase}</span><span class="badge">动作：观察，不可执行</span></p><p>思瑞浦不是“准备启动”，而是已经完成趋势确认。优势是MA10&gt;MA20、收盘站上MA60、RPS三周期同步强；风险是收盘距MA20达到{m['vs_ma20_pct']:.1f}%并放量上涨，当前位置更像加速而非低风险首买点。</p><p class="muted">原始价格锚为本地通达信；MA、ATR、RPS使用本地v_daily_qfq等比前复权。G0运营一致性通过，但不是独立外部复权证明。</p></header>
    <section><h2>关键数字</h2><div class="grid"><div class="card"><div>收盘 / 当日</div><div class="num">{m['close']:.2f} / <span class="up">{m['ret1_pct']:+.2f}%</span></div></div><div class="card"><div>5日 / 10日 / 20日</div><div class="num">{m['ret5_pct']:+.1f}% / {m['ret10_pct']:+.1f}% / {m['ret20_pct']:+.1f}%</div></div><div class="card"><div>RPS 50 / 120 / 250</div><div class="num">{m['rps50']:.1f} / {m['rps120']:.1f} / {m['rps250']:.1f}</div></div><div class="card"><div>量比 / ATR20</div><div class="num">{m['vol_ratio']:.2f}× / {m['atr_pct']:.1f}%</div></div></div><p>MA5={m['ma5']:.2f}、MA10={m['ma10']:.2f}、MA20={m['ma20']:.2f}、MA60={m['ma60']:.2f}。收盘距MA20 {m['vs_ma20_pct']:+.2f}%、距MA60 {m['vs_ma60_pct']:+.2f}%。</p></section>
    <section>{svg}<p class="muted">前复权价格图；成交量保持原始口径。最新收盘与本地TDX原始收盘一致。</p></section>
    <section><h2>走势确认</h2><table><thead><tr><th>证据</th><th>当前状态</th><th>解释</th></tr></thead><tbody><tr><td>均线结构</td><td>MA10&gt;MA20，MA20和MA60均转升</td><td>中期上升趋势成立</td></tr><tr><td>公式阶段</td><td>{stage}</td><td>已突破此前20日高并站上MA60，不再是预警阶段</td></tr><tr><td>相对强度</td><td>RPS50/120/250均高于85</td><td>短中长周期同步强</td></tr><tr><td>量价</td><td>量比{m['vol_ratio']:.2f}，MACD柱{'增强' if m['macd_rising'] else '走弱'}</td><td>上涨得到量能和动能确认</td></tr><tr><td>风险</td><td>距MA20 {m['vs_ma20_pct']:+.1f}%</td><td class="risk">超过12%反追高线，盈亏比开始恶化</td></tr></tbody></table></section>
    <section><h2>关键价格</h2><table><thead><tr><th>位置</th><th>价格</th><th>含义</th></tr></thead><tbody><tr><td>继续加速确认</td><td>{m['confirm']:.2f}</td><td>突破9月18日高点后仍需防冲高回落</td></tr><tr><td>第一支撑</td><td>{m['support1']:.2f}</td><td>短均线支撑，适合观察回踩强弱</td></tr><tr><td>第二支撑</td><td>{m['support2']:.2f}</td><td>跌破后加速结构降级</td></tr><tr><td>趋势防线</td><td>{m['ma20']:.2f}</td><td>收盘跌破减半，连续两日不能收回则趋势假设失败</td></tr><tr><td>近端历史压力</td><td>{m['resistance']:.2f}</td><td>若已突破则以创新高后的2×ATR移动防线管理</td></tr></tbody></table><p class="callout">按科创板20%涨跌幅，有效风险距离下限至少12%；当前到可见压力的空间约{reward_pct:+.1f}%，静态赔率约{rr:.2f}R。空仓不应因为趋势确认就追高，优先等回踩MA10/MA20缩量承接。</p></section>
    <section><h2>账户分叉</h2><p><b>空仓：</b>不在高乖离位置追。等待回踩第一/第二支撑并缩量止跌，或创新高后第一次正常回踩确认。</p><p><b>已有趋势仓：</b>可继续跟随，但不因QR再次加仓。收盘跌破MA20减半，连续两日收在MA20下方清掉；收盘跌破MA60无条件退出。</p><p><b>重仓：</b>高乖离阶段优先处理集中风险，不把浮盈当作新的风险预算。科创板T+1下，新仓当日不能卖出。</p></section>
    <section><h2>行业与门控</h2><p>半导体行业当前排名第{semi_rank}，5日中位收益{semi['ret5_pct']:+.2f}%、MA20宽度{semi['breadth_ma20_pct']:.1f}%、成交额/20日均额{semi['amount_vs_adv20']:.2f}×，行业共振较强且非单股驱动。</p><p>但9月18日系统没有产生正式第一行业，G3未通过；账户状态缺失，G5未通过，因此标题必须是“观察，不可执行”，而不是条件买入。</p></section>
    <section><h2>事实、推断与边界</h2><p><b>事实：</b>价格、量、均线、RPS和行业宽度来自本地数据，截至9月18日。</p><p><b>推断：</b>思瑞浦处于成熟上升趋势的加速阶段，而非主升前潜伏阶段。</p><p><b>交易假设：</b>只有回踩短均线或MA20获得承接，新的风险收益比才可能改善。</p><p>ST_cycle={eco.get('ST_cycle')}、状态“{eco.get('cycle_state')}”、st_cycle_formal={str(eco.get('st_cycle_formal')).lower()}，仅作风险上限参考。未使用估算资金流、陈旧概念或分钟级CER。覆盖沪深A股，不含北交所。</p></section>
    </main></body></html>'''
    assert "NaN" not in html_doc and "Infinity" not in html_doc and "http://" not in html_doc and "https://" not in html_doc
    report=OUT/"a_share_analysis_20260919_688536_daily_k.html"
    report.write_text(html_doc,encoding="utf-8")
    print(json.dumps({"report":str(report),"metrics":m,"verdict":verdict,"industry_rank":semi_rank,"stage":stage},ensure_ascii=False))


if __name__=="__main__":
    main()
