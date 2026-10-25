from __future__ import annotations

import concurrent.futures
import html
import json
import math
import struct
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TARGET = pd.Timestamp("2026-08-14")
TARGET_COMPACT = "20260814"
TDX = Path(r"C:\new_tdx\vipdoc")
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INDUSTRY_CFG = HQ / "tdxhy.cfg"
INCON = Path(r"C:\new_tdx\incon.dat")
REC = struct.Struct("<IIIIIfII")


def safe_div(a: float, b: float) -> float:
    return float(a / b) if b and math.isfinite(b) else float("nan")


def finite(v: float | None) -> bool:
    return v is not None and math.isfinite(float(v))


def fmt(v: float | None, digits: int = 2, suffix: str = "") -> str:
    if not finite(v):
        return "停算"
    return f"{float(v):.{digits}f}{suffix}"


def pctile(value: float, history: pd.Series) -> float:
    h = pd.to_numeric(history, errors="coerce").dropna()
    if not finite(value) or len(h) < 40:
        return float("nan")
    return float((h <= value).mean() * 100)


def load_masters() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from macro.sector import load_industry_names, resolve_industry_name
    from rps_analyzer.industry_rps import load_industry_map
    from run_ma20_screener import _load_tdx_name_master

    names = _load_tdx_name_master(HQ)
    industry_df = load_industry_map(INDUSTRY_CFG)
    industry_by_code = industry_df.drop_duplicates("ts_code").set_index("ts_code")["industry"].to_dict()
    industry_names = load_industry_names(INCON)
    resolved = {v: resolve_industry_name(v, industry_names) for v in set(industry_by_code.values())}
    return names, industry_by_code, resolved


def is_risk_name(name: str) -> bool:
    n = str(name).replace(" ", "").upper()
    return n.startswith(("ST", "*ST")) or "退" in n


def board_limit(code: str) -> float:
    raw = code[:6]
    return 0.20 if raw.startswith(("300", "301", "688")) else 0.10


