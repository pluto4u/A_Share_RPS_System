"""小红书每日复盘：由当日市场/看板数据生成 6 张 1080x1440 图片和一份可直接粘贴的文案（post.md）。

只做“准备好发布的素材”，不登录、不发布。内容只含市场层面的数据与主线题材，不点名个股，也不含买卖建议。
所有数字和文字都由数据生成；没有写死的行情数字。

    python scripts/reporting/xhs_daily_post.py                 # 最新一个有看板的交易日
    python scripts/reporting/xhs_daily_post.py --date 2026-10-23
    python scripts/reporting/xhs_daily_post.py --no-render     # 只生成 HTML 与文案，不截图

输入：outputs/market_timing_<日期>.json、outputs/daily_board_<日期>.json（缺失时回退旧 output/）
输出：outputs/xhs/<日期>/card1..6.png、post.md
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import LEGACY_OUTPUT_DIR, OUTPUT_ROOT

import argparse
import html as _html
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

UP, DN, INK, MUTED, GRID, ACC, GOLD = "#d0383f", "#008a9c", "#1d2733", "#6b7785", "#e6e9ed", "#2f5bd3", "#8a6427"
TOTAL = 6
TITLE_MAX, BODY_MAX = 20, 900   # 小红书标题 20 字、正文 1000 字上限，留出余量
DISCLAIMER = "以上为个人对市场数据的整理，仅作观察记录，不构成任何投资建议，股市有风险。"


def read_out(name: str) -> Path:
    new = OUTPUT_ROOT / name
    return new if new.exists() else LEGACY_OUTPUT_DIR / name


def f(x, n=1): return f"{x:.{n}f}"
def md(d): return d[5:].replace("-", "/")
def sg(x, n=1): return ("+" if x >= 0 else "−") + f(abs(x), n)
def pct(x, n=1): return f(x * 100, n) + "%"
def E(s): return _html.escape(str(s))


class Ctx:
    """当日数据的只读视图。"""

    def __init__(self, mt: dict, board: dict):
        self.mt, self.board = mt, board
        self.L = mt["latest"]
        self.BC, self.BS = mt["bottom_config"], mt["bottom_study"]
        self.series = mt["series"]
        self.S = self.series[-60:]
        self.dates = [x["trade_date"] for x in self.S]
        self.prev = self.series[-2]
        self.asof = mt["asof"]
        self.slash = self.asof.replace("-", "/")
        self.st = {(r["signal"], r["horizon"]): r for r in self.BS}
        self.theme = board.get("theme") or {}
        self.eligible = self.L["eligible"]


# --------------------------------------------------------------------------- 绘图与版式
def line_chart(c: Ctx, series, w=960, h=420, ymin=0, ymax=100, ref=None, ticks=None, yfmt=lambda v: f(v, 0), log=False):
    l, r, t, b = 70, 30, 20, 50
    W, H = w - l - r, h - t - b
    n = len(series[0]["v"])
    X = lambda i: l + i * W / (n - 1)

    def Y(v):
        v = min(ymax, max(ymin, v))
        p = ((math.log10(v) - math.log10(ymin)) / (math.log10(ymax) - math.log10(ymin))) if log else (v - ymin) / (ymax - ymin)
        return t + H - p * H

    o = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">']
    for v in ticks or [ymin + (ymax - ymin) * i / 4 for i in range(5)]:
        o.append(f'<line x1="{l}" x2="{w-r}" y1="{Y(v)}" y2="{Y(v)}" stroke="{GRID}"/><text x="{l-10}" y="{Y(v)+7}" text-anchor="end" class="ax">{yfmt(v)}</text>')
    for v, lab in ref or []:
        o.append(f'<line x1="{l}" x2="{w-r}" y1="{Y(v)}" y2="{Y(v)}" stroke="#a07a3c" stroke-width="2" stroke-dasharray="8 6"/>')
        if lab:
            o.append(f'<text x="{w-r}" y="{Y(v)-8}" text-anchor="end" class="ref">{lab}</text>')
    for i in (0, n // 2, n - 1):
        anc = "start" if i == 0 else "end" if i == n - 1 else "middle"
        o.append(f'<text x="{X(i)}" y="{h-12}" text-anchor="{anc}" class="ax">{md(c.dates[i])}</text>')
    for s in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s["v"]) if v is not None)
        dash = 'stroke-dasharray="10 7"' if s.get("dash") else ""
        o.append(f'<polyline points="{pts}" fill="none" stroke="{s["c"]}" stroke-width="3.5" stroke-linejoin="round" {dash}/>')
        o.append(f'<circle cx="{X(n-1)}" cy="{Y(s["v"][-1])}" r="7" fill="{s["c"]}" stroke="#fff" stroke-width="2"/>')
    o.append("</svg>")
    return "".join(o)


def legend(items):
    out = []
    for t, col, dashed in items:
        bg = f"repeating-linear-gradient(90deg,{col} 0 8px,transparent 8px 13px)" if dashed else col
        out.append(f'<span><i style="background:{bg}"></i>{t}</span>')
    return '<div class="lg">' + "".join(out) + "</div>"


def table(heads, rows, font=26):
    th = "".join(f"<th>{h}</th>" for h in heads)
    body = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    return f'<table style="font-size:{font}px"><tr>{th}</tr>{body or "<tr><td colspan=9>无数据</td></tr>"}</table>'


def page(c: Ctx, n: int, title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1440px;background:#f6f4ef;font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:{INK};padding:64px 60px;position:relative;overflow:hidden}}
.k{{font-size:26px;color:{MUTED};letter-spacing:2px}} .pg{{position:absolute;top:64px;right:60px;font-size:26px;color:{MUTED}}}
h1{{font-size:56px;line-height:1.25;margin:18px 0 28px;font-weight:800}}
.card{{background:#fff;border-radius:22px;padding:28px 34px;margin-bottom:22px;box-shadow:0 2px 0 #e8e4da}}
p{{font-size:28px;line-height:1.6}} .m{{color:{MUTED}}} .s{{font-size:24px;color:{MUTED};line-height:1.5}} h3{{font-size:28px;margin-bottom:8px}}
.up{{color:{UP}}} .dn{{color:{DN}}} b{{font-weight:800}}
.ax{{font-size:22px;fill:{MUTED}}} .ref{{font-size:22px;fill:{GOLD};font-weight:700}}
.lg{{display:flex;gap:30px;font-size:25px;margin:4px 0 6px}} .lg i{{display:inline-block;width:34px;height:6px;border-radius:3px;margin-right:10px;vertical-align:middle}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}} .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.big{{font-size:58px;font-weight:800;line-height:1.1}} .lab{{font-size:24px;color:{MUTED};margin-top:6px}}
table{{border-collapse:collapse;width:100%}} td,th{{padding:9px 6px;border-bottom:1px solid {GRID};text-align:right;white-space:nowrap}} td:first-child,th:first-child{{text-align:left}} th{{color:{MUTED};font-weight:400;font-size:23px}}
.foot{{position:absolute;bottom:40px;left:60px;right:60px;font-size:21px;color:#9aa3ad}}
</style></head><body><div class="k">A股市场体检 · {c.slash}</div><div class="pg">{n}/{TOTAL}</div><h1>{title}</h1>{body}
<div class="foot">沪深A股（不含北交所）{c.eligible}只可比较股票 · 仅为市场观察，不构成投资建议</div></body></html>"""


