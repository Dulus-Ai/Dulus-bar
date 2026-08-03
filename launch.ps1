# launch.ps1 (Windows) — just the bar (floating island).
# For bar + Dulus in one shot: .\connect.ps1
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

# Kill stale frozen exe that holds the port but won't handshake
Get-Process DulusBar -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

# Always run from source — dist\DulusBar.exe gets stale after code changes
python -m dulus_bar @args
