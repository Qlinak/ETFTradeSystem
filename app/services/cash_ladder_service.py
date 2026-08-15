"""Domain service for cash ladder query orchestration."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.cash_ladder_repository import fetch_cash_ladder_rows, fetch_statement_timestamp


def get_cash_ladder_response(
    session: Session,
    *,
    as_of: date,
    horizon: int,
) -> dict:
    window_end = as_of + timedelta(days=horizon - 1)

    raw_rows = fetch_cash_ladder_rows(
        session,
        as_of=as_of,
        window_end=window_end,
    )

    rows: list[dict] = []
    totals: dict[tuple[date, str], dict[str, Decimal]] = {}

    for row in raw_rows:
        inflow = Decimal(row["inflow"])
        outflow = Decimal(row["outflow"])
        net = Decimal(row["net"])

        rows.append(
            {
                "settlementDate": row["settlement_date"],
                "productId": row["product_id"],
                "currency": row["currency"],
                "inflow": f"{inflow:.4f}",
                "outflow": f"{outflow:.4f}",
                "net": f"{net:.4f}",
            }
        )

        key = (row["settlement_date"], row["currency"])
        if key not in totals:
            totals[key] = {
                "inflow": Decimal("0"),
                "outflow": Decimal("0"),
            }
        totals[key]["inflow"] += inflow
        totals[key]["outflow"] += outflow

    totals_by_date_currency = [
        {
            "settlementDate": settlement_date,
            "currency": currency,
            "inflow": f"{values['inflow']:.4f}",
            "outflow": f"{values['outflow']:.4f}",
            "net": f"{(values['inflow'] - values['outflow']):.4f}",
        }
        for (settlement_date, currency), values in sorted(totals.items())
    ]

    generated_at = fetch_statement_timestamp(session).scalar_one()

    return {
        "asOf": as_of,
        "horizon": horizon,
        "windowEnd": window_end,
        "generatedAt": generated_at,
        "rows": rows,
        "totalsByDateCurrency": totals_by_date_currency,
    }
