#!/usr/bin/env python3
"""Classify the 2026-08-27 ZZ1234 exact screen using only existing output fields.

The project G0 price-adjustment gate is currently failed.  This script therefore
does not read price history or calculate new MA/ATR/return features.  It audits
the already-produced screen columns for research reporting only.
"""
from __future__ import annotations

import json
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
SOURCE = OUT / "siruipu_tdx_exact_20260827.csv"
DETAIL = OUT / "siruipu_tdx_exact_morphology_20260827.csv"
SUMMARY = OUT / "siruipu_tdx_exact_morphology_20260827.json"
REPORT = OUT / "a_share_analysis_20260827_2215_104_morphology.html"


def shape_bucket(distance: float) -> str:
    if distance <= 12:
        return "S1 低乖离确认"
    if distance <= 20:
        return "S2 标准突破"
    if distance <= 35:
        return "S3 加速突破"
    return "S4 高位亢奋"


def volume_bucket(ratio: float) -> str:
    if ratio >= 3:
        return "爆量"
    if ratio >= 1.8:
        return "强放量"
    if ratio >= 1:
        return "温和放量"
    return "缩量"


def rps_profile(row: pd.Series) -> str:
    r50, r120, r250 = row["RPS_50"], row["RPS_120"], row["RPS_250"]
    if r50 >= 80 and r120 >= 80 and r250 >= 80:
        return "全周期强"
    if r120 >= 80 and r250 >= 80 and r50 < 80:
        return "中长强/短期降速"
    if r120 >= 80:
        return "120日强"
    if r120 < 60:
        return "相对强度不足"
    return "中性"


