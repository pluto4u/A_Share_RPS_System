"""
================================================================================
A股市场情绪监控  v2.0       market_gambler_watch.py
================================================================================
盘中/盘后均可运行的一站式终端报告：

  [快速模式 ~15秒]  默认
    1. 指数总览  — 8 大指数实时价格 + MA20 趋势 + 量比
    2. 情绪面板  — 涨跌统计 / 涨停·跌停·炸板 / 涨跌比 / 涨幅分布 / 连板梯队
    3. 赚钱效应  — 昨日涨停股今日平均收益
    4. 庄家信号  — 510300 ETF 量比异常检测

  [完整模式 ~3-5分钟]  --full
    5. 全市场广度 — 收盘 > MA20 占比，冰点警报
    6. RPS 筛选   — RPS>90 & 跌幅<2% Top10

使用:
    python market_gambler_watch.py            # 快速模式 (盘中实时)
    python market_gambler_watch.py --full     # 完整模式 (含 MA20 广度 + RPS)
================================================================================
"""

import io
import os
import sys
import time
import argparse
import warnings
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ── Windows 终端中文修复 ─────────────────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  全局配置                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ── 监控指数 ──
WATCH_INDICES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000688", "科创50"),
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("899050", "北证50"),
]

# ── 参数 ──
MA_PERIOD        = 20       # 均线天数
RPS_PERIOD       = 20       # RPS 计算天数
ETF_CODE         = "510300" # 庄家信号 ETF
VOLUME_MULTI     = 1.5      # ETF 量比阈值
FREEZE_THRESHOLD = 10       # 广度冰点阈值 (%)
TOP_N            = 10       # 最终筛选数量
MAX_WORKERS      = 8        # 并发线程数
RETRY_TIMES      = 2        # API 重试次数
RETRY_DELAY      = 0.5      # 重试间隔 (秒)

# ── 涨幅分布区间 ──
CHANGE_BINS = [
    (-999, -7,  "< -7% "),
    ( -7,  -5,  "-7~-5%"),
    ( -5,  -3,  "-5~-3%"),
    ( -3,  -1,  "-3~-1%"),
    ( -1,   0,  " -1~0%"),
    (  0,   1,  "  0~1%"),
    (  1,   3,  " 1~+3%"),
    (  3,   5,  " 3~+5%"),
    (  5,   7,  " 5~+7%"),
    (  7, 999,  " > +7%"),
]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  工具函数                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def safe_call(func, *args, **kwargs):
    """带重试的 API 调用"""
    for attempt in range(RETRY_TIMES):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception:
            if attempt < RETRY_TIMES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def banner(text):
    print(f"\n{'=' * 66}")
    print(f"  {text}")
    print(f"{'=' * 66}")


def section(title):
    pad = max(0, 56 - len(title) * 2)
    print(f"\n  {'─' * 3} {title} {'─' * pad}")


def ascii_bar(n, max_n, width=20):
    """简易 ASCII 柱形"""
    if max_n <= 0:
        return ""
    filled = int(n / max_n * width)
    return "\u2588" * filled


def detect_market_status():
    """判断当前盘面状态: 盘前 / 盘中 / 午休 / 盘后 / 休市"""
    now = datetime.now()
    if now.weekday() >= 5:
        return "休市"
    t = now.hour * 100 + now.minute
    if t < 925:
        return "盘前"
    if t <= 1130:
        return "盘中"
    if t < 1300:
        return "午休"
    if t <= 1500:
        return "盘中"
    return "盘后"


