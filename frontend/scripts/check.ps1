<#
.SYNOPSIS
    Run every quality check of the frontend: format, lint, types, API types,
    tests with coverage, production build.

.DESCRIPTION
    Runs on Windows with your local Node (like the gateway's checks with
    local Go): the tests need no running stack, they fake GoTrue and the
    API. Installs the dependencies first when node_modules is missing.
    Stops at the first failing check. Line coverage must be at least 90%,
    the bar of the two backend services. CI runs the same checks.

.PARAMETER Fix
    Reformat the code and regenerate the API types from openapi.json first.

.EXAMPLE
    .\frontend\scripts\check.ps1
    .\frontend\scripts\check.ps1 -Fix
#>
param([switch]$Fix)

# Work from the frontend folder, wherever the script is called from.
Set-Location (Resolve-Path "$PSScriptRoot\..")

function Invoke-Check([string]$Name, [scriptblock]$Command) {
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path node_modules)) {
    Invoke-Check "npm ci" { npm ci --no-audit --no-fund }
}

if ($Fix) {
    Invoke-Check "prettier fix" { npx prettier --write . --log-level warn }
    Invoke-Check "api types"    { npm run --silent api:types }
}

Invoke-Check "prettier" { npx prettier --check . --log-level warn }
Invoke-Check "eslint"   { npx eslint . --max-warnings 0 }
Invoke-Check "tsc"      { npx tsc -b }
Invoke-Check "api types match openapi.json" {
    # The types are generated from the committed API contract; a stale copy
    # would let the app compile against an API that no longer exists.
    $fresh = Join-Path ([IO.Path]::GetTempPath()) "apim-schema.d.ts"
    npx openapi-typescript ../backend/control-plane/openapi.json -o $fresh --silent --default-non-nullable false
    if ((Get-Content $fresh -Raw) -ne (Get-Content src/api/schema.d.ts -Raw)) {
        Write-Host "src/api/schema.d.ts is out of date (run with -Fix)" -ForegroundColor Yellow
        $global:LASTEXITCODE = 1
    }
}
Invoke-Check "vitest (coverage)" { npx vitest run --coverage }
Invoke-Check "vite build"        { npx vite build --logLevel warn }

Write-Host "`nAll checks passed" -ForegroundColor Green
