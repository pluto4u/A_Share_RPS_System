"""W1: completed five-minute bars, frozen prior-session references, no orders."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TailPlan(CheckedModel):
    version: str = "tail_watch_w1"
    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    prior_session: date
    session: date
    # Explicit upstream review, not inferred from dates or a positive price.
    previous_session_verified: bool = False
    price_basis_verified: bool = False
    eligible: bool
    pivot: float = Field(gt=0)
    atr20: float = Field(gt=0)
    ma20: float = Field(gt=0)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_dates(self) -> TailPlan:
        if self.prior_session >= self.session:
            raise ValueError("references must precede the session")
        return self


class MinuteBar(CheckedModel):
    """End-labelled 5-minute bar. Naive datetime means Asia/Shanghai, by contract."""

    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    end: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume_shares: float = Field(ge=0)
    amount_yuan: float = Field(ge=0)

    @model_validator(mode="after")
    def check_bar(self) -> MinuteBar:
        if self.end.tzinfo is not None:
            raise ValueError("use naive Asia/Shanghai bar-end timestamps")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent OHLC")
        if self.volume_shares == 0:
            if self.amount_yuan != 0:
                raise ValueError("amount with zero volume")
        else:
            average = self.amount_yuan / self.volume_shares
            if not self.low - 0.011 <= average <= self.high + 0.011:
                raise ValueError(
                    "amount/volume price mismatch; check shares vs lots and adjustment"
                )
        return self


class TailConfig(CheckedModel):
    volume_min: float = Field(default=1.3, gt=0)
    volume_max: float = Field(default=3, gt=0)
    clv_min: float = Field(default=0.65, ge=0, le=1)
    overshoot_atr: float = Field(default=0.5, gt=0)
    structure_atr: float = Field(default=0.5, gt=0)
    range_atr: float = Field(default=1.5, gt=0)
    max_bias: float = Field(default=0.12, gt=0)
    ma_distance_atr: float = Field(default=3, gt=0)

    @model_validator(mode="after")
    def check_volume(self) -> TailConfig:
        if self.volume_min > self.volume_max:
            raise ValueError("invalid volume range")
        return self


def session_ends(day: date) -> list[datetime]:
    return [
        datetime.combine(day, start) + timedelta(minutes=5 * i)
        for start in (time(9, 30), time(13))
        for i in range(1, 25)
    ]


def replay(
    plan: TailPlan,
    bars: list[MinuteBar],
    as_of: datetime,
    cfg: TailConfig | None = None,
) -> dict[str, Any]:
    """Reject incomplete history; never turn missing minute data into a zero signal."""
    cfg = cfg or TailConfig()
    if as_of.tzinfo is not None or as_of.date() != plan.session:
        raise ValueError("as_of must be naive Asia/Shanghai on the planned session")
    eligible_ends = [t for t in session_ends(plan.session) if t <= as_of]
    used = [b for b in bars if b.end <= as_of]
    result: dict[str, Any] = {
        "version": plan.version,
        "ts_code": plan.ts_code,
        "as_of": as_of.isoformat(),
        "state": "数据待核验",
        "ready": None,
        "events": [],
        "checks": {},
        "first_observed_at": None,
        "invalidated_at": None,
        "prior_reference_date": plan.prior_session.isoformat(),
        "structure_reference": plan.pivot - cfg.structure_atr * plan.atr20,
        "performance_validated": False,
    }
    if not plan.previous_session_verified or not plan.price_basis_verified:
        result["reason"] = "需要确认相邻交易日及价格口径/除权事件"
        return result
    if not eligible_ends or [b.end for b in used] != eligible_ends:
        result["reason"] = "缺完整开盘至评估时点5分钟数据，或乱序/重复/缺根"
        return result
    if any(b.ts_code != plan.ts_code for b in used):
        raise ValueError("bar/plan security mismatch")
    if not plan.eligible:
        result.update(state="不在前日观察池", ready=False)
        return result
    cumulative_volume = cumulative_amount = 0.0
    vwaps: list[float | None] = []
    first: str | None = None
    invalid: str | None = None
    events: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}
    high, low = float("-inf"), float("inf")
    last_vr: float | None = None
    last_clv: float | None = None
    state = "等待尾盘窗口"
    for i, bar in enumerate(used):
        cumulative_volume += bar.volume_shares
        cumulative_amount += bar.amount_yuan
        vwaps.append(cumulative_amount / cumulative_volume if cumulative_volume > 0 else None)
        high, low = max(high, bar.high), min(low, bar.low)
        if first and bar.close < result["structure_reference"]:
            invalid = invalid or bar.end.isoformat()
        if bar.end.time() in (time(14, 45), time(14, 50), time(14, 55)):
            baseline = used[i - 7 : i - 1]  # Six bars BEFORE the two confirmation bars.
            last_two = used[i - 1 : i + 1]
            volume = sum(b.volume_shares for b in baseline)
            vr = 3 * sum(b.volume_shares for b in last_two) / volume if volume > 0 else None
            clv = (bar.close - low) / (high - low) if high > low else None
            checks = {
                "two_closes_above_pivot": all(b.close > plan.pivot for b in last_two),
                "two_closes_above_vwap": all(
                    vwaps[j] is not None and used[j].close >= float(vwaps[j] or 0)
                    for j in (i - 1, i)
                ),
                "volume_confirmation": vr is not None and cfg.volume_min <= vr <= cfg.volume_max,
                "close_location": clv is not None and clv >= cfg.clv_min,
                "prior_consolidation": (
                    max(b.high for b in baseline) - min(b.low for b in baseline)
                    <= cfg.range_atr * plan.atr20
                ),
                "limited_extension": (
                    0 < bar.close - plan.pivot <= cfg.overshoot_atr * plan.atr20
                    and 0 < bar.close - plan.ma20 <= cfg.ma_distance_atr * plan.atr20
                    and bar.close / plan.ma20 - 1 <= cfg.max_bias
                ),
            }
            ready = all(checks.values()) and invalid is None
            if ready:
                first = first or bar.end.isoformat()
            state = (
                "结构失效"
                if invalid
                else "条件齐备待复核"
                if ready
                else ("已出现条件，等待复核" if first else "条件未齐备")
            )
            events.append(
                {
                    "bar_end": bar.end.isoformat(),
                    "state": state,
                    "checks": checks.copy(),
                    "volume_ratio": vr,
                    "clv": clv,
                }
            )
            last_vr, last_clv = vr, clv
        elif bar.end.time() >= time(15):
            state = "结构失效" if invalid else "收盘复核" if first else "本日未观察到齐备条件"
    if time(14, 57) <= as_of.time() < time(15) and invalid is None:
        state = "窗口关闭待复核" if first else "尾盘窗口关闭"
    result.update(
        state=state,
        ready=state == "条件齐备待复核",
        events=events,
        checks=checks,
        first_observed_at=first,
        invalidated_at=invalid,
        evaluated_bar_end=used[-1].end.isoformat(),
        last_window_bar_end=events[-1]["bar_end"] if events else None,
        vwap=vwaps[-1],
        volume_ratio=last_vr,
        clv=last_clv,
        failed_checks=[k for k, v in checks.items() if not v],
    )
    result["close_review"] = (
        {
            "close": used[-1].close,
            "above_frozen_pivot": used[-1].close > plan.pivot,
            "above_session_vwap": vwaps[-1] is not None and used[-1].close >= float(vwaps[-1] or 0),
            "structure_invalidated": invalid is not None,
        }
        if used[-1].end.time() == time(15)
        else None
    )
    return result


def tdx_formula(plan: TailPlan | None = None, cfg: TailConfig | None = None) -> str:
    """Frozen constants, five-minute sequence functions; publish PREVIOUS closed bar."""
    c = cfg or TailConfig()
    code = plan.ts_code[:6] if plan else "000000"
    day = int(plan.session.strftime("%Y%m%d")) - 19000000 if plan else 0
    enabled = int(
        bool(
            plan and plan.eligible and plan.previous_session_verified and plan.price_basis_verified
        )
    )
    p, a, m = (plan.pivot, plan.atr20, plan.ma20) if plan else (0.0, 0.0, 0.0)
    return f"""{{W1尾盘条件观察副图，不输出委托。只能用于5分钟、不复权分钟价格}}
{{数值来自前一交易日；未核对计划时默认关闭。不是通用全市场公式}}
{{输出采用REF(...,1)，只显示上一根已完成K线；上根时刻是实际评估时刻}}
ENABLED:={enabled};
TARGET:={day};
P0:={p:.8f};
A0:={a:.8f};
M0:={m:.8f};
BIND:=CODELIKE('{code}') AND DATE=TARGET AND PERIOD=1;
N:=BARSLAST(DATE<>REF(DATE,1))+1;
EXPECTED:=IF(TIME<=1130,((HOUR-9)*60+MINUTE-30)/5,
24+((HOUR-13)*60+MINUTE)/5);
DATAOK:=ENABLED=1 AND BIND AND P0>0 AND A0>0 AND M0>0
AND N=EXPECTED AND REF(TIME,N-1)=935;
VW:=SUM(AMOUNT,N)/MAX(SUM(VOL,N)*100,0.000001);
VBASE:=REF(SUM(VOL,6),2);
VR:=SUM(VOL,2)*3/MAX(VBASE,0.000001);
DH:=HHV(H,N);
DL:=LLV(L,N);
CLV:=IF(DH>DL,(C-DL)/(DH-DL),0);
HOLDOK:=C>P0 AND REF(C,1)>P0;
VWOK:=C>=VW AND REF(C,1)>=REF(VW,1);
VOLOK:=VBASE>0 AND VR>={c.volume_min:g} AND VR<={c.volume_max:g};
LOCOK:=DH>DL AND CLV>={c.clv_min:g};
TIGHT:=REF(HHV(H,6),2)-REF(LLV(L,6),2)<={c.range_atr:g}*A0;
EXTOK:=C>P0 AND C-P0<={c.overshoot_atr:g}*A0 AND C>M0
AND C-M0<={c.ma_distance_atr:g}*A0 AND C/MAX(M0,0.000001)-1<={c.max_bias:g};
WINDOW:=TIME=1445 OR TIME=1450 OR TIME=1455;
RAWOK:=DATAOK AND WINDOW AND HOLDOK AND VWOK AND VOLOK AND LOCOK AND TIGHT AND EXTOK;
SEEN:=COUNT(RAWOK,N)>0;
BAD:=SEEN AND C<P0-{c.structure_atr:g}*A0;
BROKEN:=COUNT(BAD,N)>0;
S:=IF(NOT(DATAOK),-1,IF(BROKEN,3,IF(RAWOK,1,IF(SEEN,2,0))));
{{状态 -1数据待核验，0等待/未齐备，1齐备待复核，2曾齐备待复核，3结构失效}}
状态:IF(BIND,REF(S,1),-1),NODRAW;
上根时刻:REF(TIME,1),NODRAW;
两根站稳:REF(HOLDOK,1),NODRAW;
均价确认:REF(VWOK,1),NODRAW;
量能确认:REF(VOLOK,1),NODRAW;
收盘位置:REF(LOCOK,1),NODRAW;
窄幅整理:REF(TIGHT,1),NODRAW;
偏离合格:REF(EXTOK,1),NODRAW;
{{显示版1.1：图形位置是发布时刻，数字是实际评估时刻；不改变W1条件}}
界上:5,COLOR404040;
界下:0,COLOR404040;
FIRSTOK:=状态=1 AND REF(COUNT(RAWOK,N),1)=1;
FIRSTBAD:=状态=3 AND REF(状态,1)<>3;
STICKLINE(状态=1,0,1.8,3,0),COLORYELLOW;
STICKLINE(状态=2,0,0.6,2,0),COLORCYAN;
STICKLINE(状态=3,0,0.3,2,0),COLORGRAY;
STICKLINE(FIRSTOK,0,2.4,4,0),COLORYELLOW;
DRAWTEXT(FIRSTOK,2.7,'★ 首次齐备'),COLORYELLOW;
DRAWNUMBER(FIRSTOK,1.0,REF(TIME,1)),COLORWHITE;
STICKLINE(FIRSTBAD,0,3.0,4,0),COLORGRAY;
DRAWTEXT(FIRSTBAD,3.3,'X 结构失效'),COLORGRAY;
DRAWNUMBER(FIRSTBAD,1.0,REF(TIME,1)),COLORWHITE;
DRAWTEXT_FIX(ISLASTBAR,0.01,0.01,0,
'W1尾盘观察 | {code} | 绑定日期{day + 19000000 if day else 0} | 5分钟'),COLORWHITE;
DRAWTEXT_FIX(ISLASTBAR,0.01,0.10,0,'只读上根已完成K线；历史状态不是当前交易许可'),COLORGRAY;
DRAWTEXT_FIX(ISLASTBAR AND ENABLED=0,0.01,0.22,0,'未启用：请使用已核验的逐股计划'),COLORYELLOW;
DRAWTEXT_FIX(ISLASTBAR AND ENABLED=1 AND PERIOD<>1,0.01,0.22,0,'周期错误：请切到5分钟图'),COLORRED;
DRAWTEXT_FIX(ISLASTBAR AND ENABLED=1 AND PERIOD=1 AND NOT(CODELIKE('{code}')),
0.01,0.22,0,'股票不匹配：核对本指标绑定代码'),COLORRED;
DRAWTEXT_FIX(ISLASTBAR AND ENABLED=1 AND PERIOD=1 AND CODELIKE('{code}') AND DATE<>TARGET,
0.01,0.22,0,'日期不匹配：计划仅对绑定日有效，不是今日信号'),COLORRED;
DRAWTEXT_FIX(ISLASTBAR AND ENABLED=1 AND BIND AND 状态=-1,
0.01,0.22,0,'数据不足：加载完整前日和当日5分钟线，并核对参数'),COLORRED;
DRAWTEXT_FIX(ISLASTBAR AND 状态=0,0.01,0.22,0,'等待：未到评估窗口或条件未齐备'),COLORGRAY;
DRAWTEXT_FIX(ISLASTBAR AND 状态=1,0.01,0.22,0,'黄色：条件齐备，等待人工复核'),COLORYELLOW;
DRAWTEXT_FIX(ISLASTBAR AND 状态=2,0.01,0.22,0,'青色：曾经齐备，当前部分条件不再成立'),COLORCYAN;
DRAWTEXT_FIX(ISLASTBAR AND 状态=3,0.01,0.22,0,'灰色X：结构已失效，本日不重新激活'),COLORGRAY;
""" + _diagnostic_labels()


def _diagnostic_labels() -> str:
    labels = [
        ("两根站稳", "站稳"),
        ("均价确认", "均价"),
        ("量能确认", "量能"),
        ("收盘位置", "位置"),
        ("窄幅整理", "窄幅"),
        ("偏离合格", "偏离"),
    ]
    return "".join(
        f"DRAWTEXT_FIX(ISLASTBAR AND 状态>=0 AND {field}={value},{0.01 + i * 0.16:.2f},"
        f"0.88,0,'{label}:{word}'),{color};\n"
        for i, (field, label) in enumerate(labels)
        for value, word, color in ((1, "是", "COLORGREEN"), (0, "否", "COLORGRAY"))
    )