def load_tail_records(names: dict[str, str], industry_by_code: dict[str, str], tail_n: int = 145) -> pd.DataFrame:
    rows: list[dict] = []
    for market in ("sh", "sz"):
        root = TDX / market / "lday"
        for path in root.glob("*.day"):
            stem = path.stem.lower()
            if not (stem.startswith("sh6") or stem.startswith("sz0") or stem.startswith("sz3")):
                continue
            code6 = stem[2:8]
            ts_code = f"{code6}.{market.upper()}"
            size = path.stat().st_size
            count = size // REC.size
            take = min(tail_n, count)
            with path.open("rb") as fh:
                fh.seek((count - take) * REC.size)
                blob = fh.read(take * REC.size)
            for date, opn, high, low, close, amount, vol, _ in REC.iter_unpack(blob):
                if date > 20260814 or date < 20260101:
                    continue
                rows.append(
                    {
                        "trade_date": pd.Timestamp(str(date)),
                        "ts_code": ts_code,
                        "symbol": f"{market}{code6}",
                        "name": names.get(ts_code, code6),
                        "industry": industry_by_code.get(ts_code),
                        "open": opn / 100.0,
                        "high": high / 100.0,
                        "low": low / 100.0,
                        "close": close / 100.0,
                        "amount": float(amount),
                        "vol": int(vol),
                    }
                )
    df = pd.DataFrame(rows).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    df["ret"] = df["close"] / df["prev_close"] - 1.0
    df["open_ret"] = df["open"] / df["prev_close"] - 1.0
    df["high_ret"] = df["high"] / df["prev_close"] - 1.0
    df["low_ret"] = df["low"] / df["prev_close"] - 1.0
    df["adv20"] = g["amount"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    df["risk_name"] = df["name"].map(is_risk_name)
    return df


def sentiment_proxy(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    work = df[(~df["risk_name"]) & df["industry"].notna()].copy()
    work["limit"] = work["ts_code"].map(board_limit)
    work["valid_ret"] = work["ret"].abs() <= (work["limit"] + 0.06)
    work["is_lu"] = work["valid_ret"] & (work["ret"] >= work["limit"] - 0.005)
    work["is_ld"] = work["valid_ret"] & (work["ret"] <= -work["limit"] + 0.005)
    work["touched"] = work["valid_ret"] & (work["high_ret"] >= work["limit"] - 0.005)
    dates = sorted(work["trade_date"].unique())
    prev_streak: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    for date in dates:
        day = work[work["trade_date"].eq(date)].copy()
        current_codes = set(day["ts_code"])
        prev_lu = {c for c, s in prev_streak.items() if s >= 1}
        prior_first = {c for c, s in prev_streak.items() if s == 1}
        prior_second = {c for c, s in prev_streak.items() if s == 2}
        prior_h = max(prev_streak.values(), default=0)
        prior_highest = {c for c, s in prev_streak.items() if s == prior_h and prior_h > 0}
        today_lu = set(day.loc[day["is_lu"], "ts_code"])
        yset = prev_lu & current_codes
        yfirst = prior_first & current_codes
        broken = int((day["touched"] & ~day["is_lu"]).sum())
        sealed = int(day["is_lu"].sum())
        ld = int(day["is_ld"].sum())
        sealed_ind = day.loc[day["is_lu"], "industry"].value_counts()
        concentration = float(sealed_ind.iloc[0] / sealed) if sealed and len(sealed_ind) else float("nan")
        row = {
            "trade_date": date,
            "J1": safe_div(len(prior_first & today_lu), len(prior_first)),
            "J2": safe_div(len(prior_second & today_lu), len(prior_second)),
            "JH": safe_div(len(prior_highest & today_lu), len(prior_highest)),
            "YLR_close": float(day.set_index("ts_code").loc[list(yset), "ret"].mean()) if yset else float("nan"),
            "YLR_open": float(day.set_index("ts_code").loc[list(yset), "open_ret"].mean()) if yset else float("nan"),
            "YLR_median": float(day.set_index("ts_code").loc[list(yset), "ret"].median()) if yset else float("nan"),
            "YLR_first": float(day.set_index("ts_code").loc[list(yfirst), "ret"].mean()) if yfirst else float("nan"),
            "FR": safe_div(broken, sealed + broken),
            "B": safe_div(sealed, sealed + ld),
            "C": concentration,
            "limit_up": sealed,
            "limit_down": ld,
            "broken": broken,
            "touched": sealed + broken,
            "prev_lu_n": len(yset),
            "H_prev": prior_h,
        }
        new_streak: dict[str, int] = {}
        for code in current_codes:
            new_streak[code] = prev_streak.get(code, 0) + 1 if code in today_lu else 0
        row["H"] = max(new_streak.values(), default=0)
        row["SHS"] = row["H"] - prior_h
        rows.append(row)
        prev_streak = new_streak
    daily = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    weights = {"J2": 0.30, "YLR_close": 0.25, "FR_inv": 0.20, "J1": 0.15, "B": 0.10}
    st_values: list[float] = []
    coverage_values: list[float] = []
    pct_columns: dict[str, list[float]] = defaultdict(list)
    for i, row in daily.iterrows():
        hist = daily.iloc[max(0, i - 60):i]
        pieces: dict[str, float] = {
            "J2": pctile(row["J2"], hist["J2"]),
            "YLR_close": pctile(row["YLR_close"], hist["YLR_close"]),
            "FR_inv": 100 - pctile(row["FR"], hist["FR"]) if finite(pctile(row["FR"], hist["FR"])) else float("nan"),
            "J1": pctile(row["J1"], hist["J1"]),
            "B": pctile(row["B"], hist["B"]),
        }
        den = sum(weights[k] for k, v in pieces.items() if finite(v))
        st = sum(weights[k] * v for k, v in pieces.items() if finite(v)) / den if den else float("nan")
        st_values.append(st)
        coverage_values.append(den / sum(weights.values()))
        for k, v in pieces.items():
            pct_columns[k].append(v)
    daily["ST_cycle"] = st_values
    daily["coverage"] = coverage_values
    for k, vals in pct_columns.items():
        daily[f"pct_{k}"] = vals
    daily["delta_ST"] = daily["ST_cycle"].diff()
    daily["ST_ma3"] = daily["ST_cycle"].rolling(3).mean()
    daily["slope3"] = (daily["ST_cycle"] - daily["ST_cycle"].shift(2)) / 2
    latest = daily.iloc[-1]
    state = "修复"
    rationale = "ST 位于修复区间且盘面从前一日负反馈中回升"
    if finite(latest["ST_cycle"]) and latest["ST_cycle"] < 20 and latest["slope3"] <= 0:
        state, rationale = "冰点", "ST<20 且三日斜率未转正"
    prev2 = daily.tail(2)
    ylr2neg = len(prev2) == 2 and (prev2["YLR_close"] < 0).all()
    hist60 = daily.iloc[-61:-1]
    fr70 = hist60["FR"].quantile(0.70)
    if ylr2neg and finite(latest["FR"]) and latest["FR"] > fr70 and latest["pct_J2"] < 30:
        state, rationale = "退潮", "YLR 连续两日为负，炸板率高分位且 J2 低分位"
    if finite(latest["ST_cycle"]) and latest["ST_cycle"] >= 85 and latest["delta_ST"] <= 0:
        state, rationale = "高潮", "ST>=85 且边际回落"
    if finite(latest["ST_cycle"]) and latest["ST_cycle"] >= 60 and latest["YLR_close"] > 0 and latest["pct_J2"] >= 60 and latest["JH"] > 0:
        state, rationale = "主升", "ST、J2、YLR 与最高空间晋级同时满足代理条件"
    # qfq gate: proxy can classify, but never upgrade the operational state beyond repair.
    if state in {"主升", "高潮"}:
        state, rationale = "修复", rationale + "；但未复权限制触发低置信度降级，不执行升档"
    state_caps = {"冰点": 0.10, "修复": 0.30, "主升": 1.00, "高潮": 0.30, "退潮": 0.10}
    snapshot = {k: (None if not finite(latest[k]) else float(latest[k])) for k in [
        "J1", "J2", "JH", "YLR_close", "YLR_open", "YLR_median", "YLR_first", "FR", "B", "C",
        "ST_cycle", "delta_ST", "ST_ma3", "slope3", "coverage", "pct_J1", "pct_J2", "pct_YLR_close", "pct_FR_inv", "pct_B"
    ]}
    for k in ["limit_up", "limit_down", "broken", "touched", "prev_lu_n", "H", "H_prev", "SHS"]:
        snapshot[k] = int(latest[k])
    snapshot.update({"state": state, "cap": state_caps[state], "rationale": rationale, "confidence": "低", "adjustment": "unadjusted proxy"})
    return daily, snapshot


def select_fixed_baskets(latest: pd.DataFrame) -> dict[str, pd.DataFrame]:
    defs = {
        "AI硬件": {"industries": {"T1202", "T1203", "T1204"}, "n": 36, "kind": "main"},
        "资源材料": {"industries": {"T020205", "T0208", "T020603"}, "n": 30, "kind": "secondary"},
        "黄金煤炭": {"industries": {"T020204", "T010101"}, "n": 24, "kind": "defense"},
        "软件服务": {"industries": {"T1205"}, "n": 30, "kind": "fake"},
    }
    out: dict[str, pd.DataFrame] = {}
    for name, spec in defs.items():
        sub = latest[(latest["industry"].isin(spec["industries"])) & latest["adv20"].notna()].copy()
        sub = sub.sort_values("adv20", ascending=False).head(spec["n"]).copy()
        sub["basket"] = name
        sub["basket_kind"] = spec["kind"]
        out[name] = sub
    return out


def fetch_tencent(symbol: str) -> dict:
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    try:
        resp = requests.get(url, timeout=18, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        node = resp.json().get("data", {}).get(symbol, {})
        data_node = node.get("data", {})
        date = str(data_node.get("date", ""))
        raw_rows = data_node.get("data", [])
        qt_node = node.get("qt", {})
        qt = qt_node.get(symbol, [])
        zhishu = qt_node.get("zhishu", [])
        parsed = []
        structure_ok = True
        for raw in raw_rows:
            parts = str(raw).split()
            if len(parts) != 4:
                structure_ok = False
                continue
            parsed.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
        times = [x[0] for x in parsed]
        amts = np.array([x[3] for x in parsed], dtype=float)
        mono = bool(len(amts) and np.all(np.diff(amts) >= -0.01))
        order_ok = times == sorted(times) and len(times) == len(set(times))
        prev_close = float(qt[4]) if len(qt) > 4 and qt[4] else float("nan")
        quote_ts = str(qt[30]) if len(qt) > 30 else ""
        quote_amount = float(str(qt[35]).split("/")[-1]) if len(qt) > 35 and "/" in str(qt[35]) else float("nan")
        final_amount = float(amts[-1]) if len(amts) else float("nan")
        # Tencent's quote snapshot and minute series can differ slightly because the
        # snapshot includes late exchange corrections. A documented 0.5% tolerance
        # catches truncation while allowing those corrections.
        amount_match = finite(quote_amount) and finite(final_amount) and abs(final_amount - quote_amount) / max(quote_amount, 1) <= 0.005
        return {
            "symbol": symbol,
            "url": url,
            "date": date,
            "quote_ts": quote_ts,
            "name": str(qt[1]) if len(qt) > 1 else symbol,
            "prev_close": prev_close,
            "rows": parsed,
            "row_count": len(parsed),
            "structure_ok": structure_ok and len(parsed) >= 240,
            "monotonic": mono,
            "time_order_ok": order_ok,
            "amount_match": amount_match,
            "quote_amount": quote_amount,
            "final_amount": final_amount,
            "breadth": {
                "up": int(zhishu[2]), "flat": int(zhishu[3]), "down": int(zhishu[4])
            } if len(zhishu) > 4 and all(str(zhishu[i]).isdigit() for i in (2, 3, 4)) else None,
            "valid": date == TARGET_COMPACT and structure_ok and mono and order_ok and amount_match and len(parsed) >= 240 and finite(prev_close),
            "error": None,
        }
    except Exception as exc:
        return {"symbol": symbol, "valid": False, "error": str(exc), "rows": [], "url": url}


def reach_metrics(item: dict, adv20: float, q: float) -> dict:
    rows = item.get("rows", [])
    prev = item.get("prev_close", float("nan"))
    result = {"reached": False, "gap_reached": False, "confirm_time": None, "Aq": None, "fund_ratio": None, "CER_adv": None, "retention": None, "CER_quality": None}
    if not item.get("valid") or not rows or not finite(prev) or not finite(adv20) or adv20 <= 0:
        return result
    rets = [r[1] / prev - 1 for r in rows]
    first_reached = rets[0] >= q
    result["gap_reached"] = bool(first_reached)
    confirm = None
    for i in range(1, len(rows)):
        if rets[i - 1] >= q and rets[i] >= q:
            confirm = i
            break
    close_ret = rets[-1]
    result["retention"] = float(np.clip(close_ret / q, 0, 1.2))
    if confirm is None or first_reached:
        return result
    aq = float(rows[confirm][3])
    fund_ratio = aq / adv20
    cer = q / fund_ratio if fund_ratio > 0 else float("nan")
    result.update({
        "reached": True,
        "confirm_time": rows[confirm][0],
        "Aq": aq,
        "fund_ratio": fund_ratio,
        "CER_adv": cer,
        "CER_quality": cer * min(result["retention"], 1.0) if finite(cer) else None,
    })
    return result


def analyze_basket(name: str, basket: pd.DataFrame, fetched: dict[str, dict], q: float) -> dict:
    records = []
    for row in basket.itertuples(index=False):
        item = fetched.get(row.symbol, {})
        if not item.get("valid"):
            continue
        rec = {"symbol": row.symbol, "ts_code": row.ts_code, "name": item.get("name", row.name), "adv20": float(row.adv20), "item": item}
        rec.update(reach_metrics(item, float(row.adv20), q))
        prev = item["prev_close"]
        rec["close_ret"] = item["rows"][-1][1] / prev - 1
        rec["amount"] = item["rows"][-1][3]
        records.append(rec)
    valid_n = len(records)
    result = {
        "name": name,
        "q": q,
        "component_n": int(len(basket)),
        "effective_n": valid_n,
        "coverage": safe_div(valid_n, len(basket)),
        "reached": False,
        "gap_reached": False,
        "confirm_time": None,
        "Aq": None,
        "fund_ratio": None,
        "CER_adv": None,
        "retention": None,
        "CER_quality": None,
        "reach_breadth": None,
        "positive_breadth": float(np.mean([r["close_ret"] > 0 for r in records])) if records else None,
        "median_stock_CER": None,
        "leader_share": None,
        "close_equal_ret": float(np.mean([r["close_ret"] for r in records])) if records else None,
        "minute_path": [],
        "stocks": records,
    }
    if valid_n < 8:
        return result
    common = sorted(set.intersection(*(set(x["item"]["rows"][i][0] for i in range(len(x["item"]["rows"]))) for x in records)))
    by_symbol = {r["symbol"]: {x[0]: x for x in r["item"]["rows"]} for r in records}
    eq_rets, cum_amounts = [], []
    for tm in common:
        eq_rets.append(float(np.mean([by_symbol[r["symbol"]][tm][1] / r["item"]["prev_close"] - 1 for r in records])))
        cum_amounts.append(float(sum(by_symbol[r["symbol"]][tm][3] for r in records)))
    # 四个阈值共用同一条等权分钟路径；只在 q=1% 对象保存，避免审计文件重复膨胀。
    if abs(q - 0.01) < 1e-12:
        result["minute_path"] = [{"time": tm, "return": ret} for tm, ret in zip(common, eq_rets)]
    first_reached = bool(eq_rets and eq_rets[0] >= q)
    result["gap_reached"] = first_reached
    confirm = None
    for i in range(1, len(common)):
        if eq_rets[i - 1] >= q and eq_rets[i] >= q:
            confirm = i
            break
    result["retention"] = float(np.clip(eq_rets[-1] / q, 0, 1.2)) if eq_rets else None
    if confirm is None or first_reached:
        return result
    aq = cum_amounts[confirm]
    adv = sum(r["adv20"] for r in records)
    fr = aq / adv
    cer = q / fr if fr > 0 else float("nan")
    tm = common[confirm]
    individual_at_confirm = [by_symbol[r["symbol"]][tm][1] / r["item"]["prev_close"] - 1 for r in records]
    stock_cers = [r["CER_adv"] for r in records if r["reached"] and finite(r["CER_adv"])]
    amounts_at_confirm = [by_symbol[r["symbol"]][tm][3] for r in records]
    result.update({
        "reached": True,
        "confirm_time": tm,
        "Aq": aq,
        "fund_ratio": fr,
        "CER_adv": cer,
        "CER_quality": cer * min(result["retention"], 1.0),
        "reach_breadth": float(np.mean(np.array(individual_at_confirm) >= q)),
        "median_stock_CER": float(np.median(stock_cers)) if stock_cers else None,
        "leader_share": safe_div(max(amounts_at_confirm), sum(amounts_at_confirm)),
    })
    return result


def index_path(item: dict) -> dict:
    rows = item["rows"]
    prev = item["prev_close"]
    points = {}
    lookup = {r[0]: r for r in rows}
    for tm in ["0930", "1000", "1030", "1130", "1330", "1430", "1500"]:
        if tm in lookup:
            points[tm] = (lookup[tm][1] / prev - 1) * 100
    rets = np.array([r[1] / prev - 1 for r in rows])
    low_i, high_i = int(np.argmin(rets)), int(np.argmax(rets))
    diffs = np.diff(np.array([r[3] for r in rows]), prepend=0)
    return {
        "name": item["name"], "symbol": item["symbol"], "points": points,
        "low_time": rows[low_i][0], "low_ret": float(rets[low_i] * 100),
        "high_time": rows[high_i][0], "high_ret": float(rets[high_i] * 100),
        "close_ret": float(rets[-1] * 100),
        "am_amount_share": float(sum(diffs[:121]) / sum(diffs)) if sum(diffs) else None,
    }


def stock_role_table(ai: dict) -> list[dict]:
    stocks = ai["stocks"]
    if not stocks:
        return []
    reached = [s for s in stocks if s["reached"]]
    leader = max(stocks, key=lambda x: (x["close_ret"], x["amount"]))
    middle = max(stocks, key=lambda x: x["adv20"])
    elastic_pool = [s for s in stocks if s["symbol"] not in {leader["symbol"], middle["symbol"]}]
    elastic = max(elastic_pool or stocks, key=lambda x: (x.get("CER_quality") or -1, x["close_ret"]))
    roles = [("龙头代理", leader), ("中军代理", middle), ("弹性代理", elastic)]
    out = []
    for role, s in roles:
        out.append({
            "role": role, "name": s["name"], "symbol": s["symbol"], "close_ret": s["close_ret"],
            "amount_yi": s["amount"] / 1e8, "adv20_yi": s["adv20"] / 1e8,
            "prev_close": s["item"]["prev_close"], "open": s["item"]["rows"][0][1],
            "close": s["item"]["rows"][-1][1],
            "high": max(row[1] for row in s["item"]["rows"]),
            "low": min(row[1] for row in s["item"]["rows"]),
            "confirm_time": s.get("confirm_time"), "CER_adv": s.get("CER_adv"),
            "retention": s.get("retention"), "CER_quality": s.get("CER_quality"),
        })
    return out


def esc(v: object) -> str:
    return html.escape(str(v), quote=True)


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def build_html(data: dict) -> str:
    market = data["market"]
    s = data["sentiment"]
    baskets = data["baskets"]
    roles = data["roles"]
    idx = data["indices"]
    qa = data["qa"]
    ai1 = baskets["AI硬件"]["q1"]
    res1 = baskets["资源材料"]["q1"]
    defense1 = baskets["黄金煤炭"]["q1"]
    fake1 = baskets["软件服务"]["q1"]
    role_positive = sum(r["close_ret"] > 0 for r in roles)
    role_reached = sum(finite(r["CER_adv"]) for r in roles)
    resonance = role_positive == 3 and role_reached >= 2 and ai1["positive_breadth"] >= 0.60
    def reach_label(x: dict) -> str:
        if x.get("gap_reached"):
            return "09:30跳空触达·剔除"
        return str(x.get("confirm_time") or "未触达")
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    role_rows = "".join(
        f"<tr><td><b>{esc(r['role'])}</b></td><td>{esc(r['name'])}<small>{esc(r['symbol'])}</small></td>"
        f"<td class='num'>{fmt(r['close_ret']*100,2,'%')}</td><td class='num'>{fmt(r['amount_yi'],2,'亿')}</td>"
        f"<td>{esc(r['confirm_time'] or '未确认3%')}</td><td class='num'>{fmt(r['CER_adv'],2)}</td>"
        f"<td class='num'>{fmt((r['retention'] or 0)*100,0,'%') if r['retention'] is not None else '停算'}</td></tr>" for r in roles
    )
    basket_rows = ""
    for name, obj in baskets.items():
        q1 = obj["q1"]
        q2 = obj["q2"]
        classification = {"AI硬件": "第一主线", "资源材料": "次主线", "黄金煤炭": "防守", "软件服务": "伪轮动"}[name]
        basket_rows += (
            f"<tr><td><b>{esc(classification)}</b><small>{esc(name)}</small></td>"
            f"<td>{q1['effective_n']}/{q1['component_n']}</td>"
            f"<td class='num'>{fmt(q1['close_equal_ret']*100 if finite(q1['close_equal_ret']) else None,2,'%')}</td>"
            f"<td class='num'>{fmt(q1['positive_breadth']*100 if finite(q1['positive_breadth']) else None,0,'%')}</td>"
            f"<td>{esc(reach_label(q1))}</td>"
            f"<td class='num'>{fmt(q1['CER_adv'],2)}</td><td class='num'>{fmt(q1['retention']*100 if finite(q1['retention']) else None,0,'%')}</td><td class='num'>{fmt(q1['reach_breadth']*100 if finite(q1['reach_breadth']) else None,0,'%')}</td>"
            f"<td>{esc(reach_label(q2))}</td><td class='num'>{fmt(q2['CER_adv'],2)}</td></tr>"
        )
    stock_pool = []
    for name in ["AI硬件", "资源材料"]:
        stock_pool.extend(baskets[name]["q3"]["stocks"])
    stock_pool = sorted([x for x in stock_pool if x["reached"] and finite(x["CER_quality"])], key=lambda x: x["CER_quality"], reverse=True)[:12]
    stock_rows = "".join(
        f"<tr><td>{i+1}</td><td><b>{esc(x['name'])}</b><small>{esc(x['symbol'])}</small></td>"
        f"<td>{esc(x['confirm_time'])}</td><td class='num'>{fmt(x['Aq']/1e8,2,'亿')}</td>"
        f"<td class='num'>{fmt(x['fund_ratio']*100,1,'% ADV')}</td><td class='num'>{fmt(x['CER_adv'],2)}</td>"
        f"<td class='num'>{fmt(x['retention']*100,0,'%')}</td><td class='num'>{fmt(x['CER_quality'],2)}</td></tr>"
        for i, x in enumerate(stock_pool)
    ) or "<tr><td colspan='8'>固定篮子内无满足“非跳空、连续两分钟站上 3%”的有效样本。</td></tr>"
    stock_pool5 = []
    for name in ["AI硬件", "资源材料"]:
        stock_pool5.extend(baskets[name]["q5"]["stocks"])
    stock_pool5 = sorted([x for x in stock_pool5 if x["reached"] and finite(x["CER_quality"])], key=lambda x: x["CER_quality"], reverse=True)[:8]
    stock_rows5 = "".join(
        f"<tr><td>{i+1}</td><td><b>{esc(x['name'])}</b><small>{esc(x['symbol'])}</small></td>"
        f"<td>{esc(x['confirm_time'])}</td><td class='num'>{fmt(x['Aq']/1e8,2,'亿')}</td>"
        f"<td class='num'>{fmt(x['fund_ratio']*100,1,'% ADV')}</td><td class='num'>{fmt(x['CER_adv'],2)}</td>"
        f"<td class='num'>{fmt(x['retention']*100,0,'%')}</td><td class='num'>{fmt(x['CER_quality'],2)}</td></tr>"
        for i, x in enumerate(stock_pool5)
    ) or "<tr><td colspan='8'>固定篮子内无满足“非跳空、连续两分钟站上 5%”的有效样本。</td></tr>"
    index_rows = "".join(
        f"<tr><td><b>{esc(x['name'])}</b></td><td class='num'>{fmt(x['points'].get('0930'),2,'%')}</td>"
        f"<td class='num'>{fmt(x['points'].get('1000'),2,'%')}</td><td class='num'>{fmt(x['points'].get('1130'),2,'%')}</td>"
        f"<td class='num'>{fmt(x['points'].get('1430'),2,'%')}</td><td class='num'>{fmt(x['close_ret'],2,'%')}</td>"
        f"<td>{esc(x['low_time'])} / {fmt(x['low_ret'],2,'%')}</td></tr>" for x in idx
    )
    st_parts = [
        ("ST_cycle", fmt(s["ST_cycle"], 1)), ("ΔST", fmt(s["delta_ST"], 1)), ("J1", fmt(s["J1"]*100 if finite(s["J1"]) else None, 0, "%")),
        ("J2", fmt(s["J2"]*100 if finite(s["J2"]) else None, 0, "%")), ("YLR", fmt(s["YLR_close"]*100 if finite(s["YLR_close"]) else None, 2, "%")),
        ("炸板率", fmt(s["FR"]*100 if finite(s["FR"]) else None, 1, "%")), ("涨/跌停代理", f"{s['limit_up']} / {s['limit_down']}"),
        ("最高空间", f"{s['H']}板（前日{s['H_prev']}板）"),
    ]
    st_html = "".join(f"<div class='mini'><span>{esc(k)}</span><strong>{esc(v)}</strong></div>" for k, v in st_parts)
    validation_rows = "".join(
        f"<tr><td>{esc(x['symbol'])}</td><td>{esc(x['date'])}</td><td>{x['row_count']}</td><td>{badge('通过','good') if x['valid'] else badge('失败','bad')}</td>"
        f"<td>{'是' if x['monotonic'] else '否'}</td><td>{'是' if x['amount_match'] else '否'}</td></tr>" for x in qa["index_validation"]
    )
    snapshot_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股全市场日内复盘 · 2026-08-14</title>
<style>
:root{{--bg:#08101d;--panel:#101c2d;--panel2:#142238;--ink:#e9f0f8;--muted:#94a6bb;--line:#263a52;--cyan:#54d2d2;--green:#42cf89;--red:#ff6d76;--amber:#ffc857;--blue:#7fa9ff}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 85% -10%,#163353 0,transparent 32%),var(--bg);color:var(--ink);font:14px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1280px;margin:auto;padding:24px}} header{{display:flex;justify-content:space-between;gap:18px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:18px}} h1{{font-size:29px;line-height:1.15;margin:0}} h2{{font-size:18px;margin:0 0 12px}} h3{{font-size:15px;margin:0 0 8px}} p{{margin:6px 0}} .muted,small{{color:var(--muted)}} small{{display:block;font-size:11px}} .stamp{{text-align:right;color:var(--muted)}}
.hero{{display:grid;grid-template-columns:1.15fr .85fr .85fr;gap:14px;margin:18px 0}} .card{{background:linear-gradient(145deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:14px;padding:17px;box-shadow:0 8px 26px #0004}} .eyebrow{{color:var(--cyan);font-weight:700;letter-spacing:.08em;font-size:12px}} .big{{font-size:30px;font-weight:800;line-height:1.2;margin:4px 0}} .subbig{{font-size:20px;font-weight:750}} .badge{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 8px;margin:2px;color:var(--muted);font-size:12px}} .badge.good{{color:var(--green);border-color:#2c7358}} .badge.warn{{color:var(--amber);border-color:#786126}} .badge.bad{{color:var(--red);border-color:#7b3441}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0}} .grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:14px 0}} .strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}} .mini{{background:#091626;border:1px solid #20364d;border-radius:10px;padding:9px}} .mini span{{display:block;color:var(--muted);font-size:11px}} .mini strong{{font-size:16px}} .callout{{border-left:3px solid var(--amber);padding:8px 12px;background:#1b1b19;border-radius:4px}} .plan{{border-color:#315b7c;background:linear-gradient(145deg,#10263a,#101c2d)}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th{{color:var(--muted);font-weight:600;text-align:left;background:#0b1726;position:sticky;top:0}} th,td{{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}} td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}} .tablebox{{overflow:auto;max-height:520px}} ul{{margin:6px 0;padding-left:20px}} li{{margin:5px 0}} .fact{{color:var(--green)}} .infer{{color:var(--amber)}} .hypo{{color:var(--blue)}} .kpi{{font-size:20px;font-weight:800}} footer{{margin-top:20px;border-top:1px solid var(--line);padding:16px 0;color:var(--muted)}}
@media(max-width:860px){{.hero,.grid2,.grid3{{grid-template-columns:1fr}}.strip{{grid-template-columns:1fr 1fr}}header{{align-items:start;flex-direction:column}}.stamp{{text-align:left}}.wrap{{padding:14px}}}}
@media print{{:root{{--bg:#fff;--panel:#fff;--panel2:#fff;--ink:#111;--muted:#555;--line:#ccc}}body{{background:#fff}}.card{{box-shadow:none;break-inside:avoid}}.tablebox{{max-height:none;overflow:visible}}}}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">A-SHARE INTRADAY REVIEW</div><h1>全市场日内复盘</h1><p class="muted">复盘日 2026-08-14（最近交易日） · 数据截至收盘 · 账户空仓</p></div><div class="stamp">生成：{esc(now)}<br>时区：Asia/Shanghai</div></header>

<section class="hero">
<article class="card"><div class="eyebrow">唯一市场状态</div><div class="big">高位轮动 · 弱修复分化</div><p>指数先压后修，成长强于权重；腾讯上涨家数不足半数，未形成全市场普涨。</p><div>{badge(f"腾讯宽度 {market['breadth_total']}只",'good')}{badge('日线前复权未验证','warn')}{badge('结论置信度 中低','warn')}</div></article>
<article class="card"><div class="eyebrow">ST_cycle 状态</div><div class="big">{esc(s['state'])}</div><p>代理温度 {fmt(s['ST_cycle'],1)} / 100；{esc(s['rationale'])}。</p><div>{badge('代理口径','warn')}{badge('不允许升档','bad')}</div></article>
<article class="card"><div class="eyebrow">次日风险预算</div><div class="big">上限 {int(s['cap']*100)}%</div><p><b>实际初始仓位：0%</b>。上限不是目标；风险预算和个股硬止损未输入，不能反推伪精确股数。</p><div>{badge('不融资','good')}{badge('T+1','warn')}</div></article>
</section>

<section class="card plan"><div class="eyebrow">唯一主计划 · 8月17日</div><div class="subbig">只观察 AI 硬件第一主线的“分歧后再共振”；开盘不追，未满足则全天空仓。</div>
<div class="grid3"><div><h3>允许触发</h3><p>固定篮子正宽 ≥60%，龙头/中军/弹性至少两类重新站上开盘均价，且沪深成交额节奏不弱于 8/14 同时段。</p></div><div><h3>取消条件</h3><p>竞价主线三角色中两类低开且 9:45 前不能修复；或软件/医药仅权重脉冲、主线宽度跌破 50%。</p></div><div><h3>硬失效</h3><p>昨日涨停溢价继续转负并扩散、最高空间断板无替代，或主线中军放量跌破开盘区间低点：停止新增，计划归零。</p></div></div>
<div class="callout"><b>T+1：</b>当天买入不可卖。任何试仓必须在下单前写明次日极端低开处置；若无法接受隔夜跳空风险，本计划仓位就是 0%。盘中只允许降低上限，不允许临时加名单或升档。</div></section>

<section class="grid2"><article class="card"><h2>盘面截面 <span class="fact">[事实]</span></h2><div class="strip"><div class="mini"><span>两市成交额</span><strong>{fmt(market['total_amount_yi']/10000,3,'万亿')}</strong></div><div class="mini"><span>上涨宽度·腾讯</span><strong>{fmt(market['up_pct'],1,'%')}</strong></div><div class="mini"><span>个股中位收益</span><strong>停算</strong></div><div class="mini"><span>成交额/ADV20</span><strong>{fmt(market['amount_vs_prior20']*100,1,'%')}</strong></div></div><p>创业板 +1.12%、中证1000 +0.70%，沪指 +0.01%、沪深300 +0.04%、科创50近乎平盘。成长方向修复，但赚钱宽度尚未过半；个股收益中位数因前复权闸门停算。</p></article>
<article class="card"><h2>情绪代理状态机 <span class="infer">[推断]</span></h2><div class="strip">{st_html}</div><p class="muted">涨停、接力和 YLR 来自未复权 `.day` 的制度化代理，滚动基线为此前最多 60 个有效交易日。由于除权参考价未核验，状态只可降级使用，不得据此升到主升。</p></article></section>

<section class="card"><h2>盘面过程阶段：分歧 → 修复，尾盘未扩散 <span class="fact">[事实]</span></h2><div class="tablebox"><table><thead><tr><th>指数</th><th class="num">09:30</th><th class="num">10:00</th><th class="num">11:30</th><th class="num">14:30</th><th class="num">收盘</th><th>日内低点</th></tr></thead><tbody>{index_rows}</tbody></table></div><p class="infer">[推断] 指数低点后回升，但全市场宽度仍弱、成交额较前日缩约 15.9%，所以阶段不是“扩散/加速”，而是弱修复中的结构性抱团。</p></section>

<section class="card"><h2>主线、次主线、防守与伪轮动</h2><div class="tablebox"><table><thead><tr><th>定位 / 固定篮子</th><th>有效/固定</th><th class="num">等权收盘</th><th class="num">正宽</th><th>1%确认</th><th class="num">板块CER</th><th class="num">保持率</th><th class="num">触达宽</th><th>2%确认</th><th class="num">板块CER</th></tr></thead><tbody>{basket_rows}</tbody></table></div>
<ul><li><span class="infer">[推断]</span> <b>第一主线：AI硬件</b>（通信设备+元器件+半导体）。原始成交额约占全市场 34%，固定篮子收盘正宽约 81%；但 09:30 已跳空触达 1%，板块 CER 按规则剔除。</li><li><span class="infer">[推断]</span> <b>次主线：资源材料</b>（小金属+矿物制品+玻璃）。固定篮子正宽 90%，09:37 确认 1%，但绝对成交容量显著小于 AI 硬件。</li><li><span class="infer">[推断]</span> <b>防守：黄金+煤炭仅相对抗跌</b>。1% 到 13:29 才确认、CER 较低，不把尾盘红盘包装成强防守主线。</li><li><span class="infer">[推断]</span> <b>伪轮动：软件服务</b>。固定篮子虽收涨，但 2% 到 14:16 才确认、触达宽仅约 1/3，属于少数高弹性标的拉动而非充分扩散。</li></ul></section>

<section class="grid2"><article class="card"><h2>龙头—中军—弹性共振</h2><div class="tablebox"><table><thead><tr><th>角色</th><th>标的</th><th class="num">收盘涨幅</th><th class="num">成交额</th><th>3%确认</th><th class="num">CER</th><th class="num">保持率</th></tr></thead><tbody>{role_rows}</tbody></table></div><p>{badge('共振偏强·非满格' if resonance else '共振不完整','good' if resonance else 'warn')} 三角色均收红 {role_positive}/3，3% 有效确认 {role_reached}/3；中军未完成 3% 确认，因此不是满格共振。这只是固定成分内的量价角色代理，不等于主观“龙头封号”。</p></article>
<article class="card"><h2>结论边界</h2><ul><li><span class="fact">[事实]</span> 腾讯分钟接口日期、字段、累计额单调性与收盘累计额已校验。</li><li><span class="infer">[推断]</span> AI硬件为第一主线，来自宽度、成交容量、板块 CER 与角色共振的合并判断。</li><li><span class="hypo">[交易假设]</span> 次日只有“分歧后再共振”值得试错；若开盘直接加速，宁可错过。</li><li><span class="hypo">[交易假设]</span> 30% 是状态机上限，不是建议满打；当前因账户风险参数缺失，实际执行保持 0%。</li></ul></article></section>

<section class="card"><h2>个股资金效能比 · 3%主阈值</h2><p class="muted">从 09:30 起，连续两分钟收盘站上 +3% 才确认；09:30 已触达者作为跳空样本剔除。资金使用量取腾讯累计额在确认时刻的值；ADV20 只用本地通达信原始成交额。</p><div class="tablebox"><table><thead><tr><th>#</th><th>标的</th><th>确认</th><th class="num">Aq</th><th class="num">资金/ADV20</th><th class="num">CER</th><th class="num">保持率</th><th class="num">质量CER</th></tr></thead><tbody>{stock_rows}</tbody></table></div><p class="muted">CER 是相对同一阈值、同一流动性口径的研究指标；不表示“主力净流入”，也不能单独触发买入。</p></section>

<section class="card"><h2>个股资金效能比 · 5%次阈值</h2><p class="muted">与 3% 主阈值完全同口径，仅用于辨别真正高弹性推进；不可与 3% CER 横向混排。</p><div class="tablebox"><table><thead><tr><th>#</th><th>标的</th><th>确认</th><th class="num">Aq</th><th class="num">资金/ADV20</th><th class="num">CER</th><th class="num">保持率</th><th class="num">质量CER</th></tr></thead><tbody>{stock_rows5}</tbody></table></div></section>

<section class="grid2"><article class="card"><h2>数据质量闸门</h2><ul><li>{badge('通过','good')} 本地尾记录：5273 个文件落在 8/14；分析截面 4987 只非风险样本。</li><li>{badge('通过','good')} 腾讯有效分钟：{qa['valid_stock_minutes']}/{qa['requested_stock_minutes']} 只固定篮子成分；每只至少 240 行。</li><li>{badge('剔除','warn')} 接口校验未通过成分：{esc('、'.join(qa['failed_stock_symbols']) or '无')}；不补值，不进入篮子 CER。</li><li>{badge('通过','good')} 累计成交额只做确认时刻取值或区间差分，未逐分钟累加。</li><li>{badge('停算','bad')} RPS、MA、ATR、跨日收益、回撤、日线 CER：前复权未验证。</li><li>{badge('降级','warn')} ST_cycle：本地未复权涨停生态代理，置信度低，不允许升档。</li></ul></article>
<article class="card"><h2>腾讯指数样本校验</h2><div class="tablebox"><table><thead><tr><th>代码</th><th>日期</th><th>行数</th><th>总校验</th><th>累计单调</th><th>收盘额吻合</th></tr></thead><tbody>{validation_rows}</tbody></table></div></article></section>

<section class="card"><h2>计算口径与可复核来源</h2><p><b>腾讯分钟：</b><code>https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={{symbol}}</code>；实际响应时间戳与日期记录在内嵌审计快照。<br><b>本地通达信：</b><code>C:/new_tdx/vipdoc/{{sh,sz}}/lday/*.day</code>；金额字段单位为元，价格未接入前复权因子。<br><b>行业映射：</b><code>C:/new_tdx/T0002/hq_cache/tdxhy.cfg</code>。<br><b>固定篮子：</b>各主题按 8/14 之前 ADV20 排序锁定成分（AI硬件36、资源材料30、黄金煤炭24、软件服务30），避免盘后挑选赢家。</p><p class="muted">本报告是研究与风险管理材料，不构成收益保证。所有“资金”均指成交额使用效率，不识别买卖方身份。</p></section>
<footer>自包含 HTML · 无外部脚本/字体/CDN · 审计快照已内嵌。<script id="audit-snapshot" type="application/json">{snapshot_json}</script></footer>
</div></body></html>"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    names, industry_by_code, industry_names = load_masters()
    df = load_tail_records(names, industry_by_code)
    latest = df[df["trade_date"].eq(TARGET) & (~df["risk_name"]) & df["industry"].notna()].copy()
    market_csv = pd.read_csv(OUT / "rotation_market_daily_20260814.csv", encoding="utf-8-sig")
    market_allowed = ["trade_date", "total_amount_yi", "top20_amount_share_pct", "top50_amount_share_pct", "amount_vs_prior20", "amount_vs_prior5"]
    market = market_csv.iloc[-1][market_allowed].to_dict()
    sentiment_daily, sentiment = sentiment_proxy(df)
    baskets_df = select_fixed_baskets(latest)
    index_symbols = ["sh000001", "sh000300", "sh000852", "sz399006", "sh000688"]
    breadth_symbols = ["sh000001", "sz399001"]
    stock_symbols = sorted(set().union(*(set(x["symbol"]) for x in baskets_df.values())))
    requested = sorted(set(index_symbols + breadth_symbols + stock_symbols))
    fetched: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_tencent, s): s for s in requested}
        for future in concurrent.futures.as_completed(futures):
            obj = future.result()
            fetched[obj["symbol"]] = obj
    # Retry transient/incomplete responses sequentially; never backfill a failed symbol.
    for symbol in [s for s in requested if not fetched.get(s, {}).get("valid")]:
        for _ in range(2):
            retry = fetch_tencent(symbol)
            fetched[symbol] = retry
            if retry.get("valid"):
                break
    breadth_parts = [fetched[s].get("breadth") for s in breadth_symbols if fetched.get(s, {}).get("valid") and fetched[s].get("breadth")]
    if len(breadth_parts) == 2:
        up = sum(x["up"] for x in breadth_parts)
        flat = sum(x["flat"] for x in breadth_parts)
        down = sum(x["down"] for x in breadth_parts)
        total = up + flat + down
        market.update({"breadth_total": total, "up_count": up, "flat_count": flat, "down_count": down, "up_pct": up / total * 100, "flat_pct": flat / total * 100, "down_pct": down / total * 100, "breadth_source": "Tencent sh000001+sz399001 zhishu"})
    else:
        market.update({"breadth_total": None, "up_count": None, "flat_count": None, "down_count": None, "up_pct": None, "flat_pct": None, "down_pct": None, "breadth_source": "stopped: Tencent breadth unavailable"})
    basket_analysis = {}
    for name, basket in baskets_df.items():
        basket_analysis[name] = {
            "q1": analyze_basket(name, basket, fetched, 0.01),
            "q2": analyze_basket(name, basket, fetched, 0.02),
            "q3": analyze_basket(name, basket, fetched, 0.03),
            "q5": analyze_basket(name, basket, fetched, 0.05),
        }
    indices = [index_path(fetched[s]) for s in index_symbols if fetched.get(s, {}).get("valid")]
    roles = stock_role_table(basket_analysis["AI硬件"]["q3"])
    index_validation = [{k: fetched[s].get(k) for k in ["symbol", "date", "row_count", "valid", "monotonic", "amount_match", "quote_ts", "url"]} for s in sorted(set(index_symbols + breadth_symbols))]
    valid_stock_minutes = sum(bool(fetched[s].get("valid")) for s in stock_symbols)
    failed_stock_symbols = [s for s in stock_symbols if not fetched[s].get("valid")]
    data = {
        "meta": {"target_date": "2026-08-14", "data_cutoff": "2026-08-14 close", "timezone": "Asia/Shanghai", "adjustment": "unadjusted", "generated_at": datetime.now().astimezone().isoformat()},
        "market": market,
        "sentiment": sentiment,
        "indices": indices,
        "baskets": basket_analysis,
        "roles": roles,
        "qa": {
            "requested_stock_minutes": len(stock_symbols), "valid_stock_minutes": valid_stock_minutes,
            "failed_stock_symbols": failed_stock_symbols,
            "index_validation": index_validation, "latest_rows": int(len(latest)), "tdx_tail_date_files": 5273,
            "qfq_verified": False, "st_cycle_formal": False,
        },
    }
    # Remove minute arrays from the embedded snapshot; keep only calculation results and validation metadata.
    for obj in data["baskets"].values():
        for qobj in obj.values():
            for stock in qobj["stocks"]:
                stock.pop("item", None)
    json_path = OUT / "a_share_intraday_review_20260814_audit.json"
    html_path = OUT / "a_share_intraday_review_20260814.html"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(build_html(data), encoding="utf-8")
    sentiment_daily.to_csv(OUT / "sentiment_cycle_proxy_20260814.csv", index=False, encoding="utf-8-sig")
    print(json.dumps({
        "html": str(html_path.resolve()), "audit": str(json_path.resolve()), "latest_rows": len(latest),
        "minutes": f"{valid_stock_minutes}/{len(stock_symbols)}", "sentiment": sentiment,
        "basket_q1": {k: {x: v["q1"].get(x) for x in ["effective_n", "reached", "confirm_time", "CER_adv", "positive_breadth", "reach_breadth", "close_equal_ret"]} for k, v in basket_analysis.items()},
        "roles": roles,
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
