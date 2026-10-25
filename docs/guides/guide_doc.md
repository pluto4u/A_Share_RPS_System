# RPS + Huoche Selection System — Technical Summary

This is a **multi-layer daily screener** built on TongDaXin-style RPS rules. Layers are mostly **independent filters stacked on a shared L1 pool**, not a strict pyramid where each upper layer requires the one below.

---

## Pipeline Overview (`scripts/screening/run_tdx_screener.py`)

```119:327:f:\88888.投资\08.选股\a_-share_-rps_-system\run_tdx_screener.py
    # Step 3: L1 (TdxStockScreener, default config)
    # Step 5: L2 (screen_layer2 on L1 codes)
    # Step 6: Huoche (screen_huoche on L1 codes)
    # Step 7: Early Launch (full market; tags IN_L1 / IN_HUOCHE)
    # Step 8: Super (screen_super_strong on L1 codes)
```

**Dependency graph:**

```
Full market → RPS_120/250
    ↓
  L1 (8 SXHCG conditions)
    ├── L2 (strict 5 conditions + L2_FIRST_SIGNAL)
    ├── Huoche (weekly 4 conditions + HUOCHE_FIRST_W)
    └── Super (extreme RPS + extension caps)

Full market → Early Launch (7 EL conditions)
    └── recommended subset: Early ∩ L1
```

**Weekly RPS screener** (`scripts/screening/run_weekly_screener.py`) is a **separate strategy** (accumulation breakout), not part of the daily L1/Huoche stack.

---

## RPS Foundation (`rps_calculator.py`)

```39:51:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\rps_calculator.py
    for n in periods:
        df[f'pre_close_{n}'] = df.groupby('ts_code')['close'].shift(n)
        df[f'roc_{n}'] = (df['close'] - df[f'pre_close_{n}']) / df[f'pre_close_{n}']
        df[f'RPS_{n}'] = df.groupby('trade_date')[f'roc_{n}'].rank(pct=True) * 100
```

- **RPS_n** = cross-sectional percentile rank of N-day return × 100 (0–100).
- Pipeline uses **periods [120, 250]** only.
- **Price basis:** `load_tdx_market` reads raw `.day` prices; with `--price-basis raw`, ex-dividend / bonus-share gaps enter N-day returns, MAs and drawdowns. The default `--price-basis qfq` (also in `scripts/daily/run_daily_sop.py`) applies date-truncated ratio forward adjustment from local `gbbq` (`rps_analyzer/price_adjust.py`, same algorithm as `run_local_tdx_compare.adjust`); stocks with special categories 11–15 are excluded and listed in `tdx_screener_{date}_qfq_excluded.csv`. The research cache (`trend_start/`) is qfq, so raw-basis lists are not directly comparable with research signals. On 2026-09-30 raw vs qfq moved RPS120 by >5 pts for 17.8% of stocks and changed 11 of ~28 L1 names; Super was unchanged (`output/price_basis_20261006/v1/summary.json`). `scripts/screening/run_portfolio_check.py` still uses raw prices.
- `filter_top_rps()` (RPS_120 > 90 AND RPS_250 > 90) exists but is **not** used by the main screener.

---

## Layer 1 — Daily TDX RPS (`tdx_stock_screener.py`)

**Universe:** Full market with ≥250 trading days warmup.

**Pass rule:** ALL 8 conditions (`SXHCG1`–`SXHCG8`) must be true (AND).

| Condition | Meaning | Default thresholds |
|-----------|---------|-------------------|
| **SXHCG1** | RPS strength | `RPS_120 + RPS_250 > 180` |
| **SXHCG2** | MA bull | Close > MA20; ≥20/30 days close > MA250; ≥20/30 days close > MA200; AND (≥7/10 days close > MA20 **OR** ≥3/4 days above MA10 **and** MA20) |
| **SXHCG3** | Drawdown control | 20-day max drawdown ≤ **25%**; close / 120-day high close > **0.80** |
| **SXHCG4** | MA trend | Strict: MA20 rising 5 days + MA10 ≥ MA20 for 5 days; **OR** loose: today MA10↑, MA20↑, MA10 ≥ MA20 |
| **SXHCG5** | Turnover | Daily turnover < **10%** (skipped if no turnover data → passes) |
| **SXHCG6** | Above MA5 | ≥**6**/10 days close > MA5 |
| **SXHCG7** | Liquidity | ≥**7**/10 days amount > **¥100M** |
| **SXHCG8** | Hold MA5 | ≤**1**/3 days close < MA5 |

