<#
.SYNOPSIS
    Back up the platform's three databases from the running stack.

.DESCRIPTION
    Dumps `apim` (the platform: config, audit log, request log, developers)
    and both GoTrue databases (`auth`: members' accounts; `auth_portal`:
    developers' accounts) with pg_dump's custom format, into a new folder
    under backups\ (ignored by git). Restore them with scripts\restore.ps1.

    The dumps hold everything but the secrets in .env: keep a copy of .env
    with the backup, somewhere safe. A restore needs the same
    PORTAL_GOTRUE_JWT_SECRET and GOTRUE_JWT_SECRET only for tokens that were
    already issued; logins work with any secrets.

.PARAMETER Out
    The folder to write to (default: backups\<date-time>).

.PARAMETER ProjectName
    The Compose project to back up (default: this folder's).

.EXAMPLE
    .\scripts\backup.ps1
#>
param(
    [string]$Out = "",
    [string]$ProjectName = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..")

$Databases = @("apim", "auth", "auth_portal")
$compose = @("compose")
if ($ProjectName) { $compose += @("-p", $ProjectName) }

function Invoke-Compose([string[]]$Arguments) {
    # Docker writes progress to stderr, which Windows PowerShell would take
    # for an error: the exit code decides instead.
    $ErrorActionPreference = "Continue"
    & docker @compose @Arguments 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "docker $($compose + $Arguments -join ' ') failed ($LASTEXITCODE)" }
}

if (-not $Out) { $Out = Join-Path "backups" (Get-Date -Format "yyyyMMdd-HHmmss") }
New-Item -ItemType Directory -Force $Out | Out-Null

foreach ($db in $Databases) {
    # `apim` is POSTGRES_DB; pg_dump runs inside the container as the owner,
    # and the file is copied out (PowerShell 5.1 would mangle a binary pipe).
    # No quotes inside the command: PowerShell 5.1 passes them on broken.
    $name = if ($db -eq "apim") { '$POSTGRES_DB' } else { $db }
    Invoke-Compose @("exec", "-T", "postgres", "sh", "-c",
        "pg_dump -U `$POSTGRES_USER -d $name -Fc -f /tmp/$db.dump")
    Invoke-Compose @("cp", "postgres:/tmp/$db.dump", (Join-Path $Out "$db.dump"))
    Invoke-Compose @("exec", "-T", "postgres", "rm", "/tmp/$db.dump")
    $size = (Get-Item (Join-Path $Out "$db.dump")).Length
    Write-Host ("  {0,-12} {1,10:N0} bytes" -f $db, $size)
}

Write-Host "Backed up to $Out. Keep a copy of .env with it (it holds the secrets)." -ForegroundColor Green
