from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from statistics import median

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_broker_statements import (  # noqa: E402
    Statement,
    Transaction,
    chronological,
    fetch_history,
    parse_statement,
)


BUY = "证券买入"
SELL = "证券卖出"
DIVIDEND = "红利入账"
DIVIDEND_TAX = "股息红利差异扣税"
BONUS = "红股入账"
NEW_SECURITY = "新股入账"
SUBSCRIPTION_PAYMENT = "配售缴款"
TRADE_CATEGORIES = {BUY, SELL}
POSITION_CASH_CATEGORIES = {DIVIDEND, DIVIDEND_TAX}


def fnum(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}"


def fpct(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}%}"


def fdate(value: str | None) -> str:
    if not value:
        return "—"
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def common_prefix_length(left: str, right: str) -> int:
    length = 0
    for a, b in zip(left, right):
        if a != b:
            break
        length += 1
    return length


def build_subscription_links(statement: Statement) -> tuple[dict[int, Transaction], dict[int, Transaction]]:
    payments = [tx for tx in chronological(statement.transactions) if tx.category == SUBSCRIPTION_PAYMENT]
    entries = [tx for tx in chronological(statement.transactions) if tx.category == NEW_SECURITY]
    entry_to_payment: dict[int, Transaction] = {}
    payment_to_entry: dict[int, Transaction] = {}
    used: set[int] = set()
    for entry in entries:
        candidates: list[tuple[int, int, Transaction]] = []
        entry_date = datetime.strptime(entry.date, "%Y%m%d")
        for payment in payments:
            if payment.sequence in used or payment.date > entry.date or payment.quantity != entry.quantity:
                continue
            gap = (entry_date - datetime.strptime(payment.date, "%Y%m%d")).days
            prefix = common_prefix_length(payment.name, entry.name)
            if gap <= 35 and prefix >= 2:
                candidates.append((-prefix, gap, payment))
        if candidates:
            _, _, payment = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
            entry_to_payment[entry.sequence] = payment
            payment_to_entry[payment.sequence] = entry
            used.add(payment.sequence)
    return entry_to_payment, payment_to_entry


def infer_opening_positions(
    statement: Statement, payment_to_entry: dict[int, Transaction]
) -> dict[str, float]:
    final_qty = {holding.code: holding.quantity for holding in statement.holdings if holding.code}
    delta: dict[str, float] = defaultdict(float)
    for tx in statement.transactions:
        if not tx.code:
            continue
        if tx.category == BUY:
            delta[tx.code] += tx.quantity
        elif tx.category == SELL:
            delta[tx.code] -= tx.quantity
        elif tx.category in {BONUS, NEW_SECURITY}:
            delta[tx.code] += tx.quantity
        elif tx.category == SUBSCRIPTION_PAYMENT and tx.sequence not in payment_to_entry and tx.code in final_qty:
            delta[tx.code] += tx.quantity
    return {
        code: quantity
        for code in set(final_qty) | set(delta)
        if (quantity := final_qty.get(code, 0.0) - delta.get(code, 0.0)) > 1e-8
    }


def fetch_sina_history(session: requests.Session, code: str) -> tuple[list[dict], str | None]:
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": f"bj{code}", "scale": "240", "ma": "no", "datalen": "500"}
    try:
        response = session.get(
            url,
            params=params,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        )
        response.raise_for_status()
        payload = response.json()
        rows = []
        previous_close = None
        for values in payload:
            close = float(values["close"])
            rows.append(
                {
                    "date": values["day"].replace("-", ""),
                    "open": float(values["open"]),
                    "close": close,
                    "high": float(values["high"]),
                    "low": float(values["low"]),
                    "volume": float(values["volume"]),
                    "amount": 0.0,
                    "pct": (close / previous_close - 1) * 100 if previous_close else 0.0,
                    "turnover": 0.0,
                }
            )
            previous_close = close
        return rows, None if rows else "新浪接口无行情数据"
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def fetch_all_histories(statement: Statement, output_dir: Path, refresh: bool = False) -> tuple[dict, dict]:
    cache_path = output_dir / "全量历史行情.json"
    histories: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    if cache_path.exists() and not refresh:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        histories = cached.get("histories", {})
        errors = cached.get("errors", {})

    codes = {
        tx.code
        for tx in statement.transactions
        if tx.code and tx.category in TRADE_CATEGORIES | {NEW_SECURITY}
    }
    codes |= {holding.code for holding in statement.holdings if holding.code and holding.quantity > 0}
    codes |= {"000300", "588200"}
    missing = sorted(code for code in codes if code not in histories)
    if missing:
        session = requests.Session()
        session.trust_env = False
        try:
            for code in missing:
                _, rows, error = fetch_history(session, code, "20250601", "20260718")
                if not rows and code.startswith(("4", "8", "9")):
                    rows, error = fetch_sina_history(session, code)
                if rows:
                    histories[code] = rows
                    errors.pop(code, None)
                elif error:
                    errors[code] = error
        finally:
            session.close()
        cache_path.write_text(
            json.dumps({"histories": histories, "errors": errors}, ensure_ascii=False), encoding="utf-8"
        )
    return histories, errors


def quote_features(code: str, date: str, price: float, histories: dict[str, list[dict]]) -> dict:
    rows = histories.get(code, [])
    index = next((idx for idx, row in enumerate(rows) if row["date"] == date), None)
    if index is None:
        return {}
    row = rows[index]
    prior20 = rows[max(0, index - 20) : index]
    prior60 = rows[max(0, index - 60) : index]
    out: dict[str, float | bool] = {
        "day_open": row["open"],
        "day_high": row["high"],
        "day_low": row["low"],
        "day_close": row["close"],
        "intraday_location": (price - row["low"]) / (row["high"] - row["low"])
        if row["high"] > row["low"]
        else 0.5,
    }
    for window in (5, 10, 20, 60):
        prior = rows[max(0, index - window) : index]
        if prior:
            ma = sum(item["close"] for item in prior) / len(prior)
            out[f"ma{window}"] = ma
            out[f"vs_ma{window}"] = price / ma - 1
    if prior20:
        out["distance_20d_high"] = price / max(item["high"] for item in prior20) - 1
        out["near_20d_high"] = out["distance_20d_high"] >= -0.02
        out["above_ma20"] = price > out.get("ma20", math.inf)
    if len(prior60) >= 20:
        out["prior_20d_return"] = row["close"] / prior60[-20]["close"] - 1
    for horizon in (1, 3, 5, 10, 20):
        if index + horizon >= len(rows):
            continue
        future = rows[index + horizon]
        window_rows = rows[index + 1 : index + horizon + 1]
        out[f"forward_{horizon}d"] = future["close"] / price - 1
        out[f"mfe_{horizon}d"] = max(item["high"] for item in window_rows) / price - 1
        out[f"mae_{horizon}d"] = min(item["low"] for item in window_rows) / price - 1
    return out


def benchmark_return(code: str, date: str, horizon: int, histories: dict[str, list[dict]]) -> float | None:
    rows = histories.get(code, [])
    index = next((idx for idx, row in enumerate(rows) if row["date"] == date), None)
    if index is None or index + horizon >= len(rows):
        return None
    return rows[index + horizon]["close"] / rows[index]["close"] - 1


def create_cycle(
    counters: dict[str, int],
    code: str,
    name: str,
    start: str,
    exact: bool,
    opening_qty: float = 0.0,
    origin: str = "secondary_market",
) -> dict:
    counters[code] += 1
    return {
        "cycle_id": f"{code}-C{counters[code]:03d}",
        "code": code,
        "name": name,
        "start_date": start,
        "end_date": None,
        "status": "open",
        "exact": exact,
        "origin": origin,
        "opening_qty": opening_qty,
        "buy_cash": 0.0,
        "sell_cash": 0.0,
        "other_cash": 0.0,
        "cash_flow": 0.0,
        "max_basis": 0.0,
        "execution_ids": [],
        "settlement_ids": [],
        "buy_count": 0,
        "sell_count": 0,
        "averaged_down": False,
        "add_up_count": 0,
        "add_down_count": 0,
        "rebuild_after_sell_count": 0,
        "last_buy_price": None,
        "first_buy_execution": None,
        "last_sell_execution": None,
        "pnl": None,
        "return_on_max_basis": None,
        "holding_days": None,
    }


