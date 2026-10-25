import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "output" / "a_share_analysis_20260919_analog_chip.json"
OUT = ROOT / "output" / "a_share_analysis_20260919_analog_chip_ma8.html"

data = json.loads(SRC.read_text(encoding="utf-8"))
by_code = {x["code"]: x for x in data["peers"]}

plans = [
    ("688052.SH", "A", "MA20结构最均衡，放量但乖离不大", "236–242", "收盘站稳258.30后回踩不破", "跌破230.02（MA60）"),
    ("688508.SH", "A", "RPS120强，靠近MA20，尚未明显过热", "85.5–88.0", "补量突破92.95后回踩", "跌破81.18（MA60）"),
    ("688601.SH", "A-", "均线多头、缩量运行，适合等承接", "58.4–60.0", "突破62.52且量能恢复", "跌破58.39后两日不收回"),
    ("300661.SZ", "B+", "放量启动最明确，但短线乖离接近上限", "112–115", "突破127.47后缩量回踩", "跌破111.74后不能迅速收回"),
    ("688368.SH", "B", "趋势修复领先，但波动率高、MA20/60仍缠绕", "139–143", "站稳151.00后回踩确认", "跌破139.83并持续两日"),
    ("688536.SH", "B", "中期强度高，但已明显高于MA20", "310–328", "突破362.40后不追，等回踩", "跌破310.38（MA20）先减"),
    ("688381.SH", "B-", "趋势强、均线多头，但5日涨幅和乖离偏大", "37–40", "突破43.16后等首次回踩", "跌破36.73（MA20）"),
    ("688209.SH", "C+", "RPS很强，但20日涨幅过大，只适合深回踩跟踪", "31–35", "38.84以上不追，等待均线抬升", "跌破31.36（MA20）"),
]

rows = []
for rank, (code, grade, why, zone, trigger, invalid) in enumerate(plans, 1):
    x = by_code[code]
    rows.append(
        f"<tr><td>{rank}</td><td><b>{x['name']}</b><br><code>{code}</code></td>"
        f"<td><span class='grade'>{grade}</span></td><td>{x['close']:.2f}</td>"
        f"<td>{x['ma10']:.2f} / {x['ma20']:.2f} / {x['ma60']:.2f}</td>"
        f"<td>{x['rps120']:.1f}</td><td>{x['vs_ma20_pct']:+.1f}%</td>"
        f"<td>{x['atr_pct']:.1f}%</td><td>{zone}</td><td>{trigger}</td><td>{invalid}</td><td>{why}</td></tr>"
    )

cards = "".join(
    f"<article><div class='rank'>#{i} · {by_code[c]['name']} <small>{g}</small></div>"
    f"<p>{why}</p><p><b>均线区：</b>{zone}<br><b>确认：</b>{trigger}<br><b>失效：</b>{invalid}</p></article>"
    for i, (c, g, why, zone, trigger, invalid) in enumerate(plans, 1)
)

html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>模拟芯片均线趋势8股跟踪表</title><style>
:root{{--ink:#152236;--muted:#617086;--bg:#f3f6f9;--card:#fff;--red:#c7353b;--amber:#9b6415;--line:#dbe3ea}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1420px;margin:auto;padding:22px}}header{{background:linear-gradient(135deg,#13243a,#294966);color:#fff;padding:28px;border-radius:18px}}
h1{{margin:.2rem 0;font-size:clamp(25px,4vw,42px)}}h2{{margin-top:30px}}.badge{{display:inline-block;padding:5px 10px;margin:4px;border:1px solid #7891a8;border-radius:999px}}
.decision{{margin-top:16px;padding:13px 16px;background:#ffe9ba;color:#5d3c00;border-radius:10px;font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:16px}}article{{background:var(--card);padding:16px;border:1px solid var(--line);border-radius:13px}}
.rank{{font-size:18px;font-weight:800}}small,.grade{{background:#e9f1f8;border-radius:6px;padding:2px 7px}}.scroll{{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:12px}}
table{{border-collapse:collapse;width:100%;min-width:1320px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#eaf0f5}}
.note{{background:#fff;padding:16px;border-left:5px solid var(--amber);margin:14px 0}}code{{white-space:nowrap}}footer{{color:var(--muted);margin:24px 0}}
@media(max-width:900px){{.cards{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}header{{padding:18px}}}}
@media print{{body{{background:#fff}}main{{max-width:none}}article{{break-inside:avoid}}}}
</style></head><body><main>
<header><div>模拟芯片 · 均线趋势跟踪清单</div><h1>8只值得持续跟踪的均线趋势股</h1>
<p><span class="badge">数据截至 {data['as_of']}</span><span class="badge">价格：本地v_daily_qfq前复权</span><span class="badge">沪深A股，不含北交所</span><span class="badge">动作：观察，不可执行</span></p>
<p>筛选重点不是短期涨幅，而是均线支撑是否可识别、RPS是否足够、回踩后能否定义失效。八只股票高度相关，不能同时当作八笔独立风险。</p>
<div class="decision">优先级：纳芯微、芯朋微、力芯微适合等MA20附近承接；圣邦股份适合等启动后的第一次回踩；思瑞浦、帝奥微、英集芯只跟踪，不追高。</div></header>
<h2>逐股行动卡</h2><div class="cards">{cards}</div>
<h2>完整均线表</h2><div class="scroll"><table><thead><tr><th>序</th><th>股票</th><th>级别</th><th>收盘</th><th>MA10/20/60</th><th>RPS120</th><th>距MA20</th><th>ATR20</th><th>回踩观察区</th><th>确认条件</th><th>趋势失效</th><th>入选理由</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>统一执行规则</h2>
<div class="note"><b>回踩买点：</b>价格进入观察区后，至少出现缩量止跌、收盘重新站回MA10或MA20、次日不低开破位三项中的两项；仅“碰到均线”不是买点。</div>
<div class="note"><b>趋势退出：</b>收盘跌破MA20先减半；连续两日位于MA20下方清掉趋势仓；收盘跌破MA60无条件退出。创新高后，保护线只上移不下移。</div>
<div class="note"><b>T+1与相关性：</b>同一行业同时持有多只并不等于分散。空仓者应只选一个主计划；当日新仓无法卖出，因此跳空追高比均线回踩承担更大的隔夜风险。</div>
<h2>审计边界</h2><p>“模拟芯片”是手工固定研究篮子，不是本地概念板块，不参与正式行业门控。G0本地运营一致性验证通过，但不是独立外部复权证明；正式第一行业缺失且未提供账户权益、现金和持仓状态，因此本页只提供跟踪顺序与条件，不生成仓位或买单。ST_cycle尚未完成样本外验证，仅作风险上限参考。</p>
<footer>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 数据源：本地TDX原始锚 + 本地同花顺v_daily_qfq与RPS。</footer>
</main></body></html>"""

OUT.write_text(html, encoding="utf-8")
print(OUT)
