"""Stable local identities for remotely identified resources."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from urllib import parse as urllib_parse

from .errors import ExportError
from .validation import _safe_slug


def _ascii_slug_base(name: str) -> str:
    folded = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:80]


def _unique_prefix_length(ids: Iterable[str]) -> int:
    hex_ids = [resource_id.replace("-", "") for resource_id in ids]
    for length in range(8, 33):
        if len({value[:length] for value in hex_ids}) == len(hex_ids):
            return length
    raise ExportError("duplicate resource UUIDs cannot produce unique slugs")


def _make_slugs(items: Sequence[dict[str, str]], kind: str) -> dict[str, str]:
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ExportError(f"{kind}: duplicate resource UUID")
    groups: dict[str, list[dict[str, str]]] = {}
    for item in items:
        groups.setdefault(_ascii_slug_base(item["name"]), []).append(item)

    result: dict[str, str] = {}
    for base, group in groups.items():
        needs_suffix = not base or len(group) > 1
        if not needs_suffix:
            result[group[0]["id"]] = base
            continue
        length = _unique_prefix_length(item["id"] for item in group)
        for item in group:
            suffix = item["id"].replace("-", "")[:length]
            if base:
                prefix = base[: 79 - length].rstrip("-")
                slug = f"{prefix}-{suffix}"
            else:
                slug = f"{kind}-{suffix}"
            result[item["id"]] = slug

    slugs = list(result.values())
    if len(slugs) != len(set(slugs)):
        raise ExportError(f"{kind}: slug collision after UUID disambiguation")
    return result


def _make_portable_keys(items: Sequence[dict[str, str]], kind: str) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for item in sorted(
        items, key=lambda value: (value["name"].casefold(), value["name"], value["id"])
    ):
        base = _ascii_slug_base(item["name"]) or kind
        candidate = base
        index = 2
        while candidate.casefold() in used:
            suffix = f"-{index}"
            candidate = f"{base[: 80 - len(suffix)].rstrip('-')}{suffix}"
            index += 1
        result[item["id"]] = candidate
        used.add(candidate.casefold())
    return result


def _autopilot_trigger_binding_slug(autopilot_slug: str, trigger_key: str) -> str:
    return (
        urllib_parse.quote(autopilot_slug, safe="")
        + "::"
        + urllib_parse.quote(trigger_key, safe="")
    )


def _parse_autopilot_trigger_binding_slug(value: str) -> tuple[str, str]:
    if "::" not in value:
        raise ExportError(
            "bindings.json.resources.autopilot-trigger: invalid trigger binding slug"
        )
    raw_autopilot_slug, raw_trigger_key = value.split("::", 1)
    autopilot_slug = _safe_slug(
        urllib_parse.unquote(raw_autopilot_slug),
        "autopilot trigger binding autopilot slug",
    )
    trigger_key = _safe_slug(
        urllib_parse.unquote(raw_trigger_key),
        "autopilot trigger binding trigger key",
    )
    if _autopilot_trigger_binding_slug(autopilot_slug, trigger_key) != value:
        raise ExportError(
            "bindings.json.resources.autopilot-trigger: non-canonical trigger binding slug"
        )
    return autopilot_slug, trigger_key
