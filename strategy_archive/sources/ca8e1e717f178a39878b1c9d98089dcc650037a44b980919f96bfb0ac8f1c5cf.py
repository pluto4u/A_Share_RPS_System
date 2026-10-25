"""
================================================================================
A 股 RPS 选股与风控系统 v1.0
================================================================================
基于 akshare 开源数据接口，零成本实现量化选股。

核心模块:
    1. 大盘风控模块 (Market Timing)   — 沪深300 MA20 趋势判断
    2. 基础清洗模块 (Data Filtering)   — 剔除 ST / 次新 / 停牌 / 微盘
    3. RPS 选股模块 (Relative Price Strength) — 50日 & 120日相对强度排名
    4. 结果输出模块 (Output)            — Excel 输出 + 终端汇总

使用方法:
    pip install -r requirements.txt
    python rps_system.py
================================================================================
"""

import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
import numpy as np
import pandas as pd
from tqdm import tqdm

from project_paths import EXPORTS_DIR

warnings.filterwarnings("ignore")

def _configure_windows_console():
    """Configure a real console without replacing pytest/Jupyter streams."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    os.system("")  # 启用 ANSI 转义序列支持


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  全局配置                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class Config:
    """系统可调参数，集中管理"""

    # --- 大盘风控 ---
    INDEX_CODE = "000300"           # 沪深300指数代码
    MA_PERIOD = 20                  # 均线周期（交易日）

    # --- 数据清洗 ---
    MIN_MARKET_CAP = 30e8           # 最低总市值阈值（30 亿元）
    MIN_HISTORY_DAYS = 120          # 最少需要的历史交易日数（约半年）

    # --- RPS 参数 ---
    RPS_SHORT_PERIOD = 50           # 短周期天数
    RPS_LONG_PERIOD = 120           # 长周期天数
    RPS_SHORT_THRESHOLD = 90        # 短周期 RPS 阈值
    RPS_LONG_THRESHOLD = 85         # 长周期 RPS 阈值

    # --- 并发 & IO ---
    MAX_WORKERS = 8                 # 历史行情并发线程数
    RETRY_TIMES = 3                 # 单次请求最大重试
    RETRY_DELAY = 0.5               # 重试间隔（秒）
    OUTPUT_DIR = EXPORTS_DIR / "rps"  # 结果导出目录


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  工具函数                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def safe_api_call(func, *args, retries=Config.RETRY_TIMES, **kwargs):
    """
    带重试机制的 API 调用封装。
    akshare 底层依赖网络请求，偶尔会超时或被限流，重试可大幅提升成功率。
    """
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(Config.RETRY_DELAY * (attempt + 1))
            else:
                raise exc


def print_banner():
    """打印系统启动横幅"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print()
    print("=" * 62)
    print("||   A 股 RPS 选股与风控系统  v1.0                           ||")
    print("||   数据来源: akshare (开源免费)                             ||")
    print(f"||   启动时间: {now_str}                        ||")
    print("=" * 62)


