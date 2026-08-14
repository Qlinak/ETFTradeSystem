"""Integration tests for endpoint-level order and quota behavior."""

from __future__ import annotations

from sqlalchemy import text

from tests.fixtures.db_fixtures import TEST_PREFIX, create_product_and_pd


def test_same_order_submitted_five_times_returns_same_result(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-IDEMP"
    pd_id = f"{TEST_PREFIX}PD-IDEMP"
    client_order_id = f"{TEST_PREFIX}ORDER-001"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
    )

    payload = {
        "clientOrderId": client_order_id,
        "productId": product_id,
        "pdId": pd_id,
        "orderType": "CREATION",
        "units": "2000",
        "estimatedPrice": "12.5000",
        "currency": "USD",
    }

    first_json = None
    for _ in range(5):
        response = api_client.post("/api/v1/orders", json=payload)
        assert response.status_code == 200
        if first_json is None:
            first_json = response.json()
        else:
            assert response.json() == first_json

    order_count = db_session.execute(
        text("SELECT COUNT(*) FROM orders WHERE pd_id = :pd_id AND client_order_id = :client_order_id"),
        {"pd_id": pd_id, "client_order_id": client_order_id},
    ).scalar_one()
    assert order_count == 1

    idem_count = db_session.execute(
        text("SELECT COUNT(*) FROM order_idempotency WHERE pd_id = :pd_id AND client_order_id = :client_order_id"),
        {"pd_id": pd_id, "client_order_id": client_order_id},
    ).scalar_one()
    assert idem_count == 1


def test_cancelling_twice_does_not_double_release_quota(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-CANCEL"
    pd_id = f"{TEST_PREFIX}PD-CANCEL"
    client_order_id = f"{TEST_PREFIX}ORDER-002"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
        has_qdii_quota=True,
        daily_total_quota="1000.0000",
    )

    submit_payload = {
        "clientOrderId": client_order_id,
        "productId": product_id,
        "pdId": pd_id,
        "orderType": "CREATION",
        "units": "1000",
        "estimatedPrice": "100.0000",
        "currency": "USD",
    }
    submit_response = api_client.post("/api/v1/orders", json=submit_payload)
    assert submit_response.status_code == 200
    assert submit_response.json()["status"] == "CONFIRMED"

    used_quota_after_submit = db_session.execute(
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
    assert f"{used_quota_after_submit:.4f}" == "100.0000"

    cancel_payload = {"pdId": pd_id, "reason": "Test cancel"}
    cancel_response_1 = api_client.post(f"/api/v1/orders/{client_order_id}/cancel", json=cancel_payload)
    cancel_response_2 = api_client.post(f"/api/v1/orders/{client_order_id}/cancel", json=cancel_payload)

    assert cancel_response_1.status_code == 200
    assert cancel_response_2.status_code == 200
    assert cancel_response_1.json()["status"] == "CANCELLED"
    assert cancel_response_2.json()["status"] == "CANCELLED"

    used_quota_after_cancel = db_session.execute(
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
    assert f"{used_quota_after_cancel:.4f}" == "0.0000"

    released_count = db_session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM quota_allocations qa
            JOIN orders o ON qa.order_id = o.id
            WHERE o.client_order_id = :client_order_id
              AND qa.released_at IS NOT NULL
            """
        ),
        {"client_order_id": client_order_id},
    ).scalar_one()
    assert released_count == 1


def test_orders_crossing_cutoff_are_rejected(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-CUTOFF"
    pd_id = f"{TEST_PREFIX}PD-CUTOFF"
    client_order_id = f"{TEST_PREFIX}ORDER-003"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="00:00:00",
    )

    payload = {
        "clientOrderId": client_order_id,
        "productId": product_id,
        "pdId": pd_id,
        "orderType": "CREATION",
        "units": "1000",
        "estimatedPrice": "50.0000",
        "currency": "USD",
    }
    response = api_client.post("/api/v1/orders", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REJECTED"
    assert "cutoff" in (body["rejectionReason"] or "").lower()


def test_adversarial_unit_values_are_rejected(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-UNITS"
    pd_id = f"{TEST_PREFIX}PD-UNITS"
    client_order_id = f"{TEST_PREFIX}ORDER-004"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
    )

    payload = {
        "clientOrderId": client_order_id,
        "productId": product_id,
        "pdId": pd_id,
        "orderType": "CREATION",
        "units": "1000.5",
        "estimatedPrice": "25.0000",
        "currency": "USD",
    }

    response = api_client.post("/api/v1/orders", json=payload)
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["errorCode"] == "ERR_INVALID_UNITS"


def test_double_entry_movements_remain_balanced(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-LEDGER"
    pd_id = f"{TEST_PREFIX}PD-LEDGER"
    client_order_id = f"{TEST_PREFIX}ORDER-005"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
    )

    payload = {
        "clientOrderId": client_order_id,
        "productId": product_id,
        "pdId": pd_id,
        "orderType": "CREATION",
        "units": "1000",
        "estimatedPrice": "80.0000",
        "currency": "USD",
    }
    response = api_client.post("/api/v1/orders", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMED"

    imbalances = db_session.execute(
        text(
            """
            SELECT le.movement_id, le.currency, SUM(le.signed_amount) AS balance
            FROM ledger_entries le
            JOIN cash_movements cm ON cm.id = le.movement_id
            JOIN orders o ON o.id = cm.order_id
            WHERE o.client_order_id = :client_order_id
            GROUP BY le.movement_id, le.currency
            HAVING SUM(le.signed_amount) <> 0
            """
        ),
        {"client_order_id": client_order_id},
    ).all()
    assert imbalances == []
