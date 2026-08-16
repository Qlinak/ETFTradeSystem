"""Integration tests for operations blotter list, updates, and confirm action."""

from __future__ import annotations

from sqlalchemy import text

from tests.fixtures.db_fixtures import TEST_PREFIX, create_product_and_pd


def test_orders_blotter_list_returns_filtered_rows(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-BLOTTER"
    pd_id = f"{TEST_PREFIX}PD-BLOTTER"
    client_order_id = f"{TEST_PREFIX}ORDER-BLOTTER-001"

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
        "estimatedPrice": "15.5000",
        "currency": "USD",
    }
    submit = api_client.post("/api/v1/orders", json=payload)
    assert submit.status_code == 200

    trade_date = db_session.execute(text("SELECT CURRENT_DATE")).scalar_one()

    response = api_client.get(
        "/api/v1/orders",
        params={
            "tradeDate": str(trade_date),
            "pdId": pd_id,
            "sortBy": "submittedAt",
            "sortDir": "desc",
            "cursor": 0,
            "limit": 50,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"]
    found = [row for row in body["rows"] if row["clientOrderId"] == client_order_id]
    assert len(found) == 1
    assert found[0]["pdId"] == pd_id


def test_order_updates_returns_new_events(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-UPDATES"
    pd_id = f"{TEST_PREFIX}PD-UPDATES"
    client_order_id = f"{TEST_PREFIX}ORDER-UPDATES-001"

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
        "estimatedPrice": "12.0000",
        "currency": "USD",
    }
    submit = api_client.post("/api/v1/orders", json=payload)
    assert submit.status_code == 200

    first = api_client.get("/api/v1/orders-updates", params={"since": 0, "pdId": pd_id, "limit": 100})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["events"]

    observed_client_ids = {event["order"]["clientOrderId"] for event in first_body["events"]}
    assert client_order_id in observed_client_ids

    cancel = api_client.post(
        f"/api/v1/orders/{client_order_id}/cancel",
        json={"pdId": pd_id, "reason": "ops cancellation"},
    )
    assert cancel.status_code == 200

    second = api_client.get(
        "/api/v1/orders-updates",
        params={"since": first_body["nextSince"], "pdId": pd_id, "limit": 100},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert any(event["order"]["status"] == "CANCELLED" for event in second_body["events"])


def test_confirm_returns_422_when_quota_exceeded(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-CONFIRM"
    pd_id = f"{TEST_PREFIX}PD-CONFIRM"
    client_order_id = f"{TEST_PREFIX}ORDER-CONFIRM-001"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
        has_qdii_quota=True,
        daily_total_quota="10.0000",
    )

    db_session.execute(
        text(
            """
            INSERT INTO orders (
                client_order_id,
                product_id,
                pd_id,
                order_type,
                units,
                estimated_price,
                cash_amount,
                currency,
                status
            )
            VALUES (
                :client_order_id,
                :product_id,
                :pd_id,
                'CREATION',
                1000,
                100.0000,
                100.0000,
                'USD',
                'PENDING'
            )
            """
        ),
        {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "pd_id": pd_id,
        },
    )
    db_session.commit()

    response = api_client.post(
        f"/api/v1/orders/{client_order_id}/confirm",
        json={"pdId": pd_id},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["errorCode"] == "ERR_QUOTA_EXCEEDED"

    order_status = db_session.execute(
        text(
            """
            SELECT status
            FROM orders
            WHERE client_order_id = :client_order_id
              AND pd_id = :pd_id
            ORDER BY submitted_at DESC
            LIMIT 1
            """
        ),
        {"client_order_id": client_order_id, "pd_id": pd_id},
    ).scalar_one()
    assert order_status == "REJECTED"
