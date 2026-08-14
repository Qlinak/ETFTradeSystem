"""Repository for daily quota and reservation updates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def ensure_daily_quota_row(
    session: Session,
    *,
    product_id: str,
    quota_date: date,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO product_daily_quota (
                product_id,
                quota_date,
                currency,
                total_quota,
                used_quota,
                cutoff_time,
                market,
                updated_at
            )
            SELECT
                p.id,
                :quota_date,
                p.currency,
                p.daily_total_quota,
                0,
                p.cutoff_time,
                p.market,
                statement_timestamp()
            FROM products p
            WHERE p.id = :product_id
            ON CONFLICT (product_id, quota_date) DO NOTHING
            """
        ),
        {"product_id": product_id, "quota_date": quota_date},
    )


def reserve_quota(
    session: Session,
    *,
    product_id: str,
    quota_date: date,
    amount: Decimal,
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            UPDATE product_daily_quota
            SET used_quota = used_quota + :amount,
                updated_at = statement_timestamp()
            WHERE product_id = :product_id
              AND quota_date = :quota_date
              AND used_quota + :amount <= total_quota
            RETURNING *
            """
        ),
        {
            "product_id": product_id,
            "quota_date": quota_date,
            "amount": amount,
        },
    ).mappings().first()
    return dict(row) if row else None


def create_quota_allocation(
    session: Session,
    *,
    order_id: str,
    product_id: str,
    quota_date: date,
    currency: str,
    allocated_amount: Decimal,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO quota_allocations (
                order_id,
                product_id,
                quota_date,
                currency,
                allocated_amount
            )
            VALUES (
                :order_id,
                :product_id,
                :quota_date,
                :currency,
                :allocated_amount
            )
            """
        ),
        {
            "order_id": order_id,
            "product_id": product_id,
            "quota_date": quota_date,
            "currency": currency,
            "allocated_amount": allocated_amount,
        },
    )


def release_quota_allocation_once(
    session: Session,
    *,
    order_id: str,
    release_reason: str,
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            UPDATE quota_allocations
            SET released_at = statement_timestamp(),
                release_reason = :release_reason
            WHERE order_id = :order_id
              AND released_at IS NULL
            RETURNING *
            """
        ),
        {"order_id": order_id, "release_reason": release_reason},
    ).mappings().first()
    return dict(row) if row else None


def decrement_used_quota(
    session: Session,
    *,
    product_id: str,
    quota_date: date,
    amount: Decimal,
) -> None:
    session.execute(
        text(
            """
            UPDATE product_daily_quota
            SET used_quota = GREATEST(0, used_quota - :amount),
                updated_at = statement_timestamp()
            WHERE product_id = :product_id
              AND quota_date = :quota_date
            """
        ),
        {
            "product_id": product_id,
            "quota_date": quota_date,
            "amount": amount,
        },
    )