def split_bar(L, key, den):
    out = ""
    for v, col, t in ((L["up" + key], UP, "涨 "), (L["down" + key], DN, "跌 "), (L["flat" + key], "#9aa3ad", "")):
        share = v / den * 100
        label = ((t if share > 12 else "") + f(share) + "%") if share > 5 else ""
        out += (f'<div style="width:{share:.2f}%;background:{col};height:56px;color:#fff;font-size:24px;display:flex;align-items:center;'
                f'justify-content:center;border-right:3px solid #fff;white-space:nowrap">{label}</div>')
    return f'<div style="display:flex;border-radius:10px;overflow:hidden">{out}</div>'


# --------------------------------------------------------------------------- 6 张卡片
STEPS = [("正常", "没有超卖"), ("超卖", "4项超卖指标满足≥3项"), ("超卖+背离", "指数新低，个股层面没再恶化"),
         ("投降 / 衰竭", "放量或缩量的极端普跌"), ("推力确认", "超卖后出现宽度推力")]


def stage_name(state: str) -> str:
    return "投降 / 衰竭" if state in ("超卖+缩量衰竭", "超卖+放量投降") else state


def card_cover(c: Ctx) -> str:
    L, cur = c.L, c.L["bottom_state"]
    over, base, conf = c.st[("oversold", 20)], c.st[("baseline", 20)], c.st[("bottom_confirmed", 20)]
    ladder = ""
    for i, (name, desc) in enumerate(STEPS):
        on = name == stage_name(cur)
        style = f"background:{INK};color:#fff" if on else f"background:#f1efe9;color:{MUTED}"
        mark = '<b style="float:right;font-size:26px">◀ 今天</b>' if on else ""
        ladder += (f'<div style="display:flex;align-items:center;gap:20px;margin:9px 0"><div style="width:44px;height:44px;border-radius:50%;{style};'
                   f'display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:800">{i}</div>'
                   f'<div style="flex:1;border-radius:14px;padding:11px 22px;{style}"><b style="font-size:29px">{name}</b>'
                   f'<span style="font-size:23px;margin-left:14px;opacity:.85">{desc}</span>{mark}</div></div>')
    BC = c.BC
    if cur == "推力确认":
        watch = [f'站上MA20 从 {f(L["ma20_pct"])}% 能否继续回升（50% 为推力线）',
                 f'10日上涨比例均线 {f(L["thrust_ema"], 2)} 能否升破 {BC["thrust_ema_high"]}',
                 f'60日新低 {L["low60"]} 只能否继续减少']
        wt = "接下来看（推力确认后）"
    else:
        watch = [f'单日上涨家数 > {f(BC["thrust_day_breadth"] * 100, 0)}%（今天 {pct(L["breadth"])}）',
                 f'10日上涨比例均线升破 {BC["thrust_ema_high"]}（今天 {f(L["thrust_ema"], 2)}）',
                 f'站上MA20 回到 50%（今天 {f(L["ma20_pct"])}%）']
        wt = "接下来看（任一出现即推力确认）"
    hist = (f'历史上，<b>单看超卖</b>后20日上涨概率 {f(over["index_up_pct"], 0)}%（平时 {f(base["index_up_pct"], 0)}%）；'
            f'<b>推力确认</b>后 {f(conf["index_up_pct"], 0)}%（样本 {conf["observations"]} 次，仅供参考）。')
    return page(c, 1, f"底部温度计：<br>{cur}", f"""
<div class="card">{ladder}</div>
<div class="card"><div class="grid3">
<div><div class="big up">{pct(L['breadth'])}</div><div class="lab">上涨家数占比</div></div>
<div><div class="big dn">{f(L['ma20_pct'])}%</div><div class="lab">站上MA20</div></div>
<div><div class="big dn">{L['low60']}</div><div class="lab">60日新低（只）</div></div></div></div>
<div class="card"><p>昨日（{md(c.prev['trade_date'])}）为“{c.prev['bottom_state']}”，今日为“{cur}”。{hist}</p>
<p style="font-size:26px;margin-top:10px"><b>👀 {wt}</b><br>· {watch[0]}<br>· {watch[1]}<br>· {watch[2]}</p></div>""")


