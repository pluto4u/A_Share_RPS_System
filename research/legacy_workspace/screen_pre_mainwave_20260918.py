from __future__ import annotations

import html
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
from screen_siruipu_right_side_v2 import add_indicators, HQ, INCON
from screen_siruipu_right_side import industry_label, load_industry_map, load_industry_names, load_name_master

TARGET = pd.Timestamp("2026-09-18")
PARQUET = OUT / "tdx_stock_daily_with_rps.parquet"


def wilson(wins: int, n: int) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    z, p = 1.96, wins / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (mid - half) * 100, (mid + half) * 100


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    parts = []
    bars_n = raw.groupby("ts_code", sort=False).size()
    for code, g in raw.groupby("ts_code", sort=False):
        if len(g) < 280:
            continue
        z = add_indicators(g.reset_index(drop=True))
        z["bars_full"] = np.arange(1, len(z) + 1)
        z["ret1_pct"] = z.close.pct_change() * 100
        z["ret5_pct"] = z.close.pct_change(5) * 100
        z["ret10_pct"] = z.close.pct_change(10) * 100
        z["ret20_pct"] = z.close.pct_change(20) * 100
        z["M20_REF3"] = z.M20.shift(3)
        z["M5_REF1"] = z.M5.shift(1)
        z["HH20_PREV"] = z.high.rolling(20, min_periods=20).max().shift(1)
        z["HH60_PREV"] = z.high.rolling(60, min_periods=60).max().shift(1)
        z["LL10"] = z.low.rolling(10, min_periods=10).min()
        z["vs_ma20_pct"] = (z.close / z.M20 - 1) * 100
        z["vs_ma60_pct"] = (z.close / z.M60 - 1) * 100
        z["vol_ratio"] = z.vol / z.VM20
        z["to_hh20_pct"] = (z.HH20_PREV / z.close - 1) * 100
        z["from_hh60_pct"] = (z.close / z.HH60_PREV - 1) * 100
        tr = pd.concat([z.high-z.low, (z.high-z.close.shift()).abs(), (z.low-z.close.shift()).abs()], axis=1).max(axis=1)
        z["ATR20X"] = tr.rolling(20).mean()
        z["atr_pct"] = z.ATR20X / z.close * 100
        z["ADV20_YI"] = z.ADV20 / 1e8
        z["BASE_PM"] = (z.bars_full > 260) & (z.ADV20 >= 1.5e8)
        # Tier A: positive momentum is visible, but price is still near the 20/60-day pivot and not yet extended.
        z["READY_STRUCT"] = (
            z.BASE_PM & (z.RPS_120 >= 85) & (z.RPS_250 >= 75)
            & (z.close > z.M20) & (z.M20 > z.M20_REF3)
            & (z.M5 > z.M10) & (z.M5 > z.M5_REF1)
            & (z.MH > 0) & (z.MH > z.MH_REF1)
            & z.vs_ma20_pct.between(0, 7.5)
            & z.vs_ma60_pct.between(-4, 5)
            & z.ret1_pct.between(-3, 7.5)
            & z.ret5_pct.between(0, 10)
            & z.ret10_pct.between(-2, 15)
            & z.vol_ratio.between(0.8, 1.8)
            & z.to_hh20_pct.between(-2, 6)
            & z.from_hh60_pct.between(-35, -4)
            & ~z.QD & ~z.QR & ~z.QQR
        ).fillna(False)
        # Tier B: one step earlier; MACD can still be below zero but must improve, and MA60 may remain overhead.
        z["SETUP_STRUCT"] = (
            z.BASE_PM & (z.RPS_120 >= 80) & (z.RPS_250 >= 70)
            & (z.close > z.M20) & (z.M20 > z.M20_REF3)
            & (z.M5 >= z.M10 * 0.99) & (z.M5 > z.M5_REF1)
            & (z.MH > z.MH_REF1)
            & z.vs_ma20_pct.between(0, 6)
            & z.vs_ma60_pct.between(-8, 4)
            & z.ret1_pct.between(-4, 6)
            & z.ret5_pct.between(-2, 8)
            & z.ret10_pct.between(-5, 12)
            & z.vol_ratio.between(0.7, 1.6)
            & z.to_hh20_pct.between(-1, 10)
            & z.from_hh60_pct.between(-40, -4)
            & ~z.READY_STRUCT & ~z.QD & ~z.QR & ~z.QQR
        ).fillna(False)
        z["bars_total"] = int(bars_n.get(code, len(z)))
        parts.append(z)
    return pd.concat(parts, ignore_index=True)