def add_cycle_trajectories(records: list[dict], cycles: list[dict], histories: dict[str, list[dict]]) -> None:
    events_by_cycle: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record.get("cycle_id"):
            events_by_cycle[record["cycle_id"]].append(record)
    for cycle in cycles:
        if not cycle["exact"] or cycle["origin"] != "secondary_market":
            continue
        bars = [
            row
            for row in histories.get(cycle["code"], [])
            if row["date"] >= cycle["start_date"]
            and row["date"] <= (cycle["end_date"] or "20260718")
        ]
        events = sorted(
            events_by_cycle.get(cycle["cycle_id"], []),
            key=lambda row: (row["date"], int(row["settlement_id"][1:])),
        )
        if not bars or not events:
            continue
        cash_flow = 0.0
        quantity = 0.0
        event_index = 0
        trajectory = []
        for bar in bars:
            while event_index < len(events) and events[event_index]["date"] <= bar["date"]:
                event = events[event_index]
                if event["category"] in TRADE_CATEGORIES | POSITION_CASH_CATEGORIES:
                    cash_flow += event["amount"]
                if event.get("position_after") is not None:
                    quantity = event["position_after"]
                event_index += 1
            marked_pnl = cash_flow + quantity * bar["close"]
            trajectory.append(
                {
                    "date": bar["date"],
                    "pnl": marked_pnl,
                    "quantity": quantity,
                    "close": bar["close"],
                }
            )
        if trajectory:
            cycle["mfe_cash"] = max(point["pnl"] for point in trajectory)
            cycle["mae_cash"] = min(point["pnl"] for point in trajectory)
            cycle["mfe_return_on_max_basis"] = (
                cycle["mfe_cash"] / cycle["max_basis"] if cycle["max_basis"] else None
            )
            cycle["mae_return_on_max_basis"] = (
                cycle["mae_cash"] / cycle["max_basis"] if cycle["max_basis"] else None
            )
        if len(trajectory) > 2:
            cycle["day2_date"] = trajectory[2]["date"]
            cycle["day2_pnl"] = trajectory[2]["pnl"]
            cycle["day2_return_on_max_basis"] = (
                cycle["day2_pnl"] / cycle["max_basis"] if cycle["max_basis"] else None
            )
        if len(trajectory) > 5:
            cycle["day5_date"] = trajectory[5]["date"]
            cycle["day5_pnl"] = trajectory[5]["pnl"]
            cycle["day5_return_on_max_basis"] = (
                cycle["day5_pnl"] / cycle["max_basis"] if cycle["max_basis"] else None
            )


