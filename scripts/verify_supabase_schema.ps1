# Read-only portal schema checks - no supabase login required.
# Loads .env from repo root, plus DSM_JOBS_SUPABASE_ENV_FILE when explicitly set.
# See portal/README.md for the canonical portal runbook.

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "Repo: $RepoRoot"
Write-Host "Project ref: tcclohxvhmwgjrtdkkuw"
Write-Host "Running schema verification (Management API or PostgREST; no auth.supabase.io)..."

python (Join-Path $PSScriptRoot "verify_supabase_schema.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
