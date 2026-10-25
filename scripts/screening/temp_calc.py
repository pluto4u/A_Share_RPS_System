from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import pandas as pd
import sys

from project_paths import report_file

try:
    df = pd.read_csv(report_file("etf_rotation", "rotation_nav_all.csv"), parse_dates=['trade_date'], index_col='trade_date')
    df['year'] = df.index.year
    
    # nav_D is the D strategy
    years = df['year'].unique()
    for y in years:
        df_y = df[df['year'] == y]
        ret = df_y['nav_D'].iloc[-1] / df_y['nav_D'].iloc[0] - 1
        print(f"Year {y}: {ret*100:.2f}%")

    # Let's print the return for 2025 and 2026 specifically, and also benchmark nav_A, nav_B, nav_C
    print("\nDetailed annual returns:")
    for y in years:
        df_y = df[df['year'] == y]
        print(f"--- Year {y} ---")
        for col in ['nav_A', 'nav_B', 'nav_C', 'nav_D']:
            ret = df_y[col].iloc[-1] / df_y[col].iloc[0] - 1
            print(f"{col}: {ret*100:.2f}%")

except Exception as e:
    print(f"Error: {e}")
