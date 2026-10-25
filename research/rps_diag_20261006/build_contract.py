"""Build a precise repair backlog from inspected sources, not guessed data."""
import json
from datetime import datetime,timezone

import pandas as pd

from diagnostic import HERE,ROOT,sha

OUT = ROOT / "output/rps_diag_20261006/exchange_evidence"


def main():
    if (OUT / "repair_backlog.csv").exists():
        raise FileExistsError("Repair backlog already exists")
    target = pd.read_csv(OUT / "study_window_terminated_coverage.csv",dtype={"ticker":str},parse_dates=["terminal_date"])
    cache = pd.read_csv(OUT / "retained_cache_code_quality.csv",parse_dates=["last_date"])
    raw = pd.read_csv(OUT / "configured_raw_code_coverage.csv",parse_dates=["last_date"])
    target = target.rename(columns={"last_date":"hithink_last_date","first_date":"hithink_first_date"})
    cache = cache.rename(columns={"last_date":"cache_last_date","study_window_bars":"cache_window_bars"})
    raw = raw.rename(columns={"exists":"raw_present","last_date":"raw_last_date","window_rows":"raw_window_bars"})
    backlog = target.merge(cache[["ts_code","cache_last_date","cache_window_bars"]],on="ts_code",how="left",validate="one_to_one")
    backlog = backlog.merge(raw[["ts_code","raw_present","raw_last_date","raw_window_bars"]],on="ts_code",how="left",validate="one_to_one")
    backlog["cache_present"] = backlog.cache_window_bars.notna()
    backlog["raw_ends_before_cache"] = backlog.raw_present & (backlog.raw_last_date < backlog.cache_last_date)
    backlog["next_action"] = "FETCH_MISSING_HISTORY"
    backlog.loc[backlog.cache_present & ~backlog.raw_present,"next_action"] = "RECOVER_ORIGINAL_RAW_AND_ACTIONS"
    backlog.loc[backlog.raw_present,"next_action"] = "RECONCILE_RAW_CACHE_ACTIONS_AND_SECURITY_STATUS"
    backlog.loc[backlog.raw_ends_before_cache,"next_action"] = "EXTEND_RAW_THEN_RECONCILE"
    backlog["accepted_for_research"] = False
    backlog.to_csv(OUT / "repair_backlog.csv",index=False)
    contract = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), status="BLOCKED_FOR_PROFIT_CERTIFICATION",
        input_scope="Historical mainland SH/SZ A-share universe overlapping 2016-08-22 through 2026-09-30, not today's constituents",
        inspected_terminal_window=["2018-01-01","2026-09-30"],target_codes=len(backlog),
        source_inventory=dict(cache_present=int(backlog.cache_present.sum()),raw_present=int(backlog.raw_present.sum()),
            missing_both=int((~backlog.cache_present & ~backlog.raw_present).sum()),
            cache_without_raw=int((backlog.cache_present & ~backlog.raw_present).sum()),raw_ends_before_cache=int(backlog.raw_ends_before_cache.sum())),
        required_tables={
            "security_master_history":["non-reused security_id","ticker/code validity intervals","listing and terminal effective dates",
                "historical ST/risk-warning intervals","board and special-listing trading-rule intervals","industry classification/version intervals",
                "source announcement/publication timestamp and effective timestamp"],
            "daily_raw_prices":["security_id/trade_date unique key","raw OHLC and previous close","volume in shares and amount in CNY",
                "traded/suspended status","official daily price limits or auditable historically-effective rule",
                "source/extract timestamp and input hash; missing stays missing"],
            "corporate_actions":["announcement timestamp","ex/registry/payment dates","cash dividend/share distributions/rights terms",
                "factor methodology and reconciliation to raw bars; merger/relisting/terminal treatment"],
            "capital_history":["outstanding/float shares with effective intervals","announcement/availability timestamps",
                "historical market-cap calculated using known shares and raw price, NOT amount"],
            "daily_universe":["all eligible-at-the-time securities including later terminal ones","rules known at signal date",
                "past-data/ranking/eligibility trace; no today's name/status backfill"]},
        acceptance_gates=[
            "No overwrite of frozen RPS-PV or RPS-DIAG results; repaired data gets new version/hash and explicit rerun lineage.",
            "Every official terminal-list gap must be filled or explained with source evidence; snapshots do not certify full history.",
            "Unique keys, complete unit/price-basis lineage, no unexplained OHLC violations or post-terminal activity; relisting uses a distinct interval.",
            "Reconcile raw/adjusted TDX and hithink on available overlaps; never splice qfq levels blindly.",
            "A known historical name, ST, board, float-cap and industry record must be effective and available by the signal date; unknown cannot be filled from today's record.",
            "Missing future prices/terminal settlements cannot be dropped as winners/losers or assigned zero return; settlement and valuation convention must be written before performance reruns.",
            "Executable-account research additionally needs price limits, suspensions, lot/minimum-commission/cash-dividend/settlement/capacity rules; price proxies are not account returns.",
            "Existing 2018-2026 history remains exploratory after repairs; future observation is independent only if rules are frozen before signals."],
        deferred_investment_decision=True, broker_actions=False,
        evidence_hashes={f.name:sha(f) for f in sorted(OUT.iterdir()) if f.is_file()})
    with (HERE / "data_contract.json").open("x",encoding="utf-8") as f:
        json.dump(contract,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(backlog_codes=len(backlog),source_inventory=contract["source_inventory"],accepted=0),ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
