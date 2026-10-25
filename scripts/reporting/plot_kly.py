from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False
from matplotlib.patches import Rectangle
from pathlib import Path
from trend_start.panel import load_panel

panel = load_panel(Path("output/daily_research_cache_20260930"))
i = list(panel.codes).index("002821.SZ")
dates = pd.DatetimeIndex(panel.dates)
df = pd.DataFrame({f: panel.field(f)[i] for f in ("open","high","low","close")}, index=dates).dropna().tail(45)
for w in (5, 10, 13, 20, 30):
    df[f"ma{w}"] = df.close.rolling(w).mean()

signals = {"2026-08-19", "2026-09-16"}   # computed train-track circles
fig, ax = plt.subplots(figsize=(12, 6), dpi=110)
x = np.arange(len(df))
colors = {"up": "#e0332b", "dn": "#18a058"}
for k, (dt, r) in enumerate(df.iterrows()):
    up = r.close >= r.open
    co = colors["up"] if up else colors["dn"]
    ax.plot([k, k], [r.low, r.high], color=co, lw=0.8)
    ax.add_patch(Rectangle((k - 0.32, min(r.open, r.close)), 0.64,
                           abs(r.close - r.open) + 1e-9,
                           facecolor=co if up else "white", edgecolor=co, lw=0.9))
for w, co in zip((5, 10, 13, 20, 30), ("#f0a020","#9b59b6","#18a058","#e0332b","#2080f0")):
    ax.plot(x, df[f"ma{w}"], lw=1.0, label=f"MA{w}", color=co)
for k, dt in enumerate(df.index):
    if str(dt.date()) in signals:
        ax.add_patch(plt.Circle((k, df.low.iloc[k] * 0.965), 1.1,
                                facecolor="none", edgecolor="#e0332b", lw=1.6))
        ax.annotate(str(dt.date()), (k, df.low.iloc[k] * 0.965),
                    textcoords="offset points", xytext=(0, -16),
                    ha="center", fontsize=9, color="#e0332b")
step = 5
ax.set_xticks(x[::step])
ax.set_xticklabels([d.strftime("%m-%d") for d in df.index[::step]])
ax.legend(loc="upper left", fontsize=8, ncol=5, frameon=False)
ax.set_title("凯莱英 002821 — 火车轨信号复算（红圈=我们的信号日）")
ax.grid(alpha=0.25)
fig.tight_layout()
fig.savefig("output/kly_train_track_check.png")
print("saved")
