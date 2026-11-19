# 注册 Windows 每日定时任务：每个交易日收盘后自动运行 A 股每日策略。
#
# 用法（在 PowerShell 里执行一次即可）：
#     cd D:\trading
#     .\register_task.ps1
#
# 之后每天 16:00 自动运行：python runner.py --daily
# 手动试跑： python runner.py --daily
# 删除任务： schtasks /Delete /TN AShareDailyStrategy /F
$ErrorActionPreference = "Stop"

$TaskName = "AShareDailyStrategy"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Runner = Join-Path $ProjectDir "runner.py"

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Runner`" --daily" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "16:00"

# StartWhenAvailable：错过调度时间（如关机）后尽快补跑；IgnoreNew：上一轮未结束则不重复启动
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "A股每日策略：新闻/时政 + 全市场选股 + 模拟盘" `
    -Force | Out-Null

Write-Host "已注册定时任务 [$TaskName]，每天 16:00 运行：python $Runner --daily"
Write-Host "手动试跑： python runner.py --daily"
Write-Host "删除任务： schtasks /Delete /TN $TaskName /F"