def card_breadth(c: Ctx) -> str:
    L = c.L
    ups = L["up_amount_yi"] / L["amount_yi"]
    same = abs(L["breadth"] - ups) < 0.1
    chart = line_chart(c, [{"v": [x["breadth"] * 100 for x in c.S], "c": UP},
                           {"v": [x["up_amount_yi"] / x["amount_yi"] * 100 for x in c.S], "c": ACC, "dash": 1}],
                       h=400, ref=[(70, "70%参考线")])
    note = "涨的股票和钱同步放大。" if same else "家数与成交额的分布并不一致。"
    return page(c, 2, "股票很多在涨，<br>成交是否也跟得上？", f"""
<div class="card"><p class="m" style="font-size:25px">今天：家数 vs 成交额（上涨 {L['up']} · 下跌 {L['down']} · 平盘 {L['flat']}）</p>
<p style="font-size:24px;margin-top:8px">家数占比</p>{split_bar(L, '', L['eligible'])}
<p style="font-size:24px;margin-top:12px">成交额占比（合计 {f(L['amount_yi'] / 1e4, 2)} 万亿，为20日均量的 {f(L['amount_ratio20'], 2)} 倍）</p>{split_bar(L, '_amount_yi', L['amount_yi'])}
<p style="margin-top:14px">上涨股占 {pct(L['breadth'])} 的家数、{pct(ups)} 的成交额：{note}</p></div>
<div class="card"><p class="m" style="font-size:25px">近60个交易日（%）</p>{legend([('上涨家数占比', UP, False), ('上涨成交额占比', ACC, True)])}{chart}</div>""")


