#!/bin/bash
# OpenCode 自动执行器 -- 带超时和重试
# 用法: bash oc_run.sh "你的任务描述"
#
# 特性:
#   - 超时自动杀掉重试
#   - 最多重试 3 次
#   - 执行结果写入日志文件
#   - 完成后可查看 git diff 确认改动

TASK="$1"
MAX_RETRIES=3
TIMEOUT_SECONDS=300  # 5 分钟超时
LOG_FILE="oc_run_$(date +%Y%m%d_%H%M%S).log"

if [ -z "$TASK" ]; then
    echo "用法: bash oc_run.sh \"你的任务描述\""
    exit 1
fi

echo "========================================" | tee "$LOG_FILE"
echo "任务: $TASK" | tee -a "$LOG_FILE"
echo "超时: ${TIMEOUT_SECONDS}s | 最大重试: $MAX_RETRIES" | tee -a "$LOG_FILE"
echo "日志: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

for attempt in $(seq 1 $MAX_RETRIES); do
    echo "" | tee -a "$LOG_FILE"
    echo "[尝试 $attempt/$MAX_RETRIES] $(date)" | tee -a "$LOG_FILE"

    timeout $TIMEOUT_SECONDS opencode run "$TASK" >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "" | tee -a "$LOG_FILE"
        echo "[成功] 任务在第 $attempt 次尝试完成" | tee -a "$LOG_FILE"
        echo "" | tee -a "$LOG_FILE"
        echo "--- 文件改动 ---" | tee -a "$LOG_FILE"
        git diff --stat 2>/dev/null | tee -a "$LOG_FILE"
        echo "========================================" | tee -a "$LOG_FILE"
        exit 0
    elif [ $EXIT_CODE -eq 124 ]; then
        echo "[超时] 第 $attempt 次执行超过 ${TIMEOUT_SECONDS}s，准备重试..." | tee -a "$LOG_FILE"
    else
        echo "[失败] 退出码: $EXIT_CODE，准备重试..." | tee -a "$LOG_FILE"
    fi

    sleep 5
done

echo "" | tee -a "$LOG_FILE"
echo "[放弃] $MAX_RETRIES 次尝试均失败" | tee -a "$LOG_FILE"
exit 1
