from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

import requests
from pypdf import PdfReader


DATE_RE = re.compile(r"^\d{8}$")
NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass
class Holding:
    market: str
    code: str
    name: str
    quantity: float
    price: float
    cost: float
    value: float


@dataclass
class Transaction:
    sequence: int
    date: str
    category: str
    code: str
    name: str
    quantity: float
    price: float
    amount: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    cash_balance: float
    page: int = 0
    raw_line: str = ""

    @property
    def trade_notional(self) -> float:
        return abs(self.quantity * self.price)


@dataclass
class Statement:
    source: Path
    pages: int
    print_time: str
    query_range: str
    customer: str
    id_number: str
    fund_account: str
    security_value: float
    total_assets: float
    cash_balance: float
    cash_available: float
    avg_assets_20d: float
    holdings: list[Holding]
    transactions: list[Transaction]
    normalized_hash: str
    unmatched_date_lines: list[str]


def number(value: str) -> float:
    return float(value.replace(",", ""))


def extract_value(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else default


def parse_statement(path: Path, password: str) -> Statement:
    reader = PdfReader(str(path))
    if reader.is_encrypted and reader.decrypt(password) == 0:
        raise ValueError(f"无法解密：{path}")
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages)
    normalized = re.sub(r"打印日期：[^\n]+", "打印日期：<normalized>", text)

    holdings: list[Holding] = []
    first = pages[0]
    in_holdings = False
    for line in first.splitlines():
        line = line.strip()
        if line.startswith("交易市场 证券代码"):
            in_holdings = True
            continue
        if line.startswith("资金流水明细"):
            in_holdings = False
            break
        if not in_holdings:
            continue
        tokens = line.split()
        if len(tokens) != 7:
            continue
        try:
            holdings.append(
                Holding(
                    market=tokens[0],
                    code=tokens[1],
                    name=tokens[2],
                    quantity=number(tokens[3]),
                    price=number(tokens[4]),
                    cost=number(tokens[5]),
                    value=number(tokens[6]),
                )
            )
        except ValueError:
            continue

    transactions: list[Transaction] = []
    unmatched: list[str] = []
    sequence = 0
    for page_number, page in enumerate(pages, start=1):
        for raw_line in page.splitlines():
            line = raw_line.strip()
            tokens = line.split()
            if not tokens or not DATE_RE.match(tokens[0]):
                continue
            if len(tokens) < 9 or not all(NUMBER_RE.match(x) for x in tokens[-7:]):
                unmatched.append(line)
                continue
            prefix = tokens[1:-7]
            if not prefix:
                unmatched.append(line)
                continue
            category = prefix[0]
            code = prefix[1] if len(prefix) >= 2 else ""
            name = "".join(prefix[2:]) if len(prefix) >= 3 else ""
            tail = [number(x) for x in tokens[-7:]]
            transactions.append(
                Transaction(
                    sequence=sequence,
                    date=tokens[0],
                    category=category,
                    code=code,
                    name=name,
                    quantity=tail[0],
                    price=tail[1],
                    amount=tail[2],
                    commission=tail[3],
                    stamp_tax=tail[4],
                    transfer_fee=tail[5],
                    cash_balance=tail[6],
                    page=page_number,
                    raw_line=line,
                )
            )
            sequence += 1

    return Statement(
        source=path,
        pages=len(pages),
        print_time=extract_value(text, r"打印日期：([^\n]+)"),
        query_range=extract_value(text, r"查询区间：([^\n]+)"),
        customer=extract_value(text, r"客户姓名：([^\n]+)"),
        id_number=extract_value(text, r"证件号码：([^\n]+)"),
        fund_account=extract_value(text, r"资金账号：([^\s]+)"),
        security_value=number(extract_value(text, r"证券市值\(RMB\)：\s*([\d.]+)", "0")),
        total_assets=number(extract_value(text, r"总资产\(RMB\)：\s*([\d.]+)", "0")),
        cash_balance=number(extract_value(text, r"资金余额\(RMB\)：\s*([\d.]+)", "0")),
        cash_available=number(extract_value(text, r"资金可用\(RMB\)：\s*([\d.]+)", "0")),
        avg_assets_20d=number(extract_value(text, r"前20个交易日日均资产\(RMB\)：\s*([\d.]+)", "0")),
        holdings=holdings,
        transactions=transactions,
        normalized_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        unmatched_date_lines=unmatched,
    )


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return "—"
    return "*" * max(4, len(value) - keep) + value[-keep:]


def fmt_qty(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.4f}".rstrip("0").rstrip(".")


def fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def statement_markdown(statement: Statement, duplicate_note: str = "") -> str:
    lines = [
        f"# 股票明细对账单：{statement.source.stem}",
        "",
        "> 由加密PDF转换生成。为保护隐私，证件号和资金账号已脱敏；原始PDF未修改。",
        "",
        "## 账户概览",
        "",
        "| 项目 | 内容 |",
        "|---|---:|",
        f"| 查询区间 | {statement.query_range} |",
        f"| 客户 | {statement.customer[:1]}* |",
        f"| 证件号码 | {mask(statement.id_number)} |",
        f"| 资金账号 | {mask(statement.fund_account)} |",
        f"| 证券市值 | {fmt_money(statement.security_value)} 元 |",
        f"| 总资产 | {fmt_money(statement.total_assets)} 元 |",
        f"| 资金余额 | {fmt_money(statement.cash_balance)} 元 |",
        f"| 可用资金 | {fmt_money(statement.cash_available)} 元 |",
        f"| 前20个交易日日均资产 | {fmt_money(statement.avg_assets_20d)} 元 |",
        f"| PDF页数 | {statement.pages} |",
        f"| 解析流水条数 | {len(statement.transactions)} |",
    ]
    if duplicate_note:
        lines.extend(["", f"> {duplicate_note}"])

    lines.extend(
        [
            "",
            "## 期末持仓",
            "",
            "| 市场 | 代码 | 名称 | 数量 | 市价 | 成本价 | 市值 | 浮动盈亏 | 收益率 |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for h in statement.holdings:
        pnl = (h.price - h.cost) * h.quantity
        ret = (h.price / h.cost - 1) if h.cost else math.nan
        ret_text = f"{ret:.2%}" if math.isfinite(ret) else "—"
        lines.append(
            f"| {h.market} | {h.code} | {h.name} | {fmt_qty(h.quantity)} | {h.price:.4f} | "
            f"{h.cost:.4f} | {fmt_money(h.value)} | {fmt_money(pnl)} | {ret_text} |"
        )

    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for tx in statement.transactions:
        grouped[tx.date[:6]].append(tx)
    lines.extend(["", "## 资金流水与成交记录", ""])
    for month in sorted(grouped, reverse=True):
        lines.extend(
            [
                f"### {month[:4]}-{month[4:]}",
                "",
                "| 日期 | 类别 | 代码 | 名称 | 数量 | 价格 | 总发生金额 | 手续费 | 印花税 | 过户费 | 资金余额 |",
                "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for tx in grouped[month]:
            lines.append(
                f"| {tx.date[:4]}-{tx.date[4:6]}-{tx.date[6:]} | {tx.category} | {tx.code or '—'} | "
                f"{tx.name or '—'} | {fmt_qty(tx.quantity)} | {tx.price:.4f} | {fmt_money(tx.amount)} | "
                f"{fmt_money(tx.commission)} | {fmt_money(tx.stamp_tax)} | {fmt_money(tx.transfer_fee)} | "
                f"{fmt_money(tx.cash_balance)} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 数据说明",
            "",
            "- “总发生金额”为券商对账单原始口径，买入通常为负数，卖出通常为正数。",
            "- 持仓浮动盈亏按“（市价－成本价）×数量”计算。",
            "- Markdown未写入PDF密码。",
            f"- 未识别的日期行：{len(statement.unmatched_date_lines)} 条。",
            "",
        ]
    )
    return "\n".join(lines)


def chronological(transactions: list[Transaction]) -> list[Transaction]:
    # PDF是倒序打印；同日内也按较新的成交在前，因此反转sequence恢复大致成交顺序。
    return sorted(transactions, key=lambda tx: (tx.date, -tx.sequence))


def position_delta(tx: Transaction) -> float:
    if tx.category == "证券买入":
        return tx.quantity
    if tx.category == "证券卖出":
        return -tx.quantity
    if tx.category in {"红股入账", "新股入账", "转托管入", "证券转入"}:
        return tx.quantity
    if tx.category in {"转托管出", "证券转出"}:
        return -tx.quantity
    return 0.0


def fifo_analysis(statement: Statement) -> tuple[list[dict], dict[str, float]]:
    final_qty = {h.code: h.quantity for h in statement.holdings if h.code and h.quantity}
    net_delta: dict[str, float] = defaultdict(float)
    for tx in statement.transactions:
        if tx.code:
            net_delta[tx.code] += position_delta(tx)
    opening_qty = {code: final_qty.get(code, 0.0) - delta for code, delta in net_delta.items()}

    lots: dict[str, deque[dict]] = defaultdict(deque)
    for code, qty in opening_qty.items():
        if qty > 1e-8:
            lots[code].append({"qty": qty, "price": None, "date": None, "source": "期初"})

    matched: list[dict] = []
    for tx in chronological(statement.transactions):
        if not tx.code:
            continue
        if tx.category == "证券买入":
            unit_cost = abs(tx.amount) / tx.quantity if tx.quantity else tx.price
            lots[tx.code].append({"qty": tx.quantity, "price": unit_cost, "date": tx.date, "source": "买入"})
        elif tx.category in {"红股入账", "新股入账", "转托管入", "证券转入"}:
            lots[tx.code].append({"qty": tx.quantity, "price": None, "date": tx.date, "source": tx.category})
        elif tx.category == "证券卖出":
            remaining = tx.quantity
            unit_proceeds = tx.amount / tx.quantity if tx.quantity else tx.price
            while remaining > 1e-8 and lots[tx.code]:
                lot = lots[tx.code][0]
                qty = min(remaining, lot["qty"])
                record = {
                    "code": tx.code,
                    "name": tx.name,
                    "buy_date": lot["date"],
                    "sell_date": tx.date,
                    "quantity": qty,
                    "buy_price": lot["price"],
                    "sell_price": unit_proceeds,
                    "known_cost": lot["price"] is not None,
                }
                if lot["price"] is not None:
                    record["pnl"] = (unit_proceeds - lot["price"]) * qty
                    record["return"] = unit_proceeds / lot["price"] - 1
                    record["holding_days"] = (
                        datetime.strptime(tx.date, "%Y%m%d") - datetime.strptime(lot["date"], "%Y%m%d")
                    ).days
                matched.append(record)
                lot["qty"] -= qty
                remaining -= qty
                if lot["qty"] <= 1e-8:
                    lots[tx.code].popleft()
        elif tx.category in {"转托管出", "证券转出"}:
            remaining = tx.quantity
            while remaining > 1e-8 and lots[tx.code]:
                lot = lots[tx.code][0]
                qty = min(remaining, lot["qty"])
                lot["qty"] -= qty
                remaining -= qty
                if lot["qty"] <= 1e-8:
                    lots[tx.code].popleft()

    known = [row for row in matched if row.get("known_cost")]
    stats: dict[str, float] = {}
    if known:
        pnls = [row["pnl"] for row in known]
        days = [row["holding_days"] for row in known]
        weights = [row["quantity"] * row["buy_price"] for row in known]
        stats = {
            "matched_lots": len(known),
            "matched_cost": sum(weights),
            "realized_pnl": sum(pnls),
            "win_rate": sum(pnl > 0 for pnl in pnls) / len(pnls),
            "profit_factor": sum(max(pnl, 0) for pnl in pnls) / max(1e-9, abs(sum(min(pnl, 0) for pnl in pnls))),
            "median_holding_days": median(days),
            "avg_holding_days": sum(days) / len(days),
            "within_1d": sum(day <= 1 for day in days) / len(days),
            "within_5d": sum(day <= 5 for day in days) / len(days),
            "within_20d": sum(day <= 20 for day in days) / len(days),
        }
    return matched, stats


def tencent_symbol(code: str) -> str:
    if code == "000300":
        return "sh000300"
    if len(code) == 5:
        return f"hk{code}"
    if code.startswith(("5", "6", "110", "111", "113", "118")):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


def fetch_history(
    session: requests.Session, code: str, start: str, end: str
) -> tuple[str, list[dict], str | None]:
    if not code or code.startswith("7"):
        return code, [], "申购/配售代码不抓取行情"
    symbol = tencent_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},400,"}
    last_error = ""
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=20)
            response.raise_for_status()
            payload = (response.json().get("data") or {}).get(symbol) or {}
            klines = payload.get("day") or payload.get("qfqday")
            if not klines:
                return code, [], "无行情数据"
            rows = []
            previous_close = None
            for values in klines:
                close = float(values[2])
                rows.append(
                    {
                        "date": values[0].replace("-", ""),
                        "open": float(values[1]),
                        "close": close,
                        "high": float(values[3]),
                        "low": float(values[4]),
                        "volume": float(values[5]),
                        "amount": 0.0,
                        "pct": (close / previous_close - 1) * 100 if previous_close else 0.0,
                        "turnover": 0.0,
                    }
                )
                previous_close = close
            return code, rows, None
        except Exception as exc:  # noqa: BLE001 - recorded in output for audit
            last_error = str(exc)
            time.sleep(0.15 * (attempt + 1))
    return code, [], last_error


def load_market_histories(statement: Statement) -> tuple[dict[str, list[dict]], dict[str, str]]:
    turnover_by_code: dict[str, float] = defaultdict(float)
    for tx in statement.transactions:
        if tx.code and tx.category in {"证券买入", "证券卖出"}:
            turnover_by_code[tx.code] += tx.trade_notional
    # The quote endpoint is deliberately sampled by economic importance.  The
    # top 50 names cover the great majority of traded notional and avoid API
    # throttling that makes a 150+ symbol request unreliable.
    ranked_codes = sorted(turnover_by_code, key=turnover_by_code.get, reverse=True)
    holding_codes = {holding.code for holding in statement.holdings if holding.code}
    codes = sorted(set(ranked_codes[:50]) | holding_codes | {"588200"})
    histories: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    session = requests.Session()
    session.trust_env = False
    try:
        for code in codes:
            code, rows, error = fetch_history(session, code, "20250601", "20260718")
            if rows:
                histories[code] = rows
            if error:
                errors[code] = error
    finally:
        session.close()
    return histories, errors


def timing_analysis(statement: Statement, histories: dict[str, list[dict]]) -> dict:
    samples: list[dict] = []
    for tx in statement.transactions:
        if tx.category not in {"证券买入", "证券卖出"} or tx.code not in histories:
            continue
        rows = histories[tx.code]
        index = next((i for i, row in enumerate(rows) if row["date"] == tx.date), None)
        if index is None:
            continue
        row = rows[index]
        prior = rows[max(0, index - 20) : index]
        record = {
            "date": tx.date,
            "category": tx.category,
            "code": tx.code,
            "name": tx.name,
            "notional": tx.trade_notional,
            "intraday_location": (tx.price - row["low"]) / (row["high"] - row["low"]) if row["high"] > row["low"] else 0.5,
            "above_ma20": tx.price > sum(x["close"] for x in prior) / len(prior) if prior else None,
            "near_20d_high": tx.price >= max(x["high"] for x in prior) * 0.98 if prior else None,
        }
        for horizon in (5, 20):
            if index + horizon < len(rows):
                future_close = rows[index + horizon]["close"]
                record[f"ret_{horizon}d"] = future_close / tx.price - 1
                window = rows[index + 1 : index + horizon + 1]
                record[f"mfe_{horizon}d"] = max(x["high"] for x in window) / tx.price - 1
                record[f"mae_{horizon}d"] = min(x["low"] for x in window) / tx.price - 1
        samples.append(record)

    def summarize(category: str) -> dict:
        rows = [row for row in samples if row["category"] == category]
        out: dict[str, float] = {"samples": len(rows)}
        if not rows:
            return out
        total_weight = sum(row["notional"] for row in rows)
        out["avg_intraday_location"] = sum(row["intraday_location"] for row in rows) / len(rows)
        out["weighted_intraday_location"] = sum(row["intraday_location"] * row["notional"] for row in rows) / total_weight
        out["above_ma20_rate"] = sum(bool(row["above_ma20"]) for row in rows if row["above_ma20"] is not None) / max(
            1, sum(row["above_ma20"] is not None for row in rows)
        )
        out["near_20d_high_rate"] = sum(bool(row["near_20d_high"]) for row in rows if row["near_20d_high"] is not None) / max(
            1, sum(row["near_20d_high"] is not None for row in rows)
        )
        for horizon in (5, 20):
            key = f"ret_{horizon}d"
            available = [row for row in rows if key in row]
            if available:
                weight = sum(row["notional"] for row in available)
                out[f"avg_ret_{horizon}d"] = sum(row[key] for row in available) / len(available)
                out[f"weighted_ret_{horizon}d"] = sum(row[key] * row["notional"] for row in available) / weight
                out[f"avg_mfe_{horizon}d"] = sum(row[f"mfe_{horizon}d"] for row in available) / len(available)
                out[f"avg_mae_{horizon}d"] = sum(row[f"mae_{horizon}d"] for row in available) / len(available)
                out[f"available_{horizon}d"] = len(available)
        return out

    return {"buys": summarize("证券买入"), "sells": summarize("证券卖出"), "samples": samples}


def aggregate_metrics(statement: Statement, fifo_stats: dict, matched: list[dict], timing: dict, history_errors: dict) -> dict:
    buys = [tx for tx in statement.transactions if tx.category == "证券买入"]
    sells = [tx for tx in statement.transactions if tx.category == "证券卖出"]
    all_trade_dates = {tx.date for tx in buys + sells}
    bank_in = sum(tx.amount for tx in statement.transactions if tx.category == "银行转证券")
    bank_out = -sum(tx.amount for tx in statement.transactions if tx.category == "证券转银行")
    fees = sum(tx.commission + tx.stamp_tax + tx.transfer_fee for tx in statement.transactions)
    unrealized = []
    for h in statement.holdings:
        if h.quantity and h.cost:
            pnl = (h.price - h.cost) * h.quantity
            unrealized.append({"code": h.code, "name": h.name, "pnl": pnl, "return": h.price / h.cost - 1, "value": h.value})

    by_security: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "count": 0})
    for tx in buys + sells:
        key = (tx.code, tx.name)
        by_security[key]["count"] += 1
        if tx.category == "证券买入":
            by_security[key]["buy"] += tx.trade_notional
        else:
            by_security[key]["sell"] += tx.trade_notional
    active = sorted(
        [
            {"code": code, "name": name, **values, "turnover": values["buy"] + values["sell"]}
            for (code, name), values in by_security.items()
        ],
        key=lambda row: row["turnover"],
        reverse=True,
    )

    known = [row for row in matched if row.get("known_cost")]
    by_code_pnl: dict[tuple[str, str], float] = defaultdict(float)
    for row in known:
        by_code_pnl[(row["code"], row["name"])] += row["pnl"]
    pnl_rank = sorted(
        [{"code": code, "name": name, "pnl": pnl} for (code, name), pnl in by_code_pnl.items()],
        key=lambda row: row["pnl"],
        reverse=True,
    )

    same_day_pairs = set()
    buy_keys = {(tx.date, tx.code) for tx in buys}
    for tx in sells:
        if (tx.date, tx.code) in buy_keys:
            same_day_pairs.add((tx.date, tx.code, tx.name))

    return {
        "source": statement.source.name,
        "query_range": statement.query_range,
        "pages": statement.pages,
        "transactions": len(statement.transactions),
        "unmatched_date_lines": len(statement.unmatched_date_lines),
        "buy_orders": len(buys),
        "sell_orders": len(sells),
        "trade_days": len(all_trade_dates),
        "unique_securities": len({tx.code for tx in buys + sells}),
        "buy_notional": sum(tx.trade_notional for tx in buys),
        "sell_notional": sum(tx.trade_notional for tx in sells),
        "turnover": sum(tx.trade_notional for tx in buys + sells),
        "fees_and_taxes": fees,
        "bank_in": bank_in,
        "bank_out": bank_out,
        "net_bank_flow": bank_in - bank_out,
        "same_day_roundtrips": len(same_day_pairs),
        "same_day_examples": sorted([{"date": d, "code": c, "name": n} for d, c, n in same_day_pairs], key=lambda x: x["date"], reverse=True)[:20],
        "current_security_value": statement.security_value,
        "current_cash": statement.cash_balance,
        "current_total_assets": statement.total_assets,
        "unexplained_asset_gap": statement.total_assets - statement.security_value - statement.cash_balance,
        "current_unrealized_pnl": sum(row["pnl"] for row in unrealized),
        "current_holdings": sorted(unrealized, key=lambda row: row["value"], reverse=True),
        "fifo": fifo_stats,
        "top_realized_winners": pnl_rank[:10],
        "top_realized_losers": list(reversed(pnl_rank[-10:])),
        "most_active": active[:20],
        "timing": {"buys": timing["buys"], "sells": timing["sells"]},
        "history_coverage": {
            "codes_with_data": len({sample["code"] for sample in timing["samples"]}),
            "failed_codes": history_errors,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--password", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/broker_statements"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    statements = [parse_statement(path, args.password) for path in args.files]
    same_content = len({statement.normalized_hash for statement in statements}) == 1
    for index, statement in enumerate(statements, start=1):
        duplicate_note = "两份PDF除打印时间等元数据外，账户、持仓和流水内容一致。" if same_content else ""
        output_path = args.output / f"电子对账单_{index}.md"
        output_path.write_text(statement_markdown(statement, duplicate_note), encoding="utf-8")

    canonical = statements[0]
    matched, fifo_stats = fifo_analysis(canonical)
    histories, history_errors = load_market_histories(canonical)
    timing = timing_analysis(canonical, histories)
    metrics = aggregate_metrics(canonical, fifo_stats, matched, timing, history_errors)
    metrics["two_pdfs_same_records"] = same_content
    metrics["normalized_hashes"] = [statement.normalized_hash for statement in statements]
    (args.output / "analysis_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
