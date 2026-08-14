"""Repository for idempotency record management."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def upsert_and_lock_idempotency(
    session: Session,
    *,
    pd_id: str,
    client_order_id: str,
    request_fingerprint: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    session.execute(
        text(
            """
            INSERT INTO order_idempotency (
                pd_id,
                client_order_id,
                request_fingerprint,
                request_payload
            )
            VALUES (
                :pd_id,
                :client_order_id,
                :request_fingerprint,
                CAST(:request_payload AS jsonb)
            )
            ON CONFLICT (pd_id, client_order_id) DO NOTHING
            """
        ),
        {
            "pd_id": pd_id,
            "client_order_id": client_order_id,
            "request_fingerprint": request_fingerprint,
            "request_payload": json.dumps(request_payload, separators=(",", ":"), sort_keys=True, default=str),
        },
    )

    row = session.execute(
        text(
            """
            SELECT *
            FROM order_idempotency
            WHERE pd_id = :pd_id
              AND client_order_id = :client_order_id
            FOR UPDATE
            """
        ),
        {"pd_id": pd_id, "client_order_id": client_order_id},
    ).mappings().one()
    return dict(row)


def finalize_idempotency(
    session: Session,
    *,
    pd_id: str,
    client_order_id: str,
    order_id: str,
    final_status: str,
    response_payload: dict[str, Any],
) -> None:
    session.execute(
        text(
            """
            UPDATE order_idempotency
            SET order_id = :order_id,
                response_payload = CAST(:response_payload AS jsonb),
                final_status = :final_status,
                finalized_at = statement_timestamp()
            WHERE pd_id = :pd_id
              AND client_order_id = :client_order_id
            """
        ),
        {
            "pd_id": pd_id,
            "client_order_id": client_order_id,
            "order_id": order_id,
            "final_status": final_status,
            "response_payload": json.dumps(response_payload, separators=(",", ":"), sort_keys=True, default=str),
        },
    )
