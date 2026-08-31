"""Safe local filesystem primitives shared by configuration workflows."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from .errors import ExportError
from .normalization import _canonical_text


def _actual_directory(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _actual_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def _read_local_text(path: Path, label: str) -> str:
    if not _actual_file(path):
        raise ExportError(f"{label}: expected regular file")
    try:
        return _canonical_text(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ExportError(f"{label}: expected UTF-8 text") from exc


def _read_local_json(path: Path, label: str) -> Any:
    text = _read_local_text(path, label)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportError(f"{label}: invalid JSON") from exc


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
