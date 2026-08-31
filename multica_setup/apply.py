"""Validated execution of planned remote mutations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .bindings import write_bindings
from .client import MulticaClient, _multica_api_config
from .context import discover_repository_root
from .domain import (
    DesiredAgent,
    DesiredAutopilot,
    DesiredAutopilotTrigger,
    DesiredQuickAction,
    DesiredSkill,
    DesiredSquad,
    FieldChange,
    Operation,
    Plan,
)
from .errors import (
    ApplyExecutionError,
    ApplyInterrupted,
    ExportCancelled,
    ExportError,
)
from .normalization import _terminal_text
from .rendering import render_plan
from .snapshot import _validate_quick_action_list
from .validation import _array, _canonical_uuid, _object, _required, _string
from .workflow import plan_workspace


def _operation_change(operation: Operation, field: str) -> FieldChange | None:
    return next((value for value in operation.changes if value.field == field), None)


def _contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(
            _contains_nul(key) or _contains_nul(item) for key, item in value.items()
        )
    if isinstance(value, (tuple, list)):
        return any(_contains_nul(item) for item in value)
    if hasattr(value, "__dataclass_fields__"):
        return _contains_nul(asdict(value))
    return False


def _operation_reference(operation: Operation) -> str:
    return (
        f"{operation.resource_type} "
        f"{json.dumps(_terminal_text(operation.name), ensure_ascii=False)} "
        f"({operation.action})"
    )


def _extract_created_id(raw: Any, resource_type: str, endpoint: str) -> str:
    candidate: Any = None
    if isinstance(raw, str):
        candidate = raw
    elif isinstance(raw, dict):
        candidate = raw.get("id")
        nested = raw.get(resource_type)
        if candidate is None and isinstance(nested, dict):
            candidate = nested.get("id")
        elif candidate is None and isinstance(nested, str):
            candidate = nested
    if candidate is None:
        raise ExportError(f"{endpoint}: response did not include a resource id")
    return _canonical_uuid(candidate, f"{endpoint}.id")


def _validate_apply_plan(plan: Plan) -> None:
    expected = {
        ("workspace", "update"): 0,
        ("skill", "create"): 10,
        ("skill", "update"): 10,
        ("agent", "create"): 20,
        ("agent", "update"): 20,
        ("agent", "restore"): 20,
        ("squad", "create"): 30,
        ("squad", "update"): 30,
        ("autopilot", "create"): 35,
        ("autopilot", "update"): 35,
        ("quick-action", "create"): 35,
        ("quick-action", "update"): 35,
        ("quick-action", "restore"): 35,
        ("autopilot-trigger", "create"): 37,
        ("autopilot-trigger", "update"): 37,
        ("autopilot", "archive"): 40,
        ("autopilot-trigger", "delete"): 40,
        ("quick-action", "archive"): 40,
        ("quick-action", "delete"): 40,
        ("squad", "archive"): 50,
        ("agent", "archive"): 60,
        ("skill", "delete"): 70,
    }
    previous_phase = -1
    pruning = False
    for operation in plan.operations:
        if operation.phase < 40 and not operation.name.strip():
            raise ExportError(
                f"apply cannot create or update a blank {operation.resource_type} name"
            )
        if _contains_nul(operation.desired) or _contains_nul(operation.member_targets):
            raise ExportError(
                f"apply cannot execute {_operation_reference(operation)} because its "
                "desired text contains a NUL character"
            )
        key = (operation.resource_type, operation.action)
        if expected.get(key) != operation.phase or operation.phase < previous_phase:
            raise ExportError(
                f"apply cannot execute invalid plan operation: {_operation_reference(operation)}"
            )
        previous_phase = operation.phase
        if operation.phase >= 40:
            pruning = True
            if not operation.destructive:
                raise ExportError(
                    f"apply prune operation is not marked destructive: "
                    f"{_operation_reference(operation)}"
                )
        elif pruning:
            raise ExportError("apply plan places an upsert after destructive pruning")
        if operation.action == "create":
            if operation.remote_id is not None:
                raise ExportError("apply create operation unexpectedly has a remote id")
        elif operation.remote_id is None:
            raise ExportError(
                f"apply operation is missing a remote id: {_operation_reference(operation)}"
            )

        if operation.resource_type == "workspace":
            desired = dict(operation.desired or ())
            if (
                "issue_prefix" in {value.field for value in operation.changes}
                and not str(desired.get("issue_prefix", "")).strip()
            ):
                raise ExportError("apply cannot clear the workspace issue prefix")
        elif operation.resource_type == "skill" and operation.action in {
            "create",
            "update",
        }:
            if not isinstance(operation.desired, DesiredSkill):
                raise ExportError("apply skill operation is missing desired state")
            for file_change in operation.file_changes:
                if file_change.action == "delete" and file_change.remote_id is None:
                    raise ExportError(
                        f"skill file delete is missing its remote id: "
                        f"{_terminal_text(file_change.path)!r}"
                    )
                if file_change.action in {"add", "update"} and not file_change.after:
                    raise ExportError(
                        f"multica CLI cannot apply an empty skill support file: "
                        f"{_terminal_text(file_change.path)!r}"
                    )
        elif operation.resource_type == "agent" and operation.action in {
            "create",
            "update",
            "restore",
        }:
            if not isinstance(operation.desired, DesiredAgent):
                raise ExportError("apply agent operation is missing desired state")
            max_tasks = operation.desired.max_concurrent_tasks
            max_tasks_change = _operation_change(operation, "max_concurrent_tasks")
            if max_tasks is not None and not 1 <= max_tasks <= 50:
                raise ExportError(
                    f"agent {_terminal_text(operation.name)!r}: "
                    "max_concurrent_tasks must be between 1 and 50"
                )
            if max_tasks is None and (
                operation.action == "create" or max_tasks_change is not None
            ):
                raise ExportError(
                    f"agent {_terminal_text(operation.name)!r}: multica CLI cannot "
                    "create or clear a null max_concurrent_tasks value"
                )
            if operation.target_runtime_id is None:
                raise ExportError(
                    f"agent {_terminal_text(operation.name)!r}: target runtime id is missing"
                )
        elif operation.resource_type == "squad" and operation.action in {
            "create",
            "update",
        }:
            if not isinstance(operation.desired, DesiredSquad):
                raise ExportError("apply squad operation is missing desired state")
            leaders = [value for value in operation.member_targets if value[2]]
            if len(leaders) != 1:
                raise ExportError(
                    f"squad {_terminal_text(operation.name)!r}: expected one leader target"
                )
            role_targets = {value[0]: value[2] for value in operation.member_targets}
            for change in operation.changes:
                if (
                    change.field.startswith("role[")
                    and change.field.endswith("]")
                    and change.after is None
                    and role_targets.get(change.field[5:-1], False)
                ):
                    raise ExportError(
                        f"squad {_terminal_text(operation.name)!r}: multica CLI cannot "
                        "clear the leader role"
                    )
            leader_changes = {"leader", "leader_identity"} & {
                value.field for value in operation.changes
            }
            if leaders[0][1] is None and (
                operation.action == "create" or leader_changes
            ):
                raise ExportError(
                    f"squad {_terminal_text(operation.name)!r}: multica CLI cannot "
                    "create or promote a leader with a null role"
                )
        elif operation.resource_type == "quick-action" and operation.action in {
            "create",
            "update",
            "restore",
        }:
            if not isinstance(operation.desired, DesiredQuickAction):
                raise ExportError(
                    "apply quick action operation is missing desired state"
                )
            if len(operation.dependencies) != 1:
                raise ExportError(
                    f"quick action {_terminal_text(operation.name)!r}: "
                    "expected one assignee target"
                )
        elif operation.resource_type == "autopilot" and operation.action in {
            "create",
            "update",
        }:
            if not isinstance(operation.desired, DesiredAutopilot):
                raise ExportError("apply autopilot operation is missing desired state")
            if len(operation.dependencies) != 1:
                raise ExportError(
                    f"autopilot {_terminal_text(operation.name)!r}: "
                    "expected one assignee target"
                )
        elif operation.resource_type == "autopilot-trigger" and operation.action in {
            "create",
            "update",
        }:
            if not isinstance(operation.desired, DesiredAutopilotTrigger):
                raise ExportError(
                    "apply autopilot trigger operation is missing desired state"
                )
            if operation.desired.kind != "schedule":
                raise ExportError("apply cannot manage non-schedule autopilot triggers")
            if len(operation.dependencies) != 1:
                raise ExportError(
                    f"autopilot trigger {_terminal_text(operation.name)!r}: "
                    "expected one parent autopilot"
                )
            if operation.parent_slug is None:
                raise ExportError(
                    f"autopilot trigger {_terminal_text(operation.name)!r}: "
                    "parent autopilot slug is missing"
                )


def _validate_managed_api_apply_context(plan: Plan) -> None:
    if not any(
        operation.resource_type in {"autopilot", "autopilot-trigger", "quick-action"}
        for operation in plan.operations
    ):
        return
    _, token = _multica_api_config()
    if token.startswith("mat_") or any(
        os.environ.get(value, "").strip()
        for value in (
            "MULTICA_AGENT_ID",
            "MULTICA_TASK_ID",
            "MULTICA_TASK_CONFIG_ROOT",
        )
    ):
        raise ExportError(
            "apply cannot manage autopilots or public quick actions from an agent "
            "execution context"
        )


def _validate_quick_action_apply_role(plan: Plan) -> None:
    if not any(
        operation.resource_type == "quick-action" for operation in plan.operations
    ):
        return
    client = MulticaClient()
    me = _object(client.current_user(plan.workspace_id), "current user")
    user_id = _canonical_uuid(_required(me, "id", "current user"), "current user.id")
    members = _array(client.workspace_members(plan.workspace_id), "workspace members")
    roles: list[str] = []
    for index, raw_member in enumerate(members):
        endpoint = f"workspace members[{index}]"
        member = _object(raw_member, endpoint)
        member_workspace_id = _canonical_uuid(
            _required(member, "workspace_id", endpoint),
            f"{endpoint}.workspace_id",
        )
        if member_workspace_id != plan.workspace_id:
            raise ExportError(f"{endpoint}: belongs to another workspace")
        member_user_id = _canonical_uuid(
            _required(member, "user_id", endpoint), f"{endpoint}.user_id"
        )
        role = _string(_required(member, "role", endpoint), f"{endpoint}.role")
        if member_user_id == user_id:
            roles.append(role)
    if len(roles) != 1:
        raise ExportError(
            "quick action apply preflight could not resolve the current workspace role"
        )
    if roles[0] not in {"owner", "admin"}:
        raise ExportError(
            "apply requires workspace owner or admin role to manage public quick actions"
        )


def _validate_apply_prune_safety(
    plan: Plan, *, allow_planned_quick_action_removals: bool = True
) -> None:
    prune_targets = {
        (operation.resource_type, operation.remote_id): operation
        for operation in plan.operations
        if operation.destructive
        and operation.resource_type in {"agent", "squad"}
        and operation.remote_id is not None
    }
    if not prune_targets:
        return

    client = MulticaClient()
    planned_autopilot_removals = {
        operation.remote_id
        for operation in plan.operations
        if operation.resource_type == "autopilot"
        and operation.action == "archive"
        and operation.remote_id is not None
    }
    raw = client.autopilot_list(plan.workspace_id)
    envelope = _object(raw, "autopilot list")
    values = _array(
        _required(envelope, "autopilots", "autopilot list"),
        "autopilot list.autopilots",
    )
    seen_ids: set[str] = set()
    for index, raw_value in enumerate(values):
        endpoint = f"autopilot list.autopilots[{index}]"
        value = _object(raw_value, endpoint)
        autopilot_id = _canonical_uuid(
            _required(value, "id", endpoint), f"{endpoint}.id"
        )
        if autopilot_id in seen_ids:
            raise ExportError("autopilot list: duplicate resource id")
        seen_ids.add(autopilot_id)
        if (
            allow_planned_quick_action_removals
            and autopilot_id in planned_autopilot_removals
        ):
            continue
        title = _string(_required(value, "title", endpoint), f"{endpoint}.title")
        assignee_type = _string(
            _required(value, "assignee_type", endpoint), f"{endpoint}.assignee_type"
        )
        if assignee_type not in {"agent", "squad"}:
            raise ExportError(
                f"{endpoint}.assignee_type: unsupported value "
                f"{_terminal_text(assignee_type)!r}"
            )
        assignee_id = _canonical_uuid(
            _required(value, "assignee_id", endpoint), f"{endpoint}.assignee_id"
        )
        operation = prune_targets.get((assignee_type, assignee_id))
        if operation is not None:
            raise ExportError(
                f"apply cannot prune {_operation_reference(operation)} because "
                f"autopilot {_terminal_text(title)!r} is assigned to it; reassign or "
                "remove that autopilot, then review a new plan"
            )

    planned_quick_action_removals = {
        operation.remote_id
        for operation in plan.operations
        if operation.resource_type == "quick-action"
        and operation.action in {"archive", "delete"}
        and operation.remote_id is not None
    }
    quick_actions = _validate_quick_action_list(
        client.quick_action_list(plan.workspace_id, False),
        plan.workspace_id,
        include_archived=False,
    )
    for quick_action in quick_actions:
        if (
            allow_planned_quick_action_removals
            and quick_action["id"] in planned_quick_action_removals
        ):
            continue
        operation = prune_targets.get(
            (quick_action["assignee_type"], quick_action["assignee_id"])
        )
        if operation is not None:
            raise ExportError(
                f"apply cannot prune {_operation_reference(operation)} because "
                f"public quick action {_terminal_text(quick_action['name'])!r} is "
                "assigned to it; export and reconcile quick actions or change its "
                "assignee, then review a new plan"
            )


def _resource_id_map(plan: Plan) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for resource_type, name, resource_id, lifecycle in plan.resource_ids:
        key = (resource_type, name)
        if lifecycle == "active" or key not in result:
            result[key] = resource_id
    for binding in plan.bindings:
        if binding.resource_type == "autopilot":
            result[("autopilot-slug", binding.slug)] = binding.remote_id
    return result


def _resolved_id(
    resource_ids: dict[tuple[str, str], str], resource_type: str, name: str
) -> str:
    try:
        return resource_ids[(resource_type, name)]
    except KeyError as exc:
        raise ExportError(
            f"apply could not resolve {resource_type} {_terminal_text(name)!r}"
        ) from exc


def _apply_workspace_operation(
    client: MulticaClient, plan: Plan, operation: Operation
) -> None:
    desired = dict(operation.desired or ())
    changed = {value.field for value in operation.changes}
    base = ["workspace", "update", plan.workspace_id]
    args = list(base)
    if "name" in changed:
        args.extend(("--name", desired["name"]))
    if "issue_prefix" in changed:
        args.extend(("--issue-prefix", desired["issue_prefix"]))
    input_text: str | None = None
    if "description" in changed:
        description = desired["description"]
        if description:
            args.append("--description-stdin")
            input_text = description + "\n"
        else:
            args.extend(("--description", ""))
    if args != base:
        client.write_json(
            args,
            "workspace update",
            plan.workspace_id,
            input_text=input_text,
        )
    if "context" in changed:
        context = desired["context"]
        context_args = [*base]
        context_input: str | None = None
        if context:
            context_args.append("--context-stdin")
            context_input = context + "\n"
        else:
            context_args.extend(("--context", ""))
        client.write_json(
            context_args,
            "workspace update context",
            plan.workspace_id,
            input_text=context_input,
        )


def _apply_skill_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    desired = operation.desired
    if not isinstance(desired, DesiredSkill):
        raise ExportError("apply skill operation is missing desired state")
    changed = {value.field for value in operation.changes}
    if operation.action == "create":
        raw = client.write_json(
            [
                "skill",
                "create",
                "--name",
                desired.name,
                "--description",
                desired.description or "",
                "--content-stdin",
            ],
            "skill create",
            plan.workspace_id,
            input_text=desired.document,
        )
        resource_id = _extract_created_id(raw, "skill", "skill create")
        resource_ids[("skill", desired.name)] = resource_id
    else:
        assert operation.remote_id is not None
        resource_id = operation.remote_id
        args = ["skill", "update", resource_id]
        input_text: str | None = None
        if "name" in changed:
            args.extend(("--name", desired.name))
        if "description" in changed:
            args.extend(("--description", desired.description or ""))
        if "SKILL.md" in changed:
            args.append("--content-stdin")
            input_text = desired.document
        if len(args) > 3:
            client.write_json(
                args,
                "skill update",
                plan.workspace_id,
                input_text=input_text,
            )
        resource_ids[("skill", desired.name)] = resource_id

    for file_change in operation.file_changes:
        if file_change.action in {"add", "update"}:
            assert file_change.after is not None
            client.write_json(
                [
                    "skill",
                    "files",
                    "upsert",
                    resource_id,
                    "--path",
                    file_change.path,
                    "--content-stdin",
                ],
                "skill file upsert",
                plan.workspace_id,
                input_text=file_change.after,
            )
        else:
            assert file_change.remote_id is not None
            client.write_no_output(
                [
                    "skill",
                    "files",
                    "delete",
                    resource_id,
                    file_change.remote_id,
                ],
                "skill file delete",
                plan.workspace_id,
            )


def _agent_update_args(operation: Operation, resource_id: str) -> list[str]:
    desired = operation.desired
    if not isinstance(desired, DesiredAgent):
        raise ExportError("apply agent operation is missing desired state")
    changed = {value.field for value in operation.changes}
    args = ["agent", "update", resource_id]
    if "name" in changed:
        args.extend(("--name", desired.name))
    if "description" in changed:
        args.extend(("--description", desired.description or ""))
    if "instructions" in changed:
        args.extend(("--instructions", desired.instructions))
    if "runtime" in changed:
        assert operation.target_runtime_id is not None
        args.extend(("--runtime-id", operation.target_runtime_id))
    if "model" in changed:
        args.extend(("--model", desired.model or ""))
    if "max_concurrent_tasks" in changed:
        assert desired.max_concurrent_tasks is not None
        args.extend(("--max-concurrent-tasks", str(desired.max_concurrent_tasks)))
    return args


def _apply_agent_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    desired = operation.desired
    if not isinstance(desired, DesiredAgent):
        raise ExportError("apply agent operation is missing desired state")
    if operation.action == "create":
        assert operation.target_runtime_id is not None
        args = [
            "agent",
            "create",
            "--name",
            desired.name,
            "--description",
            desired.description or "",
            "--instructions",
            desired.instructions,
            "--runtime-id",
            operation.target_runtime_id,
            "--permission-mode",
            "public_to",
            "--public-to-workspace",
        ]
        if desired.model is not None:
            args.extend(("--model", desired.model))
        if desired.max_concurrent_tasks is not None:
            args.extend(("--max-concurrent-tasks", str(desired.max_concurrent_tasks)))
        raw = client.write_json(args, "agent create", plan.workspace_id)
        resource_id = _extract_created_id(raw, "agent", "agent create")
    else:
        assert operation.remote_id is not None
        resource_id = operation.remote_id
        if operation.action == "restore":
            client.write_json(
                ["agent", "restore", resource_id],
                "agent restore",
                plan.workspace_id,
            )
        update_args = _agent_update_args(operation, resource_id)
        if len(update_args) > 3:
            client.write_json(
                update_args,
                "agent update",
                plan.workspace_id,
            )
    resource_ids[("agent", desired.name)] = resource_id
    if (
        operation.action in {"create", "restore"}
        or _operation_change(operation, "skills") is not None
    ):
        skill_ids = [
            _resolved_id(resource_ids, "skill", name) for name in operation.dependencies
        ]
        client.write_json(
            [
                "agent",
                "skills",
                "set",
                resource_id,
                "--skill-ids",
                ",".join(skill_ids),
            ],
            "agent skills set",
            plan.workspace_id,
        )


def _member_identity_before(operation: Operation) -> set[str]:
    change = _operation_change(operation, "member_identities")
    if change is None:
        return set()
    return {
        resource_id
        for _, resource_id in change.before or ()
        if isinstance(resource_id, str)
    }


def _apply_squad_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    desired = operation.desired
    if not isinstance(desired, DesiredSquad):
        raise ExportError("apply squad operation is missing desired state")
    targets = {
        name: (role, leader, _resolved_id(resource_ids, "agent", name))
        for name, role, leader in operation.member_targets
    }
    leader_name, (_, _, leader_id) = next(
        (name, value) for name, value in targets.items() if value[1]
    )
    target_ids = {value[2] for value in targets.values()}
    changed = {value.field for value in operation.changes}

    if operation.action == "create":
        raw = client.write_json(
            [
                "squad",
                "create",
                "--name",
                desired.name,
                "--leader",
                leader_id,
                "--description",
                desired.description or "",
            ],
            "squad create",
            plan.workspace_id,
        )
        resource_id = _extract_created_id(raw, "squad", "squad create")
        resource_ids[("squad", desired.name)] = resource_id
        current_ids = {leader_id}
        client.write_json(
            ["squad", "update", resource_id, "--instructions", desired.instructions],
            "squad update",
            plan.workspace_id,
        )
    else:
        assert operation.remote_id is not None
        resource_id = operation.remote_id
        resource_ids[("squad", desired.name)] = resource_id
        current_ids = _member_identity_before(operation) or set(target_ids)
        args = ["squad", "update", resource_id]
        if "name" in changed:
            args.extend(("--name", desired.name))
        if "description" in changed:
            args.extend(("--description", desired.description or ""))
        if "instructions" in changed:
            args.extend(("--instructions", desired.instructions))
        if "leader" in changed or "leader_identity" in changed:
            args.extend(("--leader", leader_id))
            current_ids.add(leader_id)
        if len(args) > 3:
            client.write_json(args, "squad update", plan.workspace_id)

    added_ids: set[str] = set()
    for name, (role, _, member_id) in sorted(targets.items()):
        if member_id not in current_ids:
            client.write_json(
                [
                    "squad",
                    "member",
                    "add",
                    resource_id,
                    "--member-id",
                    member_id,
                    "--type",
                    "agent",
                    "--role",
                    role or "",
                ],
                "squad member add",
                plan.workspace_id,
            )
            current_ids.add(member_id)
            added_ids.add(member_id)

    role_changes = {
        value.field[5:-1]: value
        for value in operation.changes
        if value.field.startswith("role[") and value.field.endswith("]")
    }
    if operation.action == "create":
        role_names = {
            name for name, (role, _, _) in targets.items() if role is not None
        }
    else:
        role_names = {
            name
            for name, change in role_changes.items()
            if name in targets and change.after is not None
        }
        if "leader_identity" in changed and targets[leader_name][0] is not None:
            role_names.add(leader_name)
    for name in sorted(role_names):
        role, _, member_id = targets[name]
        assert role is not None
        client.write_json(
            [
                "squad",
                "member",
                "set-role",
                resource_id,
                "--member-id",
                member_id,
                "--member-type",
                "agent",
                "--role",
                role,
            ],
            "squad member set-role",
            plan.workspace_id,
        )
    clear_role_names = {
        name
        for name, change in role_changes.items()
        if name in targets
        and change.after is None
        and not targets[name][1]
        and targets[name][2] not in added_ids
    }
    for name in sorted(clear_role_names):
        _, _, member_id = targets[name]
        client.write_json(
            [
                "squad",
                "member",
                "remove",
                resource_id,
                "--member-id",
                member_id,
                "--type",
                "agent",
            ],
            "squad member remove",
            plan.workspace_id,
        )
        client.write_json(
            [
                "squad",
                "member",
                "add",
                resource_id,
                "--member-id",
                member_id,
                "--type",
                "agent",
                "--role",
                "",
            ],
            "squad member add",
            plan.workspace_id,
        )

    for member_id in sorted(current_ids - target_ids):
        if member_id == leader_id:
            raise ExportError(
                f"squad {_terminal_text(operation.name)!r}: refusing to remove target leader "
                f"{_terminal_text(leader_name)!r}"
            )
        client.write_json(
            [
                "squad",
                "member",
                "remove",
                resource_id,
                "--member-id",
                member_id,
                "--type",
                "agent",
            ],
            "squad member remove",
            plan.workspace_id,
        )


def _apply_autopilot_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    desired = operation.desired
    if not isinstance(desired, DesiredAutopilot):
        raise ExportError("apply autopilot operation is missing desired state")
    assignee_id = _resolved_id(
        resource_ids, desired.assignee_type, operation.dependencies[0]
    )
    subscribers = [
        {"user_type": "member", "user_id": user_id}
        for user_id, _ in operation.subscriber_targets
    ]
    if operation.action == "create":
        raw = client.autopilot_create(
            plan.workspace_id,
            {
                "title": desired.name,
                "description": desired.prompt or None,
                "project_id": operation.target_project_id,
                "assignee_type": desired.assignee_type,
                "assignee_id": assignee_id,
                "execution_mode": desired.execution_mode,
                "subscribers": subscribers,
            },
        )
        resource_id = _extract_created_id(raw, "autopilot", "autopilot create")
        if desired.status != "active":
            client.autopilot_update(
                resource_id,
                plan.workspace_id,
                {"status": desired.status},
            )
    else:
        assert operation.remote_id is not None
        resource_id = operation.remote_id
        changed = {value.field for value in operation.changes}
        payload: dict[str, Any] = {}
        if "name" in changed:
            payload["title"] = desired.name
        if "prompt" in changed:
            payload["description"] = desired.prompt
        if "assignee" in changed:
            payload["assignee_type"] = desired.assignee_type
            payload["assignee_id"] = assignee_id
        if "execution_mode" in changed:
            payload["execution_mode"] = desired.execution_mode
        if "project" in changed:
            payload["project_id"] = operation.target_project_id
        if "subscribers" in changed:
            payload["subscribers"] = subscribers
        if "status" in changed:
            payload["status"] = desired.status
        if payload:
            client.autopilot_update(resource_id, plan.workspace_id, payload)
    resource_ids[("autopilot", desired.name)] = resource_id
    resource_ids[("autopilot-slug", desired.slug)] = resource_id


def _apply_autopilot_trigger_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    if operation.parent_slug is None:
        raise ExportError("apply autopilot trigger operation is missing parent slug")
    autopilot_id = _resolved_id(resource_ids, "autopilot-slug", operation.parent_slug)
    if operation.action == "delete":
        assert operation.remote_id is not None
        client.autopilot_trigger_delete(
            autopilot_id, operation.remote_id, plan.workspace_id
        )
        return
    desired = operation.desired
    if not isinstance(desired, DesiredAutopilotTrigger):
        raise ExportError("apply autopilot trigger operation is missing desired state")
    if desired.kind != "schedule":
        raise ExportError("apply cannot manage non-schedule autopilot triggers")
    if operation.action == "create":
        payload: dict[str, Any] = {
            "kind": desired.kind,
            "label": desired.label,
        }
        payload["cron_expression"] = desired.cron_expression
        payload["timezone"] = desired.timezone
        raw = client.autopilot_trigger_create(autopilot_id, plan.workspace_id, payload)
        trigger_id = _extract_created_id(
            raw, "autopilot-trigger", "autopilot trigger create"
        )
        if not desired.enabled:
            client.autopilot_trigger_update(
                autopilot_id,
                trigger_id,
                plan.workspace_id,
                {"enabled": False},
            )
        return
    assert operation.remote_id is not None
    changed = {value.field for value in operation.changes}
    payload = {}
    if "enabled" in changed:
        payload["enabled"] = desired.enabled
    if "label" in changed:
        payload["label"] = desired.label or ""
    if "cron_expression" in changed:
        payload["cron_expression"] = desired.cron_expression
    if "timezone" in changed:
        payload["timezone"] = desired.timezone
    if payload:
        client.autopilot_trigger_update(
            autopilot_id,
            operation.remote_id,
            plan.workspace_id,
            payload,
        )


def _apply_quick_action_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    desired = operation.desired
    if not isinstance(desired, DesiredQuickAction):
        raise ExportError("apply quick action operation is missing desired state")
    assignee_id = _resolved_id(
        resource_ids, desired.assignee_type, operation.dependencies[0]
    )
    if operation.action == "create":
        raw = client.quick_action_create(
            plan.workspace_id,
            {
                "name": desired.name,
                "description": desired.description,
                "assignee_type": desired.assignee_type,
                "assignee_id": assignee_id,
                "prompt": desired.prompt,
                "visibility": "public",
            },
        )
        resource_id = _extract_created_id(raw, "quick-action", "quick action create")
    else:
        assert operation.remote_id is not None
        resource_id = operation.remote_id
        changed = {value.field for value in operation.changes}
        payload: dict[str, Any] = {}
        if "name" in changed:
            payload["name"] = desired.name
        if "description" in changed:
            payload["description"] = desired.description
        if "assignee" in changed:
            payload["assignee_type"] = desired.assignee_type
            payload["assignee_id"] = assignee_id
        if "prompt" in changed:
            payload["prompt"] = desired.prompt
        if operation.action == "restore" or "status" in changed:
            payload["status"] = "active"
        if payload:
            client.quick_action_update(resource_id, plan.workspace_id, payload)
    resource_ids[("quick-action", desired.name)] = resource_id


def _execute_operation(
    client: MulticaClient,
    plan: Plan,
    operation: Operation,
    resource_ids: dict[tuple[str, str], str],
) -> None:
    if operation.resource_type == "workspace":
        _apply_workspace_operation(client, plan, operation)
    elif operation.resource_type == "skill" and operation.action in {
        "create",
        "update",
    }:
        _apply_skill_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "agent" and operation.action in {
        "create",
        "update",
        "restore",
    }:
        _apply_agent_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "squad" and operation.action in {
        "create",
        "update",
    }:
        _apply_squad_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "autopilot" and operation.action in {
        "create",
        "update",
    }:
        _apply_autopilot_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "autopilot-trigger" and operation.action in {
        "create",
        "update",
        "delete",
    }:
        _apply_autopilot_trigger_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "quick-action" and operation.action in {
        "create",
        "update",
        "restore",
    }:
        _apply_quick_action_operation(client, plan, operation, resource_ids)
    elif operation.resource_type == "quick-action" and operation.action == "archive":
        assert operation.remote_id is not None
        client.quick_action_update(
            operation.remote_id,
            plan.workspace_id,
            {"status": "archived"},
        )
    elif operation.resource_type == "quick-action" and operation.action == "delete":
        assert operation.remote_id is not None
        client.quick_action_delete(operation.remote_id, plan.workspace_id)
    elif operation.resource_type == "autopilot" and operation.action == "archive":
        assert operation.remote_id is not None
        client.autopilot_delete(operation.remote_id, plan.workspace_id)
    elif operation.resource_type == "squad" and operation.action == "archive":
        assert operation.remote_id is not None
        client.write_json(
            ["squad", "delete", operation.remote_id],
            "squad archive",
            plan.workspace_id,
        )
    elif operation.resource_type == "agent" and operation.action == "archive":
        assert operation.remote_id is not None
        client.write_json(
            ["agent", "archive", operation.remote_id],
            "agent archive",
            plan.workspace_id,
        )
    elif operation.resource_type == "skill" and operation.action == "delete":
        assert operation.remote_id is not None
        client.write_no_output(
            ["skill", "delete", operation.remote_id, "--yes"],
            "skill delete",
            plan.workspace_id,
        )
    else:
        raise ExportError(
            f"apply cannot execute operation: {_operation_reference(operation)}"
        )


def execute_plan(plan: Plan) -> tuple[Operation, ...]:
    client = MulticaClient()
    resource_ids = _resource_id_map(plan)
    completed: list[Operation] = []
    for index, operation in enumerate(plan.operations):
        reference = _operation_reference(operation)
        print(f"{reference}: Applying...")
        try:
            if operation.destructive:
                _validate_apply_prune_safety(
                    plan,
                    allow_planned_quick_action_removals=(
                        operation.resource_type not in {"agent", "squad"}
                    ),
                )
            _execute_operation(client, plan, operation, resource_ids)
        except KeyboardInterrupt as exc:
            raise ApplyInterrupted(
                operation,
                completed,
                plan.operations[index + 1 :],
                "interrupted",
            ) from exc
        except ExportError as exc:
            raise ApplyExecutionError(
                operation,
                completed,
                plan.operations[index + 1 :],
                str(exc),
            ) from exc
        completed.append(operation)
        print(f"{reference}: Apply complete")
    return tuple(completed)


def _approve_apply(auto_approve: bool) -> None:
    if auto_approve:
        return
    print(
        "\nDo you want to perform these actions?\n"
        "  Multica will apply the changes shown above.\n"
        "  Only 'yes' will be accepted to approve.\n\n"
        "  Enter a value: ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    response = sys.stdin.readline()
    if response == "" or response.strip() != "yes":
        raise ExportCancelled


def _plan_counts(plan: Plan) -> tuple[int, int, int]:
    added = sum(value.action == "create" for value in plan.operations)
    changed = sum(value.action in {"update", "restore"} for value in plan.operations)
    destroyed = len(plan.operations) - added - changed
    return added, changed, destroyed


def apply_workspace(
    selector: str,
    expected_workspace_id: str | None,
    *,
    auto_approve: bool,
    color: bool,
    repository_root: Path | None = None,
) -> Plan:
    repository_root = repository_root or discover_repository_root()
    plan = plan_workspace(
        selector,
        expected_workspace_id,
        repository_root=repository_root,
    )
    print(render_plan(plan, color=color, executing=True), end="", flush=True)
    _validate_apply_plan(plan)
    _validate_managed_api_apply_context(plan)
    _validate_quick_action_apply_role(plan)
    if not plan.operations:
        write_bindings(repository_root, plan.workspace_id, plan.bindings)
        return plan

    _validate_apply_prune_safety(plan)
    _approve_apply(auto_approve)
    approved_plan = plan_workspace(
        plan.workspace_id,
        plan.workspace_id,
        repository_root=repository_root,
    )
    if approved_plan != plan:
        raise ExportError(
            "apply aborted before the first mutation because local configuration "
            "or remote state changed after the plan was approved; review a new plan"
        )
    _validate_managed_api_apply_context(approved_plan)
    _validate_quick_action_apply_role(approved_plan)
    _validate_apply_prune_safety(approved_plan)

    execute_plan(plan)
    postflight = plan_workspace(
        plan.workspace_id,
        plan.workspace_id,
        repository_root=repository_root,
    )
    if postflight.operations:
        raise ExportError(
            f"apply completed its planned operations, but postflight found "
            f"{len(postflight.operations)} remaining operation(s); no rollback was "
            "attempted, so review plan and rerun apply"
        )
    write_bindings(repository_root, postflight.workspace_id, postflight.bindings)
    return plan


def _print_apply_failure(error: ApplyExecutionError) -> None:
    print(
        f"error: apply stopped at {_operation_reference(error.operation)}: "
        f"{_terminal_text(str(error))}",
        file=sys.stderr,
    )
    print("Completed operations:", file=sys.stderr)
    if error.completed:
        for operation in error.completed:
            print(f"  - {_operation_reference(operation)}", file=sys.stderr)
    else:
        print("  - none", file=sys.stderr)
    print(
        f"Failed operation:\n  - {_operation_reference(error.operation)}",
        file=sys.stderr,
    )
    print("Pending operations:", file=sys.stderr)
    if error.pending:
        for operation in error.pending:
            print(f"  - {_operation_reference(operation)}", file=sys.stderr)
    else:
        print("  - none", file=sys.stderr)
    print(
        "No rollback was attempted. Review the remote state, then rerun plan/apply.",
        file=sys.stderr,
    )
