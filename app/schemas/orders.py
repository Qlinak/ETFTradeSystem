"""Order request and response schemas."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Currency, OrderStatus, OrderType


class SubmitOrderRequest(BaseModel):
    """HTTP request schema for order submission."""

    model_config = ConfigDict(use_enum_values=True)

    clientOrderId: str = Field(..., examples=["ORD-GS-20260814-00123"])
    productId: str = Field(..., examples=["PROD-HK-001"])
    pdId: str = Field(..., examples=["PD-GOLDMAN-HK"])
    orderType: OrderType = Field(..., examples=[OrderType.CREATION.value])
    units: str = Field(..., examples=["2000000"], description="Integer quantity encoded as a string.")
    estimatedPrice: str = Field(..., examples=["50.2500"], description="Fixed-precision price encoded as a string.")
    currency: Currency = Field(..., examples=[Currency.HKD.value])


class OrderResponse(BaseModel):
    """Canonical order response schema."""

    model_config = ConfigDict(use_enum_values=True)

    systemOrderId: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    clientOrderId: str = Field(..., examples=["ORD-GS-20260814-00123"])
    productId: str = Field(..., examples=["PROD-HK-001"])
    pdId: str = Field(..., examples=["PD-GOLDMAN-HK"])
    orderType: OrderType = Field(..., examples=[OrderType.CREATION.value])
    units: str = Field(..., examples=["2000000"])
    cashAmount: str = Field(..., examples=["100500000.0000"])
    currency: Currency = Field(..., examples=[Currency.HKD.value])
    status: OrderStatus = Field(..., examples=[OrderStatus.CONFIRMED.value])
    submittedAt: datetime = Field(..., examples=["2026-08-14T10:58:30.124Z"])
    settlementDate: date | None = Field(default=None, examples=["2026-08-18"])
    rejectionReason: str | None = Field(default=None, examples=[None])


class CancelOrderRequest(BaseModel):
    """HTTP request schema for order cancellation."""

    pdId: str = Field(..., examples=["PD-GOLDMAN-HK"])
    reason: str = Field(..., examples=["Algorithmic execution adjustment"])


class ConfirmOrderRequest(BaseModel):
    """HTTP request schema for manual order confirmation."""

    pdId: str = Field(..., examples=["PD-GOLDMAN-HK"])
