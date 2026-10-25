"""Project verified result CSVs into bounded native-chart evidence; no strategy edits."""

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
VIS = Path("C:/Users/pluto4u/.codex/visualizations/2026/06/18/019edb65-8cac-7f32-be6b-481f9d1d8c57")
MODELS = {
    "A_retest": "A 横盘回踩",
    "B_trend": "B 趋势持续",
    "A_plus_B": "A+B 共享20只",
    "control": "MA20基础对照",
}


def main() -> None:
    checks = pd.read_csv(OUT / "independent_checks.csv")
    if not checks.passed.all():
        raise RuntimeError("CSV validation did not pass; presentation withheld")
    frames = []
    for model, label in MODELS.items():
        frame = pd.read_csv(OUT / f"history/{model}/equity_cash.csv")
        months = pd.to_datetime(frame.date).dt.to_period("M")
        sample = frame.groupby(months, sort=True).tail(1)
        sample = pd.concat([frame.head(1), sample]).drop_duplicates("date").copy()
        sample["strategy"] = label
        sample["equity_wan"] = sample.equity / 10_000
        frames.append(sample[["date", "strategy", "equity_wan"]])
    plot = pd.concat(frames, ignore_index=True)
    plot.to_csv(OUT / "monthly_curve_preview.csv", index=False, encoding="utf-8-sig")
    period = f"{plot.date.min()} 至 {plot.date.max()}；初始研究资金100万元"
    files = [
        f"output/dual_research_20261002_v1/history/{model}/equity_cash.csv" for model in MODELS
    ]
    caveats = [
        "2022年后的数据已被查看；曲线是已污染历史复盘，不是新独立样本外。",
        "曲线显示首日和月末实际市场日净值，最大回撤从全部日线计算，不从月末抽样估算。",
        "复权可分割研究单位不是真实股数；整手、最低佣金和容量未完整模拟。",
    ]
    source = {
        "label": "冻结双引擎模拟账本",
        "files": files,
        "caveats": caveats,
        "metricDefinitions": [
            {
                "label": "研究资金净值",
                "definition": "每日现金加持仓市值；末日未平仓按研究边界净估值扣原引擎退出费用。",
            }
        ],
        "filters": ["每组最多20只", "同日RPS120降序", "初始研究资金100万元"],
    }
    chart = {
        "schemaVersion": 1,
        "id": "dual-research-equity",
        "queryId": "monthly-equity",
        "title": "双引擎历史复盘：研究资金净值（非独立验证）",
        "chart": {
            "type": "line",
            "x": "date",
            "y": "equity_wan",
            "series": "strategy",
            "showXAxisLabel": False,
            "yLabel": "研究净值（万元）",
            "startAtZero": True,
        },
        "rows": plot.to_dict("records"),
        "source": source,
        "theme": "codex-classic",
    }
    (VIS / "dual-research-equity.json").write_text(
        json.dumps(chart, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    stat = pd.read_csv(OUT / "history/metrics.csv")
    rows = stat.loc[
        stat.model.isin([*MODELS, "A_volume"]),
        [
            "model",
            "cagr_pct",
            "max_drawdown_pct",
            "portfolio_trades",
            "mean_cash_pct",
            "ci_lo_pct",
            "ci_hi_pct",
            "top5_positive_pnl_share_pct",
        ],
    ].to_dict("records")
    sources: dict[str, Any] = {
        "schemaVersion": 1,
        "items": [
            {
                "id": "equity-comparison",
                "title": "双引擎与基础对照的历史资金曲线",
                "queries": [
                    {
                        "id": "monthly-equity",
                        "source": source,
                        "rows": plot.to_dict("records"),
                        "columns": [
                            {"field": "date", "label": "实际市场日"},
                            {"field": "strategy", "label": "模型"},
                            {"field": "equity_wan", "label": "研究净值（万元）"},
                        ],
                        "reportingPeriod": period,
                        "preview": {"kind": "sample", "note": "首日及月末预览；完整日线保留在CSV"},
                    }
                ],
            },
            {
                "id": "metrics-and-risk",
                "title": "每日净值风险与信号超额证据",
                "queries": [
                    {
                        "id": "frozen-metrics",
                        "source": {
                            "label": "冻结结果与独立CSV核对",
                            "files": [
                                "output/dual_research_20261002_v1/history/metrics.csv",
                                "output/dual_research_20261002_v1/independent_checks.csv",
                                "output/dual_research_20261002_v1/freeze.json",
                            ],
                            "caveats": caveats
                            + [
                                "A在1296组合中挑选，月度区间未校正该选择偏差；B仍缺独立验证。",
                                "20%是用户风险验收线，不是未来损失保证。",
                            ],
                        },
                        "rows": rows,
                        "columns": [
                            {"field": "model", "label": "模型"},
                            {"field": "cagr_pct", "label": "研究年化（%）"},
                            {"field": "max_drawdown_pct", "label": "每日最大回撤（%）"},
                            {"field": "portfolio_trades", "label": "组合模拟笔数"},
                            {"field": "mean_cash_pct", "label": "平均现金（%）"},
                            {"field": "ci_lo_pct", "label": "单股超额95%区间下限（%）"},
                            {"field": "ci_hi_pct", "label": "单股超额95%区间上限（%）"},
                            {"field": "top5_positive_pnl_share_pct", "label": "前5只占正贡献（%）"},
                        ],
                        "reportingPeriod": period,
                    }
                ],
            },
        ],
    }
    case = pd.read_csv(OUT / "zjx_2025_case.csv")[
        ["code", "model", "signals_in_2025", "portfolio_entries"]
    ]
    sources["items"].append(
        {
            "id": "known-zjx-case",
            "title": "2025年中际旭创：两套规则的已知案例核对",
            "queries": [
                {
                    "id": "known-case-signals",
                    "source": {
                        "label": "历史信号与组合事件",
                        "files": ["output/dual_research_20261002_v1/zjx_2025_case.csv"],
                        "caveats": ["该股由用户事先指出，不是独立验证，也未用于选参数。"],
                    },
                    "rows": case.to_dict("records"),
                    "columns": [
                        {"field": "code", "label": "代码"},
                        {"field": "model", "label": "模型"},
                        {"field": "signals_in_2025", "label": "非重叠可成交信号数"},
                        {"field": "portfolio_entries", "label": "组合实际进入数"},
                    ],
                    "reportingPeriod": "信号日为2025年；仅用户指出的300308.SZ",
                }
            ],
        }
    )
    (VIS / "dual-research-sources.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "items": sources["items"][:1]},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    (VIS / "dual-research-result-sources.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "items": sources["items"][1:]},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    print(f"Projected {len(plot)} reviewed curve points; native rendering is a separate step.")


if __name__ == "__main__":
    main()
