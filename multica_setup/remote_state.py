"""Remote inventory loading, normalization, and identity matching."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from .client import MulticaClient
from .domain import (
    DesiredAutopilot,
    DesiredState,
    RemoteAgent,
    RemoteAutopilot,
    RemoteAutopilotTrigger,
    RemoteQuickAction,
    RemoteSkill,
    RemoteSkillFile,
    RemoteSquad,
    RemoteSquadMember,
    RemoteState,
    ResourceBinding,
    ResourceMatch,
)
from .errors import ExportError
from .identity import (
    _autopilot_trigger_binding_slug,
    _parse_autopilot_trigger_binding_slug,
)
from .normalization import (
    _canonical_optional_text,
    _canonical_text,
    _terminal_text,
)
from .skill_documents import (
    _skill_frontmatter,
    _skill_markdown,
    _validate_skill_paths,
)
from .snapshot import (
    _validate_autopilot_detail,
    _validate_autopilot_list,
    _validate_named_list,
    _validate_project_list,
    _validate_quick_action_list,
    _validate_runtime_list,
    _validate_workspace_members,
)
from .validation import (
    _array,
    _canonical_uuid,
    _nullable_string,
    _object,
    _required,
    _stable_fingerprint,
    _string,
)


def _remote_named_summaries(raw: Any, endpoint: str) -> tuple[tuple[str, str], ...]:
    values = _validate_named_list(raw, endpoint)
    result = tuple(
        sorted(
            (
                (value["id"], unicodedata.normalize("NFC", value["name"]))
                for value in values
            ),
            key=lambda value: (value[1], value[0]),
        )
    )
    _remote_name_index(result, endpoint)
    return result


def _remote_agent_summaries(
    raw: Any,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    values = _array(raw, "agent list")
    active: list[tuple[str, str]] = []
    archived: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for index, raw_value in enumerate(values):
        endpoint = f"agent list[{index}]"
        value = _object(raw_value, endpoint)
        resource_id = _canonical_uuid(
            _required(value, "id", endpoint), f"{endpoint}.id"
        )
        if resource_id in seen_ids:
            raise ExportError(f"agent list: duplicate id {resource_id}")
        seen_ids.add(resource_id)
        name = unicodedata.normalize(
            "NFC", _string(_required(value, "name", endpoint), f"{endpoint}.name")
        )
        archived_at = value.get("archived_at")
        if archived_at is not None and not isinstance(archived_at, str):
            raise ExportError(f"{endpoint}.archived_at: expected string or null")
        (archived if archived_at else active).append((resource_id, name))
    active_result = tuple(sorted(active, key=lambda value: (value[1], value[0])))
    archived_result = tuple(sorted(archived, key=lambda value: (value[1], value[0])))
    _remote_name_index(active_result, "active agent list")
    _remote_name_index(archived_result, "archived agent list")
    active_names = {name.casefold() for _, name in active_result}
    archived_collisions = [
        name for _, name in archived_result if name.casefold() in active_names
    ]
    for archived_name in archived_collisions:
        if archived_name not in {name for _, name in active_result}:
            raise ExportError(
                f"agent list: active/archived case-fold collision for "
                f"{_terminal_text(archived_name)!r}"
            )
    return active_result, archived_result


def _remote_name_index(
    summaries: Sequence[tuple[str, str]], label: str
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    folded: dict[str, str] = {}
    for resource_id, name in summaries:
        if name in result:
            raise ExportError(f"{label}: duplicate name {_terminal_text(name)!r}")
        key = name.casefold()
        if key in folded:
            raise ExportError(
                f"{label}: case-fold-colliding names "
                f"{_terminal_text(folded[key])!r} and {_terminal_text(name)!r}"
            )
        result[name] = (resource_id, name)
        folded[key] = name
    return result


def _validate_plan_skill_detail(raw: Any, summary: tuple[str, str]) -> RemoteSkill:
    item = _matching_plan_detail(raw, summary, "skill get")
    description = _canonical_optional_text(
        _nullable_string(item.get("description"), "skill get.description")
    )
    content = _canonical_text(
        _string(_required(item, "content", "skill get"), "skill get.content")
    )
    document = _canonical_text(_skill_markdown(summary[1], description, content))
    _, embedded_description = _skill_frontmatter(document, f"skill {summary[1]!r}")
    # The API can omit the top-level description while preserving it in the
    # canonical SKILL.md frontmatter. Only fall back for that absent value so a
    # non-empty top-level disagreement remains visible as repairable drift.
    effective_description = (
        description if description is not None else embedded_description
    )
    files: list[RemoteSkillFile] = []
    path_records: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, raw_file in enumerate(
        _array(_required(item, "files", "skill get"), "skill get.files")
    ):
        endpoint = f"skill get.files[{index}]"
        file_item = _object(raw_file, endpoint)
        file_id = _canonical_uuid(
            _required(file_item, "id", endpoint), f"{endpoint}.id"
        )
        if file_id in seen_ids:
            raise ExportError(f"skill get.files: duplicate id {file_id}")
        seen_ids.add(file_id)
        path = _string(_required(file_item, "path", endpoint), f"{endpoint}.path")
        file_content = _canonical_text(
            _string(_required(file_item, "content", endpoint), f"{endpoint}.content")
        )
        path_records.append({"path": path, "content": file_content})
        files.append(RemoteSkillFile(id=file_id, path=path, content=file_content))
    _validate_skill_paths(path_records)
    return RemoteSkill(
        id=summary[0],
        name=summary[1],
        description=effective_description,
        document=document,
        files=tuple(sorted(files, key=lambda value: value.path)),
    )


def _permission_summary(item: dict[str, Any], workspace_id: str) -> str:
    mode = _string(
        _required(item, "permission_mode", "agent get"),
        "agent get.permission_mode",
    )
    if mode == "private":
        return "private"
    if mode != "public_to":
        raise ExportError(
            f"agent get.permission_mode: unsupported value {_terminal_text(mode)!r}"
        )
    targets = _array(
        _required(item, "invocation_targets", "agent get"),
        "agent get.invocation_targets",
    )
    member_ids: set[str] = set()
    workspace_target = False
    seen: set[tuple[str, str]] = set()
    for index, raw_target in enumerate(targets):
        endpoint = f"agent get.invocation_targets[{index}]"
        target = _object(raw_target, endpoint)
        target_type = _string(
            _required(target, "target_type", endpoint), f"{endpoint}.target_type"
        )
        target_id = _canonical_uuid(
            _required(target, "target_id", endpoint), f"{endpoint}.target_id"
        )
        identity = (target_type, target_id)
        if identity in seen:
            raise ExportError("agent get.invocation_targets: duplicate target")
        seen.add(identity)
        if target_type == "workspace":
            if target_id != workspace_id:
                raise ExportError(f"{endpoint}: target belongs to another workspace")
            workspace_target = True
        elif target_type == "member":
            member_ids.add(target_id)
        elif target_type == "team":
            raise ExportError(f"{endpoint}.target_type: unsupported team target")
        else:
            raise ExportError(
                f"{endpoint}.target_type: unsupported value {_terminal_text(target_type)!r}"
            )
    if workspace_target:
        return "workspace-public"
    return f"specific-people({len(member_ids)})"


def _workspace_public_permission(item: dict[str, Any], workspace_id: str) -> bool:
    mode = _string(
        _required(item, "permission_mode", "agent get"),
        "agent get.permission_mode",
    )
    if mode != "public_to":
        return False
    targets = _array(
        _required(item, "invocation_targets", "agent get"),
        "agent get.invocation_targets",
    )
    for index, raw_target in enumerate(targets):
        endpoint = f"agent get.invocation_targets[{index}]"
        target = _object(raw_target, endpoint)
        target_type = _string(
            _required(target, "target_type", endpoint), f"{endpoint}.target_type"
        )
        target_id = _canonical_uuid(
            _required(target, "target_id", endpoint), f"{endpoint}.target_id"
        )
        if target_type == "workspace" and target_id == workspace_id:
            return True
    return False


def _matching_plan_detail(
    raw: Any, summary: tuple[str, str], endpoint: str
) -> dict[str, Any]:
    item = _object(raw, endpoint)
    detail_id = _canonical_uuid(_required(item, "id", endpoint), f"{endpoint}.id")
    detail_name = unicodedata.normalize(
        "NFC", _string(_required(item, "name", endpoint), f"{endpoint}.name")
    )
    if detail_id != summary[0] or detail_name != summary[1]:
        raise ExportError(f"{endpoint}: list/detail identity mismatch for {summary[0]}")
    return item


def _validate_plan_agent_detail(
    raw: Any,
    summary: tuple[str, str],
    workspace_id: str,
    archived: bool,
) -> RemoteAgent:
    item = _matching_plan_detail(raw, summary, "agent get")
    raw_runtime_id = item.get("runtime_id")
    if raw_runtime_id in (None, ""):
        runtime_id = None
    else:
        runtime_id = _canonical_uuid(raw_runtime_id, "agent get.runtime_id")
    max_tasks = item.get("max_concurrent_tasks")
    if max_tasks is not None and (
        isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks <= 0
    ):
        raise ExportError(
            "agent get.max_concurrent_tasks: expected positive integer or null"
        )
    skill_ids: list[str] = []
    for index, raw_skill in enumerate(
        _array(_required(item, "skills", "agent get"), "agent get.skills")
    ):
        endpoint = f"agent get.skills[{index}]"
        skill = _object(raw_skill, endpoint)
        skill_id = _canonical_uuid(_required(skill, "id", endpoint), f"{endpoint}.id")
        if skill_id in skill_ids:
            raise ExportError(f"agent get.skills: duplicate id {skill_id}")
        skill_ids.append(skill_id)
    return RemoteAgent(
        id=summary[0],
        name=summary[1],
        description=_canonical_optional_text(
            _nullable_string(item.get("description"), "agent get.description")
        ),
        instructions=_canonical_optional_text(
            _nullable_string(item.get("instructions"), "agent get.instructions")
        ),
        runtime_id=runtime_id,
        model=_canonical_optional_text(
            _nullable_string(item.get("model"), "agent get.model")
        ),
        max_concurrent_tasks=max_tasks,
        skill_ids=tuple(sorted(skill_ids)),
        permission_summary=(
            _permission_summary(item, workspace_id) if archived else "unmanaged"
        ),
        workspace_public=_workspace_public_permission(item, workspace_id),
        archived=archived,
    )


def _validate_plan_squad_detail(
    raw: Any,
    raw_members: Any,
    summary: tuple[str, str],
) -> RemoteSquad:
    item = _matching_plan_detail(raw, summary, "squad get")
    leader_id = _canonical_uuid(
        _required(item, "leader_id", "squad get"), "squad get.leader_id"
    )
    members: list[RemoteSquadMember] = []
    seen: set[str] = set()
    for index, raw_member in enumerate(_array(raw_members, "squad member list")):
        endpoint = f"squad member list[{index}]"
        member = _object(raw_member, endpoint)
        member_id = _canonical_uuid(
            _required(member, "member_id", endpoint), f"{endpoint}.member_id"
        )
        if member_id in seen:
            raise ExportError(f"squad member list: duplicate member {member_id}")
        seen.add(member_id)
        member_type = _string(
            _required(member, "member_type", endpoint), f"{endpoint}.member_type"
        )
        if member_type != "agent":
            raise ExportError(
                f"{endpoint}.member_type: unsupported value {member_type!r}"
            )
        members.append(
            RemoteSquadMember(
                agent_id=member_id,
                role=_canonical_optional_text(
                    _nullable_string(member.get("role"), f"{endpoint}.role")
                ),
            )
        )
    if leader_id not in seen:
        members.append(RemoteSquadMember(agent_id=leader_id, role=None))
    return RemoteSquad(
        id=summary[0],
        name=summary[1],
        description=_canonical_optional_text(
            _nullable_string(item.get("description"), "squad get.description")
        ),
        instructions=_canonical_optional_text(
            _nullable_string(item.get("instructions"), "squad get.instructions")
        ),
        leader_id=leader_id,
        members=tuple(sorted(members, key=lambda value: value.agent_id)),
    )


def _quick_action_summaries(
    values: Sequence[dict[str, Any]],
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    active = tuple(
        sorted(
            (
                (value["id"], value["name"])
                for value in values
                if value["status"] == "active"
            ),
            key=lambda value: (value[1], value[0]),
        )
    )
    archived = tuple(
        sorted(
            (
                (value["id"], value["name"])
                for value in values
                if value["status"] == "archived"
            ),
            key=lambda value: (value[1], value[0]),
        )
    )
    return active, archived


def _validate_plan_quick_action(
    value: dict[str, Any], expected_id: str
) -> RemoteQuickAction:
    if value["id"] != expected_id:
        raise ExportError(f"quick action list: identity mismatch for {expected_id}")
    return RemoteQuickAction(
        id=value["id"],
        name=value["name"],
        description=value["description"].strip(),
        assignee_type=value["assignee_type"],
        assignee_id=value["assignee_id"],
        prompt=value["prompt"].strip(),
        status=value["status"],
        target_public=value["target_public"],
        target_missing=value["target_missing"],
    )


def _remote_autopilot_from_detail(value: dict[str, Any]) -> RemoteAutopilot:
    return RemoteAutopilot(
        id=value["id"],
        name=value["name"],
        prompt=value["prompt"],
        assignee_type=value["assignee_type"],
        assignee_id=value["assignee_id"],
        execution_mode=value["execution_mode"],
        project_id=value["project_id"],
        subscribers=tuple(value["subscribers"]),
        status=value["status"],
        can_write=value["can_write"],
        triggers=tuple(
            RemoteAutopilotTrigger(
                id=trigger["id"],
                autopilot_id=trigger["autopilot_id"],
                kind=trigger["kind"],
                enabled=trigger["enabled"],
                label=trigger["label"],
                cron_expression=trigger["cron_expression"],
                timezone=trigger["timezone"],
            )
            for trigger in value["triggers"]
        ),
    )


def _autopilot_trigger_projection(value: Any) -> tuple[Any, ...]:
    return (
        value.kind,
        value.enabled,
        value.label,
        value.cron_expression,
        value.timezone,
    )


def _resolve_autopilot_trigger_matches(
    desired_autopilot: DesiredAutopilot,
    remote_autopilot: RemoteAutopilot,
    bindings: Sequence[ResourceBinding],
) -> tuple[ResourceMatch, ...]:
    desired_by_key = {value.key: value for value in desired_autopilot.triggers}
    all_remote_by_id = {value.id: value for value in remote_autopilot.triggers}
    remote_by_id = {
        value.id: value
        for value in remote_autopilot.triggers
        if value.kind == "schedule"
    }
    binding_by_key: dict[str, ResourceBinding] = {}
    for binding in bindings:
        if binding.resource_type != "autopilot-trigger":
            continue
        autopilot_slug, trigger_key = _parse_autopilot_trigger_binding_slug(
            binding.slug
        )
        if autopilot_slug != desired_autopilot.slug:
            continue
        remote_trigger = all_remote_by_id.get(binding.remote_id)
        if remote_trigger is None or remote_trigger.kind == "schedule":
            binding_by_key[trigger_key] = binding

    result: list[ResourceMatch] = []
    claimed_ids: set[str] = set()
    for key in sorted(set(desired_by_key) & set(binding_by_key)):
        binding = binding_by_key[key]
        if binding.remote_id not in remote_by_id:
            raise ExportError(
                f"binding autopilot-trigger/{_terminal_text(binding.slug)} points "
                f"to missing remote id {binding.remote_id}; remove or repair the binding"
            )
        claimed_ids.add(binding.remote_id)
        result.append(
            ResourceMatch(
                resource_type="autopilot-trigger",
                slug=_autopilot_trigger_binding_slug(desired_autopilot.slug, key),
                remote_id=binding.remote_id,
                lifecycle="active",
            )
        )

    for key in sorted(set(desired_by_key) - set(binding_by_key)):
        desired_trigger = desired_by_key[key]
        candidates = [
            value.id
            for value in remote_autopilot.triggers
            if value.kind == "schedule"
            and value.id not in claimed_ids
            and _autopilot_trigger_projection(value)
            == _autopilot_trigger_projection(desired_trigger)
        ]
        if not candidates:
            candidates = [
                value.id
                for value in remote_autopilot.triggers
                if value.kind == "schedule"
                and value.id not in claimed_ids
                and value.kind == desired_trigger.kind
                and value.label == desired_trigger.label
            ]
        if len(candidates) > 1:
            raise ExportError(
                f"autopilot {_terminal_text(desired_autopilot.name)!r} trigger "
                f"{_terminal_text(key)!r} matches multiple remote triggers; run export "
                "or repair workspace bindings"
            )
        if not candidates:
            continue
        claimed_ids.add(candidates[0])
        result.append(
            ResourceMatch(
                resource_type="autopilot-trigger",
                slug=_autopilot_trigger_binding_slug(desired_autopilot.slug, key),
                remote_id=candidates[0],
                lifecycle="active",
            )
        )
    return tuple(sorted(result, key=lambda value: value.slug))


def _inventory_ids(values: Sequence[tuple[str, str]]) -> set[str]:
    return {value[0] for value in values}


def _resolve_resource_matches(
    desired_values: Sequence[Any],
    resource_type: str,
    active_summaries: Sequence[tuple[str, str]],
    archived_summaries: Sequence[tuple[str, str]],
    bindings: Sequence[ResourceBinding],
    *,
    allow_duplicate_remote_names: bool = False,
) -> tuple[ResourceMatch, ...]:
    inventory = [
        (resource_id, name, "active") for resource_id, name in active_summaries
    ]
    inventory.extend(
        (resource_id, name, "archived") for resource_id, name in archived_summaries
    )
    by_id = {
        resource_id: (name, lifecycle) for resource_id, name, lifecycle in inventory
    }
    active_by_name: dict[str, list[str]] = {}
    archived_by_name: dict[str, list[str]] = {}
    for resource_id, name in active_summaries:
        active_by_name.setdefault(name, []).append(resource_id)
    for resource_id, name in archived_summaries:
        archived_by_name.setdefault(name, []).append(resource_id)
    binding_by_slug = {
        value.slug: value for value in bindings if value.resource_type == resource_type
    }
    if allow_duplicate_remote_names:
        desired_name_groups: dict[str, list[str]] = {}
        for value in desired_values:
            desired_name_groups.setdefault(value.name, []).append(value.slug)
        for name, slugs in desired_name_groups.items():
            if len(slugs) > 1 and any(slug not in binding_by_slug for slug in slugs):
                raise ExportError(
                    f"{resource_type}: duplicate local name "
                    f"{_terminal_text(name)!r} requires an existing workspace binding "
                    "for every slug; run export or choose unique names"
                )

    result: list[ResourceMatch] = []
    claimed_ids: dict[str, str] = {}
    desired_by_slug = {value.slug: value for value in desired_values}
    for slug in sorted(desired_by_slug):
        desired = desired_by_slug[slug]
        binding = binding_by_slug.get(slug)
        match: tuple[str, str] | None = None
        if binding is not None:
            remote = by_id.get(binding.remote_id)
            if remote is None:
                raise ExportError(
                    f"binding {resource_type}/{_terminal_text(slug)} points to "
                    f"missing remote id {binding.remote_id}; remove or repair the binding"
                )
            match = (binding.remote_id, remote[1])
        else:
            active_ids = active_by_name.get(desired.name, [])
            archived_ids = archived_by_name.get(desired.name, [])
            if allow_duplicate_remote_names:
                candidates = [
                    *((resource_id, "active") for resource_id in active_ids),
                    *((resource_id, "archived") for resource_id in archived_ids),
                ]
                if len(candidates) > 1:
                    raise ExportError(
                        f"{resource_type}: desired name "
                        f"{_terminal_text(desired.name)!r} matches multiple remote ids; "
                        "create or repair its workspace binding"
                    )
                if candidates:
                    match = candidates[0]
            elif active_ids:
                match = (active_ids[0], "active")
            elif archived_ids:
                match = (archived_ids[0], "archived")

        if match is None:
            continue
        remote_id, lifecycle = match
        previous_slug = claimed_ids.get(remote_id)
        if previous_slug is not None:
            raise ExportError(
                f"{resource_type}: remote id {remote_id} resolves to both "
                f"{_terminal_text(previous_slug)!r} and {_terminal_text(slug)!r}"
            )
        claimed_ids[remote_id] = slug
        result.append(
            ResourceMatch(
                resource_type=resource_type,
                slug=slug,
                remote_id=remote_id,
                lifecycle=lifecycle,
            )
        )

    match_by_slug = {value.slug: value for value in result}
    if allow_duplicate_remote_names:
        return tuple(
            sorted(result, key=lambda value: (value.resource_type, value.slug))
        )

    for slug, desired in desired_by_slug.items():
        match = match_by_slug.get(slug)
        matched_id = match.remote_id if match is not None else None
        for resource_id, remote_name, lifecycle in inventory:
            if (
                resource_type == "agent"
                and match is not None
                and match.lifecycle == "active"
                and lifecycle == "archived"
                and remote_name == desired.name
            ):
                continue
            if (
                remote_name.casefold() == desired.name.casefold()
                and resource_id != matched_id
            ):
                raise ExportError(
                    f"{resource_type}: desired name {_terminal_text(desired.name)!r} "
                    f"is already used by remote id {resource_id}"
                )
    return tuple(sorted(result, key=lambda value: (value.resource_type, value.slug)))


def read_remote_state(
    client: MulticaClient,
    workspace: dict[str, Any],
    desired: DesiredState,
    bindings: Sequence[ResourceBinding],
) -> RemoteState:
    workspace_id = workspace["id"]
    runtimes = tuple(
        tuple(sorted(value.items()))
        for value in sorted(
            _validate_runtime_list(client.runtime_list(workspace_id)),
            key=lambda item: item["id"],
        )
    )
    active_agents, archived_agents = _remote_agent_summaries(
        client.agent_list(workspace_id, include_archived=True)
    )
    active_skills = _remote_named_summaries(
        client.skill_list(workspace_id), "skill list"
    )
    active_squads = _remote_named_summaries(
        client.squad_list(workspace_id), "squad list"
    )
    active_autopilots: tuple[tuple[str, str], ...] = ()
    autopilot_values: list[RemoteAutopilot] = []
    project_values: list[dict[str, str]] = []
    member_values: list[dict[str, str]] = []
    if desired.autopilots_managed:
        autopilot_summaries = _validate_autopilot_list(
            client.autopilot_list(workspace_id)
        )
        active_autopilots = tuple(
            sorted(
                ((value["id"], value["name"]) for value in autopilot_summaries),
                key=lambda value: (value[1], value[0]),
            )
        )
        autopilot_values = [
            _remote_autopilot_from_detail(
                _validate_autopilot_detail(
                    client.autopilot_get(value["id"], workspace_id),
                    value,
                    workspace_id,
                )
            )
            for value in sorted(autopilot_summaries, key=lambda item: item["id"])
        ]
        project_values = _validate_project_list(client.project_list(workspace_id))
        member_values = _validate_workspace_members(
            client.workspace_members(workspace_id), workspace_id
        )
    quick_action_values: list[dict[str, Any]] = []
    active_quick_actions: tuple[tuple[str, str], ...] = ()
    archived_quick_actions: tuple[tuple[str, str], ...] = ()
    if desired.quick_actions_managed:
        quick_action_values = _validate_quick_action_list(
            client.quick_action_list(workspace_id, True),
            workspace_id,
            include_archived=True,
        )
        active_quick_actions, archived_quick_actions = _quick_action_summaries(
            quick_action_values
        )

    skill_matches = _resolve_resource_matches(
        desired.skills, "skill", active_skills, (), bindings
    )
    agent_matches = _resolve_resource_matches(
        desired.agents, "agent", active_agents, archived_agents, bindings
    )
    squad_matches = _resolve_resource_matches(
        desired.squads, "squad", active_squads, (), bindings
    )
    autopilot_matches = (
        _resolve_resource_matches(
            desired.autopilots,
            "autopilot",
            active_autopilots,
            (),
            bindings,
            allow_duplicate_remote_names=True,
        )
        if desired.autopilots_managed
        else ()
    )
    quick_action_matches = (
        _resolve_resource_matches(
            desired.quick_actions,
            "quick-action",
            active_quick_actions,
            archived_quick_actions,
            bindings,
            allow_duplicate_remote_names=True,
        )
        if desired.quick_actions_managed
        else ()
    )
    desired_autopilots_by_slug = {value.slug: value for value in desired.autopilots}
    remote_autopilots_by_id = {value.id: value for value in autopilot_values}
    autopilot_trigger_matches: list[ResourceMatch] = []
    for match in autopilot_matches:
        autopilot_trigger_matches.extend(
            _resolve_autopilot_trigger_matches(
                desired_autopilots_by_slug[match.slug],
                remote_autopilots_by_id[match.remote_id],
                bindings,
            )
        )
    skill_by_id = {
        resource_id: (resource_id, name) for resource_id, name in active_skills
    }
    active_agent_by_id = {
        resource_id: (resource_id, name) for resource_id, name in active_agents
    }
    archived_agent_by_id = {
        resource_id: (resource_id, name) for resource_id, name in archived_agents
    }
    squad_by_id = {
        resource_id: (resource_id, name) for resource_id, name in active_squads
    }

    skills = tuple(
        _validate_plan_skill_detail(
            client.skill_get(match.remote_id, workspace_id),
            skill_by_id[match.remote_id],
        )
        for match in skill_matches
    )
    agents: list[RemoteAgent] = []
    for match in agent_matches:
        if match.lifecycle == "active":
            summary = active_agent_by_id[match.remote_id]
            agents.append(
                _validate_plan_agent_detail(
                    client.agent_get(summary[0], workspace_id),
                    summary,
                    workspace_id,
                    False,
                )
            )
        else:
            summary = archived_agent_by_id[match.remote_id]
            agents.append(
                _validate_plan_agent_detail(
                    client.agent_get(summary[0], workspace_id),
                    summary,
                    workspace_id,
                    True,
                )
            )
    squads = tuple(
        _validate_plan_squad_detail(
            client.squad_get(match.remote_id, workspace_id),
            client.squad_members(match.remote_id, workspace_id),
            squad_by_id[match.remote_id],
        )
        for match in squad_matches
    )
    quick_actions = tuple(
        _validate_plan_quick_action(value, value["id"]) for value in quick_action_values
    )

    final_runtimes = _validate_runtime_list(client.runtime_list(workspace_id))
    final_active_agents, final_archived_agents = _remote_agent_summaries(
        client.agent_list(workspace_id, include_archived=True)
    )
    final_skills = _remote_named_summaries(
        client.skill_list(workspace_id), "skill list"
    )
    final_squads = _remote_named_summaries(
        client.squad_list(workspace_id), "squad list"
    )
    final_autopilots: tuple[tuple[str, str], ...] = ()
    final_projects: list[dict[str, str]] = []
    final_members: list[dict[str, str]] = []
    if desired.autopilots_managed:
        final_autopilots = tuple(
            sorted(
                (
                    (value["id"], value["name"])
                    for value in _validate_autopilot_list(
                        client.autopilot_list(workspace_id)
                    )
                ),
                key=lambda value: (value[1], value[0]),
            )
        )
        final_projects = _validate_project_list(client.project_list(workspace_id))
        final_members = _validate_workspace_members(
            client.workspace_members(workspace_id), workspace_id
        )
    final_active_quick_actions: tuple[tuple[str, str], ...] = ()
    final_archived_quick_actions: tuple[tuple[str, str], ...] = ()
    if desired.quick_actions_managed:
        final_quick_action_values = _validate_quick_action_list(
            client.quick_action_list(workspace_id, True),
            workspace_id,
            include_archived=True,
        )
        (
            final_active_quick_actions,
            final_archived_quick_actions,
        ) = _quick_action_summaries(final_quick_action_values)
    if {value["id"] for value in final_runtimes} != {
        dict(value)["id"] for value in runtimes
    }:
        raise ExportError("workspace changed during plan: runtime inventory changed")
    if _inventory_ids(final_active_agents + final_archived_agents) != _inventory_ids(
        active_agents + archived_agents
    ):
        raise ExportError("workspace changed during plan: agent inventory changed")
    if _inventory_ids(final_skills) != _inventory_ids(active_skills):
        raise ExportError("workspace changed during plan: skill inventory changed")
    if _inventory_ids(final_squads) != _inventory_ids(active_squads):
        raise ExportError("workspace changed during plan: squad inventory changed")
    if _inventory_ids(final_autopilots) != _inventory_ids(active_autopilots):
        raise ExportError("workspace changed during plan: autopilot inventory changed")
    if {value["id"] for value in final_projects} != {
        value["id"] for value in project_values
    }:
        raise ExportError("workspace changed during plan: project inventory changed")
    if {value["user_id"] for value in final_members} != {
        value["user_id"] for value in member_values
    }:
        raise ExportError("workspace changed during plan: member inventory changed")
    if _inventory_ids(
        final_active_quick_actions + final_archived_quick_actions
    ) != _inventory_ids(active_quick_actions + archived_quick_actions):
        raise ExportError(
            "workspace changed during plan: quick action inventory changed"
        )

    workspace_tuple = tuple(sorted(workspace.items()))
    fingerprint_payload = {
        "workspace": workspace_tuple,
        "runtimes": runtimes,
        "active_skills": active_skills,
        "active_agents": active_agents,
        "archived_agents": archived_agents,
        "active_squads": active_squads,
        "active_autopilots": active_autopilots,
        "active_quick_actions": active_quick_actions,
        "archived_quick_actions": archived_quick_actions,
        "skills": [asdict(value) for value in skills],
        "agents": [asdict(value) for value in agents],
        "squads": [asdict(value) for value in squads],
        "autopilots": [
            {
                **asdict(value),
                "triggers": [
                    asdict(trigger)
                    for trigger in value.triggers
                    if trigger.kind == "schedule"
                ],
            }
            for value in autopilot_values
        ],
        "projects": project_values,
        "members": member_values,
        "quick_actions": [asdict(value) for value in quick_actions],
        "matches": [
            asdict(value)
            for value in (
                *skill_matches,
                *agent_matches,
                *squad_matches,
                *autopilot_matches,
                *quick_action_matches,
            )
        ],
        "autopilot_trigger_matches": [
            asdict(value) for value in autopilot_trigger_matches
        ],
    }
    return RemoteState(
        workspace=workspace_tuple,
        runtimes=runtimes,
        active_skill_summaries=active_skills,
        active_agent_summaries=active_agents,
        archived_agent_summaries=archived_agents,
        active_squad_summaries=active_squads,
        active_autopilot_summaries=active_autopilots,
        active_quick_action_summaries=active_quick_actions,
        archived_quick_action_summaries=archived_quick_actions,
        skills=skills,
        agents=tuple(agents),
        squads=squads,
        autopilots=tuple(autopilot_values),
        projects=tuple(
            sorted((value["id"], value["name"]) for value in project_values)
        ),
        members=tuple(
            sorted((value["user_id"], value["email"]) for value in member_values)
        ),
        quick_actions=quick_actions,
        autopilot_trigger_matches=tuple(autopilot_trigger_matches),
        matches=tuple(
            (
                *skill_matches,
                *agent_matches,
                *squad_matches,
                *autopilot_matches,
                *quick_action_matches,
            )
        ),
        bindings=tuple(bindings),
        fingerprint=_stable_fingerprint(fingerprint_payload),
    )
