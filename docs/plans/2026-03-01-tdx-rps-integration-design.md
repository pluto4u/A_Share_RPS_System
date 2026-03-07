# TDX本地数据源 + RPS计算 + TOS备份 集成设计

> **下一步：** 按此设计执行实现计划，参见同目录 `2026-03-01-tdx-rps-integration-plan.md`

**Goal:** 以本地通达信 `.day` 文件为数据源，计算全市场 A 股 RPS（20/60/120/250日），
结果存入 `backtest/data/rps/`，仅将每日最终快照上传到 TOS。

**Architecture:** 新增独立数据层模块 `tdx_rps_repository.py`，不破坏现有 `DataWarehouse`
（akshare 路径）；在 `tos_storage.py` 的 `SYNC_DIRS` 中追加 RPS 目录，
实现一键 `sync-up` 即可备份。

**Tech Stack:** pytdx 1.72, pandas 3.x, pyarrow (parquet), tos SDK（已有），struct（标准库）

---

## 约束与决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 存储路径 | `backtest/data/rps/` | 与现有仓库统一，tos_storage 已覆盖此目录 |
| 文件格式 | `rps_YYYYMMDD.parquet` + `rps_YYYYMMDD.csv` | parquet 供程序消费，csv 供人工查验 |
| latest 副本 | `backtest/data/rps/latest.parquet` | 下游模块无需关心日期，直接读 latest |
| TOS 策略 | 仅备份最终快照（A选项） | YAGNI，中间层无需云端存储 |
| TOS 路径 | 自动纳入 `SYNC_DIRS`（`backtest/data/rps/` → TOS同路径） | 复用现有 sync-up 机制，零额外代码 |
| 数据源 | 通达信本地 `.day` 二进制文件（`D:/tdx/vipdoc/{sh,sz}/lday/`) | 离线读取，无网络限制，速度快 |
| A股过滤 | 按代码前3位段过滤：SH(600/601/603/605/688/689) SZ(000/001/002/003/300/301) | 与前序分析一致 |
| TOS 失败策略 | 本地写成功后再上传；TOS失败仅告警，不回滚本地 | 本地是主库，TOS是备份 |

---

## 数据结构（最终结果表）

| 列名 | 类型 | 说明 |
|------|------|------|
| trade_date | datetime64 | 计算基准日（即最新交易日） |
| ts_code | str | 统一标识，如 `600000.SH` |
| market | str | `sh` / `sz` |
| code | str | 纯代码，如 `600000` |
| close | float | 最新收盘价（/100） |
| ret_20 | float | 20日涨跌幅 % |
| ret_60 | float | 60日涨跌幅 % |
| ret_120 | float | 120日涨跌幅 % |
| ret_250 | float | 250日涨跌幅 % |
| rps_20 | float | 20日 RPS（百分位0-100） |
| rps_60 | float | 60日 RPS |
| rps_120 | float | 120日 RPS |
| rps_250 | float | 250日 RPS |
| data_days | int | 该股有效交易日数（供过滤次新股） |
| created_at | datetime64 | 本次计算时间 |
| source | str | 固定为 `tdx_local` |

---

## 本地文件布局

```
backtest/data/rps/
├── daily/
│   ├── rps_20260227.parquet   # 当日快照（保留历史，供回溯）
│   ├── rps_20260227.csv
│   ├── rps_20260228.parquet
│   └── ...
└── latest.parquet             # 始终指向最新日（覆盖写）
```

---

## 模块布局

```
emotion_validation/data_layer/
├── data_warehouse.py          # 已有，akshare 路径，不动
└── tdx_rps_repository.py      # 新增：TDX读取 + RPS计算 + 存储 + TOS上传
```

`tdx_rps_repository.py` 对外提供：
- `TdxRpsRepository(tdx_dir, data_dir)` — 主类
- `.run(date_str=None)` — 全流程：读取 → 计算 → 保存本地 → 上传TOS
- `.load_latest()` — 读取 `latest.parquet`，供下游直接消费
- `.load_by_date(date_str)` — 读取指定日期快照

---

## TOS 接入

在 `tos_storage.py` 的 `SYNC_DIRS` 追加：

```python
{"local": "backtest/data/rps", "remote": "backtest/data/rps", "patterns": ["*.parquet", "*.csv"]},
```

执行 `python tos_storage.py sync-up` 即自动同步 RPS 目录，无需额外代码。

---

## 调用方式

```bash
# 计算今日RPS并保存（自动识别最新交易日）
python -m emotion_validation.data_layer.tdx_rps_repository --run

# 计算指定日期
python -m emotion_validation.data_layer.tdx_rps_repository --run --date 20260227

# 计算完成后立即上传TOS
python -m emotion_validation.data_layer.tdx_rps_repository --run --sync-tos

# 查看最近结果摘要
python -m emotion_validation.data_layer.tdx_rps_repository --status
```

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| `.day` 文件不足 N 日（次新股） | 跳过，不纳入排名 |
| 结构体解析失败 | 跳过该文件，记录 warning |
| 本地 parquet 写失败 | 抛出异常，终止任务 |
| TOS 上传失败 | 仅 warning，不影响本地结果 |
| TDX 目录不存在 | 启动时 FileNotFoundError，提前终止 |

---

## 状态记录

`backtest/data/rps/meta.json`：

```json
{
  "last_trade_date": "20260227",
  "symbol_count": 5175,
  "created_at": "2026-02-28T08:30:00",
  "source": "tdx_local",
  "tdx_dir": "D:/tdx/vipdoc"
}
```
