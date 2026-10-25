"""Package frozen evidence and the user's new scope; no new strategy calculation."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_decision_20261006"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    verified = {}
    for study, folder in [("rps_diag", "rps_diag_20261006/v1"), ("rps_repair", "rps_repair_20261006/v1")]:
        manifest = json.loads((ROOT / "output" / folder / "final_manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            if sha(ROOT / name) != expected:
                raise RuntimeError(f"Frozen source changed: {name}")
        verified[study] = len(manifest["files"])
    data = pd.read_csv(ROOT / "output/rps_diag_20261006/v1/reviewed_contrasts.csv")
    coverage = pd.read_csv(ROOT / "output/rps_diag_20261006/v1/matching_coverage.csv")
    data = data[(data.horizon == 20) & (data.comparison == "matched_difference")]
    data = data.merge(coverage[coverage.horizon == 20], on=["period", "candidate", "baseline", "horizon"], validate="one_to_one")
    names = {"rps_pv":"RPS＋量价 vs 仅RPS", "low_turn":"低位转强 vs 仅低位", "oversold_confirm":"超跌确认 vs 仅超跌"}
    order = {"rps_pv":0, "low_turn":1, "oversold_confirm":2}
    data["order"] = data.candidate.map(order)
    data = data.sort_values(["order", "period"])
    rows = [dict(signal=names[r.candidate], period="2018—2021" if r.period == "early" else "2022—2026.09",
        mean_pp=float(r.mean_pp), lower_pp=float(r.lo_pp), upper_pp=float(r.hi_pp),
        estimate=f"{r.mean_pp:+.3f}", interval=f"[{r.lo_pp:+.3f}, {r.hi_pp:+.3f}]",
        coverage_pct=float(r.valid_match_coverage_pct), coverage=f"{r.valid_match_coverage_pct:.1f}%",
        months=int(r.months), days=int(r.paired_days)) for r in data.itertuples()]
    quality = json.loads((ROOT / "output/rps_repair_20261006/v1/source_quality.json").read_text(encoding="utf-8"))
    scope_rows = [dict(item="终止上市股票专项研究", decision="停止", boundary="不再补取、恢复或对账；已有证据归档保留"),
        dict(item="后续研究股票池", decision="限定", boundary="当前项目存续沪深A股池；历史结果仅对该样本成立"),
        dict(item="存续股量额异常", decision="优先排查", boundary="定位来源与单位；不以固定倍数猜补"),
        dict(item="既有策略参数", decision="继续冻结", boundary="固定规则复核失败则归档，不追加调参搜索"),
        dict(item="实盘资格", decision="未通过", boundary="历史结果与范围调整均不构成盈利承诺或下单授权")]
    snapshot = dict(surface="report", title="把RPS研究收缩为一次有退出条件的验证",
        generatedAt=datetime.now(timezone.utc).isoformat(), status="reviewed", buildStatus="creating", filters=[],
        report=dict(asOf="2026-09-30"), queries={
            "frozen_results":dict(rows=rows, source=dict(label="RPS冻结规则的匹配诊断",
                files=[{"label":"reviewed_contrasts.csv"},{"label":"matching_coverage.csv"},{"label":"protocol.json（原实验）"}],
                filters=["样本：当前项目存续沪深A股池", "预测期：信号后20个市场日"],
                caveats=["结果来自已见历史，不是独立样本外或账户净收益。当前样本保留幸存者选择，不能推广到全市场历史。",
                    "区间为每段3个主比较校正后的98.3333%重抽样区间；量额输入异常影响尚未量化。",
                    "超跌确认匹配覆盖仅约13%/38%，不能把子样本结论推广到全部超跌事件。"],
                metricDefinitions=[dict(label="匹配增量", definition="先计算同日候选减去历史波动率/成交额代理匹配对照的20日价格变化，再取月均差；单位为百分点，不是年化或净账户收益。",
                    componentIds=["frozen-evidence-table","results-interpretation","executive-summary"])])),
            "scope_decision":dict(rows=scope_rows, source=dict(label="用户确认的研究范围与退出条件",
                files=[{"label":"RESEARCH_SCOPE.md"}],
                caveats=["范围调整未重新计算收益，也未删除原研究文件。",
                    "未来模拟中，后来终止上市的持仓不能被事后删除或豁免损失。"],
                evidenceFlow=[dict(kind="source", title="范围决定", detail="用户于2026-10-06明确表示：终止上市的股票不再研究。")],
                metricDefinitions=[dict(label="后续范围", definition="停止终止上市股票专题研究与补数，后续沿用当前项目存续沪深A股池；限定样本结论不作为全市场无偏回测。",
                    componentIds=["scope-table","scope-explanation","next-gates","executive-summary"])])),
            "input_quality":dict(rows=[dict(control_codes=20, large_difference_cells=quality["large_liquidity_disagreement_cells"],
                affected_code_dates=quality["large_disagreement_code_dates"], affected_codes=quality["large_disagreement_codes"],
                dates="2025-11-27、2025-12-01", frozen_reproduced_cells=quality["frozen_input_reproduces_raw_disagreements"])],
                source=dict(label="存续股量价输入核验", files=[{"label":"source_quality.json"},
                    {"label":"control_vendor_comparison_by_code.csv"},{"label":"frozen_input_liquidity_disagreements.csv"}],
                    caveats=["20只对照是固定代码排序交集，不是全市场随机样本。",
                        "差异在冻结输入中复现，但尚未定位供应商或入库环节责任，也未重算对策略的影响。"],
                    metricDefinitions=[dict(label="大幅量额差异", definition="两来源成交量或成交额比值落在[0.5,2]之外的单元格数，是描述性异常标记，不是新选股阈值。",
                        componentIds=["input-quality-section","executive-summary"])]))})
    (OUT / "reviewed_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "source_receipt.json").write_text(json.dumps(dict(verified_frozen_files=verified,
        prepared_at_utc=snapshot["generatedAt"], scope_sha256=sha(HERE / "RESEARCH_SCOPE.md"),
        strategy_recomputed=False, delisted_research_stopped=True), indent=2), encoding="utf-8")
    print(json.dumps(dict(snapshot=str(OUT / "reviewed_snapshot.json"), queries=3, result_rows=len(rows), verified=verified)), flush=True)


if __name__ == "__main__":
    main()
