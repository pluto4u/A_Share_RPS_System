#!/bin/bash
# ============================================================================
# A股情绪监控双层架构 - Cron 定时任务配置脚本
# 在云服务器上运行: bash setup_cron.sh
# ============================================================================

PROJECT_DIR="/root/workspace/a_-share_-rps_-system"
PYTHON_EXEC="/usr/bin/python3" # 或修改为您环境的 python 路径, 例如 /root/miniconda3/envs/quant/bin/python

# 检查项目目录
if [ ! -f "$PROJECT_DIR/cloud_runner.py" ]; then
    echo "错误: 找不到 $PROJECT_DIR/cloud_runner.py"
    echo "请确认项目路径是否正确，如果不同请修改脚本中的 PROJECT_DIR"
    exit 1
fi

# 备份现有 crontab
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d) 2>/dev/null

# 写入新的 cron 任务
(crontab -l 2>/dev/null | grep -v "cloud_runner.py" | grep -v "intraday_emergency_monitor.py" | grep -v "emotion_validation"; cat <<CRON

# ============================================================================
# === A股情绪监控双层量化架构 (Two-Tier Architecture) ===
# ============================================================================

# ----------------------------------------------------------------------------
# Tier 1: 盘中极速防守层 (抓崩盘/熔断, 耗时<15秒)
# 交易日 每10分钟运行一次，覆盖完整交易时段
# ----------------------------------------------------------------------------
10,20,30,40,50 10 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC intraday_emergency_monitor.py >> /var/log/emotion_intraday.log 2>&1
0,10,20,30,40,50 11 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC intraday_emergency_monitor.py >> /var/log/emotion_intraday.log 2>&1
0,10,20,30,40,50 13 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC intraday_emergency_monitor.py >> /var/log/emotion_intraday.log 2>&1
0,10,20,30,40,50 14 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC intraday_emergency_monitor.py >> /var/log/emotion_intraday.log 2>&1

# ----------------------------------------------------------------------------
# Tier 2: 尾盘决断进攻层 (全量日线+ETF轮动重度计算, 耗时~3分钟)
# 交易日 14:40 准时启动，确保在 14:45 推送飞书，为 14:50 下单预留时间
# ----------------------------------------------------------------------------
40 14 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC -m emotion_validation.pipeline >> /var/log/emotion_pipeline.log 2>&1
43 14 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC -m emotion_validation.etf_rotation.backtest_rotation >> /var/log/emotion_rotation.log 2>&1
45 14 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC daily_signal.py >> /var/log/emotion_signal.log 2>&1

# ----------------------------------------------------------------------------
# 盘后常规报告 (收盘总结)
# ----------------------------------------------------------------------------
10 15 * * 1-5 cd $PROJECT_DIR && $PYTHON_EXEC cloud_runner.py --full >> /var/log/emotion_monitor.log 2>&1

CRON
) | crontab -

echo "Cron 任务已配置完成！"
echo ""
echo "双层架构作息表:"
echo "✅ [盘中防守] Tier 1: 每半小时极速扫描跌停/大面，若触发熔断立刻推飞书。"
echo "✅ [尾盘进攻] Tier 2: 14:40 启动全量重度计算，14:45 推送最终调仓信号。"
echo "✅ [人工操作] 最终执行: 14:50 - 14:55 收到信号后，手动挂单完成调仓！"
echo ""
echo "查看任务: crontab -l"
