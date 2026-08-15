"""HTTP endpoints for the ETF Trade System."""

from datetime import date
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import DomainError, NotFoundError, StateConflictError, ValidationError
from app.db.session import get_db_session
from app.schemas.cash_ladder import CashLadderResponse
from app.schemas.common import ErrorResponse
from app.schemas.orders import CancelOrderRequest, OrderResponse, SubmitOrderRequest
from app.schemas.products import ProductQuotaResponse
from app.services.cash_ladder_service import get_cash_ladder_response as get_cash_ladder_response_service
from app.services.order_cancellation_service import cancel_order as cancel_order_service
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
async def submit_order(
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
async def get_order(
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
    "/orders/{client_order_id}/cancel",
    summary="Cancel an existing order",
    response_model=OrderResponse,
    response_description="Final order state after cancellation attempt",
    responses={
        404: {"model": ErrorResponse, "description": "Order not found"},
        409: {"model": ErrorResponse, "description": "Invalid order state for cancellation"},
    },
)
async def cancel_order(
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
async def get_product_quota(
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
async def get_cash_ladder(
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
