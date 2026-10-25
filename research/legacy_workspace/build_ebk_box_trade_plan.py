#!/usr/bin/env python3
"""把 EBK 横盘候选转换为箱体交易行动卡（无账户股数）。"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "output" / "ebk_sideways_breakout_20260831.json"
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
OUT = ROOT / "output"


def f(x, d=2):
    return "未取得" if x is None or not np.isfinite(float(x)) else f"{float(x):.{d}f}"


def esc(x):
    return html.escape("" if x is None else str(x))


def main():
    source = json.loads(SRC.read_text(encoding="utf-8"))
    top = source["top15"]
    codes = [x["ts_code"] for x in top]
    raw = pd.read_parquet(PARQUET, columns=["trade_date", "ts_code", "open", "high", "low", "close", "vol"], filters=[("ts_code", "in", codes)])
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    asof = raw["trade_date"].max()
    rows = []
    for base in top:
        code = base["ts_code"]
        g = raw[raw["ts_code"] == code].sort_values("trade_date")
        pc = g["close"].shift(1)
        tr = pd.concat([g["high"] - g["low"], (g["high"] - pc).abs(), (g["low"] - pc).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(20, min_periods=20).mean().iloc[-1])
        ma20 = float(g["close"].rolling(20, min_periods=20).mean().iloc[-1])
        close = float(base["close"])
        lo, hi = float(base["box_low"]), float(base["box_high"])
        height = hi - lo
        pull_lo = lo + height * 0.25
        pull_hi = lo + height * 0.45
        pull_entry = (pull_lo + pull_hi) / 2
        range_stop = lo - atr
        breakout = hi * 1.01
        breakout_stop = hi - atr
        target_range = hi
        target_break = hi + height
        range_rr = (target_range - pull_entry) / max(pull_entry - range_stop, atr)
        break_rr = (target_break - breakout) / max(breakout - breakout_stop, atr)
        if close >= breakout:
            action = "已突破：等回踩箱顶不破"
        elif close >= hi * 0.96:
            action = "箱顶附近：不追，等突破或回落"
        else:
            action = "箱体内：等下半部缩量止跌"
        rows.append({**base, "atr20": round(atr, 3), "ma20": round(ma20, 3),
                     "pullback_low": round(pull_lo, 3), "pullback_high": round(pull_hi, 3),
                     "pullback_entry_mid": round(pull_entry, 3), "range_stop": round(range_stop, 3),
                     "range_target": round(target_range, 3), "range_rr": round(range_rr, 2),
                     "breakout_trigger": round(breakout, 3), "breakout_stop": round(breakout_stop, 3),
                     "breakout_target": round(target_break, 3), "breakout_rr": round(break_rr, 2),
                     "action": action})
    out_tag = asof.strftime("%Y%m%d")
    csv_path = OUT / f"ebk_box_trade_plan_{out_tag}.csv"
    json_path = OUT / f"ebk_box_trade_plan_{out_tag}.json"
    html_path = OUT / f"a_share_analysis_{out_tag}_ebk_box_trade_plan.html"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    meta = {"generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"), "as_of": asof.strftime("%Y-%m-%d"), "source": str(SRC), "rows": rows, "csv": str(csv_path), "html": str(html_path), "note": "箱体交易情景计划；未输入账户，未计算股数；价格来自本地 v_daily_qfq 导出"}
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    trs = []
    for i, r in enumerate(rows, 1):
        trs.append("<tr>" + "".join([
            f"<td>{i}</td><td><b>{esc(r['ts_code'])}</b><br>{esc(r['name'])}</td><td>{esc(r['industry_name'])}</td>",
            f"<td>{esc(r['action'])}</td><td>{f(r['close'])}</td><td>{f(r['pullback_low'])}–{f(r['pullback_high'])}</td>",
            f"<td>{f(r['range_stop'])}</td><td>{f(r['range_target'])}</td><td>{f(r['range_rr'],1)}R</td>",
            f"<td>{f(r['breakout_trigger'])}</td><td>{f(r['breakout_stop'])}</td><td>{f(r['breakout_target'])}</td><td>{f(r['breakout_rr'],1)}R</td>",
        ]) + "</tr>")
    html_text = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EBK趋势股箱体交易计划</title><style>
:root{{--bg:#f4f6f8;--ink:#17202a;--muted:#667085;--line:#e4e7ec;--navy:#101828;--amber:#b54708;--red:#d92d20}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 system-ui,'Microsoft YaHei',sans-serif}}.wrap{{max-width:1500px;margin:auto;padding:18px}}header{{background:var(--navy);color:#fff;border-radius:18px;padding:23px 25px;display:grid;grid-template-columns:1.8fr 1fr;gap:20px}}h1{{margin:0 0 8px;font-size:27px}}h2{{font-size:20px;margin:0 0 12px}}.sub{{color:#cbd5e1}}.action{{background:#1d2939;border:1px solid #344054;border-radius:14px;padding:16px}}.action b{{display:block;color:#fedf89;font-size:25px}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}.card{{grid-column:span 4;background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px}}.wide{{grid-column:span 12}}.half{{grid-column:span 6}}.muted{{color:var(--muted)}}.risk{{color:var(--amber);font-weight:700}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{border-collapse:collapse;width:100%;min-width:1500px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#f9fafb;position:sticky;top:0}}code{{background:#f2f4f7;padding:2px 5px;border-radius:4px}}ul{{padding-left:20px}}footer{{padding:20px 3px;color:var(--muted)}}@media(max-width:800px){{header{{grid-template-columns:1fr}}.card,.half,.wide{{grid-column:span 12}}.wrap{{padding:10px}}h1{{font-size:23px}}}}
</style></head><body><main class='wrap'><header><div><h1>EBK趋势股箱体交易计划</h1><div class='sub'>数据截至 {esc(meta['as_of'])} · 11只严格通过形态与流动性硬门的候选</div><p>价格使用同花顺本地 <code>v_daily_qfq</code> 前复权；成交量为原始口径。当前价格普遍靠近箱顶，计划以“回落买、突破买”二选一，禁止同时追单。</p></div><div class='action'><span>统一动作</span><b>当前不追箱顶</b><div>等待回踩缩量止跌，或收盘放量突破箱顶后再评估。</div></div></header>
<section class='grid'><article class='card half'><h2>箱体交易规则</h2><ol><li><b>回踩路径：</b>价格回到箱体下半部买入区，缩量止跌并重新站回短均线；目标先看箱顶。</li><li><b>突破路径：</b>收盘价超过箱顶约1%，量能至少达到20日均量1.3倍；次日不高开过度时执行。</li><li><b>统一失效：</b>收盘跌破箱底，或跌破MA20且次日不能收回；不向亏损仓位补仓。</li><li><b>赔率：</b>表中R值为静态测算，不含滑点、涨跌停无法成交和行业门控。</li></ol></article><article class='card half'><h2>账户与T+1</h2><p>没有账户权益、现金和单笔风险预算，不能计算股数。实际仓位应为：</p><p><code>股数 = 单笔风险金额 ÷ 有效止损距离</code></p><p>有效止损距离取计划距离、涨跌幅限制×0.6、1×ATR20三者最大。A股股票当日新买入仓位不能按当天卖出处理，必须预留隔夜风险。</p><p class='risk'>箱体策略是趋势单，不是抄底单；先确定失效位，再决定是否参与。</p></article>
<article class='card wide'><h2>11只股票行动卡</h2><div class='table-wrap'><table><thead><tr><th>排名</th><th>代码/名称</th><th>行业</th><th>当前动作</th><th>现价</th><th>回踩买入区</th><th>回踩止损</th><th>回踩目标</th><th>回踩赔率</th><th>突破触发</th><th>突破止损</th><th>突破目标</th><th>突破赔率</th></tr></thead><tbody>{''.join(trs)}</tbody></table></div></article>
<article class='card half'><h2>门控提醒</h2><p>这11只来自局部EBK名单，尚未替代全市场六门控。只有第一行业共振、执行质量和账户G5都通过，才可能进入条件买入；次主线标的只观察。</p><p>报告中的行业名称来自当前通达信行业映射，不使用失效的概念/估算资金流数据。</p></article><article class='card half'><h2>事实、推断、假设</h2><p><b>事实：</b>箱体、ATR、均线和RPS由本地前复权日K计算。</p><p><b>推断：</b>缩量小实体靠近箱顶说明抛压暂时收敛，但不代表必然突破。</p><p><b>交易假设：</b>突破必须由收盘和量能确认；若横盘继续或跌破箱底，执行退出而非等待“解套”。</p></article></section><footer>研究计划，不构成投资建议。生成时间 {esc(meta['generated_at'])}。</footer></main></body></html>"""
    html_path.write_text(html_text, encoding="utf-8")
    print(json.dumps({"as_of": meta["as_of"], "count": len(rows), "csv": str(csv_path), "json": str(json_path), "html": str(html_path), "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
