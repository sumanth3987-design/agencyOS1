-- Account security migration (proposal only; do not execute without approval)
-- Adds email verification and session revocation columns to user.
-- Creates user_token for password reset and email verification.

-- PostgreSQL
ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP NULL;

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS user_token (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    purpose VARCHAR(40) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_user_token_user_id ON user_token(user_id);
CREATE INDEX IF NOT EXISTS ix_user_token_purpose ON user_token(purpose);
CREATE INDEX IF NOT EXISTS ix_user_token_user_purpose ON user_token(user_id, purpose);
CREATE INDEX IF NOT EXISTS ix_user_token_expires_at ON user_token(expires_at);

-- NOTE: Existing accounts are NOT automatically marked email-verified by this
-- proposal. Automatic verification of legacy accounts requires an explicit,
-- documented policy decision and is intentionally omitted. New/updated
-- application behavior must treat email_verified_at IS NULL as unverified.

-- SQLite (run separately; SQLite has limited ALTER TABLE support)
-- ALTER TABLE user ADD COLUMN email_verified_at DATETIME;
-- ALTER TABLE user ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;
-- CREATE TABLE user_token (...);