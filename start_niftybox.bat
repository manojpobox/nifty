@echo off
title NIFTYBOX Terminal - Localhost 8080
cd /d C:\Nifty
echo ==============================================================
echo                 STARTING NIFTYBOX TERMINAL
echo ==============================================================
echo Dashboard URL: http://127.0.0.1:8080
echo Connected with Fyers API (XM05617)
echo.

start http://127.0.0.1:8080
python server.py
pause
