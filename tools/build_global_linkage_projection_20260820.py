#!/usr/bin/env python3
"""Build a self-contained A-share / North Asia / US linkage scenario report.

The report deliberately avoids gated cross-day stock metrics when the local
forward-adjustment reconciliation has not passed.  A-share industry evidence is
therefore limited to same-session open-to-close structure and the separately
validated local limit-up ecology.
"""

from __future__ import annotations

import html
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / ".agents" / "skills" / "a-share-rotation-trader" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from build_limit_ecology import load_name_master, load_tail, mark_limits  # noqa: E402
from run_integrated_decision import (  # noqa: E402
    industry_label,
    load_industry_map,
    load_industry_names,
)


TRADE_DATE = "20260820"
TDX_ROOT = Path(r"C:\new_tdx\vipdoc")
HQ_CACHE = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
OUT_HTML = ROOT / "output" / "a_share_global_linkage_projection_20260820.html"
OUT_AUDIT = ROOT / "output" / "a_share_global_linkage_projection_20260820_audit.json"

YAHOO_SYMBOLS = {
    "^N225": "日经225",
    "^KS11": "韩国KOSPI",
    "^TWII": "台湾加权",
    "000660.KS": "SK海力士",
    "005930.KS": "三星电子",
    "2330.TW": "台积电",
    "2454.TW": "联发科",
    "6857.T": "爱德万测试",
    "8035.T": "东京电子",
    "9984.T": "软银集团",
    "^GSPC": "标普500",
    "^IXIC": "纳斯达克综合",
    "^DJI": "道琼斯",
    "^SOX": "费城半导体",
    "NVDA": "英伟达",
    "MU": "美光科技",
    "AVGO": "博通",
    "AMD": "AMD",
}

TENCENT_INDEX = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "未取得"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fetch_yahoo(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        "?range=5d&interval=1d&includePrePost=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    closes = [x for x in result["indicators"]["quote"][0]["close"] if x is not None]
    if len(closes) < 2:
        raise RuntimeError(f"{symbol}: fewer than two daily closes")
    price = float(meta["regularMarketPrice"])
    previous = float(closes[-2])
    timestamp = int(meta["regularMarketTime"])
    utc = datetime.fromtimestamp(timestamp).astimezone()
    return {
        "symbol": symbol,
        "label": YAHOO_SYMBOLS[symbol],
        "price": price,
        "previous": previous,
        "change_pct": (price / previous - 1) * 100,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "exchange_timezone": meta.get("timezone"),
        "market_timestamp": timestamp,
        "market_time_iso": utc.isoformat(timespec="seconds"),
        "source_url": url,
    }


def fetch_global_quotes() -> tuple[dict[str, dict], list[str]]:
    quotes: dict[str, dict] = {}
    errors: list[str] = []
    for symbol in YAHOO_SYMBOLS:
        try:
            quotes[symbol] = fetch_yahoo(symbol)
        except Exception as exc:  # source errors are disclosed, never imputed
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
    return quotes, errors


def fetch_tencent_indices() -> tuple[dict[str, dict], list[str]]:
    query = ",".join(TENCENT_INDEX)
    url = f"https://qt.gtimg.cn/q={query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://stockapp.finance.qq.com/"},
    )
    errors: list[str] = []
    result: dict[str, dict] = {}
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("gbk", errors="replace")
    except Exception as exc:
        return {}, [f"Tencent indices: {type(exc).__name__}: {exc}"]
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        code = key.removeprefix("v_")
        fields = raw.strip().strip(";\"").split("~")
        try:
            quote_time = fields[30]
            if not quote_time.startswith(TRADE_DATE):
                raise ValueError(f"returned date {quote_time[:8]}")
            result[code] = {
                "code": code,
                "label": TENCENT_INDEX.get(code, fields[1]),
                "price": float(fields[3]),
                "previous": float(fields[4]),
                "change": float(fields[31]),
                "change_pct": float(fields[32]),
                "high": float(fields[33]),
                "low": float(fields[34]),
                "quote_time": quote_time,
                "source_url": url,
            }
        except (IndexError, ValueError) as exc:
            errors.append(f"{code}: {exc}")
    return result, errors


