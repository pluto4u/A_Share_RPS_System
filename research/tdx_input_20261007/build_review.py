"""Build and execute the bounded feasibility receipt, without rerunning the audit."""
import json
from pathlib import Path
import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT/'output/tdx_input_20261007/v1'
summary = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
nb = nbformat.v4.new_notebook()
nb.metadata.kernelspec = {'display_name':'Python 3','language':'python','name':'python3'}
nb.cells = [
    nbformat.v4.new_markdown_cell('# 通达信备用输入可行性核验\n\n## tl;dr\n\n'
        f"当前登记存续池{summary['expected_codes']:,}股，原始文件存在{summary['raw_files_present']:,}份；"
        f"原输入记录缺失{summary['missing_original_keys']:,}条，复权构建通过{summary['adjustment_valid_codes']:,}股。"
        f"整池门槛{'通过' if summary['gate_passed'] else '未通过'}；未覆盖生产数据，也未重算收益。"),
    nbformat.v4.new_markdown_cell('## Context & Methods\n\n只研究原输入的5,224只存续代码；日期2016-08-22至2026-09-30。'
        '逐文件验证32字节记录、日期主键、量为股/额为人民币元、正OHLC和非负有限量额。'
        '沿用登记的比例前复权纯函数，特殊类别拒绝而不猜补。\n\n### Key Assumptions\n\n'
        '当前除权资料不能证明历史公告可得性；跨源差异不自动证明哪一方正确。这里只判断是否具备建立同池备用输入的基础。'),
    nbformat.v4.new_markdown_cell('## Data\n\n原面板与TDX .day/gbbq路径、SHA256、协议和逐股结果见registration.json、protocol.json、inventory.csv。'
        '以下从冻结CSV独立重算汇总并校验来源哈希。'),
    nbformat.v4.new_code_cell("from pathlib import Path\nimport json, hashlib\nimport pandas as pd\n"
        f"root = Path({str(ROOT)!r})\nout = root / 'output/tdx_input_20261007/v1'\n"
        "summary = json.loads((out/'summary.json').read_text(encoding='utf-8'))\n"
        "inventory = pd.read_csv(out/'inventory.csv')\n"
        "receipt = json.loads((out/'final_manifest.json').read_text(encoding='utf-8'))\n"
        "for relative, expected in receipt['files'].items():\n"
        "    assert hashlib.sha256((root/relative).read_bytes()).hexdigest() == expected\n"
        "assert len(inventory) == 5224 and inventory.ts_code.is_unique\n"
        "assert inventory.missing_original_keys.sum() == summary['missing_original_keys']\n"
        "assert inventory.adjustment_status.eq('valid').sum() == summary['adjustment_valid_codes']\n"
        "eligible = inventory.raw_status.eq('valid') & inventory.adjustment_status.eq('valid') & inventory.missing_original_keys.eq(0)\n"
        "assert bool(eligible.all()) == summary['gate_passed']\nprint('冻结文件与整池门槛重算一致')"),
    nbformat.v4.new_markdown_cell('## Results\n\n门槛逐项列出，避免把未验收代码删除后称作整池通过。'),
    nbformat.v4.new_code_cell("pd.DataFrame([\n"
        "    ['登记代码数', len(inventory)], ['原始文件存在', inventory.file_exists.sum()],\n"
        "    ['原始验证通过', inventory.raw_status.eq('valid').sum()],\n"
        "    ['缺失原股票日期', inventory.missing_original_keys.sum()],\n"
        "    ['复权构建通过', inventory.adjustment_status.eq('valid').sum()],\n"
        "    ['同池覆盖与复权均通过', eligible.sum()]\n"
        "], columns=['检查项','数量'])"),
    nbformat.v4.new_code_cell("inventory.loc[~eligible, ['ts_code','raw_status','missing_original_keys','adjustment_status','reason']].head(8)"),
    nbformat.v4.new_markdown_cell('## Takeaways\n\n原输入与本地来源哈希前后不变。若整池门槛未通过，须先处理资料/复权缺口，'
        '不能偷偷缩小股票池、拿原供应商价格混合TDX量额后称为纯TDX，或更改策略规则救收益。'
        '若通过，也须建立单独日期版本、重算RPS并复核调整价，再按原协议复核收益；不等于实盘资格。'),
]
nbformat.validate(nb)
NotebookClient(nb,timeout=120,kernel_name='python3').execute(cwd=str(ROOT))
nbformat.write(nb,OUT/'review.executed.ipynb')
nbformat.write(nb,HERE/'review.ipynb')
print(json.dumps({'executed':True,'cells':len(nb.cells),'path':str(OUT/'review.executed.ipynb')},ensure_ascii=True))
