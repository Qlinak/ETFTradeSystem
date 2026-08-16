"""Repository for order persistence and state transitions."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_ORDER_EVENTS_READY = False


def _ensure_order_status_events_infra(session: Session) -> None:
    global _ORDER_EVENTS_READY
    if _ORDER_EVENTS_READY:
        return

    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS order_status_events (
                event_id BIGSERIAL PRIMARY KEY,
                order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                client_order_id VARCHAR(128) NOT NULL,
                product_id VARCHAR(64) NOT NULL,
                pd_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'CANCELLED', 'SETTLED')),
                rejection_reason TEXT,
                event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('CREATED', 'STATUS_CHANGED')),
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS order_status_events_event_id_idx ON order_status_events (event_id)"))
    session.execute(text("CREATE INDEX IF NOT EXISTS order_status_events_order_id_idx ON order_status_events (order_id, event_id DESC)"))
    session.execute(text("CREATE INDEX IF NOT EXISTS order_status_events_occurred_at_idx ON order_status_events (occurred_at DESC)"))

    session.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION emit_order_status_event()
            RETURNS TRIGGER
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    INSERT INTO order_status_events (
                        order_id,
                        client_order_id,
                        product_id,
                        pd_id,
                        status,
                        rejection_reason,
                        event_type
                    )
                    VALUES (
                        NEW.id,
                        NEW.client_order_id,
                        NEW.product_id,
                        NEW.pd_id,
                        NEW.status,
                        NEW.rejection_reason,
                        'CREATED'
                    );
                    RETURN NEW;
                END IF;

                IF NEW.status IS DISTINCT FROM OLD.status
                   OR NEW.rejection_reason IS DISTINCT FROM OLD.rejection_reason THEN
                    INSERT INTO order_status_events (
                        order_id,
                        client_order_id,
                        product_id,
                        pd_id,
                        status,
                        rejection_reason,
                        event_type
                    )
                    VALUES (
                        NEW.id,
                        NEW.client_order_id,
                        NEW.product_id,
                        NEW.pd_id,
                        NEW.status,
                        NEW.rejection_reason,
                        'STATUS_CHANGED'
                    );
                END IF;

                RETURN NEW;
            END;
            $$
            """
        )
    )
    session.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname = 'orders_status_events_trg'
                ) THEN
                    CREATE TRIGGER orders_status_events_trg
                    AFTER INSERT OR UPDATE OF status, rejection_reason ON orders
                    FOR EACH ROW
                    EXECUTE FUNCTION emit_order_status_event();
                END IF;
            END;
            $$;
            """
        )
    )

    _ORDER_EVENTS_READY = True


def insert_order(
    session: Session,
    *,
    client_order_id: str,
    product_id: str,
    pd_id: str,
    order_type: str,
    units: int,
    estimated_price: str,
    cash_amount: str,
    currency: str,
    status: str,
    settlement_date: date | None = None,
    rejection_reason_code: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    _ensure_order_status_events_infra(session)
    row = session.execute(
        text(
            """
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
                settlement_date,
                rejection_reason_code,
                rejection_reason
            )
            VALUES (
                :client_order_id,
                :product_id,
                :pd_id,
                :order_type,
                :units,
                :estimated_price,
                :cash_amount,
                :currency,
                :status,
                :settlement_date,
                :rejection_reason_code,
                :rejection_reason
            )
            RETURNING *
            """
        ),
        {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "pd_id": pd_id,
            "order_type": order_type,
            "units": units,
            "estimated_price": estimated_price,
            "cash_amount": cash_amount,
            "currency": currency,
            "status": status,
            "settlement_date": settlement_date,
            "rejection_reason_code": rejection_reason_code,
            "rejection_reason": rejection_reason,
        },
    ).mappings().one()
    return dict(row)


def get_order_by_client_order_id(
    session: Session,
    client_order_id: str,
    pd_id: str | None = None,
) -> dict[str, Any] | None:
    if pd_id:
        query = text(
            """
            SELECT *
            FROM orders
            WHERE client_order_id = :client_order_id
              AND pd_id = :pd_id
            ORDER BY submitted_at DESC
            LIMIT 1
            """
        )
        params = {"client_order_id": client_order_id, "pd_id": pd_id}
    else:
        query = text(
            """
            SELECT *
            FROM orders
            WHERE client_order_id = :client_order_id
            ORDER BY submitted_at DESC
            LIMIT 1
            """
        )
        params = {"client_order_id": client_order_id}

    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


def get_order_by_id_for_update(session: Session, order_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT * FROM orders WHERE id = :order_id FOR UPDATE"),
        {"order_id": order_id},
    ).mappings().first()
    return dict(row) if row else None


def update_order_status(
    session: Session,
    *,
    order_id: str,
    status: str,
    rejection_reason_code: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    _ensure_order_status_events_infra(session)
    row = session.execute(
        text(
            """
            UPDATE orders
            SET status = :status,
                rejection_reason_code = :rejection_reason_code,
                rejection_reason = :rejection_reason
            WHERE id = :order_id
            RETURNING *
            """
        ),
        {
            "order_id": order_id,
            "status": status,
            "rejection_reason_code": rejection_reason_code,
            "rejection_reason": rejection_reason,
        },
    ).mappings().one()
    return dict(row)


def list_orders_for_blotter(
    session: Session,
    *,
    trade_date: date,
    product_id: str | None,
    pd_id: str | None,
    status: str | None,
    currency: str | None,
    sort_by: str,
    sort_dir: str,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    _ensure_order_status_events_infra(session)
    sort_column_map = {
        "submittedAt": "o.submitted_at",
        "productId": "o.product_id",
        "pdId": "o.pd_id",
        "status": "o.status",
        "currency": "o.currency",
    }
    order_column = sort_column_map.get(sort_by, "o.submitted_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    query = text(
        f"""
        SELECT
            o.*,
            COALESCE(last_ev.occurred_at, o.submitted_at) AS updated_at,
            last_ev.event_id AS last_event_id
        FROM orders o
        LEFT JOIN LATERAL (
            SELECT e.event_id, e.occurred_at
            FROM order_status_events e
            WHERE e.order_id = o.id
            ORDER BY e.event_id DESC
            LIMIT 1
        ) AS last_ev ON TRUE
        WHERE o.server_received_at::date = :trade_date
                    AND (CAST(:product_id AS varchar) IS NULL OR o.product_id = CAST(:product_id AS varchar))
                    AND (CAST(:pd_id AS varchar) IS NULL OR o.pd_id = CAST(:pd_id AS varchar))
                    AND (CAST(:status AS varchar) IS NULL OR o.status = CAST(:status AS varchar))
                    AND (CAST(:currency AS varchar) IS NULL OR o.currency = CAST(:currency AS varchar))
        ORDER BY {order_column} {direction}, o.id {direction}
        OFFSET :offset
        LIMIT :limit
        """
    )

    rows = session.execute(
        query,
        {
            "trade_date": trade_date,
            "product_id": product_id,
            "pd_id": pd_id,
            "status": status,
            "currency": currency,
            "offset": offset,
            "limit": limit,
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def list_order_status_events(
    session: Session,
    *,
    since_event_id: int,
    trade_date: date | None,
    product_id: str | None,
    pd_id: str | None,
    status: str | None,
    currency: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    _ensure_order_status_events_infra(session)
    rows = session.execute(
        text(
            """
            SELECT
                e.event_id,
                e.occurred_at,
                e.event_type,
                o.*
            FROM order_status_events e
            JOIN orders o ON o.id = e.order_id
            WHERE e.event_id > :since_event_id
                            AND (CAST(:trade_date AS date) IS NULL OR o.server_received_at::date = CAST(:trade_date AS date))
                            AND (CAST(:product_id AS varchar) IS NULL OR o.product_id = CAST(:product_id AS varchar))
                            AND (CAST(:pd_id AS varchar) IS NULL OR o.pd_id = CAST(:pd_id AS varchar))
                            AND (CAST(:status AS varchar) IS NULL OR o.status = CAST(:status AS varchar))
                            AND (CAST(:currency AS varchar) IS NULL OR o.currency = CAST(:currency AS varchar))
            ORDER BY e.event_id ASC
            LIMIT :limit
            """
        ),
        {
            "since_event_id": since_event_id,
            "trade_date": trade_date,
            "product_id": product_id,
            "pd_id": pd_id,
            "status": status,
            "currency": currency,
            "limit": limit,
        },
    ).mappings().all()
    return [dict(row) for row in rows]
