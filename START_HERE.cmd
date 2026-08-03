@echo off
setlocal EnableExtensions
title Dulus Bar + Dulus
cd /d "%~dp0"

echo.
echo  ============================================
echo   DULUS BAR  +  DULUS   one-click
echo  ============================================
echo.

REM 1) kill frozen bar exe (holds port, dead websocket)
taskkill /F /IM DulusBar.exe >nul 2>&1

REM 2) ensure python
where python >nul 2>&1
if errorlevel 1 (
  echo [X] python is not on PATH
  pause
  exit /b 1
)

REM 3) PYTHONPATH so -m dulus_bar works even without pip install
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

REM 4) start the bar from SOURCE (not stale exe)
echo [1/3] Starting Dulus Bar...
start "DulusBar" /MIN python -m dulus_bar

REM 5) wait until websocket is actually alive
echo [2/3] Waiting for live websocket at 127.0.0.1:17372 ...
set /a tries=0
:wait_ws
set /a tries+=1
python "%~dp0wrappers\_ws_health.py" >nul 2>&1
if not errorlevel 1 goto ws_ok
if %tries% GEQ 40 goto ws_fail
timeout /t 0 /nobreak >nul
ping -n 1 127.0.0.1 >nul
goto wait_ws

:ws_fail
echo [!] Bar did not answer in time. Launching Dulus anyway...
goto launch_dulus

:ws_ok
echo [OK] Dulus Bar websocket ALIVE

:launch_dulus
echo [3/3] Launching Dulus wired to the bar...
echo.
python "%~dp0wrappers\dulus_wrapper.py" %*
set rc=%ERRORLEVEL%
echo.
echo Dulus exited with code %rc%
echo.
pause
exit /b %rc%
