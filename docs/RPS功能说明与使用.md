# RPS 功能说明与使用

本文档说明本仓库新增的**通达信 RPS 选股**相关功能、使用方式，以及在其他终端或与 Cursor 协作时如何快速继承上下文。

---

## 一、功能概览

| 功能 | 说明 |
|------|------|
| **通达信数据读取** | 从本地 TDX `vipdoc/{sh,sz}/lday/*.day` 读取 A 股日线，输出标准 DataFrame |
| **日线 RPS 选股** | 基于 `learn/TDX_taoboshi.md` 的 8 大条件（RPS120+250、均线多头、回撤、换手等）筛选强势股 |
| **周线 RPS 选股** | 周线 RPS120 翻红 + 穿越 120 周线 + 紧凑吸筹 + 放量突破，参考亨通光电/长光华芯/通鼎互联形态 |
| **双 CSV 输出** | 选股结果同时输出：完整条件表 + 仅股票代码表 |
| **通达信板块导出** | 将选股结果 CSV 转为 TDX 自定义板块 `.blk`，在通达信内直接打开板块查看 |

---

## 二、环境与依赖

- **Python**：3.10+（建议 3.12）
- **依赖**：`pandas`、`numpy`（见项目 `requirements.txt`）
- **数据源**：通达信本地安装目录下的 `vipdoc`，默认路径 `C:/new_tdx/vipdoc`（可通过参数修改）

```bash
# 克隆后在项目根目录
pip install -r requirements.txt
```

---

## 三、数据流

```
通达信 .day 文件 (vipdoc/sh/lday, vipdoc/sz/lday)
    ↓ tdx_reader.load_tdx_market()
日线 DataFrame (trade_date, ts_code, open, high, low, close, vol, amount)
    ↓ rps_calculator.calculate_rps(periods=[120, 250])
带 RPS_120, RPS_250 的日线数据
    ↓ tdx_stock_screener / weekly_rps_screener
选股结果 DataFrame
    ↓ run_tdx_screener.py 写文件
output/tdx_screener_YYYYMMDD.csv          # 完整条件
output/tdx_screener_YYYYMMDD_codes.csv    # 仅 ts_code
    ↓ csv_to_tdx_blk.py（可选）
C:/new_tdx/T0002/blocknew/RPS_YYYYMMDD.blk    # 通达信自定义板块
```

---

## 四、脚本使用说明

### 4.1 一键选股：`run_tdx_screener.py`

从通达信读日线 → 算 RPS → 跑日线 8 条件选股 → 写出**两套 CSV**。

```bash
# 默认：最新交易日，TDX 路径 C:/new_tdx/vipdoc
py -3 run_tdx_screener.py

# 指定日期
py -3 run_tdx_screener.py --date 20260305

# 调参
py -3 run_tdx_screener.py --rps-sum 185 --near-high 0.85

# 诊断单只股票为何未入选
py -3 run_tdx_screener.py --diagnose 600519.SH

# 保存中间 RPS 数据（供后续分析）
py -3 run_tdx_screener.py --save-data
```

**输出文件（均在 `output/` 下）：**

- `tdx_screener_YYYYMMDD.csv`：完整结果（含 ts_code, close, RPS_120, RPS_250, SXHCG1~SXHCG8 等）
- `tdx_screener_YYYYMMDD_codes.csv`：仅一列 `ts_code`，便于导入其他系统或转 .blk

**可选参数：**

- `--tdx-dir`：通达信 vipdoc 路径，默认 `C:/new_tdx/vipdoc`
- `--min-days`：最少交易天数，少于此的股票不参与，默认 250

---

### 4.2 导出通达信板块：`csv_to_tdx_blk.py`

将选股结果 CSV 转为通达信自定义板块文件，在通达信里打开「自定义板块」即可看到对应股票。

```bash
# 默认：自动找最新 output/tdx_screener_*.csv，写出 RPS_日期.blk 到 C:/new_tdx/T0002/blocknew
py -3 csv_to_tdx_blk.py

# 指定输入与板块名
py -3 csv_to_tdx_blk.py --csv output/tdx_screener_20260305.csv --name RPS观察池

# 指定输出目录
py -3 csv_to_tdx_blk.py --out-dir C:/new_tdx/T0002/blocknew
```

**要求：** 输入 CSV 须包含列 `ts_code`（格式如 `600519.SH`、`000001.SZ`）。  
生成后若通达信未刷新，可重启通达信或刷新板块管理。

---

### 4.3 周线 RPS 选股（代码调用）

周线策略在 `rps_analyzer/weekly_rps_screener.py`，当前未做独立 CLI，需在 Python 中调用：

```python
from pathlib import Path
import pandas as pd
from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.weekly_rps_screener import WeeklyRpsScreener, WeeklyScreenerConfig

# 读日线（或使用已有带 RPS 的日线）
df = load_tdx_market(r"C:/new_tdx/vipdoc", min_days=300)
screener = WeeklyRpsScreener(config=WeeklyScreenerConfig())
result = screener.screen(df, target_date="20260305", verbose=True)
# result 为通过周线条件的 DataFrame，可自行写入 CSV
```

策略要点：周线 RPS_120 翻红、穿越 120 周均线、突破前紧凑吸筹（窄幅+缩量）、突破周放量、短期周均线多头。参数见 `WeeklyScreenerConfig`。

---

## 五、关键文件与模块

| 路径 | 作用 |             
|------|------|
| `run_tdx_screener.py` | 选股入口，输出双 CSV |
| `csv_to_tdx_blk.py` | CSV → 通达信 .blk |
| `rps_analyzer/tdx_reader.py` | 读取 TDX .day |
| `rps_analyzer/rps_calculator.py` | 日线 RPS 计算 |
| `rps_analyzer/tdx_stock_screener.py` | 日线 8 条件选股 |
| `rps_analyzer/weekly_rps_screener.py` | 周线吸筹突破选股 |
| `learn/TDX_taoboshi.md` | 通达信公式与选股条件参考 |

---

## 六、在其他终端快速继承

### 6.1 本机/他机直接跑

1. 克隆仓库并安装依赖：
   ```bash
   git clone <repo_url> && cd A_Share_RPS_System
   pip install -r requirements.txt
   ```
2. 若通达信不在 `C:/new_tdx/vipdoc`，使用 `--tdx-dir` 指定。
3. 执行选股与导出：
   ```bash
   py -3 run_tdx_screener.py
   py -3 csv_to_tdx_blk.py
   ```
4. 结果在 `output/`，板块文件在 `C:/new_tdx/T0002/blocknew/`（或你指定的 `--out-dir`）。

### 6.2 与 Cursor 交互时继承上下文

- **推荐**：在新会话或新终端中，直接引用本文档与相关文件，让 AI 理解现有能力与约定：
  - 在 Cursor 中 @ 本文档：`@docs/RPS功能说明与使用.md`
  - 需要改选股逻辑时 @ 公式参考：`@learn/TDX_taoboshi.md`
  - 需要改数据源或输出时 @ 对应脚本或 `rps_analyzer/` 下模块
- **可补充的说明**（复制给 Cursor 即可）：
  - “RPS 选股使用本地通达信数据，入口是 `run_tdx_screener.py`，输出两套 CSV；导出板块用 `csv_to_tdx_blk.py`。详细见 `docs/RPS功能说明与使用.md`。”
- 若你固定使用 Gitee 的某分支（如 `dev0`），可说明：“RPS 相关代码在 `dev0`，文档在 `docs/RPS功能说明与使用.md`。”

这样在其他终端或新开 Cursor 会话时，只需读这份文档即可复现环境与用法，并与 Cursor 对齐上下文。
