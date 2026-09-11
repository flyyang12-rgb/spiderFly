"""Structured task errors; depends only on the Python standard library."""
from typing import Any

class TaskError(Exception):
    """A public error without input values or the handler's exception text."""

    def __init__(
        self,
        code: str,
        operation: str,
        stage: str,
        message: str,
        *,
        fields: tuple[tuple[tuple[str | int, ...], str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.stage = stage
        self.fields = fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "operation": self.operation,
            "stage": self.stage,
            "message": str(self),
            "fields": [
                {"path": list(path), "type": error_type}
                for path, error_type in self.fields
            ],
        }
