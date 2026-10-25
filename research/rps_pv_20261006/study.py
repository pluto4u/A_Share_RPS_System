"""Finite, causal signal ablations. Outcomes are proxies, never portfolio P&L."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODELS = ("universe", "rps_only", "pv_only", "rps_pv", "low_only", "low_turn", "oversold_only", "oversold_confirm")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def write_new(path, obj):
    with path.open("x", encoding="utf-8") as file:
        json.dump(obj, file, ensure_ascii=False, indent=2, allow_nan=False)


def features(close, high, amount, volume):
    """Rows=calendar dates, columns=stocks. Trailing windows never skip gaps."""
    if not close.index.equals(high.index) or not close.columns.equals(high.columns):
        raise ValueError("misaligned price axes")
    if (not amount.index.equals(close.index) or not volume.index.equals(close.index) or
            not amount.columns.equals(close.columns) or not volume.columns.equals(close.columns)):
        raise ValueError("misaligned activity axes")
    r120 = close / close.shift(120) - 1
    r50 = close / close.shift(50) - 1
    rps120 = r120.rank(axis=1, method="average", pct=True) * 100
    rps50 = r50.rank(axis=1, method="average", pct=True) * 100
    med20 = amount.shift(1).rolling(20, min_periods=20).median()
    eligible = (close.rolling(251, min_periods=251).count().eq(251) &
                (amount > 0) & (volume > 0) & (med20 >= 20_000_000))
    pv = ((close > close.shift(60)) & (close < close.shift(5)) &
          (amount.rolling(5, min_periods=5).mean() < amount.shift(5).rolling(20, min_periods=20).mean()) &
          (close > high.shift(1)))
    low = close.rolling(250, min_periods=250).min()
    width = close.rolling(250, min_periods=250).max() - low
    position = (close - low) / width.where(width > 0)
    low_only = position <= .30
    low_turn = (low_only & (rps50 > rps50.shift(20)) & (close > high.shift(1)) & (amount > med20))
    logret = np.log(close / close.shift(1))
    ret5 = np.log(close / close.shift(5))
    market = ret5.median(axis=1)
    sigma = logret.shift(5).rolling(20, min_periods=20).std(ddof=1)
    z5 = ret5.sub(market, axis=0) / (sigma.where(sigma > 0) * np.sqrt(5))
    oversold = z5 <= -2
    masks = dict(universe=eligible, rps_only=eligible & (rps120 >= 80), pv_only=eligible & pv,
                 rps_pv=eligible & pv & (rps120 >= 80), low_only=eligible & low_only,
                 low_turn=eligible & low_turn, oversold_only=eligible & oversold,
                 oversold_confirm=eligible & oversold & (close > close.shift(1)) & (amount < amount.shift(1)))
    return masks, dict(rps120=rps120, rps50=rps50, position250=position, oversold_z5=z5)


def outcome(open_, volume, horizon):
    """Neither of these future labels is provided to features()."""
    entry = open_.shift(-1)
    exit_ = open_.shift(-(horizon+1))
    valid = (entry > 0) & (exit_ > 0) & (volume.shift(-1) > 0) & (volume.shift(-(horizon+1)) > 0)
    return (exit_ / entry - 1).where(valid)


def daily_groups(masks, label):
    rows = []
    for model, mask in masks.items():
        selected = mask.sum(axis=1)
        valid = (mask & label.notna()).sum(axis=1)
        values = label.where(mask)
        frame = pd.DataFrame(dict(model=model, selected=selected, valid=valid,
                                  unavailable=selected-valid, gross_proxy=values.mean(axis=1),
                                  positive_fraction=(values > 0).sum(axis=1) / valid.where(valid > 0),
                                  label_exit_date=pd.Series(label.index, index=label.index).shift(
                                      -(label.attrs["horizon"]+1))),
                             index=label.index)
        rows.append(frame)
    return pd.concat(rows).rename_axis("signal_date").reset_index()


def paired_interval(values):
    monthly = values.resample("ME").mean().dropna()
    if len(monthly) < 12:
        return dict(months=len(monthly), mean_pp=float(monthly.mean()*100),
                    daily_mean_pp=float(values.mean()*100), lo_pp=None, hi_pp=None)
    x = monthly.to_numpy()
    rng = np.random.default_rng(20261006)
    starts = rng.integers(0, len(x), size=(5000, int(np.ceil(len(x)/3))))
    idx = (starts[:, :, None] + np.arange(3)) % len(x)
    draws = x[idx.reshape(5000, -1)[:, :len(x)]].mean(axis=1)
    lo, hi = np.quantile(draws, [.05/12, 1-.05/12])
    return dict(months=len(x), mean_pp=float(monthly.mean()*100), daily_mean_pp=float(values.mean()*100),
                lo_pp=float(lo*100), hi_pp=float(hi*100))


def isolate_period(daily, start, end):
    subset = daily[daily.signal_date.between(start, end)].copy()
    censored = subset.label_exit_date.isna() | (subset.label_exit_date > pd.Timestamp(end))
    subset.loc[censored, "valid"] = 0
    subset.loc[censored, "unavailable"] = subset.loc[censored, "selected"]
    subset.loc[censored, ["gross_proxy", "positive_fraction"]] = np.nan
    return subset


def load_inputs(source, cutoff, out):
    columns = ["trade_date", "ts_code", "open", "high", "low", "close", "amount", "vol"]
    frame = pd.read_parquet(source, columns=columns)
    frame = frame.loc[frame.trade_date <= pd.Timestamp(cutoff)].copy()
    duplicated = int(frame.duplicated(["trade_date", "ts_code"]).sum())
    invalid = int((frame[["open", "high", "low", "close", "amount", "vol"]] <= 0).any(axis=1).sum())
    nulls = {c: int(frame[c].isna().sum()) for c in columns}
    impossible = int(((frame.high < frame[["open", "close"]].max(axis=1)) |
                      (frame.low > frame[["open", "close"]].min(axis=1))).sum())
    if duplicated or impossible or frame["trade_date"].isna().any() or frame.ts_code.isna().any():
        raise ValueError(f"invalid keys/OHLC: duplicates={duplicated}, impossible={impossible}")
    # Invalid traded rows are explicit missing observations, never zero-filled.
    good = (frame[["open", "high", "low", "close", "amount", "vol"]] > 0).all(axis=1)
    frame.loc[~good, ["open", "high", "low", "close", "amount", "vol"]] = np.nan
    quality = dict(rows=len(frame), securities=frame.ts_code.nunique(), first=str(frame.trade_date.min().date()),
                   last=str(frame.trade_date.max().date()), duplicate_keys=duplicated, invalid_rows=invalid,
                   null_counts=nulls, impossible_ohlc=impossible,
                   historical_survivorship_complete=False, historical_security_status_complete=False)
    annual = frame.groupby(frame.trade_date.dt.year).agg(rows=("ts_code", "size"), securities=("ts_code", "nunique"))
    annual.to_csv(out / "coverage_by_year.csv")
    quality["early_ending_before_last_250_sessions"] = int((frame.groupby("ts_code").trade_date.max() <
        sorted(frame.trade_date.unique())[-250]).sum())
    write_new(out / "quality.json", quality)
    print(json.dumps(quality, ensure_ascii=False), flush=True)
    prices = {c: frame.pivot(index="trade_date", columns="ts_code", values=c).astype(np.float32)
              for c in ("open", "high", "close", "amount", "vol")}
    return prices, quality


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    p = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    source = ROOT / p["input"]
    out = args.output or ROOT / "output/rps_pv_20261006" / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    registration = dict(registered_at_utc=datetime.now(timezone.utc).isoformat(), experiment=p["experiment_id"],
                        protocol=p, protocol_sha256=sha(HERE / "protocol.json"), code_sha256=sha(Path(__file__)),
                        source_path=str(source), source_sha256=sha(source),
                        metadata_sha256=sha(ROOT / p["source_metadata"]), independent_oos=False)
    write_new(out / "registered.json", registration)
    (out / "study_at_registration.py").write_bytes(Path(__file__).read_bytes())
    print(f"registered {out}", flush=True)
    prices, quality = load_inputs(source, p["cutoff"], out)
    masks, descriptors = features(prices["close"], prices["high"], prices["amount"], prices["vol"])
    signal_counts = pd.DataFrame({m: mask.sum(axis=1) for m, mask in masks.items()})
    signal_counts.to_csv(out / "signal_counts.csv", index_label="signal_date")
    # Save bounded auditable signal features BEFORE generating any future labels.
    preview_rows = []
    for model in MODELS[1:]:
        rows, cols = np.where(masks[model].to_numpy())
        selected = np.linspace(0, max(0, len(rows)-1), min(100, len(rows)), dtype=int)
        for k in selected:
            i, j = rows[k], cols[k]
            preview_rows.append(dict(model=model, code=prices["close"].columns[j],
                signal_date=str(prices["close"].index[i].date()),
                **{n: float(f.iloc[i,j]) if pd.notna(f.iloc[i,j]) else None for n, f in descriptors.items()}))
    pd.DataFrame(preview_rows).to_csv(out / "signal_feature_preview.csv", index=False)
    summaries, contrasts, yearly, all_daily = [], [], [], []
    for horizon in (20, 5):
        labels = outcome(prices["open"], prices["vol"], horizon)
        labels.attrs["horizon"] = horizon
        labels.index.name, labels.columns.name = "signal_date", "code"
        labels.to_parquet(out / f"future_labels_{horizon}d.parquet")
        daily = daily_groups(masks, labels)
        daily["horizon"] = horizon
        all_daily.append(daily)
        for period, (start, end) in p["periods"].items():
            subset = isolate_period(daily, start, end)
            for model, group in subset.groupby("model"):
                for cost in p["friction_proxy_roundtrip"]:
                    summaries.append(dict(model=model, period=period, horizon=horizon, friction_pct=cost*100,
                        signals=int(group.selected.sum()), labels=int(group.valid.sum()),
                        unavailable=int(group.unavailable.sum()), signal_days=int((group.selected > 0).sum()),
                        valid_days=int(group.gross_proxy.notna().sum()),
                        gross_daily_mean_pct=float(group.gross_proxy.mean()*100),
                        cost_proxy_daily_mean_pct=float((group.gross_proxy-cost).mean()*100)))
            pivot = subset.pivot(index="signal_date", columns="model", values="gross_proxy")
            for candidate, baseline in p["contrasts"]:
                pair = pivot[[candidate, baseline]].dropna()
                if pair.empty:
                    continue
                contrasts.append(dict(candidate=candidate, baseline=baseline, period=period, horizon=horizon,
                                      paired_days=len(pair), **paired_interval(pair[candidate]-pair[baseline])))
        for (year, model), group in daily.groupby([daily.signal_date.dt.year, "model"]):
            if year >= 2018:
                yearly.append(dict(year=int(year), model=model, horizon=horizon,
                    valid_days=int(group.gross_proxy.notna().sum()),
                    gross_daily_mean_pct=float(group.gross_proxy.mean()*100)))
        print(f"completed horizon={horizon}", flush=True)
    summary = pd.DataFrame(summaries)
    summary.to_csv(out / "summary.csv", index=False)
    contrast = pd.DataFrame(contrasts)
    contrast.to_csv(out / "contrasts.csv", index=False)
    pd.DataFrame(yearly).to_csv(out / "yearly.csv", index=False)
    pd.concat(all_daily).to_parquet(out / "daily_groups.parquet", index=False)
    lines = ["# RPS与量价拆解：历史探索", "", "不是真实交易净收益，不是独立样本外，不给可执行交易指令。",
             "前复权开盘到开盘表现代理；各信号日先等权平均，再平均日期。以下不是年化或资金收益。",
             "股票集合不保证完整退市历史；涨跌停、历史证券状态、实际现金分红与整手均未核实。",
             "", "| 模型 | 早期20日毛代理% | 后期20日毛代理% | 早期扣1%代理% | 后期扣1%代理% |",
             "|---|---:|---:|---:|---:|"]
    for model in MODELS:
        sub = summary[(summary.model==model)&(summary.horizon==20)&(summary.friction_pct==1)]
        e, l = sub[sub.period=="early"].iloc[0], sub[sub.period=="late"].iloc[0]
        lines.append(f"| {model} | {e.gross_daily_mean_pct:.3f} | {l.gross_daily_mean_pct:.3f} | "
                     f"{e.cost_proxy_daily_mean_pct:.3f} | {l.cost_proxy_daily_mean_pct:.3f} |")
    lines += ["", "## 主20日同日增量差（百分点）", "6个登记对比，各段按月3月块5000次重抽样，99.1667%区间；仅描述。"]
    for r in contrast[contrast.horizon==20].itertuples():
        lines.append(f"- {r.candidate} vs {r.baseline}，{r.period}：{r.mean_pp:.3f}，"
                     f"区间[{r.lo_pp:.3f}, {r.hi_pp:.3f}]；{r.months}月/{r.paired_days}配对日。")
    lines += ["", "## 限制", *[f"- {gap}" for gap in p["known_gaps"]],
              "- 标签不可得的事件按模型/段统计，未补零；有效标签均值可能仍受缺失选择影响，不能凭此放行策略。",
              "- 事件可以重叠；均值、区间和费用代理不能拼接为组合净值或最大回撤。",
              "- 成交额代理不是换手率或买卖方向，不能解释成主力吸筹。",
              "", "登记、质量、分年覆盖、逐日信号数、特征抽样、分离的未来标签和全部结果已保存。"]
    (out / "FINDINGS.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    if sha(source) != registration["source_sha256"] or sha(Path(__file__)) != registration["code_sha256"]:
        raise ValueError("input or code changed during run; output not accepted")
    write_new(out / "completed.json", dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),
        files={f.name:sha(f) for f in sorted(out.iterdir()) if f.is_file()}, quality_passed_for_exploration=True,
        cleared_for_investment=False, independent_oos=False))
    print(summary[(summary.horizon==20)&(summary.friction_pct==1)].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
