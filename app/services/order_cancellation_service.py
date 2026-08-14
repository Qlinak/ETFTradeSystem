"""Domain service for order cancellation flow."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import NotFoundError, StateConflictError
from app.repositories.order_repository import (
    get_order_by_client_order_id,
    get_order_by_id_for_update,
    update_order_status,
)
from app.services.ledger_service import post_cancel_entries
from app.services.order_query_service import get_order_response
from app.services.quota_service import release_quota_if_allocated


def cancel_order(
    session: Session,
    *,
    client_order_id: str,
    pd_id: str,
    reason: str,
) -> dict:
    order = get_order_by_client_order_id(session, client_order_id, pd_id)
    if order is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND)

    locked_order = get_order_by_id_for_update(session, str(order["id"]))
    if locked_order is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND)

    if locked_order["status"] == "SETTLED":
        raise StateConflictError(ErrorCode.INVALID_ORDER_STATE)

    if locked_order["status"] in ("CANCELLED", "REJECTED"):
        return get_order_response(session, client_order_id=client_order_id, pd_id=pd_id)

    updated_order = update_order_status(
        session,
        order_id=str(locked_order["id"]),
        status="CANCELLED",
        rejection_reason_code=ErrorCode.INVALID_ORDER_STATE.value,
        rejection_reason=reason,
    )

    released_amount = release_quota_if_allocated(
        session,
        order_id=str(updated_order["id"]),
        release_reason="CANCELLED",
    )

    if released_amount is not None and released_amount > 0:
        post_cancel_entries(
            session,
            order_id=str(updated_order["id"]),
            currency=updated_order["currency"],
            amount=released_amount,
        )

    return get_order_response(session, client_order_id=client_order_id, pd_id=pd_id)
