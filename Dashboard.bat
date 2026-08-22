@echo off
REM ============================================================
REM  Porneste dashboard-ul agentului. Dublu-click si atat.
REM
REM  Optiuni (se pot adauga la linia de comanda sau intr-un shortcut):
REM    Dashboard.bat --host 0.0.0.0   acces si de pe telefon (acelasi WiFi)
REM    Dashboard.bat --no-claude      fara analiza LLM
REM    Dashboard.bat --port 9000      alt port
REM ============================================================

cd /d "%~dp0"
title Dashboard Agent BingX

REM --- exista Python?
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python nu este instalat sau nu este in PATH.
    echo   Descarca-l de la https://python.org si bifeaza
    echo   "Add Python to PATH" la instalare.
    echo.
    pause
    exit /b 1
)

REM --- sunt instalate dependintele? Verificam una reprezentativa.
python -c "import ccxt, pandas, dotenv" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Lipsesc cateva pachete. Le instalez acum, dureaza un minut...
    echo.
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Instalarea a esuat. Ruleaza manual:
        echo       python -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)

REM --- pornim. app/server.py deschide singur browserul.
python -m app.server %*

REM --- daca a picat, lasam fereastra deschisa ca sa se vada de ce
if errorlevel 1 (
    echo.
    echo   ============================================================
    echo   Dashboard-ul s-a oprit cu eroare. Mesajul este mai sus.
    echo.
    echo   Cauze frecvente:
    echo     - portul 8420 e deja folosit ^(alta instanta ruleaza deja?^)
    echo     - lipsesc cheile din .env ^(agentul merge si fara, pe date publice^)
    echo   ============================================================
    echo.
    pause
)
