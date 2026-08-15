CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Reference Tables
CREATE TABLE products (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    market VARCHAR(8) NOT NULL DEFAULT 'HK' CHECK (market IN ('HK', 'US', 'CN')),
    market_timezone TEXT NOT NULL DEFAULT 'Asia/Hong_Kong',
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('HKD', 'USD', 'RMB', 'CNH')),
    creation_unit_size BIGINT NOT NULL CHECK (creation_unit_size > 0),
    cutoff_time TIME NOT NULL,
    has_qdii_quota BOOLEAN NOT NULL DEFAULT FALSE,
    daily_total_quota NUMERIC(20, 4) NOT NULL DEFAULT 0 CHECK (daily_total_quota >= 0),
    remaining_quota NUMERIC(20, 4) NOT NULL DEFAULT 0 CHECK (remaining_quota >= 0)
);

CREATE TABLE pds (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

-- Core Orders Table
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_order_id VARCHAR(128) NOT NULL,
    product_id VARCHAR(64) NOT NULL REFERENCES products(id),
    pd_id VARCHAR(64) NOT NULL REFERENCES pds(id),
    order_type VARCHAR(16) NOT NULL CHECK (order_type IN ('CREATION', 'REDEMPTION')),
    units BIGINT NOT NULL CHECK (units > 0),
    estimated_price NUMERIC(20, 8) NOT NULL DEFAULT 0 CHECK (estimated_price >= 0),
    cash_amount NUMERIC(20, 4) NOT NULL CHECK (cash_amount >= 0),
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('HKD', 'USD', 'RMB', 'CNH')),
    status VARCHAR(32) NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'CANCELLED', 'SETTLED')),
    rejection_reason_code VARCHAR(64),
    rejection_reason TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    server_received_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    settlement_date DATE,
    CONSTRAINT orders_pd_client_order_id_key UNIQUE (pd_id, client_order_id)
);

CREATE INDEX orders_product_status_idx ON orders (product_id, status);
CREATE INDEX orders_client_order_lookup_idx ON orders (client_order_id);
CREATE INDEX orders_confirmed_settlement_idx
    ON orders (settlement_date, product_id, currency)
    INCLUDE (order_type, cash_amount)
    WHERE status = 'CONFIRMED';

CREATE TABLE order_idempotency (
    pd_id VARCHAR(64) NOT NULL REFERENCES pds(id),
    client_order_id VARCHAR(128) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    request_payload JSONB NOT NULL,
    order_id UUID REFERENCES orders(id),
    response_payload JSONB,
    final_status VARCHAR(32) CHECK (final_status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'CANCELLED', 'SETTLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    finalized_at TIMESTAMPTZ,
    PRIMARY KEY (pd_id, client_order_id)
);

CREATE TABLE product_daily_quota (
    product_id VARCHAR(64) NOT NULL REFERENCES products(id),
    quota_date DATE NOT NULL,
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('HKD', 'USD', 'RMB', 'CNH')),
    total_quota NUMERIC(20, 4) NOT NULL CHECK (total_quota >= 0),
    used_quota NUMERIC(20, 4) NOT NULL DEFAULT 0 CHECK (used_quota >= 0),
    cutoff_time TIME NOT NULL,
    market VARCHAR(8) NOT NULL CHECK (market IN ('HK', 'US', 'CN')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (product_id, quota_date),
    CONSTRAINT product_daily_quota_usage_check CHECK (used_quota <= total_quota)
);

CREATE INDEX product_daily_quota_lookup_idx ON product_daily_quota (product_id, quota_date);

CREATE TABLE quota_allocations (
    order_id UUID PRIMARY KEY REFERENCES orders(id) ON DELETE RESTRICT,
    product_id VARCHAR(64) NOT NULL REFERENCES products(id),
    quota_date DATE NOT NULL,
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('HKD', 'USD', 'RMB', 'CNH')),
    allocated_amount NUMERIC(20, 4) NOT NULL CHECK (allocated_amount > 0),
    allocated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    released_at TIMESTAMPTZ,
    release_reason VARCHAR(32),
    CONSTRAINT quota_allocations_release_check CHECK (
        (released_at IS NULL AND release_reason IS NULL)
        OR released_at IS NOT NULL
    )
);

CREATE INDEX quota_allocations_open_idx
    ON quota_allocations (product_id, quota_date)
    WHERE released_at IS NULL;

