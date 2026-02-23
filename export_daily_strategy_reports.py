import pandas as pd
from pathlib import Path

from daily_signal import get_signal, load_scores
from emotion_validation.etf_rotation.etf_data import ETFDataManager
from emotion_validation.etf_rotation.rotation_strategy import ETFRotationStrategy


def export_etf_daily_report(out_path: Path) -> pd.DataFrame:
    scores = load_scores()
    mgr = ETFDataManager()
    etf_data = mgr.load_all()
    strategy = ETFRotationStrategy(top_n=2)

    rows = []
    for i in range(len(scores)):
        sub_scores = scores.iloc[: i + 1].copy()
        sig = get_signal(sub_scores, ecology=None)
        trade_date = sub_scores["trade_date"].iloc[-1]
        holdings = strategy.get_holdings(etf_data, float(sig["score"]), trade_date)
        holding_symbols = ",".join([h["symbol"] for h in holdings]) if holdings else ""
        rows.append(
            {
                "date": trade_date.strftime("%Y-%m-%d"),
                "emotion_score": round(float(sig["score"]), 2),
                "zone": sig["zone_cn"],
                "target_pos_pct": int(sig["target_pos"] * 100),
                "current_pos_pct": int(sig["current_pos"] * 100),
                "action": sig["action"],
                "action_desc": sig["action_cn"],
                "etf_holdings": holding_symbols,
            }
        )

    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return df


def export_rps_daily_report(trades_path: Path, nav_path: Path, out_path: Path) -> pd.DataFrame:
    trades = pd.read_csv(trades_path, parse_dates=["date"])
    nav = pd.read_csv(nav_path, parse_dates=["date"])

    trade_map = {}
    for _, row in trades.iterrows():
        d = row["date"].strftime("%Y-%m-%d")
        trade_map.setdefault(d, []).append(row)

    positions = {}
    rows = []
    for _, nrow in nav.iterrows():
        d = nrow["date"].strftime("%Y-%m-%d")
        day_trades = trade_map.get(d, [])
        buy_list = []
        sell_list = []

        for t in day_trades:
            code = str(t["code"])
            action = str(t["action"])
            shares = int(t["shares"])
            if action == "BUY":
                positions[code] = positions.get(code, 0) + shares
                buy_list.append(code)
            elif action == "SELL":
                old = positions.get(code, 0)
                left = max(0, old - shares)
                if left == 0:
                    positions.pop(code, None)
                else:
                    positions[code] = left
                sell_list.append(code)

        rows.append(
            {
                "date": d,
                "nav": round(float(nrow["nav"]), 2),
                "holding_count": len(positions),
                "buy_symbols": ",".join(buy_list),
                "sell_symbols": ",".join(sell_list),
            }
        )

    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return df


def main() -> None:
    base = Path("backtest/reports")
    etf_out = base / "etf_rotation" / "etf_daily_position_control.csv"
    rps_out = base / "dragon_pullback_backtest" / "rps_daily_stock_actions.csv"

    trades_path = base / "dragon_pullback_backtest" / "dragon_trades.csv"
    nav_path = base / "dragon_pullback_backtest" / "dragon_nav.csv"

    etf_df = export_etf_daily_report(etf_out)
    rps_df = export_rps_daily_report(trades_path, nav_path, rps_out)

    print(f"ETF日报已生成: {etf_out} ({len(etf_df)}行)")
    print(f"RPS日报已生成: {rps_out} ({len(rps_df)}行)")


if __name__ == "__main__":
    main()