def main() -> int:
    frame = pd.read_csv(SOURCE)
    numeric = ["close", "RPS_50", "RPS_120", "RPS_250", "vs_ma20_pct", "vol_ratio", "adv20_yi", "LOWAGE", "PEAKAGE"]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame["shape_bucket"] = frame["vs_ma20_pct"].map(shape_bucket)
    frame["volume_bucket"] = frame["vol_ratio"].map(volume_bucket)
    frame["rps_profile"] = frame.apply(rps_profile, axis=1)
    frame["rps_short_divergence"] = frame["RPS_120"] - frame["RPS_50"] >= 30
    frame["low_liquidity"] = frame["adv20_yi"] < 1.5
    frame["high_chase_risk"] = frame["vs_ma20_pct"] > 20
    frame["climax_risk"] = (frame["vs_ma20_pct"] > 35) | (frame["vol_ratio"] >= 3)
    frame["controlled_research_pool"] = (
        (frame["shape_bucket"] == "S1 低乖离确认")
        & (frame["RPS_120"] >= 80)
        & (frame["adv20_yi"] >= 1.5)
    )
    frame = frame.sort_values(["shape_bucket", "RPS_120", "RPS_250"], ascending=[True, False, False])
    frame.to_csv(DETAIL, index=False, encoding="utf-8-sig")

    shape_rows = []
    for key, group in frame.groupby("shape_bucket", sort=True):
        shape_rows.append({
            "shape": key,
            "count": int(len(group)),
            "qr": int((group["TDX_STAGE"] == "QR").sum()),
            "qqr": int((group["TDX_STAGE"] == "QQR").sum()),
            "rps120_ge80": int((group["RPS_120"] >= 80).sum()),
            "low_liquidity": int(group["low_liquidity"].sum()),
            "median_vs_ma20_pct": float(group["vs_ma20_pct"].median()),
            "top_rps120": group.head(12)[["ts_code", "name", "TDX_STAGE", "RPS_120", "vs_ma20_pct", "vol_ratio", "adv20_yi"]].to_dict("records"),
        })

    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": "2026-08-27",
        "source": str(SOURCE.as_posix()),
        "scope": "沪深A股，不含北交所；ZZ1234原式精确命中104只",
        "method": "仅使用既有输出字段做形态审计；G0失败后不新增MA/ATR/收益率计算",
        "shape_thresholds": {"S1": "距MA20<=12%", "S2": "12%<距MA20<=20%", "S3": "20%<距MA20<=35%", "S4": "距MA20>35%"},
        "counts": {
            "total": int(len(frame)),
            "qr": int((frame["TDX_STAGE"] == "QR").sum()),
            "qqr": int((frame["TDX_STAGE"] == "QQR").sum()),
            "rps_short_divergence": int(frame["rps_short_divergence"].sum()),
            "low_liquidity": int(frame["low_liquidity"].sum()),
            "high_chase_risk": int(frame["high_chase_risk"].sum()),
            "climax_risk": int(frame["climax_risk"].sum()),
            "controlled_research_pool": int(frame["controlled_research_pool"].sum()),
        },
        "shape_summary": shape_rows,
        "volume_counts": {str(k): int(v) for k, v in frame["volume_bucket"].value_counts().items()},
        "rps_profile_counts": {str(k): int(v) for k, v in frame["rps_profile"].value_counts().items()},
        "controlled_research_names": frame.loc[frame["controlled_research_pool"], ["ts_code", "name"]].to_dict("records"),
        "g0": "failed: output/qfq_verification.json pass_rate=0.75 at 2026-08-27 22:12:45",
        "disclaimer": "research morphology audit, not a trading rank or order list",
    }
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def stock_rows(group: pd.DataFrame) -> str:
        rows = []
        for row in group.itertuples(index=False):
            flags = []
            if row.rps_short_divergence:
                flags.append("短期降速")
            if row.low_liquidity:
                flags.append("流动性偏低")
            if row.climax_risk:
                flags.append("高潮风险")
            if not flags:
                flags.append("无额外红旗")
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(row.ts_code))}</td><td>{html.escape(str(row.name))}</td>"
                f"<td>{row.TDX_STAGE}</td><td>{row.close:.2f}</td><td>{row.vs_ma20_pct:+.2f}%</td>"
                f"<td>{row.vol_ratio:.2f}</td><td>{row.RPS_50:.2f}</td><td>{row.RPS_120:.2f}</td>"
                f"<td>{row.RPS_250:.2f}</td><td>{row.adv20_yi:.2f}亿元</td>"
                f"<td>{html.escape(row.rps_profile)}</td><td>{'、'.join(flags)}</td></tr>"
            )
        return "".join(rows)

    appendix = []
    for shape in ["S1 低乖离确认", "S2 标准突破", "S3 加速突破", "S4 高位亢奋"]:
        group = frame[frame["shape_bucket"] == shape]
        appendix.append(
            f"<details {'open' if shape.startswith('S1') else ''}><summary>{shape}（{len(group)}只）</summary>"
            "<div class='tablebox'><table><thead><tr><th>代码</th><th>名称</th><th>阶段</th><th>收盘</th>"
            "<th>距MA20</th><th>量比</th><th>RPS50</th><th>RPS120</th><th>RPS250</th><th>ADV20</th>"
            "<th>RPS画像</th><th>风险覆盖</th></tr></thead><tbody>"
            + stock_rows(group) + "</tbody></table></div></details>"
        )

    controlled = frame[frame["controlled_research_pool"]]
    controlled_rows = stock_rows(controlled)
    report = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>2026-08-27 ZZ1234命中104只形态分析</title><style>
