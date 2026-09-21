"""pylon.exceptions - Custom exception hierarchy."""

from __future__ import annotations


class PylonError(Exception):
    """Base exception for pylon framework."""

    pass


class BindingError(PylonError):
    """Raised when request body binding fails (invalid JSON, etc.)."""

    pass


class ValidationError(PylonError):
    """Raised when pydantic model validation fails."""

    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__(self._format_errors(errors))

    def _format_errors(self, errors: list) -> str:
        """Format validation errors into human-readable string."""
        lines = ["Validation failed:"]
        for err in errors:
            loc = ".".join(str(l) for l in err.get("loc", []))
            msg = err.get("msg", "unknown error")
            lines.append(f"  - {loc}: {msg}")
        return "\n".join(lines)


class NotFoundError(PylonError):
    """Raised when a requested resource is not found."""

    pass


class UnauthorizedError(PylonError):
    """Raised when authentication/authorization fails."""

    pass


class ConflictError(PylonError):
    """Raised when a resource conflict occurs."""

    pass
