"""
================================================================================
板块资金流入 — 网页数据采集 (唯一需要联网的模块)
================================================================================
关键设计: **不直接抓"行业资金流排名", 而是抓个股资金流再按 TDX 行业自己汇总。**

原因: 东财/同花顺的行业分类与通达信 tdxhy.cfg 不是一套口径, 用行业名做
模糊匹配会有 20~30% 的行业对不上, 而且每次网站改版都会崩。改抓个股级
数据 (含 6 位代码) 再用我们自己的行业映射聚合, 口径 100% 与选股一致,
且接口稳定性高得多。

主通道 : akshare.stock_individual_fund_flow_rank  (全 A 个股主力净流入)
备用通道: 东方财富 push2 公开 JSON 接口 (akshare 挂掉时兜底)
降级    : 全部失败时返回空表, macro.sector 会自动把该分项权重分摊掉,
          整个体系仍可正常运行, 只是少一个维度。

缓存: 同一交易日只抓一次, 落盘到 output/moneyflow_{indicator}_{date}.csv
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

CACHE_DIR = Path("output")

# akshare 的 indicator 取值
VALID_INDICATORS = ("今日", "3日", "5日", "10日")


# =============================================================================
# 采集
# =============================================================================

def _fetch_via_akshare(indicator: str = "5日") -> Optional[pd.DataFrame]:
    """
    主通道: akshare 个股资金流排名。

    返回列 (已标准化): ts_code / main_net_inflow / amount_proxy
    """
    try:
        import akshare as ak
    except ImportError:
        print("[MoneyFlow] 未安装 akshare, 跳过主通道")
        return None

    for attempt in range(3):
        try:
            df = ak.stock_individual_fund_flow_rank(indicator=indicator)
            if df is None or df.empty:
                raise ValueError("返回空表")
            return _normalize_em_frame(df, indicator)
        except Exception as e:
            if attempt == 2:
                print(f"[MoneyFlow] akshare 三次失败: {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _normalize_em_frame(df: pd.DataFrame, indicator: str) -> pd.DataFrame:
    """
    把东财返回的中文列名标准化。

    东财列名随 indicator 变化, 例如 5 日档位是 "5日主力净流入-净额"。
    这里做前缀模糊匹配, 避免硬编码。
    """
    df = df.copy()
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    if code_col is None:
        raise ValueError(f"找不到代码列: {list(df.columns)[:8]}")

    inflow_col = next(
        (c for c in df.columns
         if "主力净流入" in str(c) and "净额" in str(c)),
        None,
    )
    if inflow_col is None:
        inflow_col = next((c for c in df.columns if "主力净流入" in str(c)), None)
    if inflow_col is None:
        raise ValueError(f"找不到主力净流入列: {list(df.columns)[:8]}")

    out = pd.DataFrame({
        "code": df[code_col].astype(str).str.zfill(6),
        "main_net_inflow": pd.to_numeric(df[inflow_col], errors="coerce"),
    })

    # 最新价/涨跌幅列用于诊断, 有则带上
    price_col = next((c for c in df.columns if "最新价" in str(c)), None)
    if price_col:
        out["last_price"] = pd.to_numeric(df[price_col], errors="coerce")

    out["ts_code"] = out["code"].map(_code_to_ts)
    out = out.dropna(subset=["ts_code", "main_net_inflow"])
    out.attrs["indicator"] = indicator
    return out[["ts_code", "code", "main_net_inflow"]
               + (["last_price"] if price_col else [])]


def _code_to_ts(code: str) -> Optional[str]:
    """6 位代码 -> ts_code (与 tdx_reader 口径一致)"""
    if not isinstance(code, str) or len(code) != 6:
        return None
    if code[:3] in ("600", "601", "603", "605", "688", "689"):
        return f"{code}.SH"
    if code[:3] in ("000", "001", "002", "003", "300", "301"):
        return f"{code}.SZ"
    return None


def _fetch_via_eastmoney(indicator: str = "5日") -> Optional[pd.DataFrame]:
    """
    备用通道: 直接调用东方财富 push2 公开接口。

    akshare 内部走的也是这个端点, 单独实现一份是为了在 akshare 版本
    落后 / 字段改名时仍能取到数据。
    """
    try:
        import requests
    except ImportError:
        return None

    # fid 对应不同周期的主力净额排序字段
    fid_map = {"今日": "f62", "3日": "f267", "5日": "f164", "10日": "f174"}
    fields_map = {
        "今日": "f12,f14,f62",
        "3日": "f12,f14,f267",
        "5日": "f12,f14,f164",
        "10日": "f12,f14,f174",
    }
    fid = fid_map.get(indicator, "f164")
    fields = fields_map.get(indicator, "f12,f14,f164")

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    rows = []
    try:
        for page in range(1, 15):           # 每页 500, 最多 7000 条
            params = {
                "pn": page, "pz": 500, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": fid,
                "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
                      "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
                "fields": fields,
            }
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            rows.extend(diff)
            if len(diff) < 500:
                break
    except Exception as e:
        print(f"[MoneyFlow] 东财备用通道失败: {e}")
        return None

    if not rows:
        return None

    inflow_field = fid
    out = pd.DataFrame({
        "code": [str(r.get("f12", "")).zfill(6) for r in rows],
        "main_net_inflow": [pd.to_numeric(r.get(inflow_field), errors="coerce")
                            for r in rows],
    })
    out["ts_code"] = out["code"].map(_code_to_ts)
    out = out.dropna(subset=["ts_code", "main_net_inflow"])
    return out[["ts_code", "code", "main_net_inflow"]]


def fetch_stock_moneyflow(
    indicator: str = "5日",
    use_cache: bool = True,
    cache_dir: Path = CACHE_DIR,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    获取全 A 个股主力净流入。失败时返回空 DataFrame (体系自动降级)。

    参数
    ----
    indicator : "今日" | "3日" | "5日" | "10日"
        建议用 5 日 —— 单日资金流噪声过大, 10 日又太钝。
    use_cache : bool
        同一天重复调用直接读缓存, 避免反复打接口被限流。
    """
    if indicator not in VALID_INDICATORS:
        raise ValueError(f"indicator 必须是 {VALID_INDICATORS} 之一")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(exist_ok=True, parents=True)
    today = datetime.now().strftime("%Y%m%d")
    cache_path = cache_dir / f"moneyflow_{indicator}_{today}.csv"

    if use_cache and cache_path.exists():
        if verbose:
            print(f"[MoneyFlow] 命中缓存 {cache_path.name}")
        return pd.read_csv(cache_path, dtype={"code": str})

    df = _fetch_via_akshare(indicator)
    if df is None or df.empty:
        if verbose:
            print("[MoneyFlow] 主通道无数据, 切换东财备用通道")
        df = _fetch_via_eastmoney(indicator)

    if df is None or df.empty:
        print("[MoneyFlow] 全部通道失败 —— 资金流分项将被跳过, 其余因子照常工作")
        return pd.DataFrame(columns=["ts_code", "code", "main_net_inflow"])

    df.to_csv(cache_path, index=False, encoding="utf-8-sig")
    if verbose:
        print(f"[MoneyFlow] 取到 {len(df)} 只个股 ({indicator}), "
              f"缓存至 {cache_path.name}")
    return df