def card_theme(c: Ctx) -> str:
    th = c.theme
    con, rs = th.get("concepts") or {}, th.get("reasons") or {}
    pc = lambda v: sg(v, 1) + "%"
    cl = [[E(r["name"]), pc(r["ret1"]), pc(r["ret5"])] for r in (con.get("top") or [])[:7]]
    kw = [[E(r["keyword"]), str(r["today"]), f'{r["days"]}/{len(rs.get("sessions", [])) or 5}'] for r in (rs.get("top") or [])[:7]]
    lad = th.get("ladder") or []
    lad_rows = [[f'{int(r["continue_day_cnt"])}板', E((r.get("limit_up_reason") or "").replace("+", " · ")[:26])] for r in lad[:5]]
    left = table(["概念指数", "今日", "近5日"], cl, 24) if cl else '<p class="s">概念数据未取得</p>'
    right = table(["涨停热词", "今日", "出现天数"], kw, 24) if kw else '<p class="s">涨停池未取得</p>'
    tc = rs.get("today_count")
    return page(c, 3, "短线主线：<br>资金在炒什么？", f"""
<div class="grid2"><div class="card" style="padding:22px 24px"><h3>概念指数 近5日最强</h3>{left}</div>
<div class="card" style="padding:22px 24px"><h3>涨停原因热词{f'（今日涨停{tc}只）' if tc is not None else ''}</h3>{right}</div></div>
<div class="card"><h3>连板梯队（2板及以上）</h3>{table(['连板', '涨停原因'], lad_rows, 25) if lad_rows else '<p class="s">今天没有2板及以上</p>'}</div>
<div class="card"><p class="s">这里只回答“资金在炒什么”，不回答“该买什么”。历史回测里，次日追涨停、追突破都是负收益，主线也会很快切换。概念与涨停原因均来自同花顺。</p></div>""")


def card_ma_hl(c: Ctx) -> str:
    L = c.L
    bars = ""
    for n in (5, 10, 20, 60, 120, 250):
        v = L[f"ma{n}_pct"]
        bars += (f'<div style="display:flex;align-items:center;margin:10px 0"><span style="width:110px;font-size:27px">MA{n}</span>'
                 f'<div style="flex:1;background:#eef0f3;border-radius:8px;height:40px;position:relative"><div style="width:{v}%;background:{ACC};height:40px;border-radius:8px"></div>'
                 f'<span style="position:absolute;left:calc({v}% + 12px);top:3px;font-size:26px;font-weight:700">{f(v)}%</span>'
                 f'<div style="position:absolute;left:50%;top:-5px;bottom:-5px;border-left:2px dashed #a07a3c"></div></div>'
                 f'<span style="width:200px;text-align:right;font-size:22px;color:{MUTED}">{L[f"ma{n}_count"]}/{L[f"ma{n}_eligible"]}只</span></div>')
    hl = "".join(f'<tr><td>{n}日</td><td class="up"><b>{L[f"high{n}"]}</b></td><td class="dn"><b>{L[f"low{n}"]}</b></td><td><b>{L[f"high{n}"] - L[f"low{n}"]}</b></td></tr>' for n in (20, 60, 250))
    chart = line_chart(c, [{"v": [x["ma20_pct"] for x in c.S], "c": ACC}, {"v": [x["ma60_pct"] for x in c.S], "c": GOLD, "dash": 1}], h=300, ref=[(50, "一半股票")])
    return page(c, 4, "站上均线与创新高新低", f"""
<div class="card" style="padding:22px 30px">{bars}<p class="s">虚线 = 50%。站上MA20 昨日 {f(c.prev['ma20_pct'])}% → 今日 {f(L['ma20_pct'])}%。</p></div>
<div class="card" style="padding:22px 30px"><table style="font-size:26px"><tr><th>收盘突破窗口</th><th>新高</th><th>新低</th><th>新高−新低</th></tr>{hl}</table></div>
<div class="card" style="padding:18px 30px"><p class="m" style="font-size:24px">站上MA20 / MA60 近60日（%）</p>{legend([('MA20', ACC, False), ('MA60', GOLD, True)])}{chart}</div>""")