def find_trade_dates():
    """
    通过沪深300指数历史确定最近两个交易日。
    返回 (latest_date, previous_date)  格式 YYYYMMDD
    """
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = safe_call(
        ak.index_zh_a_hist,
        symbol="000300", period="daily",
        start_date=start, end_date=end,
    )
    if df is None or len(df) < 2:
        return None, None
    dates = df["日期"].tolist()
    return str(dates[-1]).replace("-", ""), str(dates[-2]).replace("-", "")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  数据采集层                                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def fetch_spot():
    """
    获取全市场 A 股实时行情。
    返回 (all_df, filtered_df)
      all_df:      含 ST、北交所，用于涨跌统计
      filtered_df: 去 ST、北交所、停牌，用于后续分析
    """
    print("  [1] 拉取全市场行情 ...", end=" ", flush=True)
    df = safe_call(ak.stock_zh_a_spot_em)
    if df is None or df.empty:
        print("失败!")
        sys.exit(1)

    # 全量（仅去停牌）
    all_df = df[df["最新价"].notna() & (df["最新价"] > 0)].copy()

    # 过滤版
    flt = all_df.copy()
    flt = flt[~flt["名称"].str.contains("ST|退", na=False)]
    flt = flt[~flt["代码"].str.startswith(("8", "4"))]

    print(f"全量 {len(all_df)} / 过滤 {len(flt)}")
    return all_df, flt.reset_index(drop=True)


def fetch_index_overview():
    """
    获取所有监控指数: 最新价 + 涨跌幅 + MA20状态 + 量比。
    通过 index_zh_a_hist 获取近60天日线计算。
    """
    print("  [2] 拉取指数数据 ", end="", flush=True)
    results = []
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

    for code, name in WATCH_INDICES:
        hist = safe_call(
            ak.index_zh_a_hist,
            symbol=code, period="daily",
            start_date=start_date, end_date=end_date,
        )
        if hist is None or hist.empty:
            results.append({"name": name, "error": True})
            print("x", end="", flush=True)
            continue

        latest = hist.iloc[-1]
        close = float(latest["收盘"])
        change_pct = float(latest["涨跌幅"]) if "涨跌幅" in hist.columns else 0.0
        volume = float(latest["成交量"]) if "成交量" in hist.columns else 0
        trade_date = str(latest["日期"]).replace("-", "")

        # MA20
        ma20_val = None
        ma20_status = "N/A"
        if len(hist) >= MA_PERIOD:
            ma20_val = float(hist["收盘"].rolling(MA_PERIOD).mean().iloc[-1])
            ma20_status = "站上" if close > ma20_val else "跌破"

        # 量比 = 今日成交量 / 前5日均量
        vol_ratio = 0.0
        if len(hist) >= 6 and "成交量" in hist.columns:
            avg5 = float(hist["成交量"].iloc[-6:-1].mean())
            vol_ratio = volume / avg5 if avg5 > 0 else 0.0

        results.append({
            "name": name, "close": close, "change_pct": change_pct,
            "ma20_status": ma20_status, "ma20_val": ma20_val,
            "vol_ratio": vol_ratio, "trade_date": trade_date, "error": False,
        })
        print(".", end="", flush=True)

    print(" 完成")
    return results


def fetch_zt_pools(date_str):
    """
    获取涨停 / 跌停 / 炸板池。
    如果 date_str 无数据（非交易日），自动往前搜索。
    """
    print(f"  [3] 拉取涨跌停数据 ({date_str}) ...", end=" ", flush=True)

    zt_df = safe_call(ak.stock_zt_pool_em, date=date_str)

    # 若指定日期没数据，向前搜索
    actual_date = date_str
    if zt_df is None or zt_df.empty:
        for i in range(1, 10):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            zt_df = safe_call(ak.stock_zt_pool_em, date=d)
            if zt_df is not None and not zt_df.empty:
                actual_date = d
                break

    # 跌停 & 炸板（同一日期）
    dt_df = safe_call(ak.stock_zt_pool_dtgc_em, date=actual_date)
    zb_df = safe_call(ak.stock_zt_pool_zbgc_em, date=actual_date)

    zt_n = len(zt_df) if zt_df is not None and not zt_df.empty else 0
    dt_n = len(dt_df) if dt_df is not None and not dt_df.empty else 0
    zb_n = len(zb_df) if zb_df is not None and not zb_df.empty else 0

    print(f"涨停 {zt_n} / 跌停 {dt_n} / 炸板 {zb_n}  (日期 {actual_date})")

    return {
        "date": actual_date,
        "zt_df": zt_df if zt_df is not None else pd.DataFrame(),
        "dt_df": dt_df if dt_df is not None else pd.DataFrame(),
        "zb_df": zb_df if zb_df is not None else pd.DataFrame(),
        "zt_n": zt_n, "dt_n": dt_n, "zb_n": zb_n,
    }


