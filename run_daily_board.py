"""Read-only daily board: market layer + sector whitelist + engine gates + leader zone, one page.

Inputs (all local): TDX daily files, output/market_timing_<date>.json, output/macro_*_<date>.csv.
Missing market-timing / macro outputs are produced first by their own entry scripts.
Outputs: output/daily_board_<date>.json and .html. No orders, no position sizing.

    python run_daily_board.py                 # latest TDX session
    python run_daily_board.py --date 2026-10-23
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# run_local_tdx_compare (the panel's adjuster) imports tmp/ scripts that need build_limit_ecology.
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))

from macro.sector import load_industry_names  # noqa: E402
from rps_analyzer.industry_rps import load_industry_map  # noqa: E402
from run_daily_rps import names_from_tnf, read_day  # noqa: E402
from trend_start.daily_board import (  # noqa: E402
    CHASE_AMOUNT, CHASE_STREAK, CHASE_ZT, ICE_GATE, LEADER_WEIGHTS, OVERHEAT, POOL_DAYS,
    build_recent_panel, circle_pool, engine_days, industry_of, leader_ranking,
    pattern_candidates, whitelist_tag,
)
from trend_start.drop_base_retest import PatternConfig  # noqa: E402
from trend_start.theme_layer import (  # noqa: E402
    concept_catalog, concept_strength, ensure_concept_closes, ensure_limit_pools, ladder, latest_reason,
    reason_heat, strong_stocks,
)

OUT = ROOT / "output"
THEME_CACHE = ROOT / "data/theme_cache"


def theme_layer(as_of: pd.Timestamp, tdx_root: Path, panel, name_of, st: np.ndarray, decorate) -> dict:
    """THS concept strength + limit-up reasons + 10-day strong stocks. Display only; failures degrade."""
    cal = pd.DatetimeIndex(read_day(tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date)
    out: dict = {}
    try:
        closes, info = ensure_concept_closes(THEME_CACHE, as_of, cal, cal[-1])
        cs = concept_strength(closes, concept_catalog(THEME_CACHE))
        out["concepts"] = {"info": info, "n": len(cs), "top": cs.head(15).to_dict("records"),
                           "cooling": cs.tail(8).iloc[::-1].to_dict("records"),
                           "top_day": cs.sort_values("ret1", ascending=False).head(8).to_dict("records")}
    except Exception as exc:  # noqa: BLE001 - board still renders without the theme data
        out["concepts_error"] = str(exc)[:300]
    lr: dict = {}
    try:
        sessions = list(cal[cal <= as_of][-5:])
        pools = ensure_limit_pools(THEME_CACHE, sessions)
        key = str(as_of.date())
        out["reasons"] = {"sessions": sorted(pools), "today_count": len(pools.get(key, [])),
                          "top": reason_heat(pools, key).head(15).to_dict("records")}
        out["ladder"] = ladder(pools.get(key, [])).to_dict("records")
        lr = latest_reason(pools)
    except Exception as exc:  # noqa: BLE001
        out["reasons_error"] = str(exc)[:300]
    strong = strong_stocks(panel.codes, panel.field("close").astype(float), panel.field("amount").astype(float), st)
    strong["last_zt"] = strong.code.map(lambda c: lr.get(c, ("", ""))[0])
    strong["zt_reason"] = strong.code.map(lambda c: lr.get(c, ("", ""))[1])
    out["strong"] = decorate(strong).to_dict("records")
    return out


def ensure_inputs(as_of: pd.Timestamp, tdx_root: Path) -> None:
    tag = f"{as_of:%Y%m%d}"
    if not (OUT / f"market_timing_{tag}.json").exists():
        print("[BOARD] market_timing missing -> running it", flush=True)
        subprocess.run([sys.executable, str(ROOT / "research/legacy_workspace/run_market_timing.py"),
                        "--date", f"{as_of:%Y-%m-%d}", "--tdx-dir", str(tdx_root / "vipdoc")], check=True)
    if not (OUT / f"macro_regime_{tag}.csv").exists():
        print("[BOARD] macro regime missing -> running it", flush=True)
        subprocess.run([sys.executable, str(ROOT / "run_macro_regime.py"), "--date", tag,
                        "--tdx-dir", str(tdx_root / "vipdoc"), "--incon", str(tdx_root / "incon.dat"),
                        "--skip-moneyflow", "--skip-geo", "--no-open"], check=True)


def market_layer(as_of: pd.Timestamp) -> dict:
    tag = f"{as_of:%Y%m%d}"
    mt = json.loads((OUT / f"market_timing_{tag}.json").read_text(encoding="utf-8"))
    L = mt["latest"]
    keys = ("breadth", "up", "down", "amount_yi", "ma20_pct", "ma60_pct", "high60", "low60",
            "bottom_state", "oversold_votes", "vol_temp", "vol_state", "vol_direction", "whipsaw",
            "csi300_breadth", "csi1000_breadth", "core_warning", "index_close", "divergence")
    regime = pd.read_csv(OUT / f"macro_regime_{tag}.csv").iloc[-1].to_dict()
    conf = {(s["signal"], s["horizon"]): s for s in mt.get("bottom_study", [])}
    return {"timing": {k: L.get(k) for k in keys}, "limit_official": mt.get("limit_official"),
            "regime": regime, "bottom_20d": conf.get(("bottom_confirmed", 20)),
            "baseline_20d": conf.get(("baseline", 20)), "vol_buckets": (mt.get("vol_study") or {}).get("buckets")}


def sector_layer(as_of: pd.Timestamp, tdx_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Names come from TDX incon.dat: the macro CSVs carry fallback names that do not match
    current TDX industry codes when run without --incon (e.g. T1001 is 银行, not 国防军工)."""
    tag = f"{as_of:%Y%m%d}"
    incon = load_industry_names(tdx_root / "incon.dat")
    sectors = pd.read_csv(OUT / f"sector_weekly_{tag}.csv").drop_duplicates("industry").set_index("industry")
    white = pd.read_csv(OUT / f"sector_whitelist_{tag}.csv").drop_duplicates("industry").set_index("industry")
    for df in (sectors, white):
        df["name"] = [incon.get(c, n) for c, n in zip(df.index, df["name"])]
    return sectors, white


