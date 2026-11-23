#!/usr/bin/env sh
# Create .env from .env.example with random secrets, ready for
# `docker compose up` (macOS / Linux; on Windows use scripts/init-env.ps1).
#
# Fills every empty value of .env.example: the Postgres user, password and
# database, the four database URLs (owner, API, audit retention, workspace
# purge) with their
# passwords, the internal token, GoTrue's database
# password, JWT secret and service-role key, and the first Platform Admin
# (admin@example.com with a random password, printed once at the end).
# Never overwrites an existing .env. Needs openssl (for the service-role key).
set -eu

cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists; nothing changed. Delete it first to start over."
  exit 0
fi

secret() { # $1 = number of random bytes, printed as hex
  head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
}

b64url() { # stdin -> base64url without padding, as JWTs use it
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

# A JWT with role "service_role", signed with the GoTrue secret (HS256): the
# key to GoTrue's admin API. Valid for 10 years; rotate it with the secret.
service_role_key() { # $1 = GoTrue JWT secret
  now=$(date +%s)
  header=$(printf '%s' '{"alg":"HS256","typ":"JWT"}' | b64url)
  payload=$(printf '{"role":"service_role","iss":"apim","iat":%s,"exp":%s}' \
    "$now" "$((now + 315360000))" | b64url)
  signature=$(printf '%s' "$header.$payload" | openssl dgst -sha256 -hmac "$1" -binary | b64url)
  printf '%s' "$header.$payload.$signature"
}

pg_user=app
pg_password=$(secret 16)
pg_db=apim
gotrue_jwt_secret=$(secret 32)
admin_password=$(secret 12)

# Keep every line and comment of the example; fill only the empty KEY= lines.
sed \
  -e "s|^POSTGRES_USER=\$|POSTGRES_USER=$pg_user|" \
  -e "s|^POSTGRES_PASSWORD=\$|POSTGRES_PASSWORD=$pg_password|" \
  -e "s|^POSTGRES_DB=\$|POSTGRES_DB=$pg_db|" \
  -e "s|^GOTRUE_DB_PASSWORD=\$|GOTRUE_DB_PASSWORD=$(secret 16)|" \
  -e "s|^GOTRUE_JWT_SECRET=\$|GOTRUE_JWT_SECRET=$gotrue_jwt_secret|" \
  -e "s|^GOTRUE_SERVICE_ROLE_KEY=\$|GOTRUE_SERVICE_ROLE_KEY=$(service_role_key "$gotrue_jwt_secret")|" \
  -e "s|^MIGRATION_DATABASE_URL=\$|MIGRATION_DATABASE_URL=postgresql+asyncpg://$pg_user:$pg_password@postgres:5432/$pg_db|" \
  -e "s|^DATABASE_URL=\$|DATABASE_URL=postgresql+asyncpg://apim_app:$(secret 16)@postgres:5432/$pg_db|" \
  -e "s|^AUDIT_RETENTION_DATABASE_URL=\$|AUDIT_RETENTION_DATABASE_URL=postgresql+asyncpg://apim_retention:$(secret 16)@postgres:5432/$pg_db|" \
  -e "s|^PURGE_DATABASE_URL=\$|PURGE_DATABASE_URL=postgresql+asyncpg://apim_purge:$(secret 16)@postgres:5432/$pg_db|" \
  -e "s|^INGEST_DATABASE_URL=\$|INGEST_DATABASE_URL=postgresql+asyncpg://apim_ingest:$(secret 16)@postgres:5432/$pg_db|" \
  -e "s|^INTERNAL_TOKEN=\$|INTERNAL_TOKEN=$(secret 24)|" \
  -e "s|^PLATFORM_ADMIN_EMAIL=\$|PLATFORM_ADMIN_EMAIL=admin@example.com|" \
  -e "s|^PLATFORM_ADMIN_PASSWORD=\$|PLATFORM_ADMIN_PASSWORD=$admin_password|" \
  .env.example > .env

echo "Created .env with random secrets."
echo "Platform Admin login (GoTrue; also in .env):"
echo "  email:    admin@example.com"
echo "  password: $admin_password"
echo "Next: docker compose up -d --build --wait"
