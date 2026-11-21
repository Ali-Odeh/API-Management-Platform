<#
.SYNOPSIS
    Run every quality check of the control plane: lint, format, types, tests.

.DESCRIPTION
    Runs inside Docker (start the stack with `docker compose up -d`). The
    database tests use a separate container with the test setup credentials.
    Stops at the first failing check.

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
    docker compose @Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

$api = @("exec", "-T", "control-plane")
$tests = @("run", "--rm", "--no-deps", "-T", "control-plane-db-init")

if ($Fix) {
    # Format first (it fixes long lines), then let the linter fix the rest.
    Invoke-Check "ruff format" ($api + @("ruff", "format", "."))
    Invoke-Check "ruff fix"    ($api + @("ruff", "check", "--fix", "."))
    Invoke-Check "openapi"     ($api + @("python", "scripts/export_openapi.py"))
}

Invoke-Check "ruff lint"   ($api + @("ruff", "check", "."))
Invoke-Check "ruff format" ($api + @("ruff", "format", "--check", "."))
Invoke-Check "mypy"        ($api + @("mypy", "app"))
Invoke-Check "pytest"      ($tests + @("pytest", "-p", "no:cacheprovider", "--cov", "--cov-report=term-missing", "--cov-fail-under=90"))

Write-Host "`nAll checks passed" -ForegroundColor Green
