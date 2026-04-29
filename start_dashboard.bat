@echo off
title CPR Strategy - Dashboard
echo Starting Dashboard...
cd /d "C:\Users\WELCOME\Desktop\fifto cpr selling"
streamlit run dashboard.py --server.port 8501 --server.headless false
pause
