"""HTTP endpoints for the ETF Trade System."""

import asyncio
import json
from datetime import date
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.core.errors import ErrorCode
from app.core.exceptions import DomainError, NotFoundError, StateConflictError, ValidationError
from app.db.session import get_db_session
from app.schemas.blotter import OrdersBlotterResponse, OrderUpdatesResponse
from app.schemas.cash_ladder import CashLadderResponse
from app.schemas.common import ErrorResponse
from app.schemas.orders import CancelOrderRequest, ConfirmOrderRequest, OrderResponse, SubmitOrderRequest
from app.schemas.products import ProductQuotaResponse
from app.services.order_blotter_service import get_order_updates as get_order_updates_service
from app.services.order_blotter_service import get_orders_blotter as get_orders_blotter_service
from app.services.cash_ladder_service import get_cash_ladder_response as get_cash_ladder_response_service
from app.services.order_cancellation_service import cancel_order as cancel_order_service
from app.services.order_confirmation_service import confirm_order as confirm_order_service
from app.services.order_query_service import get_order_response as get_order_response_service
from app.services.order_submission_service import submit_order as submit_order_service
from app.services.quota_service import get_quota_response as get_quota_response_service

router = APIRouter(prefix="/api/v1", tags=["ETF Orders"])


ORDER_SUCCESS_EXAMPLE = {
    "systemOrderId": "550e8400-e29b-41d4-a716-446655440000",
    "clientOrderId": "ORD-GS-20260814-00123",
    "productId": "PROD-HK-001",
    "pdId": "PD-GOLDMAN-HK",
    "orderType": "CREATION",
    "units": "2000000",
    "cashAmount": "100500000.0000",
    "currency": "HKD",
    "status": "CONFIRMED",
    "submittedAt": "2026-08-14T10:58:30.124Z",
    "settlementDate": "2026-08-18",
    "rejectionReason": None,
}

ORDER_ERROR_EXAMPLE = {
    "errorCode": "ERR_QUOTA_EXCEEDED",
    "message": "Requested cash amount exceeds remaining QDII quota for this product.",
    "clientOrderId": "ORD-GS-20260814-00123",
    "timestamp": "2026-08-14T10:59:59.002Z",
}

QUOTA_SUCCESS_EXAMPLE = {
    "productId": "PROD-QDII-RMB-01",
    "currency": "RMB",
    "totalDailyQuota": "500000000.0000",
    "remainingQuota": "45000000.0000",
    "cutoffTime": "11:00:00",
    "asOf": "2026-08-14T10:55:00.000Z",
}


def _error_response(detail: ErrorCode, client_order_id: str | None = None) -> ErrorResponse:
    return ErrorResponse(
        errorCode=detail,
        message=detail.value,
        clientOrderId=client_order_id,
        timestamp=datetime.now(timezone.utc),
    )


