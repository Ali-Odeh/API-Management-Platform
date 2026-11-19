<#
.SYNOPSIS
    Run every quality check of the gateway: format, vet, tests, coverage.

.DESCRIPTION
    Runs on Windows with your local Go. The rate-limit tests need Redis:
    they use Docker's Redis (database 15, so real counters are untouched),
    so start the stack first with `docker compose up -d`. Set REDIS_TEST_URL
    to use another Redis. Stops at the first failing check. Total coverage
    must be at least 90%, the same bar as the control plane. CI runs the
    same checks, plus the race detector (it needs a C compiler that Windows
    does not have by default) and a short run of every fuzz test.

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

if (-not $env:REDIS_TEST_URL) {
    $env:REDIS_TEST_URL = "redis://localhost:6379/15"
}
Write-Host "Redis for the rate-limit tests: $env:REDIS_TEST_URL"

$MinCoverage = 90
$coverFile = Join-Path ([IO.Path]::GetTempPath()) "gateway-cover.out"

Invoke-Check "go vet"  { go vet ./... }
Invoke-Check "go test" { go test -count=1 -cover "-coverprofile=$coverFile" ./... }
Invoke-Check "coverage >= $MinCoverage%" {
    # The last line of `go tool cover -func` is "total: (statements) 97.5%".
    $total = [double]((go tool cover "-func=$coverFile" | Select-Object -Last 1) -replace '.*\s([\d.]+)%$', '$1')
    Write-Host "total coverage: $total%"
    if ($total -lt $MinCoverage) {
        Write-Host "Coverage $total% is below $MinCoverage%" -ForegroundColor Yellow
        $global:LASTEXITCODE = 1
    }
}

Write-Host "`nAll checks passed" -ForegroundColor Green
