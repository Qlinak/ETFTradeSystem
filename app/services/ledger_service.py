"""Domain service for posting ledger entries."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.ledger_repository import create_cash_movement, insert_double_entry


def post_confirm_entries(
    session: Session,
    *,
    order_id: str,
    currency: str,
    amount: Decimal,
) -> None:
    movement_id = create_cash_movement(session, order_id=order_id, event_type="CONFIRM")
    insert_double_entry(
        session,
        movement_id=movement_id,
        currency=currency,
        amount=amount,
        debit_account_code="PD_CASH",
        credit_account_code="SYSTEM_CASH",
    )


def post_cancel_entries(
    session: Session,
    *,
    order_id: str,
    currency: str,
    amount: Decimal,
) -> None:
    movement_id = create_cash_movement(session, order_id=order_id, event_type="CANCEL")
    insert_double_entry(
        session,
        movement_id=movement_id,
        currency=currency,
        amount=amount,
        debit_account_code="SYSTEM_CASH",
        credit_account_code="PD_CASH",
    )
