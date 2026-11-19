#!/usr/bin/env sh
# Create .env from .env.example with random secrets, ready for
# `docker compose up` (macOS / Linux; on Windows use scripts/init-env.ps1).
#
# Fills every empty value of .env.example: the Postgres user, password and
# database, DATABASE_URL to match, a random JWT secret and internal token, and
# the first admin (admin@example.com with a random password, printed once at
# the end). Never overwrites an existing .env.
set -eu

cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists; nothing changed. Delete it first to start over."
  exit 0
fi

secret() { # $1 = number of random bytes, printed as hex
  head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
}

pg_user=app
pg_password=$(secret 16)
pg_db=apim
admin_password=$(secret 12)

# Keep every line and comment of the example; fill only the empty KEY= lines.
sed \
  -e "s|^POSTGRES_USER=\$|POSTGRES_USER=$pg_user|" \
  -e "s|^POSTGRES_PASSWORD=\$|POSTGRES_PASSWORD=$pg_password|" \
  -e "s|^POSTGRES_DB=\$|POSTGRES_DB=$pg_db|" \
  -e "s|^DATABASE_URL=\$|DATABASE_URL=postgresql+asyncpg://$pg_user:$pg_password@postgres:5432/$pg_db|" \
  -e "s|^JWT_SECRET=\$|JWT_SECRET=$(secret 32)|" \
  -e "s|^INTERNAL_TOKEN=\$|INTERNAL_TOKEN=$(secret 24)|" \
  -e "s|^ADMIN_EMAIL=\$|ADMIN_EMAIL=admin@example.com|" \
  -e "s|^ADMIN_PASSWORD=\$|ADMIN_PASSWORD=$admin_password|" \
  .env.example > .env

echo "Created .env with random secrets."
echo "Admin login for the API and Swagger (also in .env):"
echo "  email:    admin@example.com"
echo "  password: $admin_password"
echo "Next: docker compose up -d --build --wait"
