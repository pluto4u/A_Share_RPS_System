from project_paths import script_file
import importlib.util
import struct
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tdx_feasibility', ROOT/'research/tdx_input_20261007/audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def reader():
    return module.extract(ROOT/'research/rps_repair_20261006/common.py', 'read_raw',
                          dict(Path=Path, struct=struct, pd=pd, PRICE=module.PRICE))

def test_record_units_and_truncated_file(tmp_path):
    path = tmp_path/'sz000001.day'
    path.write_bytes(struct.pack('<5If2I',20250901,1000,1100,900,1050,123456.,7890,0))
    frame = reader()(path)
    assert frame.close.iloc[0] == 10.5
    assert frame.amount.iloc[0] == 123456 and frame.vol.iloc[0] == 7890
    path.write_bytes(b'x')
    with pytest.raises(ValueError, match='truncated'): reader()(path)

def test_duplicate_dates_rejected(tmp_path):
    path = tmp_path/'sz000001.day'
    bar = struct.pack('<5If2I',20250901,1000,1100,900,1050,123456.,7890,0)
    path.write_bytes(bar*2)
    with pytest.raises(ValueError, match='Duplicate'): reader()(path)

def test_adjustment_fail_closed_and_anchor():
    adjust = module.extract(script_file("run_local_tdx_compare.py"),'adjust',
        dict(Any=Any,np=np,PRICE=module.PRICE,EVENT_FIELDS=module.EVENT_FIELDS))
    raw = pd.DataFrame(dict(trade_date=pd.to_datetime(['2025-09-01','2025-09-02']),
        open=[10.,9.],high=[10.,9.],low=[10.,9.],close=[10.,9.],vol=[100.,100.],amount=[1000.,900.]))
    event = dict(date=pd.Timestamp('2025-09-02'),category=1,**dict(zip(module.EVENT_FIELDS,[10.,0.,0.,0.])))
    frame, count = adjust(raw,pd.DataFrame([event]),pd.Timestamp('2025-09-02'),bars=None)
    assert count == 1 and frame.close.tolist() == [9.,9.]
    event['category'] = 11
    with pytest.raises(ValueError, match='unsupported'): adjust(raw,pd.DataFrame([event]),pd.Timestamp('2025-09-02'),bars=None)

def test_raw_bundle_never_enters_strategy(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(ROOT/'research/tdx_input_20261007'))
    import input_bundle
    monkeypatch.setattr(input_bundle,'policy',lambda:({'strategy_input_ready':False,'adjustment':'unadjusted'},{}))
    with pytest.raises(RuntimeError,match='not accepted'): input_bundle.load_for_strategy()
