<#
.SYNOPSIS
    Restore a backup made by scripts\backup.ps1 into a fresh stack.

.DESCRIPTION
    Only into a stack whose database volume is new: the script stops if the
    platform's database already has tables, so it can never overwrite data.
    To replace the data of this folder's stack, delete it on purpose first
    (`docker compose down -v`), or restore into another Compose project
    (-ProjectName) while this one is stopped.

    1. Starts Postgres and makes the logins and the GoTrue databases
       (auth-db-init, prepare_database), with the passwords in .env.
    2. Restores the three databases, owners and grants included.
    3. Gives the config a new generation (PLAN.md section 5): a gateway that
       ran against another copy of the database then takes a full config
       instead of trusting its old sequence numbers.
    4. Runs migrations (a backup from an older version is brought up to
       date) and starts the whole stack.

.PARAMETER From
    The backup folder (with apim.dump, auth.dump, auth_portal.dump).

.PARAMETER ProjectName
    The Compose project to restore into (default: this folder's).

.EXAMPLE
    docker compose down -v      # deletes this stack's data: only when that is what you want
    .\scripts\restore.ps1 -From backups\20261123-120000
#>
param(
    [Parameter(Mandatory = $true)][string]$From,
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

function Invoke-Sql([string]$Sql) {
    # The SQL goes in on stdin: PowerShell 5.1 passes quotes inside an
    # argument on broken.
    $ErrorActionPreference = "Continue"
    $out = $Sql | & docker @compose exec -T postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 -tA' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $Sql`n$out" }
    return ($out | Out-String).Trim()
}

foreach ($db in $Databases) {
    if (-not (Test-Path (Join-Path $From "$db.dump"))) { throw "$From has no $db.dump" }
}

Write-Host "1/4 Postgres, logins and the GoTrue databases"
Invoke-Compose @("up", "-d", "--wait", "postgres")
$tables = Invoke-Sql "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"
if ($tables -ne "0") {
    throw "This stack's database already has $tables tables: a restore goes into a fresh stack only. " +
        "Delete this stack's data on purpose first (docker compose down -v), or use -ProjectName."
}
Invoke-Compose @("run", "--rm", "auth-db-init")
Invoke-Compose @("run", "--rm", "--no-deps", "control-plane-db-init", "python", "-m", "scripts.prepare_database")

Write-Host "2/4 Restoring $($Databases -join ', ')"
foreach ($db in $Databases) {
    $name = if ($db -eq "apim") { '$POSTGRES_DB' } else { $db }
    Invoke-Compose @("cp", (Join-Path $From "$db.dump"), "postgres:/tmp/$db.dump")
    # --clean --if-exists: replaces what the setup made empty (GoTrue's `auth` schema).
    Invoke-Compose @("exec", "-T", "postgres", "sh", "-c",
        "pg_restore -U `$POSTGRES_USER -d $name --clean --if-exists --exit-on-error /tmp/$db.dump && rm /tmp/$db.dump")
}

Write-Host "3/4 A new config generation"
$generation = Invoke-Sql "UPDATE config_seq SET generation = gen_random_uuid() RETURNING generation;"
Write-Host "  generation $generation"

Write-Host "4/4 Migrations and the whole stack"
Invoke-Compose @("up", "-d", "--build", "--wait")
Write-Host "Restored from $From." -ForegroundColor Green
