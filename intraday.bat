@echo off
title INTRADAY
set "DIR=C:\Users\admin\Documents\deepseek-harness\default-workspace\qopt"
set "PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PYTHONPATH=%DIR%\.pylibs"
set "PYTHONIOENCODING=utf-8"
set "USERPROFILE=%DIR%\fakehome"
set "APPDATA=%DIR%\fakehome"

echo [INTRADAY] IBKR realtime option monitor
echo [INTRADAY] needs IB Gateway on port 4001
echo.

cd /d "%DIR%"
"%PY%" run\intraday_monitor.py %*