def print_section(module_num, title):
    """打印模块分隔标题"""
    print()
    print("-" * 62)
    print(f"  【模块{module_num}】{title}")
    print("-" * 62)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  模块 1 —— 大盘风控 (Market Timing)                                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def check_market_timing():
    """
    大盘风控逻辑:
      - 获取沪深300近期日线
      - 计算 MA20
      - 收盘价 < MA20 → 系统性风险信号，终止选股

    Returns
    -------
    is_safe : bool
        True 表示大盘安全，可继续选股
    """
    print_section(1, "大盘风控检测 (Market Timing)")

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")

    print(f"  > 获取沪深300指数 ({Config.INDEX_CODE}) 日线数据 ...")
    df = safe_api_call(
        ak.index_zh_a_hist,
        symbol=Config.INDEX_CODE,
        period="daily",
        start_date=start_date,
        end_date=end_date,
    )

    if df is None or df.empty:
        print("  [ERROR] 无法获取沪深300数据，系统终止。")
        return False

    # 计算 MA20
    df["MA20"] = df["收盘"].rolling(window=Config.MA_PERIOD).mean()

    latest = df.iloc[-1]
    close_price = float(latest["收盘"])
    ma20_value = float(latest["MA20"])
    trade_date = latest["日期"]

    print(f"  > 最新交易日 : {trade_date}")
    print(f"  > 沪深300收盘 : {close_price:.2f}")
    print(f"  > 20日均线    : {ma20_value:.2f}")

    if close_price < ma20_value:
        print()
        print("  !!  ============================================  !!")
        print("  !!  空仓警告: 大盘破位，系统性风险较高              !!")
        print(f"  !!  收盘 {close_price:.2f} < MA20 {ma20_value:.2f}           !!")
        print("  !!  本次选股已终止，建议空仓等待右侧信号           !!")
        print("  !!  ============================================  !!")
        return False

    diff_pct = (close_price - ma20_value) / ma20_value * 100
    print(f"  > 状态: 安全 (收盘价高于 MA20  {diff_pct:+.2f}%)")
    print("  > 大盘趋势健康，进入选股流程 ...")
    return True


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  模块 2 —— 基础清洗 (Data Filtering)                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def filter_stocks():
    """
    全市场股票清洗:
      1) 剔除 ST / *ST
      2) 剔除停牌（最新价为 0 / NaN，或成交额为 0）
      3) 剔除总市值 < 30 亿
      4) 仅保留沪深主板 / 创业板 / 科创板（排除北交所等）

    上市不足半年的次新股将在模块3中通过历史数据长度自动过滤。

    Returns
    -------
    pd.DataFrame
        包含 code, name, close, market_cap 的清洗后股票列表
    """
    print_section(2, "基础数据清洗 (Data Filtering)")

    print("  > 获取 A 股全市场实时行情 ...")
    df = safe_api_call(ak.stock_zh_a_spot_em)
    total_count = len(df)
    print(f"  > 全市场股票总数: {total_count}")

    # 统一列名，方便后续处理
    df = df.rename(columns={
        "代码": "code",
        "名称": "name",
        "最新价": "close",
        "总市值": "market_cap",
        "成交额": "amount",
    })

    # 1) 剔除 ST
    before = len(df)
    mask_st = df["name"].str.contains("ST", case=False, na=False)
    df = df[~mask_st]
    print(f"  > 剔除 ST 股     : 移除 {before - len(df):>4} 只 -> 剩余 {len(df)}")

    # 2) 剔除停牌
    before = len(df)
    df = df[df["close"].notna() & (df["close"] > 0)]
    df = df[df["amount"].notna() & (df["amount"] > 0)]
    print(f"  > 剔除停牌股     : 移除 {before - len(df):>4} 只 -> 剩余 {len(df)}")

    # 3) 剔除市值 < 30 亿
    before = len(df)
    df = df[df["market_cap"].notna() & (df["market_cap"] >= Config.MIN_MARKET_CAP)]
    cap_b = Config.MIN_MARKET_CAP / 1e8
    print(f"  > 剔除市值<{cap_b:.0f}亿  : 移除 {before - len(df):>4} 只 -> 剩余 {len(df)}")

    # 4) 仅保留主板 / 创业板 / 科创板
    before = len(df)
    df = df[df["code"].str.match(r"^(00|30|60|68)")]
    print(f"  > 保留沪深/创/科 : 移除 {before - len(df):>4} 只 -> 剩余 {len(df)}")

    print(f"\n  >> 清洗完成，候选池: {len(df)} 只股票")
    return df[["code", "name", "close", "market_cap"]].reset_index(drop=True)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  模块 3 —— 核心 RPS 计算 (Relative Price Strength)                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _fetch_one_stock(code, start_date, end_date):
    """
    获取单只股票前复权日线，供线程池调用。

    Returns
    -------
    (code, DataFrame | None)
    """
    try:
        df = safe_api_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            return code, df
    except Exception:
        pass
    return code, None


