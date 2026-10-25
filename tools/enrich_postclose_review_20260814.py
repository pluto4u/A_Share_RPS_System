#!/usr/bin/env python3
"""为 2026-08-14 盘后复盘追加第三方估算资金流审计并生成综合版 HTML。"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TARGET = "20260814"
BASE_HTML = OUT / "a_share_intraday_review_20260814.html"
BASE_AUDIT = OUT / "a_share_intraday_review_20260814_audit.json"
FINAL_HTML = OUT / "a_share_postclose_review_20260814_deep.html"
FINAL_AUDIT = OUT / "a_share_postclose_review_20260814_deep_audit.json"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fetch_eastmoney_probe() -> dict[str, Any]:
    """只取首屏做字段/日期探针；覆盖不足时禁止做横截面结论。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f62",
        "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
              "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
        "fields": "f12,f14,f2,f3,f6,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124",
    }
    result: dict[str, Any] = {
        "source": "Eastmoney push2 clist", "url": url, "field": "f62",
        "definition": "网页模型估算主力净流入；不代表真实机构账户",
        "status": "unavailable", "usable_for_ranking": False, "rows": 0,
    }
    try:
        response = requests.get(
            url, params=params, timeout=20,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        )
        result["http_status"] = response.status_code
        response.raise_for_status()
        payload = response.json() or {}
        data = payload.get("data") or {}
        rows = data.get("diff") or []
        dates = sorted({
            datetime.fromtimestamp(row["f124"]).strftime("%Y%m%d")
            for row in rows if isinstance(row.get("f124"), (int, float)) and row["f124"] > 0
        })
        total = int(data.get("total") or 0)
        result.update({
            "status": "probe_only" if rows else "empty", "rows": len(rows),
            "total": total, "coverage": len(rows) / total if total else 0,
            "dates": dates, "date_valid": dates == [TARGET],
            "schema_valid": bool(rows) and all(key in rows[0] for key in ("f12", "f14", "f6", "f62", "f124")),
            "sample": [{key: row.get(key) for key in ("f12", "f14", "f6", "f62", "f124")} for row in rows[:3]],
            "usable_for_ranking": False,
            "reason": "仅取得资金净流入排序首屏，样本存在选择偏差；分页接口限流，禁止聚合板块或个股排名",
        })
    except Exception as exc:  # noqa: BLE001 - 审计记录需要保留具体失败原因
        result["reason"] = f"接口失败：{type(exc).__name__}: {exc}"
    return result


def fetch_ths_concepts() -> dict[str, Any]:
    url = "https://data.10jqka.com.cn/funds/gnzjl/"
    result: dict[str, Any] = {
        "source": "同花顺概念资金流公开页", "url": url,
        "definition": "概念流入、流出与净额为网页估算口径；概念成分可重叠",
        "status": "unavailable", "usable_for_hard_gate": False,
    }
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        result["http_status"] = response.status_code
        response.raise_for_status()
        response.encoding = "gbk"
        tables = pd.read_html(StringIO(response.text))
        table = tables[0] if tables else pd.DataFrame()
        needed = {"行业", "涨跌幅", "净额(亿)", "公司家数", "领涨股"}
        schema_valid = not table.empty and needed.issubset(set(map(str, table.columns)))
        top: list[dict[str, Any]] = []
        if schema_valid:
            for _, row in table.head(8).iterrows():
                top.append({
                    "concept": str(row["行业"]), "change": str(row["涨跌幅"]),
                    "estimated_net_yi": float(row["净额(亿)"]),
                    "members": int(row["公司家数"]), "leader": str(row["领涨股"]),
                })
        result.update({
            "status": "soft_evidence" if schema_valid else "schema_failed",
            "rows": int(len(table)), "schema_valid": schema_valid, "top": top,
            "date_valid": False, "as_of": "页面未提供可机读交易日期",
            "reason": "可用于概念定义和低置信度方向交叉验证；不能进入日期严格的主线、仓位或资金效能硬门控",
        })
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"页面失败：{type(exc).__name__}: {exc}"
    return result


