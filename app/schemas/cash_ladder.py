"""Cash ladder request and response schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Currency


class CashLadderRow(BaseModel):
    """Product-level cash ladder row for one settlement date and currency."""

    model_config = ConfigDict(use_enum_values=True)

    settlementDate: date = Field(..., examples=["2026-08-18"])
    productId: str = Field(..., examples=["ETF001"])
    currency: Currency = Field(..., examples=[Currency.HKD.value])
    inflow: str = Field(..., examples=["12500000.0000"])
    outflow: str = Field(..., examples=["1500000.0000"])
    net: str = Field(..., examples=["11000000.0000"])


class CashLadderDateCurrencyTotal(BaseModel):
    """Date-currency aggregate totals across products."""

    model_config = ConfigDict(use_enum_values=True)

    settlementDate: date = Field(..., examples=["2026-08-18"])
    currency: Currency = Field(..., examples=[Currency.HKD.value])
    inflow: str = Field(..., examples=["13000000.0000"])
    outflow: str = Field(..., examples=["1500000.0000"])
    net: str = Field(..., examples=["11500000.0000"])


class CashLadderResponse(BaseModel):
    """Cash ladder response payload."""

    model_config = ConfigDict(use_enum_values=True)

    asOf: date = Field(..., examples=["2026-08-14"])
    horizon: int = Field(..., examples=[30])
    windowEnd: date = Field(..., examples=["2026-09-12"])
    generatedAt: datetime = Field(..., examples=["2026-08-14T11:02:05.442Z"])
    responseTimeMs: int = Field(..., examples=[12])
    rows: list[CashLadderRow]
    totalsByDateCurrency: list[CashLadderDateCurrencyTotal]
