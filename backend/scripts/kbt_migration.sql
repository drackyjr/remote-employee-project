-- KBT Executable Schema Migration
-- Adds KBT token columns to the employees table.
-- Run once against your PostgreSQL database.
--
-- Usage:
--   psql $DATABASE_URL -f scripts/kbt_migration.sql

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS kbt_token_hash       TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS kbt_token_expires_at TIMESTAMPTZ DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS kbt_generated_at     TIMESTAMPTZ DEFAULT NULL;

-- Index speeds up auto-login lookup (employee_id is already PK, so no index needed)
-- but we add one on kbt_token_hash for fast revocation checks if needed
CREATE INDEX IF NOT EXISTS idx_employees_kbt_token_hash
  ON employees(kbt_token_hash)
  WHERE kbt_token_hash IS NOT NULL;

COMMENT ON COLUMN employees.kbt_token_hash IS
  'SHA-256 hex digest of the raw KBT token embedded in the employee executable. NULL = no KBT provisioned or token revoked.';
COMMENT ON COLUMN employees.kbt_token_expires_at IS
  'Optional expiry timestamp for the KBT token. NULL = never expires.';
COMMENT ON COLUMN employees.kbt_generated_at IS
  'Audit: when the current KBT token was last generated.';
