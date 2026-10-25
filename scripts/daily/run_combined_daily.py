"""Combine full-universe daily RPS with V2 breakout morphology, without orders.

Input is one verified run_daily_rps snapshot. Never merge old external RPS ranks.
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
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from rps_analyzer.right_confirmation_v2 import LayeredConfig, evaluate, shape_series
from run_daily_rps import Settings, daily_features, rank_then_filter, read_day

ROOT = PROJECT_ROOT


class CombinedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    recent_event_bars: int = Field(default=5, ge=1, le=20)
    ma20_band: float = Field(default=0.02, ge=0, le=0.05)
    invalid_below_ma20_bars: int = Field(default=2, ge=1, le=5)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_snapshot(source: Path) -> tuple[dict[str, Any], Any, Any]:
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    if audit["status"] != "OK" or audit["errors"] != 0:
        raise ValueError("source daily RPS run is blocked/incomplete")
    if audit["price_basis"] not in ("raw", "qfq"):
        raise ValueError("unknown source price basis")
    data = pd.read_csv(source / "all_valid.csv", na_values=["缺"])
    inventory = pd.read_csv(source / "inventory.csv", na_values=["缺"])
    if data.empty or data["代码"].duplicated().any():
        raise ValueError("empty or duplicate source universe")
    if len(data) != audit["valid_universe"] or not data["RPS分母"].eq(len(data)).all():
        raise ValueError("source denominator mismatch")
    if not data["数据日期"].eq(audit["as_of"]).all():
        raise ValueError("mixed source dates")
    if not data["价格口径"].eq(audit["price_basis"]).all():
        raise ValueError("mixed source price bases")
    ranked = rank_then_filter(data, Settings(**audit["parameters"])).set_index("代码")
    original = data.set_index("代码")
    for column in ("RPS50", "RPS120", "RPS250", "入选"):
        if not np.allclose(ranked.loc[original.index, column], original[column]):
            raise ValueError(f"source calculation mismatch: {column}")
    valid = inventory.loc[inventory.status.eq("VALID")]
    if valid["代码"].duplicated().any() or set(valid["代码"]) != set(data["代码"]):
        raise ValueError("source inventory/universe mismatch")
    root = Path(audit["root"])
    for filename, expected in audit["source_hashes"].items():
        if sha(root / "T0002/hq_cache" / filename) != expected:
            raise ValueError(f"source cache changed: {filename}; rerun run_daily_rps first")
    # Check all ranked securities, not merely the stronger subset.
    for item in valid.to_dict("records"):
        if sha(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"daily file changed: {item['代码']}; rerun run_daily_rps first")
    return audit, data, valid.set_index("代码")


def recent_event(d: Any, shapes: dict[str, Any], cfg: CombinedConfig) -> dict[str, Any]:
    """A shape event is not an assertion that historical RPS/turnover gates passed."""
    recent = np.flatnonzero(shapes["breakout"].tail(cfg.recent_event_bars).to_numpy())
    for local_pos in recent[::-1]:
        pos = len(d) - min(len(d), cfg.recent_event_bars) + int(local_pos)
        pivot = float(shapes["pivot"].iloc[pos])
        base_low = float(d.low.iloc[pos - 5 : pos].min())
        held = bool((d.close.iloc[pos:] >= pivot).all())
        if held:
            return {
                "近5日突破尚有效": True,
                "突破形态日期": str(d.trade_date.iloc[pos].date()),
                "距突破交易日": len(d) - 1 - pos,
                "冻结突破位": pivot,
                "冻结整理低点": base_low,
                "突破日量比": float(shapes["vr"].iloc[pos]),
                "突破日收盘位置": float(shapes["clv"].iloc[pos]),
                "突破前波动收缩比": float(shapes["compression"].iloc[pos]),
                "突破前缩量比": float(shapes["dry"].iloc[pos]),
            }
    return {
        "近5日突破尚有效": False,
        "突破形态日期": None,
        "距突破交易日": None,
        "冻结突破位": None,
        "冻结整理低点": None,
        "突破日量比": None,
        "突破日收盘位置": None,
        "突破前波动收缩比": None,
        "突破前缩量比": None,
    }


def assign_pool(strict_trend: bool, v2_pool: bool | None, event_alive: bool) -> str:
    if v2_pool is True and event_alive:
        return "趋势刚启动"
    return "趋势进行中" if strict_trend else "未入选"


def current_turnover(
    volume: float,
    floating: dict[str, Any] | None,
    events: Any,
    target: Any,
) -> tuple[float | None, str]:
    if not floating:
        return None, "流通股本缺失"
    if not np.isfinite(floating["shares"]) or floating["shares"] <= 0:
        raise ValueError("invalid floating shares")
    after_cache = events.loc[
        (events.date > pd.Timestamp(str(floating["record_date"]))) & (events.date <= target)
    ]
    # Category 1 can also change shares through bonus/rights, not only cash dividends.
    share_change = after_cache.category.between(2, 15) | (
        after_cache.category.eq(1)
        & ((after_cache.songgu_qianzongguben > 0) | (after_cache.peigu_houzongguben > 0))
    )
    if share_change.any():
        return None, "股本缓存日期后存在送配股/股本/特殊变更，换手率待核验"
    return volume / floating["shares"] * 100, ""


def daily_plan(d: Any, row: dict[str, Any], cfg: CombinedConfig) -> dict[str, Any]:
    """Per-stock, dated observation plans. No execution price or position advice."""
    c = d.close.reset_index(drop=True)
    ma20 = c.rolling(20).mean()
    m20 = float(ma20.iloc[-1])
    m60 = float(c.rolling(60).mean().iloc[-1])
    price = float(c.iloc[-1])
    # Solve x = (sum(last 19 closes)+x)/20; not tomorrow's guessed MA20.
    next_cross = float(c.tail(19).mean())
    lower, upper = m20, m20 * (1 + cfg.ma20_band)
    vprior = d.vol.shift(1).rolling(20).mean()
    touch = (d.low <= ma20 * (1 + cfg.ma20_band)) & (c >= ma20) & (d.vol <= vprior)
    recent_touch = bool(touch.tail(5).any())
    rebound = bool(touch.iloc[-6:-1].any() and c.iloc[-1] > d.high.iloc[-2] and price >= m20)
    below = bool((c < ma20).tail(cfg.invalid_below_ma20_bars).all())
    pocket = row.get("口袋低点")
    pocket = None if pocket is None or pd.isna(pocket) else float(pocket)
    atr = float(row["ATR20昨日"])
    extension = price / m20 - 1 > 0.12 or (atr > 0 and price - m20 > 3 * atr)
    prior_high = float(d.close.iloc[-251:-1].max())
    high = float(d.high.iloc[-1])
    vol_ceiling = float(d.vol.tail(20).mean())
    if row["分组"] == "趋势刚启动":
        pivot = float(row["冻结突破位"])
        floor = float(row["冻结整理低点"])
        plan = (
            f"复核本轮突破：冻结突破位{pivot:.2f}，其上收盘结构仍保留；"
            f"后续回落时比较量能是否低于此前20日均量，并核对收盘能否保持突破位及动态MA20。"
            "一次放量突破不等于持续主升。"
        )
        invalid = (
            f"后续任一日收盘<{pivot:.2f}，本轮突破形态失效；"
            f"收盘<{floor:.2f}则整理底部失守。"
            f"连续{cfg.invalid_below_ma20_bars}日收盘低于各自当日MA20则趋势观察假设失效。"
        )
        setup_invalid = price < pivot or price < floor
    else:
        plan = (
            f"以动态MA20为日线观察轴：本日回踩观察带{lower:.2f}—{upper:.2f}，"
            "需低点触及观察带、收盘仍在MA20之上，且回调量低于此前20日均量；"
            "之后收盘超过前一日最高价才记录日线转强，不把触及均线本身当作确认。"
        )
        invalid = (
            f"连续{cfg.invalid_below_ma20_bars}日收盘低于各自当日MA20，"
            "或任一日收盘低于动态MA60，趋势结构失效；"
            + (
                f"任一日收盘<{pocket:.2f}，本次回调结构失效。"
                if pocket is not None
                else "回调低点缺失，不能据此判断回调结构失效。"
            )
        )
        setup_invalid = pocket is not None and price < pocket
    warning = (
        "偏离MA20较大，仅记录趋势，不视作已经出现低风险回踩"
        if extension
        else "已出现回踩后日线转强（形态记录）"
        if rebound
        else "近5日出现缩量触及MA20区域，仍待后续日线确认"
        if recent_touch
        else "尚未出现近5日缩量回踩MA20区域"
    )
    if prior_high > price and price / prior_high > 0.95 and not recent_touch:
        warning += "；贴近前高但未回踩，避免将趋势资格误读为即时入场信号"
    warning += "；不复权价可能受除权干扰" if row["价格口径"] == "raw" else ""
    return {
        "MA20观察带下沿": lower,
        "MA20观察带上沿": upper,
        "下一日MA20穿越临界收盘": next_cross,
        "本日最高价": high,
        "下一日缩量比较基准_股": vol_ceiling,
        "前250根最高收盘_不含今日": prior_high,
        "近5日缩量触及MA20": recent_touch,
        "本日日线转强记录": rebound,
        "偏离过大": extension,
        "观察状态": warning,
        "日线观察方案": plan,
        "下一交易日核对": (
            f"下一交易日若收盘<{next_cross:.4f}，即低于该日MA20（无除权且新增一根K线时）；"
            f"若发生回踩，成交量与{vol_ceiling:.0f}股比较；"
            f"后续转强条件需收盘>{high:.2f}并同时守住该日MA20。"
            "这些是条件边界，不是挂单价格；再往后的均线阈值必须重算。"
        ),
        "形态失效条件": invalid,
        "当前形态失效": bool(below or setup_invalid or price < m60),
        "资格与形态区别": "RPS或缩量门槛不通过只是筛选资格改变，不自动等于趋势失效。",
    }


def report(title: str, rows: Any, audit: dict[str, Any]) -> str:
    basis = (
        "原始不复权价可能受除权干扰"
        if audit["price_basis"] == "raw"
        else "本地前复权价尚未逐只与通达信图表核对"
    )
    gaps = len(audit["coverage_gaps"])
    head = (
        f"# {title} · {audit['as_of']}\n\n"
        f"共 **{len(rows)}只**；RPS分母 **{audit['denominator']}只本地有效沪深股票**。"
        "全部日线收盘口径，不使用分钟数据。\n\n"
        "**研究观察方案，不是可执行买卖结论；不提供目标收益或首仓比例。** "
        f"本次{basis}，名称缓存中{gaps}个代码缺少日K，"
        "尚未证明交易所全市场覆盖完整；行业缺失，不臆测分布。\n\n"
        "MA20/MA60每天重算；下述价位为本日快照。形态失效并非成交保证。"
        "跳空和流动性可能导致实际成交偏离触发价，参见"
        "[Investor.gov风险说明](https://www.investor.gov/introduction-investing/"
        "investing-basics/glossary/stop-order)。此处仅引用一般风险，不表示A股支持同类订单。\n\n"
    )
    head += (
        "定义：当前V2基础门槛通过，近5根日K出现旧V2收敛放量突破，"
        "且突破后每根收盘均未低于冻结突破位；包括趋势中的再启动。"
        "历史事件只认证量价形态，不伪称事件当天RPS/换手也已通过。\n\n"
        if title == "趋势刚启动"
        else "定义：刚才的严格日线趋势筛选通过，剔除已归入刚启动者。"
        "这表示目前结构成立，不表示知道整轮上涨从何日开始。\n\n"
    )
    if rows.empty:
        head += "当前没有满足全部条件的标的；未放宽规则补足名单。\n\n"
        if "v2_base_pass" in audit:
            head += (
                f"复核{audit['evaluated_candidates']}只候选，"
                f"V2当前基础通过{audit['v2_base_pass']}只；"
                "它们没有仍有效的近5日突破形态。\n\n"
            )
            reasons = {
                "extension": "偏离MA20过大（百分比或ATR门槛）",
                "turnover": "换手率不低于10%",
            }
            for item in audit["event_exclusions"]:
                failure = "、".join(reasons.get(x, x) for x in item["V2未通过"].split(",") if x)
                head += (
                    f"- {item['名称']}（{item['代码']}）：有近期突破形态，"
                    f"但当前未通过：{failure or '数据待核验'}。\n"
                )
        return head
    head += "|代码|名称|现价|RPS120|RPS250|MA20|MA20乖离%|\n|---|---|---:|---:|---:|---:|---:|\n"
    for r in rows.to_dict("records"):
        head += (
            f"|{r['代码']}|{r['名称']}|{r['现价']:.2f}|{r['RPS120']:.2f}|{r['RPS250']:.2f}|"
            f"{r['MA20']:.2f}|{r['MA20乖离%']:.2f}|\n"
        )
    for r in rows.to_dict("records"):
        head += (
            f"\n## {r['名称']} · {r['代码']}\n\n"
            f"现价 {r['现价']:.2f}；MA10 {r['MA10']:.2f}；MA20 {r['MA20']:.2f}；"
            f"MA60 {r['MA60']:.2f}；距52周最高收盘 {r['距52周高点%']:.2f}%；"
            f"20日最大回撤 {r['20日最大回撤%']:.2f}%。\n\n"
            f"- **状态**：{r['观察状态']}\n"
            f"- **观察方案**：{r['日线观察方案']}\n"
            f"- **下个交易日核对**：{r['下一交易日核对']}\n"
            f"- **失效条件**：{r['形态失效条件']}\n"
        )
        if title == "趋势刚启动":
            head += (
                f"- **突破证据**：{r['突破形态日期']}，距今{int(r['距突破交易日'])}根日K，"
                f"量比{r['突破日量比']:.2f}；当前换手率{r['换手率%']:.2f}%。\n"
            )
    return head


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    print("[CHECK] validating global ranks and every source daily-file hash", flush=True)
    base, universe, inventory = validate_snapshot(source)
    target = pd.Timestamp(base["as_of"])
    root = Path(base["root"])
    cfg, old = CombinedConfig(), LayeredConfig()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output or ROOT / "output" / f"combined_daily_{target:%Y%m%d}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError("output must be an empty/new directory")
    from pytdx.reader import GbbqReader
    from run_local_tdx_compare import adjust
    from run_right_confirmation import local_float

    cache = root / "T0002/hq_cache"
    print("[LOAD] reading dated local floating-share cache and corporate actions", flush=True)
    floats = local_float(cache / "base.dbf", int(target.strftime("%Y%m%d")))
    events = GbbqReader().get_df(str(cache / "gbbq"))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d", errors="raise")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]
    strength = (
        (universe.RPS120 >= old.rps120_min)
        & (universe.RPS250 >= old.rps250_min)
        & (universe.RPS120 + universe.RPS250 > old.rps_sum)
    )
    candidates = universe.loc[strength | universe["入选"]]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for i, row in enumerate(candidates.to_dict("records")):
        if i % 100 == 0:
            print(
                f"[EVALUATE] {i}/{len(candidates)}; full RPS denominator={len(universe)}",
                flush=True,
            )
        code = row["代码"]
        try:
            path = Path(inventory.loc[code, "path"])
            d = read_day(path)
            d = d.loc[(d.trade_date <= target) & (d.vol > 0)].reset_index(drop=True)
            ev = grouped.get(code, empty)
            if base["price_basis"] == "qfq":
                d, _ = adjust(d, ev, target, bars=None)
            calculated = daily_features(d, Settings(**base["parameters"]))
            for key in ("现价", "MA20", "return50", "return120", "return250"):
                if not np.isclose(row[key], calculated[key]):
                    raise ValueError(f"snapshot/daily mismatch: {key}")
            f = floats.get(code)
            turnover, missing = current_turnover(float(d.vol.iloc[-1]), f, ev, target)
            shapes = shape_series(d, old)
            if len(d) >= 280:
                result = evaluate(d, row["RPS120"], row["RPS250"], turnover, old)
                v2_pool = result["trend_pool"]
            else:
                v2_pool = False
                result = {
                    "stage": "未满足确认基础",
                    "v2_failed": "历史不足280根",
                    "v2_missing": "",
                    "setup": False,
                }
            event = recent_event(d, shapes, cfg)
            pool = assign_pool(bool(row["入选"]), v2_pool, event["近5日突破尚有效"])
            merged = {
                **row,
                **event,
                "分组": pool,
                "严格趋势通过": bool(row["入选"]),
                "V2当前基础通过": v2_pool,
                "V2当前形态": result["stage"],
                "V2未通过": result["v2_failed"],
                "V2缺失": result["v2_missing"],
                "换手率%": turnover,
                "换手缺失说明": missing,
                "股本记录日期": f["record_date"] if f else None,
                "ATR20昨日": float(shapes["atr"].iloc[-1]),
                "MA20乖离%": float((d.close.iloc[-1] / row["MA20"] - 1) * 100),
                "day_sha256": inventory.loc[code, "sha256"],
            }
            if pool != "未入选":
                merged.update(daily_plan(d, merged, cfg))
            rows.append(merged)
        except (OSError, ValueError, KeyError, UnicodeError) as exc:
            errors.append({"代码": code, "error": str(exc)})
            print(f"[ERROR] {code}: {exc}", file=sys.stderr)
    diagnostic = pd.DataFrame(rows)
    diagnostic.to_csv(out / "diagnostics.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    pd.DataFrame(errors, columns=["代码", "error"]).to_csv(
        out / "errors.csv", index=False, encoding="utf-8-sig"
    )
    audit: dict[str, Any] = {
        "version": "combined_daily_v1",
        "generated_at": datetime.now().isoformat(),
        "as_of": base["as_of"],
        "price_basis": base["price_basis"],
        "denominator": len(universe),
        "evaluated_candidates": len(candidates),
        "source_run": str(source),
        "base_selection_count": int(universe["入选"].sum()),
        "rules": cfg.model_dump(),
        "v2_rules": old.model_dump(),
        "coverage_gaps": base["name_cache_without_day_file"],
        "limitations": [
            "No post-filter re-ranking; global local-valid RPS reused for both pools",
            "Raw and adjusted prices are never mixed; original historical external RPS not reused",
            "Recent events certify shape only; RPS/turnover gates apply to the current date only",
            "Current name/share caches are not point-in-time archives; not a backtest",
            "Industry unknown; prices/signals are not instructions or guaranteed executions",
        ],
        "source_hashes": {
            str(p): sha(p)
            for p in [
                source / "all_valid.csv",
                source / "audit.json",
                cache / "gbbq",
                cache / "base.dbf",
            ]
        },
        "code_hashes": {
            str(p): sha(p)
            for p in [
                Path(__file__),
                script_file("run_daily_rps.py"),
                ROOT / "rps_analyzer/right_confirmation_v2.py",
            ]
        },
        "errors": errors,
        "status": "BLOCKED" if errors else "OK_WITH_CAVEATS",
    }
    if not errors and not diagnostic.empty:
        starts = diagnostic.loc[diagnostic["分组"].eq("趋势刚启动")].sort_values(
            "RPS250", ascending=False
        )
        ongoing = diagnostic.loc[diagnostic["分组"].eq("趋势进行中")].sort_values(
            "RPS250", ascending=False
        )
        assert not set(starts["代码"]) & set(ongoing["代码"])
        audit.update(
            {
                "starting": len(starts),
                "ongoing": len(ongoing),
                "overlap_assigned_to_starting": int(starts["严格趋势通过"].sum()),
                "v2_base_pass": int(diagnostic["V2当前基础通过"].eq(True).sum()),
                "v2_missing_data": int(diagnostic["V2缺失"].fillna("").ne("").sum()),
                "ongoing_extended": int(ongoing["偏离过大"].eq(True).sum()),
                "event_exclusions": diagnostic.loc[
                    diagnostic["近5日突破尚有效"] & ~diagnostic["分组"].eq("趋势刚启动"),
                    ["代码", "名称", "V2未通过", "V2缺失"],
                ].to_dict("records"),
            }
        )
        for title, frame, stem in [
            ("趋势刚启动", starts, "trend_starting"),
            ("趋势进行中", ongoing, "trend_ongoing"),
        ]:
            columns = [
                "代码",
                "名称",
                "分组",
                "现价",
                "RPS50",
                "RPS120",
                "RPS250",
                "MA10",
                "MA20",
                "MA60",
                "MA20乖离%",
                "距52周高点%",
                "20日最大回撤%",
                "口袋低点",
                "突破形态日期",
                "冻结突破位",
                "冻结整理低点",
                "观察状态",
                "日线观察方案",
                "下一交易日核对",
                "形态失效条件",
                "下一日MA20穿越临界收盘",
                "价格口径",
                "数据日期",
                "RPS分母",
            ]
            frame.reindex(columns=columns).to_csv(
                out / f"{stem}.csv", index=False, encoding="utf-8-sig", na_rep="缺"
            )
            (out / f"{stem}.md").write_text(report(title, frame, audit), encoding="utf-8")
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(out),
                **{
                    k: audit.get(k)
                    for k in ("status", "as_of", "denominator", "starting", "ongoing")
                },
            },
            ensure_ascii=False,
        )
    )
    return 2 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
