# connect.ps1 (Windows) — One-shot: start Dulus Bar + open Dulus already wired.
# Usage:
#   .\connect.ps1
#   .\connect.ps1 "fix the webhook"
#   .\connect.ps1 -IslandOnly
#   .\connect.ps1 -DulusOnly
#   .\connect.ps1 -DulusPath "C:\path\to\dulus.py"

param(
    [string]$DulusPath = "",
    [switch]$IslandOnly,
    [switch]$DulusOnly,
    [switch]$NoNewWindow,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$DulusArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Info($msg) { Write-Host "[dulusbar] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[dulusbar] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[dulusbar] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[dulusbar] $msg" -ForegroundColor Red }

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Err "python is not on PATH."
    exit 1
}

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

function Test-IslandTcp {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", 17372, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(400)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close()
    } catch {}
    return $false
}

function Test-IslandWs {
    # Real websocket handshake — TCP open != server alive (stale exe bug)
    $helper = Join-Path $Root "wrappers\_ws_health.py"
    if (-not (Test-Path $helper)) { return (Test-IslandTcp) }
    & python $helper 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Stop-StaleBar {
    Get-Process DulusBar -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Warn ("Killing stuck Dulus Bar PID=" + $_.Id)
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 600
}

function Start-Bar {
    if (Test-IslandWs) {
        Write-Ok "Dulus Bar already running on ws://127.0.0.1:17372"
        return
    }
    if (Test-IslandTcp) {
        Write-Warn "Port 17372 open but websocket dead — restarting..."
        Stop-StaleBar
    }

    Write-Info "Starting Dulus Bar (fresh source, not the stale exe)..."
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }
    Start-Process -FilePath "python" -ArgumentList "-m","dulus_bar" -WorkingDirectory $Root | Out-Null

    for ($i = 0; $i -lt 50; $i++) {
        Start-Sleep -Milliseconds 200
        if (Test-IslandWs) {
            Write-Ok "Dulus Bar up, websocket ALIVE (ws://127.0.0.1:17372)"
            return
        }
    }
    Write-Warn "Dulus Bar did not answer the websocket in time. Check that PyQt6 opened the bar."
}

function Save-DulusPathFile([string]$PathValue) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText((Join-Path $Root "dulus_path.txt"), ($PathValue + "`n"), $utf8)
    $cfgDir = Join-Path $env:USERPROFILE ".dulus"
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir | Out-Null }
    [System.IO.File]::WriteAllText((Join-Path $cfgDir "dulus_bar_dulus_path.txt"), ($PathValue + "`n"), $utf8)
}

function Resolve-Dulus {
    if ($DulusPath) {
        $p = $DulusPath
        if (Test-Path $p -PathType Container) { $p = Join-Path $p "dulus.py" }
        if (-not (Test-Path $p)) { throw "DulusPath does not exist: $DulusPath" }
        Save-DulusPathFile $p
        Write-Info "Dulus via flag: $p"
        return $p
    }

    $helper = Join-Path $Root "wrappers\_resolve_dulus.py"
    $out = & python $helper 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Could not resolve dulus.py"
        Write-Host $out
        Write-Host ""
        Write-Host 'Tip: .\connect.ps1 -DulusPath "C:\path\to\dulus.py"'
        exit 1
    }
    $lines = @($out | ForEach-Object { "$_" } | Where-Object { $_ -ne $null -and "$_" -ne "" })
    Write-Info ("Dulus detected via " + $lines[0])
    if ($lines.Count -ge 2) { return $lines[1] }
    return $null
}

if (-not $DulusOnly) {
    Start-Bar
}

if ($IslandOnly) {
    Write-Ok "Done. Dulus Bar running. For Dulus: .\connect.ps1 -DulusOnly"
    exit 0
}

$null = Resolve-Dulus
$wrapper = Join-Path $Root "wrappers\dulus_wrapper.py"
$argList = New-Object System.Collections.Generic.List[string]
$argList.Add($wrapper) | Out-Null
if ($DulusPath) {
    $argList.Add("--dulus") | Out-Null
    $argList.Add($DulusPath) | Out-Null
}
if ($DulusArgs) {
    foreach ($a in $DulusArgs) { $argList.Add($a) | Out-Null }
}

Write-Info "Launching Dulus wired to the bar..."
Write-Host ("  python " + ($argList -join " ")) -ForegroundColor DarkGray

if ($NoNewWindow) {
    & python @($argList.ToArray())
    exit $LASTEXITCODE
}

Start-Process -FilePath "python" -ArgumentList $argList.ToArray() -WorkingDirectory $Root
Write-Ok "Dulus opened in another window + bar on top."
