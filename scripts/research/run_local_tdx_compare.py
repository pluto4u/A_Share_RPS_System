"""Offline, dated TDX reconstruction and unchanged A/B indicator comparison.

No production gate files are written. Local reconstruction is not an independent
vendor verification. Unsupported capital adjustments fail closed per security.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import gc
import hashlib
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pytdx.reader import GbbqReader

ROOT = PROJECT_ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tmp"))
from rps_analyzer.tdx_reader import _is_a_share, read_day_file  # noqa: E402
from screen_bottom_ambush import add_indicators as indicators_b  # noqa: E402
from screen_siruipu_right_side import load_name_master  # noqa: E402
from screen_siruipu_right_side_v2 import add_indicators as indicators_a  # noqa: E402

PRICE = ["open", "high", "low", "close"]
EVENT_FIELDS = [
    "hongli_panqianliutong",
    "peigujia_qianzongguben",
    "songgu_qianzongguben",
    "peigu_houzongguben",
]


def preflight(tdx_root: Path, dates: list[Any], out: Path) -> bool:
    """Read actual date records, not filesystem timestamps, before any indicators."""
    rows = []
    for market in ("sh", "sz"):
        for p in sorted((tdx_root / "vipdoc" / market / "lday").glob("*.day")):
            if not _is_a_share(p.name, market):
                continue
            raw = p.read_bytes()
            if not raw or len(raw) % 32:
                raise ValueError(f"empty or truncated day file: {p}")
            record_dates = np.ndarray(
                shape=(len(raw) // 32,), dtype="<u4", buffer=raw, strides=(32,)
            )
            rows.append(
                {
                    "ts_code": f"{p.name[2:8]}.{market.upper()}",
                    "path": str(p),
                    "latest": int(record_dates[-1]),
                    **{
                        f"has_{d:%Y%m%d}": bool(int(d.strftime("%Y%m%d")) in record_dates)
                        for d in dates
                    },
                }
            )
    inventory = pd.DataFrame(rows)
    if inventory.empty:
        raise ValueError("No A-share .day files found")
    inventory.to_csv(out / "data_inventory.csv", index=False, encoding="utf-8-sig")
    counts = {str(d.date()): int(inventory[f"has_{d:%Y%m%d}"].sum()) for d in dates}
    passed = all(n / len(rows) >= 0.80 for n in counts.values())
    audit = {
        "status": "ready" if passed else "blocked_missing_target_bars",
        "tdx_root": str(tdx_root),
        "A_share_files": len(rows),
        "date_coverage_counts": counts,
        "latest_date_distribution": {
            str(k): int(v) for k, v in inventory.latest.value_counts().items()
        },
        "generated_at": datetime.now().isoformat(),
        "candidate_counts": None,
        "reason": None if passed else "目标日K线覆盖率不足80%，未计算选股",
    }
    (out / "preflight.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=True), flush=True)
    if not passed:
        doc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>选股数据检查</title>'
        doc += "<style>body{font:17px/1.8 system-ui;max-width:1000px;margin:50px auto;padding:20px}"
        doc += "table{border-collapse:collapse}td,th{padding:10px;border:1px solid #ddd}</style>"
        doc += "<h1>0924 与 0923 右侧选股：缺少目标日日线</h1>"
        doc += "<p>本次计算已被数据检查阻断。没有生成候选，不能将缺数据解释为零命中。</p>"
        doc += "<p>行情目录：" + html.escape(str(tdx_root / "vipdoc")) + "</p>"
        doc += pd.DataFrame(
            list(counts.items()), columns=["日期", "包含当日日线的A股文件数"]
        ).to_html(index=False)
        doc += "<h2>本地日线截止日分布</h2>"
        doc += (
            inventory.latest.value_counts()
            .rename_axis("截止日")
            .reset_index(name="文件数")
            .to_html(index=False)
        )
        doc += "<p>请在通达信“盘后数据下载”中补齐沪深A股日线至2026-09-24（包含缺失历史区间），"
        doc += "并更新除权除息资料，再执行本地脚本。</p>"
        doc += '<p><a href="data_inventory.csv">逐股数据清单</a> · '
        doc += '<a href="preflight.json">检查记录</a></p></html>'
        (out / "report.html").write_text(doc, encoding="utf-8")
    return passed


def adjust(raw: Any, events: Any, target: Any, bars: int | None = 800) -> tuple[Any, int]:
    """Ratio forward adjustment; only events known by the requested date.

    Ex-right reference=(previous close*10-dividend+rights price*rights shares)
    / (10+bonus shares+rights shares). All event amounts are per ten shares.
    Multiple events during suspension are applied in chronological order.
    """
    d = raw.loc[raw.trade_date <= target].sort_values("trade_date").copy()
    if bars is not None:
        d = d.tail(bars).copy()
    if d.empty:
        raise ValueError("no dated bars")
    if d.trade_date.duplicated().any() or not np.isfinite(d[PRICE]).all().all():
        raise ValueError("duplicate dates or nonfinite prices")
    if (d[PRICE] <= 0).any().any() or (d.high < d[PRICE].max(axis=1)).any():
        raise ValueError("invalid OHLC")
    if (d.low > d[PRICE].min(axis=1)).any():
        raise ValueError("invalid low")
    ev = events.loc[(events.date > d.trade_date.iloc[0]) & (events.date <= target)]
    # Types 2..10 are capital/share-count changes. Special adjustment types
    # 11..14 and unknown type 15 are conservatively excluded, never guessed.
    if (~ev.category.isin(range(1, 11))).any():
        raise ValueError("unsupported adjustment category")
    ev = ev.loc[ev.category == 1].sort_values("date")
    if ev.date.duplicated().any():
        raise ValueError("duplicate corporate action date")
    factors = np.ones(len(d), dtype=float)
    dates = d.trade_date.to_numpy()
    last_pos = -1
    ref = 0.0
    for _, e in ev.iterrows():
        pos = int(dates.searchsorted(e.date.to_datetime64()))
        if pos == 0 or pos >= len(d):
            raise ValueError("corporate action without a following bar")
        cash, rights_price, bonus, rights = [float(e[x]) for x in EVENT_FIELDS]
        if not np.isfinite([cash, rights_price, bonus, rights]).all():
            raise ValueError("missing corporate action values")
        if min(cash, rights_price, bonus, rights) < 0:
            raise ValueError("negative corporate action values")
        if pos != last_pos:
            ref = float(d.close.iloc[pos - 1])
        next_ref = (ref * 10 - cash + rights_price * rights) / (10 + bonus + rights)
        if next_ref <= 0 or not np.isfinite(next_ref):
            raise ValueError("invalid adjustment reference")
        factors[:pos] *= next_ref / ref
        ref, last_pos = next_ref, pos
    d[PRICE] = d[PRICE].mul(factors, axis=0)
    if not np.isfinite(d[PRICE]).all().all() or (d[PRICE] <= 0).any().any():
        raise ValueError("invalid adjusted prices")
    if not np.allclose(d[PRICE].iloc[-1], raw.loc[raw.trade_date <= target, PRICE].iloc[-1]):
        raise ValueError("latest raw price anchor mismatch")
    return d, len(ev)


def select_day(frame: Any, target: Any, names: dict[str, str]) -> tuple[Any, Any]:
    """Reuse the package's indicator functions and daily A/B filters."""
    a_rows, b_rows = [], []
    for number, (code, g) in enumerate(frame.groupby("ts_code", sort=False, observed=True)):
        if number % 500 == 0:
            print(f"{target.date()}: indicators {number} symbols", flush=True)
        if g.trade_date.max() != target or len(g) <= 250:
            continue
        name = names.get(code, "")
        if not name or "ST" in name.replace(" ", "").upper() or "退" in name:
            continue
        a = indicators_a(g.tail(500)).iloc[-1]
        b = indicators_b(g.tail(650)).iloc[-1]
        base = {
            "ts_code": code,
            "name": name,
            "as_of": str(target.date()),
            "close": a.close,
            "RPS_50": a.RPS_50,
            "RPS_120": a.RPS_120,
            "RPS_250": a.RPS_250,
        }
        if (a.YJ or a.QD) and a.ADV20 >= 1.5e8 and a.amount > 0:
            a_rows.append(
                {
                    **base,
                    "stage": "DISCOVERY" if a.YJ else "UPGRADE",
                    "PRIORITY": bool(a.YJ and a.RPS_120 >= 60),
                    "rps120_ge80": bool(a.RPS_120 >= 80),
                    "vs_ma20_pct": (a.close / a.M20 - 1) * 100,
                    "vol_ratio": a.vol / a.VM20,
                    "adv20_yi": a.ADV20 / 1e8,
                }
            )
        if b.AMBUSH and b.ADV20 >= 1.5e8 and b.amount > 0:
            b_rows.append(
                {
                    **base,
                    "stage": "AMBUSH",
                    "PRIORITY": False,
                    "rps120_ge80": bool(b.RPS_120 >= 80),
                    "vs_ma20_pct": b.vs_ma20_pct,
                    "vol_ratio": b.vol_ratio,
                    "adv20_yi": b.ADV20 / 1e8,
                }
            )
    cols = [
        "ts_code",
        "name",
        "as_of",
        "stage",
        "close",
        "RPS_50",
        "RPS_120",
        "RPS_250",
        "PRIORITY",
        "rps120_ge80",
        "vs_ma20_pct",
        "vol_ratio",
        "adv20_yi",
    ]
    return tuple(
        pd.DataFrame(rows, columns=cols).sort_values(
            ["rps120_ge80", "PRIORITY", "RPS_120"], ascending=False
        )
        for rows in (a_rows, b_rows)
    )


