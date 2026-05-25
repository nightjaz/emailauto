param(
    [int]$Limit = 5
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

& $Python -m emailauto scan --dry-run --limit $Limit
