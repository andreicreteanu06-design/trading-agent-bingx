<#
.SYNOPSIS
    Porneste agentul la fiecare logon, prin folderul Startup din Windows.
    NU are nevoie de Administrator.

.DESCRIPTION
    Alternativa la RegisterAutostart.ps1 (Task Scheduler), si in practica cea
    care functioneaza.

    De ce exista amandoua: varianta cu Task Scheduler s-a dovedit fragila pe
    aceasta masina. Task-ul raporta "Running", pornea un powershell.exe, iar
    acela nu lansa niciun proces copil si nu scria niciun log. Cauzele gasite pe
    rand au fost LogonType S4U (fara sesiune interactiva), RunLevel Highest
    (token elevat, izolat de sesiunea normala) si un WorkingDirectory gol.
    Acelasi RunAlways.ps1, rulat direct in sesiunea utilizatorului, pornea toate
    cele trei procese instant, de fiecare data.

    Folderul Startup ruleaza exact in acea sesiune: acelasi utilizator, acelasi
    token, acelasi mediu. Fara elevare, fara servicii, fara token-uri separate -
    deci fara niciuna dintre problemele de mai sus.

    Compromisul fata de Task Scheduler: nu reporneste automat daca lansatorul
    insusi e omorat, si nu ruleaza cand PC-ul e pornit fara ca tu sa te loghezi.
    Pentru un dashboard personal si un logger care aduna date cat lucrezi, e un
    compromis bun - mai ales fata de alternativa care nu porneste deloc.

.USAGE
    .\InstallStartup.ps1              # instaleaza
    .\InstallStartup.ps1 -Remove      # dezinstaleaza
    .\InstallStartup.ps1 -StartNow    # instaleaza SI porneste imediat
#>

param(
    [switch]$Remove,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LauncherPath = Join-Path $ScriptDir "RunAlways.ps1"
$StartupDir   = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "BingX Agent Dashboard.lnk"

if ($Remove) {
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "[OK] Scos din Startup: $ShortcutPath"
    } else {
        Write-Host "Nu era instalat nimic in Startup."
    }
    exit 0
}

if (-not (Test-Path $LauncherPath)) {
    Write-Error "Nu gasesc lansatorul: $LauncherPath"
    exit 1
}

# -WindowStyle Hidden pe shortcut ca sa nu apara o fereastra neagra la logon.
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($ShortcutPath)
$lnk.TargetPath       = "powershell.exe"
$lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""
$lnk.WorkingDirectory = $ScriptDir
$lnk.WindowStyle      = 7          # minimizat
$lnk.Description      = "Porneste dashboard-ul BingX si loggerul de pozitionare"
$lnk.Save()

Write-Host "[OK] Instalat in Startup."
Write-Host "  Shortcut : $ShortcutPath"
Write-Host "  Porneste : la fiecare logon, automat"
Write-Host ""
Write-Host "  Verifica dupa pornire cu:"
Write-Host "    Get-Content `"$ScriptDir\logs\launcher.log`" -Tail 8"
Write-Host ""
Write-Host "  Ca sa dezinstalezi:  .\InstallStartup.ps1 -Remove"

if ($StartNow) {
    Write-Host ""
    Write-Host "Pornesc acum..."
    Start-Process powershell.exe `
        -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File","`"$LauncherPath`"" `
        -WorkingDirectory $ScriptDir `
        -WindowStyle Hidden
    Write-Host "Pornit. Verifica logs\launcher.log in cateva secunde."
}
