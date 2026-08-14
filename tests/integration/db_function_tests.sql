BEGIN;

TRUNCATE TABLE
    ledger_entries,
    cash_movements,
    quota_allocations,
    order_idempotency,
    product_daily_quota,
    orders,
    pds,
    products,
    holiday_calendars
RESTART IDENTITY CASCADE;

INSERT INTO pds (id, name)
VALUES
    ('PD-TEST-01', 'PD Test 01');

INSERT INTO products (
    id,
    name,
    market,
    market_timezone,
    currency,
    creation_unit_size,
    cutoff_time,
    has_qdii_quota,
    daily_total_quota,
    remaining_quota
)
VALUES
    ('PROD-OK-001', 'Product OK', 'HK', 'UTC', 'HKD', 1000000, '23:59:59', FALSE, 0, 0),
    ('PROD-CUTOFF-001', 'Product Cutoff', 'HK', 'UTC', 'HKD', 1000000, '11:00:00', FALSE, 0, 0);

-- validate_order_product_consistency: success case.
INSERT INTO orders (
    client_order_id,
    product_id,
    pd_id,
    order_type,
    units,
    estimated_price,
    cash_amount,
    currency,
    status,
    server_received_at,
    submitted_at
)
VALUES (
    'ORD-CONSISTENCY-SUCCESS',
    'PROD-OK-001',
    'PD-TEST-01',
    'CREATION',
    2000000,
    50.25000000,
    100.2500,
    'HKD',
    'PENDING',
    '2026-08-14 10:00:00+00',
    '2026-08-14 10:00:00+00'
);

DO $$
BEGIN
    -- validate_order_product_consistency: fail on non-multiple units.
    BEGIN
        INSERT INTO orders (
            client_order_id,
            product_id,
            pd_id,
            order_type,
            units,
            estimated_price,
            cash_amount,
            currency,
            status,
            server_received_at,
            submitted_at
        )
        VALUES (
            'ORD-CONSISTENCY-UNITS-FAIL',
            'PROD-OK-001',
            'PD-TEST-01',
            'CREATION',
            1000001,
            50.25000000,
            100.2500,
            'HKD',
            'PENDING',
            '2026-08-14 10:00:00+00',
            '2026-08-14 10:00:00+00'
        );
        RAISE EXCEPTION 'Expected non-multiple units validation to fail.';
    EXCEPTION
        WHEN OTHERS THEN
            IF POSITION('integer multiple of creation_unit_size' IN SQLERRM) = 0 THEN
                RAISE;
            END IF;
    END;

    -- validate_order_product_consistency: fail on currency mismatch.
    BEGIN
        INSERT INTO orders (
            client_order_id,
            product_id,
            pd_id,
            order_type,
            units,
            estimated_price,
            cash_amount,
            currency,
            status,
            server_received_at,
            submitted_at
        )
        VALUES (
            'ORD-CONSISTENCY-CURRENCY-FAIL',
            'PROD-OK-001',
            'PD-TEST-01',
            'CREATION',
            2000000,
            50.25000000,
            100.2500,
            'USD',
            'PENDING',
            '2026-08-14 10:00:00+00',
            '2026-08-14 10:00:00+00'
        );
        RAISE EXCEPTION 'Expected currency consistency validation to fail.';
    EXCEPTION
        WHEN OTHERS THEN
            IF POSITION('does not match product currency' IN SQLERRM) = 0 THEN
                RAISE;
            END IF;
    END;
END;
$$;

-- validate_order_cutoff_time: success before cutoff.
INSERT INTO orders (
    client_order_id,
    product_id,
    pd_id,
    order_type,
    units,
    estimated_price,
    cash_amount,
    currency,
    status,
    server_received_at,
    submitted_at
)
VALUES (
    'ORD-CUTOFF-SUCCESS',
    'PROD-CUTOFF-001',
    'PD-TEST-01',
    'CREATION',
    2000000,
    50.25000000,
    100.2500,
    'HKD',
    'PENDING',
    '2026-08-14 10:59:59+00',
    '2026-08-14 10:59:59+00'
);

DO $$
BEGIN
    -- validate_order_cutoff_time: fail at/after cutoff.
    BEGIN
        INSERT INTO orders (
            client_order_id,
            product_id,
            pd_id,
            order_type,
            units,
            estimated_price,
            cash_amount,
            currency,
            status,
            server_received_at,
            submitted_at
        )
        VALUES (
            'ORD-CUTOFF-FAIL',
            'PROD-CUTOFF-001',
            'PD-TEST-01',
            'CREATION',
            2000000,
            50.25000000,
            100.2500,
            'HKD',
            'PENDING',
            '2026-08-14 11:00:01+00',
            '2026-08-14 11:00:01+00'
        );
        RAISE EXCEPTION 'Expected cutoff validation to fail.';
    EXCEPTION
        WHEN OTHERS THEN
            IF POSITION('Cutoff breached' IN SQLERRM) = 0 THEN
                RAISE;
            END IF;
    END;
END;
$$;

-- assert_ledger_zero_sum: success case with balanced debit/credit pair.
WITH order_row AS (
    SELECT id AS order_id
    FROM orders
    WHERE client_order_id = 'ORD-CONSISTENCY-SUCCESS'
), movement_row AS (
    INSERT INTO cash_movements (order_id, event_type)
    SELECT order_id, 'CONFIRM' FROM order_row
    RETURNING id
)
INSERT INTO ledger_entries (movement_id, entry_role, account_code, currency, amount)
SELECT id, 'DEBIT', 'PD_CASH', 'HKD', 100.0000 FROM movement_row
UNION ALL
SELECT id, 'CREDIT', 'SYSTEM_CASH', 'HKD', 100.0000 FROM movement_row;

DO $$
DECLARE
    movement_id_unbalanced UUID;
BEGIN
    -- assert_ledger_zero_sum: fail case with unbalanced entries.
    WITH order_row AS (
        SELECT id AS order_id
        FROM orders
        WHERE client_order_id = 'ORD-CUTOFF-SUCCESS'
    )
    INSERT INTO cash_movements (order_id, event_type)
    SELECT order_id, 'CONFIRM' FROM order_row
    RETURNING id INTO movement_id_unbalanced;

    BEGIN
        INSERT INTO ledger_entries (movement_id, entry_role, account_code, currency, amount)
        VALUES (movement_id_unbalanced, 'DEBIT', 'PD_CASH', 'HKD', 100.0000);

        INSERT INTO ledger_entries (movement_id, entry_role, account_code, currency, amount)
        VALUES (movement_id_unbalanced, 'CREDIT', 'SYSTEM_CASH', 'HKD', 90.0000);

        SET CONSTRAINTS ledger_zero_sum_trg IMMEDIATE;
        RAISE EXCEPTION 'Expected zero-sum validation to fail.';
    EXCEPTION
        WHEN OTHERS THEN
            IF POSITION('Double-entry invariant violated' IN SQLERRM) = 0 THEN
                RAISE;
            END IF;
    END;
END;
$$;

ROLLBACK;

SELECT 'DB function tests passed.' AS result;
