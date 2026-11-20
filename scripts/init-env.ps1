<#
.SYNOPSIS
    Create .env from .env.example with random secrets, ready for `docker compose up`.

.DESCRIPTION
    Fills every empty value of .env.example: the Postgres user, password and
    database, DATABASE_URL to match, the internal token, GoTrue's database
    password, JWT secret and service-role key, and the first Platform Admin
    (admin@example.com with a random password, printed once at the end).
    Never overwrites an existing .env.

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

function ConvertTo-Base64Url([byte[]]$Bytes) {
    # Base64 without padding, with - and _ instead of + and /: how JWTs encode.
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-ServiceRoleKey([string]$Secret) {
    # A JWT with role "service_role", signed with the GoTrue secret (HS256): the
    # key to GoTrue's admin API. Valid for 10 years; rotate it with the secret.
    $utf8 = [Text.Encoding]::UTF8
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $header = ConvertTo-Base64Url $utf8.GetBytes('{"alg":"HS256","typ":"JWT"}')
    $payload = ConvertTo-Base64Url $utf8.GetBytes(
        "{""role"":""service_role"",""iss"":""apim"",""iat"":$now,""exp"":$($now + 315360000)}")
    $hmac = New-Object System.Security.Cryptography.HMACSHA256 (, $utf8.GetBytes($Secret))
    $signature = ConvertTo-Base64Url $hmac.ComputeHash($utf8.GetBytes("$header.$payload"))
    $hmac.Dispose()
    return "$header.$payload.$signature"
}

$pgUser = "app"
$pgPassword = New-Secret 16
$pgDb = "apim"
$gotrueJwtSecret = New-Secret 32
$adminPassword = New-Secret 12
$values = @{
    POSTGRES_USER           = $pgUser
    POSTGRES_PASSWORD       = $pgPassword
    POSTGRES_DB             = $pgDb
    GOTRUE_DB_PASSWORD      = New-Secret 16
    GOTRUE_JWT_SECRET       = $gotrueJwtSecret
    GOTRUE_SERVICE_ROLE_KEY = New-ServiceRoleKey $gotrueJwtSecret
    DATABASE_URL            = "postgresql+asyncpg://${pgUser}:${pgPassword}@postgres:5432/${pgDb}"
    INTERNAL_TOKEN          = New-Secret 24
    PLATFORM_ADMIN_EMAIL    = "admin@example.com"
    PLATFORM_ADMIN_PASSWORD = $adminPassword
    JWT_SECRET              = New-Secret 32
    ADMIN_EMAIL             = "admin@example.com"
    ADMIN_PASSWORD          = $adminPassword
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
Write-Host "Platform Admin login (GoTrue; also in .env):"
Write-Host "  email:    admin@example.com"
Write-Host "  password: $adminPassword"
Write-Host "Next: docker compose up -d --build --wait"
