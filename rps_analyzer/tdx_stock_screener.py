"""
================================================================================
通达信 RPS 选股策略 Python 实现
================================================================================
基于 learn/tdx_taoboshi_reference.md 的通达信公式，完整实现 8 大选股条件:

  SXHCG1: RPS120 + RPS250 > 阈值 (默认180)
  SXHCG2: 均线多头排列 (站上20日线、长期均线支撑、短期均线收敛)
  SXHCG3: 回撤可控 (20日最大回撤 <=25%，收盘接近120日最高价)
  SXHCG4: 均线趋势向上 (MA20上升+MA10>MA20)
  SXHCG5: 换手率 < 10%
  SXHCG6: 过去10天至少6天收盘 > MA5
  SXHCG7: 过去10天至少7天成交额 > 1亿
  SXHCG8: 过去3天收盘 < MA5 的天数 <= 1

依赖: pandas, numpy
数据格式: 需要 trade_date, ts_code, open, high, low, close, vol, amount, turnover 列
         以及 RPS_120, RPS_250 列 (由 rps_calculator 提前计算)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class TdxScreenerConfig:
    """通达信选股策略可调参数"""

    # --- SXHCG1: RPS 强度 ---
    rps_sum_threshold: float = 180.0   # RPS120 + RPS250 的阈值 (180~185)

    # --- SXHCG2: 均线多头 ---
    ma250_min_days: int = 20           # 过去30天收盘 > MA250 的最少天数 (20~30)
    ma250_lookback: int = 30
    ma200_min_days: int = 20           # 过去30天收盘 > MA200 的最少天数 (20~30)
    ma200_lookback: int = 30
    ma20_short_min_days: int = 7       # 过去10天收盘 > MA20 的最少天数
    ma20_short_lookback: int = 10
    ma10_4d_min: int = 3               # 过去4天 > MA10 的最少天数
    ma20_4d_min: int = 3               # 过去4天 > MA20 的最少天数
    ma_4d_lookback: int = 4

    # --- SXHCG3: 回撤控制 ---
    max_drawdown_pct: float = 0.25     # 20日内最大回撤不超过25%
    drawdown_lookback: int = 20
    near_high_ratio: float = 0.80      # 收盘 / 120日最高价 > 0.80 (0.75~0.85)
    near_high_period: int = 120

    # --- SXHCG4: 均线趋势 ---
    ma_trend_strict_days: int = 5      # 严格模式: MA20连续上升天数 (5~15)

    # --- SXHCG5: 换手率 ---
    max_turnover_pct: float = 10.0     # 日换手率上限 %

    # --- SXHCG6: 站稳5日线 ---
    ma5_min_days: int = 6              # 过去10天 > MA5 的最少天数
    ma5_lookback: int = 10

    # --- SXHCG7: 成交活跃 ---
    min_amount: float = 1e8            # 日成交额下限 (1亿)
    amount_min_days: int = 7           # 过去10天满足的最少天数
    amount_lookback: int = 10

    # --- SXHCG8: 短期不破5日线 ---
    ma5_break_max: int = 1             # 过去3天收盘 < MA5 的允许天数
    ma5_break_lookback: int = 3

    # --- 堆量 (可选, 原公式未纳入最终条件) ---
    vol_acc_short: int = 5
    vol_acc_long: int = 60
    vol_acc_ratio: float = 1.8
    use_vol_accumulation: bool = False


class TdxStockScreener:
    """
    通达信 RPS 选股策略

    用法:
        screener = TdxStockScreener(config=TdxScreenerConfig())
        result = screener.screen(df, target_date='20260228')
    """

    MA_PERIODS_NEEDED = [5, 10, 20, 200, 250]

    def __init__(self, config: Optional[TdxScreenerConfig] = None):
        self.config = config or TdxScreenerConfig()

    def screen(
        self,
        df: pd.DataFrame,
        target_date: Optional[str] = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """
        执行全部选股条件筛选

        参数
        ----------
        df : pd.DataFrame
            全市场日线数据, 需含 trade_date/ts_code/open/high/low/close/vol/amount 列,
            以及 RPS_120/RPS_250 列
        target_date : str, optional
            筛选目标日期 (YYYYMMDD)。None 则取最新交易日
        verbose : bool
            打印筛选过程

        返回
        -------
        pd.DataFrame
            通过所有条件的股票，含各条件的布尔列
        """
        df = self._prepare_data(df)

        if target_date is None:
            target_date = df["trade_date"].max()
        else:
            target_date = pd.Timestamp(target_date)

        if verbose:
            print(f"[TDX选股] 目标日期: {target_date.strftime('%Y-%m-%d')}")
            print(f"[TDX选股] 全市场股票数: {df['ts_code'].nunique()}")

        signals = self._compute_all_signals(df, target_date)

        if signals.empty:
            if verbose:
                print("[TDX选股] 目标日期无数据")
            return signals

        result = self._apply_final_filter(signals, verbose)
        return result

    def screen_all_dates(
        self,
        df: pd.DataFrame,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """
        对日期范围内每个交易日执行选股，用于回测

        返回包含 trade_date + ts_code + 各条件信号的 DataFrame
        """
        df = self._prepare_data(df)

        dates = df["trade_date"].sort_values().unique()
        if start_date:
            dates = dates[dates >= pd.Timestamp(start_date)]
        if end_date:
            dates = dates[dates <= pd.Timestamp(end_date)]

        min_warmup = max(250, self.config.near_high_period) + 30
        warmup_end = df["trade_date"].sort_values().unique()[min_warmup] if len(df["trade_date"].unique()) > min_warmup else dates[0]
        dates = dates[dates >= warmup_end]

        if verbose:
            print(f"[TDX选股] 回测日期范围: {dates[0]} ~ {dates[-1]}, 共 {len(dates)} 个交易日")

        all_results = []
        for i, dt in enumerate(dates):
            signals = self._compute_all_signals(df, dt)
            if signals.empty:
                continue
            passed = self._apply_final_filter(signals, verbose=False)
            if not passed.empty:
                all_results.append(passed)
            if verbose and (i + 1) % 50 == 0:
                print(f"  已处理 {i+1}/{len(dates)} 个交易日")

        if not all_results:
            return pd.DataFrame()

        result = pd.concat(all_results, ignore_index=True)
        if verbose:
            print(f"[TDX选股] 回测完成, 共 {len(result)} 条选股记录")
        return result

    # -------------------------------------------------------------------------
    # 内部方法
    # -------------------------------------------------------------------------

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """数据预处理: 类型转换、排序、计算均线"""
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

        required = ["trade_date", "ts_code", "close", "high", "low", "vol", "amount"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"缺少必要列: {missing}")

        g = df.groupby("ts_code")["close"]
        for p in self.MA_PERIODS_NEEDED:
            col = f"MA_{p}"
            if col not in df.columns:
                df[col] = g.transform(lambda x: x.rolling(p, min_periods=p).mean())

        if "MA_VOL_5" not in df.columns:
            df["MA_VOL_5"] = df.groupby("ts_code")["vol"].transform(
                lambda x: x.rolling(5, min_periods=5).mean()
            )
        if "MA_VOL_60" not in df.columns:
            df["MA_VOL_60"] = df.groupby("ts_code")["vol"].transform(
                lambda x: x.rolling(60, min_periods=60).mean()
            )

        return df

    def _get_stock_window(self, stock_df: pd.DataFrame, target_date, lookback: int) -> pd.DataFrame:
        """取某只股票截至 target_date 的最近 lookback 天数据"""
        mask = stock_df["trade_date"] <= target_date
        return stock_df.loc[mask].tail(lookback)

    def _compute_all_signals(self, df: pd.DataFrame, target_date) -> pd.DataFrame:
        """对 target_date 这一天，逐条件计算所有股票的信号"""
        cfg = self.config

        max_lookback = max(
            cfg.ma250_lookback, cfg.ma200_lookback,
            cfg.drawdown_lookback, cfg.ma_trend_strict_days + 1,
            cfg.ma5_lookback, cfg.amount_lookback,
            cfg.ma5_break_lookback,
        ) + 5

        records = []

        for code, stock_df in df.groupby("ts_code", sort=False):
            window = self._get_stock_window(stock_df, target_date, max_lookback)

            if len(window) < max_lookback:
                continue

            today = window.iloc[-1]

            if pd.isna(today.get("RPS_120")) or pd.isna(today.get("RPS_250")):
                continue

            rec = {
                "trade_date": target_date,
                "ts_code": code,
                "close": today["close"],
                "RPS_120": today["RPS_120"],
                "RPS_250": today["RPS_250"],
            }

            rec["SXHCG1"] = self._cond1_rps(today)
            rec["SXHCG2"] = self._cond2_ma_bull(window)
            rec["SXHCG3"] = self._cond3_drawdown(window, today)
            rec["SXHCG4"] = self._cond4_ma_trend(window)
            rec["SXHCG5"] = self._cond5_turnover(today, window)
            rec["SXHCG6"] = self._cond6_above_ma5(window)
            rec["SXHCG7"] = self._cond7_amount(window)
            rec["SXHCG8"] = self._cond8_hold_ma5(window)

            records.append(rec)

        if not records:
            return pd.DataFrame()

        return pd.DataFrame(records)

    def _apply_final_filter(self, signals: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        """合并所有条件得出最终选股"""
        cond_cols = [f"SXHCG{i}" for i in range(1, 9)]
        signals["SXHCG"] = signals[cond_cols].all(axis=1)
        passed = signals[signals["SXHCG"]].copy()

        if verbose:
            total = len(signals)
            for col in cond_cols:
                cnt = signals[col].sum()
                print(f"  {col}: {cnt}/{total} 通过 ({cnt/total*100:.1f}%)")
            print(f"  最终: {len(passed)}/{total} 只股票入选")

        return passed

    # =========================================================================
    # 8 大选股条件
    # =========================================================================

    def _cond1_rps(self, today: pd.Series) -> bool:
        """SXHCG1: RPS120 + RPS250 > 阈值"""
        return (today["RPS_120"] + today["RPS_250"]) > self.config.rps_sum_threshold

    def _cond2_ma_bull(self, window: pd.DataFrame) -> bool:
        """
        SXHCG2: 均线多头排列
          - 收盘站上20日线
          - 近30天有 >=N 天站上250日线
          - 近30天有 >=N 天站上200日线
          - (近10天 >=7天站上MA20) 或 (近4天 >=3天站上MA10 且 >=3天站上MA20)
        """
        cfg = self.config
        today = window.iloc[-1]

        if today["close"] <= today["MA_20"]:
            return False

        tail30 = window.tail(cfg.ma250_lookback)
        above_250 = (tail30["close"] > tail30["MA_250"]).sum()
        if above_250 < cfg.ma250_min_days:
            return False

        tail30_200 = window.tail(cfg.ma200_lookback)
        above_200 = (tail30_200["close"] > tail30_200["MA_200"]).sum()
        if above_200 < cfg.ma200_min_days:
            return False

        tail10 = window.tail(cfg.ma20_short_lookback)
        sxhcg23 = (tail10["close"] > tail10["MA_20"]).sum() >= cfg.ma20_short_min_days

        tail4 = window.tail(cfg.ma_4d_lookback)
        sxhcg24 = (
            (tail4["close"] > tail4["MA_10"]).sum() >= cfg.ma10_4d_min
            and (tail4["close"] > tail4["MA_20"]).sum() >= cfg.ma20_4d_min
        )

        return sxhcg23 or sxhcg24

    def _cond3_drawdown(self, window: pd.DataFrame, today: pd.Series) -> bool:
        """
        SXHCG3: 回撤控制
          - 20日内最大回撤 <= 25%
          - 收盘 / 120日最高价 > 0.80
        """
        cfg = self.config
        tail20 = window.tail(cfg.drawdown_lookback)

        highs = tail20["high"].values
        lows = tail20["low"].values

        new_high_idx = np.argmax(highs)
        new_high_price = highs[new_high_idx]

        if new_high_idx < len(highs) - 1:
            lows_after_high = lows[new_high_idx:]
            new_low_price = np.min(lows_after_high)
        else:
            new_low_price = lows[-1]

        if new_high_price > 0:
            drawdown = (new_high_price - new_low_price) / new_high_price
        else:
            drawdown = 0

        if drawdown > cfg.max_drawdown_pct:
            return False

        full_tail = window.tail(max(cfg.drawdown_lookback, cfg.near_high_period))
        hhv_120 = full_tail["close"].tail(cfg.near_high_period).max()
        if hhv_120 > 0 and today["close"] / hhv_120 <= cfg.near_high_ratio:
            return False

        return True

    def _cond4_ma_trend(self, window: pd.DataFrame) -> bool:
        """
        SXHCG4: 均线趋势
          严格模式: MA20 连续上升 N 天 且 MA10 >= MA20 连续 N 天
          宽松模式: 今日 MA10↑, MA20↑, MA10 >= MA20
        """
        cfg = self.config
        n = cfg.ma_trend_strict_days

        tail_strict = window.tail(n + 1)
        if len(tail_strict) >= n + 1:
            ma20_vals = tail_strict["MA_20"].values
            ma10_vals = tail_strict["MA_10"].values

            ma20_rising = all(ma20_vals[i + 1] >= ma20_vals[i] for i in range(len(ma20_vals) - n, len(ma20_vals) - 1))
            ma10_above_20 = all(ma10_vals[i] >= ma20_vals[i] for i in range(len(ma10_vals) - n, len(ma10_vals)))

            if ma20_rising and ma10_above_20:
                return True

        today = window.iloc[-1]
        yesterday = window.iloc[-2]
        loose = (
            today["MA_10"] >= yesterday["MA_10"]
            and today["MA_20"] >= yesterday["MA_20"]
            and today["MA_10"] >= today["MA_20"]
        )
        return loose

    def _cond5_turnover(self, today: pd.Series, window: pd.DataFrame) -> bool:
        """SXHCG5: 换手率 < 10%"""
        if "turnover" in today.index and not pd.isna(today["turnover"]):
            return today["turnover"] < self.config.max_turnover_pct

        if "total_share" in today.index and today["total_share"] > 0:
            tr = today["vol"] / today["total_share"] * 100
            return tr < self.config.max_turnover_pct

        return True

    def _cond6_above_ma5(self, window: pd.DataFrame) -> bool:
        """SXHCG6: 过去10天至少6天收盘 > MA5"""
        cfg = self.config
        tail = window.tail(cfg.ma5_lookback)
        count = (tail["close"] > tail["MA_5"]).sum()
        return count >= cfg.ma5_min_days

    def _cond7_amount(self, window: pd.DataFrame) -> bool:
        """SXHCG7: 过去10天至少7天成交额 > 1亿"""
        cfg = self.config
        tail = window.tail(cfg.amount_lookback)
        count = (tail["amount"] > cfg.min_amount).sum()
        return count >= cfg.amount_min_days

    def _cond8_hold_ma5(self, window: pd.DataFrame) -> bool:
        """SXHCG8: 过去3天收盘 < MA5 的天数 <= 1"""
        cfg = self.config
        tail = window.tail(cfg.ma5_break_lookback)
        below_count = (tail["close"] < tail["MA_5"]).sum()
        return below_count <= cfg.ma5_break_max

    # =========================================================================
    # 诊断工具
    # =========================================================================

    def diagnose(self, df: pd.DataFrame, ts_code: str, target_date: Optional[str] = None) -> Dict:
        """
        诊断单只股票在各条件上的通过/失败情况，方便调参

        返回包含每个条件状态和相关数值的字典
        """
        df = self._prepare_data(df)
        if target_date is None:
            target_date = df["trade_date"].max()
        else:
            target_date = pd.Timestamp(target_date)

        stock_df = df[df["ts_code"] == ts_code]
        if stock_df.empty:
            return {"error": f"未找到股票 {ts_code}"}

        max_lookback = 40
        window = self._get_stock_window(stock_df, target_date, max_lookback)
        today = window.iloc[-1]

        result = {
            "ts_code": ts_code,
            "trade_date": str(target_date.date()),
            "close": today["close"],
            "RPS_120": today.get("RPS_120"),
            "RPS_250": today.get("RPS_250"),
            "conditions": {},
        }

        result["conditions"]["SXHCG1"] = {
            "pass": self._cond1_rps(today),
            "rps_sum": (today.get("RPS_120", 0) or 0) + (today.get("RPS_250", 0) or 0),
            "threshold": self.config.rps_sum_threshold,
        }
        result["conditions"]["SXHCG2"] = {"pass": self._cond2_ma_bull(window)}
        result["conditions"]["SXHCG3"] = {"pass": self._cond3_drawdown(window, today)}
        result["conditions"]["SXHCG4"] = {"pass": self._cond4_ma_trend(window)}
        result["conditions"]["SXHCG5"] = {"pass": self._cond5_turnover(today, window)}
        result["conditions"]["SXHCG6"] = {"pass": self._cond6_above_ma5(window)}
        result["conditions"]["SXHCG7"] = {"pass": self._cond7_amount(window)}
        result["conditions"]["SXHCG8"] = {"pass": self._cond8_hold_ma5(window)}

        result["all_pass"] = all(c["pass"] for c in result["conditions"].values())
        return result


def print_diagnosis(diag: Dict):
    """格式化打印诊断结果"""
    print(f"\n{'='*60}")
    print(f"股票: {diag['ts_code']}  日期: {diag['trade_date']}")
    print(f"收盘: {diag['close']:.2f}  RPS120: {diag.get('RPS_120', 'N/A')}  RPS250: {diag.get('RPS_250', 'N/A')}")
    print(f"{'-'*60}")

    for name, info in diag.get("conditions", {}).items():
        status = "PASS" if info["pass"] else "FAIL"
        extra = "  ".join(f"{k}={v}" for k, v in info.items() if k != "pass")
        print(f"  [{status}] {name}  {extra}")

    print(f"{'-'*60}")
    final = "PASS" if diag["all_pass"] else "FAIL"
    print(f"  Final: {final}")
    print(f"{'='*60}\n")


def layer2_strict_config() -> TdxScreenerConfig:
    """
    Layer 2 严格参数 — 基于 docs/tdx_rps_extension_zhutu.md 主图画标记策略

    与 Layer 1 的区别:
      - RPS 阈值 185 (vs 180)
      - MA250/200 支撑天数 25 (vs 20)
      - MA20 短期天数 9 (vs 7)
      - 接近新高用 250 日 (vs 120 日)
      - 只用核心 5 条件 (关闭 SXHCG6/7/8)
    """
    return TdxScreenerConfig(
        rps_sum_threshold=185.0,
        ma250_min_days=25,
        ma200_min_days=25,
        ma20_short_min_days=9,
        near_high_period=250,
        near_high_ratio=0.80,
        ma5_min_days=0,
        ma5_lookback=10,
        min_amount=0,
        amount_min_days=0,
        amount_lookback=10,
        ma5_break_max=999,
        ma5_break_lookback=3,
    )


def screen_layer2(
    df: pd.DataFrame,
    layer1_codes: list,
    target_date: Optional[str] = None,
    first_signal_weeks: int = 8,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Layer 2: 从 Layer 1 候选池中，用 ZHUTU 严格条件复筛，
    并检测 "首次触发" — 最近 N 周内第一次满足全部条件。

    参数
    ----------
    df : pd.DataFrame
        带 RPS 的全市场日线数据
    layer1_codes : list
        Layer 1 入选的 ts_code 列表
    target_date : str, optional
        目标日期
    first_signal_weeks : int
        首次触发回看周数 (默认 8, 对应 ~40 交易日)
    verbose : bool
        打印过程

    返回
    -------
    pd.DataFrame
        Layer 2 入选股票 + L2_FIRST_SIGNAL 列
    """
    if verbose:
        print(f"\n[Layer 2] 候选池: {len(layer1_codes)} 只")

    cfg = layer2_strict_config()
    screener = TdxStockScreener(cfg)

    sub_df = df[df["ts_code"].isin(layer1_codes)].copy()
    if sub_df.empty:
        if verbose:
            print("[Layer 2] 候选池无数据")
        return pd.DataFrame()

    result = screener.screen(sub_df, target_date=target_date, verbose=verbose)

    if result.empty:
        if verbose:
            print("[Layer 2] 无股票通过严格条件")
        return result

    lookback_days = first_signal_weeks * 5
    prepared = screener._prepare_data(sub_df)

    if target_date is None:
        td = prepared["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)

    first_signal_flags = []
    for code in result["ts_code"]:
        stock_df = prepared[prepared["ts_code"] == code]
        dates = stock_df[stock_df["trade_date"] <= td]["trade_date"].nlargest(lookback_days + 1)

        if len(dates) < 2:
            first_signal_flags.append(True)
            continue

        check_dates = dates.iloc[1:]
        was_passing_before = False
        for d in check_dates:
            day_row = stock_df[stock_df["trade_date"] == d].iloc[0]
            window = screener._get_stock_window(stock_df, d, 40)
            if len(window) < 35:
                continue
            all_pass = (
                screener._cond1_rps(day_row)
                and screener._cond2_ma_bull(window)
                and screener._cond3_drawdown(window, day_row)
                and screener._cond4_ma_trend(window)
                and screener._cond5_turnover(day_row, window)
            )
            if all_pass:
                was_passing_before = True
                break

        first_signal_flags.append(not was_passing_before)

    result["L2_FIRST_SIGNAL"] = first_signal_flags

    if verbose:
        n_first = sum(first_signal_flags)
        print(f"  首次触发: {n_first}/{len(result)} 只")

    return result


# =============================================================================
# CLI 入口
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="通达信 RPS 选股策略")
    parser.add_argument("--data", type=str, help="股票日线数据文件路径 (parquet/csv)")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYYMMDD")
    parser.add_argument("--rps-sum", type=float, default=180.0, help="RPS120+RPS250 阈值")
    parser.add_argument("--diagnose", type=str, default=None, help="诊断指定股票代码")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    args = parser.parse_args()

    if not args.data:
        print("用法: python tdx_stock_screener.py --data <数据文件>")
        print("示例: python tdx_stock_screener.py --data backtest/data/stock_daily_with_rps.parquet --date 20260228")
        exit(1)

    if args.data.endswith(".parquet"):
        data = pd.read_parquet(args.data)
    else:
        data = pd.read_csv(args.data)

    config = TdxScreenerConfig(rps_sum_threshold=args.rps_sum)
    screener = TdxStockScreener(config)

    if args.diagnose:
        diag = screener.diagnose(data, args.diagnose, args.date)
        print_diagnosis(diag)
    else:
        result = screener.screen(data, target_date=args.date)
        if not result.empty:
            print(f"\n入选股票:\n{result[['ts_code', 'close', 'RPS_120', 'RPS_250']].to_string(index=False)}")
            if args.output:
                if args.output.endswith(".parquet"):
                    result.to_parquet(args.output, index=False)
                else:
                    result.to_csv(args.output, index=False, encoding="utf-8-sig")
                print(f"\n结果已保存到 {args.output}")
        else:
            print("\n无股票入选")
