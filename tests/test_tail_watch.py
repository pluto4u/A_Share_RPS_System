"""Synthetic bars prove causal behavior, not empirical profitability."""

import csv
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from rps_analyzer.tail_watch import MinuteBar, TailPlan, replay, session_ends, tdx_formula


def plan() -> TailPlan:
    return TailPlan(
        ts_code="600000.SH",
        prior_session=date(2026, 9, 23),
        session=date(2026, 9, 24),
        previous_session_verified=True,
        price_basis_verified=True,
        eligible=True,
        pivot=100,
        atr20=4,
        ma20=98,
        evidence_sha256="0" * 64,
    )


def bars() -> list[MinuteBar]:
    result = []
    for stamp in session_ends(date(2026, 9, 24)):
        rising = stamp.hour == 14 and stamp.minute in (40, 45)
        close, volume = (101.0, 2000.0) if rising else (99.0, 1000.0)
        result.append(
            MinuteBar(
                ts_code="600000.SH",
                end=stamp,
                open=close,
                high=close + 0.1,
                low=close - 0.1,
                close=close,
                volume_shares=volume,
                amount_yuan=volume * close,
            )
        )
    return result


def test_positive_tail_signal_then_no_repeated_new_event() -> None:
    result = replay(plan(), bars(), datetime(2026, 9, 24, 14, 45))
    assert result["state"] == "条件齐备待复核"
    assert result["volume_ratio"] == 2
    assert result["first_observed_at"] == "2026-09-24T14:45:00"
    later = replay(plan(), bars(), datetime(2026, 9, 24, 14, 55))
    assert later["state"] == "已出现条件，等待复核"
    assert later["first_observed_at"] == result["first_observed_at"]


def test_not_yet_completed_bar_is_ignored_and_future_cannot_change_signal() -> None:
    data = bars()
    before = replay(plan(), data, datetime(2026, 9, 24, 14, 44))
    assert before["first_observed_at"] is None
    target = datetime(2026, 9, 24, 14, 45)
    original = replay(plan(), data, target)
    shortened = [b for b in data if b.end <= target]
    assert replay(plan(), shortened, target) == original


@pytest.mark.parametrize("kind", ["missing", "duplicate", "reversed", "empty"])
def test_invalid_coverage_is_unknown(kind: str) -> None:
    data = bars()
    if kind == "missing":
        data.pop(3)
    elif kind == "duplicate":
        data.insert(3, data[3])
    elif kind == "reversed":
        data.reverse()
    else:
        data = []
    result = replay(plan(), data, datetime(2026, 9, 24, 14, 45))
    assert result["state"] == "数据待核验"
    assert result["ready"] is None


def test_failure_is_sticky_even_after_recovery() -> None:
    data = bars()
    for i, b in enumerate(data):
        if b.end.hour == 14 and b.end.minute == 50:
            data[i] = MinuteBar(
                ts_code=b.ts_code,
                end=b.end,
                open=97,
                high=97.1,
                low=96.9,
                close=97,
                volume_shares=1000,
                amount_yuan=97000,
            )
    result = replay(plan(), data, datetime(2026, 9, 24, 15))
    assert result["state"] == "结构失效"
    assert result["invalidated_at"] == "2026-09-24T14:50:00"
    assert result["ready"] is False


def test_unverified_basis_and_auction_window_are_not_ready() -> None:
    p = plan().model_copy(update={"price_basis_verified": False})
    assert replay(p, bars(), datetime(2026, 9, 24, 14, 45))["ready"] is None
    assert replay(plan(), bars(), datetime(2026, 9, 24, 14, 58))["ready"] is False


def test_security_binding_and_date_guard() -> None:
    with pytest.raises(ValueError):
        replay(plan(), bars(), datetime(2026, 9, 25, 14, 45))
    data = bars()
    data[0] = data[0].model_copy(update={"ts_code": "600001.SH"})
    with pytest.raises(ValueError):
        replay(plan(), data, datetime(2026, 9, 24, 14, 45))


def test_volume_unit_and_ohlc_validation() -> None:
    data = bars()[0].model_dump()
    data["volume_shares"] /= 100
    with pytest.raises(ValidationError):
        MinuteBar.model_validate(data)


def test_above_pivot_but_overextended_is_not_ready() -> None:
    p = plan().model_copy(update={"atr20": 1.0})
    result = replay(p, bars(), datetime(2026, 9, 24, 14, 45))
    assert result["ready"] is False
    assert "limited_extension" in result["failed_checks"]


def test_native_template_is_disabled_and_closed_bar_only() -> None:
    native = tdx_formula()
    assert "ENABLED:=0;" in native
    assert "状态:IF(BIND,REF(S,1),-1),NODRAW;" in native
    for forbidden in ("DYNAINFO", "#DAY", "FROMOPEN", "BACKSET"):
        assert forbidden not in native
    file = Path(__file__).resolve().parents[1] / "config/tail_watch_w1.tdx.txt"
    assert file.read_text(encoding="utf-8") == native


def test_display_marks_first_event_and_explains_missing_configuration() -> None:
    native = tdx_formula(plan())
    assert "FIRSTOK:=状态=1 AND REF(COUNT(RAWOK,N),1)=1;" in native
    assert "FIRSTBAD:=状态=3 AND REF(状态,1)<>3;" in native
    for text in ("★ 首次齐备", "X 结构失效", "周期错误", "日期不匹配", "股票不匹配", "数据不足"):
        assert text in native
    for field in ("站稳", "均价", "量能", "位置", "窄幅", "偏离"):
        assert f"'{field}:是'" in native
        assert f"'{field}:否'" in native
    assert "绑定日期20260924" in native
    assert "DRAWNUMBER(FIRSTOK,1.0,REF(TIME,1))" in native


@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_replay_cli_exports_audited_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    from run_tail_watch import main

    p = tmp_path / "synthetic.plan.json"
    b = tmp_path / f"synthetic.bars.{suffix}"
    p.write_text(plan().model_dump_json(), encoding="utf-8")
    rows = [bar.model_dump(mode="json") for bar in bars()]
    if suffix == "json":
        b.write_text(json.dumps(rows), encoding="utf-8")
    else:
        with b.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    output = tmp_path / "result"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_tail_watch.py",
            "--plan",
            str(p),
            "--bars",
            str(b),
            "--as-of",
            "2026-09-24T14:45:00",
            "--output",
            str(output),
        ],
    )
    main()
    result = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert result["ready"] is True
    assert len(result["source_hashes"]["bars_sha256"]) == 64
    assert (output / "indicator.tdx.txt").read_text(encoding="utf-8") == tdx_formula(plan())