def calculate_rps(filtered_df):
    """
    核心 RPS 计算流程:
      1. 并发获取所有候选股票近 ~9 个月日线（确保 120 交易日足够）
      2. 计算 50日 / 120日 涨跌幅
      3. 全市场百分位排名 → RPS 值 (0~100)
      4. 筛选 RPS50 > 90 且 RPS120 > 85

    Parameters
    ----------
    filtered_df : pd.DataFrame
        模块2清洗后的股票列表

    Returns
    -------
    pd.DataFrame
        满足 RPS 条件的股票，含 code, return_50d, return_120d, rps_50, rps_120
    """
    print_section(3, "核心 RPS 计算 (Relative Price Strength)")

    codes = filtered_df["code"].tolist()

    # 多取 60 天缓冲（节假日 & 周末）
    fetch_calendar_days = int(Config.RPS_LONG_PERIOD * 1.8) + 60
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=fetch_calendar_days)).strftime("%Y%m%d")

    print(f"  > 股票数量   : {len(codes)}")
    print(f"  > 数据区间   : {start_date} ~ {end_date}")
    print(f"  > 并发线程数 : {Config.MAX_WORKERS}")
    print(f"  > 开始获取历史行情，请耐心等待 ...\n")

    # ── 多线程获取 ──
    hist_map = {}
    fail_count = 0

    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_stock, code, start_date, end_date): code
            for code in codes
        }
        with tqdm(total=len(codes), desc="  拉取历史行情", ncols=80, unit="只") as pbar:
            for future in as_completed(futures):
                code, data = future.result()
                if data is not None:
                    hist_map[code] = data
                else:
                    fail_count += 1
                pbar.update(1)

    print(f"\n  > 成功获取 : {len(hist_map)} 只")
    if fail_count:
        print(f"  > 获取失败 : {fail_count} 只 (已跳过)")

    # ── 计算涨跌幅 ──
    records = []
    skipped_new = 0

    for code, hist_df in hist_map.items():
        hist_df = hist_df.sort_values("日期").reset_index(drop=True)
        closes = hist_df["收盘"].values

        # 数据不足 120 个交易日 → 视为次新股，跳过
        if len(closes) < Config.MIN_HISTORY_DAYS:
            skipped_new += 1
            continue

        # 50 日涨跌幅
        if len(closes) >= Config.RPS_SHORT_PERIOD:
            ret_50 = (closes[-1] / closes[-Config.RPS_SHORT_PERIOD] - 1) * 100
        else:
            ret_50 = np.nan

        # 120 日涨跌幅
        ret_120 = (closes[-1] / closes[-Config.RPS_LONG_PERIOD] - 1) * 100

        records.append({
            "code": code,
            "return_50d": ret_50,
            "return_120d": ret_120,
        })

    if skipped_new:
        print(f"  > 剔除次新股 (数据<{Config.MIN_HISTORY_DAYS}日) : {skipped_new} 只")

    rps_df = pd.DataFrame(records)

    if rps_df.empty:
        print("  [WARN] 无有效股票可供计算 RPS，流程终止。")
        return pd.DataFrame()

    print(f"  > 参与 RPS 排名 : {len(rps_df)} 只")

    # ── 百分位排名 ──
    rps_df["rps_50"] = rps_df["return_50d"].rank(pct=True).mul(100).round(2)
    rps_df["rps_120"] = rps_df["return_120d"].rank(pct=True).mul(100).round(2)

    n50 = (rps_df["rps_50"] > Config.RPS_SHORT_THRESHOLD).sum()
    n120 = (rps_df["rps_120"] > Config.RPS_LONG_THRESHOLD).sum()
    print(f"  > RPS_50 > {Config.RPS_SHORT_THRESHOLD}  : {n50} 只")
    print(f"  > RPS_120 > {Config.RPS_LONG_THRESHOLD} : {n120} 只")

    # ── 双重筛选 ──
    selected = rps_df[
        (rps_df["rps_50"] > Config.RPS_SHORT_THRESHOLD)
        & (rps_df["rps_120"] > Config.RPS_LONG_THRESHOLD)
    ].copy()

    print(f"\n  >> 同时满足双 RPS 阈值 : {len(selected)} 只")
    return selected


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  行业信息辅助                                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _get_industry_for_stock(code):
    """查询单只股票的行业归属（东方财富）"""
    try:
        info = safe_api_call(ak.stock_individual_info_em, symbol=code)
        if info is not None and not info.empty:
            row = info[info["item"] == "行业"]
            if not row.empty:
                return code, str(row["value"].values[0])
    except Exception:
        pass
    return code, "未知"


