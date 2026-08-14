"""Domain-level exceptions with stable error codes."""

from __future__ import annotations

from app.core.errors import ERROR_MESSAGES, ErrorCode


class DomainError(Exception):
    """Base class for expected business rule failures."""

    def __init__(self, code: ErrorCode, message: str | None = None):
        self.code = code
        self.message = message or ERROR_MESSAGES[code]
        super().__init__(self.message)


class NotFoundError(DomainError):
    """Raised when a domain object cannot be found."""


class ValidationError(DomainError):
    """Raised when domain-level input validation fails."""


class StateConflictError(DomainError):
    """Raised for invalid state transitions or idempotency conflicts."""
