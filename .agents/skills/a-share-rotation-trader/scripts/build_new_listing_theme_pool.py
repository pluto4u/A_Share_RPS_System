from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import math
import re
import struct
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REC = struct.Struct("<IIIIIfII")
THEME_VERSION = "new-listing-theme-v1.0-20260815"

# Versioned manual taxonomy. Fine labels intentionally stay broad when the
# issuer's exact product mix has not been rechecked against a prospectus.
THEMES: dict[str, tuple[str, str, str]] = {
    "603402": ("文旅消费", "旅游景区", "high"),
    "603352": ("汽车零部件", "汽车结构件", "medium"),
    "688785": ("半导体设备", "半导体设备及零部件", "medium"),
    "601112": ("先进材料", "玻纤及复合材料", "medium"),
    "001220": ("物流供应链", "仓储物流", "high"),
    "688712": ("医疗器械", "介入医疗器械", "medium"),
    "603284": ("轻工造纸", "造纸", "high"),
    "688818": ("商业航天", "航空航天装备", "medium"),
    "688816": ("机器视觉", "工业视觉检测", "high"),
    "301680": ("汽车零部件", "汽车电气材料", "medium"),
    "688781": ("显示产业", "Micro-OLED", "high"),
    "301682": ("电子元器件", "特种电子元器件", "medium"),
    "688813": ("新能源设备", "工业电极及专用设备", "medium"),
    "001257": ("战略金属", "小金属材料", "medium"),
    "301683": ("先进材料", "功能涂层材料", "medium"),
    "603459": ("PCB", "印制电路板", "high"),
    "688811": ("先进材料", "金属复合材料", "high"),
    "301696": ("电气自动化", "智能电气设备", "medium"),
    "603293": ("汽车电子", "汽车控制电子", "high"),
    "301666": ("存储", "企业级SSD", "high"),
    "301513": ("锂电设备", "浆料及混合设备", "medium"),
    "688820": ("先进封装", "晶圆级封装", "high"),
    "001312": ("纺织材料", "纺织面料", "high"),
    "688808": ("半导体设备", "测试测量设备", "high"),
    "301599": ("新能源设备", "智能专用设备", "medium"),
    "603407": ("化工材料", "化工原料", "medium"),
    "301531": ("非金属材料", "矿物制品", "medium"),
    "001393": ("电气连接", "电力电子连接件", "medium"),
    "001365": ("汽车电子", "汽车线束及连接", "high"),
    "603435": ("电子元器件", "电子组件", "medium"),
    "001237": ("电气设备", "电气及温控设备", "medium"),
    "688635": ("光通信", "光子器件", "high"),
    "301669": ("储能", "BMS及电池管理", "high"),
    "688797": ("半导体", "集成电路", "medium"),
    "001399": ("显示产业", "显示面板", "high"),
    "001248": ("新能源发电", "风光电力运营", "high"),
    "301583": ("机械基础件", "传动及机械零部件", "medium"),
    "688806": ("创新药", "抗体药物", "high"),
    "688825": ("存储", "DRAM", "high"),
    "301677": ("工业工具", "切削工具", "high"),
    "001232": ("PCB", "PCB制造服务", "high"),
    "603468": ("摩托车", "摩托车整车及零部件", "medium"),
    "301707": ("半导体", "芯片设计", "medium"),
    "688828": ("科学仪器", "高端测量仪器", "medium"),
    "301717": ("半导体材料", "超高纯材料", "high"),
}

INDUSTRY_FALLBACK = {
    "半导体": "半导体", "通信设备": "通信", "元器件": "电子元器件",
    "电气设备": "电气设备", "汽车配件": "汽车零部件", "医疗保健": "医疗器械",
    "生物制药": "创新药", "航空": "商业航天", "专用机械": "专用设备",
    "IT设备": "数字基础设施", "小金属": "战略金属", "新型电力": "新能源发电",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a yearly A-share new-listing theme pool")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--as-of", default="2026-08-14")
    p.add_argument("--tdx-root", default=r"C:\new_tdx\vipdoc")
    p.add_argument("--hq-cache", default=r"C:\new_tdx\T0002\hq_cache")
    p.add_argument("--incon", default=r"C:\new_tdx\incon.dat")
    p.add_argument("--output-dir", default="output")
    return p.parse_args()


def stage(days: int) -> str:
    if days <= 5:
        return "极端定价期"
    if days <= 20:
        return "价格发现期"
    if days <= 60:
        return "初步稳定期"
    if days <= 120:
        return "结构形成期"
    return "成熟次新"


