"""Offline companion verification and bounded chart/source preparation."""
import json
from pathlib import Path
import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_input_impact_20261007/v1"
summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
nb = nbformat.v4.new_notebook()
nb.cells = [
    nbformat.v4.new_markdown_cell("# 已确认量额异常的局部信号影响\n\n## tl;dr\n\n替换33个有双源数值支持的量额字段，仅量价改变2个股票日期，超跌确认改变1个，其他六组直接入选信号未变。只是局部敏感性，非全库影响或收益复核。"),
    nbformat.v4.new_markdown_cell("## Context & Methods\n\n冻结原参数与5224代码全覆盖排名，只替换20个存续对照中的33字段。原库与报告不修改。\n\n### Key Assumptions\n\n保留float32原实验计算口径；第三源不承诺独立上游。没有重算匹配分组、收益、容量或显著性，不能用此局部结果批准实盘。"),
    nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport numpy as np\nimport pandas as pd\nimport matplotlib.pyplot as plt\nroot = Path.cwd().parents[1]\nout = root / 'output/rps_input_impact_20261007/v1'\nsummary = json.loads((out/'summary.json').read_text(encoding='utf-8'))\npatch = pd.read_csv(out/'patch_receipt.csv')\nassert len(patch) == 33\nassert patch.field.value_counts().to_dict() == {'amount':29, 'vol':4}\nassert (patch.previous > 0).all() and (patch.replacement > 0).all()\nprint('Registered cells',len(patch))"),
    nbformat.v4.new_markdown_cell("## Data\n\nchanged_signals.csv保留所有翻转，model_sensitivity.csv保存八组汇总；不把同一股票日期重复计为独立样本。"),
    nbformat.v4.new_code_cell("events = pd.read_csv(out/'changed_signals.csv')\ntotals = pd.read_csv(out/'model_sensitivity.csv')\nfor row in totals.itertuples():\n    assert row.changed == len(events.loc[events.model.eq(row.model)])\n    assert row.added == len(events.loc[events.model.eq(row.model) & events.change.eq('added')])\n    assert row.removed == len(events.loc[events.model.eq(row.model) & events.change.eq('removed')])\nassert len(events[['signal_date','ts_code']].drop_duplicates()) == summary['changed_stock_dates'] == 3\ndisplay(events)"),
    nbformat.v4.new_markdown_cell("## Results\n\n数字是股票×信号日入选变化，不是交易笔数或收益变化。价格和RPS描述符固定；成交量正值可用性也未改变。"),
    nbformat.v4.new_code_cell("plt.rcParams.update({'font.family':'Microsoft YaHei','font.size':11})\nfig, ax = plt.subplots(figsize=(8.5,4.8))\nax.barh(totals.label, totals.changed, color='#41698c')\nax.invert_yaxis()\nax.set_xlim(0,3)\nax.set_xticks([0,1,2,3])\nax.set_xlabel('发生变化的股票×信号日数量（不是收益）')\nax.set_title('只替换已确认33字段：冻结八组规则的局部敏感性')\nfor i,value in enumerate(totals.changed):\n    ax.text(value+.04,i,str(value),va='center')\nfig.tight_layout()\nfig.savefig(out/'signal-impact.png',dpi=160)\nplt.show()"),
    nbformat.v4.new_markdown_cell("## Takeaways\n\n目前已确认量额异常不足以解释RPS＋量价全部历史失败，不能承诺修复就盈利。全库质量、匹配流动性分组和收益影响仍未测。直接信号只翻转3个股票日期，不能外推为整个供应商影响很小。")
]
nb.metadata.kernelspec = dict(name="python3",display_name="Python 3",language="python")
nbformat.validate(nb)
NotebookClient(nb, timeout=90, kernel_name="python3", resources={"metadata":{"path":str(HERE)}}).execute()
nbformat.validate(nb)
nbformat.write(nb, HERE / "review.ipynb")

source = dict(label="已确认33字段的冻结规则局部敏感性",
    files=[{"label":"rps_input_impact_20261007/protocol.json"}, {"label":"rps_input_impact_20261007/model_sensitivity.csv"},
           {"label":"rps_input_impact_20261007/changed_signals.csv"}, {"label":"rps_input_audit_20261007/prior_large_cells_recheck.csv"}],
    filters=["只替换此前登记并获TDX/BaoStock数值支持的33个字段", "参数、价格及全覆盖排名人口保持不变"],
    caveats=["本次只替换29成交额和4成交量字段，不是全库修复或随机样本误差率。",
             "未重算流动性匹配分组、收益、交易容量或显著性；信号翻转不等于账户损益变化。"],
    metricDefinitions=[{"label":"直接入选信号翻转", "definition":"原冻结规则与局部量额替换后规则的入选布尔值不同的股票日期数；六个零值是实际比较结果，不是缺失填零。", "componentIds":["confirmed-cells-signal-impact"]}],
    evidenceFlow=[{"kind":"source","title":"已登记来源","detail":"沿用已核对的20存续控制代码，33大幅差异全部存在BaoStock有效日线并在rtol1e-6/atol1下获TDX支持，使用其精确值，不猜乘倍率。"},
                  {"kind":"calculation","title":"只改量额","detail":"原study.load_inputs和features构造5224股票全日期基准；局部适配器先严格复现原八组掩码，再仅替换33字段。按股票日期统计新增/取消。"},
                  {"kind":"validation","title":"边界验证","detail":"原输入和注册文件前后SHA256一致；所有替换保持正量额，价格、排名、价格标签值及正成交量可用性不变。离线笔记对明细/汇总再独立核对。"}])
rows = [dict(model=row["label"], changes=row["changed"]) for row in summary["model_changes"]]
chart = dict(schemaVersion=1,id="confirmed-cells-signal-impact",title="33个已确认字段替换后的选股信号变化",
    description="局部敏感性；不是完整修复或收益变化。参数和价格不变。",height=360,
    chart=dict(type="horizontalBar",x="model",y="changes",startAtZero=True,yLabel="变化的股票×信号日数量"),
    rows=rows,source=source)
(OUT / "reviewed-chart.json").write_text(json.dumps(chart,ensure_ascii=False,indent=2),encoding="utf-8")
receipt = dict(schemaVersion=1,items=[dict(id="confirmed-cells-signal-impact",title="33字段局部敏感性，不是收益变化",
    queries=[dict(id="local-signal-impact", rows=rows,columns=[{"field":"model","label":"冻结模型"},{"field":"changes","label":"变化的股票×信号日"}],
                  reportingPeriod="冻结价格样本截至2026-09-30；本次信号翻转日期2025-11-28至2025-12-15",source=source)])])
(OUT / "reviewed-sources.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
print("Offline review executed; bounded chart/source JSON created")
