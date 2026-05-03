@echo off
chcp 65001 >nul
title FIFTO CPR Strategy v2 - Full Launch

echo Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8765 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
timeout /t 3 /nobreak >nul

echo.
echo [1/3] Starting OpenAlgo Server...
start "OpenAlgo Server" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\openalgo && python app.py"
timeout /t 6 /nobreak >nul

echo     Opening OpenAlgo in browser...
start "" "http://127.0.0.1:5000"
echo.
echo  *** LOGIN to OpenAlgo (official.fifto@gmail.com / Hari9788@)
echo  *** Connect Angel One broker inside OpenAlgo
echo  *** Press any key AFTER login + broker connected...
echo.
pause >nul

echo.
echo [2/3] Starting Trader (THOR + HULK + IRON MAN + CAPTAIN)...
start "CPR Trader" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\Desktop\fifto^ cpr^ selling && set PYTHONIOENCODING=utf-8 && python live\paper_trader.py"
timeout /t 15 /nobreak >nul

echo [3/3] Starting Dashboard (port 8080)...
start "CPR Dashboard" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\Desktop\fifto^ cpr^ selling && set PYTHONIOENCODING=utf-8 && python dashboard_server.py"
timeout /t 5 /nobreak >nul

echo     Opening Dashboard in browser...
start "" "http://localhost:8080"

echo.
echo ============================================
echo  Trader   : THOR / HULK / IRON MAN / CAPTAIN
echo  Dashboard : http://localhost:8080
echo  OpenAlgo  : http://localhost:5000
echo ============================================
echo.