def get_industry_for_selected(codes):
    """
    仅对最终入选股票查询行业，避免全市场大量 API 请求。

    Parameters
    ----------
    codes : list[str]

    Returns
    -------
    dict  {code: industry_name}
    """
    print("  > 获取入选股票的行业分类 ...")
    mapping = {}

    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as pool:
        futures = {pool.submit(_get_industry_for_stock, c): c for c in codes}
        with tqdm(total=len(codes), desc="  查询行业信息", ncols=80, unit="只") as pbar:
            for future in as_completed(futures):
                code, industry = future.result()
                mapping[code] = industry
                pbar.update(1)

    print(f"  > 行业映射完成，覆盖 {sum(1 for v in mapping.values() if v != '未知')}/{len(codes)} 只")
    return mapping


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  模块 4 —— 结果输出 (Output)                                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def output_results(selected_df, filtered_df, industry_map):
    """
    合并信息，按 RPS_50 降序输出到 Excel，并在终端打印摘要。

    Returns
    -------
    str | None
        输出文件的绝对路径；若无结果则返回 None
    """
    print_section(4, "结果输出 (Output)")

    if selected_df.empty:
        print("  [INFO] 本期无符合条件的股票，未生成文件。")
        return None

    # 合并名称
    result = selected_df.merge(filtered_df[["code", "name"]], on="code", how="left")

    # 合并行业
    result["industry"] = result["code"].map(industry_map).fillna("未知")

    # 按 RPS_50 降序
    result = result.sort_values("rps_50", ascending=False).reset_index(drop=True)
    result.index = result.index + 1  # 排名从 1 开始

    # 构造输出表
    output = result[["code", "name", "industry", "rps_50", "rps_120",
                      "return_50d", "return_120d"]].copy()
    output.columns = [
        "股票代码", "股票名称", "所属行业",
        "RPS_50日", "RPS_120日",
        "50日涨跌幅(%)", "120日涨跌幅(%)",
    ]

    # ── 写 Excel ──
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    filename = f"RPS_选股结果_{today_str}.xlsx"
    filepath = Config.OUTPUT_DIR / filename

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        output.to_excel(writer, sheet_name="RPS选股", index=True, index_label="排名")

        # 自动列宽（简单估算）
        ws = writer.sheets["RPS选股"]
        for col_cells in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col_cells) + 4
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len, 25)

    abs_path = os.path.abspath(filepath)
    print(f"  > 入选股票数 : {len(output)}")
    print(f"  > 文件已保存 : {abs_path}")

    # ── 终端摘要表 ──
    header = f"  {'排名':^4} | {'代码':^8} | {'名称':^8} | {'行业':^8} | {'RPS50':^6} | {'RPS120':^6} | {'50日%':^7} | {'120日%':^7}"
    sep = "  " + "-" * (len(header) - 2)

    print()
    print(sep)
    print(header)
    print(sep)

    show_n = min(30, len(output))
    for rank, (_, row) in enumerate(output.head(show_n).iterrows(), 1):
        print(
            f"  {rank:>4} | {row['股票代码']:<8} | {row['股票名称']:<8} | "
            f"{row['所属行业']:<8} | {row['RPS_50日']:>5.1f} | {row['RPS_120日']:>5.1f}  | "
            f"{row['50日涨跌幅(%)']:>+6.1f}% | {row['120日涨跌幅(%)']:>+6.1f}%"
        )

    if len(output) > show_n:
        print(f"  ... 还有 {len(output) - show_n} 只，详见 Excel 文件")

    print(sep)
    return abs_path


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  主函数入口                                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def main():
    _configure_windows_console()
    parser = argparse.ArgumentParser(description="A股 RPS 选股与风控系统 v1.0")
    parser.add_argument("--feishu", action="store_true",
                        help="推送结果到飞书群 (需先配置 feishu_config.json)")
    args = parser.parse_args()

    print_banner()
    wall_start = time.time()

    # ━━ 模块 1: 大盘风控 ━━
    market_safe = check_market_timing()
    if not market_safe:
        print("\n  系统已终止。大盘风控信号触发，建议空仓。\n")
        # 即使空仓也推送飞书通知
        if args.feishu:
            _push_feishu({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "market_safe": False,
                "total_stocks": 0,
                "filtered_stocks": 0,
                "selected_df": None,
                "industry_summary": {},
            }, None)
        return

    # ━━ 模块 2: 数据清洗 ━━
    filtered_df = filter_stocks()
    if filtered_df.empty:
        print("\n  清洗后无有效股票，系统终止。\n")
        return

    # ━━ 模块 3: RPS 选股 ━━
    selected_df = calculate_rps(filtered_df)

    # ━━ 获取行业信息（仅针对入选股票） ━━
    industry_map = {}
    if not selected_df.empty:
        industry_map = get_industry_for_selected(selected_df["code"].tolist())

    # ━━ 模块 4: 输出 ━━
    filepath = output_results(selected_df, filtered_df, industry_map)

    # ━━ 飞书推送 (可选) ━━
    if args.feishu:
        # 构造行业统计
        industry_summary = {}
        for v in industry_map.values():
            if v != "未知":
                industry_summary[v] = industry_summary.get(v, 0) + 1

        # 构造输出 DataFrame (与 output_results 中相同格式)
        output_df = None
        if not selected_df.empty:
            result = selected_df.merge(filtered_df[["code", "name"]], on="code", how="left")
            result["industry"] = result["code"].map(industry_map).fillna("未知")
            result = result.sort_values("rps_50", ascending=False).reset_index(drop=True)
            output_df = result.rename(columns={
                "code": "股票代码", "name": "股票名称", "industry": "所属行业",
                "rps_50": "RPS_50日", "rps_120": "RPS_120日",
                "return_50d": "50日涨跌幅(%)", "return_120d": "120日涨跌幅(%)",
            })

        _push_feishu({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "market_safe": True,
            "total_stocks": 0,  # 精确值已在清洗阶段打印
            "filtered_stocks": len(filtered_df),
            "selected_df": output_df,
            "industry_summary": industry_summary,
        }, output_df)

    # ━━ 运行统计 ━━
    elapsed = time.time() - wall_start
    minutes = int(elapsed // 60)
    seconds = elapsed % 60

    print()
    print("=" * 62)
    print(f"  系统运行完成!")
    print(f"  总耗时 : {minutes} 分 {seconds:.1f} 秒")
    if filepath:
        print(f"  结果文件 : {filepath}")
    print("=" * 62)
    print()


def _push_feishu(report_data, output_df):
    """飞书推送封装"""
    try:
        from feishu_bot import push_rps_report
        push_rps_report(report_data, output_df)
    except ImportError:
        print("\n  [飞书] feishu_bot.py 模块未找到，跳过推送")
    except Exception as e:
        print(f"\n  [飞书] 推送异常: {e}")


if __name__ == "__main__":
    main()
