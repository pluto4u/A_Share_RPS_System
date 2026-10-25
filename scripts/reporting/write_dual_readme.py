"""Write an evidence-linked handoff from completed, audited local CSVs."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = PROJECT_ROOT
OUT = ROOT / "output/dual_research_20261002_v1"
LABELS = {
    "A_retest": "A 横盘缩量后放量回踩",
    "A_volume": "A 放量后直接进入对照",
    "B_trend": "B 向上结构中的缩量回调",
    "control": "MA20基础控制",
    "A_plus_B": "A+B共享20只",
}


def n(value: Any) -> str:
    return "缺" if value is None or pd.isna(value) else f"{float(value):.2f}"


def main() -> None:
    checks = pd.read_csv(OUT / "independent_checks.csv")
    if not checks.passed.all():
        raise RuntimeError("audit checks failed; handoff withheld")
    freeze = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    history = pd.read_csv(OUT / "history/metrics.csv")
    costs = pd.read_csv(OUT / "cost_decomposition.csv")
    positions = pd.read_csv(OUT / "position_concentration.csv")
    case = pd.read_csv(OUT / "zjx_2025_case.csv")
    direction = pd.read_csv(OUT / "old_daily_direction_summary.csv")
    horizons = pd.read_csv(OUT / "old_daily_horizon_audit.csv")
    leaks = pd.read_csv(OUT / "old_boundary_leaks.csv")
    models = ["A_retest", "A_volume", "B_trend", "control", "A_plus_B"]
    rows = []
    for model in models:
        r = history.loc[history.model == model].iloc[0]
        rows.append(
            f"| {LABELS[model]} | {n(r.cagr_pct)} | {n(r.max_drawdown_pct)} | "
            f"{n(r.net_profit)} | {int(r.portfolio_trades)} | "
            f"[{n(r.ci_lo_pct)}, {n(r.ci_hi_pct)}] |"
        )
    a = history.loc[history.model == "A_retest"].iloc[0]
    control = history.loc[history.model == "A_volume"].iloc[0]
    uplift = (
        pd.notna(a.ci_lo_pct) and a.ci_lo_pct > 0 and a.excess_mean_pct > control.excess_mean_pct
    )
    concentration_rows = []
    cost_rows = []
    for model in ("A_retest", "B_trend", "A_plus_B"):
        p = positions.loc[(positions.period == "history") & (positions.model == model)].iloc[0]
        c = costs.loc[(costs.period == "history") & (costs.model == model)].iloc[0]
        concentration_rows.append(
            f"| {LABELS[model]} | {int(p.unique_held_stocks)} | "
            f"{n(p.mean_holdings)} | {n(p.top5_stock_days_share_pct)} |"
        )
        cost_rows.append(
            f"| {LABELS[model]} | {n(c.gross_price_pnl)} | "
            f"{n(c.modeled_total_cost)} | {n(c.net_profit)} |"
        )
    case_rows = [
        f"| {LABELS[r.model]} | {r.signals_in_2025} | {r.portfolio_entries} | "
        f"{r.first_signal if pd.notna(r.first_signal) else '缺'} | "
        f"{n(r.net_pnl_for_these_trades)} |"
        for r in case.itertuples(index=False)
    ]
    direction_rows = [
        f"| {r.source} | {r.both_present} | {r.same_sign_including_both_zero} | "
        f"{n(r.d1_mean_pct)} | {n(r.d5_mean_pct)} | "
        f"{n(r.d1_excess_mean_pct)} | {n(r.d5_excess_mean_pct)} |"
        for r in direction.itertuples(index=False)
    ]
    failed_risk = history.loc[~history.risk_limit_pass, "model"].map(LABELS).tolist()
    text = "\n".join(
        [
            "# 本地双引擎研究交付",
            "",
            "已完成两套日线候选规则、严格样本内选参、共享20只资金账本、历史复盘及确定性核对。",
            "**工程可以运行；尚未找到能够稳定赚钱、且满足20%回撤约束的已验证策略。**",
            "",
            "100万元是研究初始资金；收盘确认、下一市场交易日开盘模拟。不是实盘交易指令。",
            "复权可分割研究单位不是真实股数；整手、最低佣金、真实历史限价与容量尚未完整适配。",
            "",
            "## 两套模式",
            "",
            "- A：前半段高点/后半段低点下跌，之后更窄、更低量横盘，阳线放量站上MA20；"
            "后续缩量实际回落至MA20附近才确认。放量日不进入。",
            "- B：MA20向上、低点抬高，近均线缩量回调后阳线恢复。"
            "它不要求先大跌，也不使用创新高或海龟突破。",
            "- 共同失效研究定义：收盘低于MA20或确认时冻结的该次回踩/整理低点，"
            "按原引擎下一可成交开盘处理。RPS120只辅助同日排序。",
            "",
            "## 已污染历史复盘",
            "",
            f"时间：{history.iloc[0].start}至{history.iloc[0].end}。2022年后的数据已被查看，不能重新称独立样本外。",
            "单股超额区间采用月度区块2000次重采样；不是组合年化区间，且没有校正A网格搜索的选择偏差。",
            "",
            "| 模型 | 研究年化% | 每日最大回撤% | 净利润（元） | 组合模拟笔数 | "
            "单股超额95%区间（百分点） |",
            "|---|---:|---:|---:|---:|---|",
            *rows,
            "",
            "按用户指定的区间整体高于0且高于放量直接进入对照要求："
            f"{'历史统计通过，仍无独立验证' if uplift else '没有提升'}。",
            "未通过20%风险验收线："
            f"{'、'.join(failed_risk) if failed_risk else '无，但不能保证未来回撤'}。",
            "",
            "## 持仓是否集中",
            "",
            "| 模型 | 实际持仓股票数 | 平均同时持仓 | 前5只占持仓股日% |",
            "|---|---:|---:|---:|",
            *concentration_rows,
            "",
            "股日是所有持仓段的日数之和，不是连续最长持有天数。完整股票贡献见[position_concentration.csv](position_concentration.csv)和各模型stock_contribution.csv。",
            "",
            "## 价格与费用拆解",
            "",
            "| 模型 | 不含模拟费用的价格盈亏（元） | 模拟费用（元） | 净盈亏（元） |",
            "|---|---:|---:|---:|",
            *cost_rows,
            "",
            "价格盈亏-费用=资金净变化已独立核对；这只是账面分解，不证明亏损的因果解释。",
            "",
            "## 2025年中际旭创的已知案例",
            "",
            "| 模型 | 非重叠可成交信号 | 实际组合进入 | 首次信号 | 该信号年份交易净盈亏（元） |",
            "|---|---:|---:|---|---:|",
            *case_rows,
            "",
            "该股是用户事先指出的案例，不能用它选参数或充当独立验证。出现信号不等于抓住全年主升或证明组合盈利。",
            "",
            "## 冻结规则核对",
            "",
            f"A只搜索允许的1296组合；选中编号{freeze['a_combo']}，冻结参数{freeze['a_params']}。",
            "训练输入、控制收益与未平仓结算均截断到2021-12-31；先样本内排名，再冻结，再历史复盘。B事前单一假设，未优化。",
            f"旧run_pullback_system.py历史结算边界存在{len(leaks)}笔跨截止日数据；证据[old_boundary_leaks.csv](old_boundary_leaks.csv)。新版本未覆盖旧脚本或结果。",
            "旧版run_pullback_system.py:311在scan_stock中对整段股票数组执行sell_next_open；"
            "480计算超额，481只按信号日过滤，482汇总这些可能越过截止日的ret。"
            "这是样本外价格进入旧挑选的路径；新版本不冒充旧结果。",
            "1296组合挑选仍有样本内过拟合风险；当前历史ST身份和退市覆盖有限。",
            "",
            "## 旧历史对照：隔日与隔周",
            "",
            "| 口径 | 两期均非缺行数 | 两期同号行数（含同时为0） | 隔日均值% | 隔周均值% | "
            "隔日超额均值% | 隔周超额均值% |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *direction_rows,
            "",
            f"按市场交易日重算：旧隔日不一致{int((~horizons.d1_matches_market_horizon).sum())}行，旧隔周不一致{int((~horizons.d5_matches_market_horizon).sum())}行。",
            "旧脚本用股票第1/5根后续K线，停牌时不等于市场第1/5交易日；旧对照均值不能直接用于修复后的超额，因此重算超额标缺。新双引擎使用真实市场日，停牌缺收盘保留缺。",
            "",
            "## 怎么看和运行",
            "",
            "1. [history/metrics.csv](history/metrics.csv)：两策略、共享组合和同引擎对照。",
            "2. [A信号](history/A_retest/trade_outcomes.csv)、"
            "[B信号](history/B_trend/trade_outcomes.csv)：两份历史信号结果。",
            "3. [最新复核状态](history/latest_candidates.csv)："
            "面板末日待人工复核；无实时买卖建议。",
            "4. [独立核对](independent_checks.csv)、[freeze.json](freeze.json)："
            "算术、时间边界和输入/源码指纹。",
            "5. [TESTS.md](TESTS.md)：离线测试与全仓已有问题。"
            "入口../../run_dual_research.py；读结果审计../../audit_dual_research.py。",
            "原注册train/replay拒绝覆盖已有目录或再看历史。"
            "只读复核：在策略目录执行`py -3.12 audit_dual_research.py`。"
            "不要删除冻结文件再冒充独立样本外。",
            "",
            "## 故障续算与交付边界",
            "",
            "基础控制组合的完整拒绝字典耗尽本机内存，续算只改日志/存储方式，不改信号、费用、排序和参数。A/B已完成成交与逐日净值保持一致，同日控制收益也保持一致；原结果CSV哈希未变。",
            "复盘过程中重构过相同控制事件并做A/B一致性核对，因此也不声称技术上没有重新读取历史。记录见[recovery_manifest.json](recovery_manifest.json)和[verification.json](verification.json)。",
            f"独立结果检查：{len(checks)}项，失败{int((~checks.passed).sum())}项。RPS分母仅抽查两个样本内市场日，不代表全历史已验证。",
            "当前目录不是Git仓库，没有擅自建立仓库或上传本地数据；全仓旧测试/ruff/mypy未全绿，不宣称已满足开PR条件。",
            "",
            "## 下一轮只提出样本内假设",
            "",
            "- 形态辨别：相同缩量形态，在下跌反弹和已经形成持续上升结构中是否具有不同样本内收益？",
            "- 失效辨别：损失是否主要来自真实结构破坏，还是正常回调中的频繁反复触发？"
            "先标注样本内交易路径和持有段，不根据已看历史给出新参数。",
            "- 组合辨别：日线信号优势与RPS排序后的有限名额是否一致；"
            "先用样本内成交/拒绝日志检查选择偏差。",
            "下一轮需另行登记研究边界；当前冻结版本不再优化。真正验证只能积累冻结后的未来未见数据。",
            "",
        ]
    )
    (OUT / "README.md").write_text(text, encoding="utf-8")
    print("Evidence-linked README.md written; no strategy or parameters changed.")


if __name__ == "__main__":
    main()
