<#
.SYNOPSIS
    Run every quality check of the control plane: lint, format, types, tests.

.DESCRIPTION
    Runs inside the running control-plane container (start it first with
    `docker compose up -d`). Stops at the first failing check.

.PARAMETER Fix
    Let ruff fix what it can, reformat the code, and refresh openapi.json
    before checking.

.EXAMPLE
    .\backend\control-plane\scripts\check.ps1
    .\backend\control-plane\scripts\check.ps1 -Fix
#>
param([switch]$Fix)

# Work from the repo root, wherever the script is called from.
Set-Location (Resolve-Path "$PSScriptRoot\..\..\..")

function Invoke-Check([string]$Name, [string[]]$Command) {
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    docker compose exec -T control-plane @Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if ($Fix) {
    # Format first (it fixes long lines), then let the linter fix the rest.
    Invoke-Check "ruff format" @("ruff", "format", ".")
    Invoke-Check "ruff fix"    @("ruff", "check", "--fix", ".")
    Invoke-Check "openapi"     @("python", "scripts/export_openapi.py")
}

Invoke-Check "ruff lint"   @("ruff", "check", ".")
Invoke-Check "ruff format" @("ruff", "format", "--check", ".")
Invoke-Check "mypy"        @("mypy", "app")
Invoke-Check "pytest"      @("pytest", "-p", "no:cacheprovider", "--cov", "--cov-report=term-missing", "--cov-fail-under=90")

Write-Host "`nAll checks passed" -ForegroundColor Green
