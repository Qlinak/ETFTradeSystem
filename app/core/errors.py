"""Application error codes and default messages."""

from enum import Enum


class ErrorCode(str, Enum):
    QUOTA_EXCEEDED = "ERR_QUOTA_EXCEEDED"
    CUTOFF_PASSED = "ERR_CUTOFF_PASSED"
    INVALID_UNITS = "ERR_INVALID_UNITS"
    INVALID_CURRENCY = "ERR_INVALID_CURRENCY"
    INVALID_ORDER_STATE = "ERR_INVALID_ORDER_STATE"
    ORDER_NOT_FOUND = "ERR_ORDER_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "ERR_IDEMPOTENCY_CONFLICT"
    NOT_IMPLEMENTED = "ERR_NOT_IMPLEMENTED"


ERROR_MESSAGES = {
    ErrorCode.QUOTA_EXCEEDED: "Requested cash amount exceeds remaining QDII quota for this product.",
    ErrorCode.CUTOFF_PASSED: "Order missed the product cutoff time.",
    ErrorCode.INVALID_UNITS: "Units must be a positive integer multiple of the product creation unit.",
    ErrorCode.INVALID_CURRENCY: "Order currency does not match the product currency.",
    ErrorCode.INVALID_ORDER_STATE: "Order cannot transition from its current state.",
    ErrorCode.ORDER_NOT_FOUND: "Order was not found for the provided clientOrderId.",
    ErrorCode.IDEMPOTENCY_CONFLICT: "Repeated clientOrderId was submitted with a different payload.",
    ErrorCode.NOT_IMPLEMENTED: "This endpoint is defined in Swagger but not implemented yet.",
}