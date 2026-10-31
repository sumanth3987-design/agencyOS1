-- =====================================================================
-- Proposed migration: payment uniqueness (Stage 6F)
-- Target table: payment
-- Status: PROPOSAL ONLY. Do not execute without separate approval.
-- Database engines covered: PostgreSQL and SQLite.
-- Other database engines require an adapted procedure.
--
-- IMPORTANT:
--   * This file is not a universal cross-engine script. PostgreSQL and
--     SQLite have different procedural SQL, transaction, and locking
--     behavior. Run only the section for the target engine.
--   * This migration is not idempotent with respect to incorrectly named
--     or incorrectly defined existing indexes. Index definitions must be
--     inspected before treating an index as present (see preflight).
--   * CREATE ... IF NOT EXISTS checks the index NAME only. It will not
--     replace an existing index that has the wrong definition.
--   * No application database has been modified by this proposal.
--   * This SQL has not been executed or validated against PostgreSQL or
--     SQLite in this repository.
--   * PostgreSQL execution and validation remain outstanding.
-- =====================================================================

-- =====================================================================
-- 1. Common preflight inspection (run before any data change)
-- =====================================================================

-- 1a. Duplicate non-NULL razorpay_order_id values.
-- Expected result: zero rows.
SELECT razorpay_order_id, COUNT(*) AS duplicate_count
FROM payment
WHERE razorpay_order_id IS NOT NULL
GROUP BY razorpay_order_id
HAVING COUNT(*) > 1;

-- 1b. Duplicate non-NULL razorpay_payment_id values.
-- Expected result: zero rows.
SELECT razorpay_payment_id, COUNT(*) AS duplicate_count
FROM payment
WHERE razorpay_payment_id IS NOT NULL
GROUP BY razorpay_payment_id
HAVING COUNT(*) > 1;

-- 1c. Multiple Created payments for the same (project_id, customer_id).
-- Expected result: zero rows.
SELECT project_id, customer_id, COUNT(*) AS duplicate_count
FROM payment
WHERE status = 'Created'
GROUP BY project_id, customer_id
HAVING COUNT(*) > 1;

-- 1d. Empty-string payment IDs that will be normalized to NULL.
-- These rows are not a hard conflict by themselves, but they are reported
-- because their meaning changes from empty string to NULL during migration.
SELECT id, project_id, customer_id, razorpay_order_id
FROM payment
WHERE razorpay_payment_id = '';

-- 1e. Empty-string order IDs. These cannot be normalized to NULL because
-- razorpay_order_id is NOT NULL and unique. Any row returned here is a
-- hard blocker and must be repaired manually before migration.
-- Expected result: zero rows.
SELECT id, project_id, customer_id, razorpay_order_id
FROM payment
WHERE razorpay_order_id = '';

-- 1f. Existing payment indexes and their definitions.
-- PostgreSQL:
--   SELECT schemaname, tablename, indexname, indexdef
--   FROM pg_indexes
--   WHERE tablename = 'payment'
--   ORDER BY indexname;
--
-- SQLite:
--   SELECT name, sql
--   FROM sqlite_master
--   WHERE type = 'index'
--     AND tbl_name = 'payment'
--   ORDER BY name;
--   PRAGMA index_list('payment');

-- 1g. Count empty-string payment IDs before normalization.
-- Run this before the UPDATE in section 3 or 4 and record the result as
-- empty_payment_ids_before_normalization.
SELECT COUNT(*) AS empty_payment_ids_before_normalization
FROM payment
WHERE razorpay_payment_id = '';

-- =====================================================================
-- 2. Intended data-changing behavior
-- =====================================================================
-- An empty string is not a real Razorpay payment ID. Before creating the
-- partial unique index, normalize empty strings to NULL. After this change,
-- multiple rows may have NULL razorpay_payment_id; the partial index
-- intentionally allows multiple NULL values.
--
-- This UPDATE is the only data-changing statement in this proposal.