def board(code: str) -> str:
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith("6"):
        return "沪市主板"
    return "深市主板"


def read_candidates(tdx_root: Path, year: int, as_of: int) -> list[dict]:
    rows = []
    for market in ("sh", "sz"):
        for path in (tdx_root / market / "lday").glob("*.day"):
            stem = path.stem.lower()
            if not (stem.startswith("sh6") or stem.startswith("sz0") or stem.startswith("sz3")):
                continue
            blob = path.read_bytes()
            if len(blob) < REC.size or len(blob) % REC.size:
                continue
            records = list(REC.iter_unpack(blob))
            records = [r for r in records if r[0] <= as_of]
            if not records:
                continue
            first = records[0][0]
            if not (year * 10000 + 101 <= first <= min(as_of, year * 10000 + 1231)):
                continue
            code = stem[2:8]
            amounts = [float(r[5]) for r in records]
            prior20 = amounts[-21:-1] if len(amounts) >= 21 else []
            rows.append({
                "symbol": f"{market}{code}", "code": code, "ts_code": f"{code}.{market.upper()}",
                "market": market, "board": board(code), "listing_date_local": str(first),
                "last_date_local": str(records[-1][0]), "trading_days": len(records),
                "stage": stage(len(records)), "adv20_ready": len(prior20) == 20,
                "adv20_amount_median_yi": float(np.median(prior20) / 1e8) if len(prior20) == 20 else None,
                "latest_amount_yi": float(amounts[-1] / 1e8),
            })
    return sorted(rows, key=lambda x: (x["listing_date_local"], x["code"]))


