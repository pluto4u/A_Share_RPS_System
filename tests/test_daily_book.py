import pandas as pd

from trend_start.daily_book import assign_slots


def test_same_day_buys_keep_the_stronger_names_inside_the_slot_cap():
    events = pd.DataFrame({
        "code": ["A", "B", "C"],
        "entry_cal": [1, 1, 1],
        "exit_cal": [5, 5, 5],
        "rps120": [10.0, 90.0, 50.0],
    })
    taken = assign_slots(events, slots=2)
    assert set(taken.loc[taken["成交"], "code"]) == {"B", "C"}


def test_a_sale_at_the_open_frees_the_slot_for_a_new_buy():
    events = pd.DataFrame({
        "code": ["A", "B"],
        "entry_cal": [1, 5],
        "exit_cal": [5, 9],
        "rps120": [80.0, 70.0],
    })
    taken = assign_slots(events, slots=1)
    assert list(taken.loc[taken["成交"], "code"]) == ["A", "B"]
