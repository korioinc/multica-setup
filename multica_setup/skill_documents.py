"""Skill frontmatter parsing, rendering, and support-path validation."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import PurePosixPath

from .errors import ExportError
from .normalization import _canonical_optional_text


def _strip_yaml_comment(raw_value: str) -> str:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_value):
        char = raw_value[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = None
        elif quote == "'":
            if char == "'":
                if index + 1 < len(raw_value) and raw_value[index + 1] == "'":
                    index += 1
                else:
                    quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "#" and (index == 0 or raw_value[index - 1].isspace()):
            return raw_value[:index].rstrip()
        index += 1
    return raw_value.strip()


def _yaml_scalar_kind(raw_value: str) -> str:
    value = _strip_yaml_comment(raw_value)
    if not value:
        return "null"
    if re.fullmatch(r"[>|][+-]?[1-9]?", value):
        return "string"
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return "invalid"
        if isinstance(parsed, str):
            return "string"
        return "null" if parsed is None else "other"
    if value.startswith("'"):
        if len(value) >= 2 and value.endswith("'"):
            return "string"
        return "invalid"

    lowered = value.casefold()
    if lowered in {"null", "~"}:
        return "null"
    if lowered in {"true", "false"}:
        return "other"
    if value.startswith(("[", "{")) or re.fullmatch(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value
    ):
        return "other"
    return "string"


def _frontmatter_field_kind(header: str, key: str) -> str:
    matches = re.findall(rf"(?m)^{re.escape(key)}[ \t]*:[ \t]*(.*)$", header)
    if len(matches) != 1:
        return "missing" if not matches else "duplicate"
    return _yaml_scalar_kind(matches[0])


def _skill_markdown(name: str, description: str | None, content: str) -> str:
    if content.startswith("---"):
        lines = content.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise ExportError(f"skill {name!r}: malformed frontmatter opening")
        closing = next(
            (
                index
                for index, line in enumerate(lines[1:], 1)
                if line.rstrip("\r\n") == "---"
            ),
            None,
        )
        if closing is None:
            raise ExportError(f"skill {name!r}: unclosed frontmatter")
        header = "".join(lines[1:closing])
        name_kind = _frontmatter_field_kind(header, "name")
        description_kind = _frontmatter_field_kind(header, "description")
        if name_kind != "string" or description_kind not in {"string", "null"}:
            raise ExportError(
                f"skill {name!r}: frontmatter requires string name and string or null description"
            )
        return content
    return (
        "---\n"
        f"name: {json.dumps(name, ensure_ascii=False)}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n"
        f"{content}"
    )


def _yaml_scalar_value(raw_value: str, label: str) -> str | None:
    value = _strip_yaml_comment(raw_value)
    if not value or value.casefold() in {"null", "~"}:
        return None
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ExportError(f"{label}: invalid quoted string") from exc
        if not isinstance(parsed, str):
            raise ExportError(f"{label}: expected string or null")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ExportError(f"{label}: invalid quoted string")
        return value[1:-1].replace("''", "'")
    if (
        value.casefold() in {"true", "false"}
        or value.startswith(("[", "{"))
        or re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)
    ):
        raise ExportError(f"{label}: expected string or null")
    return value


def _yaml_block_value(
    lines: Sequence[str], start: int, indicator: str, label: str
) -> tuple[str, int]:
    collected: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        collected.append(line)
        index += 1
    nonblank = [len(line) - len(line.lstrip(" ")) for line in collected if line.strip()]
    if any(line.startswith("\t") for line in collected if line.strip()):
        raise ExportError(f"{label}: invalid block string")
    if nonblank:
        explicit_indent = next(
            (int(char) for char in indicator[1:] if char.isdigit()), None
        )
        indent = explicit_indent if explicit_indent is not None else min(nonblank)
        if indent <= 0 or any(value < indent for value in nonblank):
            raise ExportError(f"{label}: invalid block indentation")
        values = [line[indent:] if line.strip() else "" for line in collected]
    else:
        values = ["" for _ in collected]
    style = indicator[0]
    chomping = next((char for char in indicator[1:] if char in "+-"), "")
    if style == "|":
        result = "\n".join(values) + ("\n" if values else "")
    else:
        result = ""
        previous: str | None = None
        blank_count = 0
        for value in values:
            if not value:
                blank_count += 1
                continue
            if previous is None:
                result += "\n" * blank_count + value
            elif blank_count:
                result += "\n" * blank_count + value
            elif previous.startswith((" ", "\t")) or value.startswith((" ", "\t")):
                result += "\n" + value
            else:
                result += " " + value
            previous = value
            blank_count = 0
        if previous is None:
            result = "\n" * blank_count
        else:
            result += "\n" * (blank_count + 1)
    if not nonblank and chomping != "+":
        result = ""
    elif chomping == "-":
        result = result.rstrip("\n")
    elif chomping != "+" and result:
        result = result.rstrip("\n") + "\n"
    return result, index


def _skill_frontmatter(document: str, label: str) -> tuple[str, str | None]:
    lines = document.splitlines()
    if not lines or lines[0] != "---":
        raise ExportError(f"{label}: SKILL.md requires frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ExportError(f"{label}: unclosed frontmatter") from exc

    found: dict[str, str | None] = {}
    index = 1
    while index < closing:
        line = lines[index]
        match = re.fullmatch(r"(name|description)[ \t]*:[ \t]*(.*)", line)
        if match:
            key, raw_value = match.groups()
            if key in found:
                raise ExportError(f"{label}: duplicate frontmatter {key}")
            scalar = _strip_yaml_comment(raw_value)
            if re.fullmatch(r"[>|][+-]?[1-9]?", scalar):
                value, next_index = _yaml_block_value(
                    lines[:closing], index + 1, scalar, f"{label}.{key}"
                )
                found[key] = value
                index = next_index
                continue
            found[key] = _yaml_scalar_value(raw_value, f"{label}.{key}")
        index += 1

    if "name" not in found or not isinstance(found["name"], str) or not found["name"]:
        raise ExportError(f"{label}: frontmatter requires string name")
    if "description" not in found:
        raise ExportError(f"{label}: frontmatter requires string or null description")
    return (
        unicodedata.normalize("NFC", found["name"]),
        _canonical_optional_text(found["description"]),
    )


def _validate_skill_paths(files: Sequence[dict[str, str]]) -> None:
    normalized_paths: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for item in files:
        path = item["path"]
        if (
            not path
            or "\\" in path
            or any(unicodedata.category(char) == "Cc" for char in path)
        ):
            raise ExportError(f"skill file path is unsafe: {path!r}")
        pure = PurePosixPath(path)
        parts = tuple(path.split("/"))
        if pure.is_absolute() or any(part in ("", ".", "..") for part in parts):
            raise ExportError(f"skill file path is unsafe: {path!r}")
        normalized = unicodedata.normalize("NFC", path).casefold()
        normalized_parts = tuple(
            unicodedata.normalize("NFC", part).casefold() for part in parts
        )
        if normalized_parts[0] == "skill.md":
            raise ExportError("skill file path collides with reserved SKILL.md")
        if normalized in seen:
            raise ExportError(f"duplicate skill file path: {path!r}")
        seen.add(normalized)
        normalized_paths.append((path, normalized_parts))

    for index, (path, parts) in enumerate(normalized_paths):
        for other_path, other_parts in normalized_paths[index + 1 :]:
            shortest = min(len(parts), len(other_parts))
            if parts[:shortest] == other_parts[:shortest]:
                raise ExportError(
                    f"skill file/directory path collision: {path!r} and {other_path!r}"
                )
