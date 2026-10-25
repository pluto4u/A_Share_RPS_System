from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "tmp"))
from screen_siruipu_right_side_v2 import add_indicators

CODE = "688146.SH"
TARGET = pd.Timestamp("2026-09-18")
PARQUET = OUT / "tdx_stock_daily_with_rps.parquet"


def wilson(wins: int, total: int) -> tuple[float, float]:
    if not total:
        return math.nan, math.nan
    z = 1.96
    p = wins / total
    den = 1 + z * z / total
    mid = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return (mid - half) * 100, (mid + half) * 100


def collect_events(calc: pd.DataFrame, mask_col: str, start: str = "2022-01-01") -> pd.DataFrame:
    rows = []
    for code, g in calc.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date").reset_index(drop=True)
        idxs = np.flatnonzero(g[mask_col].fillna(False).to_numpy(bool))
        last = -999
        for i in idxs:
            if g.at[i, "trade_date"] < pd.Timestamp(start) or i - last < 20 or i + 20 >= len(g):
                continue
            last = i
            entry = float(g.at[i + 1, "open"])
            if not np.isfinite(entry) or entry <= 0:
                continue
            item = {"ts_code": code, "signal_date": g.at[i, "trade_date"], "entry": entry}
            for h in (1, 5, 10, 20):
                item[f"ret{h}"] = (float(g.at[i + h, "close"]) / entry - 1) * 100
                win = g.iloc[i + 1:i + h + 1]
                item[f"mfe{h}"] = (float(win.high.max()) / entry - 1) * 100
                item[f"mae{h}"] = (float(win.low.min()) / entry - 1) * 100
            rows.append(item)
    return pd.DataFrame(rows)


def summary(events: pd.DataFrame) -> dict:
    result = {"events": int(len(events)), "stocks": int(events.ts_code.nunique()) if len(events) else 0}
    for h in (1, 5, 10, 20):
        s = events[f"ret{h}"]
        wins = int((s > 0).sum())
        lo, hi = wilson(wins, len(s))
        result[str(h)] = {
            "win_rate_pct": round(wins / len(s) * 100, 1),
            "win_rate_ci95_pct": [round(lo, 1), round(hi, 1)],
            "median_pct": round(float(s.median()), 2),
            "mean_pct": round(float(s.mean()), 2),
            "p_gain5_pct": round(float((s >= 5).mean() * 100), 1),
            "p_loss5_pct": round(float((s <= -5).mean() * 100), 1),
            "mfe_median_pct": round(float(events[f"mfe{h}"].median()), 2),
            "mae_median_pct": round(float(events[f"mae{h}"].median()), 2),
        }
    return result