def collect_events(calc: pd.DataFrame, col: str) -> pd.DataFrame:
    rows = []
    for code, g in calc.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date").reset_index(drop=True)
        idxs = np.flatnonzero(g[col].fillna(False).to_numpy(bool))
        last = -999
        for i in idxs:
            if g.at[i, "trade_date"] < pd.Timestamp("2022-01-01") or i - last < 20 or i + 20 >= len(g):
                continue
            last = i
            entry = float(g.at[i + 1, "open"])
            if not np.isfinite(entry) or entry <= 0:
                continue
            d = {"code": code, "date": g.at[i, "trade_date"]}
            for h in (5, 10, 20):
                d[f"ret{h}"] = (float(g.at[i + h, "close"]) / entry - 1) * 100
            win = g.iloc[i + 1:i + 21]
            d["mfe20"] = (float(win.high.max()) / entry - 1) * 100
            d["mae20"] = (float(win.low.min()) / entry - 1) * 100
            rows.append(d)
    return pd.DataFrame(rows)


def event_summary(e: pd.DataFrame) -> dict:
    out = {"events": int(len(e)), "stocks": int(e.code.nunique()) if len(e) else 0}
    for h in (5, 10, 20):
        s = e[f"ret{h}"]
        wins = int((s > 0).sum())
        lo, hi = wilson(wins, len(s))
        out[str(h)] = {
            "positive_pct": round(wins / len(s) * 100, 1), "ci95_pct": [round(lo, 1), round(hi, 1)],
            "median_pct": round(float(s.median()), 2), "mean_pct": round(float(s.mean()), 2),
        }
    out["mfe15_20_pct"] = round(float((e.mfe20 >= 15).mean() * 100), 1)
    out["mfe20_median_pct"] = round(float(e.mfe20.median()), 2)
    out["mae20_median_pct"] = round(float(e.mae20.median()), 2)
    return out


def spark(g: pd.DataFrame, label: str) -> str:
    s = g.tail(70)
    vals = s.close.astype(float) / float(s.close.iloc[0]) * 100
    w, h, p = 260, 70, 6
    lo, hi = float(vals.min()), float(vals.max())
    span = max(hi-lo, 1)
    xs = np.linspace(p, w-p, len(vals))
    ys = p + (hi-vals.to_numpy())/span*(h-2*p)
    pts = " ".join(f"{x:.1f},{y:.1f}" for x,y in zip(xs,ys))
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(label)}近70日归一化收盘"><polyline points="{pts}" fill="none" stroke="#1b4d7a" stroke-width="2.6"/></svg>'


