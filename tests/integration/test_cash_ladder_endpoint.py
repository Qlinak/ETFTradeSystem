"""Integration tests for the cash ladder endpoint."""

from __future__ import annotations

from sqlalchemy import text

from tests.fixtures.db_fixtures import TEST_PREFIX, create_product_and_pd


def _insert_order(
    db_session,
    *,
    client_order_id: str,
    product_id: str,
    pd_id: str,
    order_type: str,
    status: str,
    cash_amount: str,
    submitted_at: str,
    settlement_date: str | None = None,
) -> None:
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
                status,
                submitted_at,
                server_received_at,
                settlement_date
            )
            VALUES (
                :client_order_id,
                :product_id,
                :pd_id,
                :order_type,
                1000,
                1.00000000,
                :cash_amount,
                'USD',
                :status,
                CAST(:submitted_at AS timestamptz),
                CAST(:submitted_at AS timestamptz),
                CAST(:settlement_date AS date)
            )
            """
        ),
        {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "pd_id": pd_id,
            "order_type": order_type,
            "cash_amount": cash_amount,
            "status": status,
            "submitted_at": submitted_at,
            "settlement_date": settlement_date,
        },
    )
    db_session.commit()


def test_cash_ladder_uses_holiday_calendar_and_returns_response_time(api_client, db_session) -> None:
    product_id = f"{TEST_PREFIX}PROD-LADDER"
    pd_id = f"{TEST_PREFIX}PD-LADDER"

    create_product_and_pd(
        db_session,
        product_id=product_id,
        pd_id=pd_id,
        currency="USD",
        creation_unit_size=1000,
        cutoff_time="23:59:59",
    )

    # Force Monday to be a holiday so T+2 from Friday lands on Wednesday.
    db_session.execute(
        text(
            """
            INSERT INTO holiday_calendars (market, holiday_date)
            VALUES ('US', '2026-08-17')
            ON CONFLICT (market, holiday_date) DO NOTHING
            """
        )
    )
    db_session.commit()

    _insert_order(
        db_session,
        client_order_id=f"{TEST_PREFIX}LADDER-CREATION",
        product_id=product_id,
        pd_id=pd_id,
        order_type="CREATION",
        status="CONFIRMED",
        cash_amount="100.0000",
        submitted_at="2026-08-14T10:00:00Z",
    )
    _insert_order(
        db_session,
        client_order_id=f"{TEST_PREFIX}LADDER-REDEMPTION",
        product_id=product_id,
        pd_id=pd_id,
        order_type="REDEMPTION",
        status="CONFIRMED",
        cash_amount="40.0000",
        submitted_at="2026-08-14T10:30:00Z",
    )
    _insert_order(
        db_session,
        client_order_id=f"{TEST_PREFIX}LADDER-SETTLED",
        product_id=product_id,
        pd_id=pd_id,
        order_type="CREATION",
        status="SETTLED",
        cash_amount="9999.0000",
        submitted_at="2026-08-14T11:00:00Z",
        settlement_date="2026-08-19",
    )

    response = api_client.get("/api/v1/cash-ladder", params={"asOf": "2026-08-14", "horizon": 7})
    assert response.status_code == 200

    payload = response.json()
    assert payload["asOf"] == "2026-08-14"
    assert payload["horizon"] == 7
    assert isinstance(payload["responseTimeMs"], int)
    assert payload["responseTimeMs"] >= 0

    target_row = next(
        row
        for row in payload["rows"]
        if row["settlementDate"] == "2026-08-19"
        and row["productId"] == product_id
        and row["currency"] == "USD"
    )
    assert target_row["inflow"] == "40.0000"
    assert target_row["outflow"] == "100.0000"
    assert target_row["net"] == "-60.0000"

    target_total = next(
        row
        for row in payload["totalsByDateCurrency"]
        if row["settlementDate"] == "2026-08-19" and row["currency"] == "USD"
    )
    assert target_total["inflow"] == "40.0000"
    assert target_total["outflow"] == "100.0000"
    assert target_total["net"] == "-60.0000"


def test_cash_ladder_rejects_invalid_horizon(api_client) -> None:
    response = api_client.get("/api/v1/cash-ladder", params={"asOf": "2026-08-14", "horizon": 0})
    assert response.status_code == 422
