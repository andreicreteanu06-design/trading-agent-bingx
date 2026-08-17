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
$LiqLoggerLog = Join-Path $LogDir "liq-logger.log"
$DepthLoggerLog = Join-Path $LogDir "depth-logger.log"
$PaperExecLog = Join-Path $LogDir "paper-executor.log"
$LauncherLog = Join-Path $LogDir "launcher.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    <#
        Writes to the log file only — deliberately NOT via Tee-Object.

        Tee-Object also emits to the success stream, which needs somewhere to
        go. Task Scheduler runs this with S4U logon and no console attached, so
        that write throws, and with $ErrorActionPreference = "Stop" it killed
        the launcher on its very first log line. The symptom was maddening: the
        task reported "Running", yet no child process and no log file ever
        appeared. Add-Content has no such dependency.
    #>
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try {
        Add-Content -Path $LauncherLog -Value "$ts  $Msg" -Encoding UTF8 -ErrorAction Stop
    } catch {
        # Logging must never be the thing that takes the supervisor down.
    }
}

function Start-ProcessWithLogging {
    <#
        Starts a child process with stdout/stderr redirected straight to files.

        Note on the approach: an earlier version read the streams manually and
        pumped them into Start-Job background jobs. That cannot work — Process
        stream objects are not serializable across runspaces, so the jobs fail
        the moment they receive them. Letting the OS write the files directly is
        both simpler and more reliable, and it survives this script crashing.

        Log files are truncated when a process restarts. That is intentional:
        the restart history lives in launcher.log, and these files are meant to
        show the CURRENT process's output, not an ever-growing archive. The OI
        logger's actual data goes to logs\positioning.jsonl and is unaffected.

        Pass Node tools as "npx.cmd", not "npx". On Windows `npx` resolves to
        npx.ps1, a PowerShell script, and Start-Process refuses it with
        "%1 is not a valid Win32 application" — the .cmd shim is a real
        executable and is what actually launches.
    #>
    param(
        [string]$Name,
        [string]$Exe,
        # NOT named $Args: that collides with PowerShell's automatic $args
        # variable, which under Set-StrictMode -Version Latest throws and takes
        # the whole launcher down before any child process starts.
        [string]$ArgLine,
        [string]$WorkingDir,
        [string]$LogFile
    )

    $errFile = [System.IO.Path]::ChangeExtension($LogFile, ".err.log")

    $proc = Start-Process -FilePath $Exe `
        -ArgumentList $ArgLine `
        -WorkingDirectory $WorkingDir `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError $errFile `
        -WindowStyle Hidden `
        -PassThru

    return $proc
}

# ---- Single-instance guard ----
#
# Doua instante simultane pornesc doua seturi de procese copil pe aceleasi
# porturi: a doua nu poate lega portul, moare, reporneste, e omorata de
# supervizorul primeia la urmatorul ciclu de 10s - Next.js repornind la
# fiecare ~20s. S-a intamplat deja o data in productie. Un mutex global e
# vizibil intre orice doua procese Windows, spre deosebire de un fisier PID
# care poate ramane stale dupa un crash.
$mutexCreated = $false
$singleInstanceMutex = New-Object System.Threading.Mutex($true, "Global\BingXTradingAgent_RunAlways", [ref]$mutexCreated)
if (-not $mutexCreated) {
    Write-Log "O alta instanta a launcherului ruleaza deja - ies fara sa pornesc nimic."
    exit 0
}

# ---- Reap orphans from a previous launcher ----
#
# The mutex above guarantees we are the only launcher running. Therefore any
# project child process alive right now is an orphan: it belonged to a launcher
# that died without running its finally block — which is exactly what
# Stop-Process -Force does. Left alone, they hold ports (Next.js EADDRINUSE) and
# duplicate work (two OI loggers writing the same JSONL). Observed in practice.
$orphanPatterns = 'app\.server|oi_logger|liq_logger|depth_logger|paper_executor|next start'
$orphans = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -match $orphanPatterns
}
foreach ($o in $orphans) {
    Write-Log "Reaping orphan PID $($o.ProcessId) from a previous launcher"
    try { Stop-Process -Id $o.ProcessId -Force -ErrorAction Stop } catch { }
}
if ($orphans) { Start-Sleep -Seconds 3 }  # lasa porturile sa se elibereze

Write-Log "=== LAUNCHER STARTED ==="
Write-Log "Working dir: $ScriptDir"
Write-Log "Python: ${ApiHost}:$PythonPort | Next.js: ${NextHost}:$NextPort"

$pythonProc = $null
$nextProc   = $null
$oiLoggerProc = $null
$liqLoggerProc = $null
$depthLoggerProc = $null
$paperExecProc = $null
$running = $true

# ---- Clean shutdown ----
#
# Note: an earlier version called [System.Console]::CancelKeyPress.Add(...).
# That throws — CancelKeyPress is an event, not a property, and PowerShell
# cannot subscribe to it with .Add(). It killed the launcher before a single
# process was started, which is why nothing ever ran. A try/finally around the
# supervision loop covers every exit path (Ctrl+C, Task Scheduler stop, error)
# without needing to hook console events at all.
function Stop-Children {
    Write-Log "Stopping child processes..."
    foreach ($p in @($pythonProc, $nextProc, $oiLoggerProc, $liqLoggerProc, $depthLoggerProc, $paperExecProc)) {
        if ($p -and -not $p.HasExited) {
            try { $p.Kill() } catch { }
        }
    }
}

# ---- Main supervision loop ----
try {
while ($running) {
    # --- Python API ---
    if (-not $pythonProc -or $pythonProc.HasExited) {
        Write-Log "Starting Python API on ${ApiHost}:$PythonPort..."
        $pythonProc = Start-ProcessWithLogging -Name "PythonAPI" `
            -Exe "python" -ArgLine "-m app.server --host $ApiHost --port $PythonPort --no-browser" `
            -WorkingDir $ScriptDir -LogFile $PythonLog
    }

    # --- Next.js production server ---
    if (-not $nextProc -or $nextProc.HasExited) {
        Write-Log "Starting Next.js on ${NextHost}:$NextPort..."
        $nextProc = Start-ProcessWithLogging -Name "NextJS" `
            -Exe "npx.cmd" -ArgLine "next start -H $NextHost -p $NextPort" `
            -WorkingDir (Join-Path $ScriptDir "web") -LogFile $NextLog
    }

    # --- Open-interest / funding logger ---
    # Reads once an hour internally (its own --loop), so a 10s check here just
    # confirms the process itself is still alive, not that it just fetched.
    if (-not $oiLoggerProc -or $oiLoggerProc.HasExited) {
        Write-Log "Starting OI/funding logger..."
        $oiLoggerProc = Start-ProcessWithLogging -Name "OiLogger" `
            -Exe "python" -ArgLine "tools\oi_logger.py --loop" `
            -WorkingDir $ScriptDir -LogFile $OiLoggerLog
    }

    # --- Forced-liquidation logger ---
    # Same reasoning as the OI logger: liquidation history cannot be bought,
    # only recorded, so it has to survive reboots without anyone remembering it.
    # This one holds a WebSocket open rather than polling, so a dropped process
    # is a hard gap in the stream — the 10s liveness check matters more here.
    if (-not $liqLoggerProc -or $liqLoggerProc.HasExited) {
        Write-Log "Starting liquidation logger..."
        $liqLoggerProc = Start-ProcessWithLogging -Name "LiqLogger" `
            -Exe "python" -ArgLine "tools\liq_logger.py" `
            -WorkingDir $ScriptDir -LogFile $LiqLoggerLog
    }

    # --- Order book depth logger (BTC/ETH/SOL, for scalping edge research) ---
    # Same reasoning as the liquidation logger: depth history cannot be bought,
    # only recorded, so it has to survive reboots without anyone remembering it.
    if (-not $depthLoggerProc -or $depthLoggerProc.HasExited) {
        Write-Log "Starting depth logger..."
        $depthLoggerProc = Start-ProcessWithLogging -Name "DepthLogger" `
            -Exe "python" -ArgLine "tools\depth_logger.py" `
            -WorkingDir $ScriptDir -LogFile $DepthLoggerLog
    }

    # --- Paper trading executor (no real orders — see execution/paper_executor.py) ---
    # Assumes logs/paper_state.json already exists (bootstrapped manually with
    # --capital once); --loop alone won't create it. If state is ever reset,
    # someone has to run --capital by hand once before this picks it back up.
    if (-not $paperExecProc -or $paperExecProc.HasExited) {
        Write-Log "Starting paper executor..."
        $paperExecProc = Start-ProcessWithLogging -Name "PaperExecutor" `
            -Exe "python" -ArgLine "execution\paper_executor.py --loop" `
            -WorkingDir $ScriptDir -LogFile $PaperExecLog
    }

    # Wait a bit before checking again
    Start-Sleep -Seconds 10
}
}
finally {
    Stop-Children
    if ($singleInstanceMutex) {
        try { $singleInstanceMutex.ReleaseMutex() } catch { }
        $singleInstanceMutex.Dispose()
    }
    Write-Log "=== LAUNCHER STOPPED ==="
}