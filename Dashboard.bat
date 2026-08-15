@echo off
REM Dublu-click pe acest fisier porneste dashboard-ul si deschide browserul.
REM Pentru acces de pe telefon (acelasi WiFi), adauga: --host 0.0.0.0

cd /d "%~dp0"
python -m app.server %*
if errorlevel 1 (
    echo.
    echo Ceva n-a mers. Verifica mesajul de mai sus.
    pause
)
