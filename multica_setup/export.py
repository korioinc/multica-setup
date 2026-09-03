"""Atomic export rendering, merge, publication, and binding refresh."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .bindings import (
    _commit_staged_binding,
    _discard_staged_binding,
    _stage_binding_write,
    load_bindings,
)
from .client import MulticaClient
from .constants import (
    AUTOPILOT_SELECTOR_FILE,
    BINDING_RESOURCE_TYPES,
    LEGACY_MANAGED_CATEGORIES,
    MANAGED_CATEGORIES,
    QUICK_ACTION_SELECTOR_FILE,
    WORKSPACE_FILES,
)
from .context import current_repository_root
from .domain import ResourceBinding
from .errors import ExportError
from .filesystem import (
    _actual_directory,
    _actual_file,
    _read_local_json,
    _write_json,
    _write_text,
)
from .identity import (
    _autopilot_trigger_binding_slug,
    _parse_autopilot_trigger_binding_slug,
)
from .local_state import (
    _autopilot_directory_index,
    _load_desired_state_from_src,
    _resolve_selector,
    _resource_directories,
    _resource_directory_index,
    _validate_directory_entries,
)
from .normalization import _runtime_device_name, _terminal_text
from .skill_documents import _skill_markdown
from .snapshot import build_snapshot
from .validation import _array, _brief, _canonical_uuid


def render_stage(stage: Path, snapshot: dict[str, Any]) -> None:
    for category in MANAGED_CATEGORIES:
        (stage / category).mkdir(parents=True)

    agent_slugs = snapshot["agent_slugs"]
    skill_slugs = snapshot["skill_slugs"]
    for agent in snapshot["agents"]:
        target = stage / "agent" / agent_slugs[agent["id"]]
        runtime_id = agent["runtime_id"]
        runtime_source = snapshot["runtimes"].get(runtime_id)
        _write_text(target / "instructions.md", agent["instructions"] or "")
        _write_json(
            target / "metadata.json",
            {
                "name": agent["name"],
                "description": agent["description"],
                "skills": sorted(skill_slugs[value] for value in agent["skill_ids"]),
                "runtime": _runtime_device_name(runtime_source),
                "provider": runtime_source["provider"] if runtime_source else None,
                "model": agent["model"] or None,
                "max_concurrent_tasks": agent["max_concurrent_tasks"],
            },
        )

    for skill in snapshot["skills"]:
        target = stage / "skills" / skill_slugs[skill["id"]]
        _write_text(
            target / "SKILL.md",
            _skill_markdown(skill["name"], skill["description"], skill["content"]),
        )
        target_root = target.resolve()
        for support_file in sorted(skill["files"], key=lambda value: value["path"]):
            destination = target.joinpath(*PurePosixPath(support_file["path"]).parts)
            resolved_parent = destination.parent.resolve()
            if os.path.commonpath((str(target_root), str(resolved_parent))) != str(
                target_root
            ):
                raise ExportError(
                    f"skill file escapes target directory: {support_file['path']!r}"
                )
            if any(
                part.is_symlink()
                for part in (destination, *destination.parents)
                if part != stage.parent
            ):
                raise ExportError(
                    f"skill file path contains symlink: {support_file['path']!r}"
                )
            _write_text(destination, support_file["content"])

    squad_slugs = snapshot["squad_slugs"]
    for squad in snapshot["squads"]:
        target = stage / "squad" / squad_slugs[squad["id"]]
        by_id = {member["id"]: member for member in squad["members"]}
        by_id.setdefault(squad["leader_id"], {"id": squad["leader_id"], "role": None})
        members: list[dict[str, Any]] = []
        for member_id, member in by_id.items():
            record: dict[str, Any] = {"agent_slug": agent_slugs[member_id]}
            if member["role"]:
                record["role"] = member["role"]
            record["leader"] = member_id == squad["leader_id"]
            members.append(record)
        members.sort(key=lambda value: (not value["leader"], value["agent_slug"]))
        _write_text(target / "instructions.md", squad["instructions"] or "")
        _write_json(
            target / "metadata.json",
            {
                "name": squad["name"],
                "description": squad["description"],
                "agents": members,
            },
        )

    autopilot_slugs = snapshot["autopilot_slugs"]
    for autopilot in snapshot["autopilots"]:
        slug = autopilot_slugs[autopilot["id"]]
        target = stage / "autopilots" / slug
        triggers: list[dict[str, Any]] = []
        trigger_keys = snapshot["autopilot_trigger_keys"][autopilot["id"]]
        for trigger in autopilot["triggers"]:
            if trigger["kind"] != "schedule":
                continue
            record: dict[str, Any] = {
                "key": trigger_keys[trigger["id"]],
                "kind": trigger["kind"],
                "enabled": trigger["enabled"],
                "label": trigger["label"],
            }
            record["cron_expression"] = trigger["cron_expression"]
            record["timezone"] = trigger["timezone"]
            triggers.append(record)
        assignee_slugs = (
            agent_slugs if autopilot["assignee_type"] == "agent" else squad_slugs
        )
        project = snapshot["projects"].get(autopilot["project_id"])
        _write_json(
            target / "metadata.json",
            {
                "name": autopilot["name"],
                "assignee_type": autopilot["assignee_type"],
                "assignee_slug": assignee_slugs[autopilot["assignee_id"]],
                "execution_mode": autopilot["execution_mode"],
                "project": (
                    snapshot["project_slugs"][autopilot["project_id"]]
                    if project is not None
                    else None
                ),
                "subscribers": sorted(
                    snapshot["members"][value]["email"]
                    for value in autopilot["subscribers"]
                ),
                "status": autopilot["status"],
                "triggers": sorted(triggers, key=lambda value: value["key"]),
            },
        )
        _write_text(target / "prompt.md", autopilot["prompt"])

    quick_action_slugs = snapshot["quick_action_slugs"]
    for quick_action in snapshot["quick_actions"]:
        target = stage / "quick-actions" / quick_action_slugs[quick_action["id"]]
        assignee_slugs = (
            agent_slugs if quick_action["assignee_type"] == "agent" else squad_slugs
        )
        _write_json(
            target / "metadata.json",
            {
                "name": quick_action["name"].strip(),
                "description": quick_action["description"].strip(),
                "assignee_type": quick_action["assignee_type"],
                "assignee_slug": assignee_slugs[quick_action["assignee_id"]],
            },
        )
        _write_text(target / "prompt.md", quick_action["prompt"].strip())

    workspace = snapshot["workspace"]
    workspace_dir = stage / "workspace" / workspace["id"]
    _write_text(workspace_dir / "instructions.md", workspace["context"] or "")
    _write_json(
        workspace_dir / "metadata.json",
        {
            "name": workspace["name"],
            "description": workspace["description"],
            "issue_prefix": workspace["issue_prefix"],
        },
    )
    _write_json(workspace_dir / "agent.json", sorted(agent_slugs.values()))
    _write_json(workspace_dir / "skill.json", sorted(skill_slugs.values()))
    _write_json(workspace_dir / "squad.json", sorted(squad_slugs.values()))
    _write_json(
        workspace_dir / AUTOPILOT_SELECTOR_FILE,
        sorted(autopilot_slugs.values()),
    )
    _write_json(
        workspace_dir / QUICK_ACTION_SELECTOR_FILE,
        sorted(quick_action_slugs.values()),
    )


def _workspace_directory_index(workspace_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for directory in _resource_directories(workspace_root, "src/workspace"):
        workspace_id = _canonical_uuid(
            directory.name, f"local workspace directory {directory.name!r}"
        )
        if directory.name != workspace_id:
            raise ExportError(
                "local workspace directory name must be a canonical UUID: "
                f"{directory.name!r}"
            )
        _validate_directory_entries(
            directory,
            set(WORKSPACE_FILES),
            f"workspace {workspace_id}",
            optional={AUTOPILOT_SELECTOR_FILE, QUICK_ACTION_SELECTOR_FILE},
        )
        result[workspace_id] = directory
    return result


def preflight_destination(src: Path, workspace_id: str) -> bool:
    if _canonical_uuid(workspace_id, "export workspace id") != workspace_id:
        raise ExportError("export workspace id must be a canonical UUID")
    if src.exists() or src.is_symlink():
        if not _actual_directory(src):
            raise ExportError(f"output root is not a regular directory: {src}")
    existing = [
        src / category
        for category in MANAGED_CATEGORIES
        if (src / category).exists() or (src / category).is_symlink()
    ]
    if not existing:
        return False
    existing_names = {value.name for value in existing}
    if existing_names != set(LEGACY_MANAGED_CATEGORIES) and existing_names != set(
        MANAGED_CATEGORIES
    ):
        raise ExportError(
            "managed snapshot is incomplete; refusing to adopt partial category roots"
        )
    for category in existing_names:
        root = src / category
        if not _actual_directory(root):
            raise ExportError(f"managed category is not a regular directory: {root}")
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                raise ExportError(f"managed snapshot contains symlink: {candidate}")

    if not _workspace_directory_index(src / "workspace"):
        raise ExportError("managed snapshot must contain at least one workspace")
    return True


def _tree_manifest(root: Path) -> list[tuple[str, str, bytes | None]]:
    manifest: list[tuple[str, str, bytes | None]] = []
    for path in sorted(
        root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ExportError(f"managed tree contains symlink: {path}")
        if path.is_dir():
            manifest.append((relative, "directory", None))
        elif path.is_file():
            manifest.append((relative, "file", path.read_bytes()))
        else:
            raise ExportError(
                f"managed tree contains unsupported filesystem entry: {path}"
            )
    return manifest


def _managed_trees_equal(src: Path, stage: Path) -> bool:
    if not all(_actual_directory(src / category) for category in MANAGED_CATEGORIES):
        return False
    return all(
        _tree_manifest(src / category) == _tree_manifest(stage / category)
        for category in MANAGED_CATEGORIES
    )


def _selector_slugs(
    workspace_directory: Path,
    selector_file: str,
    available: dict[str, Path],
    label: str,
) -> tuple[set[str], bool]:
    raw = _read_local_json(workspace_directory / selector_file, label)
    selected = _resolve_selector(raw, available, label)
    values = _array(raw, label)
    return ({value.name for value in selected}, values == ["*"])


def _copy_preserved_path(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        return
    if _actual_directory(source):
        shutil.copytree(source, target)
    elif _actual_file(source):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    else:
        raise ExportError(f"cannot preserve unsupported path {source}")


def _paths_equal(left: Path, right: Path) -> bool:
    if _actual_file(left) and _actual_file(right):
        return left.read_bytes() == right.read_bytes()
    if _actual_directory(left) and _actual_directory(right):
        return _tree_manifest(left) == _tree_manifest(right)
    return False


def merge_existing_snapshot(
    src: Path,
    stage: Path,
    selected_workspace_id: str,
    bindings: Sequence[ResourceBinding],
) -> list[str]:
    existing_workspaces = _workspace_directory_index(src / "workspace")
    selected_workspace = existing_workspaces.get(selected_workspace_id)
    sibling_workspaces = {
        workspace_id: directory
        for workspace_id, directory in existing_workspaces.items()
        if workspace_id != selected_workspace_id
    }
    for workspace_id, directory in sibling_workspaces.items():
        shutil.copytree(directory, stage / "workspace" / workspace_id)

    binding_slugs: dict[str, set[str]] = {
        resource_type: {
            value.slug for value in bindings if value.resource_type == resource_type
        }
        for resource_type in BINDING_RESOURCE_TYPES
    }
    warnings: list[str] = []
    for category, resource_type, selector_file in (
        ("agent", "agent", "agent.json"),
        ("skills", "skill", "skill.json"),
        ("squad", "squad", "squad.json"),
        ("autopilots", "autopilot", AUTOPILOT_SELECTOR_FILE),
        ("quick-actions", "quick-action", QUICK_ACTION_SELECTOR_FILE),
    ):
        existing_root = src / category
        existing_definitions = (
            (
                _autopilot_directory_index(existing_root, f"src/{category}")
                if category == "autopilots"
                else _resource_directory_index(existing_root, f"src/{category}")
            )
            if _actual_directory(existing_root)
            else {}
        )
        staged_definitions = (
            _autopilot_directory_index(stage / category, f"stage/{category}")
            if category == "autopilots"
            else _resource_directory_index(stage / category, f"stage/{category}")
        )
        target_owned = set(binding_slugs[resource_type])
        if selected_workspace is not None and _actual_file(
            selected_workspace / selector_file
        ):
            selected, wildcard = _selector_slugs(
                selected_workspace,
                selector_file,
                existing_definitions,
                f"workspace {selected_workspace_id} {selector_file}",
            )
            target_owned.update(selected)
            if wildcard:
                shutil.copy2(
                    selected_workspace / selector_file,
                    stage / "workspace" / selected_workspace_id / selector_file,
                )

        sibling_users: dict[str, set[str]] = {}
        for workspace_id, workspace_directory in sibling_workspaces.items():
            if not _actual_file(workspace_directory / selector_file):
                continue
            selected, _ = _selector_slugs(
                workspace_directory,
                selector_file,
                existing_definitions,
                f"workspace {workspace_id} {selector_file}",
            )
            for slug in selected:
                sibling_users.setdefault(slug, set()).add(workspace_id)

        for slug, workspace_ids in sibling_users.items():
            staged = staged_definitions.get(slug)
            existing = existing_definitions[slug]
            if staged is not None:
                if slug not in target_owned and not _paths_equal(existing, staged):
                    raise ExportError(
                        f"export {category}/{_terminal_text(slug)} collides with a "
                        "sibling-only definition"
                    )
                if slug in target_owned and not _paths_equal(existing, staged):
                    warnings.append(
                        f"updated shared {category}/{slug} used by workspace(s) "
                        f"{', '.join(sorted(workspace_ids))}"
                    )
                continue
            _copy_preserved_path(existing, stage / category / slug)

    if sibling_workspaces:
        existing_autopilots = src / "autopilots"
        for entry in existing_autopilots.iterdir():
            if not (_actual_file(entry) and entry.suffix == ".md"):
                continue
            target = stage / "autopilots" / entry.name
            if target.exists() or target.is_symlink():
                if not _paths_equal(entry, target):
                    raise ExportError(
                        f"export autopilot/{_terminal_text(entry.name)} has ambiguous "
                        "multi-workspace ownership"
                    )
                continue
            _copy_preserved_path(entry, target)

    for workspace_id in sorted({selected_workspace_id, *sibling_workspaces.keys()}):
        _load_desired_state_from_src(stage, workspace_id, require_agent_runtime=False)
    return warnings


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _make_tree_removable(path: Path) -> None:
    if path.is_symlink():
        path.parent.chmod(stat.S_IRWXU)
        return
    if path.is_file():
        path.parent.chmod(stat.S_IRWXU)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return
    if not path.exists():
        return
    path.chmod(stat.S_IRWXU)
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        current_path.chmod(stat.S_IRWXU)
        for directory in directories:
            candidate = current_path / directory
            if not candidate.is_symlink():
                candidate.chmod(stat.S_IRWXU)
        for filename in files:
            candidate = current_path / filename
            if not candidate.is_symlink():
                candidate.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _cleanup_tree(path: Path, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    last_error: OSError | None = None
    for attempt in range(2):
        try:
            _remove_path(path)
            last_error = None
            break
        except OSError as exc:
            last_error = exc
            if attempt == 0:
                try:
                    _make_tree_removable(path)
                except OSError:
                    pass
    if last_error is not None:
        raise ExportError(
            f"could not clean {label} {path}: {_brief(str(last_error))}"
        ) from last_error
    if path.exists() or path.is_symlink():
        raise ExportError(f"could not clean {label} {path}")


def publish_snapshot(
    src: Path, stage: Path, had_snapshot: bool
) -> tuple[bool, list[str]]:
    src_preexisted = src.exists()
    if had_snapshot and _managed_trees_equal(src, stage):
        return False, []

    backup = Path(tempfile.mkdtemp(prefix=".multica-export-backup-", dir=src.parent))
    moved_to_backup: list[str] = []
    installed: list[str] = []
    try:
        src.mkdir(parents=True, exist_ok=True)
        for category in MANAGED_CATEGORIES:
            current = src / category
            if current.exists():
                os.replace(current, backup / category)
                moved_to_backup.append(category)
        for category in MANAGED_CATEGORIES:
            os.replace(stage / category, src / category)
            installed.append(category)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for category in reversed(installed):
            try:
                _remove_path(src / category)
            except BaseException as rollback_exc:
                rollback_errors.append(_brief(str(rollback_exc)))
        for category in reversed(moved_to_backup):
            try:
                os.replace(backup / category, src / category)
            except BaseException as rollback_exc:
                rollback_errors.append(_brief(str(rollback_exc)))
        if not src_preexisted:
            try:
                src.rmdir()
            except BaseException as rollback_exc:
                rollback_errors.append(_brief(str(rollback_exc)))
        if rollback_errors:
            raise ExportError(
                "publish rollback was incomplete; original data remains in "
                f"{backup}: {'; '.join(rollback_errors)}"
            ) from exc
        try:
            _cleanup_tree(backup, "export backup")
        except ExportError as cleanup_exc:
            raise ExportError(
                f"publish was rolled back, but cleanup failed: {cleanup_exc}"
            ) from exc
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, OSError):
            raise ExportError(
                f"publish failed and was rolled back: {_brief(str(exc))}"
            ) from exc
        raise

    warnings: list[str] = []
    try:
        _cleanup_tree(backup, "export backup")
    except (ExportError, KeyboardInterrupt) as exc:
        detail = str(exc) or "cleanup was interrupted"
        warnings.append(
            f"new snapshot is committed, but backup cleanup is incomplete at {backup}: {detail}"
        )
    return True, warnings


def _snapshot_bindings(snapshot: dict[str, Any]) -> tuple[ResourceBinding, ...]:
    result: list[ResourceBinding] = []
    for resource_type, values_key, slugs_key in (
        ("agent", "agents", "agent_slugs"),
        ("skill", "skills", "skill_slugs"),
        ("squad", "squads", "squad_slugs"),
        ("autopilot", "autopilots", "autopilot_slugs"),
        ("quick-action", "quick_actions", "quick_action_slugs"),
    ):
        slugs = snapshot[slugs_key]
        for value in snapshot[values_key]:
            result.append(
                ResourceBinding(
                    resource_type=resource_type,
                    slug=slugs[value["id"]],
                    remote_id=value["id"],
                    last_known_name=value["name"],
                )
            )
    referenced_project_ids: set[str] = set()
    for autopilot in snapshot["autopilots"]:
        autopilot_slug = snapshot["autopilot_slugs"][autopilot["id"]]
        trigger_keys = snapshot["autopilot_trigger_keys"][autopilot["id"]]
        for trigger in autopilot["triggers"]:
            if trigger["kind"] != "schedule":
                continue
            trigger_key = trigger_keys[trigger["id"]]
            result.append(
                ResourceBinding(
                    resource_type="autopilot-trigger",
                    slug=_autopilot_trigger_binding_slug(autopilot_slug, trigger_key),
                    remote_id=trigger["id"],
                    last_known_name=trigger["label"] or trigger_key,
                )
            )
        if autopilot["project_id"] is not None:
            referenced_project_ids.add(autopilot["project_id"])
    for project_id in sorted(referenced_project_ids):
        project = snapshot["projects"][project_id]
        result.append(
            ResourceBinding(
                resource_type="autopilot-project",
                slug=snapshot["project_slugs"][project_id],
                remote_id=project_id,
                last_known_name=project["name"],
            )
        )
    return tuple(sorted(result, key=lambda value: (value.resource_type, value.slug)))


def _available_snapshot_slug(proposed: str, resource_id: str, used: set[str]) -> str:
    if proposed.casefold() not in used:
        return proposed
    compact_id = resource_id.replace("-", "")
    for length in range(8, 33):
        prefix = proposed[: 79 - length].rstrip("-")
        candidate = f"{prefix}-{compact_id[:length]}" if prefix else compact_id[:length]
        if candidate.casefold() not in used:
            return candidate
    raise ExportError(f"could not allocate a unique slug for remote id {resource_id}")


def _available_portable_key(proposed: str, used: set[str]) -> str:
    if proposed.casefold() not in used:
        return proposed
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = f"{proposed[: 80 - len(suffix)].rstrip('-')}{suffix}"
        if candidate.casefold() not in used:
            return candidate
        index += 1


def _preserve_bound_snapshot_slugs(
    snapshot: dict[str, Any], bindings: Sequence[ResourceBinding]
) -> None:
    for resource_type, values_key, slugs_key in (
        ("agent", "agents", "agent_slugs"),
        ("skill", "skills", "skill_slugs"),
        ("squad", "squads", "squad_slugs"),
        ("autopilot", "autopilots", "autopilot_slugs"),
        ("quick-action", "quick_actions", "quick_action_slugs"),
    ):
        bound_slugs = {
            value.remote_id: value.slug
            for value in bindings
            if value.resource_type == resource_type
        }
        proposed_slugs = snapshot[slugs_key]
        resolved: dict[str, str] = {}
        used: set[str] = set()
        values_by_id = {value["id"]: value for value in snapshot[values_key]}
        for resource_id in sorted(set(values_by_id) & set(bound_slugs)):
            slug = bound_slugs[resource_id]
            folded = slug.casefold()
            if folded in used:
                raise ExportError(
                    f"{resource_type}: bound slug collision for remote id {resource_id}"
                )
            resolved[resource_id] = slug
            used.add(folded)
        for resource_id in sorted(set(values_by_id) - set(resolved)):
            slug = _available_snapshot_slug(
                proposed_slugs[resource_id], resource_id, used
            )
            resolved[resource_id] = slug
            used.add(slug.casefold())
        snapshot[slugs_key] = resolved

    bound_project_slugs = {
        value.remote_id: value.slug
        for value in bindings
        if value.resource_type == "autopilot-project"
    }
    proposed_project_slugs = snapshot["project_slugs"]
    resolved_project_slugs: dict[str, str] = {}
    used_project_slugs: set[str] = set()
    project_ids = set(snapshot["projects"])
    for project_id in sorted(project_ids & set(bound_project_slugs)):
        project_slug = bound_project_slugs[project_id]
        if project_slug.casefold() in used_project_slugs:
            raise ExportError(
                f"autopilot project: bound slug collision for remote id {project_id}"
            )
        resolved_project_slugs[project_id] = project_slug
        used_project_slugs.add(project_slug.casefold())
    for project_id in sorted(project_ids - set(resolved_project_slugs)):
        project_slug = _available_portable_key(
            proposed_project_slugs[project_id], used_project_slugs
        )
        resolved_project_slugs[project_id] = project_slug
        used_project_slugs.add(project_slug.casefold())
    snapshot["project_slugs"] = resolved_project_slugs

    trigger_bindings = {
        value.remote_id: _parse_autopilot_trigger_binding_slug(value.slug)
        for value in bindings
        if value.resource_type == "autopilot-trigger"
    }
    for autopilot in snapshot["autopilots"]:
        autopilot_id = autopilot["id"]
        autopilot_slug = snapshot["autopilot_slugs"][autopilot_id]
        proposed_keys = snapshot["autopilot_trigger_keys"][autopilot_id]
        resolved_keys: dict[str, str] = {}
        used: set[str] = set()
        trigger_ids = {
            value["id"]
            for value in autopilot["triggers"]
            if value["kind"] == "schedule"
        }
        for trigger_id in sorted(trigger_ids & set(trigger_bindings)):
            bound_autopilot_slug, trigger_key = trigger_bindings[trigger_id]
            if bound_autopilot_slug != autopilot_slug:
                continue
            if trigger_key.casefold() in used:
                raise ExportError(
                    f"autopilot {autopilot_slug}: bound trigger key collision"
                )
            resolved_keys[trigger_id] = trigger_key
            used.add(trigger_key.casefold())
        for trigger_id in sorted(trigger_ids - set(resolved_keys)):
            trigger_key = _available_portable_key(proposed_keys[trigger_id], used)
            resolved_keys[trigger_id] = trigger_key
            used.add(trigger_key.casefold())
        snapshot["autopilot_trigger_keys"][autopilot_id] = resolved_keys


def export_workspace(
    selector: str,
    expected_workspace_id: str | None = None,
    *,
    repository_root: Path | None = None,
) -> tuple[dict[str, Any], Path, bool, list[str]]:
    repository_root = repository_root or current_repository_root()
    src = repository_root / "src"
    snapshot = build_snapshot(MulticaClient(), selector, expected_workspace_id)
    bindings = load_bindings(repository_root, snapshot["workspace"]["id"])
    _preserve_bound_snapshot_slugs(snapshot, bindings)
    had_snapshot = preflight_destination(src, snapshot["workspace"]["id"])
    binding_stage, binding_path = _stage_binding_write(
        repository_root,
        snapshot["workspace"]["id"],
        _snapshot_bindings(snapshot),
    )
    try:
        stage = Path(
            tempfile.mkdtemp(prefix=".multica-export-stage-", dir=repository_root)
        )
    except BaseException:
        _discard_staged_binding(binding_stage)
        raise
    changed = False
    warnings: list[str] = []
    try:
        try:
            render_stage(stage, snapshot)
            merge_warnings = (
                merge_existing_snapshot(
                    src,
                    stage,
                    snapshot["workspace"]["id"],
                    bindings,
                )
                if had_snapshot
                else []
            )
            changed, publish_warnings = publish_snapshot(src, stage, had_snapshot)
            warnings = [*merge_warnings, *publish_warnings]
            if snapshot["unmanaged_webhook_triggers"]:
                warnings.append(
                    f"preserved {snapshot['unmanaged_webhook_triggers']} remote "
                    "webhook trigger(s) as manually managed; they were not exported"
                )
        finally:
            try:
                _cleanup_tree(stage, "export stage")
            except (ExportError, KeyboardInterrupt) as exc:
                if not changed:
                    raise
                detail = str(exc) or "cleanup was interrupted"
                warnings.append(
                    "new snapshot is committed, but stage cleanup is incomplete at "
                    f"{stage}: {detail}"
                )
        try:
            _commit_staged_binding(binding_stage, binding_path)
        except ExportError as exc:
            raise ExportError(
                "snapshot is committed, but workspace bindings could not be cached; "
                "do not edit resource names before rerunning export or apply: "
                f"{exc}"
            ) from exc
    finally:
        _discard_staged_binding(binding_stage)
    return snapshot, src, changed, warnings