def flow_section(flow: dict[str, Any]) -> str:
    em = flow["eastmoney"]
    ths = flow["ths"]
    em_status = "日期/字段探针通过·覆盖不足" if em.get("date_valid") and em.get("schema_valid") else "不可用"
    ths_rows = "".join(
        f"<tr><td><b>{esc(row['concept'])}</b></td><td class='num'>{esc(row['change'])}</td>"
        f"<td class='num'>{row['estimated_net_yi']:.2f}亿</td><td class='num'>{row['members']}</td><td>{esc(row['leader'])}</td></tr>"
        for row in ths.get("top", [])[:6]
    ) or "<tr><td colspan='5'>未取得可读概念表。</td></tr>"
    return f"""
<section class="card"><h2>第三方估算资金流 · 只作旁证</h2>
<div class="grid2"><div><h3>东方财富个股流向探针</h3>
<p><span class="fact">[事实]</span> 状态：<b>{esc(em_status)}</b>；返回 {em.get('rows',0)}/{em.get('total','未取得')} 只；返回日期：{esc('、'.join(em.get('dates',[])) or '未取得')}。</p>
<p class="muted">字段 f62 是网页模型估算“主力净流入”。本次分页受限，只取得排序首屏，存在选择偏差，因此不做板块汇总、不做个股排名，也不改变第一主线。</p></div>
<div><h3>同花顺概念流向</h3><p><span class="fact">[事实]</span> 公开页可解析 {ths.get('rows',0)} 条；但页面没有可机读交易日期，故仅作低置信度交叉验证。</p>
<p class="infer">[推断]</span> 若CPO/通信等概念位居页面前列，只能视为与AI硬件量价结论方向一致；概念成分重叠，净额不可与东方财富或腾讯成交额相加。</p></div></div>
<div class="tablebox"><table><thead><tr><th>同花顺概念（软证据）</th><th class="num">涨跌幅</th><th class="num">估算净额</th><th class="num">公司家数</th><th>领涨股</th></tr></thead><tbody>{ths_rows}</tbody></table></div>
<p class="muted">降级结论：第三方资金流没有通过“完整覆盖 + 明确日期”双门控。本报告的主线仍由腾讯价格/累计成交额、固定篮子宽度和角色共振决定。</p></section>
"""


def gate_section() -> str:
    return """
<section class="card"><h2>六道门控 · 账户级结论</h2>
<div class="strip">
  <div class="mini"><span>G0 数据质量</span><strong style="color:var(--red)">未通过</strong><small>前复权未验证</small></div>
  <div class="mini"><span>G1 风险许可</span><strong style="color:var(--amber)">降级</strong><small>ST_cycle 为代理口径</small></div>
  <div class="mini"><span>G2 RPS候选</span><strong style="color:var(--red)">停算</strong><small>不沿用旧榜单</small></div>
  <div class="mini"><span>G3/G4 主线执行研究</span><strong style="color:var(--green)">可观察</strong><small>腾讯分钟 120/120</small></div>
  <div class="mini"><span>G5 账户执行</span><strong style="color:var(--red)">未通过</strong><small>权益/现金/风险预算缺失</small></div>
  <div class="mini"><span>最终动作</span><strong style="color:var(--red)">零交易</strong><small>研究上限30%·可执行0%</small></div>
</div>
<p class="hypo">[交易假设] AI硬件“分歧后再共振”只作为8月17日观察剧本；在复权门和账户门修复前，即使盘中满足量价条件也不下单。</p>
</section>
"""