def build(as_of: pd.Timestamp, tdx_root: Path) -> dict:
    ensure_inputs(as_of, tdx_root)
    market = market_layer(as_of)
    sectors, white = sector_layer(as_of, tdx_root)
    print("[BOARD] building recent panel", flush=True)
    panel, audit = build_recent_panel(tdx_root, as_of)
    names = names_from_tnf(tdx_root / "T0002/hq_cache")
    name_of = lambda c: names.get(str(c), "")  # noqa: E731 - keys are "600000.SH"
    st = np.array(["ST" in name_of(c).upper() or "退" in name_of(c) for c in panel.codes])
    ind_map = load_industry_map(tdx_root / "T0002/hq_cache/tdxhy.cfg")
    incon = load_industry_names(tdx_root / "incon.dat")

    def decorate(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.assign(name=[], industry_name=[], quadrant=[], in_whitelist=[], st=[])
        df = df.copy()
        df["name"] = df.code.map(name_of)
        df["st"] = df.name.str.upper().str.contains("ST") | df.name.str.contains("退")
        ind = industry_of(df.code, ind_map)
        tags = whitelist_tag(ind, sectors, set(white.index.astype(str)))
        tags["industry_name"] = [incon.get(c) or n for c, n in zip(ind, tags.industry_name)]
        return pd.concat([df, tags], axis=1)

    days = engine_days(panel)
    today = days.iloc[-1]
    print("[BOARD] scanning patterns", flush=True)
    pats = decorate(pattern_candidates(panel, PatternConfig()))
    last = len(panel.dates) - 1
    traded = np.isfinite(panel.field("close")[:, last])
    pool = circle_pool(panel)
    pool = pool.loc[pool.code.isin(set(panel.codes[traded].astype(str)))]
    pool = decorate(pool.merge(pd.DataFrame({"code": panel.codes.astype(str),
                                             "close": panel.field("close")[:, last]}), on="code"))
    leaders = leader_ranking(panel, st)
    leader_meta = dict(leaders.attrs)
    leaders = decorate(leaders.head(40))
    print("[BOARD] theme layer (THS)", flush=True)
    theme = theme_layer(as_of, tdx_root, panel, name_of, st, decorate)

    recent = days.tail(10).reset_index().rename(columns={"index": "trade_date"})
    recent["trade_date"] = recent.trade_date.dt.strftime("%Y-%m-%d")
    engines = {
        "ice_engine_on": bool(today.ice_engine_on), "chase_engine_on": bool(today.chase_engine_on),
        "ice_gate": bool(today.ice_gate), "overheat": bool(today.overheat),
        "repair": bool(today.repair), "reversal": bool(today.reversal), "ice_level": int(today.ice_level),
        "advance_share": float(today.advance_share), "zt": float(today.zt_count), "dt": float(today.dt_count),
        "max_streak": float(today.max_streak), "amount_vs_prev5": float(today.amount_vs_prev5),
    }
    return {
        "asof": f"{as_of:%Y-%m-%d}",
        "generated": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "panel": audit, "market": market, "theme": theme,
        "sectors": {"whitelist": white.reset_index().to_dict("records"),
                    "top": sectors.sort_values("score", ascending=False).head(12).reset_index().to_dict("records")},
        "engines": engines, "recent_days": recent.to_dict("records"),
        "candidates": {"pool_ice": pool.sort_values("sessions_since_circle").to_dict("records"),
                       "pattern_quiet": pats.loc[pats.kind == "quiet"].to_dict("records"),
                       "chase": pats.loc[pats.kind.isin(["breakout", "confirmed"])].to_dict("records")},
        "leaders": {"meta": leader_meta, "rows": leaders.to_dict("records")},
        "rules": {"ice_gate": f"上涨家数占比≤{ICE_GATE.advance_thr:.0%}连续≥{ICE_GATE.min_streak}日，且前一日≤35%（由弱转更弱）；当日上涨≥{OVERHEAT:.0%}为过热不开",
                  "chase": f"前一日冰点后的修复日，成交额≥前5日均×{CHASE_AMOUNT}，涨停≥{CHASE_ZT}，连板高度>{CHASE_STREAK}",
                  "pool_days": POOL_DAYS, "leader_weights": LEADER_WEIGHTS},
    }


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, pd.Timestamp):
        return x.isoformat()[:10]
    return x


