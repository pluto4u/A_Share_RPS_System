#!/usr/bin/env python3
"""对通达信 EBK 趋势股名单做横盘潜在突破排序。

输入：08.tdx_qushi/260831.EBK（TDX 7 位市场前缀编码）
价格：同花顺本地 v_daily_qfq 导出的 RPS parquet；量额保持原始口径。
输出：前15候选 CSV、审计 JSON、自包含 HTML。

这是一套研究排序模型，不是经过校准的真实概率；只有后续历史事件研究才能把分数映射为概率。
"""
from __future__ import annotations

import html
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EBK = ROOT / "08.tdx_qushi" / "260831.EBK"
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
OUT = ROOT / "output"
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
sys.path.insert(0, str(ROOT / "tmp"))
from screen_siruipu_right_side import (  # noqa: E402
    industry_label, load_industry_map, load_industry_names, load_name_master,
)


def parse_ebk(path: Path) -> tuple[list[str], list[str], list[str]]:
    rows = [x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if x.strip().isdigit()]
    all_codes: list[str] = []
    hs: list[str] = []
    bj: list[str] = []
    for raw in rows:
        if len(raw) != 7:
            continue
        market, code = raw[0], raw[1:]
        if market == "0":
            ts = f"{code}.SZ"
        elif market == "1":
            ts = f"{code}.SH"
        elif market == "2":
            ts = f"{code}.BJ"
        else:
            continue
        if ts not in all_codes:
            all_codes.append(ts)
        (bj if ts.endswith(".BJ") else hs).append(ts)
    return all_codes, hs, bj


