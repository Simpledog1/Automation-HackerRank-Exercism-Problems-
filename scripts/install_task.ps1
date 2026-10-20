# Registers (or updates) the daily "SolutionSync Daily" task for the current user.
# Safe to run again: it replaces the existing task instead of creating a duplicate.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 [-Time 06:00]
param([string]$Time = "06:00")
$ErrorActionPreference = "Stop"

$TaskName = "SolutionSync Daily"
$Root = Split-Path -Parent $PSScriptRoot

# A stable interpreter path (not a temporary terminal setting). pythonw = no console window.
$Python = & py -3 -c "import sys; print(sys.executable)"
$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (Test-Path $PythonW) { $Python = $PythonW }
if (-not (Test-Path $Python)) { throw "Python was not found." }

# Task Scheduler uses this PC's clock. The app counts quota in Manila time on its own,
# but the start time only means "Manila time" if the PC is on UTC+08:00.
$tz = Get-TimeZone
if ($tz.BaseUtcOffset -ne [TimeSpan]::FromHours(8) -or $tz.SupportsDaylightSavingTime) {
    Write-Warning "PC time zone is $($tz.Id); $Time will be PC time, not Manila time."
}

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m solsync run" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# Runs as you, only while you are signed in, without admin rights. That is what lets it
# use your saved Git and Exercism credentials. No password is stored in the task.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description "Uploads up to 2 completed HackerRank/Exercism solutions per day. See $Root\README.md" | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Task:        $($t.TaskName) ($($t.State))"
Write-Output "Runs:        $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
Write-Output "In folder:   $($t.Actions[0].WorkingDirectory)"
Write-Output "Daily at:    $Time (PC time zone $($tz.Id))"
Write-Output "Next run:    $($info.NextRunTime)"
Write-Output "Catch-up:    $($t.Settings.StartWhenAvailable)  Overlap: $($t.Settings.MultipleInstances)"
Write-Output "Run level:   $($t.Principal.RunLevel)  Logon: $($t.Principal.LogonType)"
