"""
本周交易日情绪回顾：拉取每日涨停/跌停/炸板 + 指数涨跌 + ETF量比，计算每日情绪评分
"""
import io, os, sys, time, warnings
from datetime import datetime, timedelta

import akshare as ak
import numpy as np
import pandas as pd

from sentiment_engine import score_to_position, final_position

warnings.filterwarnings("ignore")

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("")


def safe_call(func, *args, **kwargs):
    for attempt in range(3):
        try:
            return func(*args, **kwargs)
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    return None


def get_trade_dates_this_week():
    """获取本周的交易日列表"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
    df = safe_call(ak.index_zh_a_hist, symbol="000300", period="daily",
                   start_date=start, end_date=end)
    if df is None or df.empty:
        return []
    
    df["日期"] = pd.to_datetime(df["日期"])
    # 本周一的日期
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    
    week_dates = df[df["日期"] >= monday]["日期"].tolist()
    return week_dates, df


def compute_daily_sentiment(date_str, idx_hist_df):
    """计算某一天的情绪评分"""
    result = {"date": date_str}
    
    # 1. 涨停/跌停/炸板
    zt_df = safe_call(ak.stock_zt_pool_em, date=date_str)
    dt_df = safe_call(ak.stock_zt_pool_dtgc_em, date=date_str)
    zb_df = safe_call(ak.stock_zt_pool_zbgc_em, date=date_str)
    
    zt_n = len(zt_df) if zt_df is not None and not zt_df.empty else 0
    dt_n = len(dt_df) if dt_df is not None and not dt_df.empty else 0
    zb_n = len(zb_df) if zb_df is not None and not zb_df.empty else 0
    total_board = zt_n + zb_n
    zb_rate = zb_n / total_board * 100 if total_board > 0 else 50
    
    result["zt_n"] = zt_n
    result["dt_n"] = dt_n
    result["zb_n"] = zb_n
    result["zb_rate"] = round(zb_rate, 1)
    
    # 涨跌停比得分 (20%)
    zt_ratio = zt_n / dt_n if dt_n > 0 else 10
    if   zt_ratio > 5:   result["score_zt"] = 100
    elif zt_ratio > 3:   result["score_zt"] = 75
    elif zt_ratio > 1.5: result["score_zt"] = 50
    elif zt_ratio > 1:   result["score_zt"] = 25
    else:                result["score_zt"] = 0
    
    # 炸板率得分 (15%)
    if   zb_rate < 15: result["score_zb"] = 100
    elif zb_rate < 25: result["score_zb"] = 75
    elif zb_rate < 35: result["score_zb"] = 50
    elif zb_rate < 50: result["score_zb"] = 25
    else:              result["score_zb"] = 0
    
    # 2. 指数涨跌 (沪深300)
    date_dt = pd.to_datetime(date_str)
    row = idx_hist_df[idx_hist_df["日期"] == date_dt]
    if not row.empty:
        result["hs300_close"] = float(row["收盘"].values[0])
        result["hs300_change"] = float(row["涨跌幅"].values[0])
        
        # 计算该日 MA20
        idx = idx_hist_df[idx_hist_df["日期"] <= date_dt].tail(20)
        if len(idx) >= 20:
            ma20 = float(idx["收盘"].mean())
            result["hs300_ma20"] = round(ma20, 2)
            result["above_ma20"] = result["hs300_close"] > ma20
        else:
            result["above_ma20"] = None
    
    # 3. ETF 量比
    etf_end = date_str
    etf_start = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    etf_df = safe_call(ak.fund_etf_hist_em, symbol="510300", period="daily",
                       start_date=etf_start, end_date=etf_end, adjust="qfq")
    if etf_df is not None and len(etf_df) >= 6:
        etf_row = etf_df[etf_df["日期"] == pd.to_datetime(date_str).strftime("%Y-%m-%d")]
        if etf_row.empty:
            etf_row = etf_df.iloc[[-1]]
        vol_today = float(etf_row["成交量"].values[0])
        vol_avg5 = float(etf_df["成交量"].iloc[-6:-1].mean())
        result["etf_vol_ratio"] = round(vol_today / vol_avg5, 2) if vol_avg5 > 0 else 0
    else:
        result["etf_vol_ratio"] = None
    
    # ETF量比得分 (15%)
    vr = result.get("etf_vol_ratio")
    if   vr is None:                  result["score_etf"] = 50
    elif 1.0 <= vr <= 1.5:           result["score_etf"] = 100
    elif 0.8 <= vr < 1.0:            result["score_etf"] = 65
    elif 1.5 < vr <= 2.0:            result["score_etf"] = 50
    else:                             result["score_etf"] = 25
    
    # 4. 简化涨跌比得分 (25%) — 用指数涨跌幅近似
    chg = result.get("hs300_change", 0)
    if   chg > 1.5: result["score_ad"] = 100
    elif chg > 0.5: result["score_ad"] = 75
    elif chg > 0:   result["score_ad"] = 50
    elif chg > -1:  result["score_ad"] = 25
    else:           result["score_ad"] = 0
    
    # 5. 赚钱效应得分 (25%) — 需要前一天涨停股的今日表现，此处简化
    # 用"涨停数 vs 跌停数"和指数涨跌近似
    if zt_n > 30 and zt_ratio > 2:    result["score_money"] = 75
    elif zt_n > 20 and zt_ratio > 1:  result["score_money"] = 50
    elif zt_ratio > 1:                result["score_money"] = 40
    else:                             result["score_money"] = 20
    
    # 加权总分
    weights = {"score_ad": 0.25, "score_money": 0.25, "score_zt": 0.20,
               "score_zb": 0.15, "score_etf": 0.15}
    total = sum(result.get(k, 50) * w for k, w in weights.items())
    result["score"] = round(total)
    
    # 仓位映射
    result["position_raw"] = score_to_position(result["score"])
    
    # 趋势门控
    trend_gate = 100 if result.get("above_ma20", True) else 0
    result["trend_gate"] = trend_gate
    result["position_final"] = final_position(result["position_raw"], trend_gate, 100)
    
    return result


def main():
    print("=" * 70)
    print("  本周交易日情绪回顾")
    print("=" * 70)
    
    dates, idx_df = get_trade_dates_this_week()
    if not dates:
        print("  无法获取本周交易日，请检查网络")
        return
    
    print(f"  本周交易日: {len(dates)} 天")
    print()
    
    results = []
    for dt in dates:
        date_str = dt.strftime("%Y%m%d")
        print(f"  正在计算 {date_str} ...", end=" ", flush=True)
        r = compute_daily_sentiment(date_str, idx_df)
        results.append(r)
        print(f"评分 {r['score']}/100  仓位 {r['position_final']}%")
        time.sleep(0.3)
    
    # 汇总表
    print()
    print("=" * 70)
    print(f"  {'日期':>10}  {'沪深300':>10}  {'涨跌%':>7}  {'MA20':>6}  "
          f"{'涨停':>4}  {'跌停':>4}  {'炸板率':>6}  {'ETF量比':>7}  "
          f"{'评分':>4}  {'建议仓位':>8}  {'最终仓位':>8}")
    print("  " + "-" * 66)
    
    for r in results:
        ma_tag = "站上" if r.get("above_ma20") else "跌破"
        etf_str = f"{r['etf_vol_ratio']:.2f}x" if r.get("etf_vol_ratio") else "N/A"
        print(
            f"  {r['date']:>10}  "
            f"{r.get('hs300_close', 0):>10.2f}  "
            f"{r.get('hs300_change', 0):>+6.2f}%  "
            f"{ma_tag:>6}  "
            f"{r['zt_n']:>4}  "
            f"{r['dt_n']:>4}  "
            f"{r['zb_rate']:>5.1f}%  "
            f"{etf_str:>7}  "
            f"{r['score']:>4}  "
            f"{r['position_raw']:>7}%  "
            f"{r['position_final']:>7}%"
        )
    
    # 周度总结
    print()
    print("  " + "=" * 66)
    avg_score = np.mean([r["score"] for r in results])
    min_score = min(r["score"] for r in results)
    max_score = max(r["score"] for r in results)
    days_below_ma = sum(1 for r in results if not r.get("above_ma20", True))
    
    print(f"  周度情绪均分  : {avg_score:.0f}/100")
    print(f"  评分区间      : {min_score} ~ {max_score}")
    print(f"  跌破MA20天数  : {days_below_ma}/{len(results)}")
    
    if avg_score >= 60:
        verdict = "偏强 — 可适当加仓"
    elif avg_score >= 40:
        verdict = "中性 — 精选个股，控制仓位"
    elif avg_score >= 20:
        verdict = "偏弱 — 防御为主，极度精选"
    else:
        verdict = "极弱 — 空仓等待，不要抄底"
    
    if days_below_ma >= 3:
        verdict += "  [趋势门控:关闭]"
    
    print(f"  周度判定      : {verdict}")
    print("  " + "=" * 66)


if __name__ == "__main__":
    main()
