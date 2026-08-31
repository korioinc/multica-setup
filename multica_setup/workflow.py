"""Composition of local and remote state into a reconciliation plan."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from .bindings import load_bindings
from .client import MulticaClient
from .context import discover_repository_root
from .domain import Plan
from .errors import ExportError
from .local_state import load_desired_state
from .normalization import _canonical_optional_text
from .planning import build_plan
from .remote_state import read_remote_state
from .snapshot import _validate_workspace


def plan_workspace(
    selector: str,
    expected_workspace_id: str | None = None,
    *,
    repository_root: Path | None = None,
) -> Plan:
    repository_root = repository_root or discover_repository_root()
    client = MulticaClient()
    workspace = _validate_workspace(client.workspace_get(selector))
    workspace = {
        **workspace,
        "name": unicodedata.normalize("NFC", workspace["name"]),
        "description": _canonical_optional_text(workspace["description"]),
        "context": _canonical_optional_text(workspace["context"]),
    }
    if expected_workspace_id is not None and workspace["id"] != expected_workspace_id:
        raise ExportError(
            "workspace get returned a different workspace than the interactive selection"
        )
    desired = load_desired_state(repository_root, workspace["id"])
    bindings = load_bindings(repository_root, workspace["id"])
    current = read_remote_state(client, workspace, desired, bindings)
    return build_plan(desired, current)