def fetch_etf_volume():
    """获取 510300 ETF 量比"""
    print(f"  [4] 拉取 {ETF_CODE} ETF 量比 ...", end=" ", flush=True)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = safe_call(
        ak.fund_etf_hist_em,
        symbol=ETF_CODE, period="daily",
        start_date=start, end_date=end, adjust="qfq",
    )
    if df is None or len(df) < 6:
        print("数据不足")
        return None, None, None

    vol_today = float(df["成交量"].iloc[-1])
    vol_avg5 = float(df["成交量"].iloc[-6:-1].mean())
    ratio = vol_today / vol_avg5 if vol_avg5 > 0 else 0.0
    print(f"量比 {ratio:.2f}x")
    return vol_today, vol_avg5, ratio


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  数据采集 — 完整模式专用 (慢)                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _fetch_one_hist(code, start, end):
    """线程池: 获取单只股票历史 K 线"""
    try:
        h = safe_call(
            ak.stock_zh_a_hist,
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq",
        )
        if h is not None and len(h) >= MA_PERIOD:
            return code, h
    except Exception:
        pass
    return code, None


def fetch_history_batch(codes):
    """多线程下载全市场 K 线 (完整模式)"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

    print(f"\n  [FULL] 下载 {len(codes)} 只股票历史 K 线 ...")
    data = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_fetch_one_hist, c, start, end): c for c in codes}
        with tqdm(total=len(codes), desc="  下载进度", ncols=72, unit="只") as pbar:
            for f in as_completed(futs):
                code, h = f.result()
                if h is not None:
                    data[code] = h
                pbar.update(1)

    print(f"  成功 {len(data)} / 失败 {len(codes) - len(data)}")
    return data


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — 指数总览                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def show_indices(idx_data):
    section("指数总览")
    hdr = f"  {'指数':<10}{'最新价':>10}{'涨跌幅':>10}{'MA20':>8}{'量比':>8}"
    print(hdr)
    print("  " + "-" * 46)

    for d in idx_data:
        if d.get("error"):
            print(f"  {d['name']:<10}{'-- 数据异常 --':>36}")
            continue

        # MA20 标记
        ma_tag = d["ma20_status"]
        if ma_tag == "跌破":
            ma_tag = "*跌破"  # 醒目

        # 量比标记
        vr = d["vol_ratio"]
        vr_str = f"{vr:.2f}x"
        if vr > VOLUME_MULTI:
            vr_str += "!"

        print(
            f"  {d['name']:<10}"
            f"{d['close']:>10.2f}"
            f"{d['change_pct']:>+9.2f}%"
            f"{ma_tag:>8}"
            f"{vr_str:>8}"
        )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — 情绪面板                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def show_sentiment(all_spot, zt_pools):
    section("情绪面板")

    changes = all_spot["涨跌幅"].dropna()
    total = len(changes)

    # ── 涨跌统计 ──
    up_n   = int((changes > 0).sum())
    down_n = int((changes < 0).sum())
    flat_n = int((changes == 0).sum())
    ratio  = up_n / down_n if down_n > 0 else float("inf")

    # 涨跌比判定
    if   ratio > 3:    ad_verdict = "极强"
    elif ratio > 1.5:  ad_verdict = "偏强"
    elif ratio > 0.8:  ad_verdict = "均衡"
    elif ratio > 0.3:  ad_verdict = "偏弱"
    else:              ad_verdict = "极弱"

    print(f"  涨跌统计:")
    print(f"    上涨 {up_n:>5}  ({up_n/total*100:>5.1f}%)"
          f"  |  下跌 {down_n:>5}  ({down_n/total*100:>5.1f}%)"
          f"  |  平盘 {flat_n:>4}")

    # ── 涨停 / 跌停 / 炸板 ──
    zt_n, dt_n, zb_n = zt_pools["zt_n"], zt_pools["dt_n"], zt_pools["zb_n"]
    total_board = zt_n + zb_n
    zb_rate = zb_n / total_board * 100 if total_board > 0 else 0

    print(f"    涨停 {zt_n:>5}  |  跌停 {dt_n:>5}  |  炸板 {zb_n:>5}"
          f"  (炸板率 {zb_rate:.1f}%)")

    print(f"    涨跌比: {ratio:.2f}  [{ad_verdict}]")

    # ── 涨幅分布 (ASCII 柱状图) ──
    print(f"\n  涨幅分布:")
    dist = []
    for low, high, label in CHANGE_BINS:
        cnt = int(((changes > low) & (changes <= high)).sum())
        dist.append((label, cnt))

    max_cnt = max(c for _, c in dist) if dist else 1
    for label, cnt in dist:
        bar = ascii_bar(cnt, max_cnt, 22)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"    {label} {bar:<22} {cnt:>5}  ({pct:>5.1f}%)")

    # ── 连板梯队 ──
    zt_df = zt_pools["zt_df"]
    if not zt_df.empty:
        # 找连板列名
        board_col = None
        for col_name in ["连板数", "连板天数"]:
            if col_name in zt_df.columns:
                board_col = col_name
                break

        if board_col:
            print(f"\n  连板梯队:")
            tiers = {}
            for _, row in zt_df.iterrows():
                b = int(row[board_col])
                if b not in tiers:
                    tiers[b] = []
                tiers[b].append(f"{row['代码']}{row['名称']}")

            for boards in sorted(tiers.keys(), reverse=True):
                stocks = tiers[boards]
                tag = f"{boards}连板" if boards > 1 else "首板  "
                detail = ""
                if boards >= 3 and len(stocks) <= 5:
                    detail = "  -> " + ", ".join(stocks)
                elif boards >= 3:
                    detail = f"  -> {', '.join(stocks[:3])} ..."
                print(f"    {tag:>6} : {len(stocks):>3} 只{detail}")

    # ── 情绪小结 ──
    print(f"\n  情绪小结:")
    signals = []
    if ratio > 1.5:
        signals.append("涨跌比强")
    elif ratio < 0.5:
        signals.append("涨跌比弱")
    if zt_n > dt_n * 3 and zt_n > 20:
        signals.append("涨停活跃")
    if dt_n > zt_n:
        signals.append("跌停偏多")
    if zb_rate < 15 and zt_n > 10:
        signals.append("封板质量好")
    elif zb_rate > 40:
        signals.append("炸板率高")

    if not signals:
        signals.append("中性")
    print(f"    {'  |  '.join(signals)}")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — 赚钱效应                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def show_money_effect(spot_df, yesterday_str):
    """计算昨日涨停 → 今日表现"""
    section("赚钱效应 (昨日涨停 -> 今日表现)")

    if yesterday_str is None:
        print("  交易日数据不可用")
        return None

    zt_df = safe_call(ak.stock_zt_pool_em, date=yesterday_str)

    # 若指定日期无数据，往前找
    actual = yesterday_str
    if zt_df is None or zt_df.empty:
        for i in range(1, 10):
            d = (datetime.strptime(yesterday_str, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
            zt_df = safe_call(ak.stock_zt_pool_em, date=d)
            if zt_df is not None and not zt_df.empty:
                actual = d
                break

    if zt_df is None or zt_df.empty:
        print("  未获取到前一交易日涨停数据")
        return None

    zt_codes = zt_df["代码"].tolist()
    matched = spot_df[spot_df["代码"].isin(zt_codes)].copy()

    if matched.empty:
        print(f"  涨停日 {actual}  共 {len(zt_codes)} 只 -> 今日无匹配")
        return None

    avg_ret = float(matched["涨跌幅"].mean())
    median_ret = float(matched["涨跌幅"].median())
    up_pct = (matched["涨跌幅"] > 0).sum() / len(matched) * 100
    max_ret = float(matched["涨跌幅"].max())
    min_ret = float(matched["涨跌幅"].min())

    if avg_ret > 2:     verdict = "[强] 追板大赚"
    elif avg_ret > 0:   verdict = "[好] 追板有肉"
    elif avg_ret > -1:  verdict = "[弱] 追板微亏"
    else:               verdict = "[差] 追板大亏"

    print(f"  涨停日      : {actual}  ({len(zt_codes)} 只)")
    print(f"  今日均收益   : {avg_ret:+.2f}%   (中位数 {median_ret:+.2f}%)")
    print(f"  上涨比例    : {up_pct:.0f}%")
    print(f"  极值        : 最高 {max_ret:+.2f}%  /  最低 {min_ret:+.2f}%")
    print(f"  判定        : {verdict}")
    return avg_ret


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — 庄家信号                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def show_etf_signal(vol_today, vol_avg5, vol_ratio):
    section(f"庄家信号 ({ETF_CODE} ETF)")
    if vol_ratio is None:
        print("  数据不可用")
        return

    alert = "!! 放量异常 !!" if vol_ratio > VOLUME_MULTI else "正常"
    print(f"  今日成交量   : {vol_today / 1e4:>10,.0f} 万手")
    print(f"  5 日均量     : {vol_avg5 / 1e4:>10,.0f} 万手")
    print(f"  量比         : {vol_ratio:.2f}x     [{alert}]")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — 全市场广度 (完整模式)                                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def analyze_breadth(hist_data):
    """收盘 > MA20 占比"""
    above = total = 0
    for code, df in hist_data.items():
        closes = df["收盘"]
        if len(closes) < MA_PERIOD:
            continue
        ma = float(closes.rolling(MA_PERIOD).mean().iloc[-1])
        if np.isnan(ma):
            continue
        total += 1
        if float(closes.iloc[-1]) > ma:
            above += 1
    pct = above / total * 100 if total > 0 else 0.0
    return pct, above, total


def show_breadth(pct, above, total):
    section("全市场广度 (收盘 vs MA20)")
    is_freeze = pct < FREEZE_THRESHOLD
    print(f"  收盘 > MA{MA_PERIOD}  : {pct:.1f}%  ({above} / {total})")
    if is_freeze:
        print()
        print("  !!  =============================================  !!")
        print(f"  !!    冰点行情!  仅 {pct:.1f}% 站上均线                  !!")
        print("  !!    极端缩量/杀跌区域，关注超跌反弹机会            !!")
        print("  !!  =============================================  !!")
    return is_freeze


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  分析 & 展示 — RPS 筛选 (完整模式)                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def rps_screen(hist_data, spot_df):
    """计算全市场 RPS -> 筛选 RPS>90 & 跌幅<2%"""
    returns = {}
    for code, df in hist_data.items():
        closes = df["收盘"].values
        if len(closes) >= RPS_PERIOD:
            ret = (closes[-1] / closes[-RPS_PERIOD] - 1) * 100
            returns[code] = ret

    if not returns:
        return pd.DataFrame()

    ret_s = pd.Series(returns)
    rps = ret_s.rank(pct=True) * 100

    hi_codes = rps[rps > 90].index.tolist()
    result = spot_df[spot_df["代码"].isin(hi_codes)].copy()
    result = result[result["涨跌幅"] > -2]
    result["RPS"] = result["代码"].map(rps).round(1)
    result = result.sort_values("RPS", ascending=False).head(TOP_N)
    return result[["代码", "名称", "最新价", "涨跌幅", "RPS"]].reset_index(drop=True)


def show_rps_result(result, reasons):
    section(f"触发筛选 ({' + '.join(reasons)})")
    print(f"  规则: RPS({RPS_PERIOD}日) > 90  且  今日跌幅 < 2%  Top {TOP_N}")
    print()

    if result.empty:
        print("  (无符合条件的股票)")
        return

    hdr = f"  {'#':>3}  {'代码':<8}{'名称':<10}{'最新价':>8}{'涨跌幅':>10}{'RPS':>8}"
    sep = "  " + "-" * 50
    print(hdr)
    print(sep)
    for idx, row in result.iterrows():
        print(
            f"  {idx+1:>3}  "
            f"{row['代码']:<8}"
            f"{row['名称']:<10}"
            f"{row['最新价']:>8.2f}"
            f"{row['涨跌幅']:>+9.2f}%"
            f"{row['RPS']:>8.1f}"
        )
    print(sep)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  主函数                                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def main():
    parser = argparse.ArgumentParser(description="A股市场情绪监控 v2.0")
    parser.add_argument("--full", action="store_true",
                        help="完整模式: 包含 MA20 广度 + RPS 筛选 (耗时 3-5 分钟)")
    parser.add_argument("--feishu", action="store_true",
                        help="推送结果到飞书群 (需先配置 feishu_config.json)")
    args = parser.parse_args()

    wall_start = time.time()
    status = detect_market_status()
    mode_label = "完整模式" if args.full else "快速模式"

    banner(
        f"A股市场情绪监控  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        f"  [{status}] [{mode_label}]"
    )

    # ══════════════════════════════════════════════════════════════════════════
    #  第一阶段: 数据采集 (快速)
    # ══════════════════════════════════════════════════════════════════════════
    latest_date, prev_date = find_trade_dates()
    if latest_date:
        print(f"  交易日: 最新 {latest_date} / 前一 {prev_date}")

    all_spot, filtered_spot = fetch_spot()
    idx_data   = fetch_index_overview()
    today_str  = latest_date or datetime.now().strftime("%Y%m%d")
    zt_pools   = fetch_zt_pools(today_str)
    vol_today, vol_avg5, vol_ratio = fetch_etf_volume()

    # ══════════════════════════════════════════════════════════════════════════
    #  第二阶段: 快速分析 & 展示
    # ══════════════════════════════════════════════════════════════════════════
    banner("分 析 报 告")

    show_indices(idx_data)
    show_sentiment(all_spot, zt_pools)
    avg_ret = show_money_effect(filtered_spot, prev_date)
    show_etf_signal(vol_today, vol_avg5, vol_ratio)

    # ══════════════════════════════════════════════════════════════════════════
    #  第三阶段: 完整模式 (慢) — MA20 广度 + RPS 筛选
    # ══════════════════════════════════════════════════════════════════════════
    if args.full:
        banner("深度分析 (完整模式)")
        hist_data = fetch_history_batch(filtered_spot["代码"].tolist())

        # 广度
        breadth_pct, above, total = analyze_breadth(hist_data)
        is_freeze = show_breadth(breadth_pct, above, total)

        # 筛选条件
        breadth_low = is_freeze
        money_good = avg_ret is not None and avg_ret > 0

        if breadth_low or money_good:
            reasons = []
            if breadth_low:
                reasons.append("广度冰点")
            if money_good:
                reasons.append("赚钱效应好")
            result = rps_screen(hist_data, filtered_spot)
            show_rps_result(result, reasons)
        else:
            section("最终筛选")
            print(f"  未触发。条件:")
            print(f"    - 广度 < {FREEZE_THRESHOLD}% (当前 {breadth_pct:.1f}%)")
            money_str = f"{avg_ret:+.2f}%" if avg_ret is not None else "N/A"
            print(f"    - 赚钱效应 > 0  (当前 {money_str})")
    else:
        print(f"\n  提示: 运行  python market_gambler_watch.py --full")
        print(f"        可获取 MA20 广度 + RPS 筛选 (耗时 3-5 分钟)")

    # ══════════════════════════════════════════════════════════════════════════
    #  飞书推送 (可选)
    # ══════════════════════════════════════════════════════════════════════════
    if args.feishu:
        try:
            from feishu_bot import push_market_report

            report_data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status": status,
                "idx_data": idx_data,
                "all_spot": all_spot,
                "zt_pools": zt_pools,
                "money_effect": avg_ret,
                "etf_signal": {
                    "vol_today": vol_today,
                    "vol_avg5": vol_avg5,
                    "ratio": vol_ratio,
                },
            }
            # 完整模式下附带广度数据
            try:
                if args.full and breadth_pct is not None:
                    report_data["breadth"] = {
                        "pct": breadth_pct, "above": above, "total": total,
                    }
            except NameError:
                pass  # 快速模式下无广度变量
            push_market_report(report_data)
        except ImportError:
            print("\n  [飞书] feishu_bot.py 模块未找到，跳过推送")
        except Exception as e:
            print(f"\n  [飞书] 推送异常: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  结束
    # ══════════════════════════════════════════════════════════════════════════
    elapsed = time.time() - wall_start
    m, s = divmod(elapsed, 60)

    print()
    print("=" * 66)
    print(f"  完成  |  耗时 {int(m)} 分 {s:.0f} 秒  |  {mode_label}")
    print("=" * 66)
    print()


if __name__ == "__main__":
    main()
