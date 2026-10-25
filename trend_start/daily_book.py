"""Slot book for the daily pattern system. A sell at the open frees the slot that day."""

from __future__ import annotations

import numpy as np
import pandas as pd

OPEN_EXIT = 10**9


def assign_slots(events: pd.DataFrame, slots: int = 20) -> pd.DataFrame:
    """Mark which signals are bought. Same-day names are taken by RPS120, high first."""
    out = events.copy()
    out["成交"] = False
    if out.empty:
        return out
    out["_exit"] = out["exit_cal"].fillna(OPEN_EXIT)
    held: dict[str, float] = {}
    taken: list[int] = []
    for entry_cal, group in out.groupby("entry_cal", sort=True):
        held = {code: exit_cal for code, exit_cal in held.items() if exit_cal > entry_cal}
        room = slots - len(held)
        if room <= 0:
            continue
        picks = group.sort_values("rps120", ascending=False).head(room + len(held))
        for idx, row in picks.iterrows():
            if row.code in held or len(held) >= slots:
                continue
            held[str(row.code)] = float(row["_exit"])
            taken.append(idx)
    out.loc[taken, "成交"] = True
    return out.drop(columns="_exit")


def horizon_summary(frame: pd.DataFrame, column: str, recent: int = 60) -> dict[str, float]:
    done = frame.dropna(subset=[column])
    window = done.tail(recent)
    def pack(part: pd.DataFrame) -> dict[str, float]:
        if part.empty:
            return {"次数": 0, "上涨占比": np.nan, "平均涨跌": np.nan}
        r = part[column]
        return {"次数": int(len(r)), "上涨占比": float((r > 0).mean()), "平均涨跌": float(r.mean())}
    return {"近端": pack(window), "全部": pack(done)}
