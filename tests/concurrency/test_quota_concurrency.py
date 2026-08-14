"""Concurrency tests for quota reservation integrity."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from app.db.session import SessionLocal
from app.schemas.orders import SubmitOrderRequest
from app.services.order_submission_service import submit_order
from tests.fixtures.db_fixtures import TEST_PREFIX, create_product_and_pd


def test_eight_concurrent_qdii_requests_never_over_issue_quota(db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-CONCUR"
    pd_id = f"{TEST_PREFIX}PD-CONCUR"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
        has_qdii_quota=True,
        daily_total_quota="500.0000",
    )

    def _submit(index: int) -> str:
        session = SessionLocal()
        try:
            payload = SubmitOrderRequest(
                clientOrderId=f"{TEST_PREFIX}ORDER-CONCUR-{index}",
                productId=product_id,
                pdId=pd_id,
                orderType="CREATION",
                units="1000",
                estimatedPrice="100.0000",
                currency="USD",
            )
            response = submit_order(session, payload)
            session.commit()
            return response["status"]
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(_submit, range(8)))

    confirmed_count = statuses.count("CONFIRMED")
    rejected_count = statuses.count("REJECTED")

    assert confirmed_count == 5
    assert rejected_count == 3

    used_quota = db_session.execute(
        text(
            """
            SELECT used_quota
            FROM product_daily_quota
            WHERE product_id = :product_id
              AND quota_date = CURRENT_DATE
            """
        ),
        {"product_id": product_id},
    ).scalar_one()
    assert f"{used_quota:.4f}" == "500.0000"

    over_issued = db_session.execute(
        text(
            """
            SELECT used_quota > total_quota
            FROM product_daily_quota
            WHERE product_id = :product_id
              AND quota_date = CURRENT_DATE
            """
        ),
        {"product_id": product_id},
    ).scalar_one()
    assert over_issued is False
