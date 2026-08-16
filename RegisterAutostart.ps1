<#
.SYNOPSIS
    Registers RunAlways.ps1 to run at user logon via Windows Task Scheduler.
    Run this ONCE, from an elevated (Admin) PowerShell, after verifying the launcher works.

.USAGE
    1. Test the launcher first:
       .\RunAlways.ps1
       # Verify both servers start, dashboard loads at http://127.0.0.1:3000
       # Ctrl+C to stop

    2. Then run THIS script as Administrator:
       Start-Process powershell -Verb RunAs -ArgumentList "-File RegisterAutostart.ps1"

.NOTES
    - Task runs whether user is logged on or not, with highest privileges (for binding 0.0.0.0).
    - Uses the current user's account; password is NOT stored (uses S4U logon).
    - To unregister later: Unregister-ScheduledTask -TaskName "BingXAgentDashboard" -Confirm:$false
#>

param(
    [string]$TaskName = "BingXAgentDashboard",
    [string]$LauncherScript = "RunAlways.ps1"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LauncherPath = Join-Path $ScriptDir $LauncherScript

if (-not (Test-Path $LauncherPath)) {
    Write-Error "Launcher script not found: $LauncherPath"
    exit 1
}

# Requires elevation for "Run whether user is logged on or not"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "This script must run as Administrator. Re-launching..."
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Definition)`""
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -RandomDelay "00:02:00"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

# LogonType Interactive, nu S4U.
#
# S4U ("Service For User") ruleaza fara sesiune interactiva si fara credentiale
# de retea. Testat aici: task-ul pornea powershell.exe, procesul chiar traia,
# dar nu lansa niciun proces copil si nu scria niciun log - esec complet tacut,
# in timp ce Task Scheduler raporta senin "Running".
#
# Interactive ruleaza in sesiunea utilizatorului logat. Compromisul: porneste
# doar dupa ce te loghezi, nu si cand PC-ul e pornit fara utilizator. Pentru un
# dashboard personal si un logger care aduna date cat lucrezi, e exact ce
# trebuie - si, spre deosebire de S4U, chiar functioneaza.
$principal = New-ScheduledTaskPrincipal -UserId (whoami) -LogonType Interactive -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "BingX Trading Agent: Python API + Next.js dashboard (production). Runs at logon, restarts on crash. Access via Tailscale." `
        -Force
    Write-Host "[OK] Task '$TaskName' registered successfully."
    Write-Host "  Runs at user logon (up to 2 min random delay), hidden window."
    Write-Host "  To test now: Start-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  To view: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
    Write-Host "  To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
}
catch {
    Write-Error "Failed to register task: $_"
    exit 1
}