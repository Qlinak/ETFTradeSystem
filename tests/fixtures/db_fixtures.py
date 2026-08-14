"""Shared DB helpers for integration and concurrency tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app


TEST_PREFIX = "TEST-PHASE6-"


@pytest.fixture(scope="session")
def api_client() -> Generator[TestClient, None, None]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_test_rows() -> Generator[None, None, None]:
    _purge_test_rows()
    yield
    _purge_test_rows()


def _purge_test_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(
            text(
                """
                DELETE FROM ledger_entries
                WHERE movement_id IN (
                    SELECT id
                    FROM cash_movements
                    WHERE order_id IN (
                        SELECT id
                        FROM orders
                        WHERE client_order_id LIKE :prefix
                           OR product_id LIKE :prefix
                           OR pd_id LIKE :prefix
                    )
                )
                """
            ),
            {"prefix": f"{TEST_PREFIX}%"},
        )
        session.execute(
            text(
                """
                DELETE FROM cash_movements
                WHERE order_id IN (
                    SELECT id
                    FROM orders
                    WHERE client_order_id LIKE :prefix
                       OR product_id LIKE :prefix
                       OR pd_id LIKE :prefix
                )
                """
            ),
            {"prefix": f"{TEST_PREFIX}%"},
        )
        session.execute(
            text(
                """
                DELETE FROM quota_allocations
                WHERE order_id IN (
                    SELECT id
                    FROM orders
                    WHERE client_order_id LIKE :prefix
                       OR product_id LIKE :prefix
                       OR pd_id LIKE :prefix
                )
                """
            ),
            {"prefix": f"{TEST_PREFIX}%"},
        )
        session.execute(
            text("DELETE FROM order_idempotency WHERE client_order_id LIKE :prefix OR pd_id LIKE :prefix"),
            {"prefix": f"{TEST_PREFIX}%"},
        )
        session.execute(
            text(
                """
                DELETE FROM orders
                WHERE client_order_id LIKE :prefix
                   OR product_id LIKE :prefix
                   OR pd_id LIKE :prefix
                """
            ),
            {"prefix": f"{TEST_PREFIX}%"},
        )
        session.execute(text("DELETE FROM product_daily_quota WHERE product_id LIKE :prefix"), {"prefix": f"{TEST_PREFIX}%"})
        session.execute(text("DELETE FROM products WHERE id LIKE :prefix"), {"prefix": f"{TEST_PREFIX}%"})
        session.execute(text("DELETE FROM pds WHERE id LIKE :prefix"), {"prefix": f"{TEST_PREFIX}%"})
        session.commit()
    finally:
        session.close()


def create_product_and_pd(
    session: Session,
    *,
    product_id: str,
    pd_id: str,
    currency: str = "USD",
    creation_unit_size: int = 1000,
    cutoff_time: str = "23:59:59",
    has_qdii_quota: bool = False,
    daily_total_quota: str = "0.0000",
) -> None:
    session.execute(
        text(
            """
            INSERT INTO pds (id, name)
            VALUES (:pd_id, :name)
            """
        ),
        {"pd_id": pd_id, "name": f"{pd_id} Name"},
    )
    session.execute(
        text(
            """
            INSERT INTO products (
                id,
                name,
                market,
                market_timezone,
                currency,
                creation_unit_size,
                cutoff_time,
                has_qdii_quota,
                daily_total_quota,
                remaining_quota
            )
            VALUES (
                :product_id,
                :name,
                'US',
                'UTC',
                :currency,
                :creation_unit_size,
                CAST(:cutoff_time AS time),
                :has_qdii_quota,
                :daily_total_quota,
                :daily_total_quota
            )
            """
        ),
        {
            "product_id": product_id,
            "name": f"{product_id} Name",
            "currency": currency,
            "creation_unit_size": creation_unit_size,
            "cutoff_time": cutoff_time,
            "has_qdii_quota": has_qdii_quota,
            "daily_total_quota": daily_total_quota,
        },
    )
    session.commit()