-- =====================================================================
-- 3. PostgreSQL procedure (transactional DDL)
-- =====================================================================
-- Run with psql:
--   \set ON_ERROR_STOP on
--   \i MIGRATION_PAYMENT_UNIQUENESS.sql   -- select only this section
--
-- PostgreSQL DDL is transactional. If any statement fails, the transaction
-- is aborted and must be rolled back. Do not assume SQLite has identical
-- transactional DDL behavior.
--
-- BEGIN;
-- SET LOCAL lock_timeout = '5s';
--
-- -- Guard: stop immediately if any preflight conflict exists.
-- DO $$
-- BEGIN
--     IF EXISTS (
--         SELECT 1
--         FROM payment
--         WHERE razorpay_order_id IS NOT NULL
--         GROUP BY razorpay_order_id
--         HAVING COUNT(*) > 1
--     ) THEN
--         RAISE EXCEPTION 'Preflight failed: duplicate non-NULL razorpay_order_id values exist in payment.';
--     END IF;
--
--     IF EXISTS (
--         SELECT 1
--         FROM payment
--         WHERE razorpay_payment_id IS NOT NULL
--         GROUP BY razorpay_payment_id
--         HAVING COUNT(*) > 1
--     ) THEN
--         RAISE EXCEPTION 'Preflight failed: duplicate non-NULL razorpay_payment_id values exist in payment.';
--     END IF;
--
--     IF EXISTS (
--         SELECT 1
--         FROM payment
--         WHERE status = 'Created'
--         GROUP BY project_id, customer_id
--         HAVING COUNT(*) > 1
--     ) THEN
--         RAISE EXCEPTION 'Preflight failed: multiple Created payments exist for the same project_id/customer_id.';
--     END IF;
--
--     IF EXISTS (
--         SELECT 1
--         FROM payment
--         WHERE razorpay_order_id = ''
--     ) THEN
--         RAISE EXCEPTION 'Preflight failed: empty razorpay_order_id values exist in payment.';
--     END IF;
-- END
-- $$;
--
-- Capture empty_payment_ids_before_normalization using query 1g before
-- running this UPDATE.
-- -- Normalize empty payment IDs to NULL.
-- UPDATE payment
-- SET razorpay_payment_id = NULL
-- WHERE razorpay_payment_id = '';
-- After commit, run the post-migration count query in section 6 and confirm
-- empty_payment_ids_after_normalization is 0.
--
-- -- Unique Razorpay order ID across all payment rows.
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_razorpay_order_id
--     ON payment (razorpay_order_id);
--
-- -- Unique non-NULL Razorpay payment ID.
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_razorpay_payment_id_partial
--     ON payment (razorpay_payment_id)
--     WHERE razorpay_payment_id IS NOT NULL;
--
-- -- At most one Created payment per (project_id, customer_id).
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_project_customer_created
--     ON payment (project_id, customer_id)
--     WHERE status = 'Created';
--
-- COMMIT;
--
-- Recovery:
--   * If any statement fails, run ROLLBACK; and inspect the error.
--   * If a lock timeout occurs, retry during a maintenance window.
--   * If an index name already exists with a different definition, the
--     CREATE ... IF NOT EXISTS statement will not repair it. Inspect and
--     resolve the index definition manually before rerunning.

-- =====================================================================
-- 4. SQLite procedure (file-backed database; not in-memory test databases)
-- =====================================================================
-- SQLite supports transactional DDL in many cases, but driver, ORM, and
-- CLI transaction behavior differ from PostgreSQL. Use a verified backup
-- and test this procedure on a copy of the database before production use.
--
-- Recommended SQLite CLI flow:
--   .backup main /path/to/payment-pre-migration-backup.sqlite
--   BEGIN IMMEDIATE;
--
-- SQLite has no procedural RAISE in plain SQL except in triggers. The
-- invalid guard that referenced __migration_preflight_failed__ has been
-- removed. Operator verification is mandatory before any data-changing
-- statement:
--   1. Run preflight queries 1a, 1b, 1c, and 1e. Each must return zero rows.
--   2. Run preflight query 1d and review every returned row.
--   3. Run preflight query 1g and record empty_payment_ids_before_normalization.
--   4. Run the index/constraint definition queries in 1f and compare them
--      with the intended definitions in section 5.
--   5. Only then run the UPDATE and CREATE INDEX statements below.
-- If any preflight query returns an unexpected row, stop and repair the
-- data before continuing.
--
-- -- Normalize empty payment IDs to NULL.
-- UPDATE payment
-- SET razorpay_payment_id = NULL
-- WHERE razorpay_payment_id = '';
--
-- -- Create uniqueness indexes.
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_razorpay_order_id
--     ON payment (razorpay_order_id);
--
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_razorpay_payment_id_partial
--     ON payment (razorpay_payment_id)
--     WHERE razorpay_payment_id IS NOT NULL;
--
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_project_customer_created
--     ON payment (project_id, customer_id)
--     WHERE status = 'Created';
--
-- COMMIT;
--
-- Recovery:
--   * If the guard query or any later statement fails, run ROLLBACK; and
--     restore from the backup if needed.
--   * If the database is locked, retry after active transactions complete.
--   * SQLite partial index syntax requires SQLite 3.8.0 or newer.
--   * If an index with one of these names already exists with a different
--     definition, SQLite will not replace it. Inspect sqlite_master first.

-- =====================================================================
-- 5. Post-migration verification (run after commit)
-- =====================================================================
-- PostgreSQL:
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE tablename = 'payment'
--   ORDER BY indexname;
--
-- SQLite:
--   SELECT name, sql
--   FROM sqlite_master
--   WHERE type = 'index'
--     AND tbl_name = 'payment'
--   ORDER BY name;
--
-- Empty payment ID normalization verification:
--   SELECT COUNT(*) AS empty_payment_ids_after_normalization
--   FROM payment
--   WHERE razorpay_payment_id = '';
-- Expected result: 0 rows, because empty strings are normalized to NULL.
--
-- Application verification:
--   Run the focused payment test suite against a migrated copy of the
--   database. Do not run it against the production database until the
--   migration has been separately approved and applied.
