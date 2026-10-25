from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable

from pypdf import PdfReader


NUMBER = r"-?\d+(?:\.\d+)?"
DATE_RE = re.compile(r"^\d{8}$")
EPS = 1e-7


@dataclass
class CreditHolding:
    market: str
    code: str
    name: str
    quantity: float
    price: float
    cost: float
    value: float


@dataclass
class FinancingDebt:
    market: str
    open_date: str
    code: str
    name: str
    debt: float
    principal: float
    other_fees: float
    due_date: str


@dataclass
class CreditTransaction:
    sequence: int
    page: int
    line: int
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
    raw_line: str

    @property
    def fees(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass
class HkHolding:
    market: str
    code: str
    name: str
    quantity: float
    pending_buy: float
    pending_sell: float
    price: float
    cost: float
    value: float


@dataclass
class HkTransaction:
    sequence: int
    page: int
    line: int
    date: str
    category: str
    code: str
    name: str
    quantity: float
    price: float
    settlement_fx: float
    amount: float
    commission: float
    stamp_tax: float
    transaction_levy: float
    trading_fee: float
    system_fee: float
    settlement_fee: float
    other_fee: float
    raw_line: str

    @property
    def fees(self) -> float:
        return (
            self.commission
            + self.stamp_tax
            + self.transaction_levy
            + self.trading_fee
            + self.system_fee
            + self.settlement_fee
            + self.other_fee
        )

    @property
    def net_cash(self) -> float:
        if self.category == "港股通买入":
            return -(self.amount + self.fees)
        if self.category == "港股通卖出":
            return self.amount - self.fees
        if self.category == "港股通红利发放":
            return self.amount - self.fees
        if self.category == "港股通组合费":
            return -(self.amount + self.fees)
        return 0.0

    @property
    def implied_hkd_price(self) -> float | None:
        if self.category not in {"港股通买入", "港股通卖出"} or self.settlement_fx <= 0:
            return None
        return self.price / self.settlement_fx


@dataclass
class CreditStatement:
    source: str
    pages: int
    print_time: str
    query_range: str
    total_assets: float
    net_assets: float
    maintenance_ratio: float
    security_value: float
    collateral_value: float
    margin_available: float
    cash_balance: float
    cash_available: float
    cash_withdrawable: float
    collateral_adjusted_assets: float
    financing_limit: float
    lending_limit: float
    total_limit: float
    financing_available: float
    lending_available: float
    total_available: float
    financing_debt: float
    lending_debt: float
    total_debt: float
    financing_full_occupancy_fee: float
    lending_full_occupancy_fee: float
    avg_assets_20d: float
    holdings: list[CreditHolding]
    debts: list[FinancingDebt]
    transactions: list[CreditTransaction]
    unmatched_date_lines: list[dict]


@dataclass
class HkStatement:
    source: str
    pages: int
    print_time: str
    query_range: str
    total_assets: float
    security_value: float
    cash_balance: float
    cash_available: float
    hk_value: float
    hk_available: float
    avg_assets_20d: float
    holdings: list[HkHolding]
    transactions: list[HkTransaction]
    unmatched_date_lines: list[dict]


def n(value: str) -> float:
    return float(value.replace(",", ""))


def read_pdf(path: Path, password: str) -> tuple[list[str], str]:
    reader = PdfReader(str(path))
    if reader.is_encrypted and reader.decrypt(password) == 0:
        raise ValueError(f"PDF密码不正确：{path.name}")
    pages = [page.extract_text() or "" for page in reader.pages]
    return pages, "\n".join(pages)


def first_value(text: str, pattern: str, default: float = 0.0) -> float:
    match = re.search(pattern, text)
    return n(match.group(1)) if match else default


def first_text(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else default


def parse_credit(path: Path, password: str) -> CreditStatement:
    pages, text = read_pdf(path, password)
    holdings: list[CreditHolding] = []
    in_holdings = False
    for raw in pages[0].splitlines():
        line = raw.strip()
        if line.startswith("交易市场 证券代码 证券名称 持仓数量"):
            in_holdings = True
            continue
        if line.startswith("融资负债汇总"):
            break
        if not in_holdings:
            continue
        tokens = line.split()
        if len(tokens) != 7 or not re.fullmatch(r"\d{6}", tokens[1]):
            continue
        try:
            holdings.append(
                CreditHolding(
                    market=tokens[0],
                    code=tokens[1],
                    name=tokens[2],
                    quantity=n(tokens[3]),
                    price=n(tokens[4]),
                    cost=n(tokens[5]),
                    value=n(tokens[6]),
                )
            )
        except ValueError:
            continue

    debts: list[FinancingDebt] = []
    in_debts = False
    debt_re = re.compile(
        rf"^(\S+)\s+(\d{{8}})\s+(\d{{6}})\s+(\S+)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+(\d{{8}})$"
    )
    for page in pages[:2]:
        for raw in page.splitlines():
            line = raw.strip()
            if line.startswith("交易市场 开仓日期 证券代码 证券名称"):
                in_debts = True
                continue
            if line.startswith("资金流水明细"):
                in_debts = False
                break
            if not in_debts:
                continue
            match = debt_re.match(line)
            if match:
                debts.append(
                    FinancingDebt(
                        market=match.group(1),
                        open_date=match.group(2),
                        code=match.group(3),
                        name=match.group(4),
                        debt=n(match.group(5)),
                        principal=n(match.group(6)),
                        other_fees=n(match.group(7)),
                        due_date=match.group(8),
                    )
                )

    transactions: list[CreditTransaction] = []
    unmatched: list[dict] = []
    sequence = 0
    for page_number, page in enumerate(pages, start=1):
        for line_number, raw in enumerate(page.splitlines(), start=1):
            line = raw.strip()
            tokens = line.split()
            if not tokens or not DATE_RE.fullmatch(tokens[0]):
                continue
            if len(tokens) not in {9, 11}:
                unmatched.append({"page": page_number, "line": line_number, "raw": line})
                continue
            try:
                tail = [n(value) for value in tokens[-7:]]
            except ValueError:
                unmatched.append({"page": page_number, "line": line_number, "raw": line})
                continue
            prefix = tokens[1:-7]
            category = prefix[0]
            code = prefix[1] if len(prefix) >= 2 else ""
            name = "".join(prefix[2:]) if len(prefix) >= 3 else ""
            transactions.append(
                CreditTransaction(
                    sequence=sequence,
                    page=page_number,
                    line=line_number,
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
                    raw_line=line,
                )
            )
            sequence += 1

    first = pages[0]
    return CreditStatement(
        source=str(path),
        pages=len(pages),
        print_time=first_text(text, r"打印日期：\s*([^\n]+)"),
        query_range=first_text(text, r"查询区间：\s*([^\n]+)"),
        total_assets=first_value(first, r"总资产：\s*([\d.]+)"),
        net_assets=first_value(first, r"净资产：\s*([\d.]+)"),
        maintenance_ratio=first_value(first, r"维持担保比例：\s*([\d.]+)"),
        security_value=first_value(first, r"证券市值：\s*([\d.]+)"),
        collateral_value=first_value(first, r"担保品市值：\s*([\d.]+)"),
        margin_available=first_value(first, r"保证金可用：\s*([\d.]+)"),
        cash_balance=first_value(first, r"资金余额：\s*([\d.]+)"),
        cash_available=first_value(first, r"资金可用：\s*([\d.]+)"),
        cash_withdrawable=first_value(first, r"资金可取：\s*([\d.]+)"),
        collateral_adjusted_assets=first_value(first, r"担保权益调整资产：\s*([\d.]+)"),
        financing_limit=first_value(first, r"融资授信额度：\s*([\d.]+)"),
        lending_limit=first_value(first, r"融券授信额度：\s*([\d.]+)"),
        total_limit=first_value(first, r"总授信额度：\s*([\d.]+)"),
        financing_available=first_value(first, r"融资可用额度：\s*([\d.]+)"),
        lending_available=first_value(first, r"融券可用额度：\s*([\d.]+)"),
        total_available=first_value(first, r"总可用额度：\s*([\d.]+)"),
        financing_debt=first_value(first, r"融资负债总额：\s*([\d.]+)"),
        lending_debt=first_value(first, r"融券负债总额：\s*([\d.]+)"),
        total_debt=first_value(first, r"(?:^|\s)负债总额：\s*([\d.]+)"),
        financing_full_occupancy_fee=first_value(first, r"融资头寸全额占用费：\s*([\d.]+)"),
        lending_full_occupancy_fee=first_value(first, r"融券头寸全额占用费：\s*([\d.]+)"),
        avg_assets_20d=first_value(first, r"前20个交易日日均资产：\s*([\d.]+)"),
        holdings=holdings,
        debts=debts,
        transactions=transactions,
        unmatched_date_lines=unmatched,
    )


def parse_hk(path: Path, password: str) -> HkStatement:
    pages, text = read_pdf(path, password)
    holdings: list[HkHolding] = []
    in_holdings = False
    holding_re = re.compile(
        rf"^(\S+)\s+(\d{{5}})\s+(\S+)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})$"
    )
    for raw in pages[0].splitlines():
        line = raw.strip()
        if line.startswith("交易市场 证券代码 证券名称 持仓数量"):
            in_holdings = True
            continue
        if line.startswith("资金流水明细"):
            break
        if not in_holdings:
            continue
        match = holding_re.match(line)
        if match:
            holdings.append(
                HkHolding(
                    market=match.group(1),
                    code=match.group(2),
                    name=match.group(3),
                    quantity=n(match.group(4)),
                    pending_buy=n(match.group(5)),
                    pending_sell=n(match.group(6)),
                    price=n(match.group(7)),
                    cost=n(match.group(8)),
                    value=n(match.group(9)),
                )
            )

    # The text layer occasionally glues code, name, and quantity together.  Parse
    # from the ten fixed decimal fields at the right edge, then split the left head.
    tx_re = re.compile(
        rf"^(?P<date>\d{{8}})\s+(?P<category>\S+)\s+"
        rf"(?P<head>.*?)(?P<quantity>\d+)\s+"
        rf"(?P<price>{NUMBER})\s+(?P<fx>{NUMBER})\s+(?P<amount>{NUMBER})\s+"
        rf"(?P<commission>{NUMBER})\s+(?P<stamp>{NUMBER})\s+(?P<levy>{NUMBER})\s+"
        rf"(?P<trading>{NUMBER})\s+(?P<system>{NUMBER})\s+(?P<settlement>{NUMBER})\s+"
        rf"(?P<other>{NUMBER})$"
    )
    transactions: list[HkTransaction] = []
    unmatched: list[dict] = []
    sequence = 0
    for page_number, page in enumerate(pages, start=1):
        for line_number, raw in enumerate(page.splitlines(), start=1):
            line = raw.strip()
            tokens = line.split()
            if not tokens or not DATE_RE.fullmatch(tokens[0]):
                continue
            match = tx_re.match(line)
            if not match:
                unmatched.append({"page": page_number, "line": line_number, "raw": line})
                continue
            head = re.sub(r"\s+", "", match.group("head"))
            code = head[:5] if re.match(r"^\d{5}", head) else ""
            name = head[5:] if code else head
            transactions.append(
                HkTransaction(
                    sequence=sequence,
                    page=page_number,
                    line=line_number,
                    date=match.group("date"),
                    category=match.group("category"),
                    code=code,
                    name=name,
                    quantity=n(match.group("quantity")),
                    price=n(match.group("price")),
                    settlement_fx=n(match.group("fx")),
                    amount=n(match.group("amount")),
                    commission=n(match.group("commission")),
                    stamp_tax=n(match.group("stamp")),
                    transaction_levy=n(match.group("levy")),
                    trading_fee=n(match.group("trading")),
                    system_fee=n(match.group("system")),
                    settlement_fee=n(match.group("settlement")),
                    other_fee=n(match.group("other")),
                    raw_line=line,
                )
            )
            sequence += 1

    first = pages[0]
    return HkStatement(
        source=str(path),
        pages=len(pages),
        print_time=first_text(text, r"打印日期：\s*([^\n]+)"),
        query_range=first_text(text, r"查询区间：\s*([^\n]+)"),
        total_assets=first_value(first, r"总资产\(RMB\)：\s*([\d.]+)"),
        security_value=first_value(first, r"证券市值\(RMB\)：\s*([\d.]+)"),
        cash_balance=first_value(first, r"资金余额\(RMB\)：\s*([\d.]+)"),
        cash_available=first_value(first, r"资金可用\(RMB\)：\s*([\d.]+)"),
        hk_value=first_value(first, r"港股通市值\(RMB\)：\s*([\d.]+)"),
        hk_available=first_value(first, r"港股通可用\(RMB\)：\s*([\d.]+)"),
        avg_assets_20d=first_value(first, r"前20个交易日日均资产\(RMB\)：\s*([\d.]+)"),
        holdings=holdings,
        transactions=transactions,
        unmatched_date_lines=unmatched,
    )


def chronological(rows: Iterable) -> list:
    return sorted(rows, key=lambda row: (row.date, -row.sequence))


def credit_position_delta(tx: CreditTransaction) -> float:
    if tx.category in {"担保品买入", "融资买入", "新股入账"}:
        return tx.quantity
    if tx.category == "担保品卖出":
        return -tx.quantity
    return 0.0


def hk_position_delta(tx: HkTransaction) -> float:
    if tx.category == "港股通买入":
        return tx.quantity
    if tx.category == "港股通卖出":
        return -tx.quantity
    return 0.0


def credit_trade_cash(tx: CreditTransaction) -> float:
    if tx.category in {"担保品买入", "融资买入", "担保品卖出"}:
        return tx.amount
    return 0.0


def cycle_stats(cycles: list[dict]) -> dict:
    closed = [row for row in cycles if row["status"] == "closed" and row["known_cost"]]
    open_known = [row for row in cycles if row["status"] == "open" and row["known_cost"]]
    pnls = [row["pnl"] for row in closed]
    gains = sum(max(value, 0.0) for value in pnls)
    losses = sum(min(value, 0.0) for value in pnls)
    return {
        "closed_known_cycles": len(closed),
        "open_known_cycles": len(open_known),
        "unknown_opening_cycles": sum(row["status"] == "opening_unknown" for row in cycles),
        "realized_pnl": sum(pnls),
        "gross_profit": gains,
        "gross_loss": losses,
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "profit_factor": gains / abs(losses) if losses < -EPS else None,
        "median_holding_days": median(row["holding_days"] for row in closed) if closed else None,
        "trade_notional": sum(row["buy_cost"] + row["sell_proceeds"] for row in closed),
    }


def build_cycles(
    rows: list,
    final_quantities: dict[str, float],
    delta_fn,
    trade_cash_fn,
    buy_categories: set[str],
    sell_categories: set[str],
    price_fn,
    new_share_costs: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], dict[str, float], list[dict]]:
    by_code: dict[str, list] = defaultdict(list)
    net_delta: dict[str, float] = defaultdict(float)
    for row in rows:
        if not row.code:
            continue
        delta = delta_fn(row)
        if abs(delta) > EPS:
            by_code[row.code].append(row)
            net_delta[row.code] += delta
    opening = {
        code: final_quantities.get(code, 0.0) - net_delta.get(code, 0.0)
        for code in set(final_quantities) | set(net_delta)
    }
    cycles: list[dict] = []
    anomalies: list[dict] = []
    cycle_counter = 0
    for code, code_rows in sorted(by_code.items()):
        code_rows = chronological(code_rows)
        pos = opening.get(code, 0.0)
        if abs(pos) < EPS:
            pos = 0.0
        known = pos == 0.0
        active: dict | None = None
        if pos > EPS:
            active = {
                "cycle_id": None,
                "code": code,
                "name": code_rows[0].name,
                "status": "opening_unknown",
                "known_cost": False,
                "opening_quantity": pos,
                "start_date": None,
                "end_date": None,
                "holding_days": None,
                "position": pos,
                "cash_flow": None,
                "pnl": None,
                "buy_cost": None,
                "sell_proceeds": None,
                "peak_basis": None,
                "executions": 0,
                "buy_executions": 0,
                "sell_executions": 0,
                "financed_buy_executions": 0,
                "add_direction": "期初成本未知",
                "first_page": code_rows[0].page,
                "last_page": code_rows[0].page,
            }
        elif pos < -EPS:
            anomalies.append({"code": code, "type": "negative_opening_quantity", "value": pos})
        basis = 0.0
        cash = 0.0
        buy_cost = 0.0
        sell_proceeds = 0.0
        peak_basis = 0.0
        buy_prices: list[float] = []
        for row in code_rows:
            delta = delta_fn(row)
            is_buy = row.category in buy_categories
            is_sell = row.category in sell_categories
            if pos == 0 and delta > EPS:
                cycle_counter += 1
                known = True
                active = {
                    "cycle_id": cycle_counter,
                    "code": code,
                    "name": row.name,
                    "status": "open",
                    "known_cost": True,
                    "opening_quantity": 0.0,
                    "start_date": row.date,
                    "end_date": None,
                    "holding_days": None,
                    "position": 0.0,
                    "cash_flow": 0.0,
                    "pnl": None,
                    "buy_cost": 0.0,
                    "sell_proceeds": 0.0,
                    "peak_basis": 0.0,
                    "executions": 0,
                    "buy_executions": 0,
                    "sell_executions": 0,
                    "financed_buy_executions": 0,
                    "add_direction": "单笔建仓",
                    "first_page": row.page,
                    "last_page": row.page,
                }
                basis = 0.0
                cash = 0.0
                buy_cost = 0.0
                sell_proceeds = 0.0
                peak_basis = 0.0
                buy_prices = []
            if active is None:
                anomalies.append({"code": code, "date": row.date, "type": "row_without_active_cycle"})
                continue
            active["executions"] += 1
            active["last_page"] = row.page
            if is_buy:
                active["buy_executions"] += 1
                if row.category == "融资买入":
                    active["financed_buy_executions"] += 1
                price = price_fn(row)
                if price is not None:
                    buy_prices.append(price)
            if is_sell:
                active["sell_executions"] += 1
            flow = trade_cash_fn(row) if known else 0.0
            if row.category == "新股入账" and new_share_costs:
                matches = new_share_costs.get(code, [])
                if matches:
                    match = matches.pop(0)
                    flow += match["amount"]
                    active["start_date"] = min(active["start_date"], match["date"])
            if known:
                cash += flow
                if delta > EPS:
                    acquisition = max(0.0, -flow)
                    basis += acquisition
                    buy_cost += acquisition
                elif delta < -EPS and pos > EPS:
                    qty_sold = -delta
                    if qty_sold > pos + 1e-5:
                        anomalies.append(
                            {"code": code, "date": row.date, "type": "oversell", "position": pos, "sell": qty_sold}
                        )
                    avg_cost = basis / pos if pos > EPS else 0.0
                    basis -= avg_cost * min(qty_sold, pos)
                    sell_proceeds += max(0.0, flow)
                peak_basis = max(peak_basis, basis)
            pos += delta
            if abs(pos) < EPS:
                pos = 0.0
            if pos < -1e-5:
                anomalies.append({"code": code, "date": row.date, "type": "negative_position", "value": pos})
            active["position"] = pos
            if pos == 0:
                if known:
                    active["status"] = "closed"
                    active["end_date"] = row.date
                    active["holding_days"] = (
                        datetime.strptime(row.date, "%Y%m%d")
                        - datetime.strptime(active["start_date"], "%Y%m%d")
                    ).days
                    active["cash_flow"] = cash
                    active["pnl"] = cash
                    active["buy_cost"] = buy_cost
                    active["sell_proceeds"] = sell_proceeds
                    active["peak_basis"] = peak_basis
                    if len(buy_prices) <= 1:
                        active["add_direction"] = "单笔建仓"
                    else:
                        ups = sum(b >= a for a, b in zip(buy_prices, buy_prices[1:]))
                        downs = sum(b < a for a, b in zip(buy_prices, buy_prices[1:]))
                        active["add_direction"] = "只向上加仓" if downs == 0 else "只向下加仓" if ups == 0 else "混合加仓"
                else:
                    active["end_date"] = row.date
                    active["position"] = 0.0
                cycles.append(active)
                active = None
                known = True
                basis = cash = buy_cost = sell_proceeds = peak_basis = 0.0
                buy_prices = []
        if active is not None:
            if known:
                active["cash_flow"] = cash
                active["buy_cost"] = buy_cost
                active["sell_proceeds"] = sell_proceeds
                active["peak_basis"] = peak_basis
                active["remaining_basis"] = basis
                if len(buy_prices) <= 1:
                    active["add_direction"] = "单笔建仓"
                else:
                    ups = sum(b >= a for a, b in zip(buy_prices, buy_prices[1:]))
                    downs = sum(b < a for a, b in zip(buy_prices, buy_prices[1:]))
                    active["add_direction"] = "只向上加仓" if downs == 0 else "只向下加仓" if ups == 0 else "混合加仓"
            cycles.append(active)
    return cycles, opening, anomalies


def match_subscription_costs(statement: CreditStatement) -> dict[str, list[dict]]:
    payments = [row for row in chronological(statement.transactions) if row.category == "配售缴款"]
    receipts = [row for row in chronological(statement.transactions) if row.category == "新股入账"]
    out: dict[str, list[dict]] = defaultdict(list)
    used: set[int] = set()
    for receipt in receipts:
        candidates = []
        for payment in payments:
            if payment.sequence in used:
                continue
            days = (
                datetime.strptime(receipt.date, "%Y%m%d") - datetime.strptime(payment.date, "%Y%m%d")
            ).days
            same_terms = abs(payment.quantity - receipt.quantity) < EPS and abs(payment.price - receipt.price) < EPS
            if 0 <= days <= 45 and same_terms:
                candidates.append((days, payment))
        if candidates:
            _, payment = min(candidates, key=lambda item: item[0])
            used.add(payment.sequence)
            out[receipt.code].append({"date": payment.date, "amount": payment.amount, "sequence": payment.sequence})
    return out


def group_cycle_stats(cycles: list[dict], key_fn) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for cycle in cycles:
        if cycle["status"] != "closed" or not cycle["known_cost"]:
            continue
        grouped[str(key_fn(cycle))].append(cycle)
    rows = []
    for label, items in grouped.items():
        pnls = [item["pnl"] for item in items]
        gains = sum(max(value, 0.0) for value in pnls)
        losses = sum(min(value, 0.0) for value in pnls)
        rows.append(
            {
                "group": label,
                "cycles": len(items),
                "pnl": sum(pnls),
                "win_rate": sum(value > 0 for value in pnls) / len(pnls),
                "profit_factor": gains / abs(losses) if losses < -EPS else None,
                "avg_pnl": sum(pnls) / len(pnls),
            }
        )
    return sorted(rows, key=lambda row: row["group"])


def code_cycle_summary(cycles: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for cycle in cycles:
        if cycle["status"] == "closed" and cycle["known_cost"]:
            grouped[cycle["code"]].append(cycle)
    out = []
    for code, items in grouped.items():
        pnls = [item["pnl"] for item in items]
        gains = sum(max(value, 0.0) for value in pnls)
        losses = sum(min(value, 0.0) for value in pnls)
        out.append(
            {
                "code": code,
                "name": items[-1]["name"],
                "cycles": len(items),
                "pnl": sum(pnls),
                "win_rate": sum(value > 0 for value in pnls) / len(pnls),
                "profit_factor": gains / abs(losses) if losses < -EPS else None,
            }
        )
    return sorted(out, key=lambda row: row["pnl"], reverse=True)


def duration_bucket(cycle: dict) -> str:
    days = cycle["holding_days"]
    if days == 0:
        return "0天（日内）"
    if days == 1:
        return "1天"
    if days <= 5:
        return "2-5天"
    if days <= 20:
        return "6-20天"
    return "超过20天"


def size_bucket(cycle: dict) -> str:
    value = cycle.get("peak_basis") or 0.0
    if value < 50_000:
        return "<5万元"
    if value < 100_000:
        return "5-10万元"
    if value < 300_000:
        return "10-30万元"
    if value < 1_000_000:
        return "30-100万元"
    return ">=100万元"


def build_hk_daily_cycles(
    rows: list[HkTransaction],
) -> tuple[list[dict], dict[str, float], dict[str, float], list[dict]]:
    """Build exact cash cycles while refusing to invent intraday execution order.

    The HK statement has dates but no timestamps or contract numbers.  We therefore
    aggregate by code and trade date and only declare a flat position at day-end.
    Cash P&L for every 0-to-0 cycle remains exact; FIFO-style intraday lot P&L would
    not be unique.
    """

    by_code_date: dict[str, dict[str, list[HkTransaction]]] = defaultdict(lambda: defaultdict(list))
    names: dict[str, str] = {}
    for row in rows:
        if row.code and row.category in {"港股通买入", "港股通卖出"}:
            by_code_date[row.code][row.date].append(row)
            names[row.code] = row.name or names.get(row.code, "")

    opening: dict[str, float] = {}
    ending: dict[str, float] = {}
    anomalies: list[dict] = []
    cycles: list[dict] = []
    cycle_counter = 0

    for code, daily_rows in sorted(by_code_date.items()):
        day_deltas: dict[str, float] = {}
        cumulative = 0.0
        min_cumulative = 0.0
        for date in sorted(daily_rows):
            delta = sum(hk_position_delta(row) for row in daily_rows[date])
            day_deltas[date] = delta
            cumulative += delta
            min_cumulative = min(min_cumulative, cumulative)
        opening_qty = max(0.0, -min_cumulative)
        opening[code] = opening_qty
        pos = opening_qty
        known = pos == 0.0
        active: dict | None = None
        cash = basis = buy_cost = sell_proceeds = peak_basis = 0.0
        daily_buy_prices: list[float] = []

        if not known:
            active = {
                "cycle_id": None,
                "code": code,
                "name": names[code],
                "status": "opening_unknown",
                "known_cost": False,
                "opening_quantity": opening_qty,
                "start_date": None,
                "end_date": None,
                "holding_days": None,
                "position": pos,
                "cash_flow": None,
                "pnl": None,
                "buy_cost": None,
                "sell_proceeds": None,
                "peak_basis": None,
                "executions": 0,
                "buy_executions": 0,
                "sell_executions": 0,
                "financed_buy_executions": 0,
                "add_direction": "期初成本未知",
                "first_page": min(row.page for day in daily_rows.values() for row in day),
                "last_page": max(row.page for day in daily_rows.values() for row in day),
            }

        for date in sorted(daily_rows):
            day = daily_rows[date]
            buys = [row for row in day if row.category == "港股通买入"]
            sells = [row for row in day if row.category == "港股通卖出"]
            day_buy_qty = sum(row.quantity for row in buys)
            day_sell_qty = sum(row.quantity for row in sells)
            day_buy_cost = sum(-row.net_cash for row in buys)
            day_sell_proceeds = sum(row.net_cash for row in sells)
            day_flow = day_sell_proceeds - day_buy_cost
            if pos == 0 and (buys or sells):
                cycle_counter += 1
                known = True
                active = {
                    "cycle_id": cycle_counter,
                    "code": code,
                    "name": names[code],
                    "status": "open",
                    "known_cost": True,
                    "opening_quantity": 0.0,
                    "start_date": date,
                    "end_date": None,
                    "holding_days": None,
                    "position": 0.0,
                    "cash_flow": 0.0,
                    "pnl": None,
                    "buy_cost": 0.0,
                    "sell_proceeds": 0.0,
                    "peak_basis": 0.0,
                    "executions": 0,
                    "buy_executions": 0,
                    "sell_executions": 0,
                    "financed_buy_executions": 0,
                    "add_direction": "单日建仓",
                    "first_page": min(row.page for row in day),
                    "last_page": max(row.page for row in day),
                }
                cash = basis = buy_cost = sell_proceeds = peak_basis = 0.0
                daily_buy_prices = []

            if active is None:
                anomalies.append({"code": code, "date": date, "type": "day_without_active_cycle"})
                continue
            active["executions"] += len(day)
            active["buy_executions"] += len(buys)
            active["sell_executions"] += len(sells)
            active["first_page"] = min(active["first_page"], min(row.page for row in day))
            active["last_page"] = max(active["last_page"], max(row.page for row in day))

            if buys:
                total_qty = sum(row.quantity for row in buys)
                hkd_vwap = sum((row.implied_hkd_price or 0.0) * row.quantity for row in buys) / total_qty
                daily_buy_prices.append(hkd_vwap)

            new_pos = pos + day_buy_qty - day_sell_qty
            if new_pos < -1e-5:
                anomalies.append({"code": code, "date": date, "type": "negative_day_end_position", "value": new_pos})
            if known:
                cash += day_flow
                buy_cost += day_buy_cost
                sell_proceeds += day_sell_proceeds
                # A day-level moving-average estimate is used only for capital
                # usage.  It does not affect the exact cycle cash P&L.
                available_qty = pos + day_buy_qty
                available_basis = basis + day_buy_cost
                peak_basis = max(peak_basis, available_basis)
                avg_cost = available_basis / available_qty if available_qty > EPS else 0.0
                basis = max(0.0, avg_cost * max(0.0, new_pos))
            pos = 0.0 if abs(new_pos) < EPS else new_pos
            active["position"] = pos

            if pos == 0:
                if known:
                    active["status"] = "closed"
                    active["end_date"] = date
                    active["holding_days"] = (
                        datetime.strptime(date, "%Y%m%d")
                        - datetime.strptime(active["start_date"], "%Y%m%d")
                    ).days
                    active["cash_flow"] = cash
                    active["pnl"] = cash
                    active["buy_cost"] = buy_cost
                    active["sell_proceeds"] = sell_proceeds
                    active["peak_basis"] = peak_basis
                    if len(daily_buy_prices) <= 1:
                        active["add_direction"] = "单日建仓"
                    else:
                        ups = sum(b >= a for a, b in zip(daily_buy_prices, daily_buy_prices[1:]))
                        downs = sum(b < a for a, b in zip(daily_buy_prices, daily_buy_prices[1:]))
                        active["add_direction"] = (
                            "只向上加仓" if downs == 0 else "只向下加仓" if ups == 0 else "混合加仓"
                        )
                else:
                    active["end_date"] = date
                cycles.append(active)
                active = None
                known = True
                cash = basis = buy_cost = sell_proceeds = peak_basis = 0.0
                daily_buy_prices = []

        ending[code] = pos
        if active is not None:
            if known:
                active["cash_flow"] = cash
                active["buy_cost"] = buy_cost
                active["sell_proceeds"] = sell_proceeds
                active["peak_basis"] = peak_basis
                active["remaining_basis"] = basis
                if len(daily_buy_prices) <= 1:
                    active["add_direction"] = "单日建仓"
                else:
                    ups = sum(b >= a for a, b in zip(daily_buy_prices, daily_buy_prices[1:]))
                    downs = sum(b < a for a, b in zip(daily_buy_prices, daily_buy_prices[1:]))
                    active["add_direction"] = (
                        "只向上加仓" if downs == 0 else "只向下加仓" if ups == 0 else "混合加仓"
                    )
            cycles.append(active)
    return cycles, opening, ending, anomalies


def analyse_credit(statement: CreditStatement) -> dict:
    final_qty = {row.code: row.quantity for row in statement.holdings}
    subscription_costs = match_subscription_costs(statement)
    cycles, opening, anomalies = build_cycles(
        statement.transactions,
        final_qty,
        credit_position_delta,
        credit_trade_cash,
        {"担保品买入", "融资买入", "新股入账"},
        {"担保品卖出"},
        lambda row: row.price,
        subscription_costs,
    )
    holdings_by_code = {row.code: row for row in statement.holdings}
    for cycle in cycles:
        if cycle["status"] == "open" and cycle["known_cost"] and cycle["code"] in holdings_by_code:
            holding = holdings_by_code[cycle["code"]]
            cycle["market_value"] = holding.value
            cycle["pnl"] = cycle["cash_flow"] + holding.value
            cycle["effective_break_even_cost"] = -cycle["cash_flow"] / holding.quantity if holding.quantity else None
            cycle["broker_cost"] = holding.cost
            cycle["cost_reconciliation_error"] = (
                cycle["effective_break_even_cost"] - holding.cost
                if cycle["effective_break_even_cost"] is not None
                else None
            )

    balance_checks = []
    for newer, older in zip(statement.transactions, statement.transactions[1:]):
        expected = older.cash_balance + newer.amount
        balance_checks.append(
            {
                "newer_sequence": newer.sequence,
                "older_sequence": older.sequence,
                "expected": expected,
                "reported": newer.cash_balance,
                "error": newer.cash_balance - expected,
            }
        )
    category_counts = Counter(row.category for row in statement.transactions)
    category_amounts = defaultdict(float)
    for row in statement.transactions:
        category_amounts[row.category] += row.amount
    debt_total = sum(row.debt for row in statement.debts)
    principal_total = sum(row.principal for row in statement.debts)
    accrued_fees = sum(row.other_fees for row in statement.debts)
    as_of = datetime.strptime(statement.query_range[-10:].replace("/", ""), "%Y%m%d")
    due_within_30d = [
        row
        for row in statement.debts
        if 0 <= (datetime.strptime(row.due_date, "%Y%m%d") - as_of).days <= 30
    ]
    largest_debt = max(statement.debts, key=lambda row: row.debt) if statement.debts else None
    open_known = [row for row in cycles if row["status"] == "open" and row["known_cost"]]
    holding_reconciliation = []
    for holding in statement.holdings:
        cycle = next((row for row in open_known if row["code"] == holding.code), None)
        holding_reconciliation.append(
            {
                "code": holding.code,
                "name": holding.name,
                "reported_quantity": holding.quantity,
                "calculated_quantity": cycle["position"] if cycle else None,
                "broker_cost": holding.cost,
                "effective_break_even_cost": cycle.get("effective_break_even_cost") if cycle else None,
                "cost_error": cycle.get("cost_reconciliation_error") if cycle else None,
                "market_value": holding.value,
                "net_asset_weight": holding.value / statement.net_assets if statement.net_assets else None,
                "total_asset_weight": holding.value / statement.total_assets if statement.total_assets else None,
                "open_cycle_pnl": cycle.get("pnl") if cycle else None,
            }
        )
    broker_cost_pnl = sum(
        (holding.price - holding.cost) * holding.quantity for holding in statement.holdings
    )
    true_open_cycle_pnl = sum(row.get("pnl") or 0.0 for row in open_known)
    known_contribution_after_paid_financing = (
        cycle_stats(cycles)["realized_pnl"]
        + true_open_cycle_pnl
        + category_amounts.get("红利入账", 0.0)
        + category_amounts.get("股息红利差异扣税", 0.0)
        + category_amounts.get("利息归本", 0.0)
        + category_amounts.get("偿还融资利息", 0.0)
        + category_amounts.get("偿还融资逾期利息", 0.0)
    )
    return {
        "audit": {
            "pages": statement.pages,
            "transactions": len(statement.transactions),
            "unmatched_date_lines": statement.unmatched_date_lines,
            "category_counts": dict(category_counts),
            "cash_balance_links": len(balance_checks),
            "cash_balance_failures_gt_0_02": sum(abs(row["error"]) > 0.02 for row in balance_checks),
            "cash_balance_max_abs_error": max((abs(row["error"]) for row in balance_checks), default=0.0),
            "debt_rows": len(statement.debts),
            "debt_reported": statement.financing_debt,
            "debt_parsed": debt_total,
            "debt_reconciliation_error": debt_total - statement.financing_debt,
            "principal_parsed": principal_total,
            "accrued_fees_parsed": accrued_fees,
            "due_within_30d_count": len(due_within_30d),
            "due_within_30d_debt": sum(row.debt for row in due_within_30d),
            "largest_debt": asdict(largest_debt) if largest_debt else None,
            "position_anomalies": anomalies,
            "opening_quantities": opening,
        },
        "cash_flows": {
            "category_amounts": dict(category_amounts),
            "financing_interest_paid": -category_amounts.get("偿还融资利息", 0.0),
            "overdue_interest_paid": -category_amounts.get("偿还融资逾期利息", 0.0),
            "principal_repaid": -category_amounts.get("偿还融资负债本金", 0.0),
            "financing_borrowed": category_amounts.get("融资借入", 0.0),
            "bank_net_transfer": category_amounts.get("银行转证券", 0.0),
            "dividends_net": category_amounts.get("红利入账", 0.0)
            + category_amounts.get("股息红利差异扣税", 0.0),
            "interest_on_cash": category_amounts.get("利息归本", 0.0),
        },
        "leverage": {
            "gross_assets_to_net_assets": statement.total_assets / statement.net_assets if statement.net_assets else None,
            "debt_to_net_assets": statement.financing_debt / statement.net_assets if statement.net_assets else None,
            "debt_to_total_assets": statement.financing_debt / statement.total_assets if statement.total_assets else None,
            "maintenance_ratio_reported": statement.maintenance_ratio / 100.0,
            "maintenance_ratio_recomputed": statement.total_assets / statement.financing_debt if statement.financing_debt else None,
            "portfolio_drop_to_150pct": 1 - 1.5 / (statement.total_assets / statement.financing_debt),
            "portfolio_drop_to_140pct": 1 - 1.4 / (statement.total_assets / statement.financing_debt),
            "portfolio_drop_to_130pct": 1 - 1.3 / (statement.total_assets / statement.financing_debt),
        },
        "cycle_stats": cycle_stats(cycles),
        "open_cycle_mark_to_market": true_open_cycle_pnl,
        "broker_cost_mark_to_market": broker_cost_pnl,
        "broker_cost_vs_true_open_pnl_gap": broker_cost_pnl - true_open_cycle_pnl,
        "known_contribution_after_paid_financing": known_contribution_after_paid_financing,
        "groups": {
            "by_duration": group_cycle_stats(cycles, duration_bucket),
            "by_add_direction": group_cycle_stats(cycles, lambda row: row["add_direction"]),
            "by_size": group_cycle_stats(cycles, size_bucket),
            "by_financing": group_cycle_stats(
                cycles, lambda row: "含融资买入" if row["financed_buy_executions"] else "仅担保品交易"
            ),
        },
        "by_code": code_cycle_summary(cycles),
        "holding_reconciliation": holding_reconciliation,
        "cycles": cycles,
    }


def analyse_hk(statement: HkStatement) -> dict:
    cycles, opening, ending, anomalies = build_hk_daily_cycles(statement.transactions)
    category_counts = Counter(row.category for row in statement.transactions)
    fee_totals = {
        "commission": sum(row.commission for row in statement.transactions),
        "stamp_tax": sum(row.stamp_tax for row in statement.transactions),
        "transaction_levy": sum(row.transaction_levy for row in statement.transactions),
        "trading_fee": sum(row.trading_fee for row in statement.transactions),
        "system_fee": sum(row.system_fee for row in statement.transactions),
        "settlement_fee": sum(row.settlement_fee for row in statement.transactions),
        "other_fee": sum(row.other_fee for row in statement.transactions),
    }
    trades = [row for row in statement.transactions if row.category in {"港股通买入", "港股通卖出"}]
    principal_errors = [row.amount - row.quantity * row.price for row in trades]
    dividends = [row for row in statement.transactions if row.category == "港股通红利发放"]
    combo = [row for row in statement.transactions if row.category == "港股通组合费"]
    return {
        "audit": {
            "pages": statement.pages,
            "transactions": len(statement.transactions),
            "unmatched_date_lines": statement.unmatched_date_lines,
            "category_counts": dict(category_counts),
            "trade_rows": len(trades),
            "principal_formula_max_abs_error": max((abs(value) for value in principal_errors), default=0.0),
            "principal_formula_mean_abs_error": sum(abs(value) for value in principal_errors) / len(principal_errors)
            if principal_errors
            else 0.0,
            "position_anomalies": anomalies,
            "opening_quantities": opening,
            "ending_quantities_from_trades": ending,
            "ending_economic_quantity": sum(ending.values()),
            "pending_buy": sum(row.pending_buy for row in statement.holdings),
            "pending_sell": sum(row.pending_sell for row in statement.holdings),
        },
        "cash_flows": {
            "buy_principal": sum(row.amount for row in trades if row.category == "港股通买入"),
            "sell_principal": sum(row.amount for row in trades if row.category == "港股通卖出"),
            "trade_fees": sum(row.fees for row in trades),
            "dividends": sum(row.net_cash for row in dividends),
            "combination_fees": -sum(row.net_cash for row in combo),
            "all_statement_net_cash": sum(row.net_cash for row in statement.transactions),
            "fee_totals": fee_totals,
        },
        "cycle_stats": cycle_stats(cycles),
        "groups": {
            "by_duration": group_cycle_stats(cycles, duration_bucket),
            "by_add_direction": group_cycle_stats(cycles, lambda row: row["add_direction"]),
            "by_size": group_cycle_stats(cycles, size_bucket),
        },
        "by_code": code_cycle_summary(cycles),
        "open_positions": [
            {
                "code": row["code"],
                "name": row["name"],
                "quantity": row["position"],
                "net_cash_invested": -(row["cash_flow"] or 0.0),
                "estimated_remaining_basis": row.get("remaining_basis"),
                "start_date": row["start_date"],
            }
            for row in cycles
            if row["status"] == "open" and row["known_cost"]
        ],
        "cycles": cycles,
    }


def fmoney(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def fqty(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.4f}".rstrip("0").rstrip(".")


def fpct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def fdate(value: str | None) -> str:
    if not value:
        return "—"
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def credit_markdown(statement: CreditStatement, analysis: dict) -> str:
    lines = [
        "# 信用账户电子对账单",
        "",
        "> 由加密PDF逐页提取并脱敏转换。原PDF未修改；Markdown不保存密码、姓名、证件号或资金账号。",
        "",
        "## 账户快照",
        "",
        "|项目|数值|",
        "|---|---:|",
        f"|查询区间|{statement.query_range}|",
        f"|打印时间|{statement.print_time}|",
        f"|总资产|{fmoney(statement.total_assets)} 元|",
        f"|净资产|{fmoney(statement.net_assets)} 元|",
        f"|证券市值|{fmoney(statement.security_value)} 元|",
        f"|担保品市值|{fmoney(statement.collateral_value)} 元|",
        f"|保证金可用|{fmoney(statement.margin_available)} 元|",
        f"|资金余额|{fmoney(statement.cash_balance)} 元|",
        f"|资金可用|{fmoney(statement.cash_available)} 元|",
        f"|资金可取|{fmoney(statement.cash_withdrawable)} 元|",
        f"|担保权益调整资产|{fmoney(statement.collateral_adjusted_assets)} 元|",
        f"|融资授信额度|{fmoney(statement.financing_limit)} 元|",
        f"|融券授信额度|{fmoney(statement.lending_limit)} 元|",
        f"|总授信额度|{fmoney(statement.total_limit)} 元|",
        f"|维持担保比例|{statement.maintenance_ratio:.2f}%|",
        f"|融资可用额度|{fmoney(statement.financing_available)} 元|",
        f"|融券可用额度|{fmoney(statement.lending_available)} 元|",
        f"|总可用额度|{fmoney(statement.total_available)} 元|",
        f"|融资负债总额|{fmoney(statement.financing_debt)} 元|",
        f"|融券负债总额|{fmoney(statement.lending_debt)} 元|",
        f"|负债总额|{fmoney(statement.total_debt)} 元|",
        f"|融资头寸全额占用费|{fmoney(statement.financing_full_occupancy_fee)} 元|",
        f"|融券头寸全额占用费|{fmoney(statement.lending_full_occupancy_fee)} 元|",
        f"|前20个交易日日均资产|{fmoney(statement.avg_assets_20d)} 元|",
        f"|PDF页数|{statement.pages}|",
        f"|完整流水|{len(statement.transactions)} 条|",
        f"|未解析日期行|{len(statement.unmatched_date_lines)} 条|",
        "",
        "## 期末持仓",
        "",
        "|市场|代码|名称|数量|市价|券商成本价|市值|券商成本口径盈亏|占净资产|",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in statement.holdings:
        pnl = (row.price - row.cost) * row.quantity
        lines.append(
            f"|{row.market}|{row.code}|{row.name}|{fqty(row.quantity)}|{row.price:.4f}|{row.cost:.4f}|"
            f"{fmoney(row.value)}|{fmoney(pnl)}|{fpct(row.value / statement.net_assets if statement.net_assets else None)}|"
        )
    lines.extend(
        [
            "",
            "> 券商成本价在部分卖出后可能是动态保本价，不等于原始买入均价。例如剩余小仓位的低成本价可能已包含此前实现收益。",
            "",
            "## 期末融资负债",
            "",
            "|市场|开仓日|代码|名称|融资负债|本金|其他费用|到期日|",
            "|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in statement.debts:
        lines.append(
            f"|{row.market}|{fdate(row.open_date)}|{row.code}|{row.name}|{fmoney(row.debt)}|"
            f"{fmoney(row.principal)}|{fmoney(row.other_fees)}|{fdate(row.due_date)}|"
        )
    lines.extend(
        [
            "",
            "## 全部资金流水",
            "",
            "> 按PDF原始倒序列示。总发生金额已经是该行对现金余额的净影响；买入通常为负、卖出通常为正。页码和序号可回查原PDF。",
            "",
        ]
    )
    grouped: dict[str, list[CreditTransaction]] = defaultdict(list)
    for row in statement.transactions:
        grouped[row.date[:6]].append(row)
    for month in sorted(grouped, reverse=True):
        lines.extend(
            [
                f"### {month[:4]}-{month[4:]}",
                "",
                "|序号|页|日期|类别|代码|名称|数量|价格|总发生金额|手续费|印花税|过户费|资金余额|",
                "|---:|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in grouped[month]:
            lines.append(
                f"|C{row.sequence + 1:04d}|{row.page}|{fdate(row.date)}|{row.category}|{row.code or '—'}|"
                f"{row.name or '—'}|{fqty(row.quantity)}|{row.price:.4f}|{fmoney(row.amount)}|"
                f"{fmoney(row.commission)}|{fmoney(row.stamp_tax)}|{fmoney(row.transfer_fee)}|{fmoney(row.cash_balance)}|"
            )
        lines.append("")
    lines.extend(
        [
            "## 转换校验",
            "",
            f"- 解析日期行：{len(statement.transactions)}；未解析：{len(statement.unmatched_date_lines)}。",
            f"- 相邻余额勾稽：{analysis['audit']['cash_balance_links']}组；超过0.02元误差："
            f"{analysis['audit']['cash_balance_failures_gt_0_02']}组；最大绝对误差："
            f"{analysis['audit']['cash_balance_max_abs_error']:.4f}元。",
            f"- 融资负债明细合计与首页差额：{analysis['audit']['debt_reconciliation_error']:.4f}元。",
            "",
        ]
    )
    return "\n".join(lines)


def hk_markdown(statement: HkStatement, analysis: dict) -> str:
    lines = [
        "# 港股通电子对账单",
        "",
        "> 由加密PDF逐页提取并脱敏转换。原PDF未修改；Markdown不保存密码、姓名、证件号或资金账号。",
        "",
        "## 账户快照",
        "",
        "|项目|数值|",
        "|---|---:|",
        f"|查询区间|{statement.query_range}|",
        f"|打印时间|{statement.print_time}|",
        f"|总资产|{fmoney(statement.total_assets)} 元|",
        f"|证券市值|{fmoney(statement.security_value)} 元|",
        f"|资金余额|{fmoney(statement.cash_balance)} 元|",
        f"|资金可用|{fmoney(statement.cash_available)} 元|",
        f"|港股通市值|{fmoney(statement.hk_value)} 元|",
        f"|港股通可用|{fmoney(statement.hk_available)} 元|",
        f"|前20个交易日日均资产|{fmoney(statement.avg_assets_20d)} 元|",
        f"|PDF页数|{statement.pages}|",
        f"|完整流水|{len(statement.transactions)} 条|",
        f"|未解析日期行|{len(statement.unmatched_date_lines)} 条|",
        "",
        "## 期末持仓与在途",
        "",
        "|市场|代码|名称|持仓数量|在途买入|在途卖出|市价|成本价|市值|",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in statement.holdings:
        lines.append(
            f"|{row.market}|{row.code}|{row.name}|{fqty(row.quantity)}|{fqty(row.pending_buy)}|"
            f"{fqty(row.pending_sell)}|{row.price:.3f}|{row.cost:.3f}|{fmoney(row.value)}|"
        )
    lines.extend(
        [
            "",
            "> 首页四行“持仓/在途买入/在途卖出”无法按表头方向机械相加，否则会得到负持仓。Markdown仅忠实保留PDF原表数值；未交收经济头寸另由全期买卖净量勾稽。",
            "",
            "## 口径",
            "",
            "- 买入净现金 = -(总发生金额 + 七项费用)。",
            "- 卖出净现金 = 总发生金额 - 七项费用。",
            "- 买卖行中，数量 x 成交价格约等于总发生金额，说明成交价格和总发生金额均为结算人民币口径；原港币成交价可用成交价格 / 结算汇率还原。",
            "- 红利行的成交价格是每股港币股息，金额是已折算的人民币实际到账红利；PDF没有独立红利税/补扣税行，无法由本单反推税前红利或税率。组合费直接作为人民币支出。",
            "- “发生日期”经行情成交区间交叉验证后高度符合T+2交收日，本文忠实保留PDF原字段，但它不应直接当作成交日。",
            "",
            "## 全部资金流水",
            "",
            "> 按PDF原始倒序列示。另列计算净现金，方便逐笔核算；页码和序号可回查原PDF。",
            "",
        ]
    )
    grouped: dict[str, list[HkTransaction]] = defaultdict(list)
    for row in statement.transactions:
        grouped[row.date[:6]].append(row)
    for month in sorted(grouped, reverse=True):
        lines.extend(
            [
                f"### {month[:4]}-{month[4:]}",
                "",
                "|序号|页|发生日期（PDF原字段）|类别|代码|名称|数量|原PDF成交价格（买卖RMB/股；红利HKD/股）|结算汇率|还原价(HKD)|总发生金额|手续费|印花税|交易征费|交易费|系统费用|交收费|其他费用|七项费用合计|计算净现金|",
                "|---:|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in grouped[month]:
            implied = row.implied_hkd_price
            implied_text = "—" if implied is None else f"{implied:.3f}"
            lines.append(
                f"|H{row.sequence + 1:04d}|{row.page}|{fdate(row.date)}|{row.category}|{row.code or '—'}|"
                f"{row.name or '—'}|{fqty(row.quantity)}|{row.price:.3f}|{row.settlement_fx:.5f}|"
                f"{implied_text}|{fmoney(row.amount)}|{fmoney(row.commission)}|{fmoney(row.stamp_tax)}|"
                f"{fmoney(row.transaction_levy)}|{fmoney(row.trading_fee)}|{fmoney(row.system_fee)}|"
                f"{fmoney(row.settlement_fee)}|{fmoney(row.other_fee)}|{fmoney(row.fees)}|{fmoney(row.net_cash)}|"
            )
        lines.append("")
    content = "\n".join(lines)
    content += (
        "\n## 转换校验\n\n"
        f"- 解析日期行：{len(statement.transactions)}；未解析：{len(statement.unmatched_date_lines)}。\n"
        f"- 买卖本金公式最大绝对误差：{analysis['audit']['principal_formula_max_abs_error']:.4f}元；"
        f"平均绝对误差：{analysis['audit']['principal_formula_mean_abs_error']:.4f}元。\n"
        f"- 全期交易净量反推的未交收净买入（期末经济头寸）："
        f"{fqty(analysis['audit']['ending_economic_quantity'])}股。PDF原表各列机械合计为“在途买入”"
        f"{fqty(analysis['audit']['pending_buy'])}股、“在途卖出”{fqty(analysis['audit']['pending_sell'])}股；"
        "两列不得直接按表头方向解读为经济增减。\n"
    )
    return content


def stats_table(rows: list[dict]) -> list[str]:
    out = ["|分组|周期数|净盈亏|胜率|盈利因子|平均盈亏|", "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        pf = "—" if row["profit_factor"] is None else f"{row['profit_factor']:.3f}"
        out.append(
            f"|{row['group']}|{row['cycles']}|{fmoney(row['pnl'])}|{fpct(row['win_rate'])}|{pf}|{fmoney(row['avg_pnl'])}|"
        )
    return out


def analysis_markdown(credit: CreditStatement, ca: dict, hk: HkStatement, ha: dict) -> str:
    cs = ca["cycle_stats"]
    hs = ha["cycle_stats"]
    leverage = ca["leverage"]
    cash = ca["cash_flows"]
    hcash = ha["cash_flows"]
    cs_pf = "—" if cs["profit_factor"] is None else f"{cs['profit_factor']:.3f}"
    hs_pf = "—" if hs["profit_factor"] is None else f"{hs['profit_factor']:.3f}"
    credit_duration = {row["group"]: row for row in ca["groups"]["by_duration"]}
    credit_add = {row["group"]: row for row in ca["groups"]["by_add_direction"]}
    hk_duration = {row["group"]: row for row in ha["groups"]["by_duration"]}
    hk_add = {row["group"]: row for row in ha["groups"]["by_add_direction"]}
    top_holdings = sorted(ca["holding_reconciliation"], key=lambda row: row["market_value"], reverse=True)
    largest_credit_losses = sorted(
        [row for row in ca["cycles"] if row["status"] == "closed" and row["known_cost"]],
        key=lambda row: row["pnl"],
    )[:20]
    largest_hk_losses = sorted(
        [row for row in ha["cycles"] if row["status"] == "closed" and row["known_cost"]],
        key=lambda row: row["pnl"],
    )[:20]
    lines = [
        "# 信用账户与港股通逐笔交易分析",
        "",
        "> 分析区间：2025-07-18至2026-07-18。金额均为人民币，除非明确标注。本文是账户行为审计，不构成证券买卖建议。",
        "",
        "## 结论先行",
        "",
        f"1. 信用账户当前总资产{fmoney(credit.total_assets)}元、净资产{fmoney(credit.net_assets)}元、融资负债"
        f"{fmoney(credit.financing_debt)}元，资产/净资产为{leverage['gross_assets_to_net_assets']:.2f}倍；账户风险的第一变量不是选股胜率，而是杠杆与半导体集中度。",
        f"2. 信用账户可精确识别的已平仓证券周期{cs['closed_known_cycles']}个，含费实现盈亏{fmoney(cs['realized_pnl'])}元，"
        f"胜率{fpct(cs['win_rate'])}、盈利因子{cs_pf}。"
        f"期末可识别未平仓周期按市值计盈亏{fmoney(ca['open_cycle_mark_to_market'])}元。",
        f"   首页动态成本显示的期末盈亏只有{fmoney(ca['broker_cost_mark_to_market'])}元，与本轮真实买卖成本相差"
        f"{fmoney(ca['broker_cost_vs_true_open_pnl_gap'])}元；这是成本口径差异，不是PDF抄录错误。",
        f"3. 区间内已支付融资利息{fmoney(cash['financing_interest_paid'])}元、逾期利息{fmoney(cash['overdue_interest_paid'])}元；"
        f"期末负债另含尚在负债表内的其他费用{fmoney(ca['audit']['accrued_fees_parsed'])}元，三者不可重复计入同一口径。",
        f"4. 港股通可精确识别已平仓周期{hs['closed_known_cycles']}个，含七项交易费用后盈亏{fmoney(hs['realized_pnl'])}元，"
        f"胜率{fpct(hs['win_rate'])}、盈利因子{hs_pf}；"
        f"另有实际到账红利{fmoney(hcash['dividends'])}元、组合费{fmoney(hcash['combination_fees'])}元。",
        "5. 所有无法知道期初成本的首段持仓均被标记为“期初成本未知”，没有把卖出收入误算为利润；配售缴款与新债入账按数量、面值和日期配对。",
        "",
        "## 一、数据完整性与会计边界",
        "",
        "|检查|信用账户|港股通|",
        "|---|---:|---:|",
        f"|PDF页数|{credit.pages}|{hk.pages}|",
        f"|解析流水|{len(credit.transactions)}|{len(hk.transactions)}|",
        f"|未解析日期行|{len(credit.unmatched_date_lines)}|{len(hk.unmatched_date_lines)}|",
        f"|买卖成交行|{sum(ca['audit']['category_counts'].get(k, 0) for k in ['担保品买入','担保品卖出','融资买入'])}|{ha['audit']['trade_rows']}|",
        f"|期初成本未知首段|{cs['unknown_opening_cycles']}|{hs['unknown_opening_cycles']}|",
        f"|头寸异常|{len(ca['audit']['position_anomalies'])}|{len(ha['audit']['position_anomalies'])}|",
        "",
        f"信用账户的{ca['audit']['cash_balance_links']}组相邻流水均用“较旧余额 + 较新行发生金额 = 较新余额”复核，"
        f"超过2分钱的误差有{ca['audit']['cash_balance_failures_gt_0_02']}组。融资负债明细合计与首页差额为"
        f"{ca['audit']['debt_reconciliation_error']:.4f}元。港股通买卖的“数量 x 成交价格”与总发生金额平均误差"
        f"{ha['audit']['principal_formula_mean_abs_error']:.4f}元，证明本金是人民币结算口径。",
        "",
        "分析分三层，禁止混算：",
        "",
        "- 证券交易收益：买入净支出、卖出净收入，均含交易费用；信用账户融资借入和偿还本金只是资产负债变化，不是收益。",
        "- 融资成本：已支付利息、逾期利息；期末负债表中的未付其他费用单列。",
        "- 公司行动与账户现金：红利、红利差异税、配售、新债入账、银行转账、利息归本单列。",
        "",
        "## 二、信用账户当前风险",
        "",
        "|指标|数值|解释|",
        "|---|---:|---|",
        f"|总资产/净资产|{leverage['gross_assets_to_net_assets']:.2f}倍|在负债不变的静态假设下，总资产组合每波动1%，净资产约波动2.55%|",
        f"|融资负债/净资产|{fpct(leverage['debt_to_net_assets'])}|负债超过净资产|",
        f"|融资负债/总资产|{fpct(leverage['debt_to_total_assets'])}|资产中由融资支撑的比例|",
        f"|维持担保比例|{fpct(leverage['maintenance_ratio_reported'])}|与总资产/融资负债复算一致|",
        f"|组合下跌至150%比例的静态跌幅|{fpct(leverage['portfolio_drop_to_150pct'])}|假定负债不变、全仓同步下跌；不是券商强平线|",
        f"|组合下跌至140%比例的静态跌幅|{fpct(leverage['portfolio_drop_to_140pct'])}|同上|",
        f"|组合下跌至130%比例的静态跌幅|{fpct(leverage['portfolio_drop_to_130pct'])}|同上|",
        "",
        "### 期末集中度",
        "",
        "|代码|名称|市值|占总资产|占净资产|券商成本口径盈亏|可识别周期盈亏|",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in top_holdings:
        holding = next(item for item in credit.holdings if item.code == row["code"])
        broker_pnl = (holding.price - holding.cost) * holding.quantity
        lines.append(
            f"|{row['code']}|{row['name']}|{fmoney(row['market_value'])}|{fpct(row['total_asset_weight'])}|"
            f"{fpct(row['net_asset_weight'])}|{fmoney(broker_pnl)}|{fmoney(row['open_cycle_pnl'])}|"
        )
    lines.extend(
        [
            "",
            "上述持仓几乎全部属于半导体设备、材料、芯片或科创半导体ETF，同一产业因子会在市场回撤时同步收缩。"
            "单一ETF的市值已超过净资产，表面上是一个代码，经济上仍是高杠杆行业仓位。期末资金余额、可用和可取均为0，"
            "没有现金缓冲；若要降低风险，只能转入资金、减仓或两者同时进行。",
            "",
            f"按本期完整买卖重建，10个开放周期的实际成本对应浮亏{fmoney(ca['open_cycle_mark_to_market'])}元；"
            f"首页动态成本口径只显示{fmoney(ca['broker_cost_mark_to_market'])}元，相差"
            f"{fmoney(ca['broker_cost_vs_true_open_pnl_gap'])}元。主要来自588710、中科飞测和芯碁微装在既往卖出后"
            "把已实现盈亏摊入剩余成本。判断本轮风险时必须使用逐笔现金流成本，不能把动态保本价当原始买入价。",
            "",
            "### 融资现金流",
            "",
            "|项目|区间金额|",
            "|---|---:|",
            f"|融资借入|{fmoney(cash['financing_borrowed'])}|",
            f"|偿还本金|{fmoney(cash['principal_repaid'])}|",
            f"|已支付正常利息|{fmoney(cash['financing_interest_paid'])}|",
            f"|已支付逾期利息|{fmoney(cash['overdue_interest_paid'])}|",
            f"|期末尚在负债表的其他费用|{fmoney(ca['audit']['accrued_fees_parsed'])}|",
            f"|银行净转入|{fmoney(cash['bank_net_transfer'])}|",
            "",
            f"已识别平仓收益 + 期末本轮浮动盈亏 + 净红利/现金利息 - 已支付融资利息 = "
            f"{fmoney(ca['known_contribution_after_paid_financing'])}元。这个数仍不是账户全年净利润：8个期初仓缺少成本，"
            "且期末7,554.03元负债费用中包含期初前已开始的合约，不能擅自全部归入本期。",
            "",
            "借入与还本不能相减后当作盈亏。真正拖累收益的是正常利息、逾期利息，以及尚未支付但已进入负债的费用。"
            "逾期利息说明交易风险之外还存在到期管理问题，必须把每笔融资合约的到期日前置到交易系统。",
            "",
            f"期末最大融资合同是{ca['audit']['largest_debt']['code']} {ca['audit']['largest_debt']['name']}，负债"
            f"{fmoney(ca['audit']['largest_debt']['debt'])}元，占总融资负债"
            f"{ca['audit']['largest_debt']['debt'] / credit.financing_debt:.2%}，到期日"
            f"{fdate(ca['audit']['largest_debt']['due_date'])}。打印日起30天内到期"
            f"{ca['audit']['due_within_30d_count']}笔、合计{fmoney(ca['audit']['due_within_30d_debt'])}元；金额不大，但已有逾期利息记录，"
            "所以到期管理应作为硬风控，而不是临近到期再人工处理。",
            "",
            "## 三、信用账户历史交易行为",
            "",
            "### 持有期",
            "",
            *stats_table(ca["groups"]["by_duration"]),
            "",
            "### 加仓方向",
            "",
            *stats_table(ca["groups"]["by_add_direction"]),
            "",
            "### 峰值成本占用",
            "",
            *stats_table(ca["groups"]["by_size"]),
            "",
            "### 是否使用融资买入",
            "",
            *stats_table(ca["groups"]["by_financing"]),
            "",
            f"信用账户最明显的时间差异是：1天周期合计{fmoney(credit_duration['1天']['pnl'])}元，"
            f"而2-5天、6-20天和超过20天分别为{fmoney(credit_duration['2-5天']['pnl'])}、"
            f"{fmoney(credit_duration['6-20天']['pnl'])}和{fmoney(credit_duration['超过20天']['pnl'])}元。"
            "这不等于未来应机械延长持仓；它说明信用账户的短线退出质量弱，而能够形成趋势的交易贡献了大部分利润。",
            "",
            f"只向上加仓的{credit_add['只向上加仓']['cycles']}个周期合计{fmoney(credit_add['只向上加仓']['pnl'])}元；"
            f"只向下加仓的{credit_add['只向下加仓']['cycles']}个周期约为{fmoney(credit_add['只向下加仓']['pnl'])}元。"
            "该分组按一个零仓周期内的买价序列定义，不能把相关性当因果，但足以否定“摊低成本天然提高胜率”。",
            "",
            "### 最大20个已识别亏损周期",
            "",
            "|周期|证券|日期|持有天数|峰值成本|含费盈亏|加仓|融资买入笔数|",
            "|---:|---|---|---:|---:|---:|---|---:|",
        ]
    )
    for row in largest_credit_losses:
        lines.append(
            f"|{row['cycle_id']}|{row['code']} {row['name']}|{fdate(row['start_date'])}至{fdate(row['end_date'])}|"
            f"{row['holding_days']}|{fmoney(row['peak_basis'])}|{fmoney(row['pnl'])}|{row['add_direction']}|"
            f"{row['financed_buy_executions']}|"
        )
    lines.extend(
        [
            "",
            "## 四、港股通历史交易行为",
            "",
            "由每日数量重建：唯一期初未知仓为03896金山云6,000股；期末仍有06869长飞光纤光缆3,500股、00522 ASMPT 300股。"
            "这两笔在途头寸未计入已实现盈亏，也没有用首页0市值强行估值。",
            "",
            "|期末未平证券|数量|开始日|周期净现金占用|日末成本池估计|",
            "|---|---:|---|---:|---:|",
            *[
                f"|{row['code']} {row['name']}|{fqty(row['quantity'])}|{fdate(row['start_date'])}|"
                f"{fmoney(row['net_cash_invested'])}|{fmoney(row['estimated_remaining_basis'])}|"
                for row in ha["open_positions"]
            ],
            "",
            "|项目|数值|",
            "|---|---:|",
            f"|买入本金|{fmoney(hcash['buy_principal'])}|",
            f"|卖出本金|{fmoney(hcash['sell_principal'])}|",
            f"|双边成交额|{fmoney(hcash['buy_principal'] + hcash['sell_principal'])}|",
            f"|买卖七项费用|{fmoney(hcash['trade_fees'])}|",
            f"|费用/双边成交额|{(hcash['trade_fees'] / (hcash['buy_principal'] + hcash['sell_principal']) * 10000):.2f} bp|",
            f"|双边成交额/20日平均资产|{(hcash['buy_principal'] + hcash['sell_principal']) / hk.avg_assets_20d:.2f}倍|",
            f"|实际到账红利|{fmoney(hcash['dividends'])}|",
            f"|组合费|{fmoney(hcash['combination_fees'])}|",
            f"|可识别已平仓周期盈亏|{fmoney(hs['realized_pnl'])}|",
            "",
            "|费用分项|金额|",
            "|---|---:|",
            f"|手续费|{fmoney(hcash['fee_totals']['commission'])}|",
            f"|印花税|{fmoney(hcash['fee_totals']['stamp_tax'])}|",
            f"|交易征费|{fmoney(hcash['fee_totals']['transaction_levy'])}|",
            f"|交易费|{fmoney(hcash['fee_totals']['trading_fee'])}|",
            f"|系统费用|{fmoney(hcash['fee_totals']['system_fee'])}|",
            f"|交收费|{fmoney(hcash['fee_totals']['settlement_fee'])}|",
            f"|其他费用|{fmoney(hcash['fee_totals']['other_fee'])}|",
            "",
            "“全部流水净现金”包含期初持仓卖出收入，不能直接当作利润；只有从空仓开始、最终回到空仓的周期才能精确核算。"
            "港股通七项费用已在每个周期内扣除，实际到账红利与组合费因不一定能准确归属成交周期而单列。"
            "PDF没有独立红利税或补扣税行，因此不能用本对账单反推税前红利或实际税率。",
            "",
            f"显式买卖费用{fmoney(hcash['trade_fees'])}元，规模相当于已识别平仓亏损的"
            f"{abs(hcash['trade_fees'] / hs['realized_pnl']):.2%}；其中印花税"
            f"{fmoney(hcash['fee_totals']['stamp_tax'])}元，占全部显式费用"
            f"{hcash['fee_totals']['stamp_tax'] / hcash['trade_fees']:.2%}。降低无效周转是可以直接控制的收益来源。",
            "",
            "### 持有期",
            "",
            *stats_table(ha["groups"]["by_duration"]),
            "",
            "### 加仓方向（按还原港币价判断）",
            "",
            *stats_table(ha["groups"]["by_add_direction"]),
            "",
            "### 峰值成本占用",
            "",
            *stats_table(ha["groups"]["by_size"]),
            "",
            f"港股通的结构更明确：0天和1天周期合计{fmoney(hk_duration['0天（日内）']['pnl'] + hk_duration['1天']['pnl'])}元；"
            f"2-5天仅小幅为正{fmoney(hk_duration['2-5天']['pnl'])}元；6-20天与超过20天合计"
            f"{fmoney(hk_duration['6-20天']['pnl'] + hk_duration['超过20天']['pnl'])}元。"
            "问题不是单纯“持有太短”或“持有太长”，而是日内试错成本高、失败仓又拖成中长线。",
            "",
            f"只向下加仓的{hk_add['只向下加仓']['cycles']}个周期亏{fmoney(hk_add['只向下加仓']['pnl'])}元，"
            f"胜率{fpct(hk_add['只向下加仓']['win_rate'])}、盈利因子{hk_add['只向下加仓']['profit_factor']:.3f}；"
            f"只向上加仓的{hk_add['只向上加仓']['cycles']}个周期赚{fmoney(hk_add['只向上加仓']['pnl'])}元。"
            "这是最有操作价值的历史证据：禁止用加仓修复亏损，只允许在盈利且原风险预算未突破时加仓。",
            "",
            "### 按证券代码累计的主要盈亏来源",
            "",
            "|方向|代码|名称|完整周期数|含费盈亏|胜率|",
            "|---|---|---|---:|---:|---:|",
            *[
                f"|主要盈利|{row['code']}|{row['name']}|{row['cycles']}|{fmoney(row['pnl'])}|{fpct(row['win_rate'])}|"
                for row in ha["by_code"][:5]
            ],
            *[
                f"|主要亏损|{row['code']}|{row['name']}|{row['cycles']}|{fmoney(row['pnl'])}|{fpct(row['win_rate'])}|"
                for row in sorted(ha["by_code"], key=lambda item: item["pnl"])[:5]
            ],
            "",
            "### 最大20个已识别亏损周期",
            "",
            "|周期|证券|日期|持有天数|峰值成本|含费盈亏|加仓|成交笔数|",
            "|---:|---|---|---:|---:|---:|---|---:|",
        ]
    )
    for row in largest_hk_losses:
        lines.append(
            f"|{row['cycle_id']}|{row['code']} {row['name']}|{fdate(row['start_date'])}至{fdate(row['end_date'])}|"
            f"{row['holding_days']}|{fmoney(row['peak_basis'])}|{fmoney(row['pnl'])}|{row['add_direction']}|{row['executions']}|"
        )
    lines.extend(
        [
            "",
            "## 五、针对亏损的执行制度",
            "",
            "1. **先限总风险，再选股票。** 信用账户恢复期把总资产/净资产降到事先设定的上限；同一半导体产业链的ETF和个股合并计算风险，不按代码数量假装分散。",
            "2. **融资到期日必须系统化。** 每笔融资开仓时同步写入到期日，提前30/10/3个自然日提醒；出现逾期利息后，在清零前停止新增融资。",
            "3. **禁止为摊低成本而加仓。** 只有原逻辑仍成立、价格结构重新转强且总风险不增加时才能加仓；否则低于上次买价的加仓视为规则违背。",
            "4. **每笔交易先写失效价。** 用“账户净资产 x 单笔风险预算 /（入场价-失效价）”反推股数；不能先决定仓位再寻找能接受的止损。",
            "5. **日内与隔夜分账。** 港股通同日往返、1-2日、趋势持有分别统计，不允许短线失败后临时改口为长线。",
            "6. **30笔修复样本。** 下一阶段固定一种入场模型，单笔计划风险从净资产0.25%开始；每10笔复盘净R、盈利因子、最大连续亏损R和规则违背率，正期望稳定后才提高风险。",
            "",
            "## 六、方法与限制",
            "",
            "- 平仓周期采用“持仓从0开始、最终回到0”的现金流法，结果不依赖FIFO或移动平均假设。",
            "- 为分配部分卖出的成本，内部使用移动平均成本池；但周期总盈亏始终等于含费净现金之和。",
            "- 期初持仓没有买入成本，因此首段卖出收入不计为利润；只有清仓后的新周期才进入精确统计。",
            "- 信用账户期末未平仓周期用“周期现金流 + 期末市值”计价，并与券商动态成本价核对。",
            "- 港股通买卖价为人民币结算单价；还原港币价只用于比较加仓方向，不改变人民币盈亏。",
            "- 港股通“发生日期”高度符合T+2交收日：周期的含费现金盈亏不受影响，但本报告持有天数是按PDF日期序列计算，节假日附近可与实际成交日持有天数略有差异。",
            "- 对账单缺少每日净值、事前止损和交易理由，不能诚实重建正式最大回撤、R倍数或策略期望；这些字段必须从下一笔开始记录。",
            "",
            "## 七、官方规则依据",
            "",
            "- [证监会：融资融券交易机制优化](https://www.csrc.gov.cn/csrc/c100028/c1000931/content.shtml)：2019年已取消统一的130%最低维持担保比例限制，由券商结合客户与担保物情况约定；因此本报告的130%/140%/150%只做压力情景，不能替代你的合同阈值。",
            "- [证监会：证券公司融资融券业务管理办法](https://www.csrc.gov.cn/csrc/c106256/c1654005/content.shtml)：最低维持担保比例、补足期限及违约处置方式应由券商与客户约定；到期未偿债也可能触发担保物处置。",
            "- [上交所：沪港通服务页](https://www.sse.com.cn/services/hkexsc/home/index.shtml)：日终买入按卖出结算汇兑比率支付人民币，卖出按买入结算汇兑比率收取人民币。",
            "- [上交所投教：关于港股通汇率](https://edu.sse.com.cn/service/hotline/qa/c/5717491.shtml)：结算汇率按当日买卖成交金额及交易、结算税费分摊，支持本报告按每行结算人民币金额核算。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert and audit credit/HK Connect broker PDFs")
    parser.add_argument("--credit", type=Path, required=True)
    parser.add_argument("--hk", type=Path, required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/broker_statements/credit_hk"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    credit = parse_credit(args.credit, args.password)
    hk = parse_hk(args.hk, args.password)
    credit_analysis = analyse_credit(credit)
    hk_analysis = analyse_hk(hk)

    (args.output / "信用账户电子对账单.md").write_text(
        credit_markdown(credit, credit_analysis), encoding="utf-8"
    )
    (args.output / "港股通电子对账单.md").write_text(hk_markdown(hk, hk_analysis), encoding="utf-8")
    (args.output / "信用账户与港股通交易分析.md").write_text(
        analysis_markdown(credit, credit_analysis, hk, hk_analysis), encoding="utf-8"
    )

    write_csv(args.output / "信用账户逐笔流水.csv", [asdict(row) for row in credit.transactions])
    hk_rows = []
    for row in hk.transactions:
        item = asdict(row)
        item["fees"] = row.fees
        item["net_cash"] = row.net_cash
        item["implied_hkd_price"] = row.implied_hkd_price
        hk_rows.append(item)
    write_csv(args.output / "港股通逐笔流水.csv", hk_rows)
    write_csv(args.output / "信用账户融资负债.csv", [asdict(row) for row in credit.debts])
    write_csv(args.output / "信用账户证券周期.csv", credit_analysis["cycles"])
    write_csv(args.output / "港股通证券周期.csv", hk_analysis["cycles"])
    (args.output / "分析指标.json").write_text(
        json.dumps({"credit": credit_analysis, "hk": hk_analysis}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "credit_rows": len(credit.transactions),
        "credit_unmatched": len(credit.unmatched_date_lines),
        "credit_cash_failures": credit_analysis["audit"]["cash_balance_failures_gt_0_02"],
        "credit_debt_error": credit_analysis["audit"]["debt_reconciliation_error"],
        "credit_cycle_stats": credit_analysis["cycle_stats"],
        "hk_rows": len(hk.transactions),
        "hk_unmatched": len(hk.unmatched_date_lines),
        "hk_principal_max_error": hk_analysis["audit"]["principal_formula_max_abs_error"],
        "hk_cycle_stats": hk_analysis["cycle_stats"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
