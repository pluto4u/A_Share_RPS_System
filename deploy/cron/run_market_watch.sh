#!/bin/bash
# A股情绪监控 - Cron 定时任务脚本
# 用法: crontab -e 添加以下行
#   0 15 * * 1-5  /path/to/run_market_watch.sh >> /var/log/market_watch.log 2>&1
# (工作日每天下午3点执行)

cd "$(dirname "$0")/../.." || exit 1

# 激活虚拟环境（如有）
# source venv/bin/activate

# 执行云端runner
python3 cloud_runner.py --full

exit $?
