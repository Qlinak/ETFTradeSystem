"""Domain service for operations-side order confirmation action."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ERROR_MESSAGES, ErrorCode
from app.core.exceptions import NotFoundError, StateConflictError, ValidationError
from app.repositories.order_repository import (
    get_order_by_client_order_id,
    get_order_by_id_for_update,
    update_order_status,
)
from app.repositories.product_repository import get_product_for_update
from app.services.ledger_service import post_confirm_entries
from app.services.order_query_service import get_order_response
from app.services.quota_service import reserve_quota_for_order


def confirm_order(
    session: Session,
    *,
    client_order_id: str,
    pd_id: str,
) -> dict:
    order = get_order_by_client_order_id(session, client_order_id, pd_id)
    if order is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND)

    locked_order = get_order_by_id_for_update(session, str(order["id"]))
    if locked_order is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND)

    if locked_order["status"] == "CONFIRMED":
        return get_order_response(session, client_order_id=client_order_id, pd_id=pd_id)

    if locked_order["status"] in ("CANCELLED", "SETTLED"):
        raise StateConflictError(ErrorCode.INVALID_ORDER_STATE)

    if locked_order["status"] == "REJECTED":
        raise StateConflictError(ErrorCode.INVALID_ORDER_STATE, "Rejected orders cannot be confirmed.")

    if locked_order["status"] != "PENDING":
        raise ValidationError(ErrorCode.INVALID_ORDER_STATE)

    product = get_product_for_update(session, locked_order["product_id"])
    if product is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND, "Product was not found.")

    if product["has_qdii_quota"]:
        try:
            reserve_quota_for_order(
                session,
                order_id=str(locked_order["id"]),
                product_id=locked_order["product_id"],
                quota_date=locked_order["server_received_at"].date(),
                currency=locked_order["currency"],
                amount=locked_order["cash_amount"],
            )
        except StateConflictError:
            update_order_status(
                session,
                order_id=str(locked_order["id"]),
                status="REJECTED",
                rejection_reason_code=ErrorCode.QUOTA_EXCEEDED.value,
                rejection_reason=ERROR_MESSAGES[ErrorCode.QUOTA_EXCEEDED],
            )
            raise ValidationError(ErrorCode.QUOTA_EXCEEDED)

    confirmed = update_order_status(
        session,
        order_id=str(locked_order["id"]),
        status="CONFIRMED",
        rejection_reason_code=None,
        rejection_reason=None,
    )

    post_confirm_entries(
        session,
        order_id=str(confirmed["id"]),
        currency=confirmed["currency"],
        amount=confirmed["cash_amount"],
    )

    return get_order_response(session, client_order_id=confirmed["client_order_id"], pd_id=confirmed["pd_id"])
