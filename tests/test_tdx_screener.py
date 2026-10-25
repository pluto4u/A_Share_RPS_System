"""
通达信 RPS 选股策略测试

生成模拟的 A 股日线数据，含强势股和弱势股，验证选股器各条件逻辑。
可独立运行: python tests/test_tdx_screener.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_stock_screener import TdxStockScreener, TdxScreenerConfig, print_diagnosis
from rps_analyzer.early_launch_screener import _scan_one, EarlyLaunchConfig


def generate_stock_data(
    code: str,
    days: int = 300,
    start_price: float = 10.0,
    trend: str = "bull",
    volatility: float = 0.02,
    base_amount: float = 2e8,
    base_vol: float = 5e6,
    seed: int = None,
) -> pd.DataFrame:
    """
    生成单只股票的模拟日线

    trend: "bull" 稳定上涨, "bear" 下跌, "sideways" 震荡, "explosive" 暴涨后回调
    """
    rng = np.random.RandomState(seed)

    dates = pd.bdate_range(end="2026-02-28", periods=days, freq="B")
    n = len(dates)

    if trend == "bull":
        daily_ret = rng.normal(0.003, volatility, n)
    elif trend == "bear":
        daily_ret = rng.normal(-0.003, volatility, n)
    elif trend == "sideways":
        daily_ret = rng.normal(0.0, volatility * 0.5, n)
    elif trend == "explosive":
        half = n // 2
        phase1 = rng.normal(0.008, volatility, half)
        phase2 = rng.normal(-0.005, volatility * 1.5, n - half)
        daily_ret = np.concatenate([phase1, phase2])
    else:
        daily_ret = rng.normal(0.001, volatility, n)

    daily_ret[0] = 0
    prices = start_price * np.cumprod(1 + daily_ret)

    highs = prices * (1 + np.abs(rng.normal(0, 0.01, n)))
    lows = prices * (1 - np.abs(rng.normal(0, 0.01, n)))
    opens = lows + (highs - lows) * rng.uniform(0.3, 0.7, n)

    vol_mult = rng.uniform(0.7, 1.3, n)
    if trend == "bull":
        vol_mult *= np.linspace(0.8, 1.4, n)

    vols = base_vol * vol_mult
    amounts = base_amount * vol_mult
    turnover = rng.uniform(1, 8, n)

    return pd.DataFrame({
        "trade_date": dates,
        "ts_code": code,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "vol": vols,
        "amount": amounts,
        "turnover": turnover,
    })


def generate_market_data(n_stocks: int = 200, days: int = 300) -> pd.DataFrame:
    """生成模拟全市场数据，含不同类型的股票"""
    all_data = []

    n_bull = int(n_stocks * 0.15)
    n_bear = int(n_stocks * 0.30)
    n_side = int(n_stocks * 0.40)
    n_expl = n_stocks - n_bull - n_bear - n_side

    idx = 0
    for i in range(n_bull):
        code = f"{600000 + idx:06d}.SH"
        df = generate_stock_data(
            code, days=days, trend="bull",
            start_price=np.random.uniform(8, 30),
            base_amount=np.random.uniform(1.5e8, 5e8),
            seed=idx,
        )
        all_data.append(df)
        idx += 1

    for i in range(n_bear):
        code = f"{600000 + idx:06d}.SH" if idx < 100 else f"{000000 + idx:06d}.SZ"
        df = generate_stock_data(
            code, days=days, trend="bear",
            start_price=np.random.uniform(5, 20),
            base_amount=np.random.uniform(0.5e8, 2e8),
            seed=idx,
        )
        all_data.append(df)
        idx += 1

    for i in range(n_side):
        code = f"{000000 + idx:06d}.SZ"
        df = generate_stock_data(
            code, days=days, trend="sideways",
            start_price=np.random.uniform(5, 25),
            base_amount=np.random.uniform(0.3e8, 1.5e8),
            seed=idx,
        )
        all_data.append(df)
        idx += 1

    for i in range(n_expl):
        code = f"{300000 + idx:06d}.SZ"
        df = generate_stock_data(
            code, days=days, trend="explosive",
            start_price=np.random.uniform(10, 40),
            base_amount=np.random.uniform(1e8, 4e8),
            seed=idx,
        )
        all_data.append(df)
        idx += 1

    market = pd.concat(all_data, ignore_index=True)
    print(f"[测试] 生成市场数据: {market['ts_code'].nunique()} 只股票, {len(market)} 行")
    return market


def test_early_launch_excludes_stock_without_target_date():
    """停牌等缺少目标日行情的股票，不能用旧记录冒充当日信号。"""
    dates = pd.bdate_range("2026-03-01", periods=100)
    stale = pd.DataFrame({
        "trade_date": dates,
        "ts_code": "600213.SH",
        "close": 10.0,
        "high": 10.2,
        "amount": 1e8,
        "vol": 1e7,
        "RPS_120": 95.0,
        "RPS_50": 95.0,
        "RPS_250": 95.0,
        "MA20": 9.5,
        "MA60": 9.0,
        "VOL_MA60": 1e7,
        "pct": 0.01,
    })
    target_dt = dates[-1] + pd.offsets.BDay(1)

    assert _scan_one(stale, target_dt, EarlyLaunchConfig()) is None


def test_screener():
    """主测试流程"""
    print("=" * 70)
    print("通达信 RPS 选股策略 — 测试")
    print("=" * 70)

    # 1. 生成模拟数据
    print("\n[Step 1] 生成模拟市场数据...")
    market = generate_market_data(n_stocks=200, days=300)

    # 2. 计算 RPS
    print("\n[Step 2] 计算 RPS_120, RPS_250...")
    market_rps = calculate_rps(market, periods=[120, 250], verbose=True)

    # 3. 运行选股器
    print("\n[Step 3] 运行通达信选股策略...")
    config = TdxScreenerConfig(
        rps_sum_threshold=180,
        near_high_ratio=0.80,
    )
    screener = TdxStockScreener(config)
    result = screener.screen(market_rps, target_date="20260228", verbose=True)

    # 4. 显示结果
    print("\n[Step 4] 选股结果:")
    if not result.empty:
        display_cols = ["ts_code", "close", "RPS_120", "RPS_250"]
        cond_cols = [f"SXHCG{i}" for i in range(1, 9)]
        print(result[display_cols].to_string(index=False, float_format="%.2f"))
        print(f"\n共 {len(result)} 只股票入选")
    else:
        print("无股票入选 (模拟数据可能不满足全部条件，属正常)")

    # 5. 诊断一只强势股
    bull_codes = market_rps[market_rps["ts_code"].str.startswith("600000")]["ts_code"].unique()
    if len(bull_codes) > 0:
        test_code = bull_codes[0]
        print(f"\n[Step 5] 诊断单只股票: {test_code}")
        diag = screener.diagnose(market_rps, test_code, "20260228")
        print_diagnosis(diag)

    # 6. 不同参数灵敏度测试
    print("\n[Step 6] 参数灵敏度测试:")
    for threshold in [170, 175, 180, 185]:
        cfg = TdxScreenerConfig(rps_sum_threshold=threshold)
        s = TdxStockScreener(cfg)
        r = s.screen(market_rps, target_date="20260228", verbose=False)
        print(f"  RPS阈值={threshold}: {len(r)} 只入选")

    print("\n" + "=" * 70)
    print("测试完成!")
    print("=" * 70)


def test_with_real_data(data_path: str, date: str = None):
    """
    用真实数据测试

    用法:
        test_with_real_data("backtest/data/stock_daily.parquet")
        test_with_real_data("your_tdx_data.csv", date="20260228")
    """
    print(f"加载数据: {data_path}")
    if data_path.endswith(".parquet"):
        df = pd.read_parquet(data_path)
    else:
        df = pd.read_csv(data_path)

    print(f"数据量: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    print(f"列名: {list(df.columns)}")

    if "RPS_120" not in df.columns or "RPS_250" not in df.columns:
        print("计算 RPS...")
        df = calculate_rps(df, periods=[120, 250], verbose=True)

    config = TdxScreenerConfig()
    screener = TdxStockScreener(config)
    result = screener.screen(df, target_date=date, verbose=True)

    if not result.empty:
        print(f"\n入选 {len(result)} 只:")
        print(result[["ts_code", "close", "RPS_120", "RPS_250"]].to_string(index=False, float_format="%.2f"))

    return result


# This is a manual CLI helper requiring a real file path, not a pytest fixture test.
test_with_real_data.__test__ = False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        data_path = sys.argv[1]
        date = sys.argv[2] if len(sys.argv) > 2 else None
        test_with_real_data(data_path, date)
    else:
        test_screener()
