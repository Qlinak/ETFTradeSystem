"""Repository for cash ladder read queries."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def fetch_cash_ladder_rows(
    session: Session,
    *,
    as_of: date,
    window_end: date,
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            WITH product_currency AS (
                SELECT p.id AS product_id, p.currency
                FROM products p
            ),
            date_spine AS (
                SELECT d::date AS settlement_date
                FROM generate_series(CAST(:as_of AS date), CAST(:window_end AS date), interval '1 day') AS gs(d)
            ),
            confirmed_with_settlement AS (
                SELECT
                    o.id,
                    o.product_id,
                    o.currency,
                    o.order_type,
                    o.cash_amount,
                    o.settlement_date AS effective_settlement_date
                FROM orders o
                WHERE o.status = 'CONFIRMED'
                  AND o.settlement_date BETWEEN CAST(:as_of AS date) AND CAST(:window_end AS date)
            ),
            confirmed_needs_derive AS (
                SELECT
                    o.id,
                    o.product_id,
                    o.currency,
                    o.order_type,
                    o.cash_amount,
                    derived.settlement_date AS effective_settlement_date
                FROM orders o
                JOIN products p ON p.id = o.product_id
                LEFT JOIN LATERAL (
                    SELECT d::date AS settlement_date
                    FROM generate_series(
                        ((o.submitted_at AT TIME ZONE p.market_timezone)::date + 1)::timestamp,
                        ((o.submitted_at AT TIME ZONE p.market_timezone)::date + 20)::timestamp,
                        interval '1 day'
                    ) AS g(d)
                    WHERE EXTRACT(ISODOW FROM d) < 6
                      AND NOT EXISTS (
                          SELECT 1
                          FROM holiday_calendars h
                          WHERE h.market = p.market
                            AND h.holiday_date = d::date
                      )
                    ORDER BY d
                    OFFSET 1
                    LIMIT 1
                ) AS derived ON TRUE
                WHERE o.status = 'CONFIRMED'
                  AND o.settlement_date IS NULL
                  AND derived.settlement_date BETWEEN CAST(:as_of AS date) AND CAST(:window_end AS date)
            ),
            open_confirmed AS (
                SELECT * FROM confirmed_with_settlement
                UNION ALL
                SELECT * FROM confirmed_needs_derive
            ),
            filtered AS (
                SELECT *
                FROM open_confirmed
                WHERE effective_settlement_date IS NOT NULL
            ),
            aggregated AS (
                SELECT
                    f.effective_settlement_date AS settlement_date,
                    f.product_id,
                    f.currency,
                    COALESCE(SUM(CASE WHEN f.order_type = 'REDEMPTION' THEN f.cash_amount ELSE 0 END), 0)::numeric(20,4) AS inflow,
                    COALESCE(SUM(CASE WHEN f.order_type = 'CREATION' THEN f.cash_amount ELSE 0 END), 0)::numeric(20,4) AS outflow
                FROM filtered f
                GROUP BY f.effective_settlement_date, f.product_id, f.currency
            )
            SELECT
                ds.settlement_date,
                pc.product_id,
                pc.currency,
                COALESCE(a.inflow, 0)::numeric(20,4) AS inflow,
                COALESCE(a.outflow, 0)::numeric(20,4) AS outflow,
                (COALESCE(a.inflow, 0) - COALESCE(a.outflow, 0))::numeric(20,4) AS net
            FROM date_spine ds
            CROSS JOIN product_currency pc
            LEFT JOIN aggregated a
              ON a.settlement_date = ds.settlement_date
             AND a.product_id = pc.product_id
             AND a.currency = pc.currency
            ORDER BY ds.settlement_date, pc.currency, pc.product_id
            """
        ),
        {
            "as_of": as_of,
            "window_end": window_end,
        },
    ).mappings().all()

    return [dict(row) for row in rows]


def fetch_statement_timestamp(session: Session):
    return session.execute(text("SELECT statement_timestamp()"))
