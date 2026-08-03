@echo off
setlocal
REM ============================================================
REM   Dulus Bar — double-click to start the floating island.
REM   No terminal needed. Uses pythonw so no console lingers.
REM ============================================================
cd /d "%~dp0"

REM Kill a stale bar that may be holding the websocket port.
taskkill /F /IM DulusBar.exe >nul 2>&1

set "PYTHONPATH=%~dp0;%PYTHONPATH%"

REM Prefer pythonw (windowed, no console). Fall back to minimized python.
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw -m dulus_bar
) else (
  where python >nul 2>&1 || (echo [X] Python not found on PATH & pause & exit /b 1)
  start "Dulus Bar" /min python -m dulus_bar
)

exit /b 0
