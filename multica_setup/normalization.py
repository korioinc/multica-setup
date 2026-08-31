"""Canonical text and runtime-name normalization."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _runtime_device_name(runtime: dict[str, Any] | None) -> str | None:
    if runtime is None:
        return None
    name = runtime["name"]
    if name:
        match = re.search(r"\(([^()]*)\)\s*$", name)
        if match and match.group(1).strip():
            return match.group(1).strip()
    custom_name = runtime["custom_name"]
    if custom_name and custom_name.strip():
        return custom_name.strip()
    if name and name.strip():
        return name.strip()
    return None


def _canonical_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _canonical_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _canonical_text(value)
    return normalized if normalized else None


def _terminal_text(value: str) -> str:
    return "".join(
        char
        if not unicodedata.category(char).startswith("C")
        else f"\\u{ord(char):04x}"
        for char in value
    )
