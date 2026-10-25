"""Executed research companion, concise findings and shared Sources payload."""
import json
import sys
from pathlib import Path

import nbformat
import pandas as pd
from jupyter_client import KernelManager
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_diag_20261006/v1"
EVIDENCE = OUT.parent / "exchange_evidence"
NAMES = {"rps_pv":"RPS＋量价", "rps_only":"仅RPS", "low_turn":"低位转强", "low_only":"仅低位",
         "oversold_confirm":"超跌＋确认", "oversold_only":"仅超跌"}
ORDER = ["rps_pv", "low_turn", "oversold_confirm"]


def main():
    if (OUT / "final_manifest.json").exists():
        raise RuntimeError("Frozen research cannot be refreshed; make a new version")
    if (HERE / "review.ipynb").exists() and "--refresh" not in sys.argv:
        raise FileExistsError("Research companion already exists")
    contrasts = pd.read_csv(OUT / "reviewed_contrasts.csv")
    primary = contrasts[(contrasts.horizon==20)&(contrasts.comparison=="matched_difference")].copy()
    coverage = pd.read_csv(OUT / "matching_coverage.csv")
    coverage = coverage[coverage.horizon==20]
    main_result = primary.merge(coverage,on=["candidate","baseline","period","horizon"],validate="one_to_one")
    verification = json.loads((OUT / "verification.json").read_text(encoding="utf-8"))
    terminal = json.loads((EVIDENCE / "coverage.json").read_text(encoding="utf-8"))
    contract = json.loads((HERE / "data_contract.json").read_text(encoding="utf-8"))
    confirmation = pd.read_csv(OUT / "confirmation_summary.csv")
    probes = json.loads((OUT.parent / "history_probe/summary.json").read_text(encoding="utf-8"))
    lines = ["# RPS冻结信号：机制诊断与补数清单", "", "## 结论", "",
        "当前没有稳定、可兑现的盈利证据；不能把全部RPS、低位或超跌概念一概判死。应先修历史股票池，再评估有限、预登记的研究假设。",
        "这轮沿用已冻结信号，不改阈值、不重选赢家；属于事后历史诊断，不是未见样本外。",
        "", "## 1. 数据缺口已经实证核实", "",
        f"交易所当前终止上市名单中，{terminal['window'][0]}至{terminal['window'][1]}的{terminal['window_terminal_codes']}只沪深A股，在冻结面板及同花顺本地原始表均无行情记录（沪99、深140）。名单是代码级快照，不能替代完整证券历史主表，也不意味着239只都会满足策略条件。偏差方向和大小尚未量化。",
        "原始库5224只沪深股全部在最近250个市场日仍有行情；库中没有历史ST、行业、市值/流通股本或上市有效期。不能用今天的分类倒填历史。",
        "对照保留的TDX缓存，128只有研究期内198696条调整价行情；C:/new_tdx配置路径下106只有原始日线，22只只有缓存，111只两者均无；5只原始末日早于缓存。原始OHLC基础检查未见异常，不等于完整复权、时点证券状态或真实执行验证。旧D:/tdx路径不可用；并非本机完全没有TDX原始数据。",
        "这些补数线索没有并入本轮回测。repair_backlog.csv逐代码列下一步；data_contract.json列接入与验收闸门。",
        "有限取数可行性探测：当前同花顺market.history入口以合法沪深代码查询601258.SH、000540.SZ的2023年6月未复权日线，都返回FUYAO_1002 / Unknown thscode；未获得行情，也未入库。仅说明这个入口拒绝这两个请求，不能推广为全部退市行情不存在。原回执在history_probe，不能用别的股票替代缺失代码。",
        "", "## 2. 主20日：过去波动率与流动性代理匹配", "",
        "同日eligible股票的前20日波动率和成交额中位数各分5组，形成25格；对照去除候选重叠，每格已知对照至少5只。成交额不是市值，未做行业/规模中性。",
        "逐日按有效候选事件权重算候选减匹配对照的调整价未来20日差，然后月均，3月循环块5000次重抽样。每段3主对比98.3333%区间；不是因果效果、年化或账户收益。",
        "", "| 信号对比 | 历史段 | 月均增量（百分点） | 98.3333%区间 | 有效标签匹配覆盖 | 有效月/日 |",
        "|---|---|---:|---|---:|---|" ]
    for r in main_result.sort_values(["candidate","period"]).itertuples():
        lines.append(f"| {NAMES[r.candidate]} vs {NAMES[r.baseline]} | {r.period} | {r.mean_pp:+.3f} | [{r.lo_pp:+.3f}, {r.hi_pp:+.3f}] | {r.valid_match_coverage_pct:.2f}% | {r.months}/{r.paired_days} |")
    lines += ["", "- 低位转强在两段仍负，且区间均低于0；该冻结确认没有增加优势，不能用更高阈值临时补救。",
        "- RPS＋量价早期点估计正、后期负，两段区间均跨0；波动/流动性匹配没有让跨段稳定优势出现，也不能由此归因为市值或行业。",
        "- 超跌确认仅约13%/38%的有效事件被匹配；早期仅37个月、148日。两段区间宽且跨0，不得把匹配子样本推成全部超跌的结论。",
        "- 配对日期/月可能不同；均值不是资金净值，事件重叠；5日只作敏感性，未增加新的主检验。",
        "- 原contrasts.csv把20日辅助对比的interval_kind也标成main，已在reviewed_contrasts.csv更正为描述性；数值、主比较和信号未改，原文件保留。annotation_notes.json记录更正与哈希。",
        "", "## 3. 确认事件的价格变化（不是收益修复）", "",
        "以下先按信号日等权平均事件，再平均日期。已确认样本从确认前一日收盘到信号后次日开盘的调整价变化："]
    for model in ORDER:
        values = confirmation[confirmation.model==model].set_index("period")
        lines.append(f"- {NAMES[model]}：早期{values.loc['early','total_to_next_open_pct']:+.3f}%，后期{values.loc['late','total_to_next_open_pct']:+.3f}%；后期开盘跳空均值{values.loc['late','next_open_gap_pct']:+.3f}%。")
    lines += ["", "收盘超过前日最高价的确认，本就选择了当天上涨的事件。该条件下价格变化不证明差表现被买价因果解释，更不能反推昨天买入：昨天尚不知道次日是否确认。缺失开盘和段末进入不能静默补零。",
        "", "## 下一阶段", "",
        "1. 对128只缓存/106只原始价做证券ID与复权对账，补111只缺失行情和5只原始末段；缺资料时只报告缺口。",
        "2. 建有效期与公告可得时间明确的证券状态、行业、股本及公司行动表；用历史有效股票池重算RPS。",
        "3. 新数据另版本重跑冻结规则，明确是同一历史复核；真实执行与未来前瞻观察必须另设闸门。不盲拼复权、不删亏损年份、不把已见历史说成独立验证。",
        "", "## 复现与验证", "",
        f"独立抽样风险代理/匹配控制均值{verification['passed']}/{verification['sampled_matched_events']}项通过；选择仍复用冻结代码，非独立全量信号实现或成交审计。原始组点估计最大浮点差{verification['maximum_original_point_difference_pp']:.9g}个百分点（float32/64累加差），信号计数完全一致。",
        "运行：python research/rps_diag_20261006/diagnostic.py --output output/rps_diag_20261006/<新目录>；默认拒绝覆盖。",
        "review.ipynb有已执行表格和可编辑绘图代码。此前RPS v2和ETF实验保持不变。",
        "", "官方原始名单及请求时间、原响应、SQL、哈希保存在exchange_evidence与local_evidence。",
        "- 沪市：https://www.sse.com.cn/assortment/stock/list/delisting/",
        "- 深市：https://www.szse.cn/market/stock/suspend/index.html"]
    (HERE / "FINDINGS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    notebook = nbformat.v4.new_notebook()
    notebook.cells = [
        nbformat.v4.new_markdown_cell("# RPS冻结信号：匹配诊断与历史覆盖\n\n## tl;dr\n低位转强确认两段仍弱；RPS＋量价没有稳定增量；超跌匹配覆盖不足。核实239只研究期退市A股在当前面板和同花顺原始库均缺失，旧缓存有128只、本机原始价有其中106只，111只仍缺。应先修历史股票池，不追加调参。"),
        nbformat.v4.new_markdown_cell("## Context & Methods\n\n### Key Assumptions\n本轮是已见历史的事后机制诊断，不是新策略或未见样本外。冻结信号完全相同。调整价只作价格表现，不模拟真实成交/现金账户。主20日，5日敏感性；段末退出跨期标签截断。信号前20市场日波动率和成交额中位数各5组、同日25格、对照去掉候选、已知控制至少5只。成交额不是市值；没有行业或历史市值中性。\n\n月均同日匹配差，3月循环块5000次重抽样、每段3主比较98.3333%区间；缺标签/无匹配计数不补零。重叠事件与数据缺口限制因果和盈利判断。其他预计算区间只作描述。"),
        nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport pandas as pd\nimport matplotlib.pyplot as plt\nimport numpy as np\nfrom IPython.display import display\nout = Path.cwd()\nevidence = out.parent / 'exchange_evidence'\ncontrasts = pd.read_csv(out/'reviewed_contrasts.csv')\ncoverage = pd.read_csv(out/'matching_coverage.csv')\nconfirmation = pd.read_csv(out/'confirmation_summary.csv')\nverification = json.loads((out/'verification.json').read_text(encoding='utf-8'))\nterminal = json.loads((evidence/'coverage.json').read_text(encoding='utf-8'))\nraw = json.loads((evidence/'configured_raw_coverage.json').read_text(encoding='utf-8'))\nnames = "+repr(NAMES)+"\norder = "+repr(ORDER)+"\nplt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'figure.dpi':120,'font.size':11})\ncolors = {'early':'#3565a0','late':'#bb8537'}"),
        nbformat.v4.new_markdown_cell("## Data\n行情控制源为哈希已冻结的同花顺前复权面板；名单控制源是2026-10-06读取的上/深交易所终止上市快照。只做覆盖核对，名单不能当时点证券主表。完整SQL与原响应在local_evidence，原JSON/XLSX与请求回执在exchange_evidence。旧TDX缓存与配置原始日线只盘点、不混入标签。"),
        nbformat.v4.new_code_cell("display(pd.DataFrame([{'核对退市代码':terminal['window_terminal_codes'],'当前面板有记录':terminal['in_frozen_panel'],'同花顺原表有记录':terminal['with_any_raw_history'],'缓存有记录':128,'本机原始价有记录':raw['raw_present'],'两者都缺':111}]))\ndisplay(pd.DataFrame([verification])[['sampled_matched_events','passed','unmatched_points_reconciled_to_frozen_v2','maximum_original_point_difference_pp']])"),
        nbformat.v4.new_markdown_cell("## Results\n### 1. 主20日匹配差与统计区间\n均值和区间均基于月均同日差；有效事件匹配覆盖随点估计同时展示，避免把小子样本推广到全部候选。价格差不是年化、账户收益或因果效果。"),
        nbformat.v4.new_code_cell("primary = contrasts[(contrasts.horizon==20)&(contrasts.comparison=='matched_difference')].merge(coverage[coverage.horizon==20],on=['candidate','baseline','period','horizon'],validate='one_to_one')\nfig, ax = plt.subplots(figsize=(11,5.5))\nfor period,offset,marker,label in [('early',-.13,'o','2018—2021'),('late',.13,'D','2022—2026.09')]:\n    data = primary[primary.period==period].set_index('candidate').reindex(order)\n    y = np.arange(3)+offset\n    ax.errorbar(data.mean_pp,y,xerr=[data.mean_pp-data.lo_pp,data.hi_pp-data.mean_pp],fmt=marker,capsize=4,color=colors[period],mfc=colors[period] if period=='early' else 'white',label=label)\n    for point,yy,cov in zip(data.mean_pp,y,data.valid_match_coverage_pct):\n        ax.annotate(f'{point:+.3f}；覆盖{cov:.1f}%',(point,yy),xytext=(0,-17 if period=='late' else 9),textcoords='offset points',ha='center',fontsize=10)\nax.set_yticks(range(3),[names[m]+' vs '+names[b] for m,b in zip(order,['rps_only','low_only','oversold_only'])]); ax.invert_yaxis()\nax.axvline(0,color='#444444',linewidth=.8); ax.grid(axis='x',color='#dddddd',linewidth=.5); ax.set_axisbelow(True)\nax.set_xlabel('月均同日匹配20日价格差（百分点）；98.3333%区间'); ax.set_title('固定3对比：过去波动率与流动性代理匹配')\nax.legend(loc='upper right'); ax.set_ylim(2.7,-.65); fig.tight_layout(); fig.savefig(out/'matched-intervals.png'); plt.show()\ndisplay(primary[['candidate','baseline','period','mean_pp','lo_pp','hi_pp','months','paired_days','labels','matched_used_labels','valid_match_coverage_pct']].round(3))"),
        nbformat.v4.new_markdown_cell("低位转强两段仍负、区间低于0；RPS＋量价两段区间跨0，点估计跨段变号。超跌确认匹配覆盖约13%/38%，早期只有37月、148日；宽区间与覆盖选择限制了判断。\n\n### 2. 确认前后价格变化\n仅描述已经确认的样本，先按信号日等权平均，再平均日期。这不是可实施的提前买入收益，也不是确认导致收益差的因果证明。"),
        nbformat.v4.new_code_cell("fig, ax = plt.subplots(figsize=(10,4.8))\nfor period,offset,label,hatch in [('early',-.18,'2018—2021',''),('late',.18,'2022—2026.09','//')]:\n    data = confirmation[confirmation.period==period].set_index('model').reindex(order)\n    x = np.arange(3)+offset\n    bars = ax.bar(x,data.total_to_next_open_pct,width=.34,color=colors[period],label=label,hatch=hatch)\n    ax.bar_label(bars,fmt='%.3f%%',padding=4)\nax.set_xticks(range(3),[names[m] for m in order]); ax.set_ylim(0,3.9)\nax.set_ylabel('确认前日收盘 → 信号后次日开盘（%）'); ax.set_title('已确认事件的价格变化（非策略收益）'); ax.legend()\nax.grid(axis='y',color='#dddddd',linewidth=.5); ax.set_axisbelow(True); fig.tight_layout(); fig.savefig(out/'confirmation-price.png'); plt.show()\ndisplay(confirmation[confirmation.model.isin(order)][['model','period','signal_day_pct','next_open_gap_pct','total_to_next_open_pct','next_open_available']].round(3))"),
        nbformat.v4.new_markdown_cell("## Takeaways\n先补数，后判断盈利。239只研究期终止上市股票都缺当前同花顺历史，128只旧缓存仅是修复线索；其中106只存在本机原始价，22只只有缓存、111只两者均无，5只原始末日早于缓存。数据契约要求不复用代码的证券ID与有效期、历史ST/行业/股本、公司行动与可得时间、原始价格限制和最终处置约定。\n\n确认日条件天然挑选上涨事件，因此约3%的确认前后涨幅不能作为昨天买入的可执行修复。补数后另版本重跑冻结规则仍是旧历史复核；不能临时改阈值、删失败年或叫独立样本外。没有实盘、券商或自动下单动作。")]
    notebook.cells[-1].source += "\n\n当前同花顺market.history入口对601258.SH、000540.SZ的2023年6月未复权历史查询均返回Unknown thscode，未取得行情；仅限两只请求的可行性结论，不说明其他来源不存在。需具备相应历史覆盖的合法数据或原始导出，不能猜补。回执：history_probe/summary.json。"
    notebook.metadata["kernelspec"] = dict(display_name="Python research",language="python",name="python3")
    nbformat.validate(notebook)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable,"-m","ipykernel_launcher","-f","{connection_file}"]
    NotebookClient(notebook,km=manager,timeout=120,resources={"metadata":{"path":str(OUT)}}).execute()
    nbformat.validate(notebook)
    nbformat.write(notebook,HERE / "review.ipynb")
    reviewed_rows = [dict(对比=NAMES[r.candidate]+" vs "+NAMES[r.baseline],历史段=r.period,
        月均差百分点=round(r.mean_pp,6),区间下限=round(r.lo_pp,6),区间上限=round(r.hi_pp,6),
        标签匹配覆盖百分比=round(r.valid_match_coverage_pct,4),有效月=r.months,有效日=r.paired_days,
        有效标签=r.labels,匹配标签=r.matched_used_labels) for r in main_result.itertuples()]
    source = dict(label="冻结信号：20日历史匹配诊断",files=[dict(label=f) for f in ["protocol.json","reviewed_contrasts.csv","annotation_notes.json","matching_coverage.csv","verification.json","final_manifest.json"]],
        filters=["2018—2021；2022—2026-09-30", "固定3对比、同日25格，已知非候选对照至少5只"],
        caveats=["事后诊断、已见历史；不是独立样本外、因果效应、年化或账户收益。", "历史股票池缺239只研究期终止上市代码；退市名单快照不是完整证券历史主表。", "仅过去波动率与成交额代理匹配，不是市值/行业中性。", "超跌有效标签匹配覆盖约13%/38%，不能推广为全部超跌结论。", "3月循环块5000次重抽样、每段3主对比98.3333%区间；重叠事件。"],
        metricDefinitions=[dict(label="月均同日20日匹配差",definition="下一日开盘至其后第20市场日开盘调整价表现，减同日同格非候选基线均值；先每天按有效候选事件加权，再等权平均月均差。段末跨期标签不可用。")])
    inventory_rows = [dict(项目="研究期官方终止上市A股",数量=239),dict(项目="冻结面板/同花顺原表存在",数量=0),dict(项目="旧TDX缓存存在",数量=128),dict(项目="配置TDX原始价存在",数量=106),dict(项目="只有缓存，没有原始价",数量=22),dict(项目="缓存和原始价均缺",数量=111),dict(项目="原始末日早于缓存",数量=5)]
    inventory_source = dict(label="交易所名单与本机覆盖核对",files=[dict(label=f) for f in ["sse_page1.json","szse_terminated.xlsx","study_window_terminated_coverage.csv","raw_coverage.sql","repair_backlog.csv","data_contract.json"]],
        caveats=["原名单于2026-10-06取得，窗口终止日2018-01-01—2026-09-30；沪深A代码级核对。", "名单不是历史完整证券主表；不说明每只都会满足策略条件，也不量化偏差方向。", "缓存、原始价尚未完成复权与历史状态验收，没有并入RPS输入。"],
        filters=["沪深A股；研究期239终止上市代码"],metricDefinitions=[dict(label="存在",definition="缓存中有研究期行情或明确配置路径存在原始日线；不等于完整或可交易状态验证。")])
    payload = dict(schemaVersion=1,items=[dict(id="rps-diag",title="RPS确认诊断与历史覆盖",queries=[
        dict(id="matched-20d",source=source,rows=reviewed_rows,columns=list(reviewed_rows[0]),reportingPeriod="2018-01-01—2021-12-31；2022-01-01—2026-09-30",preview=dict(kind="aggregate",note="固定3主对比的月均差和匹配覆盖；非逐笔成交。")),
        dict(id="history-coverage",source=inventory_source,rows=inventory_rows,columns=["项目","数量"],reportingPeriod="2026-10-06快照核对2018—2026.09终止上市代码",preview=dict(kind="aggregate",note="来源存在性盘点；未补入回测。"))])])
    (OUT / "reviewed-sources.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(notebook=str(HERE / "review.ipynb"),executed=True,cells=len(notebook.cells)),ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
