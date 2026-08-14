"""Domain service for cutoff validation using database time."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def is_before_cutoff(session: Session, product_id: str) -> bool:
    """Return True when current DB statement time is strictly earlier than product cutoff."""

    return bool(
        session.execute(
            text(
                """
                SELECT (
                    (statement_timestamp() AT TIME ZONE p.market_timezone)::time < p.cutoff_time
                ) AS accepted
                FROM products p
                WHERE p.id = :product_id
                """
            ),
            {"product_id": product_id},
        ).scalar_one()
    )