```286:420:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\tdx_stock_screener.py
    def _cond1_rps(self, today: pd.Series) -> bool:
        return (today["RPS_120"] + today["RPS_250"]) > self.config.rps_sum_threshold
    # ... SXHCG2-8 as above ...
```

**MA periods used:** 5, 10, 20, 200, 250.

**Entry timing:** Signal = **in list today**. No “first signal” flag at L1.

---

## Layer 2 — Strict ZHUTU (`screen_layer2` in `tdx_stock_screener.py`)

**Universe:** L1 codes only.

**Stricter config vs L1:**

```501:515:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\tdx_stock_screener.py
    return TdxScreenerConfig(
        rps_sum_threshold=185.0,
        ma250_min_days=25,
        ma200_min_days=25,
        ma20_short_min_days=9,
        near_high_period=250,      # vs L1's 120
        near_high_ratio=0.80,
        ma5_min_days=0,            # disables SXHCG6
        min_amount=0,              # disables SXHCG7
        ma5_break_max=999,         # disables SXHCG8
    )
```

**Active conditions:** SXHCG1–5 only (RPS, MA bull, drawdown, MA trend, turnover).

**L2_FIRST_SIGNAL:** True if stock passes L2 today but did **not** pass L2 on any prior day in the last **8 weeks (~40 trading days)**.

```566:601:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\tdx_stock_screener.py
    lookback_days = first_signal_weeks * 5  # default 8*5=40
    # ... scan prior days for SXHCG1-5 all passing ...
    first_signal_flags.append(not was_passing_before)
```

---

## Super — “极强 RPS 榜” (`super_strong.py`)

**Universe:** L1 codes only — **not** L2 or Huoche.

**What Super requires:**

```107:113:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\super_strong.py
    cond = (
        (today["RPS_120"] >= cfg.rps120_min)      # default 98
        & (today["RPS_250"] >= cfg.rps250_min)    # default 98
        & (today["vs_ma60"] <= cfg.vs_ma60_max)   # default 60% above MA60
        & (today["ret20"] <= cfg.ret20_max)      # default 60% 20-day return
        & (amount_col >= cfg.min_amount)          # default ¥50M
    )
```

| Filter | Default | Purpose |
|--------|---------|---------|
| RPS_120 | ≥ **98** | Top ~2% |
| RPS_250 | ≥ **98** | Top ~2% |
| close/MA60 − 1 | ≤ **60%** | Anti-chase |
| 20-day return | ≤ **60%** | Avoid post-acceleration |
| Daily amount | ≥ **¥50M** | Liquidity |

**Intent:** “Already proven strong, not collapsed, not over-extended” — complementary to Early Launch.

**Overlap stats** are computed at export time (`IN_L2`, `IN_HUOCHE`, `IN_EARLY`) but **not required** for Super membership.

---

## Huoche — Weekly “火车轨” (`huoche_screener.py`)

**Universe:** L1 codes only. Daily → weekly (Friday-aligned), then weekly RPS_120.

**Pass rule:** ALL 4 weekly conditions (`W_SXHCG1`–`W_SXHCG4`). **No turnover condition** (W_SXHCG5 skipped).

| Condition | Threshold |
|-----------|-----------|
| **W_SXHCG1** | `W_RPS_120 ≥ 90` (top 10%; no W_RPS_250 — insufficient history) |
| **W_SXHCG2** | Close > MA20W; ≥**25**/30 weeks close > MA60W; ≥**9**/10 weeks close > MA20W |
| **W_SXHCG3** | 20-week max drawdown ≤ **25%**; close / 120-week high close > **0.80** |
| **W_SXHCG4** | MA20W rising 5 weeks + MA10W ≥ MA20W; **OR** loose weekly trend (same pattern as daily L1) |

```66:136:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\huoche_screener.py
# W_SXHCG1-4 implementations
```

**Default HuocheConfig:** `rps120w_min=90`, `near_high_period_w=120`, `near_high_ratio=0.80`, `max_drawdown_pct=0.25`, `first_signal_weeks=8`, `ma_trend_strict_weeks=5`.

---

## Huoche-First — First weekly trigger logic

**Field:** `HUOCHE_FIRST_W`

**Logic:** When `W_SXHCG` is true today, look back the prior **N weeks** (default **8**, excluding current week). If **no** prior week also passed all 4 conditions → `HUOCHE_FIRST_W = True`.

