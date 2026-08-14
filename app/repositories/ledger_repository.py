"""Repository for ledger movement persistence."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session


def create_cash_movement(session: Session, *, order_id: str, event_type: str) -> str:
    movement_id = session.execute(
        text(
            """
            INSERT INTO cash_movements (order_id, event_type)
            VALUES (:order_id, :event_type)
            RETURNING id
            """
        ),
        {"order_id": order_id, "event_type": event_type},
    ).scalar_one()
    return str(movement_id)


def insert_double_entry(
    session: Session,
    *,
    movement_id: str,
    currency: str,
    amount: Decimal,
    debit_account_code: str,
    credit_account_code: str,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO ledger_entries (movement_id, entry_role, account_code, currency, amount)
            VALUES
                (:movement_id, 'DEBIT', :debit_account_code, :currency, :amount),
                (:movement_id, 'CREDIT', :credit_account_code, :currency, :amount)
            """
        ),
        {
            "movement_id": movement_id,
            "debit_account_code": debit_account_code,
            "credit_account_code": credit_account_code,
            "currency": currency,
            "amount": amount,
        },
    )
