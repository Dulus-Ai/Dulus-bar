# setup.ps1 (Windows) — install deps + drop ready-to-use aliases.
# Usage:  .\setup.ps1
#         .\setup.ps1 -DulusPath "C:\path\to\dulus.py"

param(
    [string]$DulusPath = "",
    [switch]$SkipProfile,
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Info($msg) { Write-Host "[setup] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[setup] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[setup] $msg" -ForegroundColor Yellow }

Write-Info "Dulus Bar root: $Root"

# 1) deps
if (-not $SkipDeps) {
    Write-Info "Installing dependencies..."
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    # editable install so `python -m dulus_bar` / `dulusbar` always work
    python -m pip install -e . 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "pip install -e . failed — that's fine, connect/launch use PYTHONPATH"
    } else {
        Write-Ok "editable install OK (command: dulusbar)"
    }
    Write-Ok "deps OK"
}

# 2) remember dulus path (only if explicitly provided — otherwise auto-detected)
if ($DulusPath) {
    if (Test-Path $DulusPath -PathType Container) {
        $DulusPath = Join-Path $DulusPath "dulus.py"
    }
    if (-not (Test-Path $DulusPath)) {
        Write-Warn "DulusPath does not exist: $DulusPath (not saving)"
    } else {
        # utf8 NoBOM — the Python resolver chokes on a BOM
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText((Join-Path $Root "dulus_path.txt"), ($DulusPath + "`n"), $utf8)
        $cfgDir = Join-Path $env:USERPROFILE ".dulus"
        if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir | Out-Null }
        [System.IO.File]::WriteAllText((Join-Path $cfgDir "dulus_bar_dulus_path.txt"), ($DulusPath + "`n"), $utf8)
        Write-Ok "Dulus path saved: $DulusPath"
    }
} else {
    Write-Info "No -DulusPath given — the wrapper will auto-detect dulus.py at connect time."
}

# 3) PowerShell profile aliases
if (-not $SkipProfile) {
    $profilePath = $PROFILE
    $profileDir = Split-Path -Parent $profilePath
    if (-not (Test-Path $profileDir)) {
        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    }
    if (-not (Test-Path $profilePath)) {
        New-Item -ItemType File -Path $profilePath -Force | Out-Null
    }

    $block = @"

# >>> dulus-bar (auto) >>>
function dulusbar {
    param([Parameter(ValueFromRemainingArguments=`$true)][string[]]`$Args)
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\connect.ps1" @Args
}
function dulusbar-only {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\connect.ps1" -IslandOnly
}
function dulus-connect {
    param([Parameter(ValueFromRemainingArguments=`$true)][string[]]`$Args)
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\connect.ps1" -DulusOnly @Args
}
# <<< dulus-bar (auto) <<<
"@

    $existing = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue
    if ($existing -and $existing -match "dulus-bar \(auto\)") {
        $new = [regex]::Replace($existing, "(?s)# >>> dulus-bar \(auto\) >>>.*?# <<< dulus-bar \(auto\) <<<", $block.Trim())
        Set-Content -Path $profilePath -Value $new -Encoding UTF8
        Write-Ok "Aliases updated in `$PROFILE"
    } else {
        # Clean any legacy vibe-island block, then add the new one
        if ($existing -and $existing -match "vibe-island \(auto\)") {
            $existing = [regex]::Replace($existing, "(?s)# >>> vibe-island \(auto\) >>>.*?# <<< vibe-island \(auto\) <<<", "")
            Set-Content -Path $profilePath -Value $existing -Encoding UTF8
        }
        Add-Content -Path $profilePath -Value $block -Encoding UTF8
        Write-Ok "Aliases added to `$PROFILE"
    }
    Write-Info "Aliases: dulusbar · dulusbar-only · dulus-connect"
    Write-Info "Open a NEW terminal (or: . `$PROFILE) to use them."
}

Write-Host ""
Write-Ok "Done. From now on:"
Write-Host "  dulusbar               # bar + Dulus" -ForegroundColor White
Write-Host "  dulusbar-only          # just the bar" -ForegroundColor White
Write-Host "  dulus-connect [args]   # just Dulus wired to the bar" -ForegroundColor White
Write-Host "  .\connect.cmd          # double-click, no terminal" -ForegroundColor White
Write-Host ""
