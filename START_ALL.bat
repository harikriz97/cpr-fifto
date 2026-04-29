@echo off
chcp 65001 >nul
title CPR Strategy - Full Launch

echo Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8765 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
timeout /t 3 /nobreak >nul

echo [1/3] Starting OpenAlgo Server...
start "OpenAlgo Server" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\openalgo && python app.py"
timeout /t 6 /nobreak >nul

echo [2/3] Starting Trader (Paper Mode)...
start "CPR Trader" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\Desktop\fifto^ cpr^ selling && set PYTHONIOENCODING=utf-8 && python trader.py"
timeout /t 15 /nobreak >nul

echo [3/3] Starting Dashboard (port 8080)...
start "CPR Dashboard" cmd /k "chcp 65001 >nul && cd /d C:\Users\WELCOME\Desktop\fifto^ cpr^ selling && set PYTHONIOENCODING=utf-8 && python dashboard_server.py"

echo.
echo ============================================
echo  Dashboard : http://localhost:8080
echo  OpenAlgo  : http://localhost:5000
echo ============================================
echo.
start "" "http://localhost:8080"
pause
