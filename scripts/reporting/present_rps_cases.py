"""Source-backed case explanations, not performance optimisation."""

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
OUT = ROOT / "output/case_rps_review_202606_202609_v1"
VIS = Path("C:/Users/pluto4u/.codex/visualizations/2026/06/18/019edb65-8cac-7f32-be6b-481f9d1d8c57")


def save(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def main() -> None:
    audits = pd.read_csv(OUT / "independent_checks.csv")
    if not audits.passed.all():
        raise ValueError("audit not passed")
    daily = pd.read_csv(OUT / "daily.csv", float_precision="round_trip")
    latest = pd.read_csv(OUT / "latest.csv", float_precision="round_trip")
    episodes = pd.read_csv(
        OUT / "ma20_episodes.csv", na_values=["缺"], float_precision="round_trip"
    )
    basis = pd.read_csv(OUT / "return_basis.csv", float_precision="round_trip")
    started_in_window = episodes.loc[episodes.break_date >= "2026-06-01"]
    largest = started_in_window.loc[
        started_in_window.groupby("code").review_peak_to_trough_pct.idxmin()
    ].copy()
    largest.to_csv(
        OUT / "largest_review_episode.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    gate = latest[["code", "name", "date", "RPS120", "RPS250", "below_ma20"]].copy()
    gate["cached_RPS120_plus_RPS250"] = gate.RPS120 + gate.RPS250
    gate["user_provided_reference"] = 185
    gate["passes_only_user_sum_clause"] = gate.cached_RPS120_plus_RPS250 > 185
    gate["not_actual_EXTDATA_or_full_formula_replication"] = True
    gate.to_csv(OUT / "provided_formula_rps_gate.csv", index=False, encoding="utf-8-sig")
    source_files = ["output/case_rps_review_202606_202609_v1/daily.csv"]
    for code, title, basename in (
        ("688498.SH", "源杰科技：2026年6—9月全市场RPS", "yuanjie-rps-case"),
        ("301080.SZ", "百普赛斯：2026年6—9月全市场RPS", "baipu-rps-case"),
    ):
        subset = daily.loc[daily.code == code]
        rows = [
            {"date": r.date, "period": f"RPS{p}", "rps": float(getattr(r, f"RPS{p}"))}
            for r in subset.itertuples(index=False)
            for p in (50, 120, 250)
        ]
        source = {
            "label": "本地通达信缓存的全市场RPS",
            "files": source_files + ["output/trend_start_cache_20260930/rps.npz"],
            "filters": [f"股票代码：{code}", "仅记录有交易的股票日"],
            "metricDefinitions": [
                {
                    "label": "全市场RPS",
                    "definition": "各周期区间涨幅按当日全市场有效股票排名，未在六只案例中重排。",
                }
            ],
            "caveats": [
                "这些是用户事后提供的2026案例，不是新独立样本外。",
                "本图周期按缓存字段标注，未假定截图线条颜色对应哪个周期。",
            ],
        }
        save(
            VIS / f"{basename}.json",
            {
                "schemaVersion": 1,
                "id": basename,
                "title": title,
                "chart": {
                    "type": "line",
                    "x": "date",
                    "y": "rps",
                    "series": "period",
                    "showXAxisLabel": False,
                    "yLabel": "全市场百分位（0—100）",
                    "startAtZero": True,
                },
                "rows": rows,
                "source": source,
                "theme": "codex-classic",
            },
        )
        save(
            VIS / f"{basename}-sources.json",
            {
                "schemaVersion": 1,
                "items": [
                    {
                        "id": basename,
                        "title": title,
                        "queries": [
                            {
                                "id": f"{basename}-daily",
                                "source": source,
                                "rows": rows,
                                "columns": ["date", "period", "rps"],
                                "reportingPeriod": "2026-06-01至2026-09-30",
                            }
                        ],
                    }
                ],
            },
        )
    findings = []
    selections = (
        (
            "return-basis",
            "短期RPS与价格修复可能不同步",
            "return_basis.csv",
            basis.loc[(basis.period == 50) & (basis.date == "2026-08-31")],
            [
                "code",
                "name",
                "date",
                "close",
                "comparison_date",
                "comparison_close",
                "interval_return_pct",
                "RPS",
            ],
            ["RPS50比较50根股票交易K线前的价格，不是自本次谷底的涨幅。"],
        ),
        (
            "price-rps-divergence",
            "长期RPS高不等于没有价格回撤",
            "largest_review_episode.csv",
            largest,
            [
                "code",
                "name",
                "break_date",
                "review_trough_date",
                "review_peak_to_trough_pct",
                "RPS120_at_price_trough",
                "RPS250_at_price_trough",
            ],
            [
                "价格跌幅以跌破MA20前20根最高收盘为参照，不是20日最大回撤指标。",
                "每只选期间跌幅最大的已发生MA20跌破段，含事后谷底，只用于解释案例。",
            ],
        ),
        (
            "frozen-entry-coverage",
            "原入场条件没有覆盖所有图形",
            "latest.csv",
            latest,
            ["code", "name", "date", "original_A_signal_count", "original_B_signal_count"],
            ["仅原A/B入场条件的信号数，不是实际组合成交，不代表收益。"],
        ),
        (
            "user-rps-gate",
            "用户原185条款的缓存口径核对",
            "provided_formula_rps_gate.csv",
            gate,
            [
                "code",
                "name",
                "date",
                "cached_RPS120_plus_RPS250",
                "user_provided_reference",
                "passes_only_user_sum_clause",
                "below_ma20",
            ],
            [
                "185来自用户先前提供的公式，不是本轮选出的参数。",
                "仅以本地缓存核对一个条款，不是实际通达信EXTDATA或完整公式复现。",
            ],
        ),
    )
    for identity, title, file, frame, columns, caveats in selections:
        records = frame[columns].to_dict(orient="records")
        findings.append(
            {
                "id": identity,
                "title": title,
                "queries": [
                    {
                        "id": identity,
                        "source": {
                            "label": title,
                            "files": [f"output/case_rps_review_202606_202609_v1/{file}"],
                            "caveats": caveats + ["没有据2026案例改参数或重跑退出方案。"],
                        },
                        "rows": records,
                        "columns": columns,
                        "reportingPeriod": "2026年6月至9月底案例",
                    }
                ],
            }
        )
    save(VIS / "six-case-rps-findings-sources.json", {"schemaVersion": 1, "items": findings})
    # Preserve provided labels and evidence conflicts instead of silently relabelling images.
    image_checks = [
        {
            "image": 1,
            "provided_name": "思瑞浦",
            "code_candidate": "688536.SH",
            "screen_high": 375.76,
            "screen_ma20": 323.34,
            "mapping_status": "consistent",
        },
        {
            "image": 2,
            "provided_name": "金百泽",
            "code_candidate": "301041.SZ",
            "screen_high": 41.20,
            "screen_ma20": 33.80,
            "mapping_status": "consistent",
        },
        {
            "image": 3,
            "provided_name": "源捷科技",
            "code_candidate": "688498.SH",
            "screen_high": 1967.14,
            "screen_ma20": 1680.60,
            "mapping_status": "name_confirmation_pending",
        },
        {
            "image": 4,
            "provided_name": "长盈通",
            "code_candidate": "688143.SH",
            "screen_high": 254.95,
            "screen_ma20": 183.77,
            "mapping_status": "small_price_difference",
        },
        {
            "image": 5,
            "provided_name": "义翘神州",
            "code_candidate": "301080.SZ",
            "screen_high": 141.07,
            "screen_ma20": None,
            "mapping_status": "order_confirmation_pending",
        },
        {
            "image": 6,
            "provided_name": "百普赛斯",
            "code_candidate": "301047.SZ",
            "screen_high": 165.00,
            "screen_ma20": 113.93,
            "mapping_status": "order_confirmation_pending",
        },
    ]
    for row in image_checks:
        point = latest.loc[latest.code == row["code_candidate"]].iloc[0]
        row["cached_name"] = point["name"]
        row["cached_period_high"] = float(point.review_max_high)
        row["high_difference"] = float(point.review_max_high - row["screen_high"])
        row["cached_ma20"] = float(point.MA20)
        row["ma20_difference"] = (
            float(point.MA20 - row["screen_ma20"]) if row["screen_ma20"] is not None else None
        )
    pd.DataFrame(image_checks).to_csv(
        OUT / "screenshot_reconciliation.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    lines = [
        "# 六图RPS案例核对：2026年6月至9月底",
        "",
        "结论：RPS描述指定区间的相对表现，不保证当前上涨、低回撤，也不保证先于价格修复。",
        "原冻结A/B代码、入场、退出和参数不变。本轮是事后案例解释，不是策略收益验证。",
        "",
        "## 9月30日缓存快照",
        "",
        "| 股票 | 收盘 | MA20 | RPS50 | RPS120 | RPS250 | 收盘相对MA20% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in latest.itertuples(index=False):
        lines.append(
            f"| {r.name} {r.code} | {r.close:.2f} | {r.MA20:.2f} | {r.RPS50:.2f} | "
            f"{r.RPS120:.2f} | {r.RPS250:.2f} | {r.price_vs_ma20_pct:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 必须修正的认识",
            "",
            "1. 思瑞浦等修复期间，RPS50可因比较六月高价格而偏低，不能硬要求先上升。",
            "2. 源杰/长盈通的长期RPS在很深的价格回撤中依然高，不能拿高RPS覆盖结构破坏。",
            "3. 百普/义翘较早阶段RPS50强于长期周期，但不是所有启动都遵循同一周期顺序。",
            "4. 金百泽全周期先弱后恢复，与一直保持长期强势的股票不属于同一种背景。",
            "",
            "## 主要MA20跌破段（事后描述）",
            "",
            "只选6月1日后开始的段；价格跌幅相对跌破前20根最高收盘。",
            "这不是20日最大回撤，也不是实际持仓回撤或建议承受的止损。",
            "",
            "| 股票 | 跌破日 | 谷底日 | 价格跌幅% | 谷底RPS120 | 谷底RPS250 |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for r in largest.itertuples(index=False):
        lines.append(
            f"| {r.name} | {r.break_date} | {r.review_trough_date} | "
            f"{r.review_peak_to_trough_pct:.2f} | {r.RPS120_at_price_trough:.2f} | "
            f"{r.RPS250_at_price_trough:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 原规则覆盖与用户公式",
            "",
            "原A在这六只窗口中均无确认信号；原B只有思瑞浦2次，其余无信号。",
            "它们只是条件计数，不是新的组合成交与收益回测。",
            "因此漏掉图形不能全部归因于RPS；固定横盘长度、放量后短窗口回踩和近MA20条件也不覆盖深整理。",
            "另用用户原‘RPS120+RPS250>185’条款，按缓存值做单条款核对，",
            "金百泽9月底不通过；源杰/长盈通通过，但价格仍破MA20。",
            "不能把这条核对说成实际EXTDATA或完整通达信公式复现。",
            "",
            "## 下一轮仅为样本内假设",
            "",
            "将首次启动、深回撤修复、强势整理后二次启动分开定义背景；RPS作为背景与强度证据。",
            "下一轮退出单因素实验仍应保留原入场，不能因2026六张赢家图改具体参数。",
            "若新增二次启动形态，须单独登记为新假设；不替换原四段式，不宣称已经盈利。",
            "",
            "## 截图对应及差异",
            "",
            "图5高点141.07匹配百普赛斯，图6高点165.00匹配义翘神州；原名单反序，待用户确认。",
            "第三只源捷科技与本地源杰科技名称不一致，保留待确认。",
            "源杰、长盈通截图高点与缓存有细微差异，图6的MA20也有差别，未猜测原因。",
            "副图颜色/周期图例没有完整保留；数值只来自缓存字段，不按颜色估算。",
            "",
            "## 文件与核对",
            "",
            "daily.csv为完整窗口价格/量/均线/RPS；month_end.csv为实际股票月末快照；",
            "ma20_episodes.csv和return_basis.csv为可追溯的段落与区间比较基点。",
            f"独立核对{len(audits)}项通过，六股全部窗口日值已核对；全市场排名只核对9月30日的三周期。",
            "单测43项通过，定向ruff/mypy通过；并非全仓绿。",
            "Jupyter依赖此前确认缺失，提供可复跑Python/CSV；未宣称执行notebook。",
            "目录非Git仓库，未初始化或创建PR。",
            "",
        ]
    )
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(OUT / "README.md", flush=True)
    print(
        largest[
            [
                "name",
                "review_peak_to_trough_pct",
                "RPS120_at_price_trough",
                "RPS250_at_price_trough",
            ]
        ].to_string(index=False),
        flush=True,
    )
    print(gate.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
