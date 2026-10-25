"""Fixed-universe monthly ETF study, raw-price integer-lot cash ledger."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/etf_allocation_20261006"
RAW = OUT / "raw"
MODELS = ("trend200", "momentum126", "equal_weight", "equity_hold")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def day(ms):
    return pd.to_datetime(ms, unit="ms", utc=True).tz_convert("Asia/Shanghai").normalize().tz_localize(None)


def load_data():
    protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    frames, distributions, quality = {}, {}, []
    for symbol in protocol["universe"]:
        code = symbol.split(".")[0]
        f = pd.read_csv(RAW / f"{code}_raw.csv", parse_dates=["date"]).set_index("date").sort_index()
        for col in ("open", "close", "high", "low", "volume_lots", "amount"):
            f[col] = pd.to_numeric(f[col], errors="raise")
        if f.index.has_duplicates or f.empty or not f.index.is_monotonic_increasing:
            raise ValueError(f"{code}: duplicate/empty dates")
        if f[["open", "close", "high", "low", "volume_lots", "amount"]].isna().any().any():
            raise ValueError(f"{code}: null data")
        if (f[["open", "close", "high", "low", "volume_lots", "amount"]] <= 0).any().any():
            raise ValueError(f"{code}: nonpositive data")
        if ((f.high < f[["open", "close"]].max(axis=1)) | (f.low > f[["open", "close"]].min(axis=1))).any():
            raise ValueError(f"{code}: impossible OHLC")
        vwap = f.amount / (100 * f.volume_lots)
        outside = ((vwap < f.low * .99) | (vwap > f.high * 1.01))
        if outside.any():
            raise ValueError(f"{code}: raw price / turnover inconsistency {int(outside.sum())}")
        events = []
        seen_distributions = set()
        envelope = json.loads((RAW / f"{code}_dividends.json").read_text(encoding="utf-8"))
        if envelope.get("ok") is not True:
            raise ValueError(f"{code}: unsuccessful dividend source")
        for item in envelope["data"]["item"]:
            if str(item.get("progress")) != "2":
                continue
            ex = day(item["ex_dividend_date_ms"])
            if not f.index[0] <= ex <= f.index[-1]:
                continue
            required = ("registration_date_ms", "payment_date_ms", "publish_date_ms", "per_ten_cash_before_tax")
            if any(item.get(k) is None for k in required):
                raise ValueError(f"{code}: missing distribution fields")
            reg, pay, published = [day(item[k]) for k in required[:3]]
            amount = float(item["per_ten_cash_before_tax"]) / 10
            if not published <= reg < ex <= pay or amount < 0 or ex not in f.index or reg not in f.index:
                raise ValueError(f"{code}: invalid dividend dates / amounts")
            event_key = (reg, ex, pay)
            if event_key in seen_distributions:
                raise ValueError(f"{code}: duplicate distribution event")
            seen_distributions.add(event_key)
            events.append(dict(reg=reg, ex=ex, pay=pay, published=published, amount=amount))
        cash_per_share = pd.Series(0., index=f.index)
        for e in events:
            cash_per_share.loc[e["ex"]] += e["amount"]
        total_return = (f.close + cash_per_share).div(f.close.shift()).fillna(1.)
        if (abs(total_return - 1) > .35).any():
            raise ValueError(f"{code}: possible missing split / discontinuity")
        f["tri"] = total_return.cumprod()
        f["ex_cash"] = cash_per_share
        f["capacity"] = f.amount.rolling(20, min_periods=20).median().shift(1) * .01
        frames[symbol], distributions[symbol] = f, events
        quality.append(dict(symbol=symbol, rows=len(f), first=str(f.index[0].date()),
                            last=str(f.index[-1].date()), vwap_outside=int(outside.sum()),
                            dividends=len(events), raw_adjust="none"))
    calendars = [f.index for f in frames.values()]
    if any(not index.equals(calendars[0]) for index in calendars[1:]):
        raise ValueError("calendars differ; do not silently drop missing sessions")
    return protocol, frames, distributions, quality


def weights(frames, pos, model):
    symbols = tuple(frames)
    if model == "equal_weight":
        return dict.fromkeys(symbols, .25)
    if model == "equity_hold":
        return {s: float(s == "510300.SH") for s in symbols}
    if model == "trend200":
        return {s: .25 if pos >= 199 and f.tri.iloc[pos] > f.tri.iloc[pos-199:pos+1].mean() else 0.
                for s, f in frames.items()}
    if model != "momentum126":
        raise ValueError(model)
    rank = sorted(((f.tri.iloc[pos] / f.tri.iloc[pos-126] - 1, s)
                   for s, f in frames.items()), key=lambda x: (-x[0], x[1])) if pos >= 126 else []
    chosen = [s for momentum, s in rank if momentum > 0][:2]
    return {s: .5 if s in chosen else 0. for s in symbols}


def simulate(frames, distributions, model, start, end, slip, capital=1_000_000.):
    dates = next(iter(frames.values())).index
    positions = np.flatnonzero((dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end)))
    if not len(positions) or positions[0] == 0:
        raise ValueError("no evaluation / warmup period")
    symbols = tuple(frames)
    units = dict.fromkeys(symbols, 0)
    cash = capital
    entitlements, receivables = {}, {}
    paid_keys = set()
    schedule = defaultdict(list)
    for s, events in distributions.items():
        for j, e in enumerate(events):
            schedule[e["reg"]].append((s, j, e))
    targets, target_signal, expires = None, None, -1
    rows, orders, dividend_rows, signals = [], [], [], []
    prev = positions[0] - 1

    def decide(pos, equity):
        w = weights(frames, pos, model)
        desired = {s: int(equity * w[s] / (frames[s].close.iloc[pos] * (1 + slip)) / 100) * 100
                   for s in symbols}
        signals.append({"signal_date": str(dates[pos].date()), "model": model,
                        **{s: w[s] for s in symbols}, **{s + "_units": desired[s] for s in symbols}})
        return desired

    targets = decide(prev, capital)
    target_signal = dates[prev]
    expires = positions[0] + 1
    for pos in positions:
        date = dates[pos]
        pre_cash = cash
        for key, value in entitlements.items():
            s, j = key
            e = distributions[s][j]
            if e["ex"] == date:
                receivables[key] = value
        if targets is not None and pos <= expires:
            for side in ("sell", "buy"):
                for s in symbols:
                    requested = units[s] - targets[s] if side == "sell" else targets[s] - units[s]
                    if requested <= 0:
                        continue
                    f = frames[s]
                    opening = float(f.open.iloc[pos])
                    reference = float(f.close.iloc[pos-1] - f.ex_cash.iloc[pos])
                    status = "filled"
                    quantity = int(min(requested, float(f.capacity.iloc[pos]) / opening) / 100) * 100
                    fill = opening * (1 + slip if side == "buy" else 1 - slip)
                    if abs(opening / reference - 1) >= .095:
                        quantity, status = 0, "limit_proxy_rejected"
                    if side == "buy":
                        budget = min(cash, pre_cash)
                        affordable = int(max(0., budget - 5) / (fill * 1.00025) / 100) * 100
                        quantity = min(quantity, affordable)
                    fee = max(5., quantity * fill * .00025) if quantity else 0.
                    if quantity:
                        if side == "sell":
                            units[s] -= quantity
                            cash += quantity * fill - fee
                        else:
                            units[s] += quantity
                            spent = quantity * fill + fee
                            cash -= spent
                            pre_cash -= spent
                    elif status == "filled":
                        status = "cash_or_capacity_deferred"
                    orders.append(dict(date=str(date.date()), signal_date=str(target_signal.date()),
                                       symbol=s, side=side, requested=requested, quantity=quantity,
                                       fill=fill, fee=fee, status=status))
        # Payment is credited at this close, never available for this day's open.
        for key, value in list(receivables.items()):
            s, j = key
            e = distributions[s][j]
            if e["pay"] <= date and key not in paid_keys:
                cash += value
                paid_keys.add(key)
                del receivables[key]
                dividend_rows.append(dict(date=str(date.date()), symbol=s, cash=value,
                                          registered=str(e["reg"].date()), ex=str(e["ex"].date()),
                                          contractual_payment=str(e["pay"].date())))
        for s, j, e in schedule[date]:
            entitlements[(s, j)] = units[s] * e["amount"]
        market_value = sum(units[s] * float(frames[s].close.iloc[pos]) for s in symbols)
        owed = sum(receivables.values())
        equity = cash + market_value + owed
        if cash < -.000001 or any(q < 0 or q % 100 for q in units.values()):
            raise AssertionError("negative cash / invalid lots")
        rows.append(dict(date=date, equity=equity, cash=cash, market_value=market_value,
                         receivable=owed, **{s: units[s] for s in symbols}))
        if model != "equity_hold" and pos + 1 < len(dates) and dates[pos+1].month != date.month:
            targets = decide(pos, equity)
            target_signal = date
            expires = pos + 2
    return pd.DataFrame(rows).set_index("date"), pd.DataFrame(orders), pd.DataFrame(dividend_rows), pd.DataFrame(signals)


def metrics(book, capital=1_000_000.):
    equity = book.equity
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    peak = equity.cummax().clip(lower=capital)
    returns = equity.pct_change()
    returns.iloc[0] = equity.iloc[0] / capital - 1
    return dict(total_return_pct=(equity.iloc[-1] / capital - 1)*100,
                cagr_pct=((equity.iloc[-1] / capital)**(1/years)-1)*100,
                max_drawdown_pct=float((equity/peak - 1).min()*100),
                mean_cash_pct=float((book.cash/equity).mean()*100),
                sharpe=float(returns.mean()/returns.std()*np.sqrt(252)) if returns.std() > 0 else None,
                final_equity=float(equity.iloc[-1]), days=len(book))


def bootstrap_active(candidate, benchmark):
    # Paired contiguous 3-month blocks; 97.5% interval for two hypotheses.
    monthly_a = candidate.equity.resample("ME").last().pct_change()
    monthly_b = benchmark.equity.resample("ME").last().pct_change()
    monthly_a.iloc[0] = candidate.equity.resample("ME").last().iloc[0] / 1_000_000 - 1
    monthly_b.iloc[0] = benchmark.equity.resample("ME").last().iloc[0] / 1_000_000 - 1
    active = (monthly_a - monthly_b).dropna().to_numpy()
    rng = np.random.default_rng(20261006)
    draws = []
    for _ in range(3000):
        starts = rng.integers(0, len(active), size=int(np.ceil(len(active)/3)))
        idx = np.concatenate([(s+np.arange(3)) % len(active) for s in starts])[:len(active)]
        draws.append(active[idx].mean()*12*100)
    lo, hi = np.quantile(draws, [.0125, .9875])
    return dict(annualized_arithmetic_active_pct=float(active.mean()*12*100),
                lo_pct=float(lo), hi_pct=float(hi), months=len(active), block_months=3)


def main():
    protocol, frames, distributions, quality = load_data()
    OUT.mkdir(exist_ok=True, parents=True)
    intervals = {"full": (protocol["evaluation_start"], protocol["fetch_end"]),
                 "early": tuple(protocol["historical_splits"][0]),
                 "late": tuple(protocol["historical_splits"][1])}
    summary, annual, books, checks = [], [], {}, []
    for slip in protocol["slippage_per_side"]:
        for period, (start, end) in intervals.items():
            for model in MODELS:
                book, orders, dividends, signals = simulate(frames, distributions, model, start, end, slip)
                prefix = f"{model}_{period}_{int(round((slip+.00025)*10000))}bp"
                book.to_csv(OUT/f"{prefix}_equity.csv")
                orders.to_csv(OUT/f"{prefix}_orders.csv", index=False)
                dividends.to_csv(OUT/f"{prefix}_dividends.csv", index=False)
                signals.to_csv(OUT/f"{prefix}_signals.csv", index=False)
                record = dict(model=model, period=period, cost_bp_per_side=(slip+.00025)*10000,
                              **metrics(book), fills=int((orders.quantity > 0).sum()) if len(orders) else 0)
                summary.append(record)
                books[(model, period, slip)] = book
                checks.append(dict(run=prefix, min_cash=float(book.cash.min()),
                                   accounting_error=float(abs(book.equity-book.cash-book.market_value-book.receivable).max())))
                if period == "full" and slip == protocol["slippage_per_side"][0]:
                    prior = protocol["capital"]
                    for year, group in book.groupby(book.index.year):
                        annual.append(dict(model=model, year=int(year), return_pct=(group.equity.iloc[-1]/prior-1)*100))
                        prior = group.equity.iloc[-1]
                print(prefix, round(record["cagr_pct"], 3), round(record["max_drawdown_pct"], 3), flush=True)
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(OUT/"summary.csv", index=False)
    pd.DataFrame(annual).to_csv(OUT/"annual_returns.csv", index=False)
    pd.DataFrame(checks).to_csv(OUT/"ledger_checks.csv", index=False)
    active = {}
    for model in MODELS[:2]:
        active[model] = bootstrap_active(books[(model, "full", .00075)],
                                         books[("equal_weight", "full", .00075)])
    manifest = dict(protocol_sha256=sha(HERE/"protocol.json"),
                    clarification_sha256=sha(HERE/"execution_clarification.json"),
                    code_sha256=sha(Path(__file__)), raw_files={p.name:sha(p) for p in sorted(RAW.iterdir()) if p.is_file()},
                    quality=quality, accounting_checks_passed=all(x["min_cash"] >= 0 and x["accounting_error"] < 1e-6 for x in checks),
                    active_comparison=active, independent_oos=False, real_auction_execution_verified=False)
    (OUT/"audit.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 固定ETF月度配置研究", "", "这是事后历史研究候选，不是已证明未来盈利的方案。未修改登记规则。", "",
             "原始价格：东方财富 fqt=0；分红：同花顺金融数据服务，元/10份换算为元/份。",
             "基准与候选均按同一账本、同一成本；信号后下一开盘，卖出资金最多延迟一天买入。",
             "预登记资金100万元，整数100份，现金无利息；管理费已反映于ETF报价，不重复扣减。",
             "每次委托最低佣金5元；容量只用前20日成交额。收盘估值含未平仓，不假装期末已卖出。",
             "", "| 模型 | 全期年化% | 最大回撤% | 早期收益% | 后期收益% | 30bp全期收益% | 历史门槛 |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for model in MODELS:
        def get(period, cost):
            return summary_df[(summary_df.model==model)&(summary_df.period==period)&
                              np.isclose(summary_df.cost_bp_per_side,cost)].iloc[0]
        full, early, late, stress = get("full",10), get("early",10), get("late",10), get("full",30)
        passed = (early.total_return_pct > 0 and late.total_return_pct > 0 and
                  stress.total_return_pct > 0 and full.max_drawdown_pct >= -20)
        lines.append(f"| {model} | {full.cagr_pct:.2f} | {full.max_drawdown_pct:.2f} | "
                     f"{early.total_return_pct:.2f} | {late.total_return_pct:.2f} | "
                     f"{stress.total_return_pct:.2f} | {'通过历史条件' if passed else '未通过'} |")
    lines += ["", "## 两候选相对等权配置的比较",
              "3个月连续块重抽样，固定种子，3000次；两假设使用97.5%区间。只是回顾性描述，不能清除先前研究的选择偏差。"]
    for model, m in active.items():
        lines.append(f"- {model}：年化算术月超额 {m['annualized_arithmetic_active_pct']:.2f}%；"
                     f"区间 [{m['lo_pct']:.2f}%, {m['hi_pct']:.2f}%]。")
    lines += ["", "## 尚未通过的部分",
              "- 固定四只存续ETF是事后选定的研究集合；不能代表所有历史ETF或声称完全消除了幸存者偏差。",
              "- 历史宏观市场和部分ETF走势此前已被知道；任何时间切分都不称为新的未见样本外。",
              "- 开盘价是小额模拟成交代理，尚无逐笔竞价、真实历史涨跌停价与订单簿核对。",
              "- 东方财富原始价格与同花顺分红的完整性尚需基金公告独立抽查；价格/成交额一致性和现金守恒不是第三方实盘审计。",
              "- 2026-09-30后数据没有用于收益评价。前瞻验证须记录冻结日之后实际可得行情和订单，不能补写过去日志。",
              "- 若区间跨零，盈利主要可能来自资产配置的市场收益；不得称为已证实的择时超额。",
              "", "## 复现",
              "在策略仓库执行：python research/etf_allocation_20261006/run_study.py",
              "协议、执行补充、全部成本/分段结果、逐日权益、订单、分红、信号与输入哈希均已保存。",
              "", "方法来源：https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum",
              "过拟合参考：https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf"]
    (OUT/"REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
