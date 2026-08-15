<#
.SYNOPSIS
    Always-on launcher for BingX Trading Agent dashboard.
    Starts Python API (loopback-only) + Next.js production server (0.0.0.0)
    + the open-interest/funding logger (tools/oi_logger.py).
    Logs to files, restarts crashed processes, runs hidden from Task Scheduler.

.DESCRIPTION
    This script is designed to be launched by Windows Task Scheduler at user logon.
    It keeps all three processes alive and writes stdout/stderr to rotating log files.

    The OI logger is included here, not run separately, because it only earns
    its keep if it never stops: a gap of a few days is a few days of data that
    can never be recovered. Piggybacking on the launcher that's already
    registered for autostart means it survives reboots without a second thing
    to remember to start.

    Security: Python API binds ONLY to 127.0.0.1 (never exposed).
    Next.js binds to 0.0.0.0:3000 — accessible via Tailscale only.
    No port-forwarding on router. Tailscale provides the private network.

.REQUIREMENTS
    - Python environment with agent dependencies installed
    - `npm run build` already run in web/ (production build)
    - Tailscale installed and logged in on this PC and target phone

.LOGS
    - logs\python-api.log  : Python API stdout/stderr
    - logs\nextjs.log      : Next.js server stdout/stderr
    - logs\oi-logger.log   : Open-interest/funding logger stdout/stderr
    - logs\launcher.log    : This launcher's events

.EXIT CODES
    0 = Clean shutdown (Ctrl+C or Task Scheduler stop)
    1 = Unhandled exception
#>

param(
    [int]$PythonPort = 8420,
    [int]$NextPort   = 3000,
    [string]$ApiHost = "127.0.0.1",
    [string]$NextHost = "0.0.0.0",
    [int]$RestartDelaySec = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LogDir = Join-Path $ScriptDir "logs"
$PythonLog = Join-Path $LogDir "python-api.log"
$NextLog   = Join-Path $LogDir "nextjs.log"
$OiLoggerLog = Join-Path $LogDir "oi-logger.log"
$LauncherLog = Join-Path $LogDir "launcher.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $Msg" | Tee-Object -FilePath $LauncherLog -Append
}

function Start-ProcessWithLogging {
    param(
        [string]$Name,
        [string]$Exe,
        [string]$Args,
        [string]$WorkingDir,
        [string]$LogFile,
        [ref]$ProcRef
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = $Args
    $psi.WorkingDirectory = $WorkingDir
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8

    $proc = [System.Diagnostics.Process]::Start($psi)
    $ProcRef.Value = $proc

    $outputReader = {
        param($stream, $prefix)
        while (-not $stream.EndOfStream) {
            $line = $stream.ReadLine()
            if ($line) {
                $ts = Get-Date -Format "HH:mm:ss"
                "$ts [$prefix] $line" | Out-File -FilePath $LogFile -Append -Encoding utf8
            }
        }
    }

    $stdoutJob = Start-Job -ScriptBlock $outputReader -ArgumentList $proc.StandardOutput, "OUT"
    $stderrJob = Start-Job -ScriptBlock $outputReader -ArgumentList $proc.StandardError,  "ERR"

    $proc.Exited.Register({
        param($sender, $e)
        Write-Log "$Name exited with code $($sender.ExitCode). Restarting in $RestartDelaySec sec..."
        Start-Sleep -Seconds $RestartDelaySec
        # restart logic handled by outer loop
    })

    return $proc
}

Write-Log "=== LAUNCHER STARTED ==="
Write-Log "Working dir: $ScriptDir"
Write-Log "Python: ${ApiHost}:$PythonPort | Next.js: ${NextHost}:$NextPort"

$pythonProc = $null
$nextProc   = $null
$oiLoggerProc = $null
$running = $true

# ---- Clean shutdown on Ctrl+C or Task Scheduler termination ----
$shutdown = {
    Write-Log "Shutdown signal received. Stopping processes..."
    $running = $false
    if ($pythonProc -and -not $pythonProc.HasExited)   { $pythonProc.Kill() }
    if ($nextProc   -and -not $nextProc.HasExited)     { $nextProc.Kill() }
    if ($oiLoggerProc -and -not $oiLoggerProc.HasExited) { $oiLoggerProc.Kill() }
    Write-Log "=== LAUNCHER STOPPED ==="
    exit 0
}

[System.Console]::CancelKeyPress.Add($shutdown)
Register-EngineEvent -SourceIdentifier "PowerShell.Exiting" -Action $shutdown | Out-Null

# ---- Main supervision loop ----
while ($running) {
    # --- Python API ---
    if (-not $pythonProc -or $pythonProc.HasExited) {
        Write-Log "Starting Python API on ${ApiHost}:$PythonPort..."
        $pythonProc = Start-ProcessWithLogging -Name "PythonAPI" `
            -Exe "python" -Args "-m app.server --host $ApiHost --port $PythonPort --no-browser" `
            -WorkingDir $ScriptDir -LogFile $PythonLog -ProcRef ([ref]$pythonProc)
    }

    # --- Next.js production server ---
    if (-not $nextProc -or $nextProc.HasExited) {
        Write-Log "Starting Next.js on ${NextHost}:$NextPort..."
        $nextProc = Start-ProcessWithLogging -Name "NextJS" `
            -Exe "npx" -Args "next start -H $NextHost -p $NextPort" `
            -WorkingDir (Join-Path $ScriptDir "web") -LogFile $NextLog -ProcRef ([ref]$nextProc)
    }

    # --- Open-interest / funding logger ---
    # Reads once an hour internally (its own --loop), so a 10s check here just
    # confirms the process itself is still alive, not that it just fetched.
    if (-not $oiLoggerProc -or $oiLoggerProc.HasExited) {
        Write-Log "Starting OI/funding logger..."
        $oiLoggerProc = Start-ProcessWithLogging -Name "OiLogger" `
            -Exe "python" -Args "tools\oi_logger.py --loop" `
            -WorkingDir $ScriptDir -LogFile $OiLoggerLog -ProcRef ([ref]$oiLoggerProc)
    }

    # Wait a bit before checking again
    Start-Sleep -Seconds 10
}

Write-Log "=== LAUNCHER STOPPED ==="