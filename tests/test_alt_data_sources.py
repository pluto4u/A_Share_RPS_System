import pandas as pd


def test_get_stock_list_prefers_non_eastmoney_source(monkeypatch, tmp_path):
    from emotion_validation.data_layer.data_warehouse import DataWarehouse
    import akshare as ak

    dw = DataWarehouse(data_dir=str(tmp_path))

    spot_df = pd.DataFrame({
        "code": ["000001", "300001", "600001", "830001"],
        "name": ["A", "B", "C", "BJ"],
        "total_mv": [1, 2, 3, 4],
        "circ_mv": [1, 2, 3, 4],
    })

    monkeypatch.setattr(ak, "stock_zh_a_spot", lambda: spot_df)
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: (_ for _ in ()).throw(RuntimeError("should not call em")))

    got = dw.get_stock_list()

    assert len(got) == 3
    assert sorted(got["code"].tolist()) == ["000001", "300001", "600001"]


def test_fetch_single_stock_uses_tx_hist_shape(monkeypatch, tmp_path):
    from emotion_validation.data_layer.data_warehouse import DataWarehouse
    import akshare as ak

    dw = DataWarehouse(data_dir=str(tmp_path))

    tx_df = pd.DataFrame({
        "date": ["2026-02-20", "2026-02-21"],
        "open": [10.0, 10.2],
        "close": [10.1, 10.3],
        "high": [10.2, 10.4],
        "low": [9.9, 10.1],
        "amount": [1000000, 1200000],
    })

    monkeypatch.setattr(ak, "stock_zh_a_hist_tx", lambda **kwargs: tx_df)
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fallback should not be called")))

    got = dw._fetch_single_stock("000001", "20260220", "20260225")

    assert got is not None
    assert list(got.columns)[:6] == ["trade_date", "open", "close", "high", "low", "amount"]
    assert got["ts_code"].iloc[0] == "000001.SZ"
    assert pd.to_datetime(got["trade_date"]).max() == pd.Timestamp("2026-02-21")


def test_etf_fetch_prefers_sina_hist(monkeypatch, tmp_path):
    from emotion_validation.etf_rotation.etf_data import ETFDataManager
    import akshare as ak

    mgr = ETFDataManager(data_dir=str(tmp_path))

    sina_df = pd.DataFrame({
        "date": ["2026-02-20", "2026-02-21"],
        "open": [4.0, 4.1],
        "high": [4.1, 4.2],
        "low": [3.9, 4.0],
        "close": [4.05, 4.15],
        "volume": [1000, 1200],
        "amount": [400000, 500000],
    })

    monkeypatch.setattr(ak, "fund_etf_hist_sina", lambda symbol: sina_df)
    monkeypatch.setattr(ak, "fund_etf_hist_em", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("should not call em")))

    data = mgr.fetch_all(start_date="20260220", end_date="20260225")

    assert "510300" in data
    got = data["510300"]
    assert "trade_date" in got.columns
    assert pd.to_datetime(got["trade_date"]).max() == pd.Timestamp("2026-02-21")
