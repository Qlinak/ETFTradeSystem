"""Product and quota response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Currency


class ProductQuotaResponse(BaseModel):
    """Read model for product quota visibility."""

    model_config = ConfigDict(use_enum_values=True)

    productId: str = Field(..., examples=["PROD-QDII-RMB-01"])
    currency: Currency = Field(..., examples=[Currency.RMB.value])
    totalDailyQuota: str = Field(..., examples=["500000000.0000"])
    remainingQuota: str = Field(..., examples=["45000000.0000"])
    cutoffTime: str = Field(..., examples=["11:00:00"])
    asOf: datetime = Field(..., examples=["2026-08-14T10:55:00.000Z"])