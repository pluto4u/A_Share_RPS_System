# A股情绪监控与 RPS 选股系统

基于 akshare 开源数据接口，零成本实现 A 股市场情绪监控 + 相对强度选股，并支持飞书机器人推送与云端定时调度。

代码功能、运行数据和生成产物已经分层：功能索引见
[`docs/FUNCTION-CATALOG.md`](docs/FUNCTION-CATALOG.md)，三工程整合边界见
[`docs/THREE-PROJECT-INTEGRATION.md`](docs/THREE-PROJECT-INTEGRATION.md)。

默认目录：

- `data/market/`：行情与缓存，只作为运行数据
- `outputs/reports/`：回测和研究报告
- `outputs/exports/`：RPS Excel/CSV 导出
- `outputs/signals/`：每日信号与情绪历史

可通过 `A_SHARE_DATA_DIR` 和 `A_SHARE_OUTPUT_DIR` 将数据、产物迁移到其他磁盘。

## 策略档案与本地内容工作流

全部策略、基准和研究实验见 [策略档案索引](strategy_archive/INDEX.md)，每项附规则、状态和源码快照。
AI存储与检索约定见 [归档方案](docs/AI-STORAGE.md)。

```powershell
python tools/workspace.py archive
python tools/strategy_archive.py search 龙回头
python tools/workspace.py breadth
```

最后一条命令直接读取本地通达信 `.day`，生成市场宽度数据、三张1080×1440图片及小红书文案。
默认读取 `C:/new_tdx`，也可通过 `--tdx-root` 或环境变量 `TDX_ROOT` 指定目录。
数据日期采用本地文件实际日期。
详细口径及运行方法见 [工作流说明](docs/TDX-BREADTH-WORKFLOW.md)。

---

## 功能模块

### 1. 市场情绪监控 (`scripts/screening/market_gambler_watch.py`)

**快速模式 (约 15 秒)**:
- 8 大指数总览（实时价格 + MA20 趋势 + 量比）
- 情绪面板（涨跌统计 / 涨停·跌停·炸板 / 涨跌比 / 涨幅分布 / 连板梯队）
- 赚钱效应（昨日涨停股今日表现）
- 庄家信号（510300 ETF 量比异常检测）

**完整模式 (约 3-5 分钟，添加 `--full` 参数)**:
- 全市场广度（收盘 > MA20 占比，冰点警报）
- RPS 筛选（RPS>90 & 跌幅<2% Top10）

**使用**:
```bash
python scripts/screening/market_gambler_watch.py            # 快速模式
python scripts/screening/market_gambler_watch.py --full     # 完整模式
python scripts/screening/market_gambler_watch.py --full --feishu  # 推送飞书群

# 盘中循环监控（默认每15分钟一轮，建议工作时段使用）
python scripts/screening/market_gambler_watch.py --loop --interval-minutes 15 --session-only --feishu

# 盘中循环 + 完整模式（更耗时）
python scripts/screening/market_gambler_watch.py --loop --interval-minutes 15 --session-only --full --feishu
```

---

### 2. RPS 选股系统 (`scripts/screening/rps_system.py`)

基于相对强度排名（Relative Price Strength）选股：
- 大盘风控（沪深300 MA20 趋势判断）
- 数据清洗（去 ST / 停牌 / 微盘 / 次新）
- RPS 计算（50日 & 120日百分位排名）
- Excel 输出 + 飞书多维表格同步

**使用**:
```bash
python scripts/screening/rps_system.py                # 本地运行
python scripts/screening/rps_system.py --feishu       # 推送飞书群
```

---

### 3. 统一情绪引擎 (`scripts/screening/sentiment_engine.py`) **NEW**

集中式情绪决策模块，提供：
- `score_to_position()`: 评分 → 仓位映射（0/10/30/60/80%）
- `apply_breadth_cap()`: 广度约束（冰点时上限 10%）
- `final_position()`: 最终仓位 = min(评分仓位, 趋势门控, 广度上限)

确保本地监控、回测、云端定时任务的策略逻辑一致。

---

### 4. 日线情绪回测 (`scripts/daily/backtest_sentiment_daily.py`) **NEW**

根据每日情绪评分 → 仓位信号，对沪深300指数进行择时回测。

**使用**:
```bash
python scripts/daily/backtest_sentiment_daily.py --start 2018-01-01 --end 2026-12-31
```

**输出指标**:
- 策略 / 基准年化收益
- 最大回撤
- Calmar 比率
- Sharpe 比率

---

### 5. 云端定时执行 (`scripts/integration/cloud_runner.py`) **NEW**

供 cron / systemd timer / 云函数等调度使用，执行一次完整的情绪监控 + 飞书推送。

**使用**:
```bash
python scripts/integration/cloud_runner.py                  # 正式执行
python scripts/integration/cloud_runner.py --dry-run        # 试运行
python scripts/integration/cloud_runner.py --full           # 完整模式
python scripts/integration/cloud_runner.py --session-only   # 仅交易时段执行（云端定时推荐）
```

**部署示例** (systemd):
```bash
sudo cp deploy/systemd/market-watch.service /etc/systemd/system/
sudo cp deploy/systemd/market-watch.timer /etc/systemd/system/
sudo systemctl enable --now market-watch.timer
```

---

### 6. 手动触发 API (`scripts/integration/manual_trigger_api.py`) **NEW**