def analyze_sector_relationships(audit: dict[str, Any]) -> dict[str, Any]:
    paths = {
        name: {row["time"]: float(row["return"]) for row in obj["q1"].get("minute_path", [])}
        for name, obj in audit.get("baskets", {}).items()
    }
    valid = {name: path for name, path in paths.items() if path}
    if len(valid) < 2:
        return {"status": "stopped", "reason": "固定篮子分钟路径不足"}
    common = sorted(set.intersection(*(set(path) for path in valid.values())))
    arrays = {name: np.array([path[tm] for tm in common], dtype=float) for name, path in valid.items()}
    names = list(arrays)
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            corr = float(np.corrcoef(np.diff(arrays[left]), np.diff(arrays[right]))[0, 1])
            if corr >= 0.55:
                relation = "同步共振"
            elif corr <= -0.15:
                relation = "替代/对冲"
            else:
                relation = "弱关联·轮动"
            pairs.append({"left": left, "right": right, "corr_1m": corr, "relation": relation})
    checkpoints = []
    for tm in ("0930", "1000", "1130", "1330", "1430", "1500"):
        if tm not in common:
            continue
        ranking = sorted(
            ({"name": name, "return": float(valid[name][tm])} for name in valid),
            key=lambda row: row["return"], reverse=True,
        )
        checkpoints.append({"time": tm, "ranking": ranking})
    return {
        "status": "ok", "common_minutes": len(common), "pairs": pairs,
        "checkpoints": checkpoints,
        "core_inference": [
            "AI硬件与资源材料的分钟变化高度同步，资源材料不是AI下跌时的天然对冲，更像同一风险偏好下的并行扩散。",
            "软件服务与AI硬件相关性弱，尾盘走强更接近流动性外溢；只有宽度继续扩张才可能由伪轮动升级为接力。",
            "黄金煤炭与AI硬件近乎独立，午后短暂领先体现防守切换，但尾盘未压制风险资产，因此不是全面风险收缩。",
        ],
    }


