@echo off
REM Just the bar. For bar + Dulus: connect.cmd / START_HERE.cmd
cd /d "%~dp0"
REM Kill stale frozen exe (port open but websocket dead)
taskkill /F /IM DulusBar.exe >nul 2>&1
set PYTHONPATH=%~dp0;%PYTHONPATH%
python -m dulus_bar %*
