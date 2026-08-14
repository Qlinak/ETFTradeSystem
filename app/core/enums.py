"""Application-wide enums and status constants."""

from enum import Enum


class OrderType(str, Enum):
    CREATION = "CREATION"
    REDEMPTION = "REDEMPTION"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    SETTLED = "SETTLED"


class Currency(str, Enum):
    HKD = "HKD"
    USD = "USD"
    RMB = "RMB"
    CNH = "CNH"
