"""Read-only handoff from verified IS diagnostic CSVs, no new trading rules."""

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
OUT = ROOT / "output/dual_research_is_diagnostics_20261002_v2"
VIS = Path("C:/Users/pluto4u/.codex/visualizations/2026/06/18/019edb65-8cac-7f32-be6b-481f9d1d8c57")
LABELS = {"A_retest": "A 横盘回踩", "A_volume": "A 放量对照", "B_trend": "B 趋势回调"}


def n(value: Any) -> str:
    return "缺" if pd.isna(value) else f"{float(value):.2f}"


def save_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def main() -> None:
    checks = pd.read_csv(OUT / "independent_checks.csv")
    if not checks.passed.all():
        raise RuntimeError("diagnostic checks failed")
    cost = pd.read_csv(OUT / "cost_cash_diagnostics.csv")
    populations = pd.read_csv(OUT / "population_summary.csv")
    refs = pd.read_csv(OUT / "entry_reference_distances.csv")
    paths = pd.read_csv(OUT / "exit_path_diagnostics.csv")
    paired = pd.read_csv(OUT / "admission_paired_summary.csv")
    grid = pd.read_csv(OUT / "grid_context.csv").iloc[0]
    raw = pd.read_csv(OUT / "is_event_diagnostics.csv", float_precision="round_trip")
    a = raw.loc[(raw.model == "A_retest") & (raw.disposition == "admitted")].copy()
    a["open_state"] = a.entry_px.le(a.pocket_low).map(
        {True: "open_at_or_below_floor", False: "open_above_floor"}
    )
    cohort = (
        a.groupby("open_state")
        .agg(
            events=("ret", "size"),
            mean_ret=("ret", "mean"),
            median_hold=("hold_market_sessions", "median"),
        )
        .reset_index()
    )
    cohort["mean_ret_pct"] = cohort.mean_ret * 100
    cohort.to_csv(OUT / "entry_state_cohorts.csv", index=False, encoding="utf-8-sig")
    costs_table = [
        f"| {LABELS[r.model]} | {n(r.gross_price_pnl)} | {n(r.modeled_cost)} | "
        f"{n(r.net_pnl)} | {n(r.mean_cash_pct)} |"
        for r in cost.itertuples(index=False)
    ]
    population_table = [
        f"| {LABELS[r.model]} | {r.population} | {r.events} | "
        f"{n(r.mean_net_return_pct)} | {n(r.win_pct)} | {n(r.median_hold_sessions)} |"
        for r in populations.itertuples(index=False)
    ]
    pair_table = [
        f"| {LABELS[r.model]} | {r.paired_dates} | {n(r.mean_delta_pct)} | "
        f"[{n(r.ci_lo_pct)}, {n(r.ci_hi_pct)}] |"
        for r in paired.itertuples(index=False)
    ]
    ar = refs.loc[(refs.model == "A_retest") & (refs.reference == "frozen_floor")].iloc[0]
    ca = cohort.loc[cohort.open_state == "open_at_or_below_floor"].iloc[0]
    cb = cohort.loc[cohort.open_state == "open_above_floor"].iloc[0]
    floor = paths.loc[(paths.model == "A_retest") & (paths.trigger_reason == "floor_only")].iloc[0]
    structure = pd.read_csv(OUT / "structure_groups.csv")
    b = structure.loc[
        (structure.model == "B_trend")
        & (structure.disposition == "admitted")
        & (structure.structure == "ordered_20_60_120_up60")
    ].iloc[0]
    report = "\n".join(
        [
            "# 继续研究：收益不足的样本内诊断",
            "",
            "结论：不是只缺资金利用率，也不能把B的损失归因于手续费。",
            "只分析2016-09-01至2021-12-31；未读取2022年后结果表用于新研究。旧参数、入场、退出、RPS排序、面板、结果保持冻结。",
            "这轮是看过上轮结果后提出的解释性样本内分析，不是全新事前实验或独立验证。",
            "",
            "## 1. 先分清毛收益、费用与账户结果",
            "",
            "| 模型 | 毛价格盈亏（元） | 原引擎模拟费用（元） | 净盈亏（元） | 平均现金% |",
            "|---|---:|---:|---:|---:|",
            *costs_table,
            "",
            "毛价格盈亏-费用=账户净变化已独立核对。A的毛利不够覆盖费用，B在扣费前已亏损，不能只靠降低成本解决。",
            "100万元研究仓位使用复权可分割单位；费用不是实际券商整手/最低佣金/冲击的完整模拟。",
            "",
            "## 2. 形态信号不等于资金组合",
            "",
            "| 模型 | 口径 | 事件数 | 平均单笔净收益% | 胜率% | 持有市场日中位数 |",
            "|---|---|---:|---:|---:|---:|",
            *population_table,
            "",
            "all_executable_offers为全部可成交事件；nonoverlap_stock_chain为单股非重叠链；portfolio_admitted为实际组合进入；capacity_blocked为满位/资金不足拒绝。",
            "不能把平均单笔收益直接当作资金年化，也不能把链的正均值当作组合能够赚钱。",
            f"既有网格合格组合{int(grid.eligible_combos)}个，其中只有{int(grid.eligible_positive_absolute_mean)}个单股链平均绝对净收益为正；相对弱对照为正有{int(grid.eligible_positive_relative_mean)}个。未据此重新选参。",
            "",
            "## 3. 很窄的回踩低点，是否代表整个形态失效？",
            "",
            f"A实际进入{int(ar.admitted_events)}笔，入场开盘距离冻结回踩低点的中位数仅{n(ar.distance_median_pct)}%。",
            f"其中{int(ar.open_at_or_below_reference_count)}笔（{n(ar.open_at_or_below_reference_pct)}%）开盘已等于或低于昨天已知低点，原规则仍进入。",
            f"但这组平均单笔净收益为{n(ca.mean_ret_pct)}%，开盘在低点上方组为{n(cb.mean_ret_pct)}%。因此不能把‘取消已失效开盘’直接宣称为盈利修复。",
            f"A仅因低点触发退出{int(floor.events)}笔，持有市场日中位数{n(floor.median_hold_sessions)}；亏损且后续20日完整的{int(floor.losing_full_post20_events)}笔中，有{int(floor.losing_post20_reclaims_entry_count)}笔后来收盘重新超过入场价。",
            "这提示值得检验低点失效定义，但未来反弹不能证明当时该继续持有，期间更深回撤未被一个反弹次数消除。",
            "MA20单独退出组是事后按退出原因划分的群体；其收益较高不能用来预先挑出赢家，直接保留这个组会产生未来信息泄漏。",
            "",
            "## 4. 简单增加多头排列仍不足以证明优势",
            "",
            f"B实际进入中，已有C>MA20>MA60>MA120且MA60向上的{int(b.events)}笔，平均单笔净收益仍为{n(b.mean_net_return_pct)}%。",
            "这是既有交易的描述性分组，不是加筛选后的新资金组合；不能据分组均值选一个新阈值。",
            "",
            "## 5. RPS排序是不是主要问题？",
            "",
            "同一信号日、实际进入与因容量拒绝的候选平均净收益差：",
            "",
            "| 模型 | 配对信号日数 | 平均差（百分点） | 月度区块95%区间 |",
            "|---|---:|---:|---|",
            *pair_table,
            "",
            "三个区间均跨0，当前不能证明改掉RPS排序就会改善。A只在少数拥挤日期有可配对事件；总体闲置高并不保证信号集中发生时有名额。",
            "RPS缓存保留全市场口径；固定分组只是观察，不在候选池重新排名。名额与既有持仓状态混杂，此处不是RPS的随机因果实验。",
            "",
            "## 下一轮优先顺序（仅假设，不部署）",
            "",
            "1. 保留四段式入场，先检验失效边界是否描述平台结构破坏，"
            "还是主要反映一两根回踩K线的波动；低点更宽不天然更好，风险也可能增加。",
            "2. 在样本内预先登记比较入场有效性/退出机制，和冻结原版用同一引擎、"
            "同一资金规则做单因素实验；不能根据已看历史设新参数。",
            "3. 若仍无绝对毛优势，应重新定义量价形态的时间尺度和真正持续上升结构，"
            "不堆更多RPS门槛或靠强行满仓。",
            "当前冻结版本不改。若用户坚持原退出也必须固定，那么下一轮只能继续诊断，不能把上述假设当成已经实现的收益改进。",
            "",
            "## 工程与证据",
            "",
            f"独立核对{len(checks)}项通过；包括冻结输入指纹、候选/进入/拒绝、费用守恒及固定抽样市场日收益核对。价格窗口抽查60事件，不声称全路径独立验证。",
            "入口../../diagnose_dual_is.py；独立审计../../audit_dual_is_diagnostics.py。首次诊断字段编号错误的尝试保留在../dual_research_is_diagnostics_20261002/FAILED.md，禁止用于结论；v2补了列稳定性单测。",
            "[成本与现金](cost_cash_diagnostics.csv)、[路径诊断](exit_path_diagnostics.csv)、[同日组合比较](admission_paired_summary.csv)、[入场参照距离](entry_reference_distances.csv)、[独立核对](independent_checks.csv)。",
            "目录不是Git仓库，未创建PR；全仓已有问题未消除。Jupyter依赖未提供，本轮交付可复跑Python脚本与CSV，不声称已执行Jupyter。",
            "",
            "## 外部方法参考",
            "",
            "交易费用和重复挑选是研究方法问题，文献不证明这套A股形态赚钱：",
            "",
            "- [Frazzini等：交易成本研究](https://www.aqr.com/insights/research/working-paper/trading-costs-of-asset-pricing-anomalies)。",
            "- [Bailey等：回测过拟合概率](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。",
            "",
        ]
    )
    (OUT / "README.md").write_text(report, encoding="utf-8")
    rows = []
    for r in cost.loc[cost.model.isin(["A_retest", "B_trend"])].itertuples(index=False):
        for component, value in (
            ("毛价格盈亏", r.gross_price_pnl),
            ("费用扣减", -r.modeled_cost),
            ("净盈亏", r.net_pnl),
        ):
            rows.append(
                {"component": component, "strategy": LABELS[r.model], "amount_wan": value / 10_000}
            )
    source = {
        "label": "冻结策略的样本内价格/费用账本",
        "files": ["output/dual_research_is_diagnostics_20261002_v2/cost_cash_diagnostics.csv"],
        "caveats": [
            "仅2016-09-01至2021-12-31，非新独立样本外。",
            "取消费用只是算术分解，未运行一个无费用策略。",
            "复权可分割单位不是完整真实账户成交。",
        ],
        "metricDefinitions": [
            {
                "label": "模拟资金盈亏",
                "definition": "原引擎入场数量与退出价格计算毛盈亏，减模拟费用等于净盈亏。",
            }
        ],
        "filters": ["原配置与成交规则冻结", "初始研究资金100万元"],
    }
    chart = {
        "schemaVersion": 1,
        "id": "dual-is-cost-diagnostics",
        "queryId": "is-cost-decomposition",
        "title": "样本内：价格盈亏与交易费用（万元）",
        "chart": {
            "type": "bar",
            "x": "component",
            "y": "amount_wan",
            "series": "strategy",
            "showXAxisLabel": False,
            "yLabel": "模拟盈亏（万元）",
            "startAtZero": True,
        },
        "rows": rows,
        "source": source,
        "theme": "codex-classic",
    }
    save_json(VIS / "dual-is-cost-diagnostics.json", chart)
    chart_receipt = {
        "schemaVersion": 1,
        "items": [
            {
                "id": "is-cost-decomposition",
                "title": "样本内费用与价格盈亏",
                "queries": [
                    {
                        "id": "is-cost-decomposition",
                        "source": source,
                        "rows": rows,
                        "columns": [
                            {"field": "component", "label": "成分"},
                            {"field": "strategy", "label": "模型"},
                            {"field": "amount_wan", "label": "模拟盈亏（万元）"},
                        ],
                        "reportingPeriod": "2016-09-01至2021-12-31",
                    }
                ],
            }
        ],
    }
    save_json(VIS / "dual-is-cost-sources.json", chart_receipt)
    more = {
        "schemaVersion": 1,
        "items": [
            {
                "id": "is-entry-exit-reference",
                "title": "A入场位置与短持有期",
                "queries": [
                    {
                        "id": "is-reference-distance",
                        "source": {
                            "label": "样本内事件与已知参照位",
                            "files": [
                                "output/dual_research_is_diagnostics_20261002_v2/entry_reference_distances.csv",
                                "output/dual_research_is_diagnostics_20261002_v2/population_summary.csv",
                            ],
                            "caveats": [
                                "结构/退出原因分组均未变更交易规则，事后反弹不是可实现收益证明。"
                            ],
                        },
                        "rows": [
                            ar[
                                [
                                    "model",
                                    "admitted_events",
                                    "open_at_or_below_reference_count",
                                    "distance_median_pct",
                                ]
                            ].to_dict()
                        ],
                        "columns": [
                            "model",
                            "admitted_events",
                            "open_at_or_below_reference_count",
                            "distance_median_pct",
                        ],
                        "reportingPeriod": "2016-09-01至2021-12-31",
                    }
                ],
            },
            {
                "id": "is-admission-comparison",
                "title": "排序作用仍未建立",
                "queries": [
                    {
                        "id": "is-admission-pairs",
                        "source": {
                            "label": "同日容量拒绝对照",
                            "files": [
                                "output/dual_research_is_diagnostics_20261002_v2/admission_paired_summary.csv"
                            ],
                            "caveats": ["非随机实验，不能孤立RPS因果作用；2000次月度区块重采样。"],
                        },
                        "rows": paired.to_dict("records"),
                        "columns": [
                            "model",
                            "paired_dates",
                            "mean_delta_pct",
                            "ci_lo_pct",
                            "ci_hi_pct",
                        ],
                        "reportingPeriod": "2016-09-01至2021-12-31",
                    }
                ],
            },
        ],
    }
    save_json(VIS / "dual-is-result-sources.json", more)
    print("Verified IS handoff and bounded native chart inputs written.")


if __name__ == "__main__":
    main()
