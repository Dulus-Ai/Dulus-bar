@echo off
setlocal
REM One-click (Windows): Dulus Bar + Dulus wired together.
REM Double-click, or: connect.cmd [dulus args...]
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect.ps1" %*
exit /b %ERRORLEVEL%
