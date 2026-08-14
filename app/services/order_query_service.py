"""Domain service for order query operations."""

from __future__ import annotations

from app.core.errors import ErrorCode
from app.core.exceptions import NotFoundError
from app.repositories.order_repository import get_order_by_client_order_id


def get_order_response(
    session,
    *,
    client_order_id: str,
    pd_id: str | None = None,
) -> dict:
    order = get_order_by_client_order_id(session, client_order_id, pd_id)
    if order is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND)

    return {
        "systemOrderId": str(order["id"]),
        "clientOrderId": order["client_order_id"],
        "productId": order["product_id"],
        "pdId": order["pd_id"],
        "orderType": order["order_type"],
        "units": str(order["units"]),
        "cashAmount": f"{order['cash_amount']:.4f}",
        "currency": order["currency"],
        "status": order["status"],
        "submittedAt": order["submitted_at"],
        "settlementDate": order["settlement_date"],
        "rejectionReason": order["rejection_reason"],
    }
