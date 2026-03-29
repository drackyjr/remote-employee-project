-- TBAPS Migration: Add Activation Code System Columns
-- Run once against the active PostgreSQL database.
-- Safe to re-run — all statements use IF NOT EXISTS / DO NOTHING guards.
-- Generated: 2026-03-29

BEGIN;

-- ── Activation code system ─────────────────────────────────────────────────────

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS activation_code_hash        TEXT          DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS activation_code_expires_at  TIMESTAMPTZ   DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS activation_status           TEXT          NOT NULL DEFAULT 'pending_activation',
  ADD COLUMN IF NOT EXISTS activated_at                TIMESTAMPTZ   DEFAULT NULL;

-- ── Constraint: only valid states allowed ──────────────────────────────────────

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'employees_activation_status_check'
  ) THEN
    ALTER TABLE employees
      ADD CONSTRAINT employees_activation_status_check
        CHECK (activation_status IN ('pending_activation', 'activated'));
  END IF;
END;
$$;

-- ── Index for fast lookups on activation queries ───────────────────────────────

CREATE INDEX IF NOT EXISTS idx_employees_activation_status
  ON employees(activation_status)
  WHERE deleted_at IS NULL;

-- ── Backfill: existing employees that already have kbt_token_hash keep pending ─
-- (No rows updated — new default covers all existing rows as 'pending_activation')
-- Admins may manually set existing employees to 'activated' if already live:
--   UPDATE employees SET activation_status = 'activated', activated_at = NOW()
--   WHERE kbt_token_hash IS NOT NULL AND deleted_at IS NULL;

COMMIT;
