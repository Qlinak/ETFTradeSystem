"""Domain service for idempotency fingerprinting and replay handling."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ErrorCode
from app.core.exceptions import StateConflictError
from app.repositories.idempotency_repository import upsert_and_lock_idempotency


def compute_request_fingerprint(payload: dict[str, Any]) -> str:
    canonical_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def lock_idempotency_record(
    session: Session,
    *,
    pd_id: str,
    client_order_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = compute_request_fingerprint(payload)
    record = upsert_and_lock_idempotency(
        session,
        pd_id=pd_id,
        client_order_id=client_order_id,
        request_fingerprint=fingerprint,
        request_payload=payload,
    )

    if record["request_fingerprint"] != fingerprint:
        raise StateConflictError(ErrorCode.IDEMPOTENCY_CONFLICT)

    return record
