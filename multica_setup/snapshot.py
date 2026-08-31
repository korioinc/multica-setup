"""Remote snapshot collection and API response validation."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

from .client import MulticaClient
from .errors import ExportError
from .identity import _make_portable_keys, _make_slugs
from .normalization import (
    _canonical_optional_text,
    _canonical_text,
)
from .skill_documents import _validate_skill_paths
from .validation import (
    _array,
    _canonical_uuid,
    _nullable_string,
    _object,
    _required,
    _string,
)


def _validate_workspace(raw: Any) -> dict[str, Any]:
    item = _object(raw, "workspace get")
    return {
        "id": _canonical_uuid(
            _required(item, "id", "workspace get"), "workspace get.id"
        ),
        "name": _string(_required(item, "name", "workspace get"), "workspace get.name"),
        "description": _nullable_string(
            item.get("description"), "workspace get.description"
        ),
        "issue_prefix": _string(
            _required(item, "issue_prefix", "workspace get"),
            "workspace get.issue_prefix",
        ),
        "context": _nullable_string(item.get("context"), "workspace get.context"),
    }


def _validate_workspace_list(raw: Any) -> list[dict[str, str]]:
    workspaces = _array(raw, "workspace list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_workspace in enumerate(workspaces):
        endpoint = f"workspace list[{index}]"
        workspace = _object(raw_workspace, endpoint)
        workspace_id = _canonical_uuid(
            _required(workspace, "id", endpoint), f"{endpoint}.id"
        )
        if workspace_id in seen:
            raise ExportError(f"workspace list: duplicate id {workspace_id}")
        seen.add(workspace_id)
        result.append(
            {
                "id": workspace_id,
                "name": _string(
                    _required(workspace, "name", endpoint), f"{endpoint}.name"
                ),
                "slug": _string(
                    _required(workspace, "slug", endpoint), f"{endpoint}.slug"
                ),
            }
        )
    return sorted(
        result,
        key=lambda item: (item["name"].casefold(), item["slug"].casefold(), item["id"]),
    )


def _validate_named_list(
    raw: Any, endpoint: str, name_field: str = "name"
) -> list[dict[str, str]]:
    values = _array(raw, endpoint)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(values):
        item = _object(raw_item, f"{endpoint}[{index}]")
        resource_id = _canonical_uuid(
            _required(item, "id", f"{endpoint}[{index}]"), f"{endpoint}[{index}].id"
        )
        if resource_id in seen:
            raise ExportError(f"{endpoint}: duplicate id {resource_id}")
        seen.add(resource_id)
        result.append(
            {
                "id": resource_id,
                "name": _string(
                    _required(item, name_field, f"{endpoint}[{index}]"),
                    f"{endpoint}[{index}].{name_field}",
                ),
            }
        )
    return result


def _validate_runtime_list(raw: Any) -> list[dict[str, Any]]:
    values = _array(raw, "runtime list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(values):
        endpoint = f"runtime list[{index}]"
        item = _object(raw_item, endpoint)
        resource_id = _canonical_uuid(_required(item, "id", endpoint), f"{endpoint}.id")
        if resource_id in seen:
            raise ExportError(f"runtime list: duplicate id {resource_id}")
        seen.add(resource_id)
        result.append(
            {
                "id": resource_id,
                "name": _nullable_string(item.get("name"), f"{endpoint}.name"),
                "custom_name": _nullable_string(
                    item.get("custom_name"), f"{endpoint}.custom_name"
                ),
                "provider": _nullable_string(
                    item.get("provider"), f"{endpoint}.provider"
                ),
            }
        )
    return result


def _validate_autopilot_list(raw: Any) -> list[dict[str, str]]:
    envelope = _object(raw, "autopilot list")
    values = _array(
        _required(envelope, "autopilots", "autopilot list"), "autopilot list.autopilots"
    )
    return _validate_named_list(values, "autopilot list.autopilots", "title")


def _validate_project_list(raw: Any) -> list[dict[str, str]]:
    values = raw.get("projects") if isinstance(raw, dict) else raw
    return _validate_named_list(values, "project list", "title")


def _validate_workspace_members(raw: Any, workspace_id: str) -> list[dict[str, str]]:
    values = _array(raw, "workspace members")
    result: list[dict[str, str]] = []
    seen_user_ids: set[str] = set()
    seen_emails: set[str] = set()
    for index, raw_value in enumerate(values):
        endpoint = f"workspace members[{index}]"
        value = _object(raw_value, endpoint)
        item_workspace_id = _canonical_uuid(
            _required(value, "workspace_id", endpoint), f"{endpoint}.workspace_id"
        )
        if item_workspace_id != workspace_id:
            raise ExportError(f"{endpoint}: belongs to another workspace")
        user_id = _canonical_uuid(
            _required(value, "user_id", endpoint), f"{endpoint}.user_id"
        )
        email = unicodedata.normalize(
            "NFC", _string(_required(value, "email", endpoint), f"{endpoint}.email")
        ).strip()
        if not email:
            raise ExportError(f"{endpoint}.email: must not be blank")
        folded_email = email.casefold()
        if user_id in seen_user_ids or folded_email in seen_emails:
            raise ExportError("workspace members: duplicate user id or email")
        seen_user_ids.add(user_id)
        seen_emails.add(folded_email)
        result.append(
            {
                "user_id": user_id,
                "email": email,
                "name": unicodedata.normalize(
                    "NFC",
                    _string(_required(value, "name", endpoint), f"{endpoint}.name"),
                ),
            }
        )
    return sorted(result, key=lambda item: (item["email"].casefold(), item["user_id"]))


def _validate_quick_action_list(
    raw: Any, workspace_id: str, *, include_archived: bool
) -> list[dict[str, Any]]:
    envelope = _object(raw, "quick action list")
    values = _array(
        _required(envelope, "quick_actions", "quick action list"),
        "quick action list.quick_actions",
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_value in enumerate(values):
        endpoint = f"quick action list.quick_actions[{index}]"
        value = _object(raw_value, endpoint)
        resource_id = _canonical_uuid(
            _required(value, "id", endpoint), f"{endpoint}.id"
        )
        if resource_id in seen:
            raise ExportError(f"quick action list: duplicate id {resource_id}")
        seen.add(resource_id)
        item_workspace_id = _canonical_uuid(
            _required(value, "workspace_id", endpoint), f"{endpoint}.workspace_id"
        )
        if item_workspace_id != workspace_id:
            raise ExportError(f"{endpoint}: belongs to another workspace")
        visibility = _string(
            _required(value, "visibility", endpoint), f"{endpoint}.visibility"
        )
        if visibility not in {"public", "private"}:
            raise ExportError(
                f"{endpoint}.visibility: unsupported value {visibility!r}"
            )
        status = _string(_required(value, "status", endpoint), f"{endpoint}.status")
        if status not in {"active", "archived"}:
            raise ExportError(f"{endpoint}.status: unsupported value {status!r}")
        if not include_archived and status != "active":
            raise ExportError(f"{endpoint}: archived item returned without request")
        assignee_type = _string(
            _required(value, "assignee_type", endpoint),
            f"{endpoint}.assignee_type",
        )
        if assignee_type not in {"agent", "squad"}:
            raise ExportError(
                f"{endpoint}.assignee_type: unsupported value {assignee_type!r}"
            )
        item = {
            "id": resource_id,
            "name": unicodedata.normalize(
                "NFC", _string(_required(value, "name", endpoint), f"{endpoint}.name")
            ),
            "description": _canonical_text(
                _string(
                    _required(value, "description", endpoint),
                    f"{endpoint}.description",
                )
            ),
            "assignee_type": assignee_type,
            "assignee_id": _canonical_uuid(
                _required(value, "assignee_id", endpoint),
                f"{endpoint}.assignee_id",
            ),
            "prompt": _canonical_text(
                _string(_required(value, "prompt", endpoint), f"{endpoint}.prompt")
            ),
            "visibility": visibility,
            "status": status,
            "target_public": value.get("target_public", False),
            "target_missing": value.get("target_missing", False),
        }
        if not isinstance(item["target_public"], bool):
            raise ExportError(f"{endpoint}.target_public: expected boolean")
        if not isinstance(item["target_missing"], bool):
            raise ExportError(f"{endpoint}.target_missing: expected boolean")
        if visibility == "public":
            result.append(item)
    return sorted(result, key=lambda item: item["id"])


def _matching_detail(
    raw: Any, summary: dict[str, str], endpoint: str, name_field: str = "name"
) -> dict[str, Any]:
    item = _object(raw, endpoint)
    detail_id = _canonical_uuid(_required(item, "id", endpoint), f"{endpoint}.id")
    detail_name = _string(
        _required(item, name_field, endpoint), f"{endpoint}.{name_field}"
    )
    if detail_id != summary["id"] or detail_name != summary["name"]:
        raise ExportError(
            f"{endpoint}: list/detail identity mismatch for {summary['id']}"
        )
    return item


def _validate_agent_detail(raw: Any, summary: dict[str, str]) -> dict[str, Any]:
    item = _matching_detail(raw, summary, "agent get")
    raw_skills = _array(_required(item, "skills", "agent get"), "agent get.skills")
    skill_ids: set[str] = set()
    for index, raw_skill in enumerate(raw_skills):
        skill = _object(raw_skill, f"agent get.skills[{index}]")
        skill_id = _canonical_uuid(
            _required(skill, "id", f"agent get.skills[{index}]"),
            f"agent get.skills[{index}].id",
        )
        if skill_id in skill_ids:
            raise ExportError(f"agent get.skills: duplicate id {skill_id}")
        skill_ids.add(skill_id)
    runtime_id = item.get("runtime_id")
    if runtime_id is not None:
        runtime_id = _canonical_uuid(runtime_id, "agent get.runtime_id")
    max_tasks = item.get("max_concurrent_tasks")
    if max_tasks is not None and (
        isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks <= 0
    ):
        raise ExportError(
            "agent get.max_concurrent_tasks: expected positive integer or null"
        )
    return {
        "id": summary["id"],
        "name": summary["name"],
        "description": _nullable_string(
            item.get("description"), "agent get.description"
        ),
        "instructions": _nullable_string(
            item.get("instructions"), "agent get.instructions"
        ),
        "runtime_id": runtime_id,
        "model": _nullable_string(item.get("model"), "agent get.model"),
        "max_concurrent_tasks": max_tasks,
        "skill_ids": skill_ids,
    }


def _validate_skill_detail(raw: Any, summary: dict[str, str]) -> dict[str, Any]:
    item = _matching_detail(raw, summary, "skill get")
    content = _string(_required(item, "content", "skill get"), "skill get.content")
    raw_files = _array(_required(item, "files", "skill get"), "skill get.files")
    files: list[dict[str, str]] = []
    for index, raw_file in enumerate(raw_files):
        endpoint = f"skill get.files[{index}]"
        file_item = _object(raw_file, endpoint)
        files.append(
            {
                "path": _string(
                    _required(file_item, "path", endpoint), f"{endpoint}.path"
                ),
                "content": _string(
                    _required(file_item, "content", endpoint), f"{endpoint}.content"
                ),
            }
        )
    _validate_skill_paths(files)
    return {
        "id": summary["id"],
        "name": summary["name"],
        "description": _nullable_string(
            item.get("description"), "skill get.description"
        ),
        "content": content,
        "files": files,
    }


def _validate_squad_detail(
    raw: Any, raw_members: Any, summary: dict[str, str]
) -> dict[str, Any]:
    item = _matching_detail(raw, summary, "squad get")
    leader_id = _canonical_uuid(
        _required(item, "leader_id", "squad get"), "squad get.leader_id"
    )
    members: list[dict[str, Any]] = []
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
            {
                "id": member_id,
                "role": _nullable_string(member.get("role"), f"{endpoint}.role"),
            }
        )
    return {
        "id": summary["id"],
        "name": summary["name"],
        "description": _nullable_string(
            item.get("description"), "squad get.description"
        ),
        "instructions": _nullable_string(
            item.get("instructions"), "squad get.instructions"
        ),
        "leader_id": leader_id,
        "members": members,
    }


def _validate_autopilot_trigger(
    raw: Any, autopilot_id: str, index: int
) -> dict[str, Any]:
    endpoint = f"autopilot get.triggers[{index}]"
    value = _object(raw, endpoint)
    trigger_id = _canonical_uuid(_required(value, "id", endpoint), f"{endpoint}.id")
    item_autopilot_id = _canonical_uuid(
        _required(value, "autopilot_id", endpoint), f"{endpoint}.autopilot_id"
    )
    if item_autopilot_id != autopilot_id:
        raise ExportError(f"{endpoint}: belongs to another autopilot")
    kind = _string(_required(value, "kind", endpoint), f"{endpoint}.kind")
    if kind != "schedule":
        return {
            "id": trigger_id,
            "autopilot_id": autopilot_id,
            "kind": kind,
            "enabled": False,
            "label": None,
            "cron_expression": None,
            "timezone": None,
        }
    enabled = _required(value, "enabled", endpoint)
    if not isinstance(enabled, bool):
        raise ExportError(f"{endpoint}.enabled: expected boolean")
    label = _canonical_optional_text(
        _nullable_string(value.get("label"), f"{endpoint}.label")
    )
    cron_expression = _canonical_optional_text(
        _nullable_string(value.get("cron_expression"), f"{endpoint}.cron_expression")
    )
    timezone = _canonical_optional_text(
        _nullable_string(value.get("timezone"), f"{endpoint}.timezone")
    )
    if not cron_expression:
        raise ExportError(f"{endpoint}.cron_expression: required for schedule")
    if not timezone:
        timezone = "UTC"
    return {
        "id": trigger_id,
        "autopilot_id": autopilot_id,
        "kind": kind,
        "enabled": enabled,
        "label": label,
        "cron_expression": cron_expression,
        "timezone": timezone,
    }


def _validate_autopilot_detail(
    raw: Any, summary: dict[str, str], workspace_id: str
) -> dict[str, Any]:
    envelope = _object(raw, "autopilot get")
    _array(
        _required(envelope, "collaborators", "autopilot get"),
        "autopilot get.collaborators",
    )
    raw_triggers = _array(
        _required(envelope, "triggers", "autopilot get"),
        "autopilot get.triggers",
    )
    item = _matching_detail(
        _required(envelope, "autopilot", "autopilot get"),
        summary,
        "autopilot get.autopilot",
        "title",
    )
    endpoint = "autopilot get.autopilot"
    item_workspace_id = _canonical_uuid(
        _required(item, "workspace_id", endpoint), f"{endpoint}.workspace_id"
    )
    if item_workspace_id != workspace_id:
        raise ExportError(f"{endpoint}: belongs to another workspace")
    assignee_type = _string(
        _required(item, "assignee_type", endpoint), f"{endpoint}.assignee_type"
    )
    if assignee_type not in {"agent", "squad"}:
        raise ExportError(
            f"{endpoint}.assignee_type: unsupported value {assignee_type!r}"
        )
    execution_mode = _string(
        _required(item, "execution_mode", endpoint), f"{endpoint}.execution_mode"
    )
    if execution_mode not in {"create_issue", "run_only"}:
        raise ExportError(
            f"{endpoint}.execution_mode: unsupported value {execution_mode!r}"
        )
    status = _string(_required(item, "status", endpoint), f"{endpoint}.status")
    if status not in {"active", "paused"}:
        raise ExportError(f"{endpoint}.status: unsupported value {status!r}")
    project_id = item.get("project_id")
    if project_id is not None:
        project_id = _canonical_uuid(project_id, f"{endpoint}.project_id")
    subscribers: list[str] = []
    seen_subscribers: set[str] = set()
    for index, raw_subscriber in enumerate(
        _array(_required(item, "subscribers", endpoint), f"{endpoint}.subscribers")
    ):
        subscriber_endpoint = f"{endpoint}.subscribers[{index}]"
        subscriber = _object(raw_subscriber, subscriber_endpoint)
        user_type = _string(
            _required(subscriber, "user_type", subscriber_endpoint),
            f"{subscriber_endpoint}.user_type",
        )
        if user_type != "member":
            raise ExportError(
                f"{subscriber_endpoint}.user_type: unsupported value {user_type!r}"
            )
        user_id = _canonical_uuid(
            _required(subscriber, "user_id", subscriber_endpoint),
            f"{subscriber_endpoint}.user_id",
        )
        if user_id in seen_subscribers:
            continue
        seen_subscribers.add(user_id)
        subscribers.append(user_id)
    can_write = _required(item, "can_write", endpoint)
    if not isinstance(can_write, bool):
        raise ExportError(f"{endpoint}.can_write: expected boolean")
    triggers = [
        _validate_autopilot_trigger(value, summary["id"], index)
        for index, value in enumerate(raw_triggers)
    ]
    if len({value["id"] for value in triggers}) != len(triggers):
        raise ExportError("autopilot get.triggers: duplicate id")
    return {
        "id": summary["id"],
        "name": summary["name"],
        "prompt": _canonical_text(
            _nullable_string(item.get("description"), f"{endpoint}.description") or ""
        ),
        "assignee_type": assignee_type,
        "assignee_id": _canonical_uuid(
            _required(item, "assignee_id", endpoint), f"{endpoint}.assignee_id"
        ),
        "execution_mode": execution_mode,
        "project_id": project_id,
        "subscribers": sorted(subscribers),
        "status": status,
        "can_write": can_write,
        "triggers": sorted(triggers, key=lambda value: value["id"]),
    }


def _ids(items: Sequence[dict[str, Any]]) -> set[str]:
    return {item["id"] for item in items}


def _assert_stable(
    label: str, before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]
) -> None:
    if _ids(before) != _ids(after):
        raise ExportError(f"workspace changed during export: {label} inventory changed")


def build_snapshot(
    client: MulticaClient,
    selector: str,
    expected_workspace_id: str | None = None,
) -> dict[str, Any]:
    workspace = _validate_workspace(client.workspace_get(selector))
    workspace_id = workspace["id"]
    if expected_workspace_id is not None and workspace_id != expected_workspace_id:
        raise ExportError(
            "workspace get returned a different workspace than the interactive selection"
        )

    runtimes = _validate_runtime_list(client.runtime_list(workspace_id))
    agent_summaries = _validate_named_list(
        client.agent_list(workspace_id), "agent list"
    )
    skill_summaries = _validate_named_list(
        client.skill_list(workspace_id), "skill list"
    )
    squad_summaries = _validate_named_list(
        client.squad_list(workspace_id), "squad list"
    )
    autopilot_summaries = _validate_autopilot_list(client.autopilot_list(workspace_id))
    projects = (
        _validate_project_list(client.project_list(workspace_id))
        if autopilot_summaries
        else []
    )
    members = (
        _validate_workspace_members(
            client.workspace_members(workspace_id), workspace_id
        )
        if autopilot_summaries
        else []
    )
    quick_actions = _validate_quick_action_list(
        client.quick_action_list(workspace_id, False),
        workspace_id,
        include_archived=False,
    )

    agents = [
        _validate_agent_detail(client.agent_get(item["id"], workspace_id), item)
        for item in sorted(agent_summaries, key=lambda value: value["id"])
    ]
    skills = [
        _validate_skill_detail(client.skill_get(item["id"], workspace_id), item)
        for item in sorted(skill_summaries, key=lambda value: value["id"])
    ]
    squads = [
        _validate_squad_detail(
            client.squad_get(item["id"], workspace_id),
            client.squad_members(item["id"], workspace_id),
            item,
        )
        for item in sorted(squad_summaries, key=lambda value: value["id"])
    ]
    autopilots = [
        _validate_autopilot_detail(
            client.autopilot_get(item["id"], workspace_id), item, workspace_id
        )
        for item in sorted(autopilot_summaries, key=lambda value: value["id"])
    ]

    final_runtimes = _validate_runtime_list(client.runtime_list(workspace_id))
    final_agents = _validate_named_list(client.agent_list(workspace_id), "agent list")
    final_skills = _validate_named_list(client.skill_list(workspace_id), "skill list")
    final_squads = _validate_named_list(client.squad_list(workspace_id), "squad list")
    final_autopilots = _validate_autopilot_list(client.autopilot_list(workspace_id))
    final_projects = (
        _validate_project_list(client.project_list(workspace_id))
        if autopilot_summaries
        else []
    )
    final_members = (
        _validate_workspace_members(
            client.workspace_members(workspace_id), workspace_id
        )
        if autopilot_summaries
        else []
    )
    final_quick_actions = _validate_quick_action_list(
        client.quick_action_list(workspace_id, False),
        workspace_id,
        include_archived=False,
    )
    _assert_stable("runtime", runtimes, final_runtimes)
    _assert_stable("agent", agent_summaries, final_agents)
    _assert_stable("skill", skill_summaries, final_skills)
    _assert_stable("squad", squad_summaries, final_squads)
    _assert_stable("autopilot", autopilot_summaries, final_autopilots)
    _assert_stable("project", projects, final_projects)
    if {value["user_id"] for value in members} != {
        value["user_id"] for value in final_members
    }:
        raise ExportError("workspace changed during export: member inventory changed")
    _assert_stable("quick action", quick_actions, final_quick_actions)

    agent_slugs = _make_slugs(agent_summaries, "agent")
    skill_slugs = _make_slugs(skill_summaries, "skill")
    squad_slugs = _make_slugs(squad_summaries, "squad")
    autopilot_slugs = _make_slugs(autopilot_summaries, "autopilot")
    quick_action_slugs = _make_slugs(quick_actions, "quick-action")
    project_slugs = _make_portable_keys(projects, "project")
    runtime_map = {item["id"]: item for item in runtimes}
    project_map = {item["id"]: item for item in projects}
    member_map = {item["user_id"]: item for item in members}

    for agent in agents:
        missing = agent["skill_ids"] - set(skill_slugs)
        if missing:
            raise ExportError(
                f"agent {agent['name']!r} references unlisted skill {sorted(missing)[0]}"
            )
    for squad in squads:
        referenced = {member["id"] for member in squad["members"]} | {
            squad["leader_id"]
        }
        missing = referenced - set(agent_slugs)
        if missing:
            raise ExportError(
                f"squad {squad['name']!r} references unlisted agent {sorted(missing)[0]}"
            )
    for quick_action in quick_actions:
        targets = (
            agent_slugs if quick_action["assignee_type"] == "agent" else squad_slugs
        )
        if quick_action["assignee_id"] not in targets:
            raise ExportError(
                f"quick action {quick_action['name']!r} references unlisted "
                f"{quick_action['assignee_type']} {quick_action['assignee_id']}"
            )
        if quick_action["target_missing"] or not quick_action["target_public"]:
            raise ExportError(
                f"quick action {quick_action['name']!r} does not have a public, "
                "available target"
            )
    for autopilot in autopilots:
        targets = agent_slugs if autopilot["assignee_type"] == "agent" else squad_slugs
        if autopilot["assignee_id"] not in targets:
            raise ExportError(
                f"autopilot {autopilot['name']!r} references unlisted "
                f"{autopilot['assignee_type']} {autopilot['assignee_id']}"
            )
        project_id = autopilot["project_id"]
        if project_id is not None and project_id not in project_map:
            raise ExportError(
                f"autopilot {autopilot['name']!r} references unlisted project "
                f"{project_id}"
            )
        missing_subscribers = set(autopilot["subscribers"]) - set(member_map)
        if missing_subscribers:
            raise ExportError(
                f"autopilot {autopilot['name']!r} references non-member subscriber "
                f"{sorted(missing_subscribers)[0]}"
            )

    autopilot_trigger_keys: dict[str, dict[str, str]] = {}
    for autopilot in autopilots:
        autopilot_trigger_keys[autopilot["id"]] = _make_portable_keys(
            [
                {
                    "id": trigger["id"],
                    "name": trigger["label"] or trigger["kind"],
                }
                for trigger in autopilot["triggers"]
                if trigger["kind"] == "schedule"
            ],
            "trigger",
        )

    return {
        "workspace": workspace,
        "agents": agents,
        "skills": skills,
        "squads": squads,
        "autopilots": autopilots,
        "quick_actions": quick_actions,
        "runtimes": runtime_map,
        "projects": project_map,
        "project_slugs": project_slugs,
        "members": member_map,
        "agent_slugs": agent_slugs,
        "skill_slugs": skill_slugs,
        "squad_slugs": squad_slugs,
        "autopilot_slugs": autopilot_slugs,
        "autopilot_trigger_keys": autopilot_trigger_keys,
        "unmanaged_webhook_triggers": sum(
            trigger["kind"] == "webhook"
            for autopilot in autopilots
            for trigger in autopilot["triggers"]
        ),
        "quick_action_slugs": quick_action_slugs,
    }