def clip01(x: float) -> float:
    if not np.isfinite(x):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def score_one(g: pd.DataFrame, target: pd.Timestamp, name: str, industry: str) -> dict | None:
    g = g[g["trade_date"] <= target].sort_values("trade_date").reset_index(drop=True)
    if len(g) < 260 or g.empty or g.iloc[-1]["trade_date"] != target:
        return None
    close = g["close"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    op = g["open"].astype(float)
    vol = g["vol"].astype(float)
    amt = g["amount"].astype(float)
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    atr20 = tr.rolling(20, min_periods=20).mean()
    vm20 = vol.rolling(20, min_periods=20).mean()
    adv20 = amt.rolling(20, min_periods=20).mean()
    if any(pd.isna(x.iloc[-1]) for x in (ma20, ma60, atr20, vm20, adv20)):
        return None

    # 15日横盘窗口；前15日作为对照窗口，避免把长期低波动误判为刚进入蓄势。
    w = g.tail(15)
    prev = g.iloc[-30:-15]
    if len(prev) < 15:
        return None
    c = float(close.iloc[-1])
    box_hi = float(w["high"].max())
    box_lo = float(w["low"].min())
    box_range = (box_hi - box_lo) / c if c > 0 else np.nan
    close_range = (float(w["close"].max()) - float(w["close"].min())) / c if c > 0 else np.nan
    body_pct = (w["close"] - w["open"]).abs() / w["close"].replace(0, np.nan)
    small_body_share = float((body_pct <= 0.018).mean())
    up_share = float((w["close"] > w["open"]).mean())
    down_share = float((w["close"] < w["open"]).mean())
    vol_ratio = float(w["vol"].tail(5).mean() / prev["vol"].mean()) if prev["vol"].mean() > 0 else np.nan
    atr_ratio = float(atr20.tail(5).mean() / atr20.iloc[-15:-5].mean()) if atr20.iloc[-15:-5].mean() > 0 else np.nan
    ma20_slope = float(ma20.iloc[-1] / ma20.iloc[-6] - 1) if ma20.iloc[-6] else np.nan
    ma60_slope = float(ma60.iloc[-1] / ma60.iloc[-11] - 1) if ma60.iloc[-11] else np.nan
    vs_ma20 = c / float(ma20.iloc[-1]) - 1
    vs_ma60 = c / float(ma60.iloc[-1]) - 1
    upper_gap = box_hi / c - 1 if c > 0 else np.nan
    box_pos = (c - box_lo) / (box_hi - box_lo) if box_hi > box_lo else 0.5
    near_upper_days = int((w["close"] >= box_hi * 0.97).sum())
    # 最近三日没有明显脱离箱体，才称为“可能突破”；已突破者单独降级为确认跟踪。
    breakout_confirmed = bool(c > box_hi * 1.01)
    rps120 = float(g["RPS_120"].iloc[-1]) if pd.notna(g["RPS_120"].iloc[-1]) else np.nan
    rps50 = float(g["RPS_50"].iloc[-1]) if pd.notna(g["RPS_50"].iloc[-1]) else np.nan
    rps250 = float(g["RPS_250"].iloc[-1]) if pd.notna(g["RPS_250"].iloc[-1]) else np.nan
    rps20ago = float(g["RPS_120"].iloc[-21]) if len(g) >= 21 and pd.notna(g["RPS_120"].iloc[-21]) else np.nan
    rps_delta20 = rps120 - rps20ago if np.isfinite(rps120) and np.isfinite(rps20ago) else np.nan
    adv_yi = float(adv20.iloc[-1] / 1e8)
    # 0~100 研究分：横盘质量 45%，突破位置/趋势 35%，相对强度/流动性 20%。
    s_range = clip01((0.20 - box_range) / 0.20)
    s_close_range = clip01((0.12 - close_range) / 0.12)
    s_body = clip01((small_body_share - 0.45) / 0.55)
    s_vol = clip01((1.05 - vol_ratio) / 0.45)
    s_atr = clip01((1.05 - atr_ratio) / 0.35)
    s_upper = clip01((0.08 - max(upper_gap, 0.0)) / 0.08)
    s_pos = clip01((box_pos - 0.55) / 0.45)
    s_trend = 0.5 * clip01((vs_ma20 + 0.01) / 0.10) + 0.5 * clip01((vs_ma60 + 0.01) / 0.18)
    s_rps = clip01((rps120 - 50.0) / 50.0) if np.isfinite(rps120) else 0.0
    s_rps_up = clip01((rps_delta20 - 0.0) / 25.0) if np.isfinite(rps_delta20) else 0.0
    s_liq = clip01(math.log1p(max(adv_yi, 0)) / math.log1p(100.0))
    raw_score = 100 * (
        0.12 * s_range + 0.08 * s_close_range + 0.12 * s_body +
        0.10 * s_vol + 0.08 * s_atr + 0.12 * s_upper + 0.08 * s_pos +
        0.12 * s_trend + 0.10 * s_rps + 0.04 * s_rps_up + 0.04 * s_liq
    )
    # 已经放量脱离箱体的标的不是“埋伏待突破”，保留但降为确认跟踪。
    if breakout_confirmed:
        raw_score -= 8.0
    # 一边倒的单向K线不符合小阴小阳横盘。
    if max(up_share, down_share) > 0.80:
        raw_score -= 8.0
    score = round(max(0.0, min(100.0, raw_score)), 1)
    if score >= 72:
        tier = "高"
    elif score >= 60:
        tier = "中高"
    elif score >= 48:
        tier = "中"
    else:
        tier = "低"
    setup = "已突破跟踪" if breakout_confirmed else ("临界待突破" if upper_gap <= 0.04 else "箱体蓄势")
    return {
        "ts_code": str(g["ts_code"].iloc[-1]), "name": name, "industry_name": industry,
        "trade_date": target.strftime("%Y-%m-%d"), "score": score, "tier": tier, "setup": setup,
        "close": round(c, 3), "box_low": round(box_lo, 3), "box_high": round(box_hi, 3),
        "box_range_pct": round(box_range * 100, 2), "close_range_pct": round(close_range * 100, 2),
        "small_body_share_pct": round(small_body_share * 100, 1), "up_share_pct": round(up_share * 100, 1),
        "down_share_pct": round(down_share * 100, 1), "vol_ratio": round(vol_ratio, 2),
        "atr_ratio": round(atr_ratio, 2), "box_pos_pct": round(box_pos * 100, 1),
        "upper_gap_pct": round(upper_gap * 100, 2), "near_upper_days": near_upper_days,
        "vs_ma20_pct": round(vs_ma20 * 100, 2), "vs_ma60_pct": round(vs_ma60 * 100, 2),
        "ma20_slope_pct": round(ma20_slope * 100, 2), "ma60_slope_pct": round(ma60_slope * 100, 2),
        "RPS_50": round(rps50, 1) if np.isfinite(rps50) else None,
        "RPS_120": round(rps120, 1) if np.isfinite(rps120) else None,
        "RPS_250": round(rps250, 1) if np.isfinite(rps250) else None,
        "rps_delta20": round(rps_delta20, 1) if np.isfinite(rps_delta20) else None,
        "adv20_yi": round(adv_yi, 2), "breakout_confirmed": breakout_confirmed,
        "g0_note": "价格来自本地 v_daily_qfq；量额为原始口径",
    }


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def num(x, d=1, suffix="") -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "未取得"
    return f"{float(x):.{d}f}{suffix}"


def render_html(meta: dict, rows: list[dict], out_path: Path) -> None:
    trs = []
    for i, r in enumerate(rows, 1):
        trs.append("<tr>" + "".join([
            f"<td><b>{i:02d}</b></td>", f"<td><b>{esc(r['ts_code'])}</b><br>{esc(r['name'])}</td>",
            f"<td>{esc(r['industry_name'])}</td>", f"<td>{esc(r['setup'])}</td>",
            f"<td><b>{num(r['score'])}</b><br>{esc(r['tier'])}</td>", f"<td>{num(r['close'],2)}</td>",
            f"<td>{num(r['box_range_pct'],1,'%')}</td>", f"<td>{num(r['small_body_share_pct'],1,'%')}</td>",
            f"<td>{num(r['vol_ratio'],2)}x</td>", f"<td>{num(r['atr_ratio'],2)}x</td>",
            f"<td>{num(r['upper_gap_pct'],1,'%')}</td>", f"<td>{num(r['box_pos_pct'],1,'%')}</td>",
            f"<td>{num(r['RPS_120'],1)}</td><td>{num(r['adv20_yi'],1,'亿')}</td>",
        ]) + "</tr>")
    if not trs:
        trs = ["<tr><td colspan='14'>没有满足基本数据条件的可计算标的。</td></tr>"]
    top = rows[:3]
    top_cards = "".join(
        f"<div class='top'><div class='rank'>#{i}</div><div><b>{esc(r['name'])}（{esc(r['ts_code'])}）</b><span>{esc(r['industry_name'])} · {esc(r['setup'])}</span></div><strong>{num(r['score'])}</strong></div>"
        for i, r in enumerate(top, 1)
    ) or "<div class='empty'>无</div>"
    html_text = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>趋势股横盘潜在突破排序</title><style>
:root{{--bg:#f4f6f8;--ink:#17202a;--muted:#667085;--line:#e4e7ec;--navy:#101828;--amber:#b54708;--green:#039855;--red:#d92d20}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 system-ui,'Microsoft YaHei',sans-serif}}.wrap{{max-width:1450px;margin:auto;padding:18px}}header{{background:var(--navy);color:#fff;border-radius:18px;padding:23px 25px;display:grid;grid-template-columns:1.8fr 1fr;gap:20px}}h1{{margin:0 0 8px;font-size:27px}}h2{{font-size:20px;margin:0 0 12px}}.sub{{color:#cbd5e1}}.action{{background:#1d2939;border:1px solid #344054;border-radius:14px;padding:16px}}.action b{{display:block;color:#fedf89;font-size:25px}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}.card{{grid-column:span 4;background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px}}.wide{{grid-column:span 12}}.half{{grid-column:span 6}}.muted{{color:var(--muted)}}.risk{{color:var(--amber);font-weight:700}}.top{{display:flex;align-items:center;gap:12px;padding:11px;border-bottom:1px solid var(--line)}}.top:last-child{{border:0}}.rank{{font:700 22px ui-monospace;color:var(--amber);width:38px}}.top span{{display:block;color:var(--muted)}}.top strong{{margin-left:auto;font:700 22px ui-monospace}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{border-collapse:collapse;width:100%;min-width:1200px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#f9fafb;position:sticky;top:0}}code{{background:#f2f4f7;padding:2px 5px;border-radius:4px}}ul{{padding-left:20px}}footer{{padding:20px 3px;color:var(--muted)}}@media(max-width:800px){{header{{grid-template-columns:1fr}}.card,.half,.wide{{grid-column:span 12}}.wrap{{padding:10px}}h1{{font-size:23px}}}}@media print{{body{{background:#fff}}header{{box-shadow:none}}}}
</style></head><body><main class='wrap'><header><div><h1>趋势股横盘潜在突破排序</h1><div class='sub'>输入：260831.EBK · 数据截至 {esc(meta['as_of'])} · 沪深A股，不含北交所</div><p>价格：同花顺本地 <code>v_daily_qfq</code> 前复权；量额：原始口径。输入名单共 {meta['input_count']} 个，排除北交所 {meta['bj_count']} 个，实际分析 {meta['analyzed_count']} 个。</p></div><div class='action'><span>统一结论</span><b>只观察前15，不追涨</b><div>评分是相对排序，不是经过校准的真实概率；突破需等待收盘确认。</div></div></header>
<section class='grid'><article class='card'><h2>前三名</h2>{top_cards}</article><article class='card'><h2>策略定义</h2><ul><li>近15日箱体振幅与收盘波动收窄。</li><li>小实体K线占比高，避免单边大阳/大阴。</li><li>近5日量能相对前15日萎缩，ATR同步收缩。</li><li>收盘靠近箱体上沿，且位于MA20/MA60上方。</li><li>RPS120与成交额只做质量加分，不替代突破确认。</li></ul></article><article class='card'><h2>执行触发</h2><ul><li>候选日：只进入观察，不直接买入。</li><li>触发：收盘有效站上箱顶约1%，且成交量≥20日均量1.3倍。</li><li>失效：收盘跌回箱体中下部，或跌破MA20/箱底。</li><li>T+1：新仓次日才能卖出；涨停或流动性不足时不假设可成交。</li></ul></article>
<article class='card wide'><h2>前15名：横盘潜在突破</h2><div class='table-wrap'><table><thead><tr><th>排名</th><th>代码/名称</th><th>行业</th><th>形态</th><th>评分/等级</th><th>收盘</th><th>箱体振幅</th><th>小实体占比</th><th>量比</th><th>ATR比</th><th>距箱顶</th><th>箱内位置</th><th>RPS120</th><th>ADV20</th></tr></thead><tbody>{''.join(trs)}</tbody></table></div></article>
<article class='card half'><h2>如何理解“可能性”</h2><p>本页的“高/中高”表示在这批通达信趋势股中的相对形态优势，不是“上涨概率”。模型没有把分数校准成胜率，也没有把海外消息或概念题材硬塞进排序。</p><p class='risk'>不操作机会成本：横盘可能继续横盘，过早买入会承担时间成本和假突破；宁可等触发确认，也不把候选当买单。</p></article><article class='card half'><h2>数据与审计</h2><p>本地前复权 G0：{esc(meta['g0'])}。数据源：{esc(meta['source'])}。行业映射：TDX <code>tdxhy.cfg</code> 当前映射；不使用失效的概念/资金流字段。</p><p>本页只覆盖输入 EBK 中可映射到本地沪深A历史的标的，北交所编码已排除。</p></article></section><footer>研究排序，不构成投资建议。报告生成：{esc(meta['generated_at'])}。</footer></main></body></html>"""
    out_path.write_text(html_text, encoding="utf-8")


def main() -> int:
    all_codes, hs_codes, bj_codes = parse_ebk(EBK)
    if not hs_codes:
        raise SystemExit("EBK 未解析出沪深A代码")
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount", "RPS_50", "RPS_120", "RPS_250"]
    data = pd.read_parquet(PARQUET, columns=cols, filters=[("ts_code", "in", hs_codes)])
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    asof = pd.Timestamp(data["trade_date"].max())
    names = load_name_master(HQ)
    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    ind_map = dict(zip(imap["ts_code"], imap["industry"]))
    rows: list[dict] = []
    for code, g in data.groupby("ts_code", sort=False):
        name = names.get(code, "")
        compact = str(name).replace(" ", "").upper()
        if compact.startswith(("ST", "*ST")) or "退" in compact:
            continue
        ind_code = ind_map.get(code, "")
        ind_name = industry_label(ind_code, inames) if ind_code else "未映射"
        rec = score_one(g, asof, name, ind_name)
        # 形态排序前先过硬门：流动性、MA20/MA60 趋势、不过度乖离、横盘宽度。
        # 这些条件不通过时，即使贴近箱顶，也属于追高/低流动性/宽箱体，不纳入前15。
        if rec and (
            rec["adv20_yi"] >= 1.5
            and 0.0 <= rec["vs_ma20_pct"] <= 12.0
            and rec["vs_ma60_pct"] >= 0.0
            and rec["box_range_pct"] <= 22.0
            and rec["close_range_pct"] <= 16.0
            and rec["small_body_share_pct"] >= 45.0
        ):
            rows.append(rec)
    rows.sort(key=lambda x: (x["score"], x["RPS_120"] or -1, x["adv20_yi"]), reverse=True)
    top = rows[:15]
    tag = asof.strftime("%Y%m%d")
    csv_path = OUT / f"ebk_sideways_breakout_{tag}_top15.csv"
    json_path = OUT / f"ebk_sideways_breakout_{tag}.json"
    html_path = OUT / f"a_share_analysis_{tag}_ebk_sideways_breakout.html"
    pd.DataFrame(top).to_csv(csv_path, index=False, encoding="utf-8-sig")
    meta = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": asof.strftime("%Y-%m-%d"), "input_file": str(EBK), "input_count": len(all_codes),
        "bj_count": len(bj_codes), "analyzed_count": int(data["ts_code"].nunique()),
        "scored_count": len(rows), "g0": "verified_qfq / local_operational_consistency",
        "source": "output/tdx_stock_daily_with_rps.parquet（同花顺 v_daily_qfq 导出；量额原始）",
        "strategy": "15日横盘小阴小阳潜在突破相对评分；非校准概率",
        "top15": top, "csv": str(csv_path), "html": str(html_path),
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(meta, top, html_path)
    print(json.dumps({"as_of": meta["as_of"], "input_count": len(all_codes), "bj_excluded": len(bj_codes),
                      "analyzed": meta["analyzed_count"], "scored": len(rows), "top15": top,
                      "csv": str(csv_path), "json": str(json_path), "html": str(html_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
