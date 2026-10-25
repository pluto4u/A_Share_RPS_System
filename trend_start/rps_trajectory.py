"""Causal descriptions of cached full-universe RPS, never trade instructions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

PERIODS = (50, 120, 250)
Array = NDArray[Any]


class Phase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    signal: int = Field(ge=0)
    launch: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def causal(self) -> Phase:
        if not self.start <= self.end < self.signal:
            raise ValueError("reference phase must precede confirmation")
        if self.launch is not None and not self.end < self.launch < self.signal:
            raise ValueError("launch must precede the later retest confirmation")
        return self


def direction(value: float) -> str:
    if not np.isfinite(value):
        return "missing"
    return "improving" if value > 0 else "flat_or_down"


def phase_features(close: Array, rps: Mapping[int, Array], phase: Phase) -> dict[str, Any]:
    """Changes are RPS percentile points, not percentage price returns.

    Only data through signal is even validated. Future mutation must not affect
    a signal-time description. NaN stays missing, never zero or forward-filled.
    Reference endpoints follow frozen pattern bars; they are not new parameters.
    """
    if close.ndim != 1 or phase.signal >= len(close):
        raise ValueError("invalid close array or signal position")
    c = close[: phase.signal + 1].astype(float)
    if not np.isfinite(c).all() or np.any(c <= 0):
        raise ValueError("invalid traded close before confirmation")
    fields: dict[str, Any] = {
        "reference_start_bar": phase.start,
        "reference_end_bar": phase.end,
        "signal_bar": phase.signal,
        "reference_close_change_pct": (c[phase.end] / c[phase.start] - 1) * 100,
        "reference_to_signal_close_change_pct": (c[phase.signal] / c[phase.end] - 1) * 100,
        "launch_to_signal_close_change_pct": (
            (c[phase.signal] / c[phase.launch] - 1) * 100 if phase.launch is not None else np.nan
        ),
    }
    for period in PERIODS:
        if period not in rps or rps[period].ndim != 1 or len(rps[period]) != len(close):
            raise ValueError(f"missing or misaligned RPS{period}")
        a = rps[period][: phase.signal + 1].astype(float)
        valid = np.isfinite(a)
        if np.isinf(a).any() or np.any((a[valid] < 0) | (a[valid] > 100)):
            raise ValueError(f"invalid full-universe RPS{period}")
        prefix = f"rps{period}"
        start, end, signal = a[phase.start], a[phase.end], a[phase.signal]
        launch = a[phase.launch] if phase.launch is not None else np.nan
        fields.update(
            {
                f"{prefix}_reference_start": float(start),
                f"{prefix}_reference_end": float(end),
                f"{prefix}_launch": float(launch),
                f"{prefix}_signal": float(signal),
                f"{prefix}_reference_change_pp": float(end - start),
                f"{prefix}_reference_to_signal_change_pp": float(signal - end),
                f"{prefix}_launch_to_signal_change_pp": float(signal - launch),
                f"{prefix}_reference_direction": direction(float(end - start)),
                f"{prefix}_retest_direction": direction(float(signal - launch)),
            }
        )
    return fields
