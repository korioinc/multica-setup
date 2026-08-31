"""Workspace-scoped remote identity binding persistence."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from .constants import (
    BINDING_RESOURCE_TYPES,
    BINDING_RESOURCE_TYPES_V1,
    BINDING_RESOURCE_TYPES_V2,
    BINDING_SCHEMA_VERSION,
)
from .domain import ResourceBinding
from .errors import ExportError
from .filesystem import _actual_directory, _actual_file, _read_local_json
from .identity import _parse_autopilot_trigger_binding_slug
from .validation import (
    _brief,
    _canonical_uuid,
    _object,
    _safe_slug,
    _strict_keys,
    _string,
)


def _binding_path(repository_root: Path, workspace_id: str) -> Path:
    return repository_root / ".cache" / "workspaces" / workspace_id / "bindings.json"


def _checked_binding_directory(path: Path, label: str) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    if not _actual_directory(path):
        raise ExportError(f"{label}: expected regular directory")
    return True


def load_bindings(
    repository_root: Path, workspace_id: str
) -> tuple[ResourceBinding, ...]:
    canonical_workspace_id = _canonical_uuid(workspace_id, "binding workspace id")
    cache_root = repository_root / ".cache"
    workspaces_root = cache_root / "workspaces"
    workspace_root = workspaces_root / canonical_workspace_id
    for path, label in (
        (cache_root, "binding cache root"),
        (workspaces_root, "binding workspaces root"),
        (workspace_root, "binding workspace root"),
    ):
        if not _checked_binding_directory(path, label):
            return ()

    path = workspace_root / "bindings.json"
    if not path.exists() and not path.is_symlink():
        return ()
    raw = _object(_read_local_json(path, "bindings.json"), "bindings.json")
    _strict_keys(
        raw,
        {"version", "workspace_id", "resources"},
        set(),
        "bindings.json",
    )
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ExportError("bindings.json.version: expected integer")
    if version not in {1, 2, BINDING_SCHEMA_VERSION}:
        raise ExportError(f"bindings.json.version: unsupported version {version}")
    stored_workspace_id = _canonical_uuid(
        raw["workspace_id"], "bindings.json.workspace_id"
    )
    if stored_workspace_id != canonical_workspace_id:
        raise ExportError(
            "bindings.json.workspace_id does not match the selected workspace"
        )
    resources = _object(raw["resources"], "bindings.json.resources")
    required_resource_types = {
        1: set(BINDING_RESOURCE_TYPES_V1),
        2: set(BINDING_RESOURCE_TYPES_V2),
        BINDING_SCHEMA_VERSION: set(BINDING_RESOURCE_TYPES),
    }[version]
    _strict_keys(resources, required_resource_types, set(), "bindings.json.resources")

    result: list[ResourceBinding] = []
    seen_ids: dict[str, tuple[str, str]] = {}
    for resource_type in BINDING_RESOURCE_TYPES:
        values = _object(
            resources.get(resource_type, {}),
            f"bindings.json.resources.{resource_type}",
        )
        folded_slugs: dict[str, str] = {}
        for raw_slug, raw_value in values.items():
            slug = _safe_slug(raw_slug, f"bindings.json.resources.{resource_type} slug")
            if slug != raw_slug:
                raise ExportError(
                    f"bindings.json.resources.{resource_type}: slug must use NFC"
                )
            if resource_type == "autopilot-trigger":
                _parse_autopilot_trigger_binding_slug(slug)
            folded = slug.casefold()
            if folded in folded_slugs:
                raise ExportError(
                    f"bindings.json.resources.{resource_type}: "
                    "case-fold-colliding slugs"
                )
            folded_slugs[folded] = slug
            label = f"bindings.json.resources.{resource_type}.{slug}"
            value = _object(raw_value, label)
            _strict_keys(value, {"remote_id", "last_known_name"}, set(), label)
            remote_id = _canonical_uuid(value["remote_id"], f"{label}.remote_id")
            previous = seen_ids.get(remote_id)
            if previous is not None:
                raise ExportError(
                    f"bindings.json: remote id {remote_id} is bound to both "
                    f"{previous[0]}/{previous[1]} and {resource_type}/{slug}"
                )
            seen_ids[remote_id] = (resource_type, slug)
            last_known_name = unicodedata.normalize(
                "NFC", _string(value["last_known_name"], f"{label}.last_known_name")
            )
            result.append(
                ResourceBinding(
                    resource_type=resource_type,
                    slug=slug,
                    remote_id=remote_id,
                    last_known_name=last_known_name,
                )
            )
    return tuple(sorted(result, key=lambda value: (value.resource_type, value.slug)))


def _binding_payload(workspace_id: str, bindings: Sequence[ResourceBinding]) -> bytes:
    canonical_workspace_id = _canonical_uuid(workspace_id, "binding workspace id")
    resources: dict[str, dict[str, dict[str, str]]] = {
        resource_type: {} for resource_type in BINDING_RESOURCE_TYPES
    }
    seen_keys: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for binding in sorted(
        bindings, key=lambda value: (value.resource_type, value.slug)
    ):
        if binding.resource_type not in resources:
            raise ExportError(
                f"cannot write unsupported binding type {binding.resource_type!r}"
            )
        key = (binding.resource_type, binding.slug)
        if key in seen_keys or binding.remote_id in seen_ids:
            raise ExportError("cannot write duplicate resource binding")
        seen_keys.add(key)
        seen_ids.add(binding.remote_id)
        resources[binding.resource_type][binding.slug] = {
            "remote_id": binding.remote_id,
            "last_known_name": binding.last_known_name,
        }
    payload = {
        "version": BINDING_SCHEMA_VERSION,
        "workspace_id": canonical_workspace_id,
        "resources": resources,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _prepare_binding_destination(repository_root: Path, workspace_id: str) -> Path:
    canonical_workspace_id = _canonical_uuid(workspace_id, "binding workspace id")
    path = _binding_path(repository_root, canonical_workspace_id)
    for directory, label in (
        (path.parent.parent.parent, "binding cache root"),
        (path.parent.parent, "binding workspaces root"),
        (path.parent, "binding workspace root"),
    ):
        if directory.exists() or directory.is_symlink():
            if not _actual_directory(directory):
                raise ExportError(f"{label}: expected regular directory")
        else:
            try:
                directory.mkdir()
            except OSError as exc:
                raise ExportError(
                    f"could not create {label}: {_brief(str(exc))}"
                ) from exc
    if (path.exists() or path.is_symlink()) and not _actual_file(path):
        raise ExportError("bindings.json: expected regular file")
    return path


def _stage_binding_write(
    repository_root: Path,
    workspace_id: str,
    bindings: Sequence[ResourceBinding],
) -> tuple[Path, Path]:
    path = _prepare_binding_destination(repository_root, workspace_id)
    encoded = _binding_payload(workspace_id, bindings)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".bindings-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return temporary, path
    except OSError as exc:
        if temporary is not None:
            _discard_staged_binding(temporary)
        raise ExportError(f"could not stage bindings.json: {_brief(str(exc))}") from exc


def _discard_staged_binding(temporary: Path) -> None:
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass


def _commit_staged_binding(temporary: Path, path: Path) -> None:
    try:
        os.replace(temporary, path)
    except OSError as exc:
        raise ExportError(
            f"could not commit bindings.json: {_brief(str(exc))}"
        ) from exc


def write_bindings(
    repository_root: Path,
    workspace_id: str,
    bindings: Sequence[ResourceBinding],
) -> None:
    temporary, path = _stage_binding_write(repository_root, workspace_id, bindings)
    try:
        _commit_staged_binding(temporary, path)
    finally:
        _discard_staged_binding(temporary)