def card_layers(c: Ctx) -> str:
    L, BC = c.L, c.BC

    def yn(ok): return f'<b class="{"up" if ok else "m"}" style="font-size:28px">{"✔ 满足" if ok else "✘ 未满足"}</b>'
    ov = [("站上MA20 &lt; 15%", f(L["ma20_pct"]) + "%", L["ma20_pct"] < 15),
          (f'McClellan振荡 &lt; {BC["mco_max"]}', f(L["mco"], 0), L["mco"] < BC["mco_max"]),
          (f'10日上涨比例均线 &lt; {BC["thrust_ema_low"]}', f(L["thrust_ema"], 2), L["thrust_ema"] < BC["thrust_ema_low"]),
          (f'20日新低占比 &gt; {f(BC["low20_share_min"] * 100, 0)}%', f(L["low20_share"] * 100) + "%", L["low20_share"] > BC["low20_share_min"])]
    rows = "".join(f"<tr><td>{a}</td><td><b>{b}</b></td><td>{yn(k)}</td></tr>" for a, b, k in ov)
    n_ov = sum(k for _, _, k in ov)
    washout = L["breadth"] < BC["panic_breadth_max"]
    thrust_day = L["breadth"] > BC["thrust_day_breadth"]
    zweig = L["thrust_ema"] > BC["thrust_ema_high"]
    recover = L["ma20_pct"] >= 50 and c.prev["ma20_pct"] < 50
    return page(c, 5, "底部四层，逐层看", f"""
<div class="card" style="padding:22px 30px"><p><b>① 超卖</b><span class="m" style="font-size:24px">　满足≥{BC['oversold_votes']}项即超卖，今天 {n_ov} 项</span></p><table style="font-size:25px">{rows}</table></div>
<div class="card" style="padding:20px 30px"><p><b>② 投降 / 衰竭</b>　{yn(bool(L['capitulation'] or L['exhaustion']))}</p>
<p style="font-size:25px">上涨家数 {pct(L['breadth'])}，{'低于' if washout else '高于'} {f(BC['panic_breadth_max'] * 100, 0)}% 的极端普跌线；成交额为20日均量的 <b>{f(L['amount_ratio20'], 2)}</b> 倍。</p></div>
<div class="card" style="padding:20px 30px"><p><b>③ 背离</b>　{yn(bool(L['bottom_divergence']))}</p>
<p style="font-size:25px">上证 {f(L['index_close'], 0)} 点；20日新低 {L['low20']} 只，McClellan {f(L['mco'], 0)}。</p></div>
<div class="card" style="padding:20px 30px"><p><b>④ 推力确认</b>　{yn(bool(L['bottom_confirmed']))}</p>
<p style="font-size:25px">单日上涨家数 {pct(L['breadth'])}（&gt;{f(BC['thrust_day_breadth'] * 100, 0)}%：{'触发' if thrust_day else '未触发'}）；10日上涨比例均线 {f(L['thrust_ema'], 2)}（&gt;{BC['thrust_ema_high']}：{'触发' if zweig else '未触发'}）；站上MA20 回到50%：{'触发' if recover else '未触发'}。</p></div>""")


