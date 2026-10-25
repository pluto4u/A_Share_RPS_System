"""Executed companion and bounded reviewed chart/source evidence for this phase."""
import json
import sys

import nbformat
import pandas as pd
from jupyter_client import KernelManager
from nbclient import NotebookClient

from common import HERE, OUT, guard, now, save


def main():
    guard()
    if (HERE / "review.ipynb").exists():
        raise FileExistsError("Companion already exists")
    recon = json.loads((OUT / "reconciliation.json").read_text(encoding="utf-8"))
    quality = json.loads((OUT / "source_quality.json").read_text(encoding="utf-8"))
    controls = json.loads((OUT / "control_vendor_comparison.json").read_text(encoding="utf-8"))
    rows = [dict(status="本地日期及数值一致", codes=52), dict(status="旧缓存缺日线", codes=49),
            dict(status="本机原始价缺日线", codes=5), dict(status="只有调整价缓存", codes=22),
            dict(status="原始价与缓存都缺", codes=111)]
    counts = pd.read_csv(OUT / "reconciliation_by_code.csv")
    term = counts[counts.group == "terminated"]
    assert len(term) == 106 and term.numeric_overlap_agrees.sum() == 106
    assert term.local_reconciliation_agrees.sum() == 52
    assert (term.raw_only_dates > 0).sum() == 49 and (term.cache_only_dates > 0).sum() == 5
    assert not ((term.raw_only_dates > 0) & (term.cache_only_dates > 0)).any()
    assert sum(r["codes"] for r in rows) == 239
    captured = now()
    caveats = ["239只是研究期终止上市代码快照，不是完整历史有效证券主表。",
        "本地一致仅指2018至2026-09-30的原始价/缓存日期及重叠数值对账，未证明历史证券状态或真实成交。",
        "复权重建复用旧算法，不是独立供应商或公告可得时间验证；接入策略的代码数仍为0。"]
    source = dict(label="历史股票池补数核验", files=[{"label":"官方终止上市名单及补数清单"},
        {"label":"reconciliation_by_code.csv"}, {"label":"reconciliation.json"}],
        caveats=caveats, filters=["市场：沪深A股", "对象：研究期终止上市的239只代码"],
        evidenceFlow=[dict(kind="validation", title="对账结果", detail="106只重叠数值一致；52只日期集合一致、49只缓存缺1283个日期、5只原始价缺61个日期。")])
    save("inline_chart.json", dict(schemaVersion=1, id="rps-history-repair", queryId="rps_history_repair",
        title="239只历史终止上市股票的数据状态", generatedAt=captured, height=360,
        chart=dict(type="bar", x="status", y="codes", horizontal=True, yLabel="股票代码数", showValueLabels=True),
        rows=rows, source=source, theme="codex-classic"))
    save("inline_chart_sources.json", dict(schemaVersion=1, items=[dict(id="rps-history-repair", title="历史股票池的数据状态",
        queries=[dict(id="rps-history-repair-counts", source=source, rows=rows,
            columns=[{"field":"status", "label":"数据状态"},{"field":"codes", "label":"股票代码数"}],
            reportingPeriod="2018-01-01至2026-09-30", capturedAt=captured)])]))
    extra_source = dict(label="量价输入与历史接口核验", files=[{"label":"source_quality.json"},
        {"label":"control_vendor_comparison.json"},{"label":"frozen_input_liquidity_disagreements.csv"},
        {"label":"BaoStock两只股票的原响应"}],
        caveats=["20只对照不是全市场随机样本，不能推广供应商一致或错误范围。",
            "成交量/成交额差异尚未定位到供应商还是入库环节，也未量化对策略表现的影响。",
            "两只股票2023年6月的40条返回记录都标为停牌，量额全部为空，没有可验证可成交日线。"],
        evidenceFlow=[dict(kind="result", title="输入差异", detail=f"{quality['large_liquidity_disagreement_cells']}个大幅量额差异单元格，涉及{quality['large_disagreement_code_dates']}个股票日期，均在冻结面板中复现；集中于2025-11-27和2025-12-01。"),
            dict(kind="validation", title="只读与原实验保留", detail="同花顺原库前后SHA一致；此前RPS冻结实验和诊断文件另做哈希验证。")])
    save("inline_additional_sources.json", dict(schemaVersion=1, items=[dict(id="rps-input-quality", title="量价输入仍有未解决差异",
        queries=[dict(id="rps-liquidity-check", source=extra_source, capturedAt=captured)])]))
    finding = f"""# 历史股票池修复：本轮核验与研究退出条件

本轮没有发现或证明赚钱方案。它确认了两类妨碍下结论的数据问题，现有策略参数、原库及冻结结果均未改写。

## 本轮结果

- 239只研究期终止上市代码中：52只本地日期与数值一致，49只旧缓存缺1283条交易日记录，5只本机原始价缺61条；22只只有调整价缓存，111只二者都缺。
- 106只退市股171728条重叠日期上，复权价格、成交量、成交额均在事先登记容差内一致。复权复用原算法，并不是独立公告/公司行动或历史证券状态验证。全部仍隔离，不并入策略。
- 固定20只存续对照股41495条日期：原始OHLC及调整价一致；成交量337个、成交额396个单元格超登记容差。大幅差异{quality['large_liquidity_disagreement_cells']}个单元格，涉及{quality['large_disagreement_code_dates']}个股票日期、{quality['large_disagreement_codes']}只代码，集中2025-11-27、2025-12-01。这些差异在冻结输入中复现。不能据此指定哪家供应商错误，更不能猜乘10/100修复。
- BaoStock公共SDK匿名查询两只曾被现有接口拒绝的退市股成功，但2023年6月返回40条记录：40条都标为停牌、量额全空，可验证成交0条。股票基本信息退市日期与交易所快照一致。接口能响应，不等于可成交历史恢复。
- 没有批量拉取111只、没有追加阈值、没有重跑收益，更没有下单。最新用户担忧当前方向，因此先收尾这一有界核验，不把补数无限展开。

## 已知限制

名单是代码级快照，不是完整时点证券主表。历史ST/行业/股本及公告可得时间仍不完整；没有退市结算、涨跌停/T+1和真实现金账户的完整执行验证。现有诊断不能成为强意义的策略证伪，也不能成为盈利证据。补完数据的结果仍可能更差，不能承诺转正。

## 建议的研究退出条件（待下一阶段登记）

1. 先定位量额差异的来源，并取得真实成交历史与历史状态；数据验收未过，不继续盈利检验。
2. 给已有RPS＋量价/低位/超跌冻结规则最后一次同口径复核；不要借补数改阈值或选择有利年份。
3. 数据验收后仍不能在跨段、合理成本与可执行约束下体现稳定增量，就归档这一版本；不要循环优化到它看起来盈利。
4. 即便历史通过，也只进入事先冻结的前瞻模拟阶段；已见历史重跑不是独立样本外。

这是一份继续/停止研究的判断框架，不是投资建议或收益承诺。

## 复现与技术修复

新版本在research/rps_repair_20261006，证据在output/rps_repair_20261006/v1。输入哈希、事先容差及固定20只对照保存在protocol.json。只读导出SQL及原CLI回执保留；原库前后哈希一致。

首次对账因pandas日期精度(s/ns)差异而拒绝，尚未产出差异结果；修复仅统一时间戳单位，不移动/重排日期、不改数据或容差。回归测试覆盖该问题。官方菜单default.md入口返回了行情说明而非访问规则，保留原响应，不冒称访问规则已核实。只做一轮低请求数匿名探测，未绕过服务权限、配额或付费节点。

可在新输出版本中复跑；当前默认拒绝覆盖，冻结后拒绝刷新。review.ipynb是已执行的数据检查伴随文件。
"""
    (HERE / "FINDINGS.md").write_text(finding, encoding="utf-8")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_markdown_cell("# 历史股票池修复\n\n## tl;dr\n本轮只验证数据，不改策略。106只退市股重叠数值一致、52只日期完整一致；量价输入有跨源差异，历史接口返回停牌记录不能当成可成交报价。"),
        nbformat.v4.new_markdown_cell("## Context & Methods\n\n### Key Assumptions\n2018—2026.09；239只交易所代码级快照不是完整历史证券ID主表。原实验哈希不变。价格容差rtol/atol均1e-5；量额rtol1e-6、atol1；固定20只代码排序的交集对照，不是随机全市场。复权重用原算法，非独立公告时点验证。缺失值不补零，API返回成功与可成交资料分开。"),
        nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport pandas as pd\nimport matplotlib.pyplot as plt\nfrom IPython.display import display\nout = Path.cwd()\nrecon = json.loads((out/'reconciliation.json').read_text(encoding='utf-8'))\nquality = json.loads((out/'source_quality.json').read_text(encoding='utf-8'))\nrows = json.loads((out/'inline_chart.json').read_text(encoding='utf-8'))['rows']\nplt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':11})"),
        nbformat.v4.new_markdown_cell("## Data & Results\n\n数据出处：交易所冻结名单；旧TDX缓存、C:/new_tdx原始价/公司行动文件；同花顺只读原始库及冻结面板；BaoStock两个代码原返回。文件哈希和完整SQL单独保留。"),
        nbformat.v4.new_code_cell("frame = pd.DataFrame(rows)\nassert frame.codes.sum() == 239\nfig, ax = plt.subplots(figsize=(9,4.5))\nax.barh(frame.status, frame.codes, color='#3565a0')\nax.invert_yaxis()\nax.set_xlabel('股票代码数')\nax.set_title('239只研究期终止上市代码：本地数据状态')\nfor i, value in enumerate(frame.codes): ax.text(value+1, i, str(value), va='center')\nax.set_xlim(0,frame.codes.max()*1.15)\nfig.tight_layout()\nfig.savefig(out/'history-coverage.png',dpi=130)\nplt.show()\ndisplay(frame)"),
        nbformat.v4.new_code_cell("display(pd.DataFrame(quality['baostock_probes'])[['ts_code','returned_rows','tradestatus_0_rows','unknown_trade_status_rows','missing_volume_rows','usable_traded_rows']])\nlarge = pd.read_csv(out/'large_liquidity_disagreements.csv')\nassert len(large) == quality['large_liquidity_disagreement_cells']\ndisplay(large.head(12))\ndisplay(pd.DataFrame(recon['groups']).T[['codes','common_rows','numeric_overlap_agrees','date_sets_agree','accepted_for_research']])"),
        nbformat.v4.new_markdown_cell("## Takeaways\n已有证据还不支持赚钱方案。修复数据不是盈利承诺；交叉源量额异常的来源和策略影响未定，停牌占位行不等于成交记录。先关闭数据验收缺口，再对冻结规则做一次有退出条件的复核；若未通过，归档版本，不无限调参。历史通过也只可进入冻结的前瞻模拟，不是直接实盘。")]
    nb.metadata["kernelspec"] = dict(display_name="Python research", language="python", name="python3")
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    NotebookClient(nb, timeout=60, km=manager, resources={"metadata": {"path": str(OUT)}}).execute()
    nbformat.write(nb, HERE / "review.ipynb")
    save("companion_receipt.json", dict(completed_at_utc=now(), cells=len(nb.cells),
        code_cells=sum(c.cell_type == "code" for c in nb.cells), execution_errors=0))
    print("Executed companion and prepared bounded source-backed chart evidence", flush=True)


if __name__ == "__main__":
    main()
