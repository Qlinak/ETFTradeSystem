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
