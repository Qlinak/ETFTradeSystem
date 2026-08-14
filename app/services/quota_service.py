"""Domain service for quota reservation and release."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import NotFoundError, StateConflictError
from app.repositories.product_repository import get_daily_quota, get_product
from app.repositories.quota_repository import (
    create_quota_allocation,
    decrement_used_quota,
    ensure_daily_quota_row,
    release_quota_allocation_once,
    reserve_quota,
)


def reserve_quota_for_order(
    session: Session,
    *,
    order_id: str,
    product_id: str,
    quota_date: date,
    currency: str,
    amount: Decimal,
) -> None:
    ensure_daily_quota_row(session, product_id=product_id, quota_date=quota_date)
    reserved = reserve_quota(
        session,
        product_id=product_id,
        quota_date=quota_date,
        amount=amount,
    )
    if reserved is None:
        raise StateConflictError(ErrorCode.QUOTA_EXCEEDED)

    create_quota_allocation(
        session,
        order_id=order_id,
        product_id=product_id,
        quota_date=quota_date,
        currency=currency,
        allocated_amount=amount,
    )


def release_quota_if_allocated(
    session: Session,
    *,
    order_id: str,
    release_reason: str,
) -> Decimal | None:
    allocation = release_quota_allocation_once(
        session,
        order_id=order_id,
        release_reason=release_reason,
    )
    if allocation is None:
        return None

    decrement_used_quota(
        session,
        product_id=allocation["product_id"],
        quota_date=allocation["quota_date"],
        amount=allocation["allocated_amount"],
    )

    return allocation["allocated_amount"]


def get_quota_response(session: Session, *, product_id: str) -> dict:
    product = get_product(session, product_id)
    if product is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND, "Product was not found.")

    quota_row = get_daily_quota(session, product_id, date.today())
    if quota_row is None:
        total_quota = product["daily_total_quota"]
        used_quota = Decimal("0")
        cutoff_time = product["cutoff_time"]
    else:
        total_quota = quota_row["total_quota"]
        used_quota = quota_row["used_quota"]
        cutoff_time = quota_row["cutoff_time"]

    as_of = session.execute(text("SELECT statement_timestamp()"))
    remaining = total_quota - used_quota

    return {
        "productId": product["id"],
        "currency": product["currency"],
        "totalDailyQuota": f"{total_quota:.4f}",
        "remainingQuota": f"{remaining:.4f}",
        "cutoffTime": str(cutoff_time),
        "asOf": as_of.scalar_one(),
    }
