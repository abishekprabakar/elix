"""Engine errors. Each maps to one API error code (see specs/04-api.md)."""

from __future__ import annotations

from typing import Any, Optional

from elix.contracts import Denied, RailError

__all__ = ["Denied", "RailError", "Conflict", "NotFound", "Invalid", "PeriodClosed"]


class Conflict(Exception):
    """The action is allowed for this actor but not in the item's current state (HTTP 409)."""

    def __init__(self, message: str, audit_seq: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.audit_seq = audit_seq
        self.details = details


class NotFound(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Invalid(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PeriodClosed(Denied):
    """R30. The gateway refuses to post into a closed period."""

    def __init__(self, message: str, audit_seq: Optional[int] = None):
        super().__init__(message, rule_id="R30", audit_seq=audit_seq)
