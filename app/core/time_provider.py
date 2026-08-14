"""Authoritative time helpers for cutoff evaluation."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


MARKET_TIMEZONES = {
    "HK": "Asia/Hong_Kong",
    "US": "America/New_York",
    "CN": "Asia/Shanghai",
}


def market_timezone_for(market: str) -> str:
    """Resolve the IANA timezone string for a supported market code."""

    try:
        return MARKET_TIMEZONES[market]
    except KeyError as exc:
        raise ValueError(f"Unsupported market code: {market}") from exc


def localize_market_time(timestamp: datetime, market: str) -> datetime:
    """Convert an aware timestamp into the local market timezone."""

    timezone_name = market_timezone_for(market)
    return timestamp.astimezone(ZoneInfo(timezone_name))


DB_NOW_SQL = "SELECT statement_timestamp()"