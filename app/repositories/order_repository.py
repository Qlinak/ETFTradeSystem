"""Repository for order persistence and state transitions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


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
    rejection_reason_code: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
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