CREATE TABLE cash_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    event_type VARCHAR(32) NOT NULL CHECK (event_type IN ('CONFIRM', 'CANCEL', 'REJECT', 'SETTLE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    movement_id UUID NOT NULL REFERENCES cash_movements(id) ON DELETE CASCADE,
    entry_role VARCHAR(16) NOT NULL CHECK (entry_role IN ('DEBIT', 'CREDIT')),
    account_code VARCHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('HKD', 'USD', 'RMB', 'CNH')),
    amount NUMERIC(20, 4) NOT NULL CHECK (amount > 0),
    signed_amount NUMERIC(20, 4) GENERATED ALWAYS AS (
        CASE
            WHEN entry_role = 'DEBIT' THEN amount
            ELSE amount * -1
        END
    ) STORED
);

CREATE INDEX ledger_entries_movement_currency_idx ON ledger_entries (movement_id, currency);

-- Holiday Calendars Table for T+2 Computation
CREATE TABLE holiday_calendars (
    market VARCHAR(8) NOT NULL CHECK (market IN ('HK', 'US', 'CN')),
    holiday_date DATE NOT NULL,
    PRIMARY KEY (market, holiday_date)
);

CREATE OR REPLACE FUNCTION validate_order_product_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    product_creation_unit BIGINT;
    product_currency VARCHAR(3);
BEGIN
    SELECT creation_unit_size, currency
    INTO product_creation_unit, product_currency
    FROM products
    WHERE id = NEW.product_id;

    IF product_creation_unit IS NULL THEN
        RAISE EXCEPTION 'Unknown product id: %', NEW.product_id;
    END IF;

    IF NEW.units % product_creation_unit <> 0 THEN
        RAISE EXCEPTION 'Units % must be an integer multiple of creation_unit_size % for product %',
            NEW.units, product_creation_unit, NEW.product_id;
    END IF;

    IF NEW.currency <> product_currency THEN
        RAISE EXCEPTION 'Order currency % does not match product currency % for product %',
            NEW.currency, product_currency, NEW.product_id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER orders_product_consistency_trg
BEFORE INSERT OR UPDATE ON orders
FOR EACH ROW
EXECUTE FUNCTION validate_order_product_consistency();

CREATE OR REPLACE FUNCTION validate_order_cutoff_time()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    product_cutoff_time TIME;
    product_timezone TEXT;
    local_order_time TIME;
BEGIN
    -- Only enforce cutoff for incoming active orders.
    IF NEW.status NOT IN ('PENDING', 'CONFIRMED') THEN
        RETURN NEW;
    END IF;

    SELECT cutoff_time, market_timezone
    INTO product_cutoff_time, product_timezone
    FROM products
    WHERE id = NEW.product_id;

    IF product_cutoff_time IS NULL THEN
        RAISE EXCEPTION 'Unknown product id: %', NEW.product_id;
    END IF;

    local_order_time := (NEW.server_received_at AT TIME ZONE product_timezone)::time;

    -- Hard boundary: exactly at cutoff or later is rejected.
    IF local_order_time >= product_cutoff_time THEN
        RAISE EXCEPTION
            'Cutoff breached for product %: local receive time % is not earlier than cutoff %',
            NEW.product_id,
            local_order_time,
            product_cutoff_time;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS orders_cutoff_time_trg ON orders;
CREATE TRIGGER orders_cutoff_time_trg
BEFORE INSERT OR UPDATE ON orders
FOR EACH ROW
EXECUTE FUNCTION validate_order_cutoff_time();

CREATE OR REPLACE FUNCTION assert_ledger_zero_sum()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    affected_movement_id UUID;
    affected_currency VARCHAR(3);
    movement_balance NUMERIC(20, 4);
BEGIN
    affected_movement_id := COALESCE(NEW.movement_id, OLD.movement_id);
    affected_currency := COALESCE(NEW.currency, OLD.currency);

    SELECT COALESCE(SUM(signed_amount), 0)
    INTO movement_balance
    FROM ledger_entries
    WHERE movement_id = affected_movement_id
      AND currency = affected_currency;

    IF movement_balance <> 0 THEN
        RAISE EXCEPTION 'Double-entry invariant violated for movement % and currency %: balance %',
            affected_movement_id, affected_currency, movement_balance;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ledger_zero_sum_trg
AFTER INSERT OR UPDATE OR DELETE ON ledger_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_ledger_zero_sum();