# ---------------------------------------------------------------- rendering
E = html.escape


def fmt(v, n=1, pct=False, sign=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        s = f"{v * 100 if pct else v:{'+' if sign else ''}.{n}f}"
        return s + ("%" if pct else "")
    return E(str(v))


def table(heads, rows, cls=""):
    th = "".join(f"<th>{E(h)}</th>" for h in heads)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body or "<tr><td colspan=99>无</td></tr>"}</tbody></table></div>'


def stock_rows(rows, extra):
    out = []
    for r in rows:
        flag = "".join([' <span class="tag warn">ST</span>' if r.get("st") else "",
                        ' <span class="tag">白名单</span>' if r.get("in_whitelist") else "",
                        ' <span class="tag warn">涨停买不进</span>' if r.get("at_limit_up") else ""])
        out.append([E(r["code"]), E(r.get("name") or "") + flag, E(r.get("industry_name") or "未匹配"),
                    E(r.get("quadrant") or "—")] + [f(r) for f in extra])
    return out


def render_theme(th: dict, stock_rows) -> tuple[str, str]:
    """Section HTML and a one-line addition for the header summary."""
    c, rs = th.get("concepts") or {}, th.get("reasons") or {}
    pct = lambda v: fmt(v, 2, sign=True) + "%" if isinstance(v, (int, float)) else "—"  # noqa: E731
    con = lambda rows: [[E(r["name"]), pct(r["ret1"]), pct(r["ret5"]), pct(r["ret20"]), str(r["rank5"])] for r in rows]  # noqa: E731
    parts = ['<section id="theme"><h2>①b 短线主线题材（同花顺，只展示）</h2>']
    if c:
        info = c["info"]
        parts.append(f'<p>概念指数 {c["n"]} 个可算（目录 {info["concepts"]} 个，数据来源：{E(info["source"])}），按近5日涨幅排序。</p>')
        parts.append("<h3>概念指数近5日最强</h3>" + table(["概念", "今日", "近5日", "近20日", "5日排名"], con(c["top"])))
        parts.append("<h3>今日涨幅最大的概念</h3>" + table(["概念", "今日", "近5日", "近20日", "5日排名"], con(c["top_day"])))
        parts.append("<h3>近5日降温最快</h3>" + table(["概念", "今日", "近5日", "近20日", "5日排名"], con(c["cooling"])))
    else:
        parts.append(f'<p class="note warn-note">概念指数未取得：{E(th.get("concepts_error", ""))}</p>')
    if rs:
        rows = [[E(r["keyword"]), str(r["today"]), str(r["window"]), f'{r["days"]}/{len(rs["sessions"])}', E(r["today_stocks"])] for r in rs["top"]]
        parts.append(f"<h3>涨停原因热词（今日涨停 {rs['today_count']} 只，统计近 {len(rs['sessions'])} 个交易日）</h3>"
                     + table(["热词", "今日涨停数", "窗口内涨停次数", "出现天数", "今日涨停股（前6）"], rows))
        lad = [[E(r["thscode"]), E(r["name"]), f'{int(r["continue_day_cnt"])}板', E(r["limit_up_time"] or ""), E(r["limit_up_reason"] or "")]
               for r in th.get("ladder", [])]
        parts.append("<h3>连板梯队（2板及以上）</h3>" + table(["代码", "名称", "连板", "封板时间", "涨停原因"], lad))
    else:
        parts.append(f'<p class="note warn-note">涨停池未取得：{E(th.get("reasons_error", ""))}</p>')
    strong = th.get("strong") or []
    parts.append("<h3>短线强势股（近10日涨幅前25，日均成交额≥1亿，剔除ST）</h3>" + table(
        ["代码", "名称", "行业", "板块象限", "近10日", "近5日", "今日", "最近涨停日", "涨停原因"],
        stock_rows(strong, [lambda r: pct(r.get("ret10")), lambda r: pct(r.get("ret5")), lambda r: pct(r.get("ret1")),
                            lambda r: E(r.get("last_zt") or "—"), lambda r: E(r.get("zt_reason") or "")])))
    parts.append('<p class="note warn-note">本区块回答“资金在炒什么”，不回答“该买什么”。历史回测里，次日开盘追涨停股、追突破都是负收益，'
                 '修复日买当天最强的股票也没有稳定跑赢；本区块不进入任何引擎或信号。概念指数成分由同花顺维护、会调整，涨停原因为同花顺标注。</p></section>')
    hot = [r["keyword"] for r in (rs.get("top") or [])[:3]]
    top = [r["name"] for r in (c.get("top") or [])[:3]]
    line = (f"主线热词：{'、'.join(map(E, hot))}；" if hot else "") + (f"近5日最强概念：{'、'.join(map(E, top))}。" if top else "")
    return "".join(parts), line


