# Removes the daily task. Your repositories, logs and state are not touched.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
$TaskName = "SolutionSync Daily"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed task '$TaskName'."
} else {
    Write-Output "Task '$TaskName' is not installed."
}
