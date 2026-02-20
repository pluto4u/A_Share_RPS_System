#!/bin/bash
# ============================================================================
# A股情绪监控 - Cron 定时任务配置脚本
# 在云服务器上运行: bash setup_cron.sh
# ============================================================================

PROJECT_DIR="/root/projects/A_Share_RPS_System"

# 检查项目目录
if [ ! -f "$PROJECT_DIR/cloud_runner.py" ]; then
    echo "错误: 找不到 $PROJECT_DIR/cloud_runner.py"
    echo "请确认项目路径是否正确，如果不同请修改脚本中的 PROJECT_DIR"
    exit 1
fi

# 备份现有 crontab
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d) 2>/dev/null

# 写入新的 cron 任务
(crontab -l 2>/dev/null | grep -v "cloud_runner.py"; cat <<'CRON'

# === A股情绪监控定时任务 ===
# 盘中快速监控: 交易日 9:35, 10:00, 10:30, 11:00, 13:30, 14:00, 14:30 (周一至周五)
35 9 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
0 10 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
30 10 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
0 11 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
30 13 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
0 14 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1
30 14 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --session-only >> /var/log/emotion_monitor.log 2>&1

# 盘后完整分析: 交易日 15:10 (周一至周五)
10 15 * * 1-5 cd /root/projects/A_Share_RPS_System && /usr/bin/python3 cloud_runner.py --full >> /var/log/emotion_monitor.log 2>&1

CRON
) | crontab -

echo "Cron 任务已配置完成！"
echo ""
echo "已设置的定时任务:"
echo "  盘中快速监控: 9:35 10:00 10:30 11:00 13:30 14:00 14:30"
echo "  盘后完整分析: 15:10"
echo ""
echo "查看日志: tail -f /var/log/emotion_monitor.log"
echo "查看任务: crontab -l"
echo "删除任务: crontab -l | grep -v cloud_runner | crontab -"
