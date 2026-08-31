"""Shared validation and deterministic fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from typing import Any

from .errors import ExportError


def _brief(text: str, limit: int = 300) -> str:
    line = " ".join(text.strip().splitlines())
    return line[:limit] + ("..." if len(line) > limit else "")


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ExportError(f"{field}: expected UUID string")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ExportError(f"{field}: expected UUID string") from exc


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ExportError(f"{field}: expected string")
    return value


def _nullable_string(value: Any, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ExportError(f"{field}: expected string or null")
    return value


def _object(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExportError(f"{endpoint}: expected object response")
    return value


def _array(value: Any, endpoint: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExportError(f"{endpoint}: expected array response")
    return value


def _required(record: dict[str, Any], key: str, endpoint: str) -> Any:
    if key not in record:
        raise ExportError(f"{endpoint}.{key}: missing required field")
    return record[key]


def _stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strict_keys(
    record: dict[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = required - set(record)
    if missing:
        raise ExportError(f"{label}: missing required field {sorted(missing)[0]}")
    unknown = set(record) - required - optional
    if unknown:
        raise ExportError(f"{label}: unknown field {sorted(unknown)[0]}")


def _safe_slug(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFC", _string(value, label))
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(unicodedata.category(char).startswith("C") for char in normalized)
    ):
        raise ExportError(f"{label}: unsafe slug")
    return normalized
