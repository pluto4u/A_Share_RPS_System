from pathlib import Path
from datetime import datetime
import html
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DATA = OUT / "tdx_stock_daily_with_rps.parquet"
AUDIT = OUT / "integrated_decision_20260902_audit.json"
REPORT = OUT / "a_share_analysis_20260902_candidate_deep_dive.html"


META = {
    "000807.SZ": {"name": "云铝股份", "industry": "有色", "industry_rank": 84,
                  "industry_score": 26.7, "stage": "策略B｜首次缩量回踩", "priority": 1},
    "300145.SZ": {"name": "南方泵业", "industry": "通用机械", "industry_rank": 66,
                  "industry_score": 38.0, "stage": "策略A｜放量启动", "priority": 2},
    "600637.SH": {"name": "东方明珠", "industry": "传媒娱乐", "industry_rank": 20,
                  "industry_score": 68.0, "stage": "策略B｜首次缩量回踩", "priority": 3,
                  "single_stock_driven": True},
}


def f(v, n=2):
    return f"{float(v):.{n}f}"


def metrics(df, code):
    g = df[df.ts_code.eq(code)].sort_values("trade_date").copy()
    pc = g.close.shift()
    tr = pd.concat([(g.high-g.low), (g.high-pc).abs(), (g.low-pc).abs()], axis=1).max(axis=1)
    g["atr20"] = tr.rolling(20).mean()
    for n in (5, 10, 20, 60, 120):
        g[f"ma{n}"] = g.close.rolling(n).mean()
    row = g.iloc[-1]
    prev = g.iloc[-2]
    result = {"code": code, **META[code]}
    result.update({
        "open": row.open, "high": row.high, "low": row.low, "close": row.close,
        "day_pct": (row.close / prev.close - 1) * 100,
        "ma5": row.ma5, "ma10": row.ma10, "ma20": row.ma20,
        "ma60": row.ma60, "ma120": row.ma120,
        "vs_ma20": (row.close / row.ma20 - 1) * 100,
        "atr_pct": row.atr20 / row.close * 100,
        "vol_ratio": row.vol / g.vol.rolling(20).mean().iloc[-1],
        "adv20": g.amount.rolling(20).mean().iloc[-1] / 1e8,
        "rps50": row.RPS_50, "rps120": row.RPS_120, "rps250": row.RPS_250,
        "rps50_d20": row.RPS_50 - g.iloc[-21].RPS_50,
        "ret5": (row.close / g.iloc[-6].close - 1) * 100,
        "ret20": (row.close / g.iloc[-21].close - 1) * 100,
        "ret60": (row.close / g.iloc[-61].close - 1) * 100,
        "prev_high5": g.high.shift().rolling(5).max().iloc[-1],
        "prev_high20": g.high.shift().rolling(20).max().iloc[-1],
        "high60": g.high.rolling(60).max().iloc[-1],
        "low20": g.low.rolling(20).min().iloc[-1],
        "closes": g.close.tail(60).tolist(),
    })
    return result


def sparkline(values, color="#e23b3b"):
    vals = np.asarray(values, dtype=float)
    lo, hi = vals.min(), vals.max()
    span = max(hi-lo, 1e-9)
    pts = []
    for i, v in enumerate(vals):
        x = 8 + i * 284 / max(len(vals)-1, 1)
        y = 72 - (v-lo) / span * 58
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg viewBox="0 0 300 82" role="img" aria-label="近60日收盘价走势">'
            f'<line x1="8" y1="72" x2="292" y2="72" stroke="#d8dee9"/>'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2.4"/>'
            f'<text x="8" y="11" font-size="9" fill="#64748b">高 {hi:.2f}</text>'
            f'<text x="247" y="80" font-size="9" fill="#64748b">低 {lo:.2f}</text></svg>')


