param(
    [string]$Time = "08:00"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$TaskName = "EmailAuto Morning Brief"

if (-not (Test-Path $Python)) {
    throw "Virtual environment Python not found at $Python"
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m emailauto run --label morning-brief" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Generate the EmailAuto morning brief." `
    -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName' at $Time"