def render(b: dict) -> str:
    m, t, rg, en = b["market"], b["market"]["timing"], b["market"]["regime"], b["engines"]
    lo = m.get("limit_official") or {}
    white = b["sectors"]["whitelist"]
    on = lambda x: '<b class="on">开启</b>' if x else '<b class="off">未开启</b>'  # noqa: E731
    bc, bl = m.get("bottom_20d") or {}, m.get("baseline_20d") or {}
    summary = (f"底部温度计「{fmt(t['bottom_state'])}」，波动温度 {fmt(t['vol_temp'])}（{fmt(t['vol_state'])}，{fmt(t['vol_direction'])}），"
               f"宏观九宫格「{fmt(rg['regime_name'])}」；冰点引擎{'开启' if en['ice_engine_on'] else '未开启'}，"
               f"修复追高引擎{'开启' if en['chase_engine_on'] else '未开启'}；白名单行业 {len(white)} 个。")
    cards = [
        ("底部温度计", fmt(t["bottom_state"]), f"超卖投票 {fmt(t['oversold_votes'], 0)}/4；推力确认后20日上证上涨频率 {fmt(bc.get('index_up_pct'), 0)}%（全部交易日 {fmt(bl.get('index_up_pct'), 0)}%，{fmt(bc.get('observations'), 0)}个样本）"),
        ("波动温度计", f"{fmt(t['vol_temp'])}（{fmt(t['vol_state'])}）", f"{fmt(t['vol_direction'])}；近5日宽度剧烈翻转：{fmt(t['whipsaw'])}。只预示波动大小，不预示方向"),
        ("宏观九宫格", fmt(rg["regime_name"]), f"模型目标仓位 {fmt(rg['target_position'], 0, pct=True)}；趋势分 {fmt(rg['trend_score'])}、温度分 {fmt(rg['heat_score'])}。锚点未经真实数据标定，仅作参考"),
        ("市场宽度", fmt(t["breadth"], pct=True), f"上涨 {fmt(t['up'], 0)} / 下跌 {fmt(t['down'], 0)}；站上MA20 {fmt(t['ma20_pct'])}%；沪深300成分 {fmt(t['csi300_breadth'], pct=True)} vs 中证1000成分 {fmt(t['csi1000_breadth'], pct=True)}"),
        ("新高新低（60日）", f"{fmt(t['high60'], 0)} / {fmt(t['low60'], 0)}", f"同花顺官方：涨停 {fmt(lo.get('limit_up'), 0)}、跌停 {fmt(lo.get('limit_down'), 0)}、炸板 {fmt(lo.get('limit_broken'), 0)}"),
        ("引擎", f"冰点 {'开' if en['ice_engine_on'] else '关'} · 修复 {'开' if en['chase_engine_on'] else '关'}", f"冰点等级 {en['ice_level']}；修复日 {fmt(en['repair'])}；成交额/前5日 {fmt(en['amount_vs_prev5'], 2)}；涨停 {fmt(en['zt'], 0)}，连板高度 {fmt(en['max_streak'], 0)}"),
    ]
    card_html = "".join(f'<div class="card"><div class="k">{E(k)}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for k, v, s in cards)

    wl_rows = [[E(r.get("name") or r["industry"]), E(r["industry"]), E(r.get("quadrant_cn") or ""), fmt(r.get("score")),
                fmt(r.get("ret_short"), pct=True, sign=True), fmt(r.get("ret_mid"), pct=True, sign=True), E(r.get("note") or "")] for r in white]
    top_rows = [[E(r.get("name") or r["industry"]), E(r["industry"]), E(r.get("quadrant_cn") or ""), fmt(r.get("score")),
                 fmt(r.get("S1_rps_mid")), fmt(r.get("S3_accel"), sign=True), fmt(r.get("S2_breadth"), pct=True)] for r in b["sectors"]["top"]]

    day_rows = [[E(d["trade_date"]), fmt(d["advance_share"], pct=True), fmt(d["zt_count"], 0), fmt(d["dt_count"], 0),
                 fmt(d["max_streak"], 0), fmt(d["broken_rate"], pct=True), fmt(d["amount_vs_prev5"], 2), str(d["ice_level"]),
                 fmt(d["ice_gate"]), fmt(d["overheat"]), fmt(d["repair"]), on(d["ice_engine_on"]), on(d["chase_engine_on"])]
                for d in b["recent_days"]][::-1]

    cand = b["candidates"]
    px = lambda r: fmt(r.get("close"), 2)  # noqa: E731
    pool_tbl = table(["代码", "名称", "行业", "板块象限", "收盘", "距画圈（交易日）"],
                     stock_rows(cand["pool_ice"][:60], [px, lambda r: fmt(r.get("sessions_since_circle"), 0)]))
    pat_extra = [px, lambda r: fmt(r.get("pct"), 2, sign=True), lambda r: fmt(r.get("pivot"), 2),
                 lambda r: fmt(r.get("floor"), 2), lambda r: fmt(r.get("amount_yi"), 2)]
    quiet_tbl = table(["代码", "名称", "行业", "板块象限", "收盘", "涨跌幅%", "枢轴", "失效位", "成交额（亿）"], stock_rows(cand["pattern_quiet"], pat_extra))
    chase_tbl = table(["代码", "名称", "行业", "板块象限", "收盘", "涨跌幅%", "枢轴", "失效位", "成交额（亿）", "形态"],
                      stock_rows(cand["chase"], pat_extra + [lambda r: {"breakout": "放量突破", "confirmed": "突破确认"}.get(r.get("kind"), r.get("kind"))]))

    lm = b["leaders"]["meta"]
    lead_tbl = table(["代码", "名称", "行业", "板块象限", "得分", "RPS120", "离20周线", "周低离20周线", "缩量比", "52周涨幅", "站上20周线（周）", "近4周RPS≥90周数"],
                     stock_rows(b["leaders"]["rows"][:30], [lambda r: fmt(r.get("score"), 3), lambda r: fmt(r.get("rps120"), 1),
                                                            lambda r: fmt(r.get("close_recover"), pct=True, sign=True), lambda r: fmt(r.get("low_touch"), pct=True, sign=True),
                                                            lambda r: fmt(r.get("vol_dry"), 2), lambda r: fmt(r.get("ret52"), pct=True),
                                                            lambda r: fmt(r.get("trend_age"), 0), lambda r: fmt(r.get("rps_weeks_4"), 0)]))
    status_note = lambda flag, name: (f'今日<b class="on">{name}开启</b>，下列为同日按研究口径成立的形态，供复核，不是买入清单。' if flag  # noqa: E731
                                      else f'今日<b class="off">{name}未开启</b>，下列仅为形态观察；按研究口径今天不会入场。')
    rules = b["rules"]
    weights = "，".join(f"{k} {v:+.2f}" for k, v in rules["leader_weights"].items())
    theme_html, theme_summary = render_theme(b.get("theme") or {}, stock_rows)
    summary += theme_summary
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>每日综合看板</title><style>
:root{{--bg:#f2f5f8;--ink:#23364a;--muted:#687c8f;--card:#fff;--line:#e0e7ee;--on:#bd3f4a;--off:#687c8f;--accent:#162f46}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1240px;margin:auto;padding:20px 16px}}
header{{background:var(--accent);color:#fff;border-radius:12px;padding:24px}}header .s{{color:#d2deea;font-size:13px}}h1{{font-size:26px;margin:6px 0}}h2{{font-size:20px;margin:0 0 8px}}h3{{font-size:16px;margin:18px 0 6px}}
section{{background:var(--card);border-radius:10px;padding:20px;margin-top:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
.card{{border-left:3px solid #94adc3;padding:8px 12px}}.card .k{{color:var(--muted);font-size:13px}}.card .v{{font-size:22px;font-weight:700}}.card .s{{font-size:13px;color:var(--muted)}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-weight:600}}
.on{{color:var(--on)}}.off{{color:var(--off)}}.tag{{font-size:11px;padding:1px 6px;border-radius:8px;background:#e7eef6;margin-left:4px}}.tag.warn{{background:#fff1dc}}
.note{{background:#edf3f8;padding:10px 14px;border-radius:6px;margin:10px 0}}.warn-note{{background:#fff1dc}}nav{{display:flex;gap:16px;flex-wrap:wrap;margin:14px 0 0}}nav a{{color:#235e97}}
</style></head><body><main>
<header><div class="s">截至 {E(b['asof'])} 收盘 · 生成于 {E(b['generated'])} · 只读看板，不产生订单</div><h1>每日综合看板</h1><p>{summary}</p></header>
<nav><a href="#market">市场层</a><a href="#theme">短线主线题材</a><a href="#sector">板块白名单</a><a href="#engine">冰点与修复引擎</a><a href="#leader">领涨区排名</a><a href="#method">口径</a></nav>
<section id="market"><h2>① 市场层</h2><div class="grid">{card_html}</div>
<p class="note warn-note">顶部背离预警在3年置换检验中没有预测力（p≥0.31），本页不展示为信号。九宫格目标仓位来自未标定锚点，波动温度计用于风险预算参考，均不是本页的买卖结论。</p></section>
{theme_html}
<section id="sector"><h2>② 板块白名单（周度，宏观模块）</h2>{table(['行业', '代码', '象限', '总分', '20日涨', '中期涨', '说明'], wl_rows)}
<h3>板块强度前12</h3>{table(['行业', '代码', '象限', '总分', '中期RPS', '加速', '宽度'], top_rows)}
<p class="note">白名单混合了不同层级的通达信行业代码；个股按行业代码前缀归属。</p></section>
<section id="engine"><h2>③ 冰点与修复引擎（冠军组合F的定义，冻结不改）</h2>
<p>冰点闸门：{E(rules['ice_gate'])}。修复追高：{E(rules['chase'])}。组合F的优先级：圈池冰点 &gt; 形态冰点 &gt; 修复追高，共用20个仓位。</p>
{table(['日期', '上涨占比', '涨停', '跌停', '连板高度', '炸板率', '成交额/前5日', '冰点等级', '冰点闸门', '过热', '修复日', '冰点引擎', '修复引擎'], day_rows)}
<p class="note">涨跌停按板块规则近似（ST 5%未区分），与同花顺官方数可能有出入。</p>
<h3>圈池冰点：火车轨画圈后{rules['pool_days']}日内的观察池（{len(cand['pool_ice'])}只，按画圈远近显示前60只）</h3><p>{status_note(en['ice_engine_on'], '冰点引擎')}</p>{pool_tbl}
<h3>形态冰点：缩量回踩形态（{len(cand['pattern_quiet'])}只）</h3><p>{status_note(en['ice_engine_on'], '冰点引擎')}</p>{quiet_tbl}
<h3>修复追高：放量突破 / 突破确认形态（{len(cand['chase'])}只）</h3><p>{status_note(en['chase_engine_on'], '修复引擎')}</p>{chase_tbl}</section>
<section id="leader"><h2>④ 领涨区排名（周度）</h2>
<p>周截至 {E(lm.get('week_end', ''))}{'' if lm.get('week_complete') else '（本周未完结，按截至今日计算）'}；领涨区共 {lm.get('zone_size', 0)} 只：RPS120≥90、周收盘在20周线上、10周线在20周线上、距52周高点≥70%，剔除ST。按权重打分取前30。</p>
{lead_tbl}<p class="note">权重（来自已看过的2024-10至2026-08历史，属规则成型证据，非样本外证明）：{E(weights)}。负权重表示该值越小越优先，即“刚够格、贴线、未发散、缩量、涨幅小”。分数用于排序，不作阈值开关。</p></section>
<section id="method"><h2>⑤ 口径与边界</h2>
<p>价格：通达信日线，按 gbbq 除权事件做等比前复权，与冻结研究的面板同一路径；本页只取最近 {b['panel']['sessions']} 个交易日（{E(b['panel']['first_session'])} 起），保留 {b['panel']['kept']} 只，读取失败 {b['panel']['excluded']} 只。市场层读取 market_timing_{E(b['asof'].replace('-', ''))}.json；板块与九宫格读取宏观模块同日输出。</p>
<p>冠军组合F在已看过的2022-01至2026-09历史上为年化+6.5%、最大回撤−17.3%；这些数字来自已访问数据，不是独立样本外。本页不计算仓位、不生成订单，引擎开启也只表示研究定义成立。</p></section>
</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    args = ap.parse_args()
    if args.date:
        as_of = pd.Timestamp(args.date)
    else:
        as_of = pd.Timestamp(read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date.iloc[-1])
    board = clean(build(as_of, args.tdx_root))
    tag = f"{as_of:%Y%m%d}"
    (OUT / f"daily_board_{tag}.json").write_text(json.dumps(board, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    hp = OUT / f"daily_board_{tag}.html"
    hp.write_text(render(board), encoding="utf-8")
    e = board["engines"]
    print(json.dumps({"report": str(hp), "ice_engine_on": e["ice_engine_on"], "chase_engine_on": e["chase_engine_on"],
                      "pool": len(board["candidates"]["pool_ice"]), "quiet": len(board["candidates"]["pattern_quiet"]),
                      "chase": len(board["candidates"]["chase"]), "leader_zone": board["leaders"]["meta"].get("zone_size")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