```179:195:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\huoche_screener.py
    if sxhcg:
        recent = window.tail(cfg.first_signal_weeks + 1).iloc[:-1]
        first = True
        for _, row in recent.iterrows():
            # ... if all 4 conditions passed on that week → first = False
        rec["HUOCHE_FIRST_W"] = first
```

**Output:** `tdx_screener_{date}_huoche_first_codes.csv` — subset of Huoche with `HUOCHE_FIRST_W=True`.

**Operational note** (`scripts/screening/run_portfolio_check.py`): Huoche FIRST is flagged as a strong signal (“可越级建机动仓”).

---

## Early Launch — Layer 1.5 (`early_launch_screener.py`)

**Universe:** **Full market** (not restricted to L1). Recommended use: **`Early ∩ L1`** (`tdx_screener_{date}_early_L1.csv`).

**Pass rule:** ALL **7** conditions (`EL_ALL`):

| # | Condition | Default threshold |
|---|-----------|-------------------|
| EL1 | RPS_120 range | **[70, 100]** |
| EL2 | Consecutive days close > MA20 | **[3, 12]** |
| EL3 | Segment return since break above MA20 | **[5%, 45%]** |
| EL4 | ≥1 breakout day in segment | vol ≥ **1.3×** VOL_MA60 AND daily return ≥ **2.5%** |
| EL5 | Position vs MA60 | close/MA60 − 1 ≤ **40%** |
| EL6 | Near 60-day high | close / 60-day high ≥ **85%** |
| EL7 | Pre-launch quiet volume | Pre-20d avg vol / prior-60d avg vol ≤ **1.30** (skipped if insufficient history) |

Also: min **90** days history, daily amount ≥ **¥50M**.

```139:139:f:\88888.投资\08.选股\a_-share_-rps_-system\rps_analyzer\early_launch_screener.py
    el_all = el1 and el2 and el3 and el4 and el5 and el6 and el7
```

**Ranking score** (internal sort only): `-vs_ma60 + RPS_120/100 + streak/20`.

**Calibration note:** Params tuned via `scripts/validation/verify_early_launch.py` against 2026-04-17 Huoche picks; docs claim D+7 recall ~68% for that cohort.

---

## Cross-Layer Relationships

| Layer | Parent universe | Requires lower layer? |
|-------|----------------|----------------------|
| L1 | Full market | — |
| L2 | L1 | Yes (L1) |
| Huoche | L1 | Yes (L1) |
| Huoche-First | Huoche | Yes (Huoche + first-week flag) |
| Super | L1 | Yes (L1 only) |
| Early (recommended) | Full market ∩ L1 | L1 filter applied at export |

**Super is NOT** “L2 + Huoche + something.” It is **L1 + extreme dual RPS + extension caps**.

Typical counts (from docs): L1 ~200–400, L2 ~100–200, Huoche ~10–30, Early ~50–300, Super varies.

---

## Position Sizing / Risk / Regime / Buy Zone / Exits

### In core screeners (L1–Super, Huoche, Early)

| Feature | Present? | Details |
|---------|----------|---------|
| **Position sizing** | No | Lists only; no capital allocation |
| **Risk-based stop at entry** | Partial (implicit) | Super `vs_ma60≤60%`, Early `vs_ma60≤40%`, L1 `near_high_ratio=0.80` — position discipline, not stop prices |
| **Market regime / breadth filter** | **No** in `scripts/screening/run_tdx_screener.py` | Breadth cap exists separately in `scripts/screening/sentiment_engine.py` / `scripts/screening/market_gambler_watch.py`, not wired into these screeners |
| **Buy-zone discipline** | Partial | Early/Super cap extension vs MA60; L1 requires close within 80% of 120d (or 250d for L2) high; no “limit order band” in screener |
| **Sell / exit tied to entry** | **No** in screeners | Exit logic lives in `scripts/screening/run_portfolio_check.py` |

### Exit rules (`scripts/screening/run_portfolio_check.py`) — operational, not backtested in screener

```165:204:f:\88888.投资\08.选股\a_-share_-rps_-system\run_portfolio_check.py
    if pnl <= -0.08:           → 全部清仓 (hard stop -8%)
    elif close < MA60:         → 全部清仓
    elif 2+ days close < MA20:  → 核心减半 / 机动全清
    Super exit streak:         → 核心: 5d→减半, 10d→全清; 机动: 3d→全清
    pnl ≥ 25%:                 → 止盈减半
    drawdown from 60d high ≤ -15% while profitable → 保盈清仓
    held ≥ 30d and pnl < 5%:   → 时间止损
```

