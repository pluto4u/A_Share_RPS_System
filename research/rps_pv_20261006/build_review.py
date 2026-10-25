"""Build/execute a companion notebook and reviewed inline delivery payloads."""
import json
import sys
from pathlib import Path

import nbformat
import pandas as pd
from nbclient import NotebookClient
from jupyter_client import KernelManager

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_pv_20261006/v2"
NAMES = {"universe":"同日研究集合", "rps_only":"仅RPS", "pv_only":"仅量价",
         "rps_pv":"RPS＋量价", "low_only":"仅低位", "low_turn":"低位转强",
         "oversold_only":"仅超跌", "oversold_confirm":"超跌＋确认"}


def main():
    summary = pd.read_csv(OUT / "summary.csv")
    subset = summary[(summary.horizon==20)&(summary.friction_pct==1)].copy()
    done = json.loads((OUT / "completed.json").read_text(encoding="utf-8"))
    quality = json.loads((OUT / "quality.json").read_text(encoding="utf-8"))
    verification = json.loads((OUT / "verification.json").read_text(encoding="utf-8"))
    rows = [dict(model=NAMES[r.model], period="2018—2021" if r.period=="early" else "2022—2026.09",
                 value=round(r.gross_daily_mean_pct, 4)) for r in subset.itertuples()]
    source = dict(label="RPS量价固定规则历史探索", files=[dict(label="summary.csv"), dict(label="protocol.json")],
        caveats=["仅限当前本地覆盖股票，完整退市历史尚未核实。", "20日调整价开盘到开盘表现代理不是年化、组合或实盘收益。",
                 "各信号日先等权平均股票，再平均日期；事件可能重叠。", "图中为扣费前代理，尚未模拟真实限价、资金、整手与现金分红。",
                 "历史已经被使用，不是独立样本外验证。"],
        filters=["历史段：2018—2021、2022—2026-09-30", "固定8组，不搜索新参数"],
        metricDefinitions=[dict(name="20日价格表现代理", definition="收盘信号后下一市场日开盘到其后第20市场日开盘的调整价简单收益，先按信号日等权平均股票，再平均日期。")])
    payload = dict(schemaVersion=1, id="rps-pv-20d-proxy", title="20日价格表现代理：两段历史比较",
                   description="扣费前代理，不是年化或实盘收益；只反映本地覆盖股票。",
                   chart=dict(type="horizontalBar", x="model", y="value", series="period",
                              yLabel="20日价格表现代理（%）", startAtZero=True),
                   rows=rows, source=source, height=520, generatedAt=done["completed_at_utc"], theme="codex-classic")
    (OUT / "reviewed-chart.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = dict(schemaVersion=1, items=[dict(id="rps-pv-history", title="固定8组20日价格表现代理", queries=[dict(
        id="proxy-summary", source=source, rows=rows, columns=["model", "period", "value"],
        reportingPeriod="2018-01-01—2021-12-31；2022-01-01—2026-09-30",
        preview=dict(kind="aggregate", note="各段各组汇总，不是逐笔成交数据。"))])])
    (OUT / "reviewed-sources.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    notebook = nbformat.v4.new_notebook()
    notebook.cells = [
        nbformat.v4.new_markdown_cell("# RPS、量价、低位与超跌：固定规则历史拆解\n\n## tl;dr\n没有一组通过本轮登记的跨两段历史与费用压力门槛。RPS＋量价早期较好、后期较弱；低位转强确认弱于仅低位；超跌确认有弱线索但后期不足。不能据此断言整个概念无效，也不能宣称找到盈利策略。"),
        nbformat.v4.new_markdown_cell("## Context & Methods\n\n### Key Assumptions\n前复权价格只作表现代理；历史已被使用。统一下一日开盘进入、其后第20市场日开盘观察；5日是敏感性。固定8组，无调参。各段未来标签在段末截断，信号与未来标签分开保存。区间以月均同日差做3月连续块重抽样，各段6对比使用99.1667%区间。协议、登记代码与数据指纹可检查。\n\n股票目录可能存在幸存者偏差；未核实原始价限价、整手、现金分红或组合资金；不得把图表当净值曲线。"),
        nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport pandas as pd\nimport matplotlib.pyplot as plt\nfrom IPython.display import display\nout = Path.cwd()\nsummary = pd.read_csv(out / 'summary.csv')\ncontrasts = pd.read_csv(out / 'contrasts.csv')\nquality = json.loads((out / 'quality.json').read_text(encoding='utf-8'))\nverification = json.loads((out / 'verification.json').read_text(encoding='utf-8'))\nplt.rcParams.update({'font.family':'Microsoft YaHei', 'axes.unicode_minus':False, 'figure.dpi':120})\nnames = "+repr(NAMES)),
        nbformat.v4.new_markdown_cell("## Data\n控制源：同花顺本地前复权表，兼容文件名tdx_stock_daily_with_rps.parquet。5224只股票均在最近250个市场日有记录，因此不能证明完整历史退市覆盖。质量指标和独立抽样核对如下；抽样不是全量成交审计。"),
        nbformat.v4.new_code_cell("display(pd.DataFrame([quality])[['rows','securities','first','last','duplicate_keys','invalid_rows','impossible_ohlc','early_ending_before_last_250_sessions']])\ndisplay(pd.DataFrame([verification]))"),
        nbformat.v4.new_markdown_cell("## Results\n### 1. 两段历史的20日价格表现代理\n扣费前、非年化、非组合收益。完整8组均展示，不只公布赢家。"),
        nbformat.v4.new_code_cell("selected = summary[(summary.horizon==20)&(summary.friction_pct==1)].copy()\norder = list(names)\npivot = selected.pivot(index='model',columns='period',values='gross_daily_mean_pct').reindex(order)\nfig, ax = plt.subplots(figsize=(10,6))\ny = list(range(len(order)))\nax.barh([v-.18 for v in y], pivot.early, height=.34, label='2018—2021', color='#3565a0')\nax.barh([v+.18 for v in y], pivot.late, height=.34, label='2022—2026.09', color='#bb8537')\nax.set_yticks(y,[names[m] for m in order]); ax.invert_yaxis(); ax.axvline(0,color='#444444',linewidth=.8)\nax.set_xlabel('20日价格表现代理（%），扣费前'); ax.set_title('固定8组：历史信号表现，不是资金收益')\nax.legend(); fig.tight_layout(); fig.savefig(out/'proxy-comparison.png'); plt.show()\ndisplay(selected[['model','period','signals','labels','unavailable','gross_daily_mean_pct','cost_proxy_daily_mean_pct']].round(3))"),
        nbformat.v4.new_markdown_cell("### 2. 主20日同日增量差与统计区间\n点估计及区间都基于月均差。不是因果实验，不消除历史选择偏差。"),
        nbformat.v4.new_code_cell("pairs = list(dict.fromkeys(zip(contrasts.candidate,contrasts.baseline)))\nfig, axes = plt.subplots(1,2,figsize=(13,5),sharex=True,sharey=True)\nfor ax, period, label in zip(axes,['early','late'],['2018—2021','2022—2026.09']):\n    c = contrasts[(contrasts.horizon==20)&(contrasts.period==period)].set_index(['candidate','baseline']).reindex(pairs)\n    ax.errorbar(c.mean_pp,range(len(c)),xerr=[c.mean_pp-c.lo_pp,c.hi_pp-c.mean_pp],fmt='o',capsize=3,color='#3565a0')\n    ax.set_title(label); ax.axvline(0,color='#444444',linewidth=.8); ax.set_xlabel('增量差（百分点），99.1667%区间')\naxes[0].set_yticks(range(len(pairs)),[names[a]+' vs '+names[b] for a,b in pairs]); axes[0].invert_yaxis()\nfig.tight_layout(); fig.savefig(out/'paired-intervals.png'); plt.show()\ndisplay(contrasts[contrasts.horizon==20].round(3))"),
        nbformat.v4.new_markdown_cell("## Takeaways\n当前规则没有证明稳定可兑现优势。超跌确认后期相对仅超跌月均差约+0.031个百分点，区间约[-0.435,+0.508]，不能宣称有效。低位转强两段均弱于仅低位，不能因名字听起来合理就保留确认。\n\n下一步先补历史退市/证券状态、原始价交易限制，并诊断规模与波动暴露；不修改本次阈值、删除失败年份或追加网格。若另提规则须新编号。研究源代码、标签、统计和160个抽样核对均可复查。")]
    notebook.metadata["kernelspec"] = dict(display_name="Python research", language="python", name="python3")
    nbformat.validate(notebook)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(notebook, km=manager, timeout=120, resources={"metadata":{"path":str(OUT)}})
    client.execute()
    nbformat.validate(notebook)
    nbformat.write(notebook, HERE / "review.ipynb")
    print(json.dumps(dict(notebook=str(HERE / "review.ipynb"), executed=True, cells=len(notebook.cells)), ensure_ascii=False))


if __name__ == "__main__":
    main()