def compare(previous: Any, current: Any) -> Any:
    joined = previous.merge(
        current, on="ts_code", how="outer", suffixes=("_previous", "_current"), indicator=True
    )
    joined["change"] = (
        joined["_merge"]
        .astype(str)
        .map({"left_only": "退出", "right_only": "新增", "both": "持续"})
    )
    joined["name"] = joined.name_current.combine_first(joined.name_previous)
    joined["rps120_delta"] = joined.RPS_120_current - joined.RPS_120_previous
    return joined.drop(columns="_merge")


def rps_inplace(frame: Any) -> None:
    """Same dated percentile ranks as calculate_rps without full-frame copies."""
    frame.sort_values(["trade_date", "ts_code"], inplace=True, ignore_index=True)
    for period in (50, 120, 250):
        previous = frame.groupby("ts_code", observed=True).close.shift(period)
        roc = (frame.close - previous) / previous
        frame[f"RPS_{period}"] = roc.groupby(frame.trade_date).rank(pct=True) * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--previous", default="2026-09-23")
    ap.add_argument("--date", default="2026-09-24")
    args = ap.parse_args()
    dates = [pd.Timestamp(args.previous), pd.Timestamp(args.date)]
    if dates[0] >= dates[1]:
        raise ValueError("previous must precede date")
    out = ROOT / "output" / f"local_tdx_{dates[0]:%Y%m%d}_{dates[1]:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    if not preflight(args.tdx_root, dates, out):
        print(f"BLOCKED REPORT: {out / 'report.html'}", flush=True)
        raise SystemExit(2)
    gbbq = args.tdx_root / "T0002/hq_cache/gbbq"
    print("Reading local corporate actions", flush=True)
    events = GbbqReader().get_df(str(gbbq))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]
    names = load_name_master(args.tdx_root / "T0002/hq_cache")
    if not names:
        raise ValueError("missing local name master")
    print("Reading local daily files", flush=True)
    raw_data: dict[str, Any] = {}
    hashes = hashlib.sha256()
    for market in ("sh", "sz"):
        for p in sorted((args.tdx_root / "vipdoc" / market / "lday").glob("*.day")):
            if not _is_a_share(p.name, market):
                continue
            if p.stat().st_size % 32:
                raise ValueError(f"truncated daily file: {p}")
            d = read_day_file(str(p))
            if d is not None:
                code = f"{p.name[2:8]}.{market.upper()}"
                d = d.loc[d.trade_date <= dates[-1]].tail(810).copy()
                if not d.empty:
                    raw_data[code] = d
                    hashes.update(code.encode())
                    hashes.update(pd.util.hash_pandas_object(d, index=False).values.tobytes())
    if not raw_data:
        raise ValueError("no A-share daily data")
    audits, results = [], []
    for target in dates:
        parts, excluded, event_count = [], [], 0
        for code, raw in raw_data.items():
            try:
                d, n = adjust(raw, grouped.get(code, empty), target)
                d["ts_code"] = code
                parts.append(d)
                event_count += n
            except ValueError as exc:
                excluded.append({"ts_code": code, "reason": str(exc)})
        full = pd.concat(parts, ignore_index=True)
        del parts, d
        full["ts_code"] = full.ts_code.astype("category")
        gc.collect()
        latest_count = int(full.loc[full.trade_date == target, "ts_code"].nunique())
        coverage = latest_count / len(raw_data)
        if coverage < 0.80:
            raise ValueError(f"{target.date()}: target-date coverage {coverage:.1%} < 80%")
        print(f"{target.date()}: {latest_count} symbols, RPS and A/B indicators", flush=True)
        rps_inplace(full)
        gc.collect()
        a, b = select_day(full, target, names)
        for label, table in (("A", a), ("B", b)):
            table.to_csv(out / f"{target:%Y%m%d}_{label}.csv", index=False, encoding="utf-8-sig")
        audit = {
            "as_of": str(target.date()),
            "target_date_symbols": latest_count,
            "coverage": coverage,
            "rps_universe_symbols": int(full.ts_code.nunique()),
            "events_applied": event_count,
            "excluded": excluded,
            "max_bar_date": str(full.trade_date.max().date()),
            "A": len(a),
            "B": len(b),
            "A_rps120_ge80": int(a.rps120_ge80.sum()),
            "adjustment_status": "local_reconstructed_checks_passed",
            "independent_vendor_verified": False,
        }
        audits.append(audit)
        results.append((a, b))
        print(json.dumps(audit, ensure_ascii=True), flush=True)
        del full
        gc.collect()
    comparisons = [compare(results[0][i], results[1][i]) for i in (0, 1)]
    for label, table in zip(("A", "B"), comparisons, strict=True):
        table.to_csv(out / f"comparison_{label}.csv", index=False, encoding="utf-8-sig")
    audit_doc = {
        "generated_at": datetime.now().isoformat(),
        "tdx_root": str(args.tdx_root),
        "gbbq_sha256": hashlib.sha256(gbbq.read_bytes()).hexdigest(),
        "dated_daily_sha256": hashes.hexdigest(),
        "dates": audits,
        "changes": {
            label: table.change.value_counts().to_dict()
            for label, table in zip(("A", "B"), comparisons, strict=True)
        },
        "limits": [
            "本地等比前复权重建，未与独立行情源或客户端前复权导出对账",
            "按目标日截断K线和除权事件；名称及证券范围使用当前缓存，非历史时点快照",
            "复用A/B原指标；完整展示命中，不截取日报前30；未运行G1及G2至G5",
            "仅研究候选；退出表示不再满足筛选条件；不生成交易指令",
        ],
    }
    (out / "audit.json").write_text(
        json.dumps(audit_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sections = []
    for label, table in zip(("A", "B"), comparisons, strict=True):
        cols = [
            "change",
            "ts_code",
            "name",
            "stage_previous",
            "stage_current",
            "close_previous",
            "close_current",
            "RPS_120_previous",
            "RPS_120_current",
            "rps120_delta",
            "vol_ratio_current",
            "vs_ma20_pct_current",
        ]
        sections.append(
            f"<h2>策略{label} · 两日变化</h2>"
            + table[cols].to_html(
                index=False, border=0, float_format=lambda x: f"{x:.2f}", na_rep="—"
            )
        )
    for idx, target in enumerate(dates):
        for label, table in zip(("A", "B"), results[idx], strict=True):
            sections.append(
                f"<h2>{target.date()} · 策略{label}完整候选 ({len(table)})</h2>"
                + table.to_html(index=False, border=0, float_format=lambda x: f"{x:.2f}")
            )
    report = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>通达信右侧选股对比</title>
<style>body{font:15px/1.7 system-ui,'Microsoft YaHei';background:#f3f5f8;color:#172338;margin:32px}
main{max-width:1600px;margin:auto}h1{font-size:28px}h2{margin-top:36px}table{border-collapse:collapse;
background:white;width:100%;font-size:13px;display:block;overflow:auto}
th,td{padding:10px;border-bottom:1px solid #ddd;
white-space:nowrap;text-align:left}th{background:#e5edf5}input{padding:12px;width:320px}
.card{background:white;padding:20px;border-radius:12px}a{color:#176e9e}</style><main>
"""
        + f"<h1>通达信右侧选股 · {dates[1].date()} 对比 {dates[0].date()}</h1>"
    )
    report += (
        '<div class="card"><p>策略A：右侧启动；策略B：缩量回踩。来源：D盘通达信日线及除权资料。</p>'
    )
    report += "<p>" + "<br>".join(html.escape(x) for x in audit_doc["limits"]) + "</p>"
    report += (
        "<p>"
        + html.escape(str(audit_doc["changes"]))
        + '</p><a href="audit.json">数据审计</a></div>'
    )
    report += '<p><input id="search" placeholder="搜索股票代码、名称、阶段或新增/持续/退出"></p>'
    report += "".join(sections)
    report += """<script>document.querySelector('#search').addEventListener('input',e=>{
const q=e.target.value.toLowerCase();
document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))
});</script></main></html>"""
    (out / "report.html").write_text(report, encoding="utf-8")
    print(f"REPORT: {out / 'report.html'}", flush=True)


if __name__ == "__main__":
    main()
