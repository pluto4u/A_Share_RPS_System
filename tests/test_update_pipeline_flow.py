import pandas as pd
from types import SimpleNamespace


def test_today_action_update_runs_warehouse_then_pipeline(monkeypatch):
    import today_action

    calls = []

    def _fake_run(cmd, check=False):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    # Avoid touching real data files during unit test
    monkeypatch.setattr(today_action, "load_scores", lambda: pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-02-13"]),
        "emotion_score": [50.0],
    }))
    monkeypatch.setattr(today_action, "get_signal", lambda scores, ecology: {
        "date": "2026-02-13",
        "score": 50.0,
        "zone_cn": "正常",
        "current_pos": 0.5,
        "target_pos": 0.5,
        "action": "HOLD",
        "chg_1d": 0.0,
        "chg_3d": 0.0,
        "days_since_change": 5,
        "days_until_next": 0,
        "last_change_date": "2026-02-10",
        "last_change_from": 0.5,
        "last_change_to": 0.5,
    })
    monkeypatch.setattr(today_action, "get_etf_holdings", lambda score, as_of: ([], None))
    monkeypatch.setattr(today_action, "print_action", lambda sig, holdings, scores: None)

    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    import sys
    monkeypatch.setattr(sys, "argv", ["today_action.py", "--update"])

    today_action.main()

    assert len(calls) >= 2
    assert calls[0][2] == "emotion_validation.data_layer.data_warehouse"
    assert calls[0][3] == "--update"
    assert calls[1][2] == "emotion_validation.pipeline"


def test_today_action_update_aborts_when_warehouse_update_keeps_failing(monkeypatch):
    import today_action

    calls = []

    def _fake_run(cmd, check=False):
        calls.append(cmd)
        # Simulate warehouse update always failing
        if len(cmd) >= 4 and cmd[2] == "emotion_validation.data_layer.data_warehouse":
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(today_action, "load_scores", lambda: pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-02-13"]),
        "emotion_score": [50.0],
    }))
    monkeypatch.setattr(today_action, "get_signal", lambda scores, ecology: {
        "date": "2026-02-13",
        "score": 50.0,
        "zone_cn": "正常",
        "current_pos": 0.5,
        "target_pos": 0.5,
        "action": "HOLD",
        "chg_1d": 0.0,
        "chg_3d": 0.0,
        "days_since_change": 5,
        "days_until_next": 0,
        "last_change_date": "2026-02-10",
        "last_change_from": 0.5,
        "last_change_to": 0.5,
    })
    monkeypatch.setattr(today_action, "get_etf_holdings", lambda score, as_of: ([], None))
    monkeypatch.setattr(today_action, "print_action", lambda sig, holdings, scores: None)

    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    import sys
    monkeypatch.setattr(sys, "argv", ["today_action.py", "--update"])

    try:
        today_action.main()
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("today_action.main() should exit with code 1 on repeated update failures")

    warehouse_calls = [c for c in calls if len(c) >= 4 and c[2] == "emotion_validation.data_layer.data_warehouse"]
    pipeline_calls = [c for c in calls if len(c) >= 3 and c[2] == "emotion_validation.pipeline"]
    assert len(warehouse_calls) == 3
    assert len(pipeline_calls) == 0


def test_data_warehouse_update_sets_meta_to_actual_data_date(monkeypatch, tmp_path):
    from emotion_validation.data_layer import data_warehouse as dw_mod

    warehouse = dw_mod.DataWarehouse(data_dir=str(tmp_path))
    warehouse._meta = {"last_update_date": "20260219"}

    class _FakeDateTime:
        @staticmethod
        def now():
            return pd.Timestamp("2026-02-24").to_pydatetime()

        @staticmethod
        def strptime(*args, **kwargs):
            from datetime import datetime
            return datetime.strptime(*args, **kwargs)

    monkeypatch.setattr(dw_mod, "datetime", _FakeDateTime)
    monkeypatch.setattr(warehouse, "get_stock_list", lambda: pd.DataFrame({"code": ["000001"]}))
    monkeypatch.setattr(
        warehouse,
        "_fetch_single_stock",
        lambda code, start, end: pd.DataFrame({
            "trade_date": pd.to_datetime(["2026-02-21"]),
            "ts_code": ["000001.SZ"],
            "close": [10.0],
        }),
    )
    monkeypatch.setattr(warehouse, "_fetch_index_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *args, **kwargs: None)

    ok = warehouse.update_data(max_workers=1)

    assert ok is True
    assert warehouse._meta["last_update_date"] == "20260221"
