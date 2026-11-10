from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping

from .utils import LinkNotFoundError


FORM_EDIT_RECORD_VERSION = 1
FORM_EDIT_RECORD_FORMAT = "mechanicalsoup-form-edits"


class UnsupportedFormEditError(LinkNotFoundError, ValueError):
    """Raised when an edit cannot be represented in a form edit record."""


@dataclass
class FormEditConflict:
    """A structural reason why a form edit record cannot be replayed."""

    code: str
    message: str
    operation: int | None = None
    name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "operation": self.operation,
            "name": self.name,
            "details": dict(self.details),
        }


@dataclass
class FormEditApplyResult:
    """Result of applying a versioned form edit record to a form."""

    success: bool
    conflicts: list[FormEditConflict] = field(default_factory=list)
    applied_edits: int = 0

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "conflicts": [conflict.to_dict()
                          for conflict in self.conflicts],
            "applied_edits": self.applied_edits,
        }


class FormEditHistory:
    """Versioned, JSON-serializable records of explicit form edits."""

    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []

    def add(self, edit: Mapping[str, Any]) -> None:
        self.edits.append(dict(edit))

    def clear(self) -> None:
        self.edits.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": FORM_EDIT_RECORD_VERSION,
            "format": FORM_EDIT_RECORD_FORMAT,
            "edits": [dict(edit) for edit in self.edits],
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, record: Mapping[str, Any] | str) -> "FormEditHistory":
        edits = parse_form_edit_record(record)
        history = cls()
        history.edits = [dict(edit) for edit in edits]
        return history

    @classmethod
    def from_json(cls, record: str) -> "FormEditHistory":
        return cls.from_dict(record)


def parse_form_edit_record(record: Mapping[str, Any] | str) -> list[
        dict[str, Any]]:
    """Validate a record envelope and return a copied list of edits."""
    if isinstance(record, str):
        parsed = json.loads(record)
    else:
        parsed = record

    if not isinstance(parsed, Mapping):
        raise ValueError("Form edit record must be a JSON object")
    if parsed.get("version") != FORM_EDIT_RECORD_VERSION:
        raise ValueError("Unsupported form edit record version")
    if parsed.get("format") != FORM_EDIT_RECORD_FORMAT:
        raise ValueError("Unsupported form edit record format")

    edits = parsed.get("edits")
    if not isinstance(edits, list):
        raise ValueError("Form edit record must contain an edits list")

    copied_edits: list[dict[str, Any]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("Each form edit must be an object")
        copied_edits.append(dict(edit))
    return copied_edits