:root{{--ink:#172033;--muted:#667085;--bg:#f4f7fb;--card:#fff;--line:#dbe3ef;--red:#d92d20;--green:#07835b;--amber:#b54708;--blue:#175cd3;--purple:#6941c6}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 'Segoe UI','Microsoft YaHei',Arial,sans-serif}}.wrap{{max-width:1450px;margin:auto;padding:18px}}.hero{{background:linear-gradient(135deg,#101828,#263754);color:#fff;border-radius:18px;padding:24px;box-shadow:0 16px 42px #10182822}}.hero h1{{font-size:clamp(28px,3.4vw,44px);margin:7px 0}}.hero p{{color:#d0d5dd;margin:5px 0}}.eyebrow{{font-size:12px;color:#aab6c9}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}.card{{grid-column:span 4;background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px}}.half{{grid-column:span 6}}.wide{{grid-column:span 12}}.hero .card{{background:#ffffff0d;border-color:#ffffff24}}.k{{font-size:12px;color:var(--muted)}}.hero .k{{color:#aab6c9}}.v{{font-size:25px;font-weight:800;margin-top:3px}}.bad{{color:#ff827a}}.ok{{color:#42d69f}}.warn{{color:#fdb022}}.small{{font-size:13px;color:var(--muted)}}.hero .small{{color:#cbd5e1}}h2{{font-size:22px;margin:27px 0 10px}}h3{{font-size:17px;margin:0 0 8px}}.tablebox{{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fff}}table{{width:100%;min-width:1050px;border-collapse:collapse}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}th{{background:#f8fafc;color:#475467;font-size:12px;position:sticky;top:0}}tr:last-child td{{border-bottom:0}}.callout{{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:14px 16px;color:#7c2d12}}.fact{{border-top:4px solid var(--blue)}}.infer{{border-top:4px solid var(--purple)}}.hypo{{border-top:4px solid var(--amber)}}.bar{{display:flex;height:34px;border-radius:8px;overflow:hidden;margin:12px 0;color:#fff;font-weight:750;text-align:center}}.bar span{{display:flex;align-items:center;justify-content:center;min-width:45px}}.s1{{background:#175cd3}}.s2{{background:#6941c6}}.s3{{background:#b54708}}.s4{{background:#b42318}}.tag{{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700;margin:2px 4px 2px 0}}.red{{background:#fee4e2;color:#b42318}}.amber{{background:#fef0c7;color:#93370d}}.blue{{background:#dbeafe;color:#1849a9}}.green{{background:#dcfae6;color:#067647}}details{{background:#fff;border:1px solid var(--line);border-radius:12px;margin:10px 0;overflow:hidden}}summary{{cursor:pointer;padding:13px 15px;font-weight:780}}details .tablebox{{border:0;border-top:1px solid var(--line);border-radius:0}}ul{{margin:7px 0;padding-left:20px}}.source{{font-size:12px;color:#475467;word-break:break-word}}.mono{{font-family:Consolas,monospace}}.footer{{font-size:12px;color:var(--muted);margin:24px 0 6px}}@media(max-width:900px){{.card,.half{{grid-column:span 12}}.wrap{{padding:10px}}table{{min-width:980px}}}}@media print{{body{{background:#fff}}.hero,.card{{box-shadow:none}}.card,details{{break-inside:avoid}}}}
</style></head><body><main class='wrap'><section class='hero'><div class='eyebrow'>形态审计 · 2026-08-27 22:15 北京时间 · 数据截至2026-08-27 · 同花顺QFQ研究输出 · 沪深A股，不含北交所</div>
<h1>104只不是104个机会：整体偏晚，风险分化很大</h1><p>37只低乖离、33只标准突破、22只加速、12只高位亢奋；形态研究最值得继续跟踪的是6只“低乖离＋RPS120≥80＋ADV20≥1.5亿元”股票，但全部不可执行。</p><div class='grid'>
<div class='card'><div class='k'>低乖离确认 S1</div><div class='v ok'>37只</div><div class='small'>其中29只流动性低于正式门槛</div></div>
<div class='card'><div class='k'>标准突破 S2</div><div class='v'>33只</div><div class='small'>赔率开始下降，13只RPS120≥80</div></div>
<div class='card'><div class='k'>加速＋亢奋 S3/S4</div><div class='v bad'>34只</div><div class='small'>距MA20超过20%，不适合机械追价</div></div>
<div class='card'><div class='k'>低流动性</div><div class='v warn'>53只</div><div class='small'>ADV20低于1.5亿元，T+1退出质量存疑</div></div>
<div class='card'><div class='k'>短期RPS降速</div><div class='v warn'>31只</div><div class='small'>RPS120比RPS50至少高30点</div></div>
<div class='card'><div class='k'>当前动作</div><div class='v bad'>零交易</div><div class='small'>G0仅75%通过；形态分层仅供研究</div></div></div></section>

<h2>1. 104只的形态分布</h2><section class='card wide'><div class='bar' aria-label='104只按MA20乖离分层：S1 37，S2 33，S3 22，S4 12'><span class='s1' style='width:35.58%'>S1 37</span><span class='s2' style='width:31.73%'>S2 33</span><span class='s3' style='width:21.15%'>S3 22</span><span class='s4' style='width:11.54%'>S4 12</span></div><p class='small'>分层只使用选股CSV已有的“距MA20”字段：S1≤12%；S2为12%—20%；S3为20%—35%；S4&gt;35%。</p></section>
<div class='tablebox'><table><thead><tr><th>层级</th><th>数量</th><th>QR/QQR</th><th>RPS120≥80</th><th>低流动性</th><th>中位乖离</th><th>形态解释</th><th>研究动作</th></tr></thead><tbody>
<tr><td>S1 低乖离确认</td><td>37</td><td>27 / 10</td><td>12</td><td>29</td><td>+8.90%</td><td>位置相对可控，但大部分容量不足</td><td>只保留RPS与流动性同时合格者观察</td></tr>
<tr><td>S2 标准突破</td><td>33</td><td>19 / 14</td><td>13</td><td>15</td><td>+15.54%</td><td>突破已被市场识别，赔率低于S1</td><td>等待回踩，不追连续拉升</td></tr>
<tr><td>S3 加速突破</td><td>22</td><td>13 / 9</td><td>14</td><td>8</td><td>+24.75%</td><td>强度较高但进入加速段</td><td>更适合已有仓位管理，不适合新开仓</td></tr>
<tr><td>S4 高位亢奋</td><td>12</td><td>6 / 6</td><td>12</td><td>1</td><td>+50.56%</td><td>高RPS与极端乖离共存，兑现风险最高</td><td>拒绝追涨，等待结构重置</td></tr>
</tbody></table></div>

<h2>2. 三个覆盖维度</h2><section class='grid'><div class='card fact'><h3>量能结构</h3><p>温和放量46只、强放量22只、爆量20只、缩量16只。爆量不自动代表更强：若与S3/S4同时出现，更可能是加速换手或高潮风险。</p></div><div class='card infer'><h3>RPS结构</h3><p>全周期强14只；中长强但短期降速21只；仅120日强16只；中性22只；相对强度不足31只。形态突破不等于横截面强势。</p></div><div class='card hypo'><h3>流动性结构</h3><p>53只ADV20低于1.5亿元，占51.0%。这类信号即使图形好看，也不满足正式G2流动性门槛，T+1下退出风险更高。</p></div></section>
<div class='callout'><strong>最关键的误区：</strong>QQR只是“量能更强的趋势确认”，并非更好的新开仓点。104只中有28只触发“距MA20&gt;35%或量比≥3”的高潮覆盖，不能把红柱、突破和放量机械相加成买入分数。</div>

<h2>3. 六只低乖离研究池：相对可控，但仍不可执行</h2><p>这六只同时满足S1、RPS120≥80、ADV20≥1.5亿元。它们只是104只中的形态研究子集，未经过第一行业主线、市场风险和账户门控。</p><div class='tablebox'><table><thead><tr><th>代码</th><th>名称</th><th>阶段</th><th>收盘</th><th>距MA20</th><th>量比</th><th>RPS50</th><th>RPS120</th><th>RPS250</th><th>ADV20</th><th>RPS画像</th><th>风险覆盖</th></tr></thead><tbody>{controlled_rows}</tbody></table></div>
<section class='grid'><div class='card half fact'><h3>相对更整齐</h3><ul><li>首华燃气：全周期RPS较强，但量比0.86，属于缩量确认。</li><li>英集芯：QQR、量比1.97，放量明确；RPS250为79.40，略低于80。</li><li>福立旺：RPS120较强，RPS250仅72.18，长期强度尚未完全同步。</li></ul></div><div class='card half infer'><h3>明显需要等待</h3><ul><li>纽威数控：流动性刚过1.5亿元门槛，RPS50低于RPS120。</li><li>罗博特科：容量充足，但缩量且RPS50显著落后于RPS120。</li><li>晋控煤业：低乖离、容量合格，但量比0.78且短期RPS降速。</li></ul></div></section>

<h2>4. 高位亢奋组：12只全部RPS120≥80，但不能追</h2><section class='card wide'><p>长盈通、汉森制药、神奇制药、百花医药、金健米业、肯特股份、深中华A、康希诺、海鸥住工、兆日科技、万向德农、楚天龙均位于S4。该组中位乖离+50.56%，6只为QQR。强度是真实事实，但买点赔率差是更重要的交易推断。</p><p><span class='tag red'>长盈通 +35.35%</span><span class='tag red'>汉森制药 +63.08%</span><span class='tag red'>神奇制药 +65.47%</span><span class='tag amber'>康希诺 量比3.56</span></p></section>

<h2>5. 事实、推断与交易假设</h2><section class='grid'><div class='card fact'><h3>事实</h3><p>104只中QR 65、QQR 39；34只乖离超过20%；53只低流动性；31只短期RPS明显落后；G0最新通过率75%。</p></div><div class='card infer'><h3>推断</h3><p>本批信号整体是“突破确认器”而非“早期发现器”。S1更适合继续研究，S3/S4更适合已有仓位状态管理。</p></div><div class='card hypo'><h3>交易假设</h3><p>未来若要改善赔率，应验证“QR后首次缩量回踩、未破关键结构、再转强”的二次入场，而不是继续收紧或放宽同一天突破阈值。</p></div></section>

<h2>6. 全部104只明细</h2><p class='small'>按形态层级、RPS120和RPS250排序；展开查看。风险标签是覆盖项，不是综合评分。</p>{''.join(appendix)}

<h2>7. 口径与限制</h2><section class='card wide'><ul><li>本报告只审计既有同花顺QFQ选股输出，不在G0失败后新增MA、ATR或收益率计算。</li><li>正式复权对账于2026-08-27 22:12重跑，16只中12只通过、4只失败，通过率75%，状态仍为unadjusted。</li><li>市场状态、ST_cycle、行业排序和账户信息未覆盖，因此没有可执行排序、仓位、入场价或止损价。</li><li>未使用估算资金流、过期概念归属或不可回测分钟指标；覆盖沪深A股，不含北交所。</li></ul><p class='source'>来源：<span class='mono'>output/siruipu_tdx_exact_20260827.csv</span>、<span class='mono'>output/qfq_verification.json</span>。衍生明细：<span class='mono'>output/siruipu_tdx_exact_morphology_20260827.csv</span>、<span class='mono'>output/siruipu_tdx_exact_morphology_20260827.json</span>。</p></section>
<h2>下一步值得验证的问题</h2><section class='card wide'><ol><li>S1六只研究池能否在行业第一主线门控后显著减少假突破？</li><li>RPS120强但RPS50降速，是健康换手还是趋势衰竭？需要未来+5/+10/+20日分层验证。</li><li>S3/S4若只用于持仓管理而不新开仓，组合最大回撤能否下降？</li><li>ADV20门槛从固定金额改为成交额横截面分位，是否更适应不同市场阶段？</li></ol></section>
<p class='footer'>研究用途，不构成投资建议。当前账户级动作仍为零交易；不操作的机会成本是可能错过部分加速延续，但原式历史超额证据不足。</p></main></body></html>"""
    REPORT.write_text(report, encoding="utf-8")

    print(json.dumps({"detail": str(DETAIL), "summary": str(SUMMARY), "report": str(REPORT), "counts": payload["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