def verdict(r):
    if r["code"] == "000807.SZ":
        return {
            "tag": "观察优先级 1｜最接近合格回踩",
            "why": "缩量回踩MA20，RPS50近20日改善明显，RPS250仍强；但收盘低于MA120，RPS120仅39.8，且有色行业仅第84。",
            "watch": "先收复27.50；更强确认是放量突破28.99。仅满足价格条件仍不等于通过G3。",
            "cancel": "收盘跌破25.11，或反弹时量价背离；继续留在MA120下方则不升级。",
            "r_check": "以26.57观察、25.11结构失效，计划风险5.5%，受普通股跌停约束有效风险至少6%；2R需约29.76，高于60日高28.99，必须先完成新高突破。",
        }
    if r["code"] == "300145.SZ":
        return {
            "tag": "观察优先级 2｜启动成立但不宜追",
            "why": "放量1.62倍突破前高，短线5/20日收益较强；但距MA20达11.5%，RPS50仅27.1且20日下降47.6点，行业仅第66。",
            "watch": "不追4.94—5.00；等待4.65—4.78缩量承接并守住4.50，再观察能否重新放量收复5.00。",
            "cancel": "收盘跌破MA20约4.43，或放量跌破4.50；启动次日直接冲高回落也视为失败。",
            "r_check": "若回踩约4.72、以4.43为结构失效，普通股6%跌停约束主导；2R至少看5.29。当前虽有空间，但低RPS和非主线使胜率门未过。",
        }
    return {
        "tag": "观察优先级 3｜上方空间不足",
        "why": "仍站在MA20/MA60上且回踩量缩；但低于MA120，RPS120仅38.8，传媒娱乐行业虽第20却被判定为单股驱动。",
        "watch": "先收复8.65并放量，再看8.92能否有效突破；8.65以下只是箱体内反弹。",
        "cancel": "收盘跌破7.83，或跌破MA60约8.13后无法次日收回。",
        "r_check": "以8.41观察、7.83结构失效，风险约6.9%，2R需约9.57，明显高于60日高8.92；现有箱体不满足2R。",
    }


