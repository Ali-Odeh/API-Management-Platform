<#
.SYNOPSIS
    Create .env from .env.example with random secrets, ready for `docker compose up`.

.DESCRIPTION
    Fills every empty value of .env.example: the Postgres user, password and
    database, DATABASE_URL to match, a random JWT secret and internal token,
    and the first admin (admin@example.com with a random password, printed
    once at the end). Never overwrites an existing .env.

.EXAMPLE
    .\scripts\init-env.ps1
#>

# Work from the repo root, wherever the script is called from.
Set-Location (Resolve-Path "$PSScriptRoot\..")

if (Test-Path .env) {
    Write-Host ".env already exists; nothing changed. Delete it first to start over." -ForegroundColor Yellow
    exit 0
}

function New-Secret([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buffer)
    $rng.Dispose()
    return -join ($buffer | ForEach-Object { $_.ToString("x2") })
}

$pgUser = "app"
$pgPassword = New-Secret 16
$pgDb = "apim"
$adminPassword = New-Secret 12
$values = @{
    POSTGRES_USER     = $pgUser
    POSTGRES_PASSWORD = $pgPassword
    POSTGRES_DB       = $pgDb
    DATABASE_URL      = "postgresql+asyncpg://${pgUser}:${pgPassword}@postgres:5432/${pgDb}"
    JWT_SECRET        = New-Secret 32
    INTERNAL_TOKEN    = New-Secret 24
    ADMIN_EMAIL       = "admin@example.com"
    ADMIN_PASSWORD    = $adminPassword
}

# Keep every line and comment of the example; fill only empty KEY= lines.
$lines = Get-Content .env.example | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=$' -and $values.ContainsKey($Matches[1])) {
        "$($Matches[1])=$($values[$Matches[1]])"
    } else {
        $_
    }
}

# UTF-8 without BOM and LF line endings: Docker Compose reads them as is.
[IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding $false))

Write-Host "Created .env with random secrets." -ForegroundColor Green
Write-Host "Admin login for the API and Swagger (also in .env):"
Write-Host "  email:    admin@example.com"
Write-Host "  password: $adminPassword"
Write-Host "Next: docker compose up -d --build --wait"
