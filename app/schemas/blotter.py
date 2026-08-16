"""Schemas for operations blotter list and near-real-time updates."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class OrderBlotterRow(BaseModel):
    systemOrderId: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    clientOrderId: str = Field(..., examples=["ORD-GS-20260814-00123"])
    productId: str = Field(..., examples=["PROD-HK-001"])
    pdId: str = Field(..., examples=["PD-GOLDMAN-HK"])
    orderType: str = Field(..., examples=["CREATION"])
    units: str = Field(..., examples=["2000000"])
    estimatedPrice: str = Field(..., examples=["50.25000000"])
    cashAmount: str = Field(..., examples=["100500000.0000"])
    currency: str = Field(..., examples=["HKD"])
    status: str = Field(..., examples=["CONFIRMED"])
    submittedAt: datetime = Field(..., examples=["2026-08-14T10:58:30.124Z"])
    settlementDate: date | None = Field(default=None, examples=["2026-08-18"])
    rejectionReason: str | None = Field(default=None)
    updatedAt: datetime = Field(..., examples=["2026-08-14T10:58:31.124Z"])
    lastEventId: int | None = Field(default=None, examples=[12345])


class OrdersBlotterResponse(BaseModel):
    tradeDate: date
    sortBy: str
    sortDir: str
    cursor: int
    nextCursor: int | None = None
    hasMore: bool
    serverTime: datetime
    rows: list[OrderBlotterRow]


class OrderUpdateEvent(BaseModel):
    eventId: int
    eventType: str
    occurredAt: datetime
    order: OrderBlotterRow


class OrderUpdatesResponse(BaseModel):
    since: int
    nextSince: int
    hasMore: bool
    serverTime: datetime
    events: list[OrderUpdateEvent]
