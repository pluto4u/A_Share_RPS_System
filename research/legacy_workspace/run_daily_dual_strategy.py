#!/usr/bin/env python3
"""每日双策略：右侧刚启动 + 启动后首次缩量回踩。

每天更新同一份 HTML 日志。复权 G0 未通过时，停止 RPS/均线/形态计算，
仍写入一条“零交易”审计记录；不存在跳过 G0 的参数。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DB = ROOT / "data" / "hithink" / "market.duckdb"
GATE = OUT / "qfq_verification.json"
ECOLOGY = OUT / "limit_ecology_latest.json"
HISTORY = OUT / "daily_dual_strategy_history.json"
REPORT = OUT / "a_share_analysis_daily_dual_strategy_journal.html"
LOG = OUT / "daily_dual_strategy_last_run.log"

VERIFY = ROOT / "skills" / "a-share-rotation-trader" / "scripts" / "verify_qfq.py"
BUILD_ECOLOGY = ROOT / "skills" / "a-share-rotation-trader" / "scripts" / "build_limit_ecology.py"
REFRESH_RPS = ROOT / "tmp" / "refresh_rps_hithink.py"
SCREEN_START = ROOT / "tmp" / "screen_siruipu_right_side_v2.py"
SCREEN_PULLBACK = ROOT / "tmp" / "screen_bottom_ambush.py"


def now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def run_step(name: str, cmd: list[str], *, allowed: tuple[int, ...] = (0,)) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    item = {
        "name": name,
        "returncode": proc.returncode,
        "ok": proc.returncode in allowed,
        "stdout": proc.stdout[-6000:],
        "stderr": proc.stderr[-4000:],
    }
    return item


def cli() -> str:
    return "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"


def latest_manifest_date() -> str:
    # manifest.json 是历史运营清单，增量同步可能已更新 DuckDB 但尚未重写清单；
    # 以生产库 v_daily 的最大交易日为准，避免 G1 日期与真实库日期错配而误报零交易。
    try:
        proc = subprocess.run(
            [cli(), "--db", str(DB), "db", "query",
             "--sql", "SELECT MAX(date) AS max_date FROM v_daily", "--format", "json"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        payload = json.loads(proc.stdout or "{}")
        rows = (payload.get("data") or []) if isinstance(payload, dict) else []
        if proc.returncode == 0 and rows and rows[0].get("max_date"):
            return str(rows[0]["max_date"])
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError):
        pass
    manifest = read_json(ROOT / "data" / "hithink" / "manifest.json", {}) or {}
    return str((manifest.get("latest_daily") or {}).get("date") or "未取得")


def load_start_candidates(tag: str) -> list[dict[str, Any]]:
    path = OUT / f"siruipu_right_side_v2_{tag}.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"ts_code": str})
    # 只保留“刚启动”的 DISCOVERY / UPGRADE；QR/QQR 属于趋势已确认，不再叫刚起来。
    df = df[df["role"].isin(["DISCOVERY", "UPGRADE"])].copy()
    df = df[df["adv20_yi"] >= 1.5]
    df["g2_rps_ready"] = df["RPS_120"] >= 80
    df["stage_cn"] = df["role"].map({"DISCOVERY": "预警转强", "UPGRADE": "放量启动"})
    df = df.sort_values(
        ["g2_rps_ready", "PRIORITY", "RPS_120", "adv20_yi"],
        ascending=[False, False, False, False],
    ).head(30)
    keep = ["ts_code", "name", "industry_name", "stage_cn", "g2_rps_ready", "close",
            "RPS_50", "RPS_120", "RPS_250", "vs_ma20_pct", "vol_ratio", "adv20_yi"]
    return df[keep].where(pd.notna(df[keep]), None).to_dict("records")


def load_pullback_candidates(tag: str) -> list[dict[str, Any]]:
    path = OUT / f"bottom_ambush_{tag}.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"ts_code": str})
    df = df[df["stage"] == "AMBUSH"].copy()
    df = df.sort_values(["RPS_50", "rps50_delta20", "ADV20"], ascending=False).head(30)
    df["adv20_yi"] = df["ADV20"] / 1e8
    keep = ["ts_code", "name", "industry_name", "stage", "close", "drawdown_pct",
            "atr_pct", "vs_ma20_pct", "vol_ratio", "RPS_50", "rps50_delta20",
            "RPS_120", "adv20_yi"]
    return df[keep].where(pd.notna(df[keep]), None).to_dict("records")


def fnum(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        if value is None or pd.isna(value):
            return "未取得"
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def candidate_table(rows: list[dict[str, Any]], kind: str) -> str:
    if not rows:
        return '<div class="empty">无可展示候选：可能是当日无命中，或前置数据闸门已阻断。</div>'
    if kind == "start":
        heads = ["代码/名称", "行业", "阶段", "G2 RPS", "收盘", "RPS50/120/250", "距MA20", "量比", "ADV20"]
        body = []
        for r in rows:
            ready = "达标" if r.get("g2_rps_ready") else "未达80"
            body.append("<tr>" + "".join([
                f"<td><b>{esc(r.get('ts_code'))}</b><br>{esc(r.get('name'))}</td>",
                f"<td>{esc(r.get('industry_name')) or '未映射'}</td>",
                f"<td>{esc(r.get('stage_cn'))}</td>",
                f"<td>{ready}</td>",
                f"<td>{fnum(r.get('close'), 2)}</td>",
                f"<td>{fnum(r.get('RPS_50'))} / {fnum(r.get('RPS_120'))} / {fnum(r.get('RPS_250'))}</td>",
                f"<td>{fnum(r.get('vs_ma20_pct'), 1, '%')}</td>",
                f"<td>{fnum(r.get('vol_ratio'), 2)}</td>",
                f"<td>{fnum(r.get('adv20_yi'), 1, '亿')}</td>",
            ]) + "</tr>")
    else:
        heads = ["代码/名称", "行业", "收盘", "回撤", "ATR", "距MA20", "量比", "RPS50改善", "RPS120", "ADV20"]
        body = []
        for r in rows:
            body.append("<tr>" + "".join([
                f"<td><b>{esc(r.get('ts_code'))}</b><br>{esc(r.get('name'))}</td>",
                f"<td>{esc(r.get('industry_name')) or '未映射'}</td>",
                f"<td>{fnum(r.get('close'), 2)}</td>",
                f"<td>{fnum(r.get('drawdown_pct'), 1, '%')}</td>",
                f"<td>{fnum(r.get('atr_pct'), 1, '%')}</td>",
                f"<td>{fnum(r.get('vs_ma20_pct'), 1, '%')}</td>",
                f"<td>{fnum(r.get('vol_ratio'), 2)}</td>",
                f"<td>{fnum(r.get('RPS_50'))}（20日+{fnum(r.get('rps50_delta20'))}）</td>",
                f"<td>{fnum(r.get('RPS_120'))}</td>",
                f"<td>{fnum(r.get('adv20_yi'), 1, '亿')}</td>",
            ]) + "</tr>")
    return "<div class='table-wrap'><table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"


def render_report(history: list[dict[str, Any]]) -> str:
    latest = history[-1]
    g0 = latest["g0"]
    eco = latest["ecology"]
    blocked = latest["action"] == "零交易"
    action_class = "blocked" if blocked else "watch"
    g0_text = "通过" if g0.get("price_adjustment") == "verified_qfq" else "未通过"
    cards = []
    for item in reversed(history[-30:]):
        cards.append(
            f"<tr><td>{esc(item.get('data_asof'))}</td><td>{esc(item.get('action'))}</td>"
            f"<td>{esc(item.get('g0', {}).get('price_adjustment'))}</td>"
            f"<td>{len(item.get('strategy_a', []))}</td><td>{len(item.get('strategy_b', []))}</td>"
            f"<td>{esc(item.get('ecology', {}).get('cycle_state') or '未取得')}</td>"
            f"<td>{esc(item.get('reason'))}</td></tr>"
        )
    run_errors = [x for x in latest.get("steps", []) if not x.get("ok")]
    error_text = "；".join(f"{x['name']}退出码{x['returncode']}" for x in run_errors) or "无"
    bad_codes = [d.get("ts_code") for d in g0.get("details", []) if d.get("status") != "verified_qfq"]
    bad_codes_text = "、".join(str(x) for x in bad_codes) or "无"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股每日双策略日志</title><style>
:root{{--bg:#f4f6f8;--ink:#17202a;--muted:#667085;--card:#fff;--line:#e4e7ec;--red:#d92d20;--green:#039855;--amber:#b54708;--navy:#101828}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 system-ui,"Microsoft YaHei",sans-serif}}
.wrap{{max-width:1420px;margin:auto;padding:18px}}header{{background:var(--navy);color:#fff;border-radius:18px;padding:22px 24px;display:grid;grid-template-columns:2fr 1fr;gap:20px}}
h1{{margin:0 0 8px;font-size:27px}}h2{{font-size:20px;margin:0 0 13px}}h3{{margin:0 0 8px}}.sub{{color:#cbd5e1}}.action{{border-radius:14px;padding:18px;background:#1d2939;border:1px solid #344054}}.action.blocked b{{color:#fda29b}}.action.watch b{{color:#fedf89}}.action b{{font-size:28px;display:block}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}.card{{grid-column:span 4;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px;break-inside:avoid}}.wide{{grid-column:span 12}}.half{{grid-column:span 6}}.metric{{font:700 25px ui-monospace,Consolas,monospace}}.muted{{color:var(--muted)}}.risk{{color:var(--amber);font-weight:700}}.ok{{color:var(--green)}}.bad{{color:var(--red)}}
ul{{padding-left:20px;margin:7px 0}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;min-width:880px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#f9fafb;position:sticky;top:0}}.empty{{padding:18px;background:#f9fafb;border:1px dashed #98a2b3;border-radius:10px;color:var(--muted)}}code{{background:#f2f4f7;padding:2px 5px;border-radius:4px}}footer{{padding:24px 3px;color:var(--muted)}}
@media(max-width:800px){{header{{grid-template-columns:1fr}}.card,.half,.wide{{grid-column:span 12}}.wrap{{padding:10px}}h1{{font-size:23px}}}}
@media print{{body{{background:#fff}}.wrap{{max-width:none}}.card,header{{box-shadow:none}}}}
</style></head><body><main class="wrap">
<header><div><h1>沪深A股每日双策略日志</h1><div class="sub">策略A：右侧刚启动　｜　策略B：启动后首次缩量回踩　｜　数据截至 {esc(latest['data_asof'])}</div><p>生成时间 {esc(latest['generated_at'])}；价格口径必须先通过真实前复权对账；覆盖沪深A股，不含北交所。</p></div>
<div class="action {action_class}"><span>当前统一动作</span><b>{esc(latest['action'])}</b><div>{esc(latest['reason'])}</div></div></header>

<section class="grid">
<article class="card"><div class="muted">G0 前复权闸门</div><div class="metric {'ok' if g0_text == '通过' else 'bad'}">{g0_text}</div><div>状态：{esc(g0.get('price_adjustment','未取得'))}</div><div>通过率：{fnum((g0.get('pass_rate') or 0)*100,1,'%')}</div></article>
<article class="card"><div class="muted">量化周期状态</div><div class="metric">{esc(eco.get('cycle_state') or '未取得')}</div><div>ST_cycle：{fnum(eco.get('ST_cycle'))}；斜率：{fnum(eco.get('slope3'))}</div><div>formal：{esc(eco.get('st_cycle_formal','未取得'))}；仓位上限参考：{fnum(eco.get('cycle_position_cap') or eco.get('cycle_position_cap_pct'),1,'%')}</div></article>
<article class="card"><div class="muted">双策略命中</div><div class="metric">A {len(latest['strategy_a'])} / B {len(latest['strategy_b'])}</div><div>A只统计转强当天；B只统计2～8日后首次回踩。</div></article>

<article class="card half"><h2>策略A｜右侧刚起来</h2><ul><li>深度调整后，低点距离当前不超过15日，先确认不是高位加速。</li><li>收盘站上MA20，MA5高于MA10且继续上行。</li><li>突破前5日高为预警；突破前10日高并放量为启动。</li><li>距MA20不超过4%（预警）/15%（启动），ADV20至少1.5亿元。</li><li>RPS120≥60进入研究池；≥80才满足G2，但仍须第一行业、执行质量和账户G5。</li></ul><p class="risk">用途：找“由弱转强的当天”，不追QR/QQR的趋势加速段。</p></article>
<article class="card half"><h2>策略B｜右侧回踩上车</h2><ul><li>先有TURN：缩量筑底后站上MA20或突破前10日高，量能1.05～2.50倍。</li><li>只看TURN后2～8日，避免同日冲动追入。</li><li>最低价触及MA20上方2%以内，收盘不低于MA20的99.5%。</li><li>成交量≤20日均量95%，单日跌幅不超过2%。</li><li>RPS50≥40且20日改善至少15点，ADV20至少1.5亿元。</li></ul><p class="risk">用途：用确认后的第一次缩量回踩换取更清楚的失效位。</p></article>

<article class="card wide"><h2>今天的策略A候选</h2><p class="muted">以下仅为研究候选；未完成第一行业共振与账户G5时，全部不可直接下单。</p>{candidate_table(latest['strategy_a'], 'start')}</article>
<article class="card wide"><h2>今天的策略B候选</h2><p class="muted">回踩信号不是抄底；收盘有效跌破MA20、结构低点或MA60时按趋势账本退出。</p>{candidate_table(latest['strategy_b'], 'pullback')}</article>

<article class="card half"><h2>共同门控与执行纪律</h2><ol><li>G0前复权不通过：MA/ATR/RPS/回撤/形态全部停算，零交易。</li><li>G1情绪数据日期、覆盖率不合格：零交易；formal=false时仓位上限不高于35%。</li><li>只有全市场第一行业内候选才可能执行；次行业只观察。</li><li>未提供账户、触发价、ATR和失效位：不生成伪精确仓位。</li><li>T+1：当天新仓不能卖；不向亏损仓位补仓。</li></ol></article>
<article class="card half"><h2>当前阻断与修复</h2><p><b>G0失败代码：</b>{esc(bad_codes_text)}</p><p><b>流水线异常：</b>{esc(error_text)}</p><p><b>当前风险：</b>{esc(latest.get('reason'))}</p><p><b>机会成本：</b>严格门控可能错过快速V形反弹；保留门控是为了避免用错误复权把除权跳空当成趋势信号。</p></article>

<article class="card wide"><h2>近30个交易日运行记录</h2><div class="table-wrap"><table><thead><tr><th>数据日</th><th>动作</th><th>G0</th><th>A命中</th><th>B命中</th><th>周期</th><th>原因</th></tr></thead><tbody>{''.join(cards)}</tbody></table></div></article>
<article class="card wide"><h2>事实、推断与交易假设</h2><p><b>事实：</b>同花顺本地库负责日K与RPS输入；通达信原始日线与本地前复权视图做G0运营一致性校验；成交额使用原始元口径。</p><p><b>推断：</b>首次站上MA20代表供需由弱转强，随后缩量回踩代表抛压减弱。这是可检验的统计假设，不是必涨事实。</p><p><b>交易假设：</b>若候选同时位于第一行业、回踩不破失效位且预期收益至少为风险2倍，才进入条件买入评估；否则保持观察。</p></article>
</section><footer>本页为研究与审计日志，不构成投资建议。每日自动化只更新候选与证据，不代表自动下单。</footer>
</main></body></html>"""


