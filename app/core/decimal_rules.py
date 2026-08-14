"""Shared fixed-precision rules for order validation and math."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


PRICE_SCALE = Decimal("0.00000001")
CASH_SCALE = Decimal("0.0001")


def parse_units(raw_units: str) -> int:
    """Parse units as an integer string without accepting floating-point forms."""

    if not raw_units.isdigit():
        raise ValueError("Units must be a positive integer string.")

    units = int(raw_units)
    if units <= 0:
        raise ValueError("Units must be greater than zero.")
    return units


def parse_price(raw_price: str) -> Decimal:
    """Parse and normalize estimated price to 8 decimal places."""

    try:
        price = Decimal(raw_price)
    except InvalidOperation as exc:
        raise ValueError("Estimated price must be a valid decimal string.") from exc

    if price < 0:
        raise ValueError("Estimated price cannot be negative.")

    return price.quantize(PRICE_SCALE, rounding=ROUND_HALF_UP)


def normalize_cash_amount(raw_amount: Decimal | str) -> Decimal:
    """Normalize cash amounts to 4 decimal places."""

    try:
        amount = raw_amount if isinstance(raw_amount, Decimal) else Decimal(raw_amount)
    except InvalidOperation as exc:
        raise ValueError("Cash amount must be a valid decimal string.") from exc

    if amount < 0:
        raise ValueError("Cash amount cannot be negative.")

    return amount.quantize(CASH_SCALE, rounding=ROUND_HALF_UP)