def svg_chart(g: pd.DataFrame) -> str:
    g = g.tail(80).copy().reset_index(drop=True)
    w, h, pad = 1040, 430, 55
    vol_h = 90
    price_h = h - vol_h - 55
    ymin = float(min(g.low.min(), g.M20.min(), g.M60.min()))
    ymax = float(max(g.high.max(), g.M20.max(), g.M60.max()))
    span = max(ymax - ymin, 1)
    xs = np.linspace(pad, w - 20, len(g))
    def y(v): return 25 + (ymax - float(v)) / span * price_h
    def poly(col):
        pts = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, g[col]) if pd.notna(v))
        return pts
    bars=[]
    maxv=float(g.vol.max())
    for x,(_,r) in zip(xs,g.iterrows()):
        col="#c9343a" if r.close>=r.open else "#0b8f68"
        yy=25+price_h+25
        vh=float(r.vol)/maxv*vol_h
        bars.append(f'<rect x="{x-3:.1f}" y="{yy+vol_h-vh:.1f}" width="6" height="{vh:.1f}" fill="{col}" opacity=".65"/>')
    dates=[0,len(g)//2,len(g)-1]
    labels="".join(f'<text x="{xs[i]:.1f}" y="{h-3}" text-anchor="middle">{g.at[i,"trade_date"]:%m-%d}</text>' for i in dates)
    return f'''<svg viewBox="0 0 {w} {h}" role="img" aria-label="中船特气近80个交易日前复权收盘、MA20、MA60及成交量"><rect width="100%" height="100%" fill="#fff"/><text x="{pad}" y="18" font-weight="700">近80个交易日：前复权收盘 / MA20 / MA60，成交量</text><polyline points="{poly('close')}" fill="none" stroke="#142338" stroke-width="3"/><polyline points="{poly('M20')}" fill="none" stroke="#d5534f" stroke-width="2"/><polyline points="{poly('M60')}" fill="none" stroke="#2878b5" stroke-width="2"/><line x1="{pad}" y1="{25+price_h+25}" x2="{w-20}" y2="{25+price_h+25}" stroke="#dce3ec"/>{''.join(bars)}{labels}<text x="{w-270}" y="18" fill="#142338">收盘</text><text x="{w-190}" y="18" fill="#d5534f">MA20</text><text x="{w-100}" y="18" fill="#2878b5">MA60</text></svg>'''


def main() -> None:
    gate = json.loads((OUT / "qfq_verification.json").read_text(encoding="utf-8"))
    eco = json.loads((OUT / "limit_ecology_latest.json").read_text(encoding="utf-8"))
    integrated = json.loads((OUT / "integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"] == "verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"] == "2026-09-18"
    assert eco["as_of_trade_date"] == "2026-09-18"
    cols = ["trade_date","ts_code","open","high","low","close","vol","amount","RPS_50","RPS_120","RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw.trade_date)
    raw = raw[raw.trade_date <= TARGET].sort_values(["ts_code","trade_date"])
    # Five years plus warmup is enough for the 250-bar eligibility and 2022+ study.
    raw = raw[raw.trade_date >= "2020-01-01"].copy()
    parts=[]
    for _,g in raw.groupby("ts_code", sort=False):
        if len(g) < 280: continue
        z=add_indicators(g.reset_index(drop=True))
        z["bars_full"] = np.arange(1,len(z)+1)
        parts.append(z)
    calc=pd.concat(parts,ignore_index=True)
    calc["BASE_STUDY"]=(calc.bars_full>250)&(calc.ADV20>=1.5e8)
    calc["BROAD"]=(calc.BASE_STUDY&calc.YJ&(calc.RPS_120>=80))
    calc["SIMILAR"]=(calc.BROAD&(calc.RPS_50<20)&(calc.close<calc.M60))
    broad=collect_events(calc,"BROAD")
    similar=collect_events(calc,"SIMILAR")
    recent=similar[similar.signal_date>=pd.Timestamp("2024-01-01")].copy()
    sg=calc[(calc.ts_code==CODE)&(calc.trade_date<=TARGET)].sort_values("trade_date").copy()
    row=sg.iloc[-1]; prev=sg.iloc[-2]
    metrics={
        "date":str(row.trade_date.date()),"open":float(row.open),"high":float(row.high),"low":float(row.low),"close":float(row.close),
        "ret1_pct":float((row.close/prev.close-1)*100),"ma5":float(row.M5),"ma10":float(row.M10),"ma20":float(row.M20),"ma60":float(row.M60),"ma120":float(row.M120),
        "ma20_slope1":float(row.M20-prev.M20),"vol_ratio":float(row.vol/row.VM20),"adv20_yi":float(row.ADV20/1e8),"atr20":float(pd.concat([sg.high-sg.low,(sg.high-sg.close.shift()).abs(),(sg.low-sg.close.shift()).abs()],axis=1).max(axis=1).rolling(20).mean().iloc[-1]),
        "rps50":float(row.RPS_50),"rps120":float(row.RPS_120),"rps250":float(row.RPS_250),"prior5_high":float(sg.high.iloc[-6:-1].max()),"prior20_high":float(sg.high.iloc[-21:-1].max()),"low10":float(sg.low.tail(10).min()),"low20":float(sg.low.tail(20).min()),
        "from_ma20_pct":float((row.close/row.M20-1)*100),"to_ma60_pct":float((row.M60/row.close-1)*100),"to_prior20_high_pct":float((sg.high.iloc[-21:-1].max()/row.close-1)*100),
        "macd_hist":float(row.MH),"macd_hist_rising":bool(row.MH>prev.MH),"yday_close":float(prev.close)
    }
    inds=integrated["industries"]
    semi=next(x for x in inds if x["name"]=="半导体")
    semi_rank=next(i+1 for i,x in enumerate(inds) if x["industry"]==semi["industry"])
    study={"similar":summary(similar),"similar_recent":summary(recent),"broad":summary(broad),"definition":"YJ、ADV20≥1.5亿、RPS120≥80；相似组再加RPS50<20且收盘<MA60；同股事件至少间隔20日；信号次日开盘进入","limitations":"描述性历史条件率；当前条件参与定义，存在选择偏差；当前行业/名称口径可能有历史幸存者偏差；未计交易费用和涨跌停可成交性"}
    risk_floor_pct=12.0
    risk_floor_price=metrics["close"]*(1-risk_floor_pct/100)
    reward_ma60_pct=metrics["to_ma60_pct"]
    r_ma60=reward_ma60_pct/risk_floor_pct
    two_r_price=metrics["close"]*(1+2*risk_floor_pct/100)
    verdict={"label":"中性偏多，但尚未完成趋势确认","continuation_grade":"观察，不可执行","risk_floor_pct":risk_floor_pct,"risk_floor_price":risk_floor_price,"reward_to_ma60_pct":reward_ma60_pct,"reward_r_to_ma60":r_ma60,"two_r_price":two_r_price}
    payload={"metrics":metrics,"industry":{**semi,"rank":semi_rank},"market":integrated["market"],"ecology":eco,"study":study,"verdict":verdict}
    (OUT/"a_share_analysis_688146_20260918.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    sim=study["similar"]
    def rate(h): return sim[str(h)]["win_rate_pct"]
    # Avoid turning a descriptive rate into a point forecast: use a band and explicit conditions.
    stance="延续概率略高于五五开" if rate(10)>50 else "延续概率未明显高于五五开"
    chart=svg_chart(sg)
    def study_rows(d):
        return "".join(f'<tr><td>+{h}日</td><td>{d[str(h)]["win_rate_pct"]:.1f}%</td><td>{d[str(h)]["win_rate_ci95_pct"][0]:.1f}%–{d[str(h)]["win_rate_ci95_pct"][1]:.1f}%</td><td>{d[str(h)]["median_pct"]:+.2f}%</td><td>{d[str(h)]["p_gain5_pct"]:.1f}%</td><td>{d[str(h)]["p_loss5_pct"]:.1f}%</td><td>{d[str(h)]["mfe_median_pct"]:+.2f}%</td><td>{d[str(h)]["mae_median_pct"]:+.2f}%</td></tr>' for h in (1,5,10,20))
    html=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>中船特气日K趋势分析</title><style>:root{{--navy:#142338;--muted:#667085;--line:#dfe6ee;--red:#c9343a;--green:#07805d;--amber:#9a5a08}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fa;color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1280px;margin:auto;padding:22px}}header,section{{border-radius:18px;padding:24px;margin-bottom:16px}}header{{background:var(--navy);color:white}}section{{background:white;border:1px solid var(--line)}}h1{{margin:4px 0 10px;font-size:29px}}h2{{margin:0 0 12px;font-size:21px}}.muted{{color:var(--muted)}}header .muted{{color:#c7d2e2}}.badge{{display:inline-block;padding:6px 11px;border:1px solid #7ea2bd;border-radius:9px;margin:4px 8px 4px 0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{border:1px solid var(--line);border-radius:13px;padding:15px}}.num{{font:700 25px ui-monospace,Consolas,monospace}}.up{{color:var(--red)}}.risk{{color:var(--amber);font-weight:700}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#eef3f8}}svg{{width:100%;height:auto}}.callout{{border-left:5px solid #dfa844;background:#fff7e7;padding:14px 17px}}@media(max-width:800px){{main{{padding:10px}}.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:24px}}}}@media(max-width:480px){{.grid{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main><header><div>688146.SH · 中船特气 · 日K分析</div><h1>中性偏多，但还不能叫“趋势确认”</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">当前动作：观察，不可执行</span><span class="badge">置信度：中等</span></p><p>结论：{stance}。优势是RPS120/250位于极强区、9月18日放量站回MA20；短板是仍低于MA60、MA20尚未转升、RPS50很弱，且从当前位置到MA60的赔率不足2R。</p><p class="muted">原始价格锚：本地通达信；均线、RPS、ATR与回测：本地同花顺 v_daily_qfq 等比前复权。G0运营一致性通过，但不是独立外部复权证明。</p></header>
<section><h2>关键数字</h2><div class="grid"><div class="card"><div>收盘 / 当日涨幅</div><div class="num">239.20 / <span class="up">+{metrics['ret1_pct']:.2f}%</span></div></div><div class="card"><div>MA20 / MA60</div><div class="num">236.04 / 270.31</div></div><div class="card"><div>RPS 50 / 120 / 250</div><div class="num">10.95 / 100 / 99.82</div></div><div class="card"><div>量比 / ATR20</div><div class="num">1.52 / 13.31</div></div></div><p>9月18日开226.55、高242.67、低226.26、收239.20；突破此前5日高{metrics['prior5_high']:.2f}，属于右侧预警转强。收盘距MA20仅+{metrics['from_ma20_pct']:.2f}%，但距MA60仍有{metrics['to_ma60_pct']:.2f}%空间/压力。</p></section>
<section>{chart}<p class="muted">图为前复权序列，成交量保持原始口径。最新价格与本地TDX原始收盘239.20元一致。</p></section>
<section><h2>为什么偏多</h2><ul><li>RPS120=100、RPS250=99.82，说明中长周期相对强度仍在全市场最前列。</li><li>放量1.52倍站回MA20，并突破前5日高，MACD柱转正且增强，短期供需从弱转强。</li><li>半导体行业在全市场行业分组中排名第{semi_rank}：5日中位收益+{semi['ret5_pct']:.2f}%，MA20宽度{semi['breadth_ma20_pct']:.1f}%，RPS120中位{semi['median_rps120']:.1f}，且最大个股成交占比仅{semi['top1_amount_share_pct']:.1f}%，不是单股孤立行情。</li></ul><h2>为什么不能直接判断继续大涨</h2><ul><li>收盘仍低于MA60约{metrics['to_ma60_pct']:.2f}%，上方270–275元同时叠加MA60与此前20日高274.99，是第一重强压力。</li><li>MA20当日仍下降{abs(metrics['ma20_slope1']):.2f}元，尚未形成均线向上确认；RPS50仅10.95，近50日仍显著跑输多数股票。</li><li>过去60日高点416.99后出现深回撤；当前更像超跌后的右侧修复，而非主升段二次突破。</li><li>半导体虽排名靠前，但现行门控的第一行业是被单股驱动的酿酒分组，G3没有自动递补半导体；账户G5也未提供。</li></ul></section>
<section><h2>历史同形态：条件率，不是预测概率</h2><p>相似定义：YJ预警、ADV20≥1.5亿元、RPS120≥80，同时RPS50&lt;20且收盘低于MA60；2022年以来同股信号间隔至少20日，按次日开盘进入。样本{sim['events']}次、覆盖{sim['stocks']}只股票。</p><div class="scroll"><table><thead><tr><th>持有</th><th>正收益率</th><th>95%区间</th><th>收益中位</th><th>≥+5%</th><th>≤−5%</th><th>最大有利中位</th><th>最大不利中位</th></tr></thead><tbody>{study_rows(sim)}</tbody></table></div><p class="callout">这组数字只能说明历史条件分布。条件是根据中船特气当前特征挑出的，存在选择偏差；也未计手续费、滑点与涨跌停不可成交。不能把“正收益率”直接当成该股未来上涨概率。</p></section>
<section><h2>关键价位与操作含义</h2><table><thead><tr><th>位置</th><th>价格</th><th>含义</th></tr></thead><tbody><tr><td>短线确认</td><td>242.67元上方收稳</td><td>越过9月18日高点，最好量能不低于20日均量，且半导体宽度不恶化</td></tr><tr><td>第一压力</td><td>270–275元</td><td>MA60 270.31与此前20日高274.99重合；未突破前先按压力区处理</td></tr><tr><td>第一支撑</td><td>236元附近</td><td>MA20；回踩缩量、收盘守住，才是更健康的确认</td></tr><tr><td>结构支撑</td><td>226元附近</td><td>9月18日低点；跌回该区说明突破质量下降</td></tr><tr><td>硬结构失效</td><td>215.71元下方</td><td>近10日低点；收盘有效跌破，右侧修复假设失败</td></tr></tbody></table><p class="risk">赔率检查：科创板20%涨跌幅下，有效风险距离下限按12%计，对应约210.50元；从239.20到MA60仅约+13.00%，约1.08R，未达到2R。要达到2R需约296.61元，必须先有效突破270–275元压力区。因此当前收盘价不适合依据本系统直接追买。</p></section>
<section><h2>三种情景</h2><ol><li><b>强势延续：</b>收盘站稳242.67并随后突破270–275，半导体行业宽度保持≥50%。届时才从修复升级为趋势确认。</li><li><b>健康回踩：</b>回踩236附近缩量、收盘不失MA20，再次放量转强。这比直接追高更符合右侧确认逻辑。</li><li><b>失败：</b>放量跌回MA20下方；若连续两日收盘低于MA20，趋势账本应退出。跌破215.71，修复结构失效。</li></ol><p><b>空仓：</b>等回踩确认或突破270–275后的再确认，不在当前价位把预警当主升。<b>已有仓：</b>可观察MA20与226区域，不因再次入选加仓；若收盘跌破MA20减半、连续两日跌破清仓，跌破MA60规则对本股当前并不适用，因为它尚未站上MA60形成完整趋势仓。</p><p><b>T+1风险：</b>新仓当日不可卖；科创板波动上限20%，普通止损可能失效。未提供持仓成本、账户权益、可用现金和单笔风险预算，因此不生成股数或仓位。</p></section>
<section><h2>事实、推断与交易假设</h2><p><b>事实：</b>价格、均线、RPS、成交量、行业宽度和历史事件统计来自本地日线；数据截至9月18日。</p><p><b>推断：</b>中长期相对强度保留，短期出现右侧修复，但MA60仍构成趋势确认门槛。</p><p><b>交易假设：</b>只有在236附近回踩被承接，或270–275有效突破后完成回踩确认，延续上涨的赔率才明显改善。</p><p><b>最大风险：</b>RPS120高是历史结果，不保证未来；高波动反抽可能在MA60前结束。<b>不操作的机会成本：</b>若直接V形上穿275，等待确认会少吃一段上涨；代价换来的是避免在1.08R位置追入。</p></section>
<section><h2>审计边界</h2><p>历史事件研究使用当前前复权数据和当前可得股票样本，存在历史ST状态不可得、幸存者偏差和条件选择偏差。ST_cycle=82.2、状态“修复”、覆盖率1.0，但st_cycle_formal=false，仅作风险上限参考。未使用估算资金流、陈旧概念板块或分钟级CER。</p><p>覆盖沪深A股，不含北交所。报告JSON：output/a_share_analysis_688146_20260918.json。</p></section></main></body></html>'''
    out=OUT/"a_share_analysis_20260919_688146_daily_k.html"
    assert "NaN" not in html and "Infinity" not in html and "http://" not in html and "https://" not in html
    out.write_text(html,encoding="utf-8")
    print(json.dumps({"report":str(out),"metrics":metrics,"study":study,"industry_rank":semi_rank,"verdict":verdict},ensure_ascii=False))


if __name__ == "__main__":
    main()