def fetch_quote_names(symbols: list[str]) -> tuple[dict[str, str], str | None]:
    names, timestamps = {}, []
    for start in range(0, len(symbols), 60):
        batch = symbols[start:start + 60]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        resp = requests.get(url, timeout=25, headers={"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        resp.encoding = "gbk"
        for match in re.finditer(r'v_(\w+)="([^"]*)"', resp.text):
            fields = match.group(2).split("~")
            if len(fields) > 30:
                names[match.group(1)] = fields[1]
                timestamps.append(fields[30])
    return names, max(timestamps) if timestamps else None


def fetch_first_kline(symbol: str, year: int, as_of: str) -> dict:
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,{year}-01-01,{as_of},500,qfq"}
    error = None
    for _ in range(3):
        try:
            resp = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            obj = resp.json()
            node = obj.get("data", {}).get(symbol, {})
            rows = node.get("qfqday") or node.get("day") or []
            qt = node.get("qt", {}).get(symbol, [])
            if obj.get("code") == 0 and rows:
                return {"first_date": rows[0][0].replace("-", ""), "rows": len(rows), "quote_ts": qt[30] if len(qt) > 30 else None, "url": resp.url, "error": None}
            error = "empty kline"
        except Exception as exc:
            error = str(exc)
    return {"first_date": None, "rows": 0, "quote_ts": None, "url": url, "error": error}


def clean_name(raw: str) -> str:
    return re.sub(r"^[NC]", "", raw or "").strip()


def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if pd.isna(value):
        return None
    return value


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def build_html(rows: list[dict], audit: dict) -> str:
    theme_counts = Counter(x["primary_theme"] for x in rows)
    board_counts = Counter(x["board"] for x in rows)
    stage_counts = Counter(x["stage"] for x in rows)
    top_themes = theme_counts.most_common(10)
    theme_cards = "".join(f'<div class="mini"><span>{esc(k)}</span><strong>{v}</strong></div>' for k, v in top_themes)
    board_text = " · ".join(f"{k}{v}只" for k, v in board_counts.items())
    stage_text = " · ".join(f"{k}{v}只" for k, v in stage_counts.items())
    options_theme = "".join(f'<option value="{esc(k)}">{esc(k)}（{v}）</option>' for k, v in sorted(theme_counts.items()))
    options_stage = "".join(f'<option value="{esc(k)}">{esc(k)}（{v}）</option>' for k, v in stage_counts.items())
    table_rows = []
    for x in rows:
        date = f"{x['listing_date_local'][:4]}-{x['listing_date_local'][4:6]}-{x['listing_date_local'][6:]}"
        adv = f"{x['adv20_amount_median_yi']:.2f}亿" if x["adv20_amount_median_yi"] is not None else "未满20日"
        status = "一致" if x["listing_date_match"] else ("腾讯未取得" if x["listing_date_tencent"] is None else "冲突")
        cls = "ok" if status == "一致" else "warn"
        table_rows.append(
            f'<tr data-theme="{esc(x["primary_theme"])}" data-stage="{esc(x["stage"])}" data-q="{esc(x["code"]+x["display_name"]+x["primary_theme"]+x["secondary_theme"]+x["industry_name"])}">'
            f'<td><b>{esc(x["code"])}</b><small>{esc(x["board"])}</small></td><td><b>{esc(x["display_name"])}</b><small>原名：{esc(x["name_raw"])}</small></td>'
            f'<td>{date}<small class="{cls}">{status}</small></td><td class="num">{x["trading_days"]}</td><td>{esc(x["stage"])}</td>'
            f'<td><b>{esc(x["primary_theme"])}</b><small>{esc(x["secondary_theme"])}</small></td><td>{esc(x["industry_name"])}</td>'
            f'<td>{esc(x["classification_confidence"])}</td><td class="num">{adv}</td></tr>'
        )
    rows_html = "".join(table_rows)
    snapshot = json.dumps(json_safe({"audit": audit, "rows": rows}), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>2026次新股题材池</title>
<style>:root{{--bg:#081421;--panel:#102237;--line:#294259;--ink:#edf5fb;--muted:#9bb0c3;--cyan:#42d6d1;--good:#38d493;--warn:#ffc35a;--paper:#f4f7fa;--dark:#172638}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--dark);font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}.wrap{{max-width:1440px;margin:auto;padding:22px}}.hero{{background:radial-gradient(circle at 90% 0,#174e66 0,transparent 34%),var(--bg);color:var(--ink);border-radius:18px;padding:25px}}.top{{display:flex;justify-content:space-between;gap:20px}}h1{{margin:3px 0;font-size:30px}}h2{{margin:0 0 12px}}h3{{margin:0 0 7px}}.eyebrow{{color:var(--cyan);font-weight:800;letter-spacing:.08em;font-size:12px}}.meta{{color:var(--muted);text-align:right}}.decision{{display:grid;grid-template-columns:1.1fr .9fr .9fr;gap:12px;margin-top:18px}}.darkcard{{background:#0c1d2dcc;border:1px solid var(--line);border-radius:13px;padding:15px}}.darkcard strong{{display:block;font-size:22px}}.badge{{display:inline-block;border:1px solid currentColor;border-radius:999px;padding:2px 8px;margin:4px 3px 0 0;font-size:12px}}.good,.ok{{color:var(--good)}}.warn{{color:var(--warn)}}.section{{margin:15px 0}}.card{{background:white;border:1px solid #dbe4ec;border-radius:14px;padding:17px;box-shadow:0 5px 16px #11283f0b}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.mini-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}.mini{{background:#eff5f8;border:1px solid #d7e2ea;border-radius:10px;padding:10px}}.mini span{{display:block;color:#64788c}}.mini strong{{font:800 20px ui-monospace,Consolas,monospace}}.callout{{border-left:4px solid #d39a28;background:#fff8e6;padding:11px 13px;border-radius:8px}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}input,select{{border:1px solid #cbd8e2;border-radius:9px;padding:9px 11px;background:white;font:inherit}}input{{min-width:260px;flex:1}}.tablebox{{overflow:auto;max-height:760px;border:1px solid #dce5ec;border-radius:10px}}table{{width:100%;border-collapse:collapse;min-width:1100px}}th,td{{padding:9px;border-bottom:1px solid #e1e8ee;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#edf3f7;color:#52677a;z-index:1}}td:first-child,th:first-child{{position:sticky;left:0;background:inherit;z-index:2}}td.num{{font-variant-numeric:tabular-nums;text-align:right}}small{{display:block;color:#718399}}ul{{margin:5px 0;padding-left:20px}}.foot{{color:#687b8f;border-top:1px solid #d8e2ea;padding:16px 0}}@media(max-width:900px){{.decision,.grid2,.mini-grid{{grid-template-columns:1fr}}.top{{flex-direction:column}}.meta{{text-align:left}}.wrap{{padding:11px}}}}@media print{{.filters{{display:none}}.tablebox{{max-height:none;overflow:visible}}th{{position:static}}.card,.hero{{box-shadow:none;break-inside:avoid}}}}</style></head>
<body><main class="wrap"><header class="hero"><div class="top"><div><div class="eyebrow">NEW LISTING THEME POOL · 2026</div><h1>2026次新股题材池</h1><p>沪深市场 · 数据截至 {esc(audit['as_of'])} 收盘 · 按上市阶段与主题分类</p></div><div class="meta">生成：{esc(audit['generated_at'])}<br>上市日：本地首条日线 + 腾讯首条日K<br>价格复权：原始未复权；跨日价格指标停算<br>名单置信度：{esc(audit['confidence'])}</div></div>
<div class="decision"><div class="darkcard"><div class="eyebrow">策略定位</div><strong>先建池，不因“次新”买入</strong><span>次新股只有进入全市场第一主线、角色成立、仓位状态允许时，才进入条件买入池。</span></div><div class="darkcard"><div class="eyebrow">当前覆盖</div><strong>{len(rows)}只沪深新股</strong><span>{esc(board_text)}；北交所暂未覆盖。</span></div><div class="darkcard"><div class="eyebrow">当前动作</div><strong>零交易 · 完善题材池</strong><span>未叠加当日市场状态、ST_cycle和账户风险预算，不输出买入名单。</span></div></div><span class="badge good">上市日交叉一致 {audit['date_match_count']}/{len(rows)}</span><span class="badge good">名称覆盖 {audit['name_count']}/{len(rows)}</span><span class="badge warn">RPS/MA/ATR停算</span><span class="badge warn">旧概念文件未采用</span></header>
<section class="section grid2"><article class="card"><h2>标准主题分布</h2><div class="mini-grid">{theme_cards}</div></article><article class="card"><h2>上市阶段</h2><p>{esc(stage_text)}</p><ul><li>1–5日：极端定价，只观察。</li><li>6–20日：价格发现，ADV20不足。</li><li>21–60日：可启用分钟CER，跨日价格指标仍受复权闸门约束。</li><li>61日以后：主线确认后才进入角色与买点比较。</li></ul></article></section>
<section class="section card"><h2>完整名单</h2><div class="filters"><input id="search" placeholder="搜索代码、名称、主题或行业"><select id="theme"><option value="">全部主题</option>{options_theme}</select><select id="stage"><option value="">全部阶段</option>{options_stage}</select><span id="shown" class="badge good">显示 {len(rows)} 只</span></div><div class="tablebox"><table><thead><tr><th>代码/市场</th><th>名称</th><th>上市日校验</th><th>交易日</th><th>阶段</th><th>主主题/细分</th><th>通达信行业</th><th>分类置信度</th><th>ADV20中位额</th></tr></thead><tbody id="tbody">{rows_html}</tbody></table></div></section>
<section class="section grid2"><article class="card"><h2>题材归类规则</h2><ol><li>稳定底层：通达信行业。</li><li>可交易主题：版本化人工映射，如存储、光通信、先进封装、PCB。</li><li>一个主主题，可有一个细分方向，避免板块统计重复计数。</li><li>本机概念文件日期为2025年，早于这些新股上市，故不使用。</li></ol><p class="callout">中置信度分类需要后续用招股书主营业务逐只复核；当前不会把公司简称猜测写成高置信度事实。</p></article><article class="card"><h2>后续选股接线</h2><ol><li>每日更新上市名单与交易日年龄。</li><li>次新主题与全市场第一主线比较。</li><li>上市满20日后，才用原始成交额中位数建立ADV20。</li><li>主线内按龙头/中军/弹性分组，再比较分钟CER和保持率。</li><li>仓位上限由市场与ST_cycle决定；次新风险只会压低仓位。</li></ol></article></section>
<section class="section grid2"><article class="card"><h2>事实</h2><p>截至8月14日，本地沪深日线首记录筛得 {len(rows)} 只2026年上市股票；腾讯名称覆盖 {audit['name_count']} 只，首条日K与本地日期一致 {audit['date_match_count']} 只。</p></article><article class="card"><h2>边界与风险</h2><p>北交所暂未覆盖；主题映射不是交易所行业标准；原始日线未验证前复权，不能据此计算RPS、MA、ATR和跨日收益。次新股还面临无涨跌幅期、筹码快速换手和T+1隔夜风险。</p></article></section>
<section class="section card"><h2>来源与版本</h2><p><b>本地日线：</b><code>{esc(audit['tdx_root'])}/{{sh,sz}}/lday/*.day</code>，首条记录用于候选上市日，amount原始元口径。<br><b>行业：</b><code>{esc(audit['industry_cfg'])}</code>。<br><b>腾讯：</b><code>qt.gtimg.cn/q=...</code> 获取名称；<code>web.ifzq.gtimg.cn/appstock/app/fqkline/get</code> 获取首条日K。<br><b>主题版本：</b>{THEME_VERSION}。主题中置信度项目仍需招股书复核。</p></section>
<footer class="foot">自包含HTML · 不依赖外部CSS、脚本或字体 · 本页是候选池建设，不构成买入建议。<script id="snapshot" type="application/json">{snapshot}</script></footer></main>
<script>(function(){{const q=document.getElementById('search'),t=document.getElementById('theme'),s=document.getElementById('stage'),rows=[...document.querySelectorAll('#tbody tr')],shown=document.getElementById('shown');function f(){{let n=0,needle=q.value.trim().toLowerCase();rows.forEach(r=>{{let ok=(!needle||r.dataset.q.toLowerCase().includes(needle))&&(!t.value||r.dataset.theme===t.value)&&(!s.value||r.dataset.stage===s.value);r.style.display=ok?'':'none';if(ok)n++}});shown.textContent='显示 '+n+' 只'}}[q,t,s].forEach(x=>x.addEventListener('input',f))}})();</script></body></html>'''


def main() -> None:
    args = parse_args()
    as_of_compact = args.as_of.replace("-", "")
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from macro.sector import load_industry_names, resolve_industry_name
    from rps_analyzer.industry_rps import load_industry_map

    rows = read_candidates(Path(args.tdx_root), args.year, int(as_of_compact))
    names, quote_latest = fetch_quote_names([x["symbol"] for x in rows])
    ind_df = load_industry_map(Path(args.hq_cache) / "tdxhy.cfg")
    ind_by_code = ind_df.drop_duplicates("ts_code").set_index("ts_code")["industry"].to_dict()
    ind_names = load_industry_names(Path(args.incon))
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch_first_kline, x["symbol"], args.year, args.as_of): x["symbol"] for x in rows}
        kline = {futures[f]: f.result() for f in concurrent.futures.as_completed(futures)}
    for x in rows:
        raw_name = names.get(x["symbol"], x["code"])
        x["name_raw"] = raw_name
        x["display_name"] = clean_name(raw_name)
        ind_code = ind_by_code.get(x["ts_code"])
        x["industry_code"] = ind_code
        x["industry_name"] = resolve_industry_name(ind_code, ind_names) if ind_code else "未分类"
        theme = THEMES.get(x["code"])
        if theme:
            x["primary_theme"], x["secondary_theme"], x["classification_confidence"] = theme
            x["classification_source"] = THEME_VERSION
        else:
            x["primary_theme"] = INDUSTRY_FALLBACK.get(x["industry_name"], x["industry_name"] or "待核验")
            x["secondary_theme"] = x["industry_name"] or "待核验"
            x["classification_confidence"] = "low"
            x["classification_source"] = "TDX industry fallback"
        check = kline.get(x["symbol"], {})
        x["listing_date_tencent"] = check.get("first_date")
        x["listing_date_match"] = check.get("first_date") == x["listing_date_local"]
        x["tencent_kline_rows"] = check.get("rows")
        x["tencent_quote_ts"] = check.get("quote_ts")
        x["listing_date_quality"] = "market_crosscheck" if x["listing_date_match"] else "conflict_or_missing"
    audit = {
        "year": args.year, "as_of": args.as_of, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tdx_root": str(Path(args.tdx_root)), "industry_cfg": str(Path(args.hq_cache) / "tdxhy.cfg"),
        "scope": "Shanghai and Shenzhen A-shares; Beijing Stock Exchange not included",
        "candidate_count": len(rows), "name_count": sum(x["display_name"] != x["code"] for x in rows),
        "date_match_count": sum(x["listing_date_match"] for x in rows), "quote_latest": quote_latest,
        "theme_version": THEME_VERSION, "confidence": "高（名单）/中高（题材）",
        "qfq_verified": False, "blocked_metrics": ["RPS", "MA", "ATR", "cross_day_return", "daily_CER"],
    }
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = args.as_of.replace("-", "")
    csv_path = output / f"new_listings_{args.year}_theme_pool_{stamp}.csv"
    json_path = output / f"new_listings_{args.year}_theme_pool_{stamp}_audit.json"
    html_path = output / f"new_listings_{args.year}_theme_pool_{stamp}.html"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig", na_rep="")
    json_path.write_text(json.dumps(json_safe({"audit": audit, "rows": rows}), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(build_html(rows, audit), encoding="utf-8")
    print(json.dumps({"html": str(html_path.resolve()), "csv": str(csv_path.resolve()), "audit": str(json_path.resolve()), "count": len(rows), "date_match": audit["date_match_count"], "names": audit["name_count"], "themes": Counter(x["primary_theme"] for x in rows).most_common()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