def _raise_domain_http_error(
    exc: DomainError,
    *,
    client_order_id: str | None = None,
) -> None:
    if isinstance(exc, NotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code == ErrorCode.QUOTA_EXCEEDED:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif isinstance(exc, (ValidationError, StateConflictError)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST

    raise HTTPException(
        status_code=status_code,
        detail=ErrorResponse(
            errorCode=exc.code,
            message=exc.message,
            clientOrderId=client_order_id,
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


@router.post(
    "/orders",
    summary="Submit an ETF order",
    response_model=OrderResponse,
    response_description="Final order outcome",
    responses={
        409: {"model": ErrorResponse, "description": "Idempotent or business rejection response", "content": {"application/json": {"example": ORDER_ERROR_EXAMPLE}}},
    },
)
def submit_order(
    payload: SubmitOrderRequest = Body(
        ...,
        examples=[
            {
                "clientOrderId": "ORD-GS-20260814-00123",
                "productId": "PROD-HK-001",
                "pdId": "PD-GOLDMAN-HK",
                "orderType": "CREATION",
                "units": "2000000",
                "estimatedPrice": "50.2500",
                "currency": "HKD",
            }
        ],
    ),
    session: Session = Depends(get_db_session),
) -> OrderResponse:
    try:
        response = submit_order_service(session, payload)
        session.commit()
        return OrderResponse.model_validate(response)
    except DomainError as exc:
        session.rollback()
        _raise_domain_http_error(exc, client_order_id=payload.clientOrderId)
    except SQLAlchemyError:
        session.rollback()
        raise


@router.get(
    "/orders/{client_order_id}",
    summary="Get an order by client order id",
    response_model=OrderResponse,
    response_description="Stored order outcome",
    responses={
        404: {"model": ErrorResponse, "description": "Order not found", "content": {"application/json": {"example": {**ORDER_ERROR_EXAMPLE, "errorCode": "ERR_ORDER_NOT_FOUND", "message": "Order was not found for the provided clientOrderId."}}}},
    },
)
def get_order(
    client_order_id: str = Path(..., description="Client-supplied idempotency key.", examples=["ORD-GS-20260814-00123"]),
    pd_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> OrderResponse:
    try:
        response = get_order_response_service(
            session,
            client_order_id=client_order_id,
            pd_id=pd_id,
        )
        return OrderResponse.model_validate(response)
    except DomainError as exc:
        _raise_domain_http_error(exc, client_order_id=client_order_id)


@router.post(
    "/orders/{client_order_id}/confirm",
    summary="Confirm an existing pending order",
    response_model=OrderResponse,
    response_description="Final order state after confirmation attempt",
    responses={
        404: {"model": ErrorResponse, "description": "Order not found"},
        409: {"model": ErrorResponse, "description": "Invalid order state for confirmation"},
        422: {"model": ErrorResponse, "description": "Quota exceeded during confirmation"},
    },
)
def confirm_order(
    client_order_id: str = Path(..., description="Client-supplied idempotency key.", examples=["ORD-GS-20260814-00123"]),
    payload: ConfirmOrderRequest = Body(
        ...,
        examples=[
            {
                "pdId": "PD-GOLDMAN-HK",
            }
        ],
    ),
    session: Session = Depends(get_db_session),
) -> OrderResponse:
    try:
        response = confirm_order_service(
            session,
            client_order_id=client_order_id,
            pd_id=payload.pdId,
        )
        session.commit()
        return OrderResponse.model_validate(response)
    except DomainError as exc:
        if exc.code == ErrorCode.QUOTA_EXCEEDED:
            session.commit()
        else:
            session.rollback()
        _raise_domain_http_error(exc, client_order_id=client_order_id)
    except SQLAlchemyError:
        session.rollback()
        raise


@router.post(
    "/orders/{client_order_id}/cancel",
    summary="Cancel an existing order",
    response_model=OrderResponse,
    response_description="Final order state after cancellation attempt",
    responses={
        404: {"model": ErrorResponse, "description": "Order not found"},
        409: {"model": ErrorResponse, "description": "Invalid order state for cancellation"},
    },
)
def cancel_order(
    client_order_id: str = Path(..., description="Client-supplied idempotency key.", examples=["ORD-GS-20260814-00123"]),
    payload: CancelOrderRequest = Body(
        ...,
        examples=[
            {
                "pdId": "PD-GOLDMAN-HK",
                "reason": "Algorithmic execution adjustment",
            }
        ],
    ),
    session: Session = Depends(get_db_session),
) -> OrderResponse:
    try:
        response = cancel_order_service(
            session,
            client_order_id=client_order_id,
            pd_id=payload.pdId,
            reason=payload.reason,
        )
        session.commit()
        return OrderResponse.model_validate(response)
    except DomainError as exc:
        session.rollback()
        _raise_domain_http_error(exc, client_order_id=client_order_id)
    except SQLAlchemyError:
        session.rollback()
        raise


@router.get(
    "/products/{product_id}/quota",
    summary="Get current product quota",
    response_model=ProductQuotaResponse,
    response_description="Current quota view for the requested product",
    responses={
        404: {"model": ErrorResponse, "description": "Product not found"},
    },
)
def get_product_quota(
    product_id: str = Path(..., description="Product identifier.", examples=["PROD-QDII-RMB-01"]),
    session: Session = Depends(get_db_session),
) -> ProductQuotaResponse:
    try:
        response = get_quota_response_service(session, product_id=product_id)
        return ProductQuotaResponse.model_validate(response)
    except DomainError as exc:
        _raise_domain_http_error(exc)


@router.get(
    "/cash-ladder",
    summary="Get expected cash ladder",
    response_model=CashLadderResponse,
    response_description="Expected inflow, outflow, and net amounts by settlement date, product, and currency",
)
def get_cash_ladder(
    as_of: date = Query(..., alias="asOf", description="Ladder start date (YYYY-MM-DD)."),
    horizon: int = Query(30, ge=1, le=90, description="Number of dates in the ladder window."),
    session: Session = Depends(get_db_session),
) -> CashLadderResponse:
    started = perf_counter()
    try:
        response = get_cash_ladder_response_service(session, as_of=as_of, horizon=horizon)
        response["responseTimeMs"] = int((perf_counter() - started) * 1000)
        return CashLadderResponse.model_validate(response)
    except DomainError as exc:
        _raise_domain_http_error(exc)


@router.get(
    "/orders",
    summary="Get operations blotter orders",
    response_model=OrdersBlotterResponse,
    response_description="Paged blotter rows for a single trade date",
)
def list_orders(
    trade_date: date = Query(..., alias="tradeDate", description="Trade date (YYYY-MM-DD)."),
    product_id: str | None = Query(default=None, alias="productId"),
    pd_id: str | None = Query(default=None, alias="pdId"),
    status_filter: str | None = Query(default=None, alias="status"),
    currency: str | None = Query(default=None),
    sort_by: str = Query(default="submittedAt", alias="sortBy"),
    sort_dir: str = Query(default="desc", alias="sortDir"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> OrdersBlotterResponse:
    response = get_orders_blotter_service(
        session,
        trade_date=trade_date,
        product_id=product_id,
        pd_id=pd_id,
        status=status_filter,
        currency=currency,
        sort_by=sort_by,
        sort_dir=sort_dir,
        cursor=cursor,
        limit=limit,
    )
    return OrdersBlotterResponse.model_validate(response)


@router.get(
    "/orders-updates",
    summary="Get order status updates since cursor",
    response_model=OrderUpdatesResponse,
    response_description="Incremental status changes for near-real-time fallback polling",
)
def get_order_updates(
    since: int = Query(default=0, ge=0),
    trade_date: date | None = Query(default=None, alias="tradeDate"),
    product_id: str | None = Query(default=None, alias="productId"),
    pd_id: str | None = Query(default=None, alias="pdId"),
    status_filter: str | None = Query(default=None, alias="status"),
    currency: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> OrderUpdatesResponse:
    response = get_order_updates_service(
        session,
        since_event_id=since,
        trade_date=trade_date,
        product_id=product_id,
        pd_id=pd_id,
        status=status_filter,
        currency=currency,
        limit=limit,
    )
    return OrderUpdatesResponse.model_validate(response)


@router.get("/orders-stream", summary="Stream order status updates (SSE)")
async def stream_order_updates(
    request: Request,
    since: int = Query(default=0, ge=0),
    trade_date: date | None = Query(default=None, alias="tradeDate"),
    product_id: str | None = Query(default=None, alias="productId"),
    pd_id: str | None = Query(default=None, alias="pdId"),
    status_filter: str | None = Query(default=None, alias="status"),
    currency: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> StreamingResponse:
    last_event_id = request.headers.get("last-event-id")
    cursor = since
    if last_event_id and last_event_id.isdigit():
        cursor = int(last_event_id)

    async def event_generator() -> object:
        current_cursor = cursor
        last_heartbeat = perf_counter()
        while True:
            if await request.is_disconnected():
                break

            session = SessionLocal()
            try:
                payload = get_order_updates_service(
                    session,
                    since_event_id=current_cursor,
                    trade_date=trade_date,
                    product_id=product_id,
                    pd_id=pd_id,
                    status=status_filter,
                    currency=currency,
                    limit=limit,
                )
            finally:
                session.close()

            for event in payload["events"]:
                current_cursor = max(current_cursor, event["eventId"])
                encoded = json.dumps(event, default=str)
                yield f"id: {event['eventId']}\nevent: order.status\ndata: {encoded}\n\n"

            now = perf_counter()
            if now - last_heartbeat >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
