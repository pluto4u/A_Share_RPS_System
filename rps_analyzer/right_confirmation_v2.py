"""Causal, layered research screen. V1 remains available for comparison."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from rps_analyzer.right_confirmation import evaluate as evaluate_v1
from rps_analyzer.right_confirmation import tri_and


class LayeredConfig(BaseModel):
    version: str = "right_confirmation_v2"
    rps_sum: float = Field(default=180, ge=0, le=200)
    rps120_min: float = Field(default=90, ge=0, le=100)
    rps250_min: float = Field(default=85, ge=0, le=100)
    near_high: float = Field(default=0.90, gt=0, le=1)
    max_bias20: float = Field(default=0.12, gt=0)
    max_atr_distance: float = Field(default=3, gt=0)
    contraction: float = Field(default=0.8, gt=0, le=1)
    dry_ratio: float = Field(default=0.9, gt=0, le=1)
    range_atr: float = Field(default=3, gt=0)
    pivot_proximity: float = Field(default=0.95, gt=0, le=1)
    breakout_volume_min: float = Field(default=1.3, gt=0)
    breakout_volume_max: float = Field(default=3, gt=0)
    close_location_min: float = Field(default=0.65, ge=0, le=1)


def shape_series(daily: Any, cfg: LayeredConfig) -> dict[str, Any]:
    """All setup windows stop at yesterday; recent/baseline windows do not overlap."""
    c, h, lo, v = [daily[x].reset_index(drop=True) for x in ("close", "high", "low", "vol")]
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.shift(1).rolling(20).mean()  # Frozen before today's potentially large bar.
    compression = tr.shift(1).rolling(5).mean() / tr.shift(6).rolling(10).mean().replace(0, np.nan)
    dry = v.shift(1).rolling(5).mean() / v.shift(6).rolling(20).mean().replace(0, np.nan)
    width = (h.shift(1).rolling(5).max() - lo.shift(1).rolling(5).min()) / atr.replace(0, np.nan)
    pivot = h.shift(1).rolling(20).max()
    setup_shape = (
        (compression <= cfg.contraction)
        & (dry <= cfg.dry_ratio)
        & (width <= cfg.range_atr)
        & (c.shift(1) >= pivot * cfg.pivot_proximity)
        & (c.shift(1) <= pivot)
    )
    vr = v / v.shift(1).rolling(20).mean().replace(0, np.nan)
    clv = (c - lo) / (h - lo).replace(0, np.nan)
    breakout = (
        setup_shape
        & (c > pivot)
        & (c.shift(1) <= pivot)
        & vr.between(cfg.breakout_volume_min, cfg.breakout_volume_max)
        & (clv >= cfg.close_location_min)
    )
    setup = setup_shape & (c >= pivot * cfg.pivot_proximity) & (c <= pivot)
    return {
        "atr": atr,
        "compression": compression,
        "dry": dry,
        "width": width,
        "pivot": pivot,
        "vr": vr,
        "clv": clv,
        "setup": setup,
        "breakout": breakout,
    }


def evaluate(
    daily: Any,
    rps120: float,
    rps250: float,
    turnover_pct: float | None,
    config: LayeredConfig | None = None,
) -> dict[str, Any]:
    cfg = config or LayeredConfig()
    if len(daily) < 280:
        raise ValueError("need 280 dated bars")
    values = daily[["close", "high", "low", "vol", "amount"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :3] <= 0).any() or (values[:, 3:] < 0).any():
        raise ValueError("invalid OHLC/volume/amount")
    if (daily.high < daily.close).any() or (daily.low > daily.close).any():
        raise ValueError("inconsistent OHLC")
    if not (0 <= rps120 <= 100 and 0 <= rps250 <= 100):
        raise ValueError("invalid RPS")
    if turnover_pct is not None and (not np.isfinite(turnover_pct) or turnover_pct < 0):
        raise ValueError("invalid turnover")
    c = daily.close.reset_index(drop=True)
    ma = {n: c.rolling(n).mean() for n in (20, 60, 200, 250)}
    shapes = shape_series(daily, cfg)
    atr = float(shapes["atr"].iloc[-1])
    gates: dict[str, bool | None] = {
        "strength": bool(
            rps120 + rps250 > cfg.rps_sum and rps120 >= cfg.rps120_min and rps250 >= cfg.rps250_min
        ),
        "long_trend": bool(
            (c > ma[200]).tail(30).sum() >= 20 and (c > ma[250]).tail(30).sum() >= 20
        ),
        "trend": bool(
            c.iloc[-1] > ma[20].iloc[-1] > ma[60].iloc[-1]
            and ma[20].iloc[-1] > ma[20].iloc[-6]
            and ma[60].iloc[-1] > ma[60].iloc[-6]
        ),
        "liquidity": bool((daily.amount.tail(10) > 1e8).sum() >= 7),
        "turnover": None if turnover_pct is None else bool(turnover_pct < 10),
        "near_high": bool(c.iloc[-1] / c.tail(120).max() >= cfg.near_high),
        "extension": bool(
            atr > 0
            and c.iloc[-1] / ma[20].iloc[-1] - 1 <= cfg.max_bias20
            and c.iloc[-1] - ma[20].iloc[-1] <= cfg.max_atr_distance * atr
        ),
    }
    eligible = tri_and(list(gates.values()))
    setup = bool(eligible is True and shapes["setup"].iloc[-1])
    confirmed = bool(eligible is True and shapes["breakout"].iloc[-1])
    if eligible is None:
        stage = "数据待核验"
    elif not eligible:
        stage = "未满足确认基础"
    else:
        stage = "放量突破确认" if confirmed else "缩量整理观察" if setup else "趋势观察"
    # V1 diagnostics stay intact; only explicitly versioned fields are overridden.
    old = evaluate_v1(daily, rps120, rps250, turnover_pct)
    return {
        **old,
        "v1_stage": old["stage"],
        "v1_confirmed": old["confirmed"],
        "stage": stage,
        "confirmed": confirmed,
        "setup": setup,
        "trend_pool": eligible,
        "version": cfg.version,
        **{f"v2_{k}": v for k, v in gates.items()},
        "v2_failed": ",".join(k for k, v in gates.items() if v is False),
        "v2_missing": ",".join(k for k, v in gates.items() if v is None),
        "atr20_prior": atr,
        "compression_ratio": float(shapes["compression"].iloc[-1]),
        "setup_dry_ratio": float(shapes["dry"].iloc[-1]),
        "range_atr": float(shapes["width"].iloc[-1]),
        "atr_distance": None if atr <= 0 else float((c.iloc[-1] - ma[20].iloc[-1]) / atr),
        "observation_reason": (
            "长短趋势、外部RPS、流动性通过；偏离同时受百分比和历史ATR约束。"
            + (
                "前期波动和量能收缩后出现突破形态。"
                if confirmed
                else "前期波动和量能收缩，当前仍靠近整理高点。"
                if setup
                else "目前仅属于趋势观察，不表示今天出现确认信号。"
            )
        ),
    }


def tdx_formula(cfg: LayeredConfig | None = None, diagnostic: bool = False) -> str:
    """Single source of numeric parameters; native TDX compilation still required."""
    c = cfg or LayeredConfig()
    core = f"""{{右侧V2：仅研究观察，日线收盘后运行；外部6=RPS120*10，7=RPS250*10}}
{{MODE=1趋势观察池；2整理观察池；3当日突破确认。三个池不是三条买卖指令}}
MODE:=1;
R120:=EXTDATA_USER(6,0)/10;
R250:=EXTDATA_USER(7,0)/10;
DATAOK:=BARSCOUNT(C)>=280 AND R120>0 AND R120<=100 AND R250>0 AND R250<=100 AND CAPITAL>0;
NAMEOK:=NOT(NAMELIKE('ST')) AND NOT(NAMELIKE('*ST')) AND NOT(NAMELIKE('退'));
MARKETOK:=CODELIKE('60') OR CODELIKE('68') OR CODELIKE('00') OR CODELIKE('30');
M20:=MA(C,20);
M60:=MA(C,60);
STRONG:=R120+R250>{c.rps_sum:g} AND R120>={c.rps120_min:g} AND R250>={c.rps250_min:g};
LONGOK:=COUNT(C>MA(C,200),30)>=20 AND COUNT(C>MA(C,250),30)>=20;
TRENDOK:=C>M20 AND M20>M60 AND M20>REF(M20,5) AND M60>REF(M60,5);
LIQOK:=COUNT(AMOUNT>100000000,10)>=7;
TURNOK:=VOL/CAPITAL*100<10;
HIGHOK:=C/HHV(C,120)>={c.near_high:g};
TRR:=MAX(H-L,MAX(ABS(H-REF(C,1)),ABS(L-REF(C,1))));
ATR0:=REF(MA(TRR,20),1);
EXTOK:=ATR0>0 AND C/M20-1<={c.max_bias20:g} AND C-M20<={c.max_atr_distance:g}*ATR0;
POOL:=DATAOK AND NAMEOK AND MARKETOK AND STRONG AND LONGOK AND TRENDOK
AND LIQOK AND TURNOK AND HIGHOK AND EXTOK;
PIVOT:=REF(HHV(H,20),1);
{{近期=昨日往前5根；波动基准=此前10根；量能基准=此前20根，互不重叠}}
TRBASE:=REF(MA(TRR,10),6);
VBASE:=REF(MA(VOL,20),6);
SQUEEZE:=TRBASE>0 AND REF(MA(TRR,5),1)<={c.contraction:g}*TRBASE;
DRY:=VBASE>0 AND REF(MA(VOL,5),1)<={c.dry_ratio:g}*VBASE;
TIGHT:=REF(HHV(H,5),1)-REF(LLV(L,5),1)<={c.range_atr:g}*ATR0;
PRELOC:=REF(C,1)>=PIVOT*{c.pivot_proximity:g} AND REF(C,1)<=PIVOT;
SHAPE:=SQUEEZE AND DRY AND TIGHT AND PRELOC;
SETUPOK:=POOL AND SHAPE AND C>=PIVOT*{c.pivot_proximity:g} AND C<=PIVOT;
VR:=VOL/MAX(REF(MA(VOL,20),1),0.000001);
CLV:=IF(H>L,(C-L)/(H-L),0);
EVENTOK:=POOL AND SHAPE AND C>PIVOT AND VR>={c.breakout_volume_min:g}
AND VR<={c.breakout_volume_max:g} AND CLV>={c.close_location_min:g};
"""
    if diagnostic:
        return core + (
            "\n数据:DATAOK;\n强度:STRONG;\n长趋势:LONGOK;\n短趋势:TRENDOK;\n流动性:LIQOK;"
            "\n换手:TURNOK;\n近高:HIGHOK;\n偏离:EXTOK;\n收缩:SQUEEZE;\n缩量:DRY;\n窄幅:TIGHT;"
            "\n观察池:POOL;\n整理池:SETUPOK;\n确认:EVENTOK;\n"
        )
    return core + "\nXG:IF(MODE=1,POOL,IF(MODE=2,SETUPOK,IF(MODE=3,EVENTOK,0)));\n"