def analyse_ledger(statement: Statement, histories: dict[str, list[dict]]) -> tuple[list[dict], list[dict], dict]:
    entry_to_payment, payment_to_entry = build_subscription_links(statement)
    opening = infer_opening_positions(statement, payment_to_entry)
    final_holdings = {holding.code: holding for holding in statement.holdings if holding.code}
    name_by_code = {tx.code: tx.name for tx in statement.transactions if tx.code and tx.name}
    name_by_code |= {holding.code: holding.name for holding in statement.holdings if holding.code}

    counters: dict[str, int] = defaultdict(int)
    states: dict[str, dict] = {}
    lots: dict[str, deque[dict]] = defaultdict(deque)
    cycles: list[dict] = []
    last_cycle: dict[str, dict] = {}
    records: list[dict] = []
    buy_record_by_id: dict[str, dict] = {}
    anomalies: list[str] = []

    start_date = statement.query_range[:10].replace("/", "") if statement.query_range else "20250718"
    for code, quantity in opening.items():
        cycle = create_cycle(
            counters,
            code,
            name_by_code.get(code, ""),
            start_date,
            False,
            quantity,
            origin="opening_unknown",
        )
        cycles.append(cycle)
        last_cycle[code] = cycle
        states[code] = {"qty": quantity, "basis": None, "cycle": cycle}
        lots[code].append(
            {
                "buy_id": "期初未知",
                "date": None,
                "qty": quantity,
                "unit_cost": None,
                "original_qty": quantity,
                "matched_qty": 0.0,
                "realized_pnl": 0.0,
                "sell_ids": [],
            }
        )

    rows = chronological(statement.transactions)
    execution_counter = 0
    for settlement_counter, tx in enumerate(rows, start=1):
        settlement_id = f"S{settlement_counter:04d}"
        execution_id = None
        if tx.category in TRADE_CATEGORIES:
            execution_counter += 1
            execution_id = f"E{execution_counter:04d}"
        record = {
            "settlement_id": settlement_id,
            "execution_id": execution_id or "",
            "source_sequence": tx.sequence,
            "source_page": tx.page,
            "raw_line": tx.raw_line,
            "date": tx.date,
            "category": tx.category,
            "code": tx.code,
            "name": tx.name,
            "quantity": tx.quantity,
            "price": tx.price,
            "gross_notional": tx.trade_notional,
            "amount": tx.amount,
            "commission": tx.commission,
            "stamp_tax": tx.stamp_tax,
            "transfer_fee": tx.transfer_fee,
            "fees": tx.commission + tx.stamp_tax + tx.transfer_fee,
            "cash_balance": tx.cash_balance,
            "cycle_id": "",
            "action": "",
            "position_before": None,
            "position_after": None,
            "avg_cost_before": None,
            "avg_cost_after": None,
            "realized_pnl_avg": None,
            "realized_return_avg": None,
            "fifo_known_pnl": None,
            "fifo_cost_coverage": None,
            "matched_buy_ids": "",
            "weighted_holding_days": None,
            "cost_status": "not_applicable",
            "notes": "",
        }
        code = tx.code
        state = states.setdefault(code, {"qty": 0.0, "basis": 0.0, "cycle": None}) if code else None

        if tx.category == BUY:
            if state["qty"] <= 1e-8:
                cycle = create_cycle(counters, code, tx.name, tx.date, True)
                cycles.append(cycle)
                last_cycle[code] = cycle
                state.update({"qty": 0.0, "basis": 0.0, "cycle": cycle})
            cycle = state["cycle"]
            before_qty = state["qty"]
            before_avg = state["basis"] / before_qty if state["basis"] is not None and before_qty else None
            if before_qty <= 1e-8:
                action = "初始建仓"
            elif cycle["sell_count"] > 0:
                action = "减仓后买回"
            elif cycle["last_buy_price"] is not None and tx.price >= cycle["last_buy_price"]:
                action = "向上加仓"
            else:
                action = "向下加仓"
            if before_avg is not None and tx.price < before_avg:
                cycle["averaged_down"] = True
            if before_qty > 1e-8:
                if cycle["sell_count"] == 0 and cycle["last_buy_price"] is not None:
                    if tx.price >= cycle["last_buy_price"]:
                        cycle["add_up_count"] += 1
                    else:
                        cycle["add_down_count"] += 1
                elif cycle["sell_count"] > 0:
                    cycle["rebuild_after_sell_count"] += 1
            if state["basis"] is not None:
                state["basis"] += abs(tx.amount)
            state["qty"] += tx.quantity
            after_avg = state["basis"] / state["qty"] if state["basis"] is not None else None
            cycle["cash_flow"] += tx.amount
            cycle["buy_cash"] += abs(tx.amount)
            cycle["buy_count"] += 1
            cycle["max_basis"] = max(cycle["max_basis"], state["basis"] or 0.0)
            cycle["execution_ids"].append(execution_id)
            cycle["settlement_ids"].append(settlement_id)
            cycle["first_buy_execution"] = cycle["first_buy_execution"] or execution_id
            cycle["last_buy_price"] = tx.price
            unit_cost = abs(tx.amount) / tx.quantity if tx.quantity else None
            lot = {
                "buy_id": execution_id,
                "date": tx.date,
                "qty": tx.quantity,
                "unit_cost": unit_cost,
                "original_qty": tx.quantity,
                "matched_qty": 0.0,
                "realized_pnl": 0.0,
                "sell_ids": [],
            }
            lots[code].append(lot)
            buy_record_by_id[execution_id] = lot
            record.update(
                {
                    "cycle_id": cycle["cycle_id"],
                    "action": action,
                    "position_before": before_qty,
                    "position_after": state["qty"],
                    "avg_cost_before": before_avg,
                    "avg_cost_after": after_avg,
                    "cost_status": "exact",
                }
            )

        elif tx.category == SELL:
            cycle = state.get("cycle")
            if cycle is None:
                cycle = create_cycle(
                    counters,
                    code,
                    tx.name,
                    start_date,
                    False,
                    tx.quantity,
                    origin="opening_unknown",
                )
                cycles.append(cycle)
                last_cycle[code] = cycle
                state.update({"qty": tx.quantity, "basis": None, "cycle": cycle})
                lots[code].append(
                    {
                        "buy_id": "期初未知",
                        "date": None,
                        "qty": tx.quantity,
                        "unit_cost": None,
                        "original_qty": tx.quantity,
                        "matched_qty": 0.0,
                        "realized_pnl": 0.0,
                        "sell_ids": [],
                    }
                )
            before_qty = state["qty"]
            if tx.quantity > before_qty + 1e-6:
                anomalies.append(f"{settlement_id} {code} 卖出数量超过账面持仓：{tx.quantity}>{before_qty}")
                cycle["exact"] = False
            sell_qty = min(tx.quantity, before_qty)
            before_avg = state["basis"] / before_qty if state["basis"] is not None and before_qty else None
            if before_avg is not None and before_qty:
                allocated_cost = state["basis"] * sell_qty / before_qty
                realized = tx.amount - allocated_cost
                state["basis"] -= allocated_cost
                record["realized_pnl_avg"] = realized
                record["realized_return_avg"] = realized / allocated_cost if allocated_cost else None
                record["cost_status"] = "exact_average_cost"
            else:
                record["cost_status"] = "unknown_opening_cost"

            remaining = tx.quantity
            fifo_pnl = 0.0
            known_cost = 0.0
            known_qty = 0.0
            weighted_days = 0.0
            matched_days_qty = 0.0
            matched_ids: list[str] = []
            net_unit_proceeds = tx.amount / tx.quantity if tx.quantity else 0.0
            while remaining > 1e-8 and lots[code]:
                lot = lots[code][0]
                qty = min(remaining, lot["qty"])
                matched_ids.append(f"{lot['buy_id']}×{qty:g}")
                lot["matched_qty"] += qty
                lot["sell_ids"].append(execution_id)
                if lot["unit_cost"] is not None:
                    cost = lot["unit_cost"] * qty
                    pnl = (net_unit_proceeds - lot["unit_cost"]) * qty
                    fifo_pnl += pnl
                    known_cost += cost
                    known_qty += qty
                    lot["realized_pnl"] += pnl
                    if lot["date"]:
                        days = (datetime.strptime(tx.date, "%Y%m%d") - datetime.strptime(lot["date"], "%Y%m%d")).days
                        weighted_days += days * qty
                        matched_days_qty += qty
                lot["qty"] -= qty
                remaining -= qty
                if lot["qty"] <= 1e-8:
                    lots[code].popleft()
            if remaining > 1e-8:
                matched_ids.append(f"未找到来源×{remaining:g}")
                cycle["exact"] = False
            record["fifo_known_pnl"] = fifo_pnl if known_cost else None
            record["fifo_cost_coverage"] = known_qty / tx.quantity if tx.quantity else None
            record["matched_buy_ids"] = "; ".join(matched_ids)
            record["weighted_holding_days"] = weighted_days / matched_days_qty if matched_days_qty else None

            state["qty"] = max(0.0, before_qty - tx.quantity)
            cycle["cash_flow"] += tx.amount
            cycle["sell_cash"] += tx.amount
            cycle["sell_count"] += 1
            cycle["execution_ids"].append(execution_id)
            cycle["settlement_ids"].append(settlement_id)
            cycle["last_sell_execution"] = execution_id
            after_avg = state["basis"] / state["qty"] if state["basis"] is not None and state["qty"] else None
            record.update(
                {
                    "cycle_id": cycle["cycle_id"],
                    "action": "清仓" if state["qty"] <= 1e-8 else "减仓",
                    "position_before": before_qty,
                    "position_after": state["qty"],
                    "avg_cost_before": before_avg,
                    "avg_cost_after": after_avg,
                }
            )
            if state["qty"] <= 1e-8:
                cycle["status"] = "closed"
                cycle["end_date"] = tx.date
                cycle["holding_days"] = (
                    datetime.strptime(tx.date, "%Y%m%d") - datetime.strptime(cycle["start_date"], "%Y%m%d")
                ).days
                cycle["pnl"] = cycle["cash_flow"] if cycle["exact"] else None
                cycle["return_on_max_basis"] = (
                    cycle["pnl"] / cycle["max_basis"]
                    if cycle["pnl"] is not None and cycle["max_basis"] > 0
                    else None
                )
                state.update({"qty": 0.0, "basis": 0.0, "cycle": None})

        elif tx.category == BONUS and code:
            cycle = state.get("cycle") or last_cycle.get(code)
            before_qty = state["qty"]
            if cycle and before_qty > 0:
                factor = (before_qty + tx.quantity) / before_qty
                for lot in lots[code]:
                    old_qty = lot["qty"]
                    lot["qty"] *= factor
                    if lot["unit_cost"] is not None:
                        lot["unit_cost"] /= factor
                    if lot["buy_id"] in buy_record_by_id:
                        buy_record_by_id[lot["buy_id"]]["original_qty"] += lot["qty"] - old_qty
                state["qty"] += tx.quantity
                cycle["settlement_ids"].append(settlement_id)
                record.update(
                    {
                        "cycle_id": cycle["cycle_id"],
                        "position_before": before_qty,
                        "position_after": state["qty"],
                        "cost_status": "bonus_shares_basis_unchanged",
                    }
                )
            else:
                anomalies.append(f"{settlement_id} {code} 红股入账时未找到持仓")

        elif tx.category == NEW_SECURITY and code:
            payment = entry_to_payment.get(tx.sequence)
            if state["qty"] <= 1e-8:
                cycle = create_cycle(
                    counters,
                    code,
                    tx.name,
                    payment.date if payment else tx.date,
                    payment is not None,
                    origin="primary_allotment" if payment else "unknown_new_security",
                )
                cycles.append(cycle)
                last_cycle[code] = cycle
                state.update({"qty": 0.0, "basis": 0.0 if payment else None, "cycle": cycle})
            cycle = state["cycle"]
            before_qty = state["qty"]
            unit_cost = abs(payment.amount) / tx.quantity if payment and tx.quantity else None
            if payment and state["basis"] is not None:
                state["basis"] += abs(payment.amount)
                cycle["cash_flow"] += payment.amount
                cycle["buy_cash"] += abs(payment.amount)
                cycle["max_basis"] = max(cycle["max_basis"], state["basis"])
                record["notes"] = f"成本来自{payment.date} {payment.code}配售缴款"
            else:
                cycle["exact"] = False
            state["qty"] += tx.quantity
            lots[code].append(
                {
                    "buy_id": f"{settlement_id}(配售)",
                    "date": payment.date if payment else tx.date,
                    "qty": tx.quantity,
                    "unit_cost": unit_cost,
                    "original_qty": tx.quantity,
                    "matched_qty": 0.0,
                    "realized_pnl": 0.0,
                    "sell_ids": [],
                }
            )
            record.update(
                {
                    "cycle_id": cycle["cycle_id"],
                    "position_before": before_qty,
                    "position_after": state["qty"],
                    "avg_cost_after": state["basis"] / state["qty"] if state["basis"] is not None else None,
                    "cost_status": "exact_subscription_cost" if payment else "unknown_new_security_cost",
                }
            )

        elif tx.category == SUBSCRIPTION_PAYMENT and code:
            entry = payment_to_entry.get(tx.sequence)
            if entry:
                record["notes"] = f"已匹配新证券 {entry.code} {entry.name}，成本将在入账时转入"
                record["cost_status"] = "linked_subscription_payment"
            elif code in final_holdings and final_holdings[code].quantity > 0:
                if state["qty"] <= 1e-8:
                    cycle = create_cycle(
                        counters, code, tx.name, tx.date, True, origin="primary_allotment"
                    )
                    cycles.append(cycle)
                    last_cycle[code] = cycle
                    state.update({"qty": 0.0, "basis": 0.0, "cycle": cycle})
                cycle = state["cycle"]
                before_qty = state["qty"]
                state["qty"] += tx.quantity
                state["basis"] += abs(tx.amount)
                cycle["cash_flow"] += tx.amount
                cycle["buy_cash"] += abs(tx.amount)
                cycle["max_basis"] = max(cycle["max_basis"], state["basis"])
                cycle["settlement_ids"].append(settlement_id)
                lots[code].append(
                    {
                        "buy_id": f"{settlement_id}(配售)",
                        "date": tx.date,
                        "qty": tx.quantity,
                        "unit_cost": abs(tx.amount) / tx.quantity if tx.quantity else None,
                        "original_qty": tx.quantity,
                        "matched_qty": 0.0,
                        "realized_pnl": 0.0,
                        "sell_ids": [],
                    }
                )
                record.update(
                    {
                        "cycle_id": cycle["cycle_id"],
                        "position_before": before_qty,
                        "position_after": state["qty"],
                        "avg_cost_after": state["basis"] / state["qty"],
                        "cost_status": "exact_pending_allotment",
                    }
                )

        elif tx.category in POSITION_CASH_CATEGORIES and code:
            cycle = state.get("cycle") or last_cycle.get(code)
            if cycle:
                cycle["cash_flow"] += tx.amount
                cycle["other_cash"] += tx.amount
                cycle["settlement_ids"].append(settlement_id)
                if cycle["status"] == "closed" and cycle["exact"]:
                    cycle["pnl"] = cycle["cash_flow"]
                    cycle["return_on_max_basis"] = (
                        cycle["pnl"] / cycle["max_basis"] if cycle["max_basis"] > 0 else None
                    )
                record["cycle_id"] = cycle["cycle_id"]
                record["cost_status"] = "attributed_to_cycle"
            else:
                record["notes"] = "未找到可归属的持仓周期"

        if tx.category in TRADE_CATEGORIES:
            features = quote_features(tx.code, tx.date, tx.price, histories)
            record.update(features)
            for benchmark_code in ("000300", "588200"):
                for horizon in (5, 10, 20):
                    benchmark = benchmark_return(benchmark_code, tx.date, horizon, histories)
                    record[f"benchmark_{benchmark_code}_{horizon}d"] = benchmark
                    forward = record.get(f"forward_{horizon}d")
                    record[f"alpha_{benchmark_code}_{horizon}d"] = (
                        forward - benchmark if forward is not None and benchmark is not None else None
                    )
        records.append(record)

    record_by_execution = {record["execution_id"]: record for record in records if record["execution_id"]}
    for record in records:
        if record["category"] != BUY:
            continue
        lot = buy_record_by_id.get(record["execution_id"])
        if not lot:
            continue
        record["buy_adjusted_qty"] = lot["original_qty"]
        record["buy_matched_qty"] = lot["matched_qty"]
        record["buy_remaining_qty"] = lot["qty"]
        record["buy_fifo_realized_pnl"] = lot["realized_pnl"]
        record["buy_sell_ids"] = "; ".join(dict.fromkeys(lot["sell_ids"]))
        holding = final_holdings.get(record["code"])
        if holding and lot["unit_cost"] is not None and lot["qty"] > 0:
            record["buy_open_unrealized_pnl"] = (holding.price - lot["unit_cost"]) * lot["qty"]
            record["buy_total_pnl_to_date"] = record["buy_fifo_realized_pnl"] + record["buy_open_unrealized_pnl"]
        elif lot["matched_qty"] >= lot["original_qty"] - 1e-8:
            record["buy_total_pnl_to_date"] = record["buy_fifo_realized_pnl"]

    for cycle in cycles:
        if cycle["status"] == "open":
            holding = final_holdings.get(cycle["code"])
            if cycle["exact"] and holding:
                cycle["pnl"] = cycle["cash_flow"] + holding.value
                cycle["return_on_max_basis"] = (
                    cycle["pnl"] / cycle["max_basis"] if cycle["max_basis"] > 0 else None
                )
            cycle["holding_days"] = (
                datetime.strptime("20260718", "%Y%m%d")
                - datetime.strptime(cycle["start_date"], "%Y%m%d")
            ).days
        first = record_by_execution.get(cycle["first_buy_execution"] or "")
        last = record_by_execution.get(cycle["last_sell_execution"] or "")
        if first:
            cycle["entry_intraday_location"] = first.get("intraday_location")
            cycle["entry_above_ma20"] = first.get("above_ma20")
            cycle["entry_near_20d_high"] = first.get("near_20d_high")
            cycle["entry_prior_20d_return"] = first.get("prior_20d_return")
        if last:
            cycle["exit_intraday_location"] = last.get("intraday_location")
            cycle["exit_forward_5d"] = last.get("forward_5d")
            cycle["exit_forward_20d"] = last.get("forward_20d")

    add_cycle_trajectories(records, cycles, histories)

    by_code: dict[str, list[dict]] = defaultdict(list)
    for cycle in cycles:
        by_code[cycle["code"]].append(cycle)
    for code_cycles in by_code.values():
        ordered = sorted(code_cycles, key=lambda item: (item["start_date"], item["cycle_id"]))
        previous_end = None
        for cycle in ordered:
            cycle["reentry_gap_days"] = (
                (datetime.strptime(cycle["start_date"], "%Y%m%d") - datetime.strptime(previous_end, "%Y%m%d")).days
                if previous_end
                else None
            )
            if cycle["end_date"]:
                previous_end = cycle["end_date"]
            tags = []
            if cycle["holding_days"] is not None and cycle["holding_days"] <= 1:
                tags.append("超短持有")
            if cycle["reentry_gap_days"] is not None and cycle["reentry_gap_days"] <= 1:
                tags.append("快速重入")
            if cycle["averaged_down"]:
                tags.append("下跌加仓")
            if cycle.get("entry_near_20d_high"):
                tags.append("接近20日高点买入")
            if cycle.get("exit_intraday_location") is not None and cycle["exit_intraday_location"] < 0.25:
                tags.append("日内低位卖出")
            if cycle.get("exit_forward_20d") is not None and cycle["exit_forward_20d"] > 0.05:
                tags.append("卖后20日上涨>5%")
            if cycle["pnl"] is not None and cycle["pnl"] < 0:
                tags.append("亏损周期")
            cycle["tags"] = "、".join(tags)

    final_mismatches = []
    for code in set(states) | set(final_holdings):
        calculated = states.get(code, {}).get("qty", 0.0)
        expected = final_holdings.get(code).quantity if code in final_holdings else 0.0
        if abs(calculated - expected) > 1e-6:
            final_mismatches.append({"code": code, "calculated": calculated, "expected": expected})

    price_residuals = []
    for record in records:
        if record["category"] == BUY:
            expected_amount = -(record["gross_notional"] + record["fees"])
        elif record["category"] == SELL:
            expected_amount = record["gross_notional"] - record["fees"]
        else:
            continue
        residual = record["amount"] - expected_amount
        record["displayed_price_cash_residual"] = residual
        price_residuals.append(residual)

    trade_balance_pairs = []
    for previous, current in zip(records, records[1:]):
        if (
            previous["date"] == current["date"]
            and previous["category"] in TRADE_CATEGORIES
            and current["category"] in TRADE_CATEGORIES
        ):
            trade_balance_pairs.append(current["cash_balance"] - previous["cash_balance"] - current["amount"])

    audit = {
        "settlement_rows": len(records),
        "trade_executions": execution_counter,
        "opening_positions_unknown_cost": opening,
        "subscription_links": [
            {
                "payment_date": payment.date,
                "payment_code": payment.code,
                "payment_name": payment.name,
                "entry_date": entry.date,
                "entry_code": entry.code,
                "entry_name": entry.name,
                "quantity": entry.quantity,
                "cost": abs(payment.amount),
            }
            for payment_seq, entry in payment_to_entry.items()
            for payment in [next(tx for tx in statement.transactions if tx.sequence == payment_seq)]
        ],
        "anomalies": anomalies,
        "final_position_mismatches": final_mismatches,
        "fees": {
            "commission": sum(record["commission"] for record in records),
            "stamp_tax": sum(record["stamp_tax"] for record in records),
            "transfer_fee": sum(record["transfer_fee"] for record in records),
            "total": sum(record["fees"] for record in records),
        },
        "displayed_price_rounding": {
            "trade_rows_over_0_01": sum(abs(value) > 0.011 for value in price_residuals),
            "max_abs_residual": max((abs(value) for value in price_residuals), default=0.0),
            "reason": "成交价仅显示4位小数，逐笔损益以精确净发生金额为准",
        },
        "cash_balance_order_check": {
            "adjacent_trade_pairs": len(trade_balance_pairs),
            "pairs_within_0_01": sum(abs(value) <= 0.011 for value in trade_balance_pairs),
            "max_abs_residual": max((abs(value) for value in trade_balance_pairs), default=0.0),
        },
    }
    return records, cycles, audit