def main():
    df = pd.read_parquet(DATA)
    rows = [metrics(df, code) for code in META]
    rows.sort(key=lambda x: x["priority"])
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    market = audit["market"]
    g1 = next(g for g in audit["gates"] if g["id"] == "G1")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cards = []
    for r in rows:
        v = verdict(r)
        cards.append(f'''
        <article class="stock">
          <div class="stock-head"><div><span class="rank">#{r['priority']}</span><h2>{html.escape(r['name'])} <small>{r['code']}</small></h2><p>{r['stage']} · {r['industry']}行业第{r['industry_rank']} · 共振{r['industry_score']:.1f}</p></div><span class="reject">观察，不可执行</span></div>
          <div class="cols"><div>{sparkline(r['closes'])}</div><div class="quote"><b>{r['close']:.2f}</b><span class="{'up' if r['day_pct'] >= 0 else 'down'}">{r['day_pct']:+.2f}%</span><small>高/低 {r['high']:.2f}/{r['low']:.2f} · ATR {r['atr_pct']:.1f}%</small></div></div>
          <div class="metrics">
            <div><label>MA5/10/20</label><b>{r['ma5']:.2f} / {r['ma10']:.2f} / {r['ma20']:.2f}</b></div>
            <div><label>MA60/120</label><b>{r['ma60']:.2f} / {r['ma120']:.2f}</b></div>
            <div><label>距MA20</label><b>{r['vs_ma20']:+.1f}%</b></div>
            <div><label>量比/ADV20</label><b>{r['vol_ratio']:.2f} / {r['adv20']:.1f}亿</b></div>
            <div><label>RPS 50/120/250</label><b>{r['rps50']:.1f} / {r['rps120']:.1f} / {r['rps250']:.1f}</b></div>
            <div><label>5/20/60日收益</label><b>{r['ret5']:+.1f}% / {r['ret20']:+.1f}% / {r['ret60']:+.1f}%</b></div>
          </div>
          <h3>{v['tag']}</h3><p>{v['why']}</p>
          <div class="plan"><div><label>重新进入观察池</label>{v['watch']}</div><div><label>结构取消</label>{v['cancel']}</div><div><label>2R检验</label>{v['r_check']}</div></div>
        </article>''')

    html_text = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>0902右侧趋势候选深度审计</title><style>
    :root{{--ink:#172033;--muted:#667085;--bg:#f2f5f9;--card:#fff;--red:#cf2e2e;--green:#16855b;--amber:#a15c00;--navy:#101a33}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Microsoft YaHei",Arial,sans-serif}}main{{max-width:1320px;margin:auto;padding:24px}}header{{background:linear-gradient(135deg,#0e1931,#29395e);color:#fff;border-radius:18px;padding:26px;box-shadow:0 12px 28px #101a3322}}h1{{margin:0 0 8px;font-size:30px}}header p{{margin:5px 0;color:#dbe5ff}}.decision{{display:grid;grid-template-columns:1.4fr repeat(4,1fr);gap:10px;margin-top:18px}}.decision div{{background:#ffffff12;padding:12px;border:1px solid #ffffff20;border-radius:12px}}label{{display:block;color:var(--muted);font-size:12px;font-weight:700;margin-bottom:3px}}header label{{color:#adbad7}}.decision b{{font-size:18px}}nav{{margin:18px 0;background:#fff;border-radius:12px;padding:11px 15px}}nav a{{color:#274d91;margin-right:18px;text-decoration:none}}.summary{{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin:16px 0}}.panel,.stock{{background:#fff;border-radius:16px;padding:20px;box-shadow:0 5px 18px #24314d10}}.panel h2,.stock h2{{margin:0}}.risk{{border-left:5px solid #db9b37}}.stock{{margin:16px 0}}.stock-head{{display:flex;align-items:start;justify-content:space-between;gap:12px}}.stock-head h2 small{{font-size:14px;color:var(--muted)}}.stock-head p{{margin:2px 0;color:var(--muted)}}.rank{{display:inline-block;background:#e9eef8;color:#274d91;border-radius:20px;padding:2px 10px;font-weight:700}}.reject{{white-space:nowrap;background:#fff2de;color:#8b5100;border:1px solid #f0cf9b;padding:6px 10px;border-radius:10px;font-weight:700}}.cols{{display:grid;grid-template-columns:1fr 220px;gap:20px;align-items:center;margin-top:8px}}svg{{width:100%;height:110px}}.quote b{{font:700 32px Consolas,monospace;display:block}}.quote span{{font-weight:700}}.quote small{{display:block;color:var(--muted)}}.up{{color:var(--red)}}.down{{color:var(--green)}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;margin:8px 0 14px}}.metrics div{{background:#f7f9fc;padding:10px;border-radius:10px}}.metrics b{{font:600 13px Consolas,"Microsoft YaHei"}}h3{{margin:14px 0 4px;color:#263e6e}}.plan{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.plan div{{background:#f7f9fc;border-top:3px solid #8295ba;padding:11px;border-radius:8px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #e8ecf2;text-align:left}}th{{background:#f7f9fc}}footer{{color:var(--muted);padding:20px 3px}}@media(max-width:850px){{main{{padding:10px}}.decision,.summary,.metrics,.plan,.cols{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{.decision,.summary,.metrics,.plan,.cols{{grid-template-columns:1fr}}.stock-head{{display:block}}}}@media print{{body{{background:#fff}}nav{{display:none}}.panel,.stock,header{{box-shadow:none;break-inside:avoid}}}}
    </style></head><body><main>
    <header><h1>0902右侧趋势候选｜深度审计</h1><p>生成 {generated}（北京时间） · 数据截至 2026-09-02 · 同花顺本地 v_daily_qfq 等比前复权 · 量额为原始口径</p>
      <div class="decision"><div><label>账户级结论</label><b>零交易 / 研究观察</b></div><div><label>市场状态</label><b>风险收缩倾向</b></div><div><label>量化周期</label><b>修复 34.8</b></div><div><label>有效仓位上限</label><b>≤35%</b></div><div><label>三股可执行</label><b>0 / 3</b></div></div>
      <p>唯一主计划：今天不为三只候选生成买单。次日只观察预设价位，价格触发不能绕过第一行业、RPS与账户G5。</p></header>
    <nav><a href="#market">市场</a><a href="#stocks">逐股</a><a href="#gates">门控</a><a href="#audit">口径</a></nav>
    <section id="market" class="summary"><article class="panel"><h2>为什么结论偏谨慎</h2><p>0902沪深A股中位涨跌幅 <b class="down">{market['median_ret1_pct']:+.2f}%</b>，上涨家数占比仅 <b>{market['up_pct']:.1f}%</b>；两市成交额约 <b>{market['total_amount_yi']:.0f}亿元</b>。涨停52只、跌停9只、炸板21只，昨日涨停收盘收益 -0.76%。虽然状态机仍标“修复”，但三日斜率 -17.47，属于快速降温。</p><p>全市场第一行业为<b>农林牧渔</b>，共振分94、站上MA20宽度88%、量能2.25倍ADV20。本页三只分别来自通用机械、有色、传媒娱乐，均不属于第一行业。</p></article><article class="panel risk"><h2>先给排序</h2><ol><li><b>云铝股份</b>：形态最像合格回踩，但中期强度未过。</li><li><b>南方泵业</b>：启动成立，当前追价赔率差。</li><li><b>东方明珠</b>：回踩尚稳，但2R空间不足。</li></ol><p><b>排序只代表观察优先级，不是买入排序。</b></p></article></section>
    <section id="stocks">{''.join(cards)}</section>
    <section id="gates" class="panel"><h2>G0—G5结果</h2><table><thead><tr><th>门控</th><th>结果</th><th>含义</th></tr></thead><tbody>
      <tr><td>G0 复权</td><td>通过</td><td>本地运营一致性16/16，全库公式及量额错配均为0；不是独立外部复权证明。</td></tr>
      <tr><td>G1 风险</td><td>通过但降险</td><td>ST_cycle 34.8、三日斜率-17.47；formal=false，因此上限由状态显示55%压至有效≤35%。</td></tr>
      <tr><td>G2 RPS</td><td>失败</td><td>严格条件为RPS120≥80、站上MA20/MA60、乖离≤12%、ADV20≥1.5亿；三股RPS120均不足80，整合流程全市场候选也是0只。</td></tr>
      <tr><td>G3 行业共振</td><td>三股失败</td><td>第一行业是农林牧渔；三股均为非第一行业。传媒娱乐还触发单股驱动降级。</td></tr>
      <tr><td>G4 执行质量</td><td>失败</td><td>没有同时通过G2/G3的标的，不能进入执行比较。</td></tr>
      <tr><td>G5 账户</td><td>失败</td><td>缺账户权益、现金、现有持仓与单笔风险预算，不生成股数或订单。</td></tr>
    </tbody></table></section>
    <section id="audit" class="summary"><article class="panel"><h2>事实 / 推断 / 交易假设</h2><p><b>事实：</b>价格、均线、ATR、RPS、成交额均来自0902本地数据；行业来自当日通达信行业映射。</p><p><b>推断：</b>市场处于“状态标签仍修复、内部广度快速转弱”的风险收缩倾向。</p><p><b>交易假设：</b>只有预设关键价位被收复、行业重新进入第一并通过G2，才可能形成趋势确认；本页价位仅用于观察和取消，不是委托指令。</p></article><article class="panel risk"><h2>风险与机会成本</h2><p>最大风险是T+1下启动失败后当日无法卖出。空仓可能错过云铝或南方泵业的快速反弹，但以当前低RPS、非第一行业和市场降温为代价去抢跑，预期质量不足。</p><p>拒绝使用：估算资金流、过期概念归属、历史分钟CER和未经验证的精确仓位。</p></article></section>
    <footer>覆盖沪深A股，不含北交所。ST_cycle_v1尚未完成走样本外验证，只作风险上限参考。非投资建议，不自动下单。</footer>
    </main></body></html>'''
    REPORT.write_text(html_text, encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