def main() -> None:
    gate = json.loads((OUT / "qfq_verification.json").read_text(encoding="utf-8"))
    eco = json.loads((OUT / "limit_ecology_latest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUT / "integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"] == "verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"] == "2026-09-18"
    assert eco["as_of_trade_date"] == "2026-09-18"

    cols = ["trade_date","ts_code","open","high","low","close","vol","amount","RPS_50","RPS_120","RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw.trade_date)
    raw = raw[raw.trade_date.le(TARGET) & raw.trade_date.ge(pd.Timestamp("2020-01-01"))].sort_values(["ts_code","trade_date"])
    calc = build_features(raw)

    names = load_name_master(HQ)
    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    day = calc[calc.trade_date.eq(TARGET)].copy()
    day["name"] = day.ts_code.map(names).fillna("")
    compact = day.name.str.replace(" ", "", regex=False).str.upper()
    day = day[~compact.str.startswith(("ST", "*ST")) & ~compact.str.contains("退", regex=False)].copy()
    day = day.merge(imap, on="ts_code", how="left")
    day["industry_name"] = [industry_label(str(x), inames) if pd.notna(x) else "未分类" for x in day.industry]

    industries = audit["industries"]
    rank = {x["name"]: i+1 for i,x in enumerate(industries)}
    istat = {x["name"]: x for x in industries}
    day["industry_rank"] = day.industry_name.map(rank).fillna(999).astype(int)
    day["industry_ok"] = day.industry_name.map(lambda n: n in istat and rank[n] <= 10 and not istat[n]["single_stock_driven"])
    current = day[day.industry_ok & (day.READY_STRUCT | day.SETUP_STRUCT)].copy()
    current["stage"] = np.where(current.READY_STRUCT, "临界启动", "蓄势预备")
    current["stage_order"] = np.where(current.READY_STRUCT, 0, 1)
    # This is a gate order, not a weighted score.
    current = current.sort_values(["stage_order","industry_rank","RPS_120","to_hh20_pct"], ascending=[True,True,False,True])
    shortlist = current.head(15).copy()

    def rowdict(r: pd.Series) -> dict:
        support_pool = [float(r.M10), float(r.M20), float(r.M60)]
        below = sorted([x for x in support_pool if x < r.close], reverse=True)
        return {
            "code": r.ts_code, "name": r["name"], "industry": r.industry_name, "industry_rank": int(r.industry_rank), "stage": r.stage,
            "close": float(r.close), "ret1_pct": float(r.ret1_pct), "ret5_pct": float(r.ret5_pct), "ret10_pct": float(r.ret10_pct),
            "rps50": float(r.RPS_50), "rps120": float(r.RPS_120), "rps250": float(r.RPS_250),
            "vs_ma20_pct": float(r.vs_ma20_pct), "vs_ma60_pct": float(r.vs_ma60_pct), "vol_ratio": float(r.vol_ratio),
            "atr_pct": float(r.atr_pct), "adv20_yi": float(r.ADV20_YI), "to_trigger_pct": float(max(r.to_hh20_pct,0)),
            "trigger": float(r.HH20_PREV), "support": below[0] if below else float(r.M20), "ma20": float(r.M20),
            "hard_fail": float(r.LL10), "from_hh60_pct": float(r.from_hh60_pct), "macd_positive": bool(r.MH>0),
        }

    candidates = [rowdict(r) for _,r in shortlist.iterrows()]
    events_ready = collect_events(calc, "READY_STRUCT")
    events_setup = collect_events(calc, "SETUP_STRUCT")
    study = {"ready": event_summary(events_ready), "setup": event_summary(events_setup),
             "note": "2022年以来结构条件事件；同股信号间隔≥20日，次日开盘进入；不含当前行业排名过滤，避免把当前行业状态用于历史。未计交易成本与涨跌停可成交性。"}

    payload = {"as_of":"2026-09-18","generated_at":datetime.now().isoformat(timespec="seconds"),
               "candidate_count":int(len(current)),"shortlist":candidates,"study":study,
               "decision":audit["decision"],"ecology":{"state":eco.get("cycle_state"),"ST_cycle":eco.get("ST_cycle"),"st_cycle_formal":eco.get("st_cycle_formal")},
               "definition":{"ready":"RPS120≥85、RPS250≥75；MA20三日转升；MA5>MA10且上升；MACD柱为正并增强；距MA20 0–7.5%、距MA60 -4%至+5%；5日涨0–10%；量比0.8–1.8；距前20日高不超过6%；距60日高回撤4%–35%；未触发QD/QR/QQR。","setup":"上述条件前移一档：RPS120≥80、RPS250≥70，MA20转升，MACD改善，距MA60 -8%至+4%，5日涨幅-2%至+8%，距前20日高≤10%。","current_industry_gate":"行业排名前10、非单股驱动；当前仍无正式第一行业，因此全部只观察。"}}
    (OUT/"a_share_analysis_20260919_pre_mainwave_screen.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")

    rows=[]
    for m in candidates:
        g=calc[calc.ts_code.eq(m["code"])].sort_values("trade_date")
        rows.append(f'<tr><td><b>{html.escape(m["name"])}</b><br><code>{m["code"]}</code></td><td>{html.escape(m["industry"])} #{m["industry_rank"]}</td><td>{m["stage"]}</td><td>{m["close"]:.2f}<br><span class="{("up" if m["ret1_pct"]>=0 else "down")}">{m["ret1_pct"]:+.2f}%</span></td><td>{m["rps50"]:.1f}/{m["rps120"]:.1f}/{m["rps250"]:.1f}</td><td>{m["ret5_pct"]:+.1f}%/{m["ret10_pct"]:+.1f}%</td><td>{m["vs_ma20_pct"]:+.1f}%/{m["vs_ma60_pct"]:+.1f}%</td><td>{m["vol_ratio"]:.2f}×</td><td>{m["to_trigger_pct"]:.1f}%<br>{m["trigger"]:.2f}</td><td>{spark(g,m["name"])}</td><td>{m["support"]:.2f}<br><span class="risk">失效{m["hard_fail"]:.2f}</span></td></tr>')
    table_rows="".join(rows)

    def study_rows(d: dict) -> str:
        return "".join(f'<tr><td>+{h}日</td><td>{d[str(h)]["positive_pct"]:.1f}%</td><td>{d[str(h)]["ci95_pct"][0]:.1f}%–{d[str(h)]["ci95_pct"][1]:.1f}%</td><td>{d[str(h)]["median_pct"]:+.2f}%</td><td>{d[str(h)]["mean_pct"]:+.2f}%</td></tr>' for h in (5,10,20))

    ready_names="、".join(m["name"] for m in candidates if m["stage"]=="临界启动") or "无"
    setup_names="、".join(m["name"] for m in candidates if m["stage"]=="蓄势预备") or "无"
    html_doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>主升前强动能扩展筛选</title><style>
    :root{{--navy:#142338;--bg:#f3f6fa;--line:#dce4ed;--red:#c7353b;--green:#087e5d;--amber:#9b5d0b;--muted:#657084}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1460px;margin:auto;padding:20px}}header,section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:23px;margin-bottom:16px}}header{{background:var(--navy);color:#fff}}h1{{font-size:29px;margin:4px 0 8px}}h2{{margin:0 0 10px}}.badge{{display:inline-block;border:1px solid #7692ae;border-radius:9px;padding:5px 10px;margin:3px 6px 3px 0}}.muted{{color:var(--muted)}}header .muted{{color:#cbd5e1}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:middle}}th{{background:#edf2f7}}.up{{color:var(--red)}}.down{{color:var(--green)}}.risk{{color:var(--amber);font-weight:700}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}}.card{{border:1px solid var(--line);border-radius:13px;padding:14px}}.num{{font:700 23px ui-monospace,Consolas,monospace}}svg{{width:210px;height:58px;display:block}}.callout{{background:#fff7e7;border-left:5px solid #d9a43d;padding:13px 16px}}@media(max-width:800px){{main{{padding:9px}}.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:#fff}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>本地沪深日K · 主升前动能扫描</div><h1>扩展后找到{len(candidates)}只：只做观察，不抢跑</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">临界启动：{sum(m['stage']=='临界启动' for m in candidates)}只</span><span class="badge">蓄势预备：{sum(m['stage']=='蓄势预备' for m in candidates)}只</span><span class="badge">G3/G5未通过</span></p><p><b>临界启动：</b>{ready_names}。<br><b>蓄势预备：</b>{setup_names}。</p><p class="muted">目标是把拓荆科技的信号向前移动：保留中长期RPS与均线转强，但限制短线涨幅、量比和乖离，并排除已经触发QD/QR/QQR的股票。原始价锚为本地TDX；指标使用本地v_daily_qfq等比前复权。</p></header>
    <section><h2>三层漏斗</h2><div class="cards"><div class="card"><div>慢周期底座</div><div class="num">RPS120≥80/85</div><p>要求RPS250同步较强，不用一天大涨代替中期质量。</p></div><div class="card"><div>启动前位置</div><div class="num">MA20转升</div><p>收盘站上MA20，距离MA60不超过8%，接近20日平台但尚未明显突破加速。</p></div><div class="card"><div>反追高</div><div class="num">5日≤10%</div><p>量比≤1.8、距MA20≤7.5%，并排除QD/QR/QQR，避免把主升中段冒充“主升前”。</p></div></div></section>
    <section><h2>前15名观察池</h2><div class="scroll"><table><thead><tr><th>股票</th><th>行业/排名</th><th>阶段</th><th>收盘/当日</th><th>RPS 50/120/250</th><th>5日/10日</th><th>距MA20/MA60</th><th>量比</th><th>距触发/20日高</th><th>近70日</th><th>支撑/失效</th></tr></thead><tbody>{table_rows}</tbody></table></div><p class="callout"><b>触发不是买入：</b>“20日高”只是确认观察点。下一交易日若直接高开突破，仍需看是否回落失守；当前无正式第一行业且账户数据缺失，全部标记为观察，不产生买单。</p></section>
    <section><h2>历史结构样本：先看是否真的有优势</h2><p>临界启动样本{study['ready']['events']}次、覆盖{study['ready']['stocks']}只股票；同股事件至少间隔20日，按次日开盘进入。</p><div class="scroll"><table><thead><tr><th>持有</th><th>正收益率</th><th>95%区间</th><th>收益中位</th><th>收益均值</th></tr></thead><tbody>{study_rows(study['ready'])}</tbody></table></div><p>20日内最大有利波动达到15%的比例为{study['ready']['mfe15_20_pct']:.1f}%；最大有利波动中位{study['ready']['mfe20_median_pct']:+.2f}%，最大不利波动中位{study['ready']['mae20_median_pct']:+.2f}%。</p><p class="muted">这是结构条件率，不是候选个股的预测概率。历史研究未使用当前行业排名过滤，以避免把当前行业状态倒灌到过去；仍存在当前名称/行业口径的幸存者偏差，未计手续费、滑点和涨跌停可成交性。</p></section>
    <section><h2>如何使用这张名单</h2><ol><li><b>临界启动：</b>只看收盘能否站稳20日高，或回踩MA20/MA60缩量承接；不在早盘直线拉升时追。</li><li><b>蓄势预备：</b>尚缺MACD转正或MA60确认，只建立观察，不提前抄底。</li><li><b>空仓：</b>等待触发，不因“可能主升”提前把假设当事实。<b>已有仓：</b>跌破MA20先降风险，连续两日不能收回则退出趋势假设。</li><li><b>T+1：</b>新仓当天无法卖出；有效风险距离至少取计划距离、涨跌幅限制×0.6和1×ATR20三者最大。未提供账户风险预算，因此不计算股数。</li></ol><p><b>机会成本：</b>等待确认会错过一部分V形上涨；代价换来的是降低假突破和弱反弹的误判。</p></section>
    <section><h2>门控与审计</h2><p>G0、G1通过；市场情绪状态“{eco.get('cycle_state')}”、ST_cycle={eco.get('ST_cycle')}、st_cycle_formal={str(eco.get('st_cycle_formal')).lower()}，仅作风险上限参考。G3无正式第一行业、G5缺账户状态，系统结论仍为“研究观察，不产生可执行买单”。</p><p>事实、推断和交易假设已分离：价格/RPS/均线是事实；“主升前”是条件标签；只有后续突破或回踩确认才构成交易假设。未使用估算资金流、陈旧概念板块或分钟级CER。覆盖沪深A股，不含北交所。</p></section>
    </main></body></html>'''
    assert "NaN" not in html_doc and "Infinity" not in html_doc and "http://" not in html_doc and "https://" not in html_doc
    report=OUT/"a_share_analysis_20260919_pre_mainwave_screen.html"
    report.write_text(html_doc,encoding="utf-8")
    print(json.dumps({"report":str(report),"candidate_count":int(len(current)),"shortlist":candidates,"study":study},ensure_ascii=False))


if __name__ == "__main__":
    main()