提供 HTTP 接口供手动触发情绪监控，不依赖定时任务。

**启动**:
```bash
uvicorn manual_trigger_api:app --host 0.0.0.0 --port 8000
```

**接口**:
- `POST /run-now` - 立即执行一次监控
- `GET /health` - 健康检查

**调用示例**:
```bash
curl -X POST http://localhost:8000/run-now
curl -X POST "http://localhost:8000/run-now?full=true"
```

---

## 飞书集成

### 1. 消息卡片推送

收盘后自动推送：
- 指数总览 + 情绪面板 + 赚钱效应 + 庄家信号
- 综合情绪评分 (0-100)
- 建议仓位（评分映射）
- **最终仓位**（趋势门控 + 广度约束后）

### 2. 多维表格同步

- **情绪快照表**: 每日追加一行（日期、评分、建议仓位、涨跌比等）
- **RPS 选股表**: 每次运行覆盖最新入选股票

### 配置

编辑 `feishu_config.json`:
```json
{
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/<HOOK_ID>",
    "webhook_secret": "",
    "app_id": "",
    "app_secret": "",
    "bitable_app_token": "",
    "bitable_table_id_rps": "",
    "bitable_table_id_sentiment": ""
}
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 本地测试

```bash
# 快速模式监控
python scripts/screening/market_gambler_watch.py

# 完整模式 + 飞书推送
python scripts/screening/market_gambler_watch.py --full --feishu

# RPS 选股
python scripts/screening/rps_system.py --feishu

# 回测验证
python scripts/daily/backtest_sentiment_daily.py --start 2024-01-01 --end 2024-12-31
```

### 3. 运行测试

```bash
pytest tests/ -v
```

**预期结果**: 8 passed (含单元测试 + 集成测试)

---

## 云端部署

详见 [部署 SOP](docs/deployment/volcengine-feishu-sop.md)，支持：
- Systemd 定时器（推荐）
- Cron 任务
- 手动触发 API
- 云函数 / Serverless

---

## 项目结构

```
A_Share_RPS_System/
├── market_gambler_watch.py       # 情绪监控主程序
├── rps_system.py                 # RPS 选股主程序
├── sentiment_engine.py           # 统一情绪决策引擎 (NEW)
├── backtest_sentiment_daily.py   # 日线回测入口 (NEW)
├── cloud_runner.py               # 云端定时执行器 (NEW)
├── manual_trigger_api.py         # 手动触发 API (NEW)
├── feishu_bot.py                 # 飞书集成模块
├── feishu_config.json            # 飞书配置文件
├── requirements.txt              # 依赖清单
├── tests/                        # 测试目录
│   ├── test_sentiment_engine.py
│   ├── test_sentiment_snapshot.py
│   ├── test_backtest_sentiment_daily.py
│   ├── test_cloud_runner.py
│   ├── test_feishu_card_payload.py
│   ├── test_manual_trigger_api.py
│   └── integration/
│       └── test_e2e_sentiment_pipeline.py
├── backtest/
│   └── configs/                  # 只保留版本化回测配置
├── data/
│   └── market/                   # 行情与运行缓存（不入 Git）
├── outputs/
│   ├── reports/                  # 回测与研究报告（不入 Git）
│   ├── exports/                  # Excel/CSV 导出（不入 Git）
│   ├── signals/                  # 每日信号与状态（不入 Git）
│   └── logs/                     # 日志（不入 Git）
├── deploy/
│   ├── cron/                     # Cron 脚本
│   └── systemd/                  # Systemd 配置
├── docs/
│   ├── plans/                    # 设计文档 + 实施计划
│   └── deployment/               # 部署 SOP
└── tools/
    └── workspace.py              # 三工程统一状态与清单工具
```

---

## 技术栈

- **数据源**: akshare (免费开源)
- **数据处理**: pandas, numpy
- **Web 框架**: FastAPI, uvicorn
- **通知推送**: 飞书机器人 Webhook + 多维表格 API
- **测试**: pytest
- **部署**: systemd / cron / 云函数

---

## 开发指南

### 本地运行命令

```bash
# 快速情绪监控
python scripts/screening/market_gambler_watch.py

# 完整模式（含广度+RPS）
python scripts/screening/market_gambler_watch.py --full

# 推送飞书
python scripts/screening/market_gambler_watch.py --full --feishu

# RPS 选股
python scripts/screening/rps_system.py --feishu

# 回测
python scripts/daily/backtest_sentiment_daily.py --start 2024-01-01 --end 2024-12-31

# 云端模拟执行
python scripts/integration/cloud_runner.py --dry-run

# 启动手动触发 API
python scripts/integration/manual_trigger_api.py
```

### 云部署命令

**Systemd 定时器**:
```bash
sudo systemctl start market-watch.timer
sudo systemctl status market-watch.timer
```

**手动触发 API**:
```bash
curl -X POST http://your-server:8000/run-now
```

### 飞书验证结果

运行后检查飞书群消息卡片是否包含：
- ✅ 综合评分 (0-100)
- ✅ 建议仓位 (评分映射)
- ✅ 最终仓位 (趋势门控 + 广度约束)
- ✅ 涨跌统计、赚钱效应、庄家信号等完整面板

---

## License

MIT

---

## 致谢

- 数据来源: [akshare](https://github.com/akfamily/akshare)
- 飞书开放平台: [Lark Open Platform](https://open.feishu.cn/)
