"""Repository-local desired state and workspace binding persistence."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .constants import (
    AUTOPILOT_SELECTOR_FILE,
    QUICK_ACTION_SELECTOR_FILE,
    WORKSPACE_FILES,
)
from .domain import (
    DesiredAgent,
    DesiredAutopilot,
    DesiredAutopilotTrigger,
    DesiredQuickAction,
    DesiredSkill,
    DesiredSquad,
    DesiredSquadMember,
    DesiredState,
)
from .errors import ExportError
from .filesystem import (
    _actual_directory,
    _actual_file,
    _read_local_json,
    _read_local_text,
)
from .normalization import (
    _canonical_optional_text,
    _canonical_text,
    _terminal_text,
)
from .skill_documents import (
    _skill_frontmatter,
    _validate_skill_paths,
)
from .validation import (
    _array,
    _canonical_uuid,
    _nullable_string,
    _object,
    _required,
    _safe_slug,
    _stable_fingerprint,
    _strict_keys,
    _string,
)


def _resource_directories(root: Path, label: str) -> list[Path]:
    if not _actual_directory(root):
        raise ExportError(f"{label}: expected regular directory")
    result: list[Path] = []
    for entry in sorted(
        root.iterdir(), key=lambda value: unicodedata.normalize("NFC", value.name)
    ):
        if not _actual_directory(entry):
            raise ExportError(f"{label}: expected resource directory {entry.name!r}")
        _safe_slug(entry.name, f"{label}.{entry.name}")
        result.append(entry)
    slugs = [unicodedata.normalize("NFC", entry.name) for entry in result]
    if len(slugs) != len(set(slugs)) or len(slugs) != len(
        {value.casefold() for value in slugs}
    ):
        raise ExportError(f"{label}: duplicate or case-fold-colliding slug")
    return result


def _resource_directory_index(root: Path, label: str) -> dict[str, Path]:
    return {
        unicodedata.normalize("NFC", directory.name): directory
        for directory in _resource_directories(root, label)
    }


def _autopilot_directory_index(root: Path, label: str) -> dict[str, Path]:
    if not _actual_directory(root):
        raise ExportError(f"{label}: expected regular directory")
    directories: list[Path] = []
    for entry in sorted(
        root.iterdir(), key=lambda value: unicodedata.normalize("NFC", value.name)
    ):
        if _actual_directory(entry):
            _safe_slug(entry.name, f"{label}.{entry.name}")
            directories.append(entry)
            continue
        if _actual_file(entry) and entry.suffix == ".md":
            _safe_slug(entry.stem, f"{label}.{entry.name}")
            continue
        raise ExportError(f"{label}: unsupported entry {entry.name!r}")
    slugs = [unicodedata.normalize("NFC", value.name) for value in directories]
    if len(slugs) != len(set(slugs)) or len(slugs) != len(
        {value.casefold() for value in slugs}
    ):
        raise ExportError(f"{label}: duplicate or case-fold-colliding slug")
    return {directory.name: directory for directory in directories}


def _validate_directory_entries(
    path: Path,
    expected: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed_optional = optional or set()
    actual: set[str] = set()
    for entry in path.iterdir():
        if entry.is_symlink() or not _actual_file(entry):
            raise ExportError(f"{label}: unsupported entry {entry.name!r}")
        actual.add(entry.name)
    missing = expected - actual
    if missing:
        raise ExportError(f"{label}: missing {sorted(missing)[0]}")
    unknown = actual - expected - allowed_optional
    if unknown:
        raise ExportError(f"{label}: unknown entry {sorted(unknown)[0]}")


def _local_name_index(values: Sequence[Any], kind: str) -> dict[str, Any]:
    by_name: dict[str, Any] = {}
    by_folded: dict[str, str] = {}
    for value in values:
        name = unicodedata.normalize("NFC", value.name)
        if name in by_name:
            raise ExportError(f"local {kind}: duplicate name {_terminal_text(name)!r}")
        folded = name.casefold()
        if folded in by_folded:
            raise ExportError(
                f"local {kind}: case-fold-colliding names "
                f"{_terminal_text(by_folded[folded])!r} and {_terminal_text(name)!r}"
            )
        by_name[name] = value
        by_folded[folded] = name
    return by_name


def _load_local_skills(directories: Sequence[Path]) -> dict[str, DesiredSkill]:
    values: list[DesiredSkill] = []
    for directory in directories:
        slug = unicodedata.normalize("NFC", directory.name)
        skill_file = directory / "SKILL.md"
        document = _read_local_text(skill_file, f"skill {slug}.SKILL.md")
        name, description = _skill_frontmatter(document, f"skill {slug}")
        files: list[dict[str, str]] = []
        for path in sorted(
            directory.rglob("*"),
            key=lambda value: value.relative_to(directory).as_posix(),
        ):
            relative = path.relative_to(directory).as_posix()
            if path.is_symlink():
                raise ExportError(f"skill {slug}: symlink is not allowed: {relative!r}")
            if path.is_dir():
                if not _actual_directory(path):
                    raise ExportError(
                        f"skill {slug}: unsupported directory: {relative!r}"
                    )
                continue
            if not _actual_file(path):
                raise ExportError(f"skill {slug}: unsupported file: {relative!r}")
            if relative == "SKILL.md":
                continue
            files.append(
                {
                    "path": relative,
                    "content": _read_local_text(path, f"skill {slug} file {relative}"),
                }
            )
        _validate_skill_paths(files)
        values.append(
            DesiredSkill(
                slug=slug,
                name=name,
                description=description,
                document=document,
                files=tuple((item["path"], item["content"]) for item in files),
            )
        )
    _local_name_index(values, "skill")
    return {value.slug: value for value in values}


def _local_optional_string(value: Any, label: str) -> str | None:
    return _canonical_optional_text(_nullable_string(value, label))


def _load_local_agents(directories: Sequence[Path]) -> dict[str, DesiredAgent]:
    values: list[DesiredAgent] = []
    for directory in directories:
        slug = unicodedata.normalize("NFC", directory.name)
        _validate_directory_entries(
            directory, {"metadata.json", "instructions.md"}, f"agent {slug}"
        )
        record = _object(
            _read_local_json(
                directory / "metadata.json", f"agent {slug}.metadata.json"
            ),
            f"agent {slug}.metadata.json",
        )
        required = {
            "name",
            "description",
            "skills",
            "runtime",
            "provider",
            "model",
            "max_concurrent_tasks",
        }
        _strict_keys(record, required, set(), f"agent {slug}.metadata.json")
        raw_skills = _array(record["skills"], f"agent {slug}.skills")
        skill_slugs: list[str] = []
        for index, raw_skill in enumerate(raw_skills):
            skill_slug = _safe_slug(raw_skill, f"agent {slug}.skills[{index}]")
            if skill_slug == "*":
                raise ExportError(f"agent {slug}.skills: wildcard is not allowed")
            if skill_slug in skill_slugs:
                raise ExportError(f"agent {slug}.skills: duplicate slug {skill_slug!r}")
            skill_slugs.append(skill_slug)
        max_tasks = record["max_concurrent_tasks"]
        if max_tasks is not None and (
            isinstance(max_tasks, bool)
            or not isinstance(max_tasks, int)
            or max_tasks <= 0
        ):
            raise ExportError(
                f"agent {slug}.max_concurrent_tasks: expected positive integer or null"
            )
        runtime = _local_optional_string(record["runtime"], f"agent {slug}.runtime")
        provider = _local_optional_string(record["provider"], f"agent {slug}.provider")
        values.append(
            DesiredAgent(
                slug=slug,
                name=unicodedata.normalize(
                    "NFC", _string(record["name"], f"agent {slug}.name")
                ),
                description=_local_optional_string(
                    record["description"], f"agent {slug}.description"
                ),
                instructions=_read_local_text(
                    directory / "instructions.md", f"agent {slug}.instructions.md"
                ),
                skill_slugs=tuple(sorted(skill_slugs)),
                runtime=runtime,
                provider=provider,
                model=_local_optional_string(record["model"], f"agent {slug}.model"),
                max_concurrent_tasks=max_tasks,
            )
        )
    _local_name_index(values, "agent")
    return {value.slug: value for value in values}


def _load_local_squads(directories: Sequence[Path]) -> dict[str, DesiredSquad]:
    values: list[DesiredSquad] = []
    for directory in directories:
        slug = unicodedata.normalize("NFC", directory.name)
        _validate_directory_entries(
            directory, {"metadata.json", "instructions.md"}, f"squad {slug}"
        )
        record = _object(
            _read_local_json(
                directory / "metadata.json", f"squad {slug}.metadata.json"
            ),
            f"squad {slug}.metadata.json",
        )
        _strict_keys(
            record,
            {"name", "description", "agents"},
            set(),
            f"squad {slug}.metadata.json",
        )
        members: list[DesiredSquadMember] = []
        seen: set[str] = set()
        for index, raw_member in enumerate(
            _array(record["agents"], f"squad {slug}.agents")
        ):
            label = f"squad {slug}.agents[{index}]"
            member = _object(raw_member, label)
            _strict_keys(member, {"agent_slug", "leader"}, {"role"}, label)
            agent_slug = _safe_slug(member["agent_slug"], f"{label}.agent_slug")
            if agent_slug == "*":
                raise ExportError(f"{label}.agent_slug: wildcard is not allowed")
            if agent_slug in seen:
                raise ExportError(
                    f"squad {slug}.agents: duplicate agent {agent_slug!r}"
                )
            seen.add(agent_slug)
            leader = member["leader"]
            if not isinstance(leader, bool):
                raise ExportError(f"{label}.leader: expected boolean")
            members.append(
                DesiredSquadMember(
                    agent_slug=agent_slug,
                    role=_local_optional_string(member.get("role"), f"{label}.role"),
                    leader=leader,
                )
            )
        if sum(member.leader for member in members) != 1:
            raise ExportError(f"squad {slug}: expected exactly one leader")
        values.append(
            DesiredSquad(
                slug=slug,
                name=unicodedata.normalize(
                    "NFC", _string(record["name"], f"squad {slug}.name")
                ),
                description=_local_optional_string(
                    record["description"], f"squad {slug}.description"
                ),
                instructions=_read_local_text(
                    directory / "instructions.md", f"squad {slug}.instructions.md"
                ),
                members=tuple(sorted(members, key=lambda value: value.agent_slug)),
            )
        )
    _local_name_index(values, "squad")
    return {value.slug: value for value in values}


def _quick_action_text(value: str, label: str, *, required: bool, maximum: int) -> str:
    normalized = _canonical_text(_string(value, label)).strip()
    if required and not normalized:
        raise ExportError(f"{label}: must not be blank")
    if len(normalized) > maximum:
        raise ExportError(f"{label}: must be at most {maximum} characters")
    return normalized


def _load_local_quick_actions(
    directories: Sequence[Path],
) -> dict[str, DesiredQuickAction]:
    values: list[DesiredQuickAction] = []
    for directory in directories:
        slug = unicodedata.normalize("NFC", directory.name)
        _validate_directory_entries(
            directory,
            {"metadata.json", "prompt.md"},
            f"quick action {slug}",
        )
        record = _object(
            _read_local_json(
                directory / "metadata.json", f"quick action {slug}.metadata.json"
            ),
            f"quick action {slug}.metadata.json",
        )
        _strict_keys(
            record,
            {"name", "description", "assignee_type", "assignee_slug"},
            set(),
            f"quick action {slug}.metadata.json",
        )
        assignee_type = _string(
            record["assignee_type"], f"quick action {slug}.assignee_type"
        )
        if assignee_type not in {"agent", "squad"}:
            raise ExportError(
                f"quick action {slug}.assignee_type: expected 'agent' or 'squad'"
            )
        assignee_slug = _safe_slug(
            record["assignee_slug"],
            f"quick action {slug}.assignee_slug",
        )
        if assignee_slug == "*":
            raise ExportError(
                f"quick action {slug}.assignee_slug: wildcard is not allowed"
            )
        prompt = _quick_action_text(
            _read_local_text(directory / "prompt.md", f"quick action {slug}.prompt.md"),
            f"quick action {slug}.prompt",
            required=True,
            maximum=4000,
        )
        if re.search(r"\{\{[^}]*\}\}", prompt):
            raise ExportError(
                f"quick action {slug}.prompt: template variables are not supported"
            )
        if re.search(r"mention://(?:agent|squad|member|all)/", prompt):
            raise ExportError(
                f"quick action {slug}.prompt: side-effect mentions are not allowed"
            )
        values.append(
            DesiredQuickAction(
                slug=slug,
                name=unicodedata.normalize(
                    "NFC",
                    _quick_action_text(
                        record["name"],
                        f"quick action {slug}.name",
                        required=True,
                        maximum=32,
                    ),
                ),
                description=_quick_action_text(
                    record["description"],
                    f"quick action {slug}.description",
                    required=False,
                    maximum=200,
                ),
                assignee_type=assignee_type,
                assignee_slug=assignee_slug,
                prompt=prompt,
            )
        )
    return {value.slug: value for value in values}


def _load_local_autopilots(
    directories: Sequence[Path],
) -> dict[str, DesiredAutopilot]:
    values: list[DesiredAutopilot] = []
    for directory in directories:
        slug = unicodedata.normalize("NFC", directory.name)
        _validate_directory_entries(
            directory, {"metadata.json", "prompt.md"}, f"autopilot {slug}"
        )
        record = _object(
            _read_local_json(
                directory / "metadata.json", f"autopilot {slug}.metadata.json"
            ),
            f"autopilot {slug}.metadata.json",
        )
        _strict_keys(
            record,
            {
                "name",
                "assignee_type",
                "assignee_slug",
                "execution_mode",
                "project",
                "subscribers",
                "status",
                "triggers",
            },
            {"issue_title_template"},
            f"autopilot {slug}.metadata.json",
        )
        name = unicodedata.normalize(
            "NFC", _string(record["name"], f"autopilot {slug}.name")
        ).strip()
        if not name:
            raise ExportError(f"autopilot {slug}.name: must not be blank")
        assignee_type = _string(
            record["assignee_type"], f"autopilot {slug}.assignee_type"
        )
        if assignee_type not in {"agent", "squad"}:
            raise ExportError(
                f"autopilot {slug}.assignee_type: expected 'agent' or 'squad'"
            )
        assignee_slug = _safe_slug(
            record["assignee_slug"], f"autopilot {slug}.assignee_slug"
        )
        if assignee_slug == "*":
            raise ExportError(
                f"autopilot {slug}.assignee_slug: wildcard is not allowed"
            )
        execution_mode = _string(
            record["execution_mode"], f"autopilot {slug}.execution_mode"
        )
        if execution_mode not in {"create_issue", "run_only"}:
            raise ExportError(
                f"autopilot {slug}.execution_mode: expected 'create_issue' or 'run_only'"
            )
        raw_project = record["project"]
        project: str | None = None
        if raw_project is not None:
            if isinstance(raw_project, dict):
                project_record = _object(raw_project, f"autopilot {slug}.project")
                _strict_keys(
                    project_record,
                    {"key"},
                    {"name"},
                    f"autopilot {slug}.project",
                )
                raw_project = project_record["key"]
            project = _safe_slug(raw_project, f"autopilot {slug}.project")
            if project == "*":
                raise ExportError(f"autopilot {slug}.project: wildcard is not allowed")
        subscribers: list[str] = []
        seen_subscribers: set[str] = set()
        for index, raw_subscriber in enumerate(
            _array(record["subscribers"], f"autopilot {slug}.subscribers")
        ):
            email = unicodedata.normalize(
                "NFC",
                _string(raw_subscriber, f"autopilot {slug}.subscribers[{index}]"),
            ).strip()
            if not email or "@" not in email:
                raise ExportError(
                    f"autopilot {slug}.subscribers[{index}]: expected member email"
                )
            folded = email.casefold()
            if folded in seen_subscribers:
                raise ExportError(f"autopilot {slug}.subscribers: duplicate email")
            seen_subscribers.add(folded)
            subscribers.append(email)
        status = _string(record["status"], f"autopilot {slug}.status")
        if status not in {"active", "paused"}:
            raise ExportError(f"autopilot {slug}.status: expected 'active' or 'paused'")
        triggers: list[DesiredAutopilotTrigger] = []
        trigger_keys: set[str] = set()
        for index, raw_trigger in enumerate(
            _array(record["triggers"], f"autopilot {slug}.triggers")
        ):
            label = f"autopilot {slug}.triggers[{index}]"
            trigger = _object(raw_trigger, label)
            kind = _string(_required(trigger, "kind", label), f"{label}.kind")
            base_fields = {"key", "kind", "enabled", "label"}
            if kind == "webhook":
                continue
            if kind != "schedule":
                raise ExportError(f"{label}.kind: unsupported value {kind!r}")
            _strict_keys(
                trigger,
                base_fields | {"cron_expression", "timezone"},
                set(),
                label,
            )
            key = _safe_slug(trigger["key"], f"{label}.key")
            if key == "*" or key.casefold() in trigger_keys:
                raise ExportError(
                    f"autopilot {slug}.triggers: duplicate or invalid key"
                )
            trigger_keys.add(key.casefold())
            enabled = trigger["enabled"]
            if not isinstance(enabled, bool):
                raise ExportError(f"{label}.enabled: expected boolean")
            trigger_label = _local_optional_string(trigger["label"], f"{label}.label")
            cron_expression = _canonical_text(
                _string(trigger["cron_expression"], f"{label}.cron_expression")
            ).strip()
            timezone = _canonical_text(
                _string(trigger["timezone"], f"{label}.timezone")
            ).strip()
            if not cron_expression or not timezone:
                raise ExportError(
                    f"{label}: schedule requires cron_expression and timezone"
                )
            triggers.append(
                DesiredAutopilotTrigger(
                    key=key,
                    kind=kind,
                    enabled=enabled,
                    label=trigger_label,
                    cron_expression=cron_expression,
                    timezone=timezone,
                )
            )
        values.append(
            DesiredAutopilot(
                slug=slug,
                name=name,
                prompt=_read_local_text(
                    directory / "prompt.md", f"autopilot {slug}.prompt.md"
                ),
                assignee_type=assignee_type,
                assignee_slug=assignee_slug,
                execution_mode=execution_mode,
                project=project,
                subscribers=tuple(sorted(subscribers, key=str.casefold)),
                status=status,
                triggers=tuple(sorted(triggers, key=lambda value: value.key)),
            )
        )
    return {value.slug: value for value in values}


def _resolve_selector(
    raw: Any, available: dict[str, Any], label: str
) -> tuple[Any, ...]:
    values = _array(raw, label)
    selected: list[str] = []
    for index, raw_value in enumerate(values):
        value = _safe_slug(raw_value, f"{label}[{index}]")
        if value in selected:
            raise ExportError(f"{label}: duplicate selector {value!r}")
        selected.append(value)
    if "*" in selected:
        if len(selected) != 1:
            raise ExportError(f"{label}: wildcard cannot be mixed with slugs")
        selected = sorted(available)
    unknown = set(selected) - set(available)
    if unknown:
        raise ExportError(f"{label}: unknown slug {sorted(unknown)[0]!r}")
    return tuple(available[value] for value in selected)


def _load_desired_state_from_src(
    src: Path,
    selected_workspace_id: str,
    *,
    require_agent_runtime: bool = True,
) -> DesiredState:
    if not _actual_directory(src):
        raise ExportError("src: expected regular directory")
    workspace_root = src / "workspace"
    workspaces = _resource_directories(workspace_root, "src/workspace")
    workspaces_by_id: dict[str, Path] = {}
    for directory in workspaces:
        workspace_id = _canonical_uuid(
            directory.name, f"local workspace directory {directory.name!r}"
        )
        if directory.name != workspace_id:
            raise ExportError(
                "local workspace directory name must be a canonical UUID: "
                f"{directory.name!r}"
            )
        if workspace_id in workspaces_by_id:
            raise ExportError(f"src/workspace: duplicate workspace UUID {workspace_id}")
        workspaces_by_id[workspace_id] = directory

    workspace_directory = workspaces_by_id.get(selected_workspace_id)
    if workspace_directory is None:
        raise ExportError(
            "local workspace configuration not found: "
            f"src/workspace/{_terminal_text(selected_workspace_id)}"
        )
    local_workspace_id = selected_workspace_id
    _validate_directory_entries(
        workspace_directory,
        set(WORKSPACE_FILES),
        "local workspace",
        optional={AUTOPILOT_SELECTOR_FILE, QUICK_ACTION_SELECTOR_FILE},
    )
    metadata = _object(
        _read_local_json(
            workspace_directory / "metadata.json", "workspace metadata.json"
        ),
        "workspace metadata.json",
    )
    _strict_keys(
        metadata,
        {"name", "description", "issue_prefix"},
        set(),
        "workspace metadata.json",
    )

    skill_directories = _resource_directory_index(src / "skills", "src/skills")
    agent_directories = _resource_directory_index(src / "agent", "src/agent")
    squad_directories = _resource_directory_index(src / "squad", "src/squad")
    autopilot_selector = workspace_directory / AUTOPILOT_SELECTOR_FILE
    autopilots_managed = _actual_file(autopilot_selector)
    autopilot_directories: dict[str, Path] = {}
    if autopilots_managed:
        autopilot_directories = _autopilot_directory_index(
            src / "autopilots", "src/autopilots"
        )
    quick_action_selector = workspace_directory / QUICK_ACTION_SELECTOR_FILE
    quick_actions_managed = _actual_file(quick_action_selector)
    quick_action_directories: dict[str, Path] = {}
    if quick_actions_managed:
        quick_action_directories = _resource_directory_index(
            src / "quick-actions", "src/quick-actions"
        )
    selected_skill_directories = _resolve_selector(
        _read_local_json(workspace_directory / "skill.json", "workspace skill.json"),
        skill_directories,
        "workspace skill.json",
    )
    selected_agent_directories = _resolve_selector(
        _read_local_json(workspace_directory / "agent.json", "workspace agent.json"),
        agent_directories,
        "workspace agent.json",
    )
    selected_squad_directories = _resolve_selector(
        _read_local_json(workspace_directory / "squad.json", "workspace squad.json"),
        squad_directories,
        "workspace squad.json",
    )
    selected_autopilot_directories = (
        _resolve_selector(
            _read_local_json(autopilot_selector, "workspace autopilot.json"),
            autopilot_directories,
            "workspace autopilot.json",
        )
        if autopilots_managed
        else ()
    )
    selected_quick_action_directories = (
        _resolve_selector(
            _read_local_json(quick_action_selector, "workspace quick-action.json"),
            quick_action_directories,
            "workspace quick-action.json",
        )
        if quick_actions_managed
        else ()
    )
    skills = tuple(_load_local_skills(selected_skill_directories).values())
    agents = tuple(_load_local_agents(selected_agent_directories).values())
    squads = tuple(_load_local_squads(selected_squad_directories).values())
    autopilots = tuple(_load_local_autopilots(selected_autopilot_directories).values())
    quick_actions = tuple(
        _load_local_quick_actions(selected_quick_action_directories).values()
    )

    selected_skill_slugs = {value.slug for value in skills}
    selected_agent_slugs = {value.slug for value in agents}
    for agent in agents:
        missing = set(agent.skill_slugs) - selected_skill_slugs
        if missing:
            raise ExportError(
                f"agent {_terminal_text(agent.name)!r} references unselected skill "
                f"{_terminal_text(sorted(missing)[0])!r}"
            )
        if require_agent_runtime and (agent.runtime is None or agent.provider is None):
            raise ExportError(
                f"agent {_terminal_text(agent.name)!r}: selected agent requires provider and runtime"
            )
    for squad in squads:
        missing = {member.agent_slug for member in squad.members} - selected_agent_slugs
        if missing:
            raise ExportError(
                f"squad {_terminal_text(squad.name)!r} references unselected agent "
                f"{_terminal_text(sorted(missing)[0])!r}"
            )
    for quick_action in quick_actions:
        available = (
            selected_agent_slugs
            if quick_action.assignee_type == "agent"
            else {value.slug for value in squads}
        )
        if quick_action.assignee_slug not in available:
            raise ExportError(
                f"quick action {_terminal_text(quick_action.name)!r} references "
                f"unselected {quick_action.assignee_type} "
                f"{_terminal_text(quick_action.assignee_slug)!r}"
            )
    for autopilot in autopilots:
        available = (
            selected_agent_slugs
            if autopilot.assignee_type == "agent"
            else {value.slug for value in squads}
        )
        if autopilot.assignee_slug not in available:
            raise ExportError(
                f"autopilot {_terminal_text(autopilot.name)!r} references "
                f"unselected {autopilot.assignee_type} "
                f"{_terminal_text(autopilot.assignee_slug)!r}"
            )

    workspace_name = unicodedata.normalize(
        "NFC", _string(metadata["name"], "workspace metadata.json.name")
    )
    desired_without_fingerprint = {
        "workspace_id": local_workspace_id,
        "workspace_name": workspace_name,
        "workspace_description": _local_optional_string(
            metadata["description"], "workspace metadata.json.description"
        ),
        "issue_prefix": _string(
            metadata["issue_prefix"], "workspace metadata.json.issue_prefix"
        ),
        "workspace_context": _read_local_text(
            workspace_directory / "instructions.md", "workspace instructions.md"
        ),
        "skills": [
            asdict(value) for value in sorted(skills, key=lambda item: item.name)
        ],
        "agents": [
            asdict(value) for value in sorted(agents, key=lambda item: item.name)
        ],
        "squads": [
            asdict(value) for value in sorted(squads, key=lambda item: item.name)
        ],
        "autopilots": [
            asdict(value) for value in sorted(autopilots, key=lambda item: item.name)
        ],
        "autopilots_managed": autopilots_managed,
        "quick_actions": [
            asdict(value) for value in sorted(quick_actions, key=lambda item: item.name)
        ],
        "quick_actions_managed": quick_actions_managed,
    }
    return DesiredState(
        workspace_id=local_workspace_id,
        workspace_name=workspace_name,
        workspace_description=desired_without_fingerprint["workspace_description"],
        issue_prefix=desired_without_fingerprint["issue_prefix"],
        workspace_context=desired_without_fingerprint["workspace_context"],
        skills=tuple(sorted(skills, key=lambda item: (item.name, item.slug))),
        agents=tuple(sorted(agents, key=lambda item: (item.name, item.slug))),
        squads=tuple(sorted(squads, key=lambda item: (item.name, item.slug))),
        autopilots=tuple(sorted(autopilots, key=lambda item: (item.name, item.slug))),
        autopilots_managed=autopilots_managed,
        quick_actions=tuple(
            sorted(quick_actions, key=lambda item: (item.name, item.slug))
        ),
        quick_actions_managed=quick_actions_managed,
        fingerprint=_stable_fingerprint(desired_without_fingerprint),
    )


def load_desired_state(
    repository_root: Path, selected_workspace_id: str
) -> DesiredState:
    return _load_desired_state_from_src(repository_root / "src", selected_workspace_id)
