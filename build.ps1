# Build the DSM Jobs PWA on Windows.
# Run from anywhere:  powershell -File .\build.ps1
# Or from the repo folder:  .\build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$py = $null
foreach ($cmd in @("python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        if ($cmd -eq "py") { $py = @("py", "-3") } else { $py = @("python") }
        break
    }
}
if (-not $py) {
    Write-Error "Python not found. Install Python 3.11+ and ensure 'python' is on PATH."
}

Write-Host "==> Mock scan (writes app\public\jobs.json)..." -ForegroundColor Cyan
& @py find_admin_jobs.py --mock
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm not found. Install Node.js 22+ from https://nodejs.org/"
}

Write-Host "==> Astro build -> web\..." -ForegroundColor Cyan
Push-Location app
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Preview with:" -ForegroundColor Green
Write-Host "  python -m http.server 8137 --directory web --bind 127.0.0.1"
Write-Host "  http://127.0.0.1:8137/dsm-jobs/"