**Buy discipline:** Super candidates need **≥3 consecutive days** on Super list (`--hold-days 3`); limit band **±1%** around close.

**Huoche FIRST:** Same-day alert for tactical (“机动”) entry.

### Separate pattern modules (NOT in daily pipeline)

- **`dragon_pullback_finder.py`**: 5% stop below support, max 8% risk — backtested (docstring: 44.3% win rate, 2022–2026).
- **`pocket_pivot_finder_v2.py`**: 6% stop, max 8% risk.
- **`cup_handle_finder_v2.py`**: Pattern only, no stops.

These are **not** invoked by `scripts/screening/run_tdx_screener.py`.

---

## Forward Backtest / Win-Rate Stats

| Location | What it measures |
|----------|------------------|
| **`scripts/screening/run_persistence_scan.py`** | Optional `--hold N` forward return from window end; buckets by `super_rate` |
| **`docs/strategy_early_launch.md`** | Early∩L1: ~82.5% win / +13.16% median over 10d across 5 windows (documented, not in screener code) |
| **`scripts/validation/verify_early_forward_multi.py`** | Multi-window Early validation script |
| **`dragon_pullback_finder.py`** | Inline claim: +138.8% total, 44.3% win, 2.94 PF (2022–2026) |
| **L1 / L2 / Super / Huoche screeners** | **No embedded win-rate or backtest** |

`scripts/screening/run_tdx_screener.py` → `screen_all_dates()` exists for L1 backtesting but is **not** used by the daily runner.

---

## `score` in `persistence_*.csv` (`scripts/screening/run_persistence_scan.py`)

For each stock over a date window, the script counts **how many sampled days** it appeared in each layer, then:

```44:51:f:\88888.投资\08.选股\a_-share_-rps_-system\run_persistence_scan.py
LAYER_WEIGHTS = {
    "Super": 5,
    "Huoche": 4,
    "L2": 3,
    "L1": 2,
    "Early_L1": 2,
}
```

```118:119:f:\88888.投资\08.选股\a_-share_-rps_-system\run_persistence_scan.py
    pivot["layers_covered"] = (pivot > 0).sum(axis=1)
    pivot["score"] = sum(pivot[L] * w for L, w in LAYER_WEIGHTS.items())
```

**Formula:**

```
score = Super_days×5 + Huoche_days×4 + L2_days×3 + L1_days×2 + Early_L1_days×2
```

Also exported: `layers_covered`, `super_rate = Super / n_samples`, `l1_rate`.

With `--hold N`, merges forward return and prints win-rate buckets by `super_rate` (≥80%, 50–80%, etc.).

---

## Signal Tracking (`signal_tracker.py`)

Day-over-day diff on `_codes.csv` files: **NEW / EXIT / HOLD** for L1, L2, Huoche, Early∩L1, Super. No P&L.

---

## Implications for Improving Win-Rate / Profitability

Based on what the code actually does:

1. **Layers measure “quality + persistence,” not entry timing.** Except Early Launch and FIRST flags, most layers fire on stocks already extended (L1 near 120d high, Super capped but still up to +60% vs MA60).

2. **No market filter in the core pipeline.** Breadth/sentiment caps exist elsewhere but don’t suppress L1/Huoche signals when the broad market is weak.

3. **Super is the operational “core” tier** (`scripts/screening/run_portfolio_check.py`), but membership is **RPS-extreme on L1**, not multi-layer convergence. Persistence score weights Super heavily — aligned with that SOP.

4. **Early∩L1 has the most documented alpha** (~82% win in docs); standalone Early is documented as negative alpha.

5. **Huoche lags ~2–3 weeks** by design; Early Launch exists to front-run it.

6. **Exit rules are richer than entry rules** in the trading workflow, but they’re decoupled from screener conditions (MA20/MA60/Super streak, not “failed SXHCG”).

7. **Biggest gaps for profitability work:** no integrated backtest for L1/L2/Super/Huoche layers; no position sizing; no regime gate; buy-zone is coarse (±1% limit in portfolio check only).

If you want to propose improvements, the highest-leverage angles are likely: **(a)** gate entries on breadth/sentiment, **(b)** require multi-layer convergence (e.g. Super + Huoche-First + Early), **(c)** tighten buy-zone vs pivot/MA, and **(d)** backtest layer combinations with the persistence score framework already in `scripts/screening/run_persistence_scan.py`.