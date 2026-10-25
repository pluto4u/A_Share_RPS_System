from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError
from trend_start.rps_trajectory import PERIODS, Array, Phase, direction, phase_features


def inputs() -> tuple[Array, dict[int, Array]]:
    close = np.linspace(10, 20, 20)
    ranks = {p: np.linspace(20, 90, 20) for p in PERIODS}
    return close, ranks


def test_later_retest_only() -> None:
    with pytest.raises(ValidationError):
        Phase(start=0, end=4, launch=5, signal=5)
    with pytest.raises(ValidationError):
        Phase(start=0, end=4, launch=7, signal=6)


def test_future_mutation_cannot_change_snapshot() -> None:
    close, ranks = inputs()
    phase = Phase(start=0, end=4, launch=5, signal=7)
    first = phase_features(close, ranks, phase)
    close[8:] = -1
    for values in ranks.values():
        values[8:] = 999
    assert phase_features(close, ranks, phase) == first


def test_cache_values_not_candidate_percentiles() -> None:
    close, ranks = inputs()
    result = phase_features(close, ranks, Phase(start=1, end=4, launch=5, signal=7))
    assert result["rps120_signal"] == ranks[120][7]
    assert result["rps120_signal"] != 100  # a single candidate must not rank itself first
    assert result["rps50_reference_change_pp"] == ranks[50][4] - ranks[50][1]


def test_missing_is_missing_not_zero() -> None:
    close, ranks = inputs()
    ranks[250][1] = np.nan
    result = phase_features(close, ranks, Phase(start=1, end=4, launch=5, signal=7))
    assert np.isnan(result["rps250_reference_change_pp"])
    assert result["rps250_reference_direction"] == "missing"


def test_no_launch_is_not_fabricated() -> None:
    close, ranks = inputs()
    result = phase_features(close, ranks, Phase(start=1, end=4, signal=7))
    assert np.isnan(result["rps50_launch"])
    assert result["rps50_retest_direction"] == "missing"


@pytest.mark.parametrize("problem", ["missing_period", "misaligned", "bad_rank", "bad_close"])
def test_bad_data_explicit_error(problem: str) -> None:
    close, ranks = inputs()
    if problem == "missing_period":
        del ranks[50]
    elif problem == "misaligned":
        ranks[50] = ranks[50][:-1]
    elif problem == "bad_rank":
        ranks[50][3] = 101
    else:
        close[3] = 0
    with pytest.raises(ValueError):
        phase_features(close, ranks, Phase(start=1, end=4, launch=5, signal=7))


def test_flat_direction_is_not_missing() -> None:
    assert direction(0) == "flat_or_down"
    assert direction(np.nan) == "missing"