# =============================================================================
# 按 TDX 行业聚合
# =============================================================================

def aggregate_to_industry(
    stock_flow: pd.DataFrame,
    industry_map: pd.DataFrame,
    industry_amount: Optional[pd.Series] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    个股资金流 -> TDX 行业资金流。

    参数
    ----
    stock_flow : DataFrame
        fetch_stock_moneyflow 的输出
    industry_map : DataFrame
        ts_code / industry
    industry_amount : Series, optional
        索引为 industry 的行业成交额 (元)。给定时会算出
        net_inflow_ratio = 净流入 / 成交额, 这个比值比绝对额有用得多 ——
        绝对额永远是大行业排前面, 而比值才反映"资金相对于自身体量的偏好"。

    返回
    ----
    DataFrame, index=industry, 列:
        net_inflow        主力净流入合计 (元)
        n_stocks          有资金流数据的个股数
        inflow_stock_pct  行业内净流入为正的个股占比
        net_inflow_ratio  净流入 / 行业成交额 (industry_amount 给定时)
    """
    if stock_flow is None or stock_flow.empty:
        return pd.DataFrame()

    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    sf = stock_flow.copy()
    sf["industry"] = sf["ts_code"].map(ind)
    sf = sf.dropna(subset=["industry"])

    if sf.empty:
        if verbose:
            print("[MoneyFlow] 个股资金流与行业映射无交集")
        return pd.DataFrame()

    g = sf.groupby("industry")["main_net_inflow"]
    out = pd.DataFrame({
        "net_inflow": g.sum(),
        "n_stocks": g.count(),
        "inflow_stock_pct": sf.assign(pos=sf["main_net_inflow"] > 0)
                              .groupby("industry")["pos"].mean(),
    })

    if industry_amount is not None:
        amt = industry_amount.reindex(out.index)
        out["industry_amount"] = amt
        out["net_inflow_ratio"] = out["net_inflow"] / amt.replace(0, np.nan)
    else:
        # 没有成交额时退而用净流入的横截面排名做标准化
        out["net_inflow_ratio"] = out["net_inflow"].rank(pct=True)

    out = out.sort_values("net_inflow_ratio", ascending=False)

    if verbose:
        print(f"[MoneyFlow] 聚合到 {len(out)} 个行业, "
              f"覆盖 {int(out['n_stocks'].sum())} 只个股")
    return out


def get_industry_moneyflow(
    industry_map: pd.DataFrame,
    industry_amount: Optional[pd.Series] = None,
    indicator: str = "5日",
    use_cache: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """一步到位: 抓取 + 聚合。任何环节失败都返回空表, 由调用方降级处理。"""
    sf = fetch_stock_moneyflow(indicator=indicator, use_cache=use_cache,
                               verbose=verbose)
    return aggregate_to_industry(sf, industry_map, industry_amount,
                                 verbose=verbose)