VOL_UNIT = {"rv20": "%", "park20": "%", "dispersion": "pp", "flip10": "pp", "big_move_share": "%", "limit_share": "%", "broken_rate": "%", "amount_ratio": "倍"}


def card_vol(c: Ctx) -> str:
    L = c.L
    names = c.mt.get("vol_components") or {}
    rows = []
    for k, n in names.items():
        v, p = L.get(k), L.get(k + "_pctl")
        rows.append([E(n), (f(v, 2 if k == "amount_ratio" else 1) + VOL_UNIT.get(k, "")) if isinstance(v, (int, float)) else "—",
                     f(p, 0) if isinstance(p, (int, float)) else "—"])
    vc = c.mt.get("vol_config") or {}
    temp, state = L.get("vol_temp"), L.get("vol_state")
    chart = line_chart(c, [{"v": [x.get("vol_temp") for x in c.S], "c": INK}], h=230,
                       ref=[(vc.get("hot", 70), "高"), (vc.get("cool", 30), "低")])
    head = f(temp, 1) if isinstance(temp, (int, float)) else "—"
    return page(c, 6, "波动温度计：<br>市场有多“颠”？", f"""
<div class="card" style="padding:22px 30px"><div class="grid3"><div><div class="big">{head}</div><div class="lab">波动温度（{state or '—'}）</div></div>
<div><div class="big">{L.get('vol_direction', '—')}</div><div class="lab">上证近10日方向</div></div>
<div><div class="big">{'是' if L.get('whipsaw') else '否'}</div><div class="lab">近5日宽度大幅翻转</div></div></div></div>
<div class="card" style="padding:14px 30px">{table(['波动指标', '今天', '3年分位'], rows, 24)}</div>
<div class="card" style="padding:14px 30px"><p class="m" style="font-size:23px">波动温度 近60日　<span class="s">只预示波动大小，不预示涨跌方向，不是买卖信号</span></p>{chart}</div>""")


def build_cards(c: Ctx) -> list[str]:
    return [card_cover(c), card_breadth(c), card_theme(c), card_ma_hl(c), card_layers(c), card_vol(c)]


# --------------------------------------------------------------------------- 文案
HEADLINE = {"正常": "情绪回到正常区", "超卖": "进入超卖区", "超卖+背离": "超卖+背离，等推力",
            "超卖+缩量衰竭": "超卖+缩量衰竭", "超卖+放量投降": "超卖+放量投降", "推力确认": "出现推力确认"}


