# 注册 / 移除 Windows 计划任务：每个工作日收盘后自动做每日复盘并生成小红书素材（不发布）。
#
#   .\scripts\daily\register_daily_review_task.ps1                 # 工作日 17:30 运行
#   .\scripts\daily\register_daily_review_task.ps1 -Time 18:00
#   .\scripts\daily\register_daily_review_task.ps1 -WithForwardLog # 同时记录前瞻账本
#   .\scripts\daily\register_daily_review_task.ps1 -Remove         # 取消任务
#
# 前提：运行前通达信里已下载当天日线。任务会在 17:30 起最多等 90 分钟（每 5 分钟检查一次），
# 等不到就退出并写日志：outputs\logs\daily_review\<日期>.log。以当前用户身份运行，不需要管理员权限。
param(
    [string]$Time = "17:30",
    [int]$WaitMinutes = 90,
    [switch]$WithForwardLog,
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$name = "ARPS_DailyReview"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "已移除计划任务 $name"
    } else {
        Write-Host "没有找到计划任务 $name"
    }
    exit 0
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = (Get-Command python -ErrorAction Stop).Source
$argLine = "scripts\daily\run_daily_review.py --require-today --wait-minutes $WaitMinutes"
if ($WithForwardLog) { $argLine += " --with-forward-log" }

$action   = New-ScheduledTaskAction -Execute $python -Argument $argLine -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "A股每日复盘：看板 + 小红书素材（只生成，不发布）" -Force | Out-Null

Write-Host "已注册计划任务 $name：工作日 $Time，工作目录 $root"
Write-Host "命令：$python $argLine"
Write-Host "手动试跑：Start-ScheduledTask -TaskName $name   日志：$root\outputs\logs\daily_review\"
