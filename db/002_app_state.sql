-- Full HTML statePayload snapshot — Postgres source of truth for the UI.

BEGIN;

CREATE TABLE IF NOT EXISTS user_app_state (
    user_id       BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    payload       JSONB        NOT NULL,
    source        VARCHAR(64)  NOT NULL DEFAULT 'manual',
    revision      BIGINT       NOT NULL DEFAULT 1,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_user_app_state_updated ON user_app_state (updated_at DESC);

COMMIT;