def build_post(c: Ctx) -> str:
    L, th = c.L, c.theme
    state = L["bottom_state"]
    title = f"A股复盘{md(c.asof)}｜{HEADLINE.get(state, state)}"[:TITLE_MAX]
    conf, base, over = c.st[("bottom_confirmed", 20)], c.st[("baseline", 20)], c.st[("oversold", 20)]
    gap = over["index_up_pct"] - base["index_up_pct"]
    over_txt = (f"历史上单看超卖后20日上涨概率 {f(over['index_up_pct'], 0)}%（平时 {f(base['index_up_pct'], 0)}%），"
                + ("差别不大" if abs(gap) < 5 else f"高出平时 {f(abs(gap), 0)} 个百分点" if gap > 0 else f"低于平时 {f(abs(gap), 0)} 个百分点")
                + f"；推力确认后 {f(conf['index_up_pct'], 0)}%，")
    rs, con = th.get("reasons") or {}, th.get("concepts") or {}
    kws = [r["keyword"] for r in (rs.get("top") or [])[:3]]
    cons = [r["name"] for r in (con.get("top") or [])[:3]]
    lad = th.get("ladder") or []
    top_streak = max([int(r["continue_day_cnt"]) for r in lad], default=1)
    vt, vs = L.get("vol_temp"), L.get("vol_state")
    chg = L["breadth"] - c.prev["breadth"]
    lines = [
        f"📅 {c.asof} 收盘｜沪深A股 {c.eligible} 只",
        f"📊 上涨 {L['up']} 只 / 下跌 {L['down']} 只，上涨家数占比 {pct(L['breadth'])}（较昨日 {sg(chg * 100, 1)} 个百分点）",
        f"💰 成交 {f(L['amount_yi'] / 1e4, 2)} 万亿，为20日均量的 {f(L['amount_ratio20'], 2)} 倍",
        f"📉 站上MA20 的股票 {f(L['ma20_pct'])}%（昨日 {f(c.prev['ma20_pct'])}%），60日新高/新低 {L['high60']}/{L['low60']} 只",
        "",
        f"🌡️ 底部温度计：昨日“{c.prev['bottom_state']}” → 今日“{state}”",
        f"{over_txt}但样本只有 {conf['observations']} 次，仅供参考。",
    ]
    if isinstance(vt, (int, float)):
        lines.append(f"🌪️ 波动温度 {f(vt, 1)}（{vs}），只代表波动大小，不代表方向。")
    lines.append("")
    if kws or cons:
        lines.append("🔥 短线主线（同花顺）")
        if kws:
            lines.append(f"涨停原因热词：{'、'.join(kws)}（今日涨停 {rs.get('today_count', '—')} 只，最高 {top_streak} 板）")
        if cons:
            lines.append(f"近5日最强概念：{'、'.join(cons)}")
        lines.append("")
    lines += ["👀 接下来看：站上MA20 能否继续回升、10日上涨比例均线能否升破 0.615、新低家数是否继续减少。",
              "", DISCLAIMER]
    body = "\n".join(lines)
    tags = ["A股", "复盘", "市场宽度", "股票复盘", "A股行情"] + [k for k in kws[:2]]
    tag_line = " ".join("#" + t for t in tags)
    if len(body) > BODY_MAX:
        raise ValueError(f"正文 {len(body)} 字，超过 {BODY_MAX}")
    return (f"# 小红书发布素材 {c.asof}\n\n## 标题（{len(title)}字）\n{title}\n\n## 正文（{len(body)}字）\n{body}\n\n## 话题标签\n{tag_line}\n\n"
            f"## 图片（按顺序上传）\ncard1.png … card{TOTAL}.png\n\n> 发布前请自行核对数字与措辞；本素材由数据自动生成，未登录、未发布。\n")


# --------------------------------------------------------------------------- 渲染与入口
def find_browser() -> str:
    for p in (os.environ.get("CHROME_PATH", ""), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if p and Path(p).exists():
            return p
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    raise SystemExit("找不到 Chrome/Edge，可设置环境变量 CHROME_PATH")


def render_pngs(html_dir: Path, out_dir: Path, n: int) -> None:
    browser = find_browser()
    for i in range(1, n + 1):
        out = out_dir / f"card{i}.png"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1080,1440", f"--screenshot={out}", (html_dir / f"card{i}.html").as_uri()],
                       check=True, capture_output=True, timeout=120)
        if not out.exists() or out.stat().st_size < 10_000:
            raise RuntimeError(f"{out.name} 渲染失败")


def latest_board_tag() -> str:
    tags = sorted({p.stem.replace("daily_board_", "") for d in (OUTPUT_ROOT, LEGACY_OUTPUT_DIR) if d.exists() for p in d.glob("daily_board_*.json")})
    if not tags:
        raise SystemExit("找不到 daily_board_*.json，请先运行看板")
    return tags[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="YYYY-MM-DD，默认最新有看板的日期")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()
    tag = args.date.replace("-", "") if args.date else latest_board_tag()
    mt = json.loads(read_out(f"market_timing_{tag}.json").read_text(encoding="utf-8"))
    board = json.loads(read_out(f"daily_board_{tag}.json").read_text(encoding="utf-8"))
    c = Ctx(mt, board)
    out_dir = args.out or (OUTPUT_ROOT / "xhs" / tag)
    html_dir = out_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    cards = build_cards(c)
    for i, h in enumerate(cards, 1):
        (html_dir / f"card{i}.html").write_text(h, encoding="utf-8")
    (out_dir / "post.md").write_text(build_post(c), encoding="utf-8")
    if not args.no_render:
        render_pngs(html_dir, out_dir, len(cards))
    print(json.dumps({"date": c.asof, "out": str(out_dir), "cards": len(cards), "rendered": not args.no_render}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
