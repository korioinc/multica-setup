"""User-facing errors raised by reconciliation workflows."""

from __future__ import annotations

from collections.abc import Sequence

from .domain import Operation


class ExportError(Exception):
    """A concise, user-actionable export failure."""


class ExportCancelled(Exception):
    """The operator cancelled before any local export work began."""


class ApplyExecutionError(ExportError):
    """A mutation failed after zero or more plan operations completed."""

    def __init__(
        self,
        operation: Operation,
        completed: Sequence[Operation],
        pending: Sequence[Operation],
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.operation = operation
        self.completed = tuple(completed)
        self.pending = tuple(pending)


class ApplyInterrupted(ApplyExecutionError):
    """The operator interrupted apply after execution had started."""
