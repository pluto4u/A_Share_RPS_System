"""Present source-backed exploratory descriptions without new trade rules."""

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
OUT = ROOT / "output/rps_phase_research_20261003_v1"
VIS = Path("C:/Users/pluto4u/.codex/visualizations/2026/06/18/019edb65-8cac-7f32-be6b-481f9d1d8c57")
OBSERVATIONS = {
    "rps50_reference_direction": "横盘RPS50",
    "rps50_retest_direction": "回踩RPS50",
    "rps120_retest_direction": "回踩RPS120",
}
STATES = {"improving": "上升", "flat_or_down": "持平或下降", "missing": "缺"}


def save(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def main() -> None:
    audit = pd.read_csv(OUT / "independent_checks.csv")
    if not audit.passed.all():
        raise ValueError("audit not passed")
    frame = pd.read_csv(OUT / "cohorts.csv", float_precision="round_trip")
    a = frame.loc[frame.population == "A_portfolio_admitted"].copy()
    rows = [
        {
            "phase": OBSERVATIONS[r.observation],
            "direction": STATES[r.state],
            "events": int(r.events),
        }
        for r in a.itertuples(index=False)
    ]
    source = {
        "label": "冻结A策略样本内RPS阶段分组",
        "files": ["output/rps_phase_research_20261003_v1/cohorts.csv"],
        "filters": ["模型：A_retest实际组合进入", "原入场、退出、成交成本保持不变"],
        "caveats": [
            "三种观察对应同一批交易，不能把不同观察的交易数相加。",
            "上升是阶段端点的RPS差为正，缺失没有填零。",
        ],
    }
    save(
        VIS / "rps-phase-counts.json",
        {
            "schemaVersion": 1,
            "id": "rps-phase-counts",
            "title": "A样本内：RPS变化阶段分布",
            "chart": {
                "type": "bar",
                "x": "phase",
                "y": "events",
                "series": "direction",
                "showXAxisLabel": False,
                "yLabel": "既有进入交易数（笔）",
                "startAtZero": True,
            },
            "rows": rows,
            "source": source,
            "theme": "codex-classic",
        },
    )
    save(
        VIS / "rps-phase-count-sources.json",
        {
            "schemaVersion": 1,
            "items": [
                {
                    "id": "phase-distribution",
                    "title": "RPS阶段分布",
                    "queries": [
                        {
                            "id": "phase-counts",
                            "source": source,
                            "rows": rows,
                            "columns": ["phase", "direction", "events"],
                            "reportingPeriod": "2016-09-01至2021-12-31",
                        }
                    ],
                }
            ],
        },
    )
    numeric = a.loc[a.state != "missing"].copy()
    result_rows = numeric[
        ["observation", "state", "events", "mean_frozen_net_return_pct", "ci_lo_pct", "ci_hi_pct"]
    ].to_dict(orient="records")
    result_source = {
        "label": "原冻结交易的RPS描述性收益分组",
        "files": [
            "output/rps_phase_research_20261003_v1/cohorts.csv",
            "output/rps_phase_research_20261003_v1/event_features.csv",
        ],
        "caveats": [
            "分组均值不是重新筛选后的组合收益，也不是账户年化。",
            "95%月度区块区间只描述分组均值，不能据区间重叠推断组间差异。",
            "探索性研究，区间未作多重比较调整，没有新的独立样本外验证。",
        ],
    }
    save(
        VIS / "rps-phase-result-sources.json",
        {
            "schemaVersion": 1,
            "items": [
                {
                    "id": "phase-descriptive-outcome",
                    "title": "RPS上升尚未证明正收益优势",
                    "queries": [
                        {
                            "id": "frozen-group-outcomes",
                            "source": result_source,
                            "rows": result_rows,
                            "columns": list(result_rows[0]),
                            "reportingPeriod": "2016-09-01至2021-12-31",
                        }
                    ],
                },
                {
                    "id": "image-pattern-hypotheses",
                    "title": "截图中的深整理与二次上行",
                    "queries": [
                        {
                            "id": "provided-image-patterns",
                            "source": {
                                "label": "用户提供的图5、图6",
                                "files": [
                                    "codex-clipboard-89ff2a87-de41-472b-b8ec-03fe26310ebe.png",
                                    "codex-clipboard-c261df78-a031-48fb-aaf7-7d8305a83f78.png",
                                ],
                                "caveats": [
                                    "截图没有起止交易日期及完整RPS曲线，不能确认逐股RPS值。",
                                    "形态描述只用于提出假设，事后展示的赢家不是独立验证样本。",
                                ],
                            },
                            "summary": (
                                "图5、图6可见先拉升、深整理、再上行的过程，"
                                "整理段曾跌破短均线。"
                            ),
                        }
                    ],
                },
            ],
        },
    )
    lines = [
        "# RPS更好的应用：阶段轨迹，而非叠加固定门槛",
        "",
        "结论：保留RPS，但把短期恢复、中期持续、长期背景分开记录。",
        "仅加RPS上升条件尚未证明原A策略有正收益优势；没有改旧策略，也没有新选参数。",
        "",
        "## 截图带来的结构问题",
        "",
        "图1至图4含深回撤后的修复；图5、图6含先拉升、较深整理、再上行。",
        "后两图整理段曾跌破短均线，不能假称已被‘放量后始终守MA20的短回踩’覆盖。",
        "股票名单按用户顺序记录。第三张源捷科技与本地源杰科技名称不一致，实体待确认。",
        "缺截图起止交易日期、完整RPS曲线、圆形标记定义；不能编造六股阶段RPS或案例收益。",
        "",
        "## 三种RPS的研究分工（假设，非部署结论）",
        "",
        "| 观察 | 用途 | 不能做什么 |",
        "|---|---|---|",
        "| RPS50阶段变化 | 检查短期相对强度恢复及回调后的再转强 | 单独证明资金吸筹或立刻确认趋势 |",
        "| RPS120阶段变化 | 检查中期相对强度是否维持 | 覆盖价格结构破坏、保证持续上涨 |",
        "| RPS250水平与变化 | 长期强势背景及历史涨幅背景 | 作为所有底部修复的统一高门槛 |",
        "",
        "各周期按全市场有效股票排名；没有在候选中重排。RPS变化单位为百分位点。",
        "排名提高可来自同业/市场更弱和滚动比较基点变化，不能直接解释为本股上涨。",
        "",
        "## 实际样本内观察",
        "",
        "以下是原A进入交易的分组，不是重新筛选后回测。上升只表示端点RPS差为正。",
        "",
        "| 观察 | 方向 | 笔数 | 原单笔净收益均值% | 95%月度区块区间% |",
        "|---|---|---:|---:|---|",
    ]
    for r in a.itertuples(index=False):
        lines.append(
            f"| {OBSERVATIONS[r.observation]} | {STATES[r.state]} | {r.events} | "
            f"{r.mean_frozen_net_return_pct:.4f} | [{r.ci_lo_pct:.4f}, {r.ci_hi_pct:.4f}] |"
        )
    lines.extend(
        [
            "",
            "所有非缺失A分组均值的区间均跨0，不能确认单独要求RPS上升就有正收益优势。",
            "这不证明RPS无用，也不证明上升组与下降组收益无差异；不是随机实验。",
            "B只观察既有进入，RPS50参考为原确认前3根，不能混称为A的40根横盘。",
            "",
            "## 下一轮研究边界",
            "",
            "先在独立样本内版本比较短回踩低点与原横盘结构边界的失效含义，",
            "保持入场一致；RPS轨迹先作为记录字段，不与退出规则同时优化。",
            "深整理后二次启动若需建模，另列新形态假设；原四段式不被偷换。",
            "",
            "## 复跑与核对",
            "",
            "入口research_rps_phases.py，独立核对audit_rps_phases.py。",
            f"独立核对{len(audit)}项通过，阶段路径抽查60个事件；全市场排名只核对2日×3周期。",
            "原入场退出不改，价格输入截至2021-12-31。没有重建缓存或下载行情。",
            "本轮已看先前结果与用户截图，不是全新事前或独立验证。",
            "可复跑Python与CSV；Jupyter环境缺失，未声称执行notebook。",
            "目录非Git仓库，未建立PR；只对本轮文件做质量检查，不声称全仓检查通过。",
            "",
            "[O’Neil原始RS研究](https://www.oneilglobaladvisors.com/documents/FG/oneil/research/605570_OCM_Relative_Strength_Rating-OGA.pdf)第2页定义",
            "为12个月且偏重近期的美国市场RS，不等于这里的单区间A股RPS，不能搬用其收益。",
            "",
        ]
    )
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(OUT / "README.md", flush=True)


if __name__ == "__main__":
    main()
