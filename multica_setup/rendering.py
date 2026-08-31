"""Human-readable, redacted rendering of reconciliation plans."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .domain import Plan
from .normalization import _terminal_text

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"


def _text_digest(value: str | None) -> str:
    if value is None:
        return "<absent>"
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    lines = len(value.splitlines())
    return f"sha256:{digest} bytes:{len(encoded)} lines:{lines}"


def _sensitive_digest(value: Any) -> str:
    if value is None or isinstance(value, str):
        return _text_digest(value)
    return _text_digest(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _safe_plan_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(_terminal_text(value), ensure_ascii=False)
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_safe_plan_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return _terminal_text(str(value))


def _ansi(value: str, code: str, enabled: bool) -> str:
    return f"{code}{value}{ANSI_RESET}" if enabled else value


def render_plan(plan: Plan, *, color: bool = False, executing: bool = False) -> str:
    title = f"Multica plan for workspace {plan.workspace_id}"
    lines = [_ansi(title, ANSI_BOLD, color), ""]
    if plan.unmanaged_webhook_triggers:
        lines.extend(
            (
                "Note: "
                f"{plan.unmanaged_webhook_triggers} webhook trigger(s) are "
                "manually managed and will be preserved.",
                "",
            )
        )
    add_count = 0
    change_count = 0
    destroy_count = 0
    for operation in plan.operations:
        if operation.action == "create":
            symbol = "+"
            add_count += 1
        elif operation.action in {"update", "restore"}:
            symbol = "~"
            change_count += 1
        else:
            symbol = "-"
            destroy_count += 1
        symbol_color = {
            "+": ANSI_GREEN,
            "~": ANSI_YELLOW,
            "-": ANSI_RED,
        }[symbol]
        rendered_symbol = _ansi(symbol, symbol_color, color)
        name = json.dumps(_terminal_text(operation.name), ensure_ascii=False)
        lines.append(
            f"{rendered_symbol} {operation.resource_type} {name} ({operation.action})"
        )
        if operation.remote_id is not None:
            lines.append(f"    remote_id: {operation.remote_id}")
        for change in operation.changes:
            before = (
                _sensitive_digest(change.before)
                if change.sensitive
                else _safe_plan_value(change.before)
            )
            after = (
                _sensitive_digest(change.after)
                if change.sensitive
                else _safe_plan_value(change.after)
            )
            field = _terminal_text(change.field)
            if change.before == change.after and operation.action == "create":
                lines.append(f"    {field}: {after} (explicit)")
            elif change.before == change.after:
                lines.append(f"    {field}: {after} (preserved)")
            else:
                lines.append(f"    {field}: {before} -> {after}")
        for file_change in operation.file_changes:
            path = json.dumps(_terminal_text(file_change.path), ensure_ascii=False)
            lines.append(
                f"    file {path} ({file_change.action}): "
                f"{_text_digest(file_change.before)} -> {_text_digest(file_change.after)}"
            )
        lines.append("")
    if not plan.operations:
        lines.extend((_ansi("No changes.", ANSI_GREEN, color), ""))
    if destroy_count:
        message = (
            "Warning: apply will execute the destructive actions shown above."
            if executing
            else "Destructive candidates are preview-only; plan does not modify remote state."
        )
        lines.append(
            _ansi(
                message,
                ANSI_RED,
                color,
            )
        )
        if executing:
            lines.append(
                _ansi(
                    "Squad archive can transfer open issues; agent archive can interrupt "
                    "work; autopilot archive stops automation; quick action archive hides "
                    "a workspace action; schedule trigger deletion is irreversible, as "
                    "are broken-action and skill deletion.",
                    ANSI_RED,
                    color,
                )
            )
    summary = (
        f"Plan: {_ansi(f'{add_count} to add', ANSI_GREEN, color)}, "
        f"{_ansi(f'{change_count} to change', ANSI_YELLOW, color)}, "
        f"{_ansi(f'{destroy_count} to destroy', ANSI_RED, color)}."
    )
    lines.append(summary)
    return "\n".join(lines) + "\n"