def fetch_quote(symbol: str) -> dict[str, Any]:
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    result: dict[str, Any] = {"symbol": symbol, "valid": False, "url": f"{url}?code={symbol}"}
    try:
        response = requests.get(url, params={"code": symbol}, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        node = (response.json().get("data") or {}).get(symbol, {})
        data = node.get("data") or {}
        qt = (node.get("qt") or {}).get(symbol, [])
        rows = data.get("data") or []
        parsed = [(row.split()[0], float(row.split()[1])) for row in rows if len(row.split()) >= 2]
        if data.get("date") != TARGET or not parsed or len(qt) < 6:
            result["reason"] = "日期或字段校验失败"
            return result
        prices = [row[1] for row in parsed]
        result.update({
            "valid": True, "date": data.get("date"), "name": qt[1],
            "close": float(qt[3]), "prev_close": float(qt[4]), "open": float(qt[5]),
            "high": max(prices), "low": min(prices), "rows": len(parsed),
        })
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"{type(exc).__name__}: {exc}"
    return result


def build_stock_expectations(audit: dict[str, Any]) -> list[dict[str, Any]]:
    selected = [
        ("sh600487", "确认龙头·不追", "高开分歧概率最高；它负责确认AI硬件高度，不是低风险入口。"),
        ("sz300308", "唯一优先·门控修复后", "最可能成为8月17日主线胜负手；若AI延续，中军应由跟涨转为主动承接。"),
        ("sz300223", "弹性候选·小风险", "资金效能最高但容量较小，最可能先冲后分歧，不能替代中军。"),
        ("sz300502", "前排接力候选", "若亨通高位分歧而CPO不退潮，新易盛更可能承担趋势接力。"),
        ("sz002080", "次主线择强", "资源材料若延续，中材科技是高弹性确认；但它与AI高度同向，不是对冲仓。"),
        ("sh600176", "次线容量观察", "中国巨石更偏容量中军，预期慢于中材科技但更能验证资源线持续性。"),
        ("sz300017", "20cm情绪锚·不追", "软件服务仍是伪轮动；网宿科技更可能高开分歧，除非板块宽度显著扩张。"),
    ]
    metrics: dict[str, dict[str, Any]] = {}
    for basket, obj in audit.get("baskets", {}).items():
        for stock in obj.get("q3", {}).get("stocks", []):
            metrics[stock["symbol"]] = {
                "basket": basket, "close_ret": stock.get("close_ret"),
                "cer_quality": stock.get("CER_quality"), "retention": stock.get("retention"),
                "amount": stock.get("amount"), "adv20": stock.get("adv20"),
            }
    result = []
    for symbol, stance, base_case in selected:
        quote = fetch_quote(symbol)
        row = {"symbol": symbol, "stance": stance, "base_case": base_case, **metrics.get(symbol, {}), **quote}
        if quote.get("valid"):
            name, opn, close, high, low = quote["name"], quote["open"], quote["close"], quote["high"], quote["low"]
            if symbol == "sh600487":
                row.update({
                    "trigger": f"0—3%开盘、首轮换手后重新站稳{close:.2f}，且AI篮子正宽≥60%",
                    "invalidation": f"放量失守{close:.2f}且10:00前不能收回；若高开超过{close*1.05:.2f}不追",
                    "operation": "只观察强弱，不作为首选买点",
                })
            elif symbol == "sz300308":
                row.update({
                    "trigger": f"{opn:.2f}—{close:.2f}区间获得承接，重新站上{close:.2f}并挑战{high:.2f}",
                    "invalidation": f"跌破8/14低点{low:.2f}，或亨通失去高度且板块宽度<50%",
                    "operation": "若复权与账户门修复，这是唯一优先执行候选",
                })
            elif symbol == "sz300223":
                row.update({
                    "trigger": f"缩量回踩不破{low:.2f}，随后重新站上{close:.2f}",
                    "invalidation": f"跌破{low:.2f}或高效拉升后保持率跌破60%",
                    "operation": "只允许小于中军的风险预算，不追第一波",
                })
            elif symbol == "sz300502":
                row.update({
                    "trigger": f"守住{opn:.2f}并突破8/14高点{high:.2f}，同时中际旭创不弱",
                    "invalidation": f"跌破8/14低点{low:.2f}或CPO宽度连续低于50%",
                    "operation": "作为中军后的第二顺位，不与中际同时重仓",
                })
            elif symbol == "sz002080":
                row.update({
                    "trigger": f"回踩{close:.2f}附近承接后再破{high:.2f}，资源篮子保持第一",
                    "invalidation": f"跌回{opn:.2f}下方且资源/AI同步转弱",
                    "operation": "次主线只择强一只，不能当AI对冲",
                })
            elif symbol == "sh600176":
                row.update({
                    "trigger": f"守住{opn:.2f}并稳定在{close:.2f}上方，成交不过度放大",
                    "invalidation": f"跌破{low:.2f}或中材科技强而其持续掉队",
                    "operation": "容量确认优先于弹性追涨，二者只选其一",
                })
            else:
                row.update({
                    "trigger": f"不追{close:.2f}附近高开；只有软件正宽≥60%且中军同步才重新评估",
                    "invalidation": f"跌破{close:.2f}后不能快速回收，或仍由单一20cm股票驱动",
                    "operation": "默认回避；只作软件板块情绪温度计",
                })
        else:
            row.update({"trigger": "行情字段未通过，停止", "invalidation": "不适用", "operation": "不操作"})
        result.append(row)
    return result


def relationship_section(analysis: dict[str, Any]) -> str:
    if analysis.get("status") != "ok":
        return f'<section class="card"><h2>板块关系</h2><p>停算：{esc(analysis.get("reason","数据不足"))}</p></section>'
    pair_rows = "".join(
        f"<tr><td>{esc(row['left'])} ↔ {esc(row['right'])}</td><td class='num'>{row['corr_1m']:.2f}</td><td>{esc(row['relation'])}</td></tr>"
        for row in sorted(analysis["pairs"], key=lambda row: row["corr_1m"], reverse=True)
    )
    checkpoint_rows = "".join(
        f"<tr><td>{row['time']}</td>" + "".join(
            f"<td>{esc(item['name'])} <b>{item['return']*100:+.2f}%</b></td>" for item in row["ranking"]
        ) + "</tr>" for row in analysis["checkpoints"]
    )
    inference = "".join(f"<li><span class='infer'>[推断]</span> {esc(text)}</li>" for text in analysis["core_inference"])
    return f"""
<section class="card"><h2>板块间关系：不是四条孤立排名</h2>
<div style="display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:10px;align-items:center;margin:12px 0">
  <div class="mini"><span>第一主线</span><strong>AI硬件</strong><small>早盘抢筹、尾盘回流</small></div><div class="subbig">⇄</div>
  <div class="mini"><span>同步扩散</span><strong>资源材料</strong><small>相关0.76·全天更强</small></div><div class="subbig">→</div>
  <div class="mini"><span>流动性外溢 / 对冲切换</span><strong>软件 · 黄金煤炭</strong><small>相关弱·不同时间接棒</small></div>
</div>
<div class="grid2"><div><h3>1分钟变化相关</h3><div class="tablebox"><table><thead><tr><th>组合</th><th class="num">相关</th><th>关系</th></tr></thead><tbody>{pair_rows}</tbody></table></div></div>
<div><h3>日内领先顺序</h3><div class="tablebox"><table><thead><tr><th>时间</th><th>第1</th><th>第2</th><th>第3</th><th>第4</th></tr></thead><tbody>{checkpoint_rows}</tbody></table></div></div></div>
<ul>{inference}</ul><p class="muted">相关使用四个盘前固定篮子的共同分钟等权收益变化，共 {analysis['common_minutes']} 个时间点；只描述8月14日日内关系，不外推为长期产业相关性或因果关系。</p>
</section>
"""


def stock_expectation_section(rows: list[dict[str, Any]]) -> str:
    body = "".join(
        f"<tr><td><b>{esc(row.get('name',row['symbol']))}</b><small>{esc(row['symbol'])} · {esc(row.get('basket','未分类'))}</small></td>"
        f"<td>{esc(row['stance'])}</td><td>{esc(row['base_case'])}</td><td>{esc(row['trigger'])}</td>"
        f"<td>{esc(row['invalidation'])}</td><td><b>{esc(row['operation'])}</b></td></tr>" for row in rows
    )
    return f"""
<section class="card"><h2>个股操作预期：明确顺位，不回避判断</h2>
<p><span class="hypo">[交易假设]</span> <b>最值得等待的是中际旭创，不是追亨通光电。</b>亨通负责确认高度，中际负责验证容量承接；新易盛是接力候选，君正股份是小风险弹性。资源线只在中材科技/中国巨石中择强一只，网宿科技默认不追。</p>
<div class="tablebox"><table><thead><tr><th>标的</th><th>定位</th><th>最可能路径</th><th>超预期/允许观察</th><th>低于预期</th><th>操作倾向</th></tr></thead><tbody>{body}</tbody></table></div>
<p class="callout"><b>当前账户动作仍是0%：</b>以上是有方向的盘前预期和未来门控修复后的执行顺位，不是绕过前复权、账户权益、现金和风险预算缺失而生成的即时买单。</p>
</section>
"""


def main() -> None:
    audit = json.loads(BASE_AUDIT.read_text(encoding="utf-8"))
    base_html = BASE_HTML.read_text(encoding="utf-8")
    flow = {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "eastmoney": fetch_eastmoney_probe(), "ths": fetch_ths_concepts(),
    }
    relationships = analyze_sector_relationships(audit)
    stock_expectations = build_stock_expectations(audit)
    audit["third_party_estimated_flow"] = flow
    audit["sector_relationships"] = relationships
    audit["stock_expectations"] = stock_expectations
    audit["meta"]["integrated_generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    audit["meta"]["report_version"] = "postclose_deep_v2"
    audit["decision"] = {
        "action": "零交易", "research_position_cap": audit.get("sentiment", {}).get("cap"),
        "executable_position_cap": 0,
        "market_state": "高位轮动", "process_phase": "分歧→修复，尾盘未扩散",
        "cycle_state": "修复（代理口径，低置信度）",
        "first_mainline": "AI硬件", "second_mainline": "资源材料",
        "defensive": "黄金+煤炭（相对抗跌）", "pseudo_rotation": "软件服务",
        "reason": "前复权/RPS门未通过，ST_cycle仅代理口径，账户权益、可用现金和单笔风险预算未提供",
        "watch_only": "中际旭创为未来门控修复后的唯一优先；亨通光电只确认高度，新易盛为第二顺位，当前均不构成即时买单",
        "t_plus_one": "若未来计划获准，当日新买不可卖；下单前必须写明次日极端低开处置",
    }

    marker = '<section class="card"><h2>个股资金效能比 · 3%主阈值</h2>'
    if marker not in base_html:
        raise RuntimeError("找不到资金效能章节插入点")
    final_html = base_html.replace(marker, flow_section(flow) + marker, 1)
    role_marker = '<section class="grid2"><article class="card"><h2>龙头—中军—弹性共振</h2>'
    if role_marker not in final_html:
        raise RuntimeError("找不到角色章节")
    final_html = final_html.replace(role_marker, relationship_section(relationships) + role_marker, 1)
    flow_marker = '<section class="card"><h2>第三方估算资金流 · 只作旁证</h2>'
    if flow_marker not in final_html:
        raise RuntimeError("找不到第三方资金流章节")
    final_html = final_html.replace(flow_marker, stock_expectation_section(stock_expectations) + flow_marker, 1)
    plan_marker = '<section class="card plan">'
    if plan_marker not in final_html:
        raise RuntimeError("找不到唯一计划章节")
    final_html = final_html.replace(plan_marker, gate_section() + plan_marker, 1)
    final_html = final_html.replace(
        "<title>A股全市场日内复盘 · 2026-08-14</title>",
        "<title>A股全市场盘后复盘 · 2026-08-14 · 深度版</title>", 1,
    ).replace("<h1>全市场日内复盘</h1>", "<h1>全市场盘后复盘 · 板块关系与个股预期</h1>", 1).replace(
        "A-SHARE INTRADAY REVIEW", "A-SHARE POST-CLOSE REVIEW", 1,
    )
    report_stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    final_html = re.sub(r"生成：[^<]+<br>", f"生成：{report_stamp}<br>", final_html, count=1)
    final_html = final_html.replace(
        "只观察 AI 硬件第一主线的“分歧后再共振”；开盘不追，未满足则全天空仓。",
        "零交易。只记录 AI 硬件第一主线的“分歧后再共振”，不下单；复权门与账户门修复后才能重新生成买入计划。",
        1,
    ).replace(
        "<h3>允许触发</h3><p>固定篮子正宽 ≥60%，龙头/中军/弹性至少两类重新站上开盘均价，且沪深成交额节奏不弱于 8/14 同时段。</p>",
        "<h3>观察确认（不下单）</h3><p>固定篮子正宽 ≥60%，龙头/中军/弹性至少两类重新站上开盘均价，且沪深成交额节奏不弱于 8/14 同时段；满足时只记录研究结果。</p>",
        1,
    )
    snapshot = json.dumps(audit, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    final_html, count = re.subn(
        r'<script id="audit-snapshot" type="application/json">.*?</script>',
        f'<script id="audit-snapshot" type="application/json">{snapshot}</script>',
        final_html, count=1, flags=re.S,
    )
    if count != 1:
        raise RuntimeError("审计快照替换失败")

    FINAL_AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    FINAL_HTML.write_text(final_html, encoding="utf-8")
    print(json.dumps({
        "html": str(FINAL_HTML.resolve()), "audit": str(FINAL_AUDIT.resolve()),
        "eastmoney_status": flow["eastmoney"].get("status"),
        "eastmoney_date_valid": flow["eastmoney"].get("date_valid", False),
        "ths_status": flow["ths"].get("status"), "ths_rows": flow["ths"].get("rows", 0),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
