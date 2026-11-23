-- Prepares the two GoTrue databases on the shared Postgres server: `auth` for
-- workspace members (MT.1) and `auth_portal` for developers (P.1).
-- Run by the one-shot `auth-db-init` service before GoTrue starts, on fresh
-- and on existing volumes alike, so every statement is safe to repeat.
-- GoTrue then creates its tables itself (its own migrations, not Alembic).
--
-- psql variables (from .env): auth_password = GOTRUE_DB_PASSWORD,
-- portal_password = PORTAL_GOTRUE_DB_PASSWORD, apim_db = POSTGRES_DB

-- GoTrue's migrations grant SELECT to a role named `postgres` (Supabase's
-- superuser). Our superuser has another name, so the role must exist; it
-- can't log in.
SELECT 'CREATE ROLE postgres NOLOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres')\gexec

-- GoTrue's own login: owns the `auth` database and nothing else, so it can't
-- read or change the platform's `apim` database.
SELECT 'CREATE ROLE gotrue LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gotrue')\gexec
-- Set on every run, so the password always matches .env.
SELECT format('ALTER ROLE gotrue PASSWORD %L', :'auth_password')\gexec
ALTER ROLE gotrue SET search_path = auth;

SELECT 'CREATE DATABASE auth OWNER gotrue'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'auth')\gexec

-- The developers' GoTrue (P.1) gets a login and a database of its own:
-- open sign-up there can never reach the members' accounts, and neither
-- GoTrue can open the other's database.
SELECT 'CREATE ROLE gotrue_portal LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gotrue_portal')\gexec
SELECT format('ALTER ROLE gotrue_portal PASSWORD %L', :'portal_password')\gexec
ALTER ROLE gotrue_portal SET search_path = auth;

SELECT 'CREATE DATABASE auth_portal OWNER gotrue_portal'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'auth_portal')\gexec

-- Each database only for its own login (and the superuser): GoTrue can't
-- open the platform's database, and nobody else can open GoTrue's.
REVOKE CONNECT ON DATABASE auth FROM PUBLIC;
REVOKE CONNECT ON DATABASE auth_portal FROM PUBLIC;
REVOKE CONNECT ON DATABASE :"apim_db" FROM PUBLIC;

\connect auth
-- GoTrue's migrations create their tables in this schema but don't create it.
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION gotrue;

\connect auth_portal
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION gotrue_portal;
