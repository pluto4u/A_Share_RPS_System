"""Readable Chinese summary from an existing, reconciled research run."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
from html import escape
from pathlib import Path

import pandas as pd

LABELS = {"ongoing": "趋势进行中", "breakout": "整理后直接突破", "retest": "突破后缩量回踩再转强"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    audit = json.loads((args.output / "audit.json").read_text(encoding="utf-8"))
    checked = json.loads((args.output / "validation.json").read_text(encoding="utf-8"))
    if checked["status"] != "reconciled":
        raise ValueError("Reconciliation required before rendering summary")
    table = pd.read_csv(args.output / "comparison.csv")
    part = table.loc[table.window == "historical_2022_plus"].copy()
    part["strategy"] = part.strategy.map(LABELS)
    part["market_filter"] = part.market_filter.map({True: "沪深300>MA60", False: "不设"})
    part = part[
        [
            "strategy",
            "exit_ma",
            "market_filter",
            "cagr_pct",
            "max_drawdown_pct",
            "closed_trades",
            "open_positions",
            "average_exposure_pct",
        ]
    ]
    part.columns = [
        "研究模型",
        "退出均线",
        "环境过滤",
        "年化%",
        "最大回撤%",
        "完成交易",
        "期末持仓",
        "平均资金敞口%",
    ]
    events = pd.read_csv(args.output / "fixed_horizon_events.csv")
    events["strategy"] = events.strategy.map(LABELS)
    events.columns = [
        "研究模型",
        "市场交易日",
        "全部信号",
        "可入场且成熟",
        "未成熟",
        "平均毛估值收益%",
        "月块重抽样下界%",
        "月块重抽样上界%",
    ]
    denom = pd.read_csv(args.output / "rps_denominator.csv")
    css = (
        "body{background:#f4f6f8;color:#1e293b;font:15px/1.7 system-ui;margin:0}"
        "main{max-width:1200px;margin:auto;padding:28px}h1{font-size:27px}"
        "section{background:white;padding:20px;margin:18px 0;border-radius:12px}"
        "table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:14px}"
        "th,td{padding:10px;border-bottom:1px solid #e2e8f0;text-align:right}"
        "th:first-child,td:first-child{text-align:left}thead{background:#e8eef5}"
        ".scroll{overflow-x:auto}.warning{border-left:4px solid #b45309}"
        "a{color:#1d4ed8}small{color:#64748b}"
    )
    html = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        f"<title>日线右侧研究结果</title><style>{css}</style><main>"
    )
    html += (
        "<h1>日线右侧研究：突破与回踩是否值得等待？</h1>"
        f"<small>数据截至 {escape(audit['as_of'])} · 本地通达信 · "
        "历史模拟，非实时交易建议</small>"
    )
    html += (
        "<section class='warning'><h2>当前能得出的结论</h2>"
        "<p>未证实稳定盈利优势。回踩模型参与率更低，因此小回撤不等于更好的选股能力。"
        "不能从这些回顾性对照中挑出一个收益最高的参数，再称其为独立验证。</p></section>"
    )
    html += (
        "<section><h2>数据与账本核对</h2>"
        f"<p>扫描 {audit['panel']['files_seen']} 个股票文件；纳入 {audit['panel']['kept']} 只，"
        f"特殊复权类别剔除 {audit['panel']['excluded']} 只。最新 RPS 有效本地分母 "
        f"{int(denom.valid_local_universe.iloc[-1])}。这是本地可用证券集合，"
        "不是交易所完整历史市场。</p>"
    )
    html += (
        f"<p>独立核对 {checked['portfolio_sessions_checked']} 个组合交易日："
        f"最大现金差 {checked['max_cash_error']:.8f} 元，"
        f"最大权益差 {checked['max_equity_error']:.8f} 元。"
        "验证的是账本一致性，不是样本无偏或实际可成交性。</p></section>"
    )
    html += (
        "<section><h2>2022 年以后的回顾性组合对照</h2>"
        "<p>各组合独立模拟；初始本金、仓位和费用相同。完整参数与日期见审计。"
        "年化含期末持仓估值，完成交易数不含期末持仓。</p><div class='scroll'>"
    )
    html += part.round(2).to_html(index=False, na_rep="缺", border=0) + "</div></section>"
    html += (
        "<section><h2>信号本身：固定期限毛估值收益</h2>"
        "<p>全历史信号，与上方 2022 年以后的组合不是同一统计总体。"
        "20/60 个市场交易日；未扣成本，不代表那天可卖出。"
        "同股重复信号未去重，区间只能作为探索性描述。</p><div class='scroll'>"
    )
    html += events.round(2).to_html(index=False, na_rep="缺", border=0) + "</div></section>"
    html += "<section class='warning'><h2>使用限制</h2><ul>"
    html += "".join(f"<li>{escape(w)}</li>" for w in audit["warnings"])
    html += "</ul></section><section><h2>明细与复现</h2><p>"
    for name, label in (
        ("comparison.csv", "全部分段与逐年对照"),
        ("event_chain.csv", "冻结支点与事件链"),
        ("fixed_horizon_events.csv", "期限事件研究"),
        ("benchmarks.csv", "描述性基准"),
        ("audit.json", "参数及来源审计"),
        ("validation.json", "独立账本核对"),
        ("report.html", "原始完整结果页"),
    ):
        html += f"<a href='{name}'>{label}</a>　"
    html += (
        "</p><p>完整模型、外部来源、运行命令与需要决策的事项见工程 "
        "DAILY_RESEARCH_FRAMEWORK.md。逐年折使用新本金、不承接前一年持仓；"
        "不能直接把各折收益拼成连续净值。</p></section></main></html>"
    )
    (args.output / "summary.html").write_text(html, encoding="utf-8")
    print(args.output / "summary.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