def build_intraday_industries() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    daily = load_tail(TDX_ROOT, n_bars=70, min_bars=2, verbose=False)
    latest_date = daily["trade_date"].max()
    if latest_date.strftime("%Y%m%d") != TRADE_DATE:
        raise RuntimeError(f"TDX latest date {latest_date:%Y%m%d}, expected {TRADE_DATE}")

    names = load_name_master(HQ_CACHE)
    marked = mark_limits(daily, names)
    day = marked[marked["trade_date"] == latest_date].copy()
    day = day[(day["open"] > 0) & (day["close"] > 0) & (day["vol"] > 0)].copy()
    day["intraday_return_pct"] = (day["close"] / day["open"] - 1) * 100
    span = (day["high"] - day["low"]).replace(0, np.nan)
    day["close_location_pct"] = ((day["close"] - day["low"]) / span * 100).clip(0, 100)
    day["name"] = day["ts_code"].map(names).fillna(day["ts_code"])

    imap = load_industry_map(HQ_CACHE / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    day = day.merge(imap, on="ts_code", how="inner")
    day["industry_name"] = [industry_label(code, inames) for code in day["industry"]]
    rows: list[dict] = []
    for label, grp in day.groupby("industry_name"):
        if len(grp) < 8:
            continue
        amount = float(grp["amount"].sum())
        positive_amount = float(grp.loc[grp["intraday_return_pct"] > 0, "amount"].sum())
        rows.append({
            "industry": label,
            "name": label,
            "members": int(len(grp)),
            "median_intraday_pct": float(grp["intraday_return_pct"].median()),
            "breadth_intraday_pct": float((grp["intraday_return_pct"] > 0).mean() * 100),
            "positive_amount_share_pct": float(positive_amount / amount * 100) if amount else np.nan,
            "median_close_location_pct": float(grp["close_location_pct"].median()),
            "amount_yi": amount / 1e8,
            "limit_up_n": int(grp["is_limit_up"].sum()),
            "broken_n": int(grp["broken"].sum()),
            "top1_amount_share_pct": float(grp["amount"].max() / amount * 100) if amount else np.nan,
        })
    industries = pd.DataFrame(rows)
    score_cols = [
        "median_intraday_pct",
        "breadth_intraday_pct",
        "positive_amount_share_pct",
        "median_close_location_pct",
    ]
    for col in score_cols:
        industries[f"rank_{col}"] = industries[col].rank(pct=True) * 100
    industries["intraday_structure_score"] = industries[[f"rank_{c}" for c in score_cols]].mean(axis=1)
    industries["single_stock_driven"] = industries["top1_amount_share_pct"] > 35
    industries = industries.sort_values(
        ["single_stock_driven", "intraday_structure_score"], ascending=[True, False]
    ).reset_index(drop=True)
    industries["rank"] = np.arange(1, len(industries) + 1)

    top_names = industries.head(8)["name"].tolist()
    stocks = day[day["industry_name"].isin(top_names) & ~day["is_risk"]].copy()
    stocks["industry_rank"] = stocks["industry_name"].map(
        industries.set_index("name")["rank"].to_dict()
    )
    stocks["amount_yi"] = stocks["amount"] / 1e8
    stocks = stocks.sort_values(
        ["industry_rank", "is_limit_up", "intraday_return_pct", "amount"],
        ascending=[True, False, False, False],
    )
    stock_rows = []
    for _, grp in stocks.groupby("industry_name", sort=False):
        liquid = grp.sort_values("amount", ascending=False).head(2)
        strong = grp.sort_values(["is_limit_up", "intraday_return_pct", "amount"], ascending=False).head(2)
        stock_rows.append(pd.concat([liquid, strong]).drop_duplicates("ts_code").head(3))
    stock_view = pd.concat(stock_rows, ignore_index=True) if stock_rows else pd.DataFrame()
    meta = {
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "coverage_stocks": int(day["ts_code"].nunique()),
        "industry_count": int(len(industries)),
        "method": "同日开盘至收盘中位涨幅、上涨宽度、上涨股票成交额占比、收盘位置四项等权横截面分位；非回测模型，仅作当日结构描述",
    }
    return industries, stock_view, meta


def market_row(q: dict, state: str = "") -> str:
    change = q.get("change_pct")
    cls = "up" if change is not None and change > 0 else "down" if change is not None and change < 0 else "flat"
    return (
        f"<tr><td><b>{esc(q.get('label'))}</b></td><td class='{cls}'>{pct(change)}</td>"
        f"<td>{q.get('price', 0):,.2f}</td><td>{esc(state)}</td><td>{esc(q.get('market_time_iso') or q.get('quote_time'))}</td></tr>"
    )


def make_html(audit: dict, industries: pd.DataFrame, stocks: pd.DataFrame) -> str:
    g = audit["global_quotes"]
    a = audit["a_share_indices"]
    eco = audit["sentiment"]
    qfq = audit["qfq"]
    generated = audit["generated_at_bjt"]

    asia_rows = "".join(market_row(g[s]) for s in ("^N225", "^KS11", "^TWII") if s in g)
    us_rows = "".join(market_row(g[s], "美股盘中") for s in ("^GSPC", "^IXIC", "^DJI", "^SOX") if s in g)
    a_rows = "".join(market_row(a[s], "A股已收盘") for s in TENCENT_INDEX if s in a)

    mapped_mask = industries["name"].str.contains("半导体|元器件", regex=True, na=False)
    industry_display = pd.concat([industries.head(10), industries[mapped_mask]]).drop_duplicates("name")
    semiconductor_rows = industries[industries["name"].str.contains("半导体", na=False)]
    component_rows = industries[industries["name"].str.contains("元器件", na=False)]
    semiconductor = semiconductor_rows.iloc[0].to_dict() if not semiconductor_rows.empty else {}
    components = component_rows.iloc[0].to_dict() if not component_rows.empty else {}

    ind_rows = []
    for _, r in industry_display.iterrows():
        tag = "日内第一行业" if r["rank"] == 1 else "日内前列"
        if r["rank"] > 10 and ("半导体" in r["name"] or "元器件" in r["name"]):
            tag = "海外映射行业｜内部偏弱"
        if r["single_stock_driven"]:
            tag = "单股驱动降级"
        ind_rows.append(
            "<tr>"
            f"<td>{int(r['rank'])}</td><td><b>{esc(r['name'])}</b><small>{esc(tag)}</small></td>"
            f"<td class={'up' if r['median_intraday_pct'] > 0 else 'down'}>{pct(r['median_intraday_pct'])}</td>"
            f"<td>{r['breadth_intraday_pct']:.0f}%</td><td>{r['positive_amount_share_pct']:.0f}%</td>"
            f"<td>{r['median_close_location_pct']:.0f}%</td><td>{r['amount_yi']:.0f}亿</td>"
            f"<td>{int(r['limit_up_n'])}/{int(r['broken_n'])}</td><td>{r['top1_amount_share_pct']:.1f}%</td>"
            "</tr>"
        )

    stock_rows = []
    for _, r in stocks.iterrows():
        stock_rows.append(
            "<tr>"
            f"<td>{esc(r['industry_name'])}</td><td><b>{esc(r['name'])}</b><small>{esc(r['ts_code'])}</small></td>"
            f"<td class={'up' if r['intraday_return_pct'] > 0 else 'down'}>{pct(r['intraday_return_pct'])}</td>"
            f"<td>{r['amount_yi']:.1f}亿</td><td>{r['close_location_pct']:.0f}%</td>"
            f"<td>{'封板' if r['is_limit_up'] else '触板未封' if r['broken'] else '未封板'}</td>"
            "<td>结构锚点，仅观察；复权闸门失败，不是可执行候选</td>"
            "</tr>"
        )

    raw = eco["raw"]
    korea = g.get("^KS11", {})
    hynix = g.get("000660.KS", {})
    samsung = g.get("005930.KS", {})
    sox = g.get("^SOX", {})
    micron = g.get("MU", {})

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全球联动下的A股推演 · 2026-08-20</title>
<style>
:root{{--ink:#172033;--muted:#667085;--paper:#f5f7fb;--card:#fff;--line:#dde3ec;--navy:#101827;--red:#d92d20;--green:#07883f;--amber:#b54708;--blue:#175cd3;--violet:#6938ef}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.58 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}.wrap{{max-width:1440px;margin:auto;padding:22px}}header{{background:linear-gradient(135deg,#101827,#243452);color:#fff;border-radius:22px;padding:26px 30px;box-shadow:0 16px 40px #10182820}}h1{{font-size:30px;margin:4px 0 8px}}h2{{font-size:20px;margin:0 0 14px}}h3{{font-size:16px;margin:0 0 8px}}p{{margin:7px 0}}small{{display:block;color:var(--muted);font-size:12px}}header small{{color:#c8d3e5}}.kicker{{font-weight:800;color:#9cc2ff;letter-spacing:.08em}}.hero{{display:grid;grid-template-columns:1.2fr .8fr;gap:18px;margin-top:18px}}.decision{{background:#fff;color:var(--ink);border-radius:16px;padding:18px}}.decision strong{{font-size:24px}}.badge{{display:inline-flex;padding:5px 10px;border-radius:999px;font-weight:750;background:#fff2ea;color:#b54708;margin:3px 5px 3px 0}}.badge.stop{{background:#fee4e2;color:#b42318}}.badge.info{{background:#eaf2ff;color:#175cd3}}nav{{margin:15px 0 4px}}nav a{{color:#d5e4ff;margin-right:14px;text-decoration:none}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;margin-top:16px}}.card{{grid-column:span 12;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 6px 20px #1018280a}}.half{{grid-column:span 6}}.third{{grid-column:span 4}}.metric{{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:8px 0}}.metric:last-child{{border:0}}.metric b{{font-variant-numeric:tabular-nums}}.up{{color:var(--red);font-weight:750}}.down{{color:var(--green);font-weight:750}}.flat{{color:var(--muted)}}.warn{{border-left:4px solid #f79009;background:#fffaeb;padding:12px 14px;border-radius:8px}}.stopbox{{border-left:4px solid #f04438;background:#fff1f0;padding:12px 14px;border-radius:8px}}.goodbox{{border-left:4px solid #2e90fa;background:#eff8ff;padding:12px 14px;border-radius:8px}}.tablebox{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{text-align:left;border-bottom:1px solid var(--line);padding:10px 9px;vertical-align:top}}th{{color:#475467;background:#f8fafc;position:sticky;top:0}}.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;align-items:stretch}}.node{{border:1px solid var(--line);border-radius:14px;padding:13px;background:#fafcff;position:relative}}.node:not(:last-child):after{{content:"→";position:absolute;right:-14px;top:38%;color:#98a2b3;font-size:19px;z-index:2}}.scenario{{border-top:5px solid var(--blue)}}.scenario.bull{{border-color:var(--red)}}.scenario.bear{{border-color:var(--green)}}.scenario .prob{{font-size:28px;font-weight:850;font-variant-numeric:tabular-nums}}ul{{padding-left:20px;margin:8px 0}}.fact{{border-top:4px solid #2e90fa}}.infer{{border-top:4px solid #7f56d9}}.hypo{{border-top:4px solid #f79009}}code{{background:#eef2f6;padding:2px 5px;border-radius:5px}}footer{{color:var(--muted);padding:18px 2px 35px}}@media(max-width:900px){{.hero,.flow{{grid-template-columns:1fr}}.half,.third{{grid-column:span 12}}.node:not(:last-child):after{{content:"↓";right:50%;top:auto;bottom:-18px}}h1{{font-size:25px}}.wrap{{padding:12px}}}}@media print{{body{{background:#fff}}.card,header{{box-shadow:none;break-inside:avoid}}nav{{display:none}}}}
</style></head><body><div class="wrap">
<header><div class="kicker">GLOBAL → A-SHARE TRANSMISSION</div><h1>今天全球市场的共同点不强，A股更要服从内部退潮</h1>
<p>数据日：2026-08-20｜A股/日韩台已收盘｜美股为盘中快照｜生成：{esc(generated)}</p>
<div class="hero"><div><p>一句话结论：<b>韩国是公司级回购驱动的存储反弹，美股是大盘承压但半导体相对抗跌；这能给A股半导体/存储提供明早观察方向，却不足以推翻A股自身的退潮硬覆盖。</b></p>
<nav><a href="#map">传导图</a><a href="#scenarios">三情景</a><a href="#industries">行业结构</a><a href="#audit">审计</a></nav></div>
<div class="decision"><span class="badge stop">当前动作：零交易</span><span class="badge">账户上限参考 15%</span><span class="badge info">置信度：中</span><p><strong>风险收缩 · 退潮</strong></p><p>ST_cycle={eco['ST_cycle']:.1f}，3日斜率 {eco['slope3']:+.1f}；复权闸门={esc(qfq['price_adjustment'])}，RPS/MA/ATR/跨日个股强弱停算。</p></div></div></header>

<main class="grid">
<section class="card third"><h2>A股：指数红，生态弱</h2><div class="metric"><span>上证</span><b class="up">{pct(a.get('sh000001', {}).get('change_pct'))}</b></div><div class="metric"><span>创业板</span><b class="up">{pct(a.get('sz399006', {}).get('change_pct'))}</b></div><div class="metric"><span>半导体开盘→收盘</span><b class="down">{pct(semiconductor.get('median_intraday_pct'))}</b></div><div class="metric"><span>昨日涨停收益 / 炸板率</span><b class="down">{pct(raw['YLR_close'])} / {raw['FR']*100:.1f}%</b></div><p class="warn">涨停85只不等于健康：炸板55只、昨日涨停平均亏损；半导体上涨宽度仅 {semiconductor.get('breadth_intraday_pct', float('nan')):.0f}%，海外同链与A股内部承接背离。</p></section>
<section class="card third"><h2>日韩台：修复但不同质</h2><div class="metric"><span>日经225</span><b class="up">{pct(g.get('^N225', {}).get('change_pct'))}</b></div><div class="metric"><span>KOSPI</span><b class="up">{pct(korea.get('change_pct'))}</b></div><div class="metric"><span>台湾加权</span><b class="up">{pct(g.get('^TWII', {}).get('change_pct'))}</b></div><div class="metric"><span>SK海力士 / 三星</span><b class="up">{pct(hynix.get('change_pct'))} / {pct(samsung.get('change_pct'))}</b></div><p>韩国强度高度集中于两大存储权重；日台只是温和修复，不能称为亚洲科技全面主升。</p></section>
<section class="card third"><h2>美股：大盘弱，芯片抗跌</h2><div class="metric"><span>标普 / 纳指</span><b class="down">{pct(g.get('^GSPC', {}).get('change_pct'))} / {pct(g.get('^IXIC', {}).get('change_pct'))}</b></div><div class="metric"><span>费城半导体</span><b class={'up' if sox.get('change_pct',0)>0 else 'down'}>{pct(sox.get('change_pct'))}</b></div><div class="metric"><span>美光</span><b class="up">{pct(micron.get('change_pct'))}</b></div><div class="metric"><span>美股时点</span><b>约10:00 ET</b></div><p>半导体相对强于指数是局部正信号；利率与油价压力仍压制广义风险偏好。</p></section>

<section class="card" id="map"><h2>板块间传导关系：先看“来源是否可复制”，再看A股承接</h2><div class="flow">
<div class="node"><b>韩国存储</b><small>KOSPI {pct(korea.get('change_pct'))}<br>海力士 {pct(hynix.get('change_pct'))}</small><p>公司级40万亿韩元回购注销，事件性最强。</p></div>
<div class="node"><b>全球芯片验证</b><small>SOX {pct(sox.get('change_pct'))}<br>美光 {pct(micron.get('change_pct'))}</small><p>当前只证明相对抗跌，尚未形成美股半导体普涨。</p></div>
<div class="node"><b>A股映射</b><small>半导体日内排名 {int(semiconductor.get('rank', 0))}<br>元器件日内排名 {int(components.get('rank', 0))}</small><p>今天同链内部偏弱，只获得竞价观察权；高开本身不是买点。</p></div>
<div class="node"><b>A股内部闸门</b><small>退潮｜ST_cycle {eco['ST_cycle']:.1f}<br>YLR {pct(raw['YLR_close'])}</small><p>内部情绪弱于指数，覆盖外围利好。</p></div>
<div class="node"><b>账户动作</b><small>上限参考 15%<br>当前可执行 0%</small><p>复权与账户字段未过门，等待修复而非追涨。</p></div></div>
<div class="goodbox"><b>关系结论：</b>韩国存储 → A股存储/半导体是“强产业映射、弱交易授权”；美股SOX与美光是第二验证层；A股自身涨停生态是最终否决层。今天A股内部靠前的是旅游、银行、汽车等方向，半导体排名靠后，说明海外利好与场内主导资金并未共振。若三层不同向，服从A股内部风险，而不是服从海外涨幅最大者。</div></section>

<section class="card" id="scenarios"><h2>下一交易时段推演（概率是交易假设，不是统计事实）</h2><div class="grid">
<article class="card third scenario"><div class="prob">55%</div><h3>基准：存储高开后分化</h3><ul><li>韩国回购刺激隔夜记忆仍在，A股存储/半导体容易获得高开。</li><li>美股大盘承压、A股退潮，使后排冲高回落概率高于全面扩散。</li><li><b>动作：</b>零交易；只观察行业中军能否在第一次回踩后重新站上分时均价。</li></ul><p class="warn"><b>取消：</b>板块高开但宽度快速收窄、只有一只高标封住，或昨日涨停负反馈继续扩大。</p></article>
<article class="card third scenario bull"><div class="prob">25%</div><h3>偏强：全球芯片由抗跌转普涨</h3><ul><li>美股收盘前标普/纳指收复大部分跌幅，SOX转为明显上涨，美光、英伟达同向。</li><li>A股次日半导体行业不是单股驱动，中军与弹性股同步站上均价。</li><li><b>动作：</b>即使成立也不得盘中提高15%上限；复权和账户门未修复前仍不可执行。</li></ul><p><b>机会成本：</b>可能错过第一段修复，但避免把海外事件性跳空当成A股主升确认。</p></article>
<article class="card third scenario bear"><div class="prob">20%</div><h3>偏弱：债市压力压过芯片反弹</h3><ul><li>美股大盘跌幅继续扩大，SOX由抗跌转为领跌。</li><li>A股科技竞价高开低走，科创50继续显著弱于主板。</li><li><b>动作：</b>保持零交易；已有仓优先减高波动、后排与纯情绪仓。</li></ul><p class="stopbox"><b>硬失效：</b>SOX明显转弱且A股行业中军跌破开盘区间低点；此时韩国涨幅不再具有外推价值。</p></article>
</div></section>

<section class="card" id="industries"><h2>A股当日行业结构（降级口径，不是RPS主线榜）</h2><p>复权闸门失败后，停算5/20日收益、MA宽度、RPS和ADV相对量。本表只使用2026-08-20同一交易日的开盘—收盘结构，不受除权跨日比较影响。</p><div class="tablebox"><table><thead><tr><th>#</th><th>通达信行业</th><th>开收中位</th><th>上涨宽度</th><th>上涨成交占比</th><th>收盘位置</th><th>成交额</th><th>封板/炸板</th><th>单股成交占比</th></tr></thead><tbody>{''.join(ind_rows)}</tbody></table></div><p class="warn">这里的“日内第一行业”只描述今天谁承接最好，不能替代经复权验证后的第一主线。单股成交占比超过35%的行业已降级。</p></section>

<section class="card"><h2>前列行业结构锚点：大胆预期，但不伪装成买单</h2><div class="tablebox"><table><thead><tr><th>行业</th><th>个股</th><th>开盘→收盘</th><th>成交额</th><th>收盘位置</th><th>封板状态</th><th>定位</th></tr></thead><tbody>{''.join(stock_rows)}</tbody></table></div>
<div class="goodbox"><b>对个股的操作预期：</b>明早最容易被资金先交易的是“与韩国存储直接映射、且今天A股内部已经有承接”的前排；最危险的是没有中军跟随的后排高开。合法买点只能是主线中军第一次缩量回踩后的再站上，或首次分歧后的同步修复，不能在竞价直接追。</div></section>

<section class="card half fact"><h2>事实</h2><ul><li>A股本地日线最新日期为2026-08-20，沪深口径不含北交所。</li><li>ST_cycle={eco['ST_cycle']:.1f}，状态为退潮，硬覆盖已成立；昨日涨停收益 {pct(raw['YLR_close'])}，炸板率 {raw['FR']*100:.1f}%。</li><li>日韩台已收盘；美股数据是同日约10:00 ET盘中快照，不与亚洲收盘价假装同一时点。</li><li>复权抽样 {qfq['verified']}/{qfq['sample_size']} 通过，整体未过90%硬门。</li></ul></section>
<section class="card half infer"><h2>推断</h2><ul><li>韩国上涨主要由存储权重与回购事件驱动，可复制到A股的是产业情绪，不是同等涨幅。</li><li>美股大盘下跌而SOX接近平盘，说明芯片有相对强度，但广义风险偏好尚未修复。</li><li>A股指数上涨与追涨生态恶化并存，属于权重/低位结构掩盖高位账户退潮。</li></ul></section>
<section class="card half hypo"><h2>交易假设</h2><ul><li>若美股收盘芯片继续相对强、A股次日半导体中军与宽度共振，则可能从退潮进入局部修复。</li><li>若只有竞价高开而中军不承接，海外催化将成为兑现窗口。</li><li>当前主观情景权重55/25/20仅用于预案，不是历史回测概率。</li></ul></section>
<section class="card half"><h2>风险、动作与机会成本</h2><ul><li><b>最大风险：</b>把韩国公司级回购误读成全球存储需求的新证据。</li><li><b>对应动作：</b>高开不追；退潮状态只允许降险，不允许因为美股盘中转强临时升仓。</li><li><b>T+1：</b>当日新仓无法纠错，若次日低开且无法回到均价，趋势未验证的情绪仓应退出。</li><li><b>机会成本：</b>零交易可能错过隔夜催化的第一段，但保留在情绪修复、复权门恢复后参与中军的能力。</li></ul></section>

<section class="card" id="audit"><h2>数据审计与拒绝清单</h2><table><thead><tr><th>来源</th><th>截至</th><th>用途</th><th>复权/单位</th><th>状态</th></tr></thead><tbody>
<tr><td>本地通达信 <code>C:\\new_tdx\\vipdoc</code></td><td>2026-08-20收盘</td><td>涨停生态、同日日内行业结构</td><td>个股价格原始未复权；成交额元</td><td>日内结构可用，跨日指标停算</td></tr>
<tr><td>腾讯 <code>qt.gtimg.cn</code></td><td>2026-08-20 15:00</td><td>A股指数快照与日期校验</td><td>指数点、%</td><td>{'通过' if not audit['tencent_errors'] else '部分异常'}</td></tr>
<tr><td>Yahoo Finance Chart</td><td>各市场返回时间</td><td>日韩台收盘、美股盘中指数与代表股</td><td>当地货币、%</td><td>{'通过' if not audit['global_errors'] else '部分异常'}</td></tr>
<tr><td>SK hynix公告/PRNewswire</td><td>2026-08-19</td><td>40万亿韩元回购并注销事件</td><td>事件事实</td><td>只解释韩国异动，不生成A股买点</td></tr>
</tbody></table><div class="stopbox"><b>明确拒绝：</b>未使用东方财富/同花顺估算资金流、过期概念成分、分钟CER、板块CER或流通市值CER；未把韩国涨幅机械换算为A股目标价；未在复权闸门失败时计算RPS、MA、ATR、跨日个股收益与日线CER。</div></section>
</main><footer>本报告是研究预案，不构成收益承诺。覆盖沪深A股，不含北交所。来源时间与原始字段已保存于同名audit.json；HTML自身不依赖外部资源即可阅读。</footer>
</div></body></html>"""


def main() -> None:
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    qfq = read_json(ROOT / "output" / "qfq_verification.json")
    eco = read_json(ROOT / "output" / "limit_ecology_latest.json")
    global_quotes, global_errors = fetch_global_quotes()
    a_share_indices, tencent_errors = fetch_tencent_indices()
    industries, stocks, intraday_meta = build_intraday_industries()

    missing_core = [s for s in ("^N225", "^KS11", "^TWII", "^GSPC", "^IXIC", "^SOX") if s not in global_quotes]
    if missing_core:
        raise RuntimeError(f"missing core global quotes: {missing_core}; errors={global_errors}")
    if any(s not in a_share_indices for s in ("sh000001", "sz399001", "sz399006", "sh000688")):
        raise RuntimeError(f"missing A-share index quotes; errors={tencent_errors}")
    if not industries["name"].str.contains("半导体", na=False).any():
        raise RuntimeError("TDX industry map did not yield a semiconductor row")

    now = datetime.now().astimezone()
    audit = {
        "report_version": "global_linkage_projection_v1",
        "generated_at_bjt": now.strftime("%Y-%m-%d %H:%M:%S %z"),
        "trade_date": "2026-08-20",
        "coverage": "沪深A股，不含北交所；日/韩/台主要指数；美股主要指数与半导体代表股",
        "qfq": qfq,
        "sentiment": eco,
        "a_share_indices": a_share_indices,
        "tencent_errors": tencent_errors,
        "global_quotes": global_quotes,
        "global_errors": global_errors,
        "industry_intraday_meta": intraday_meta,
        "industry_intraday_top": industries.head(20).to_dict(orient="records"),
        "overseas_mapped_industries": industries[
            industries["name"].str.contains("半导体|元器件", regex=True, na=False)
        ].to_dict(orient="records"),
        "industry_anchor_stocks": stocks[[
            "ts_code", "name", "industry_name", "industry_rank", "open", "high", "low", "close",
            "intraday_return_pct", "close_location_pct", "amount_yi", "is_limit_up", "broken",
        ]].to_dict(orient="records"),
        "scenario_hypotheses": {"base_high_open_divergence": 0.55, "bull_chip_breadth": 0.25, "bear_rates_dominate": 0.20},
        "external_event_sources": [
            "https://www.prnewswire.com/news-releases/sk-hynix-accelerates-40-trillion-won-share-repurchase-and-cancellation-program-pursues-shareholder-return-expansion-to-over-50-of-fcf-302855022.html",
            "https://apnews.com/article/1dcf7c9c3cc490b82b2632302628c46b",
        ],
    }
    report = make_html(audit, industries, stocks)
    OUT_HTML.write_text(report, encoding="utf-8")
    OUT_AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_HTML)
    print(OUT_AUDIT)


if __name__ == "__main__":
    main()
