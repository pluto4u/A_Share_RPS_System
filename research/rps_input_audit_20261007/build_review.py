"""Generate and execute an offline review notebook from preserved observations."""
from pathlib import Path
import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_input_audit_20261007/v1"
nb = nbformat.v4.new_notebook()
nb.cells = [
    nbformat.v4.new_markdown_cell("# 存续股量额第三源核对\n\n## tl;dr\n\n33个原大幅差异均获BaoStock对TDX的数值支持；同花顺缓存与库一致。不是全市场验收，不改原数据，不重算策略。"),
    nbformat.v4.new_markdown_cell("## Context & Methods\n\n固定原20只存续对照，2025-11-26至12-02；股票×日期×字段。仅重算登记容差内的数值对齐。\n\n### Key Assumptions\n\n第三供应商未证明独立上游；当前缓存与当前安装代码不能证明历史载荷/版本。后续收益没有参与检验。"),
    nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport numpy as np\nimport pandas as pd\nroot = Path.cwd().parents[1]\nout = root / 'output/rps_input_audit_20261007/v1'\nregistration = json.loads((out/'registration.json').read_text(encoding='utf-8'))\np = registration['protocol']\nprint(p['window'], len(p['codes']), p['liquidity_tolerance'])"),
    nbformat.v4.new_markdown_cell("## Data\n\n保存的原始BaoStock响应与请求参数在baostock目录；同花顺只读旧导出、当前下载缓存、TDX未复权窗口均保留CSV。复跑此笔记不联网、不修改数据库。"),
    nbformat.v4.new_code_cell("keys = ['ts_code','trade_date']\ntables = {name: pd.read_csv(out/f'{name}_window.csv', dtype={'trade_date':str}).set_index(keys) for name in ['hithink','tdx','dump','baostock']}\nfor name, frame in tables.items():\n    assert frame.index.is_unique\n    assert len(frame) == 100\n    assert set(frame.index.get_level_values('ts_code')) == set(p['codes'])\nprint({name: len(frame) for name, frame in tables.items()})"),
    nbformat.v4.new_markdown_cell("## Results\n\n下面从四份窗口表直接重算，不使用预先生成的布尔标记。容差用于数值一致性，不能作为选股阈值。"),
    nbformat.v4.new_code_cell("focus = pd.read_csv(out/'prior_large_cells_recheck.csv', dtype={'trade_date':str})\nassert len(focus) == 33\ncounts = dict(tdx_bao=0, hithink_bao=0, hithink_dump=0)\nfor row in focus.itertuples():\n    key = (row.ts_code, row.trade_date)\n    values = {name: frame.loc[key, row.field] for name, frame in tables.items()}\n    assert all(pd.notna(v) for v in values.values())\n    for label, left, right in [('tdx_bao','tdx','baostock'), ('hithink_bao','hithink','baostock'), ('hithink_dump','hithink','dump')]:\n        counts[label] += int(np.isclose(values[left], values[right], **p['liquidity_tolerance']))\nassert counts == {'tdx_bao':33,'hithink_bao':0,'hithink_dump':33}\nprint(counts)\ndisplay(focus.loc[focus.ts_code.eq('000001.SZ'), ['ts_code','trade_date','field','hithink','tdx','dump','baostock']])"),
    nbformat.v4.new_markdown_cell("## Takeaways\n\n数据关尚未关闭：下一步需要有来源凭证的隔离修正及信号影响核查。不能猜乘倍率，也不能把修正后的历史叫独立样本外。这里只做一个小范围精确对账，表格比趋势图更适合核对字段。")
]
nb.metadata.kernelspec = dict(name="python3", display_name="Python 3", language="python")
nbformat.validate(nb)
NotebookClient(nb, timeout=90, kernel_name="python3", resources={"metadata":{"path":str(HERE)}}).execute()
nbformat.validate(nb)
dest = HERE / "review.ipynb"
if dest.exists():
    raise FileExistsError(dest)
nbformat.write(nb, dest)
print(f"Executed offline notebook: {dest}")
