# 火山引擎 + 飞书部署 SOP

## 环境准备

### 1. 服务器要求
- OS: Ubuntu 20.04 LTS / CentOS 7+
- Python: 3.8+
- 内存: 2GB+ (完整模式建议 4GB+)
- 网络: 可访问 akshare 数据源、飞书 API

### 2. 安装依赖

```bash
# 克隆代码
cd /opt
git clone <your-repo-url> A_Share_RPS_System
cd A_Share_RPS_System

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置飞书

编辑 `feishu_config.json`:

```json
{
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/<你的HOOK_ID>",
    "webhook_secret": "<你的签名密钥>",
    
    "app_id": "<飞书应用ID>",
    "app_secret": "<飞书应用密钥>",
    
    "bitable_app_token": "<多维表格app_token>",
    "bitable_table_id_rps": "<RPS选股表ID>",
    "bitable_table_id_sentiment": "<情绪快照表ID>"
}
```

**获取方式**:
- Webhook URL: 飞书群设置 → 机器人 → 添加自定义机器人
- 应用凭证: 飞书开放平台 → 创建企业自建应用 → 凭证与基础信息
- 多维表格 ID: 打开表格 → 浏览器地址栏获取 `app_token` 和 `table_id`

---

## 部署方式

### 方式 1: Systemd 定时器 (推荐)

```bash
# 1. 修改服务配置
vim deploy/systemd/market-watch.service
# 更新 User 和 WorkingDirectory 为实际路径

# 2. 安装服务
sudo cp deploy/systemd/market-watch.service /etc/systemd/system/
sudo cp deploy/systemd/market-watch.timer /etc/systemd/system/

# 3. 启动定时器
sudo systemctl daemon-reload
sudo systemctl enable market-watch.timer
sudo systemctl start market-watch.timer

# 4. 查看状态
sudo systemctl status market-watch.timer
sudo systemctl list-timers --all | grep market-watch
```

**执行时间**: 工作日每天 15:00 (收盘后)

### 方式 2: Cron

```bash
# 1. 修改脚本路径
vim deploy/cron/run_market_watch.sh
# 更新 cd 路径为实际目录

# 2. 添加 cron 任务
crontab -e
# 添加以下行:
0 15 * * 1-5 /opt/A_Share_RPS_System/deploy/cron/run_market_watch.sh >> /var/log/market_watch.log 2>&1
```

### 方式 3: 手动触发 API

```bash
# 1. 启动 API 服务 (后台运行)
nohup python manual_trigger_api.py > api.log 2>&1 &

# 2. 手动触发 (快速模式)
curl -X POST http://localhost:8000/run-now

# 3. 手动触发 (完整模式)
curl -X POST "http://localhost:8000/run-now?full=true"

# 4. 健康检查
curl http://localhost:8000/health
```

**生产环境建议**: 用 supervisor / PM2 / systemd 管理 API 进程

### 方式 4: 火山函数服务 (FC) 预留入口

项目已提供函数入口文件：`volcengine_handler.py`

- 入口函数：`handler(event, context)`
- 复用逻辑：内部调用 `cloud_runner.run_once(...)`
- 建议触发器：定时触发（工作日每15分钟）

示例 event:

```json
{
  "dry_run": false,
  "full_mode": false,
  "session_only": true
}
```

建议配置：
- Runtime: Python 3.12
- 部署包包含项目根目录全部代码与依赖
- 环境变量或密文中配置飞书 webhook 与 secret

---

## 本地测试

### 1. 快速模式 (约 15 秒)

```bash
python market_gambler_watch.py
```

### 2. 完整模式 (约 3-5 分钟，含广度 + RPS)

```bash
python market_gambler_watch.py --full
```

### 3. 推送飞书测试

```bash
python market_gambler_watch.py --full --feishu
```

### 4. 回测验证

```bash
python backtest_sentiment_daily.py --start 2024-01-01 --end 2024-12-31
```

### 5. 运行全部测试

```bash
pytest tests/ -v
```

---

## 飞书验证清单

运行后检查飞书群是否收到:

1. **消息卡片**
   - [ ] 标题显示日期、时间、盘面状态
   - [ ] 指数总览（沪深300、创业板指等）
   - [ ] 情绪面板（涨跌统计、涨跌比、涨停/跌停/炸板）
   - [ ] 赚钱效应（昨日涨停今日表现）
   - [ ] 庄家信号（ETF 量比）
   - [ ] 综合评分（0-100 分）
   - [ ] **建议仓位**（评分映射）
   - [ ] **最终仓位**（趋势门控 + 广度约束后）
   - [ ] 卡片颜色根据情绪分正确变化

2. **多维表格**（需配置 app_id/secret）
   - [ ] 情绪快照追加到 `bitable_table_id_sentiment`
   - [ ] 包含：日期、情绪评分、建议仓位、涨跌比、涨停数、跌停数、炸板率、赚钱效应

---

## 常见问题

### 1. 数据拉取超时

**现象**: `[ERROR] 无法获取XXX数据`

**解决**:
- akshare 可能限流，等待 1 分钟后重试
- 检查服务器网络，确保可访问 eastmoney / sina 等源

### 2. 飞书推送失败

**现象**: `推送失败: code=xxx`

**解决**:
- 检查 `webhook_url` 是否正确
- 检查签名密钥 `webhook_secret` 是否配置（如启用了签名）
- 查看飞书机器人管理页面的调用日志

### 3. 多维表格写入失败

**现象**: `获取 token 失败` / `批量写入失败`

**解决**:
- 确认 `app_id` / `app_secret` 正确
- 检查应用权限：需开通 `bitable:app` 权限
- 确认表格 ID 正确（浏览器地址栏复制）

### 4. 定时任务未执行

**检查**:
```bash
# Systemd
sudo journalctl -u market-watch.service -n 50

# Cron
tail -f /var/log/market_watch.log
```

---

## GitHub Actions 盘中调度说明

当前工作流已配置：
- 工作日每15分钟触发一次（盘中用 `session_only` 自动过滤非交易时段）
- 工作日 15:05 (UTC+8) 额外执行一次收盘完整任务（含 RPS）

如需调整频率，可编辑 `.github/workflows/market-watch.yml` 的 `schedule.cron`。

---

## 监控建议

1. **日志归档**: 定期清理日志文件，避免磁盘占满
2. **异常告警**: 配置飞书告警群，程序异常时发送通知
3. **数据备份**: 定期备份多维表格数据
4. **性能监控**: 监控 CPU/内存占用，完整模式下载数据较多

---

## 升级流程

```bash
cd /opt/A_Share_RPS_System
git pull origin master
source venv/bin/activate
pip install -r requirements.txt --upgrade

# 重启服务 (如使用 systemd)
sudo systemctl restart market-watch.timer

# 或重启 API (如使用 API 方式)
pkill -f manual_trigger_api.py
nohup python manual_trigger_api.py > api.log 2>&1 &
```
