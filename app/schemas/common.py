"""Shared API response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ErrorCode


class ErrorResponse(BaseModel):
    """Standard API error payload."""

    model_config = ConfigDict(use_enum_values=True)

    errorCode: ErrorCode = Field(..., examples=[ErrorCode.QUOTA_EXCEEDED.value])
    message: str = Field(..., examples=["Requested cash amount exceeds remaining QDII quota for this product."])
    clientOrderId: str | None = Field(default=None, examples=["ORD-GS-20260814-00123"])
    timestamp: datetime = Field(..., examples=["2026-08-14T10:59:59.002Z"])
