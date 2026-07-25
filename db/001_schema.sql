-- Wallet multi-user schema (scoped by users.id)
-- Apply: psql … -f db/001_schema.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL DEFAULT '',
    email           VARCHAR(255) UNIQUE,
    password_hash   TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,
    wallet_kind     VARCHAR(32)  NOT NULL CHECK (wallet_kind IN ('personal', 'business')),
    color           VARCHAR(16)  NOT NULL DEFAULT '#3B82F6',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS ix_categories_user ON categories (user_id);

CREATE TABLE IF NOT EXISTS accounts (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_id       VARCHAR(64),
    name            VARCHAR(128) NOT NULL,
    amount          NUMERIC(14, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_id)
);

CREATE INDEX IF NOT EXISTS ix_accounts_user ON accounts (user_id);

CREATE TABLE IF NOT EXISTS operations (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_op_id    VARCHAR(64),
    -- UI buckets: confirmed personal/business, review queue, soft-deleted
    bucket          VARCHAR(32)  NOT NULL
        CHECK (bucket IN ('personal', 'business', 'imported', 'deleted')),
    status          VARCHAR(32)  NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed', 'deleted')),
    occurred_at     TIMESTAMPTZ  NOT NULL,
    category_name   VARCHAR(128),
    expense         VARCHAR(128) NOT NULL DEFAULT '',
    store           VARCHAR(512) NOT NULL,
    amount          NUMERIC(14, 2) NOT NULL,
    comment         TEXT         NOT NULL DEFAULT '',
    account_id      BIGINT       REFERENCES accounts (id) ON DELETE SET NULL,
    object_name     VARCHAR(256),
    customer        VARCHAR(256),
    bank            JSONB,
    fingerprint     VARCHAR(128),
    source          VARCHAR(64)  NOT NULL DEFAULT 'manual',
    group_id        INT,
    meta            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_op_id)
);

CREATE INDEX IF NOT EXISTS ix_operations_user_bucket ON operations (user_id, bucket);
CREATE INDEX IF NOT EXISTS ix_operations_user_date ON operations (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_operations_user_store ON operations (user_id, store);
CREATE INDEX IF NOT EXISTS ix_operations_user_category ON operations (user_id, category_name);

CREATE TABLE IF NOT EXISTS merchants (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    store           VARCHAR(512) NOT NULL,
    auto            BOOLEAN      NOT NULL DEFAULT FALSE,
    user_auto       BOOLEAN      NOT NULL DEFAULT FALSE,
    trust           INT          NOT NULL DEFAULT 0,
    learn           INT          NOT NULL DEFAULT 0,
    outcomes        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, store)
);

CREATE INDEX IF NOT EXISTS ix_merchants_user ON merchants (user_id);

CREATE TABLE IF NOT EXISTS debts (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_id       VARCHAR(64),
    direction       VARCHAR(16)  NOT NULL CHECK (direction IN ('lent', 'borrowed')),
    person          VARCHAR(256) NOT NULL,
    amount          NUMERIC(14, 2) NOT NULL,
    debt_date       VARCHAR(32)  NOT NULL DEFAULT '',
    due_date        VARCHAR(32)  NOT NULL DEFAULT '—',
    note            TEXT         NOT NULL DEFAULT '',
    is_closed       BOOLEAN      NOT NULL DEFAULT FALSE,
    meta            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_id)
);

CREATE INDEX IF NOT EXISTS ix_debts_user ON debts (user_id, direction);

CREATE TABLE IF NOT EXISTS account_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_id       VARCHAR(64),
    log_date        DATE,
    balances        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    fact            BOOLEAN      NOT NULL DEFAULT FALSE,
    meta            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_account_log_user ON account_log (user_id);

CREATE TABLE IF NOT EXISTS plans (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    period          CHAR(7)      NOT NULL,
    lines           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, period)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id              BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    plan_month           VARCHAR(7),
    plan_pace_modes      JSONB   NOT NULL DEFAULT '{}'::jsonb,
    plan_envelopes       JSONB   NOT NULL DEFAULT '{}'::jsonb,
    expense_presets      JSONB   NOT NULL DEFAULT '{}'::jsonb,
    default_account_id   BIGINT  REFERENCES accounts (id) ON DELETE SET NULL,
    import_skip_preview  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
