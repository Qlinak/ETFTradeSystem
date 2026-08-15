"""Repository for product and quota reads."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_product_for_update(session: Session, product_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT * FROM products WHERE id = :product_id FOR UPDATE"),
        {"product_id": product_id},
    ).mappings().first()
    return dict(row) if row else None


def get_product(session: Session, product_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT * FROM products WHERE id = :product_id"),
        {"product_id": product_id},
    ).mappings().first()
    return dict(row) if row else None


def get_pd(session: Session, pd_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT * FROM pds WHERE id = :pd_id"),
        {"pd_id": pd_id},
    ).mappings().first()
    return dict(row) if row else None


def get_daily_quota(session: Session, product_id: str, quota_date: date) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT *
            FROM product_daily_quota
            WHERE product_id = :product_id
              AND quota_date = :quota_date
            """
        ),
        {"product_id": product_id, "quota_date": quota_date},
    ).mappings().first()
    return dict(row) if row else None


def derive_settlement_date_from_db_time(session: Session, product_id: str) -> date | None:
    """Derive T+2 business settlement date using DB time, market timezone, and holidays."""

    return session.execute(
        text(
            """
            WITH p AS (
                SELECT id, market, market_timezone
                FROM products
                WHERE id = :product_id
            )
            SELECT d::date AS settlement_date
            FROM p
            CROSS JOIN LATERAL generate_series(
                ((statement_timestamp() AT TIME ZONE p.market_timezone)::date + 1)::timestamp,
                ((statement_timestamp() AT TIME ZONE p.market_timezone)::date + 20)::timestamp,
                interval '1 day'
            ) AS g(d)
            WHERE EXTRACT(ISODOW FROM d) < 6
              AND NOT EXISTS (
                  SELECT 1
                  FROM holiday_calendars h
                  WHERE h.market = p.market
                    AND h.holiday_date = d::date
              )
            ORDER BY d
            OFFSET 1
            LIMIT 1
            """
        ),
        {"product_id": product_id},
    ).scalar_one_or_none()
