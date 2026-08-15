"""Domain service for synchronous order submission flow."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.decimal_rules import normalize_cash_amount, parse_price, parse_units
from app.core.errors import ERROR_MESSAGES, ErrorCode
from app.core.exceptions import NotFoundError, StateConflictError, ValidationError
from app.repositories.idempotency_repository import finalize_idempotency
from app.repositories.order_repository import insert_order, update_order_status
from app.repositories.product_repository import derive_settlement_date_from_db_time, get_pd, get_product_for_update
from app.schemas.orders import SubmitOrderRequest
from app.services.cutoff_service import is_before_cutoff
from app.services.idempotency_service import lock_idempotency_record
from app.services.ledger_service import post_confirm_entries
from app.services.order_query_service import get_order_response
from app.services.quota_service import reserve_quota_for_order


def _build_rejected_response(order: dict[str, Any]) -> dict[str, Any]:
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


def submit_order(session: Session, payload: SubmitOrderRequest) -> dict[str, Any]:
    """Submit order with idempotency, cutoff, quota, and ledger workflow."""

    pd = get_pd(session, payload.pdId)
    if pd is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND, "PD was not found.")

    product = get_product_for_update(session, payload.productId)
    if product is None:
        raise NotFoundError(ErrorCode.ORDER_NOT_FOUND, "Product was not found.")

    request_payload = payload.model_dump(mode="json")
    idempotency = lock_idempotency_record(
        session,
        pd_id=payload.pdId,
        client_order_id=payload.clientOrderId,
        payload=request_payload,
    )

    if idempotency["response_payload"] is not None:
        return idempotency["response_payload"]

    try:
        parsed_units = parse_units(payload.units)
        parsed_price = parse_price(payload.estimatedPrice)
    except ValueError as exc:
        raise ValidationError(ErrorCode.INVALID_UNITS, str(exc)) from exc

    if not is_before_cutoff(session, payload.productId):
        rejected = insert_order(
            session,
            client_order_id=payload.clientOrderId,
            product_id=payload.productId,
            pd_id=payload.pdId,
            order_type=payload.orderType,
            units=parsed_units,
            estimated_price=str(parsed_price),
            cash_amount="0.0000",
            currency=payload.currency,
            status="REJECTED",
            rejection_reason_code=ErrorCode.CUTOFF_PASSED.value,
            rejection_reason=ERROR_MESSAGES[ErrorCode.CUTOFF_PASSED],
        )
        response = _build_rejected_response(rejected)
        finalize_idempotency(
            session,
            pd_id=payload.pdId,
            client_order_id=payload.clientOrderId,
            order_id=str(rejected["id"]),
            final_status="REJECTED",
            response_payload=response,
        )
        return response

    units = parsed_units
    if units % int(product["creation_unit_size"]) != 0:
        raise ValidationError(ErrorCode.INVALID_UNITS)

    if payload.currency != product["currency"]:
        raise ValidationError(ErrorCode.INVALID_CURRENCY)

    price = parsed_price
    cash_amount = normalize_cash_amount((Decimal(units) / Decimal(product["creation_unit_size"])) * price)
    expected_settlement_date = derive_settlement_date_from_db_time(session, payload.productId)

    pending = insert_order(
        session,
        client_order_id=payload.clientOrderId,
        product_id=payload.productId,
        pd_id=payload.pdId,
        order_type=payload.orderType,
        units=units,
        estimated_price=str(price),
        cash_amount=str(cash_amount),
        currency=payload.currency,
        status="PENDING",
        settlement_date=expected_settlement_date,
    )

    try:
        if product["has_qdii_quota"]:
            reserve_quota_for_order(
                session,
                order_id=str(pending["id"]),
                product_id=payload.productId,
                quota_date=pending["server_received_at"].date(),
                currency=payload.currency,
                amount=cash_amount,
            )
    except StateConflictError:
        rejected = update_order_status(
            session,
            order_id=str(pending["id"]),
            status="REJECTED",
            rejection_reason_code=ErrorCode.QUOTA_EXCEEDED.value,
            rejection_reason=ERROR_MESSAGES[ErrorCode.QUOTA_EXCEEDED],
        )
        response = _build_rejected_response(rejected)
        finalize_idempotency(
            session,
            pd_id=payload.pdId,
            client_order_id=payload.clientOrderId,
            order_id=str(rejected["id"]),
            final_status="REJECTED",
            response_payload=response,
        )
        return response

    confirmed = update_order_status(
        session,
        order_id=str(pending["id"]),
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

    response = get_order_response(
        session,
        client_order_id=payload.clientOrderId,
        pd_id=payload.pdId,
    )

    finalize_idempotency(
        session,
        pd_id=payload.pdId,
        client_order_id=payload.clientOrderId,
        order_id=str(confirmed["id"]),
        final_status="CONFIRMED",
        response_payload=response,
    )

    return response
