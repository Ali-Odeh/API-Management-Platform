<#
.SYNOPSIS
    Run every quality check of the gateway: format, vet, tests.

.DESCRIPTION
    Runs on Windows with your local Go (the gateway has no database, so it
    needs no container). Stops at the first failing check. CI runs the same
    checks, plus the race detector, which needs a C compiler that Windows
    does not have by default.

.PARAMETER Fix
    Reformat the code with gofmt before checking.

.EXAMPLE
    .\backend\gateway\scripts\check.ps1
    .\backend\gateway\scripts\check.ps1 -Fix
#>
param([switch]$Fix)

# Work from the gateway folder, wherever the script is called from.
Set-Location (Resolve-Path "$PSScriptRoot\..")

function Invoke-Check([string]$Name, [scriptblock]$Command) {
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if ($Fix) {
    Invoke-Check "gofmt fix" { gofmt -w . }
}

Invoke-Check "gofmt" {
    $unformatted = gofmt -l .
    if ($unformatted) {
        Write-Host "Not formatted (run with -Fix):" -ForegroundColor Yellow
        $unformatted | ForEach-Object { Write-Host "  $_" }
        $global:LASTEXITCODE = 1
    }
}
Invoke-Check "go vet"  { go vet ./... }
Invoke-Check "go test" { go test -count=1 -cover ./... }

Write-Host "`nAll checks passed" -ForegroundColor Green
