-- Prepares GoTrue's own database on the shared Postgres server (MT.1).
-- Run by the one-shot `auth-db-init` service before GoTrue starts, on fresh
-- and on existing volumes alike, so every statement is safe to repeat.
-- GoTrue then creates its tables itself (its own migrations, not Alembic).
--
-- psql variables:  auth_password = GOTRUE_DB_PASSWORD, apim_db = POSTGRES_DB (from .env)

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

-- Each database only for its own login (and the superuser): GoTrue can't
-- open the platform's database, and nobody else can open GoTrue's.
REVOKE CONNECT ON DATABASE auth FROM PUBLIC;
REVOKE CONNECT ON DATABASE :"apim_db" FROM PUBLIC;

\connect auth
-- GoTrue's migrations create their tables in this schema but don't create it.
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION gotrue;