def save_history(entry: dict[str, Any]) -> list[dict[str, Any]]:
    history = read_json(HISTORY, []) or []
    # 同一交易日重复运行时覆盖，防止周末/重跑形成伪交易日。
    history = [x for x in history if x.get("data_asof") != entry.get("data_asof")]
    history.append(entry)
    history = sorted(history, key=lambda x: (x.get("data_asof", ""), x.get("generated_at", "")))[-260:]
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def main() -> int:
    ap = argparse.ArgumentParser(description="每日双策略统一日报")
    ap.add_argument("--skip-sync", action="store_true", help="仅用于本地开发验证；自动任务不使用")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []

    if not args.skip_sync:
        steps.append(run_step("同花顺增量同步", [cli(), "--db", str(DB), "data", "sync", "--format", "json"]))
        if steps[-1]["ok"]:
            steps.append(run_step("同花顺库校验", [cli(), "--db", str(DB), "data", "validate", "--format", "json"]))

    # G0和G1每天重算；G0无跳过或覆盖通道。
    # verify_qfq 约定：0=verified_qfq，1=校验正常完成但闸门未通过。
    # 二者都不是程序故障；能否计算价格类指标只读取 JSON 中的 price_adjustment。
    steps.append(run_step(
        "前复权G0", [sys.executable, str(VERIFY), "--sample", "16"], allowed=(0, 1)
    ))
    steps.append(run_step("涨停生态G1", [sys.executable, str(BUILD_ECOLOGY), "--history", "500"]))
    gate = read_json(GATE, {}) or {}
    ecology = read_json(ECOLOGY, {}) or {}
    data_asof = latest_manifest_date()
    if ecology.get("as_of_trade_date") and data_asof == "未取得":
        data_asof = ecology["as_of_trade_date"]

    hard_pipeline_error = any(not x["ok"] for x in steps)
    g0_ok = gate.get("price_adjustment") == "verified_qfq"
    g1_ok = (
        ecology.get("as_of_trade_date") == data_asof
        and float(ecology.get("coverage") or 0) >= 0.80
    )
    strategy_a: list[dict[str, Any]] = []
    strategy_b: list[dict[str, Any]] = []

    if not hard_pipeline_error and g0_ok and g1_ok:
        steps.append(run_step("刷新RPS", [sys.executable, str(REFRESH_RPS)]))
        if steps[-1]["ok"]:
            steps.append(run_step("策略A右侧启动", [sys.executable, str(SCREEN_START)]))
            # bottom脚本成功码2代表G0阻断，但正常路径不应出现。
            steps.append(run_step("策略B首次回踩", [sys.executable, str(SCREEN_PULLBACK)], allowed=(0,)))
        hard_pipeline_error = any(not x["ok"] for x in steps)
        if not hard_pipeline_error:
            tag = data_asof.replace("-", "")
            strategy_a = load_start_candidates(tag)
            strategy_b = load_pullback_candidates(tag)

    if hard_pipeline_error:
        action = "零交易"
        reason = "每日流水线存在失败步骤；候选不发布，先修复数据或程序。"
    elif not g0_ok:
        action = "零交易"
        reason = gate.get("blocker") or "G0前复权未通过。"
    elif not g1_ok:
        action = "零交易"
        reason = "G1情绪生态日期或覆盖率不合格。"
    else:
        action = "观察，不可执行"
        reason = "双策略候选已生成；尚未完成第一行业共振与账户G5，不产生买单。"

    entry = {
        "generated_at": now_cn(),
        "data_asof": data_asof,
        "action": action,
        "reason": reason,
        "g0": gate,
        "ecology": ecology,
        "strategy_a": strategy_a,
        "strategy_b": strategy_b,
        "steps": [{k: v for k, v in x.items() if k not in {"stdout", "stderr"}} for x in steps],
        "universe": "沪深A股，不含北交所",
        "account_state": "未提供",
    }
    history = save_history(entry)
    REPORT.write_text(render_report(history), encoding="utf-8")
    LOG.write_text("\n\n".join(
        f"[{x['name']}] rc={x['returncode']} ok={x['ok']}\n{x['stdout']}\n{x['stderr']}" for x in steps
    ), encoding="utf-8")
    print(json.dumps({
        "generated_at": entry["generated_at"], "data_asof": data_asof,
        "action": action, "reason": reason,
        "strategy_a_count": len(strategy_a), "strategy_b_count": len(strategy_b),
        "report": str(REPORT), "history": str(HISTORY), "log": str(LOG),
    }, ensure_ascii=False, indent=2))
    return 0 if not hard_pipeline_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
