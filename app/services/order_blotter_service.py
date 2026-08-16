"""Service functions for operations blotter views and status updates."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.order_repository import list_order_status_events, list_orders_for_blotter


def _format_order_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "systemOrderId": str(row["id"]),
        "clientOrderId": row["client_order_id"],
        "productId": row["product_id"],
        "pdId": row["pd_id"],
        "orderType": row["order_type"],
        "units": str(row["units"]),
        "estimatedPrice": f"{row['estimated_price']:.8f}",
        "cashAmount": f"{row['cash_amount']:.4f}",
        "currency": row["currency"],
        "status": row["status"],
        "submittedAt": row["submitted_at"],
        "settlementDate": row["settlement_date"],
        "rejectionReason": row["rejection_reason"],
        "updatedAt": row["updated_at"],
        "lastEventId": row.get("last_event_id"),
    }


def get_orders_blotter(
    session: Session,
    *,
    trade_date: date,
    product_id: str | None,
    pd_id: str | None,
    status: str | None,
    currency: str | None,
    sort_by: str,
    sort_dir: str,
    cursor: int,
    limit: int,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    rows = list_orders_for_blotter(
        session,
        trade_date=trade_date,
        product_id=product_id,
        pd_id=pd_id,
        status=status,
        currency=currency,
        sort_by=sort_by,
        sort_dir=sort_dir,
        offset=cursor,
        limit=safe_limit + 1,
    )

    has_more = len(rows) > safe_limit
    visible_rows = rows[:safe_limit]
    next_cursor = cursor + safe_limit if has_more else None

    server_time = session.execute(text("SELECT statement_timestamp()")).scalar_one()

    return {
        "tradeDate": trade_date,
        "sortBy": sort_by,
        "sortDir": sort_dir,
        "cursor": cursor,
        "nextCursor": next_cursor,
        "hasMore": has_more,
        "serverTime": server_time,
        "rows": [_format_order_row(row) for row in visible_rows],
    }


def get_order_updates(
    session: Session,
    *,
    since_event_id: int,
    trade_date: date | None,
    product_id: str | None,
    pd_id: str | None,
    status: str | None,
    currency: str | None,
    limit: int,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    rows = list_order_status_events(
        session,
        since_event_id=since_event_id,
        trade_date=trade_date,
        product_id=product_id,
        pd_id=pd_id,
        status=status,
        currency=currency,
        limit=safe_limit + 1,
    )

    has_more = len(rows) > safe_limit
    visible_rows = rows[:safe_limit]

    events = []
    next_since = since_event_id
    for row in visible_rows:
        event_id = int(row["event_id"])
        next_since = max(next_since, event_id)
        events.append(
            {
                "eventId": event_id,
                "eventType": row["event_type"],
                "occurredAt": row["occurred_at"],
                "order": _format_order_row(
                    {
                        **row,
                        "updated_at": row["occurred_at"],
                        "last_event_id": event_id,
                    }
                ),
            }
        )

    server_time = session.execute(text("SELECT statement_timestamp()")).scalar_one()

    return {
        "since": since_event_id,
        "nextSince": next_since,
        "hasMore": has_more,
        "serverTime": server_time,
        "events": events,
    }