def sample_stats(cycles: list[dict], secondary_only: bool = False) -> dict:
    rows = [
        cycle
        for cycle in cycles
        if cycle["status"] == "closed"
        and cycle["exact"]
        and cycle["pnl"] is not None
        and (not secondary_only or cycle["origin"] == "secondary_market")
    ]
    pnls = [cycle["pnl"] for cycle in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    return {
        "count": len(rows),
        "pnl": sum(pnls),
        "win_rate": len(wins) / len(rows) if rows else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "payoff_ratio": (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "median_pnl": median(pnls) if pnls else None,
        "rows": rows,
    }


def grouped_cycle_stats(cycles: list[dict], grouper, secondary_only: bool = True) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for cycle in cycles:
        if (
            cycle["status"] == "closed"
            and cycle["exact"]
            and cycle["pnl"] is not None
            and (not secondary_only or cycle["origin"] == "secondary_market")
        ):
            groups[grouper(cycle)].append(cycle)
    result = []
    for label, rows in groups.items():
        pnls = [row["pnl"] for row in rows]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        result.append(
            {
                "group": label,
                "count": len(rows),
                "pnl": sum(pnls),
                "win_rate": len(wins) / len(rows),
                "avg_pnl": sum(pnls) / len(rows),
                "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            }
        )
    return result


def duration_group(cycle: dict) -> str:
    days = cycle["holding_days"]
    if days == 0:
        return "0天"
    if days == 1:
        return "1天"
    if days <= 5:
        return "2-5天"
    if days <= 10:
        return "6-10天"
    if days <= 20:
        return "11-20天"
    return ">20天"


def execution_group(cycle: dict) -> str:
    count = cycle["buy_count"] + cycle["sell_count"]
    if count == 2:
        return "2笔（一次买卖）"
    if count <= 4:
        return "3-4笔"
    return "5笔及以上"


def add_direction_group(cycle: dict) -> str:
    if cycle["rebuild_after_sell_count"] > 0:
        return "减仓后又买回"
    if cycle["add_up_count"] and cycle["add_down_count"]:
        return "上下混合加仓"
    if cycle["add_down_count"]:
        return "只向下加仓"
    if cycle["add_up_count"]:
        return "只向上加仓"
    return "单次建仓"


def position_size_group(cycle: dict) -> str:
    capital = cycle["max_basis"]
    if capital <= 20000:
        return "≤2万元"
    if capital <= 50000:
        return "2-5万元"
    if capital <= 100000:
        return "5-10万元"
    return ">10万元"


def entry_structure_group(cycle: dict) -> str:
    if cycle.get("entry_near_20d_high"):
        if cycle.get("entry_intraday_location") is not None and cycle["entry_intraday_location"] >= 0.75:
            return "强势结构+日内顶部25%"
        return "强势结构+非日内顶部25%"
    return "非20日高点附近"


def bool_group(field: str, yes: str, no: str):
    return lambda cycle: yes if cycle.get(field) else no


def calculate_metrics(records: list[dict], cycles: list[dict], audit: dict, statement: Statement) -> dict:
    all_closed_stats = sample_stats(cycles)
    all_exact_closed = all_closed_stats.pop("rows")
    secondary_stats = sample_stats(cycles, secondary_only=True)
    exact_closed = secondary_stats.pop("rows")
    primary_closed = [cycle for cycle in all_exact_closed if cycle["origin"] == "primary_allotment"]
    losing = sorted([cycle for cycle in exact_closed if cycle["pnl"] < 0], key=lambda row: row["pnl"])
    winning = sorted([cycle for cycle in exact_closed if cycle["pnl"] > 0], key=lambda row: row["pnl"], reverse=True)
    gross_loss = abs(sum(cycle["pnl"] for cycle in losing))
    top5_loss = abs(sum(cycle["pnl"] for cycle in losing[:5]))
    trade_records = [record for record in records if record["category"] in TRADE_CATEGORIES]
    exact_sell_records = [
        record for record in trade_records if record["category"] == SELL and record["realized_pnl_avg"] is not None
    ]
    buy_records = [record for record in trade_records if record["category"] == BUY]
    sell_records = [record for record in trade_records if record["category"] == SELL]
    code_execution_counts: dict[str, int] = defaultdict(int)
    for record in trade_records:
        code_execution_counts[record["code"]] += 1
    for cycle in cycles:
        cycle["code_execution_count"] = code_execution_counts.get(cycle["code"], 0)

    def weighted_forward(rows: list[dict], horizon: int) -> float | None:
        available = [row for row in rows if row.get(f"forward_{horizon}d") is not None]
        weight = sum(row["gross_notional"] for row in available)
        return (
            sum(row[f"forward_{horizon}d"] * row["gross_notional"] for row in available) / weight
            if weight
            else None
        )

    def weighted_key(rows: list[dict], key: str) -> float | None:
        available = [row for row in rows if row.get(key) is not None]
        weight = sum(row["gross_notional"] for row in available)
        return sum(row[key] * row["gross_notional"] for row in available) / weight if weight else None

    caps = []
    for cap in (1000.0, 2000.0, 3000.0, 5000.0):
        capped = sum(max(cycle["pnl"], -cap) for cycle in exact_closed)
        caps.append(
            {"loss_cap": cap, "counterfactual_pnl": capped, "improvement": capped - secondary_stats["pnl"]}
        )

    winners = [cycle for cycle in exact_closed if cycle["pnl"] > 0]
    losers = [cycle for cycle in exact_closed if cycle["pnl"] < 0]
    day2_candidates = [cycle for cycle in exact_closed if cycle.get("day2_pnl") is not None]
    day2_losers = [cycle for cycle in day2_candidates if cycle["day2_pnl"] <= 0]
    day2_time_stop_pnl = sum(
        cycle["day2_pnl"] if cycle.get("day2_pnl") is not None and cycle["day2_pnl"] <= 0 else cycle["pnl"]
        for cycle in exact_closed
    )
    day5_candidates = [cycle for cycle in exact_closed if cycle.get("day5_pnl") is not None]
    day5_losers = [cycle for cycle in day5_candidates if cycle["day5_pnl"] < 0]
    day5_winners = [cycle for cycle in day5_candidates if cycle["day5_pnl"] >= 0]
    time_stop_pnl = sum(
        cycle["day5_pnl"] if cycle.get("day5_pnl") is not None and cycle["day5_pnl"] < 0 else cycle["pnl"]
        for cycle in exact_closed
    )
    open_exact = [
        cycle for cycle in cycles if cycle["status"] == "open" and cycle["exact"] and cycle["pnl"] is not None
    ]
    open_secondary = [cycle for cycle in open_exact if cycle["origin"] == "secondary_market"]
    open_primary = [cycle for cycle in open_exact if cycle["origin"] == "primary_allotment"]
    ordered_closes = sorted(exact_closed, key=lambda cycle: (cycle["end_date"], cycle["cycle_id"]))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_drawdown_date = None
    current_losing_streak = 0
    longest_losing_streak = 0
    current_losing_cash = 0.0
    worst_losing_streak_cash = 0.0
    for cycle in ordered_closes:
        cumulative += cycle["pnl"]
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_date = cycle["end_date"]
        if cycle["pnl"] < 0:
            current_losing_streak += 1
            current_losing_cash += cycle["pnl"]
            if current_losing_streak > longest_losing_streak:
                longest_losing_streak = current_losing_streak
            worst_losing_streak_cash = min(worst_losing_streak_cash, current_losing_cash)
        else:
            current_losing_streak = 0
            current_losing_cash = 0.0

    metrics = {
        "audit": audit,
        "account_snapshot": {
            "total_assets": statement.total_assets,
            "security_value": statement.security_value,
            "cash": statement.cash_balance,
            "unexplained_asset_gap": statement.total_assets - statement.security_value - statement.cash_balance,
            "broker_displayed_unrealized_pnl": sum(
                (holding.price - holding.cost) * holding.quantity
                for holding in statement.holdings
                if holding.quantity and holding.cost
            ),
        },
        "all_exact_closed_cycles": all_closed_stats,
        "secondary_exact_closed_cycles": secondary_stats,
        "secondary_closed_trade_pnl_before_distributions": sum(
            cycle["pnl"] - cycle["other_cash"] for cycle in exact_closed
        ),
        "secondary_closed_net_distributions": sum(cycle["other_cash"] for cycle in exact_closed),
        "primary_allotment_closed": {
            "count": len(primary_closed),
            "pnl": sum(cycle["pnl"] for cycle in primary_closed),
            "cycles": primary_closed,
        },
        "open_exact_cycles": open_exact,
        "open_secondary_cycles": open_secondary,
        "open_primary_allotment_cycles": open_primary,
        "identifiable_total_pnl": sum(cycle["pnl"] for cycle in all_exact_closed + open_exact),
        "identifiable_secondary_pnl": sum(cycle["pnl"] for cycle in exact_closed + open_secondary),
        "unknown_cost_cycles": [cycle for cycle in cycles if not cycle["exact"]],
        "exact_sell_executions": len(exact_sell_records),
        "all_sell_executions": len(sell_records),
        "gross_loss": gross_loss,
        "top5_loss_share": top5_loss / gross_loss if gross_loss else None,
        "largest_losses": losing[:30],
        "largest_winners": winning[:20],
        "holding_days_winners": {
            "average": sum(cycle["holding_days"] for cycle in winners) / len(winners) if winners else None,
            "median": median(cycle["holding_days"] for cycle in winners) if winners else None,
        },
        "holding_days_losers": {
            "average": sum(cycle["holding_days"] for cycle in losers) / len(losers) if losers else None,
            "median": median(cycle["holding_days"] for cycle in losers) if losers else None,
        },
        "realized_close_sequence_risk": {
            "max_cash_drawdown": max_drawdown,
            "max_drawdown_end_date": max_drawdown_date,
            "longest_losing_streak_cycles": longest_losing_streak,
            "worst_losing_streak_cash": worst_losing_streak_cash,
            "warning": "仅按持仓周期平仓日确认盈亏，忽略重叠持仓的未实现波动，不是正式账户净值回撤",
        },
        "by_duration": grouped_cycle_stats(cycles, duration_group),
        "by_execution_count": grouped_cycle_stats(cycles, execution_group),
        "by_add_direction": grouped_cycle_stats(cycles, add_direction_group),
        "by_position_size": grouped_cycle_stats(cycles, position_size_group),
        "by_averaging_down": grouped_cycle_stats(
            cycles, bool_group("averaged_down", "发生下跌加仓", "未发生下跌加仓")
        ),
        "by_fast_reentry": grouped_cycle_stats(
            cycles,
            lambda cycle: "1天内重入" if cycle.get("reentry_gap_days") is not None and cycle["reentry_gap_days"] <= 1 else "非快速重入",
        ),
        "by_entry_near_high": grouped_cycle_stats(
            cycles,
            lambda cycle: "接近20日高点" if cycle.get("entry_near_20d_high") else "非20日高点附近",
        ),
        "by_entry_structure": grouped_cycle_stats(cycles, entry_structure_group),
        "by_code_familiarity": grouped_cycle_stats(
            cycles,
            lambda cycle: (
                "全年1-2笔"
                if cycle["code_execution_count"] <= 2
                else "全年3-5笔"
                if cycle["code_execution_count"] <= 5
                else "全年6-9笔"
                if cycle["code_execution_count"] <= 9
                else "全年10笔及以上"
            ),
        ),
        "weighted_forward_returns": {
            "buy_5d": weighted_forward(buy_records, 5),
            "buy_20d": weighted_forward(buy_records, 20),
            "sell_5d": weighted_forward(sell_records, 5),
            "sell_20d": weighted_forward(sell_records, 20),
        },
        "weighted_forward_alpha": {
            "buy_5d_vs_000300": weighted_key(buy_records, "alpha_000300_5d"),
            "buy_20d_vs_000300": weighted_key(buy_records, "alpha_000300_20d"),
            "sell_5d_vs_000300": weighted_key(sell_records, "alpha_000300_5d"),
            "sell_20d_vs_000300": weighted_key(sell_records, "alpha_000300_20d"),
            "buy_5d_vs_588200": weighted_key(buy_records, "alpha_588200_5d"),
            "buy_20d_vs_588200": weighted_key(buy_records, "alpha_588200_20d"),
            "sell_5d_vs_588200": weighted_key(sell_records, "alpha_588200_5d"),
            "sell_20d_vs_588200": weighted_key(sell_records, "alpha_588200_20d"),
        },
        "loss_cap_scenarios": caps,
        "day5_diagnostic": {
            "cycles_reaching_day5": len(day5_candidates),
            "day5_loser_count": len(day5_losers),
            "day5_loser_actual_final_pnl": sum(cycle["pnl"] for cycle in day5_losers),
            "day5_loser_marked_pnl": sum(cycle["day5_pnl"] for cycle in day5_losers),
            "day5_winner_count": len(day5_winners),
            "day5_winner_actual_final_pnl": sum(cycle["pnl"] for cycle in day5_winners),
            "counterfactual_total_pnl_if_day5_losers_closed": time_stop_pnl,
            "counterfactual_improvement": time_stop_pnl - secondary_stats["pnl"],
        },
        "day2_diagnostic": {
            "cycles_reaching_day2": len(day2_candidates),
            "day2_nonpositive_count": len(day2_losers),
            "day2_nonpositive_actual_final_pnl": sum(cycle["pnl"] for cycle in day2_losers),
            "day2_nonpositive_marked_pnl": sum(cycle["day2_pnl"] for cycle in day2_losers),
            "counterfactual_total_pnl_if_day2_nonpositive_closed": day2_time_stop_pnl,
            "counterfactual_improvement": day2_time_stop_pnl - secondary_stats["pnl"],
        },
        "cycles": cycles,
    }
    return metrics


def csv_value(value):
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return ""
    return value


def write_csv(records: list[dict], output_path: Path) -> None:
    preferred = [
        "settlement_id",
        "execution_id",
        "source_page",
        "source_sequence",
        "date",
        "category",
        "code",
        "name",
        "quantity",
        "price",
        "gross_notional",
        "amount",
        "fees",
        "cash_balance",
        "cycle_id",
        "action",
        "position_before",
        "position_after",
        "avg_cost_before",
        "avg_cost_after",
        "realized_pnl_avg",
        "realized_return_avg",
        "fifo_known_pnl",
        "fifo_cost_coverage",
        "matched_buy_ids",
        "weighted_holding_days",
        "intraday_location",
        "above_ma20",
        "near_20d_high",
        "prior_20d_return",
        "forward_1d",
        "forward_3d",
        "forward_5d",
        "forward_10d",
        "forward_20d",
        "mfe_20d",
        "mae_20d",
        "alpha_000300_20d",
        "alpha_588200_20d",
        "buy_matched_qty",
        "buy_remaining_qty",
        "buy_fifo_realized_pnl",
        "buy_open_unrealized_pnl",
        "buy_total_pnl_to_date",
        "buy_sell_ids",
        "cost_status",
        "notes",
    ]
    all_keys = {key for record in records for key in record}
    fields = preferred + sorted(all_keys - set(preferred))
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: csv_value(record.get(key)) for key in fields})


def write_ledger_markdown(records: list[dict], cycles: list[dict], audit: dict, output_path: Path) -> None:
    lines = [
        "# 逐笔交割计算账本",
        "",
        "> 来源：电子对账单_1.md 对应的原始 PDF。共逐行核对 1,040 条交割；买卖成交采用账户人民币净发生额计算，费用已包含。",
        "",
        "## 计算口径",
        "",
        "- 每一个 `Sxxxx` 是一条原始交割；每一个 `Exxxx` 是一笔证券买卖成交。",
        "- 每个代码从空仓到再次归零定义为一个持仓周期。完整周期的盈亏等于全部净现金流之和，不依赖 FIFO 或移动平均假设。",
        "- 部分卖出用移动平均成本给出逐笔盈亏，同时列出 FIFO 对应买入编号。两种分摊在完整周期结束时总盈亏相同。",
        "- 期初有成本未知持仓的卖出标为“未知期初成本”，不编造盈亏。配售缴款与新股/转债入账已按名称、数量和时间配对。",
        "- 后续收益是成交价到第 5/20 个交易日收盘价的变化；对卖出而言，正数代表卖出后继续上涨。",
        "- 持有日按自然日计算；第2/5个交易日验证则按行情交易日计算。PDF没有成交时间，因此不宣称还原盘中撮合先后。",
        "",
        "## 核对结果",
        "",
        f"- 原始交割：{audit['settlement_rows']:,} 条；买卖成交：{audit['trade_executions']:,} 笔。",
        f"- 期末持仓数量不一致：{len(audit['final_position_mismatches'])} 项；处理异常：{len(audit['anomalies'])} 项。",
        f"- 期初成本未知证券：{', '.join(audit['opening_positions_unknown_cost']) or '无'}。",
        f"- 三项显性费用合计 {fnum(audit['fees']['total'])} 元，其中佣金 {fnum(audit['fees']['commission'])}、"
        f"印花税 {fnum(audit['fees']['stamp_tax'])}、过户费 {fnum(audit['fees']['transfer_fee'])}。",
        f"- 显示价格反算净金额误差超过1分的成交有 {audit['displayed_price_rounding']['trade_rows_over_0_01']} 笔，"
        f"最大 {fnum(audit['displayed_price_rounding']['max_abs_residual'])} 元；因此逐笔成本一律采用券商“总发生金额”。",
        f"- 同日相邻纯买卖流水的资金余额校验：{audit['cash_balance_order_check']['pairs_within_0_01']}/"
        f"{audit['cash_balance_order_check']['adjacent_trade_pairs']} 组误差不超过1分，支持当前倒序还原。PDF没有成交时间、合同号，"
        "所以编号代表对账单流水顺序，不冒充底层撮合顺序。",
        "",
        "## 每笔买卖成交",
        "",
    ]
    trades_by_month: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record["category"] in TRADE_CATEGORIES:
            trades_by_month[record["date"][:6]].append(record)
    for month in sorted(trades_by_month):
        lines.extend(
            [
                f"### {month[:4]}-{month[4:]}",
                "",
                "|成交|日期|动作|代码/名称|数量@价格|净发生额|持仓前→后|卖出逐笔盈亏|收益率|对应买入/买入结果|持有日|5日后|20日后|诊断|",
                "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|",
            ]
        )
        for record in trades_by_month[month]:
            if record["category"] == SELL:
                counterpart = record["matched_buy_ids"] or "—"
                result = fnum(record["realized_pnl_avg"])
                result_return = fpct(record["realized_return_avg"])
            else:
                counterpart = (
                    f"匹配{fnum(record.get('buy_matched_qty'), 0)}股 / FIFO {fnum(record.get('buy_fifo_realized_pnl'))}"
                    f" / 剩余{fnum(record.get('buy_remaining_qty'), 0)}股"
                )
                result = "—"
                result_return = "—"
            diagnostics = []
            if record.get("near_20d_high"):
                diagnostics.append("近20日高点")
            if record.get("intraday_location") is not None and record["intraday_location"] >= 0.8:
                diagnostics.append("日内高位成交")
            if record["category"] == SELL and record.get("forward_20d") is not None and record["forward_20d"] > 0.05:
                diagnostics.append("卖后20日>5%")
            if record["cost_status"] == "unknown_opening_cost":
                diagnostics.append("期初成本未知")
            lines.append(
                f"|{record['execution_id']}/P{record['source_page']}|{fdate(record['date'])}|{record['action'] or ('买' if record['category'] == BUY else '卖')}|"
                f"{record['code']} {record['name']}|{fnum(record['quantity'], 0)}@{fnum(record['price'], 4)}|"
                f"{fnum(record['amount'])}|{fnum(record['position_before'], 0)}→{fnum(record['position_after'], 0)}|"
                f"{result}|{result_return}|{counterpart}|{fnum(record.get('weighted_holding_days'), 1)}|"
                f"{fpct(record.get('forward_5d'))}|{fpct(record.get('forward_20d'))}|{'、'.join(diagnostics) or '—'}|"
            )
        lines.append("")

    lines.extend(
        [
            "## 非买卖交割逐笔核对",
            "",
            "|交割|日期|类别|代码/名称|数量@价格|发生金额|资金余额|成本/归属说明|",
            "|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    for record in records:
        if record["category"] in TRADE_CATEGORIES:
            continue
        lines.append(
            f"|{record['settlement_id']}/P{record['source_page']}|{fdate(record['date'])}|{record['category']}|"
            f"{record['code'] or '—'} {record['name'] or ''}|{fnum(record['quantity'], 0)}@{fnum(record['price'], 4)}|"
            f"{fnum(record['amount'])}|{fnum(record['cash_balance'])}|{record['notes'] or record['cost_status']}|"
        )

    lines.extend(
        [
            "",
            "## 完整持仓周期",
            "",
            "|周期|证券|起止日期|来源/状态/精度|买/卖笔数|最大成本占用|净盈亏|回报率|持有日|行为标签|",
            "|---|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for cycle in sorted(cycles, key=lambda item: (item["start_date"], item["cycle_id"])):
        lines.append(
            f"|{cycle['cycle_id']}|{cycle['code']} {cycle['name']}|{fdate(cycle['start_date'])}→{fdate(cycle['end_date'])}|"
            f"{cycle['origin']}/{cycle['status']}/{'精确' if cycle['exact'] else '成本未知'}|{cycle['buy_count']}/{cycle['sell_count']}|"
            f"{fnum(cycle['max_basis'])}|{fnum(cycle['pnl'])}|{fpct(cycle['return_on_max_basis'])}|"
            f"{fnum(cycle['holding_days'], 0)}|{cycle.get('tags') or '—'}|"
        )
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def stats_table(rows: list[dict]) -> list[str]:
    lines = [
        "|分组|周期数|净盈亏|胜率|平均每周期|盈利因子|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"|{row['group']}|{row['count']}|{fnum(row['pnl'])}|{fpct(row['win_rate'])}|"
            f"{fnum(row['avg_pnl'])}|{fnum(row['profit_factor'], 3)}|"
        )
    return lines


def write_methodology_report(metrics: dict, output_path: Path) -> None:
    overall = metrics["secondary_exact_closed_cycles"]
    all_closed = metrics["all_exact_closed_cycles"]
    primary = metrics["primary_allotment_closed"]
    forward = metrics["weighted_forward_returns"]
    alpha = metrics["weighted_forward_alpha"]
    day2 = metrics["day2_diagnostic"]
    day5 = metrics["day5_diagnostic"]
    assets = metrics["account_snapshot"]["total_assets"]
    risk025 = assets * 0.0025
    risk050 = assets * 0.005
    risk100 = assets * 0.01
    day5_change = day5["counterfactual_improvement"]
    day5_direction = "改善" if day5_change >= 0 else "恶化"
    day2_change = day2["counterfactual_improvement"]
    day2_direction = "改善" if day2_change >= 0 else "恶化"
    lines = [
        "# 从逐笔亏损到可执行交易系统",
        "",
        "> 本报告以“同一代码从空仓到再次归零”的完整持仓周期为核心；移动平均只用于把周期盈亏分摊到每笔卖出，FIFO只用于追踪对应买入和持有时间。",
        "",
        "## 1. 先把账算对：亏损沉淀在哪里",
        "",
        f"全部成本可核实且已归零的周期共 {all_closed['count']} 个，合计 {fnum(all_closed['pnl'])} 元；"
        f"其中新股/转债配售收益 {fnum(primary['pnl'])} 元。剔除不可复制的配售收益后，二级市场完整周期共"
        f" **{overall['count']} 个**，交易本身 {fnum(metrics['secondary_closed_trade_pnl_before_distributions'])} 元，"
        f"净股息及红利税 {fnum(metrics['secondary_closed_net_distributions'])} 元，合计 **{fnum(overall['pnl'])} 元**。"
        f"胜率 {fpct(overall['win_rate'])}，平均盈利 {fnum(overall['avg_win'])} 元，平均亏损 {fnum(overall['avg_loss'])} 元，"
        f"盈亏比 {fnum(overall['payoff_ratio'], 3)}，盈利因子 {fnum(overall['profit_factor'], 3)}。",
        "",
        f"尚未闭合的二级市场仓位按期末市值计 {fnum(sum(row['pnl'] for row in metrics['open_secondary_cycles']))} 元；"
        f"二级市场已知范围合计 **{fnum(metrics['identifiable_secondary_pnl'])} 元**。加上配售后，全部成本可核实证券损益约"
        f" **{fnum(metrics['identifiable_total_pnl'])} 元**。另有3个查询期初仓因缺少此前成本，不能编造盈亏。",
        "",
        f"券商期末成本价显示的持仓浮亏为 {fnum(metrics['account_snapshot']['broker_displayed_unrealized_pnl'])} 元，"
        "与“本轮空仓起点后的全部净现金流+期末市值”略有差异。逐笔经济损益采用后者，券商显示成本仅作对照，二者不能混加。",
        "",
        "所以并不是每次平仓都在亏：已归零的二级市场周期略亏，较大的亏损沉淀在期末未平仓仓位；配售收益又掩盖了二级市场本身的负期望。",
        "",
        f"只按二级市场周期平仓日排列，累计已实现结果的最大现金回撤约"
        f" {fnum(metrics['realized_close_sequence_risk']['max_cash_drawdown'])} 元，最长连续亏损"
        f" {metrics['realized_close_sequence_risk']['longest_losing_streak_cycles']} 个周期。由于多个持仓重叠且缺少每日总资产，"
        "这不是正式净值回撤，只用于设计风险闸门。",
        "",
        "## 2. 哪些行为真正贡献亏损",
        "",
        "### 持有期（自然日）",
        "",
        *stats_table(metrics["by_duration"]),
        "",
        "### 一次周期内的成交笔数",
        "",
        *stats_table(metrics["by_execution_count"]),
        "",
        "### 加仓方向",
        "",
        *stats_table(metrics["by_add_direction"]),
        "",
        "口径：只比较首次卖出前的连续买入；本次买价低于上一笔买价记为向下加仓，高于或等于记为向上加仓。已经减仓后再买统一列为“减仓后又买回”，避免把回补误算成普通加仓。",
        "",
        "### 单周期峰值成本占用",
        "",
        *stats_table(metrics["by_position_size"]),
        "",
        "峰值成本占用按周期内移动平均成本池的最大值计算，不使用期末仓位倒推。",
        "",
        "### 下跌加仓",
        "",
        *stats_table(metrics["by_averaging_down"]),
        "",
        "### 平仓后快速重入",
        "",
        *stats_table(metrics["by_fast_reentry"]),
        "",
        "### 首次买入是否接近20日高点",
        "",
        *stats_table(metrics["by_entry_near_high"]),
        "",
        "### 强势结构与日内追价",
        "",
        *stats_table(metrics["by_entry_structure"]),
        "",
        "“日内顶部25%”使用当日最终高低价，只能事后评价成交，实盘不能偷看。前瞻规则应改写为相对昨日20日高点、ATR、VWAP或突破回踩的位置。",
        "",
        "### 对标的的交易熟悉度",
        "",
        *stats_table(metrics["by_code_familiarity"]),
        "",
        f"前五大亏损周期占全部毛亏损 **{fpct(metrics['top5_loss_share'])}**。如果少数交易可以摧毁大量正常盈利，"
        "那么第一优先级应是约束单笔风险，而不是增加预测指标。",
        "",
        "分组结果给出六个有针对性的结论：",
        "",
        "1. 不能简单要求所有仓位都拿久。短周期整体盈利、超过5天的周期整体明显亏损，但这只说明长期滞留仓需要独立失效规则，不等于第5日见亏就机械卖出。",
        "2. 下跌加仓不是提高胜率的手段。它只能作为事前定义的独立策略，并且加仓后总风险仍不得超标。",
        "3. 平仓后1天内快速重入明显拖累结果，很多重入更像情绪回补，而不是独立的新信号。",
        "4. 接近20日高点买入的周期优于非强势买入，说明在你自己的交易中强势结构更有效；但相对科创芯片ETF仍未证明个股选择有超额，不能把主题上涨误当选股能力。",
        "5. 峰值成本占用超过10万元的15个周期合计明显亏损，说明大仓位没有换来更高胜率。修复期应先用风险预算和单名上限同时约束。",
        "6. 全年只交易1—2笔的陌生代码组明显亏损，10笔以上的熟悉代码组为正。不能事后只挑熟悉赢家，但今后应固定研究池，新标的必须先完成交易卡。",
        "",
        "最强的行为对比是：只向下加仓组净亏约2.63万元、盈利因子0.108；只向上加仓组净赚约2.72万元、盈利因子1.888。第一条需要立即执行的制度就是禁止摊低成本，只允许风险受控的盈利加仓。",
        "",
        "## 3. 非对称退出：盈利拿得短，亏损拖得长",
        "",
        f"全部可取得行情的买入成交，成交后5日和20日的金额加权收益分别为 {fpct(forward['buy_5d'])}、{fpct(forward['buy_20d'])}；"
        f"卖出后5日和20日仍为 {fpct(forward['sell_5d'])}、{fpct(forward['sell_20d'])}。",
        "",
        f"扣除同期沪深300后，买入后20日超额约 {fpct(alpha['buy_20d_vs_000300'])}，卖出后20日超额约"
        f" {fpct(alpha['sell_20d_vs_000300'])}；但相对科创芯片ETF（588200），对应结果分别为"
        f" {fpct(alpha['buy_20d_vs_588200'])}、{fpct(alpha['sell_20d_vs_588200'])}。",
        "",
        "588200并不是159只证券各自严格匹配的行业基准，只能作为这批交易偏半导体/科技风格的压力测试。它仍然改变了“只是卖早”的简单解释：相对强主题后，个股选择的超额并未得到证明。以后每笔个股交易都要写明相对其真正行业ETF的独立优势；若只有行业逻辑，可用行业ETF作为核心参照，个股只作为有明确超额理由的卫星研究仓。",
        "",
        f"买入日后的第2个交易日（T+2）收盘仍未盈利的周期有 {day2['day2_nonpositive_count']} 个；它们最终合计"
        f" {fnum(day2['day2_nonpositive_actual_final_pnl'])} 元。若当时机械退出，全部二级市场周期模型会从"
        f" {fnum(overall['pnl'])} 元变为 {fnum(day2['counterfactual_total_pnl_if_day2_nonpositive_closed'])} 元，"
        f"即{day2_direction} {fnum(abs(day2_change))} 元。它可以作为下一阶段候选验证规则，但历史反事实包含事后选择，不能直接当成未来收益。",
        "",
        f"盈利周期平均持有 {fnum(metrics['holding_days_winners']['average'], 2)} 天，亏损周期平均持有"
        f" {fnum(metrics['holding_days_losers']['average'], 2)} 天。达到T+5收盘仍为浮亏的周期有"
        f" {day5['day5_loser_count']} 个，这些周期最终合计 {fnum(day5['day5_loser_actual_final_pnl'])} 元。"
        f"若机械地在第5日收盘退出所有浮亏仓，模型相对实际会{day5_direction} {fnum(abs(day5_change))} 元。"
        "这证明单纯的时间止损不够，必须结合事前失效条件和相对强度，而不是只看当时是否浮亏。",
        "",
        "卖出后继续上涨并不意味着每笔都卖错；但结合持有期结果，模式很明确：亏损仓缺乏失效机制，盈利仓缺乏跟踪机制。解决办法是更快处理无效仓，同时让有效仓分批跟随，而不是统一延长或缩短持有时间。",
        "",
        "## 4. 最大亏损周期逐项核对",
        "",
        "|周期|证券|日期|峰值成本|净亏损|持有日|加仓类型|第2日盈亏|周期MFE|周期MAE|卖后20日|标签|",
        "|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|",
        *[
            f"|{row['cycle_id']}|{row['code']} {row['name']}|{fdate(row['start_date'])}→{fdate(row['end_date'])}|"
            f"{fnum(row['max_basis'])}|{fnum(row['pnl'])}|{fnum(row['holding_days'], 0)}|{add_direction_group(row)}|"
            f"{fnum(row.get('day2_pnl'))}|{fnum(row.get('mfe_cash'))}|{fnum(row.get('mae_cash'))}|"
            f"{fpct(row.get('exit_forward_20d'))}|{row.get('tags') or '—'}|"
            for row in metrics["largest_losses"][:20]
        ],
        "",
        "这张表要逐行回看交易卡：当时是否有失效价、是否向下加仓、为何扩大仓位、为何在第2/5日仍继续持有、退出是否来自规则。没有这些事前记录的交易，只能标记“不可审计”，不能用事后故事解释。",
        "",
        "## 5. 先执行五道硬闸门",
        "",
        "### 闸门A：没有失效价，不得下单",
        "",
        "每笔交易在成交前写下：策略标签、触发条件、逻辑失效价、计划退出方式、计划风险金额。没有事前失效价的旧交易只能标记为“不可审计”，不能事后伪造止损。",
        "",
        "### 闸门B：用风险倒推股数",
        "",
        "公式：`股数 = floor[(风险预算－预估费用) ÷ (入场价－失效价)]`，A股再向下取100股整数。按对账单快照总资产计算：",
        "",
        "|账户风险率|单笔最大计划亏损|用途|",
        "|---:|---:|---|",
        f"|0.25%|{fnum(risk025)} 元|系统修复期的起始档|",
        f"|0.50%|{fnum(risk050)} 元|连续滚动样本转正后才考虑|",
        f"|1.00%|{fnum(risk100)} 元|硬上限，不是默认额度|",
        "",
        "这些百分比是本账户的保守设计值，不是普遍真理。核心是先固定现金风险，再决定仓位；不能先买满，再寻找能接受的止损。",
        "",
        "### 闸门C：禁止无条件补亏损仓",
        "",
        "只有原计划明确允许分批、总开放风险仍未超过预算，且加仓触发条件独立成立时才可加仓。否则价格低于持仓成本的加仓一律视为规则违背。不同半导体代码必须合并计算行业开放风险。",
        "",
        "### 闸门D：退出分成三类",
        "",
        "1. 逻辑失效：触价立即退出，不讨论成本价。\n2. 时间复核：在T+2和T+5检查事前约定的相对强度或催化是否出现；只有条件失效才减仓或退出，不能仅因浮亏机械卖出。\n3. 盈利跟踪：达到首个目标后分批兑现，剩余仓用10日低点、20日均线或ATR规则跟随。三种规则分别统计，禁止临场混用。",
        "",
        "### 闸门E：组合级熔断",
        "",
        "历史平仓序列最长连续亏损9个周期，说明单笔止损之外还需要组合闸门。修复期试行：同一天连续2笔完整止损后停止新增风险；30笔滚动样本从权益峰值回撤4R时风险减半并复盘，回撤6R时暂停新开仓。阈值是保守设计值，待取得真实每日净值后再校准。",
        "",
        "## 6. 尾部亏损熔断的影响范围",
        "",
        "下面只做敏感性分析：假设每个完整周期都能在给定现金亏损上限处退出，忽略跳空和滑点，不代表真实可复制收益。",
        "",
        "|每周期亏损上限|重算净盈亏|相对实际改善|",
        "|---:|---:|---:|",
    ]
    for row in metrics["loss_cap_scenarios"]:
        lines.append(
            f"|-{fnum(row['loss_cap'])}|{fnum(row['counterfactual_pnl'])}|+{fnum(row['improvement'])}|"
        )
    lines.extend(
        [
            "",
            "这张表的用途不是挑一个最漂亮的止损数，而是证明亏损尾部是否决定了全年结果。正式阈值应换成事前 `R`，并用至少30—50个同策略新样本滚动验证。",
            "",
            "## 7. 每笔交易必须新增的日志字段",
            "",
            "|阶段|必填字段|",
            "|---|---|",
            "|下单前|策略标签、入场触发、失效价、目标/跟踪规则、计划R、股数、行业总风险|",
            "|持仓中|是否按计划加仓、最大有利波动MFE、最大不利波动MAE、规则变化及原因|",
            "|平仓后|含费净盈亏、R倍数、持有期、退出原因、卖后5/10/20日收益、相对沪深300超额|",
            "|每周|各策略胜率、平均盈利R、平均亏损R、期望值、盈利因子、规则违背率|",
            "",
            "期望值公式：`胜率×平均盈利R－败率×平均亏损R`。只有同一策略在滚动样本中期望为正、盈利因子高于1，并且规则违背率持续下降，才允许提高风险预算。",
            "",
            "## 8. 30笔修复实验",
            "",
            "接下来的30笔只做一种清晰定义的策略，单笔风险从0.25%档开始；禁止同日情绪性回补，禁止突破风险预算的下跌加仓。每10笔复盘一次，但不因短期盈亏更改规则。30笔结束后只看四项：净R、盈利因子、最大连续亏损R、规则违背率。未达到正期望前不加风险。",
            "",
            "## 9. 方法依据",
            "",
            "- [CME：建立交易日志](https://www.cmegroup.com/education/courses/building-a-trade-plan/keep-a-trade-log)：逐笔记录入场理由、目标、支撑阻力、进出场及复盘统计。",
            "- [CME：单笔风险预算](https://www.cmegroup.com/education/courses/trade-and-risk-management/the-2-percent-rule)：先确定可承受现金风险，再由止损距离反推仓位；具体百分比本身应由账户校准。",
            "- [Barber 与 Odean：交易越频繁，净表现越弱](https://faculty.haas.berkeley.edu/odean/papers/returns/individual_investor_performance_4-99.pdf)：特定历史账户样本支持把换手和短期往返作为硬KPI。",
            "- [Odean：处置效应原始研究](https://faculty.haas.berkeley.edu/odean/papers/disposition/disposition.html)：支持分别检验盈利退出和亏损退出，而不是只看总胜率。",
            "- [FINRA：识别高频进出交易](https://www.finra.org/investors/insights/3-ways-guard-against-excessive-trading-your-brokerage-account)：建议核查每次交易理由、费用及快速换仓。",
            "",
            "> 本报告是交易过程审计，不构成证券买卖建议。账户缺少完整每日总资产和事前止损记录，因此不能诚实重建历史R倍数与正式净值最大回撤；这些字段必须从下一笔交易开始记录。",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--password", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/broker_statements/detailed"))
    parser.add_argument("--refresh-history", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    statement = parse_statement(args.pdf, args.password)
    histories, history_errors = fetch_all_histories(statement, args.output, args.refresh_history)
    records, cycles, audit = analyse_ledger(statement, histories)
    audit["history_codes"] = len(histories)
    audit["history_errors"] = history_errors
    metrics = calculate_metrics(records, cycles, audit, statement)

    write_csv(records, args.output / "逐笔交割计算.csv")
    write_ledger_markdown(records, cycles, audit, args.output / "逐笔交割计算.md")
    write_methodology_report(metrics, args.output / "亏损修复交易制度.md")
    (args.output / "逐笔分析指标.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "settlements": audit["settlement_rows"],
                "executions": audit["trade_executions"],
                "history_codes": audit["history_codes"],
                "history_errors": len(history_errors),
                "position_mismatches": audit["final_position_mismatches"],
                "anomalies": audit["anomalies"],
                "all_exact_closed": metrics["all_exact_closed_cycles"],
                "secondary_exact_closed": metrics["secondary_exact_closed_cycles"],
                "identifiable_total_pnl": metrics["identifiable_total_pnl"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
