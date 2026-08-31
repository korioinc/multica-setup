"""Pure reconciliation planning from desired and remote state."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

from .domain import (
    DesiredAgent,
    DesiredAutopilot,
    DesiredSkill,
    DesiredSquad,
    DesiredState,
    FieldChange,
    FileChange,
    Operation,
    Plan,
    RemoteAgent,
    RemoteSkill,
    RemoteSquad,
    RemoteState,
    ResourceBinding,
)
from .errors import ExportError
from .identity import _autopilot_trigger_binding_slug, _make_portable_keys
from .normalization import (
    _canonical_optional_text,
    _runtime_device_name,
    _terminal_text,
)


def _field_change(
    field: str,
    before: Any,
    after: Any,
    *,
    sensitive: bool = False,
    always: bool = False,
) -> FieldChange | None:
    if not always and before == after:
        return None
    return FieldChange(field=field, before=before, after=after, sensitive=sensitive)


def _changes(*values: FieldChange | None) -> tuple[FieldChange, ...]:
    return tuple(value for value in values if value is not None)


def _runtime_records(current: RemoteState) -> tuple[dict[str, Any], ...]:
    return tuple(dict(value) for value in current.runtimes)


def _resolve_runtime(
    agent: DesiredAgent, runtimes: Sequence[dict[str, Any]]
) -> tuple[str, tuple[str | None, str | None]]:
    matches = [
        runtime
        for runtime in runtimes
        if runtime["provider"] == agent.provider
        and _runtime_device_name(runtime) == agent.runtime
    ]
    if len(matches) != 1:
        raise ExportError(
            f"agent {_terminal_text(agent.name)!r}: runtime pair "
            f"({_terminal_text(agent.provider or '')!r}, {_terminal_text(agent.runtime or '')!r}) "
            f"matched {len(matches)} runtimes"
        )
    return matches[0]["id"], (agent.provider, agent.runtime)


def _runtime_pair(
    runtime_id: str | None, runtimes_by_id: dict[str, dict[str, Any]], label: str
) -> tuple[str | None, str | None] | None:
    if runtime_id is None:
        return None
    runtime = runtimes_by_id.get(runtime_id)
    if runtime is None:
        raise ExportError(f"{label}: references unlisted runtime {runtime_id}")
    return (runtime["provider"], _runtime_device_name(runtime))


def _skill_file_changes(
    desired: DesiredSkill, current: RemoteSkill | None
) -> tuple[FileChange, ...]:
    desired_files = dict(desired.files)
    current_files = {value.path: value for value in current.files} if current else {}
    result: list[FileChange] = []
    for path in sorted(set(desired_files) | set(current_files)):
        desired_content = desired_files.get(path)
        current_file = current_files.get(path)
        current_content = current_file.content if current_file else None
        if current_file is None:
            result.append(
                FileChange(
                    action="add",
                    path=path,
                    remote_id=None,
                    before=None,
                    after=desired_content,
                )
            )
        elif desired_content is None:
            result.append(
                FileChange(
                    action="delete",
                    path=path,
                    remote_id=current_file.id,
                    before=current_content,
                    after=None,
                )
            )
        elif desired_content != current_content:
            result.append(
                FileChange(
                    action="update",
                    path=path,
                    remote_id=current_file.id,
                    before=current_content,
                    after=desired_content,
                )
            )
    return tuple(result)


def _remote_skill_names_by_id(current: RemoteState) -> dict[str, str]:
    return {resource_id: name for resource_id, name in current.active_skill_summaries}


def _remote_agent_names_by_id(current: RemoteState) -> dict[str, str]:
    return {
        resource_id: name
        for resource_id, name in (
            current.active_agent_summaries + current.archived_agent_summaries
        )
    }


def _agent_relationship_names(
    agent: RemoteAgent, skill_names_by_id: dict[str, str]
) -> tuple[str, ...]:
    missing = set(agent.skill_ids) - set(skill_names_by_id)
    if missing:
        raise ExportError(
            f"agent {_terminal_text(agent.name)!r} references unlisted skill "
            f"{sorted(missing)[0]}"
        )
    return tuple(sorted(skill_names_by_id[value] for value in agent.skill_ids))


def _desired_agent_skill_names(
    agent: DesiredAgent, desired_skills_by_slug: dict[str, DesiredSkill]
) -> tuple[str, ...]:
    return tuple(
        sorted(desired_skills_by_slug[value].name for value in agent.skill_slugs)
    )


def _agent_skill_change(
    agent: RemoteAgent,
    desired_names: tuple[str, ...],
    desired_ids: tuple[str | None, ...],
    skill_names_by_id: dict[str, str],
) -> FieldChange | None:
    if all(value is not None for value in desired_ids) and tuple(
        sorted(value for value in desired_ids if value is not None)
    ) == tuple(sorted(agent.skill_ids)):
        return None
    return _field_change(
        "skills",
        _agent_relationship_names(agent, skill_names_by_id),
        desired_names,
    )


def _desired_squad_projection(
    squad: DesiredSquad,
    desired_agents_by_slug: dict[str, DesiredAgent],
    target_agent_ids_by_slug: dict[str, str | None],
) -> tuple[
    str,
    tuple[str, ...],
    tuple[tuple[str, str | None], ...],
    tuple[str, str | None],
    tuple[tuple[str, str | None], ...],
]:
    leader = next(value for value in squad.members if value.leader)
    leader_name = desired_agents_by_slug[leader.agent_slug].name
    member_names = tuple(
        sorted(desired_agents_by_slug[value.agent_slug].name for value in squad.members)
    )
    roles = tuple(
        sorted(
            (
                desired_agents_by_slug[value.agent_slug].name,
                _canonical_optional_text(value.role),
            )
            for value in squad.members
        )
    )
    leader_identity = (leader_name, target_agent_ids_by_slug[leader.agent_slug])
    member_identities = tuple(
        sorted(
            (
                desired_agents_by_slug[value.agent_slug].name,
                target_agent_ids_by_slug[value.agent_slug],
            )
            for value in squad.members
        )
    )
    return leader_name, member_names, roles, leader_identity, member_identities


def _remote_squad_projection(
    squad: RemoteSquad, agent_names_by_id: dict[str, str]
) -> tuple[
    str,
    tuple[str, ...],
    tuple[tuple[str, str | None], ...],
    tuple[str, str],
    tuple[tuple[str, str], ...],
]:
    referenced_ids = {value.agent_id for value in squad.members} | {squad.leader_id}
    missing = referenced_ids - set(agent_names_by_id)
    if missing:
        raise ExportError(
            f"squad {_terminal_text(squad.name)!r} references unlisted agent "
            f"{sorted(missing)[0]}"
        )
    roles_by_id = {value.agent_id: value.role for value in squad.members}
    roles_by_id.setdefault(squad.leader_id, None)
    leader_name = agent_names_by_id[squad.leader_id]
    return (
        leader_name,
        tuple(sorted(agent_names_by_id[value] for value in roles_by_id)),
        tuple(
            sorted(
                (agent_names_by_id[value], _canonical_optional_text(role))
                for value, role in roles_by_id.items()
            )
        ),
        (leader_name, squad.leader_id),
        tuple(sorted((agent_names_by_id[value], value) for value in roles_by_id)),
    )


def _role_changes_by_identity(
    current_roles: Sequence[tuple[str, str | None]],
    current_identities: Sequence[tuple[str, str]],
    desired_roles: Sequence[tuple[str, str | None]],
    desired_identities: Sequence[tuple[str, str | None]],
) -> tuple[FieldChange, ...]:
    current_roles_by_name = dict(current_roles)
    current_roles_by_id = {
        resource_id: current_roles_by_name.get(name)
        for name, resource_id in current_identities
    }
    desired_roles_by_name = dict(desired_roles)
    result: list[FieldChange] = []
    for name, resource_id in desired_identities:
        before = (
            current_roles_by_id.get(resource_id) if resource_id is not None else None
        )
        change = _field_change(
            f"role[{name}]",
            before,
            desired_roles_by_name.get(name),
            sensitive=True,
        )
        if change is not None:
            result.append(change)
    return tuple(result)


def _quick_action_assignee_name(
    assignee_type: str, assignee_id: str, current: RemoteState
) -> str:
    summaries = (
        current.active_agent_summaries + current.archived_agent_summaries
        if assignee_type == "agent"
        else current.active_squad_summaries
    )
    return dict(summaries).get(assignee_id, "<missing>")


def _resolve_autopilot_project(
    desired: DesiredAutopilot, current: RemoteState
) -> tuple[str | None, str | None]:
    if desired.project is None:
        return None, None
    projects_by_id = dict(current.projects)
    keyed_bindings = [
        value
        for value in current.bindings
        if value.resource_type == "autopilot-project" and value.slug == desired.project
    ]
    missing_bound = [
        value for value in keyed_bindings if value.remote_id not in projects_by_id
    ]
    if missing_bound:
        raise ExportError(
            f"autopilot project {_terminal_text(desired.project)!r} binding points "
            f"to missing remote id {missing_bound[0].remote_id}; remove or repair the binding"
        )
    bound = keyed_bindings
    if len(bound) > 1:
        raise ExportError(
            f"autopilot project {_terminal_text(desired.project)!r} has ambiguous bindings"
        )
    if bound:
        return bound[0].remote_id, desired.project
    raise ExportError(
        f"autopilot project key {_terminal_text(desired.project)!r} has no "
        "workspace binding; run export for this workspace before plan/apply"
    )


def _remote_autopilot_project_key(
    project_id: str | None, current: RemoteState
) -> str | None:
    if project_id is None:
        return None
    for binding in current.bindings:
        if (
            binding.resource_type == "autopilot-project"
            and binding.remote_id == project_id
        ):
            return binding.slug
    projects = dict(current.projects)
    if project_id not in projects:
        return "<missing>"
    return _make_portable_keys(
        [{"id": resource_id, "name": name} for resource_id, name in current.projects],
        "project",
    )[project_id]


def _resolve_autopilot_subscribers(
    desired: DesiredAutopilot, current: RemoteState
) -> tuple[tuple[str, str], ...]:
    by_email = {
        email.casefold(): (user_id, email) for user_id, email in current.members
    }
    result: list[tuple[str, str]] = []
    for email in desired.subscribers:
        target = by_email.get(email.casefold())
        if target is None:
            raise ExportError(
                f"autopilot {_terminal_text(desired.name)!r} subscriber "
                f"{_terminal_text(email)!r} is not a workspace member"
            )
        result.append(target)
    return tuple(sorted(result, key=lambda value: value[0]))


def _autopilot_trigger_plan_projection(value: Any) -> tuple[Any, ...]:
    return (
        value.kind,
        value.enabled,
        value.label,
        value.cron_expression,
        value.timezone,
    )


def build_plan(desired: DesiredState, current: RemoteState) -> Plan:
    workspace = dict(current.workspace)
    if workspace["id"] != desired.workspace_id:
        raise ExportError("planner received mismatched workspace identities")

    matches_by_type_and_slug = {
        (value.resource_type, value.slug): value for value in current.matches
    }

    operations: list[Operation] = []
    workspace_changes = _changes(
        _field_change("name", workspace["name"], desired.workspace_name),
        _field_change(
            "description",
            _canonical_optional_text(workspace["description"]),
            desired.workspace_description,
            sensitive=True,
        ),
        _field_change("issue_prefix", workspace["issue_prefix"], desired.issue_prefix),
        _field_change(
            "context",
            _canonical_optional_text(workspace["context"]),
            _canonical_optional_text(desired.workspace_context),
            sensitive=True,
        ),
    )
    if workspace_changes:
        operations.append(
            Operation(
                phase=0,
                action="update",
                resource_type="workspace",
                name=desired.workspace_name,
                remote_id=desired.workspace_id,
                desired_slug=None,
                changes=workspace_changes,
                desired=(
                    ("name", desired.workspace_name),
                    ("description", desired.workspace_description),
                    ("issue_prefix", desired.issue_prefix),
                    ("context", desired.workspace_context),
                ),
            )
        )

    remote_skills = {value.id: value for value in current.skills}
    matched_skill_ids: set[str] = set()
    for skill in desired.skills:
        match = matches_by_type_and_slug.get(("skill", skill.slug))
        remote_skill = remote_skills.get(match.remote_id) if match is not None else None
        if remote_skill is None:
            file_changes = _skill_file_changes(skill, None)
            operations.append(
                Operation(
                    phase=10,
                    action="create",
                    resource_type="skill",
                    name=skill.name,
                    remote_id=None,
                    desired_slug=skill.slug,
                    changes=_changes(
                        _field_change(
                            "description",
                            None,
                            skill.description,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change(
                            "SKILL.md",
                            None,
                            skill.document,
                            sensitive=True,
                            always=True,
                        ),
                    ),
                    desired=skill,
                    file_changes=file_changes,
                )
            )
            continue
        matched_skill_ids.add(remote_skill.id)
        file_changes = _skill_file_changes(skill, remote_skill)
        changes = _changes(
            _field_change("name", remote_skill.name, skill.name),
            _field_change(
                "description",
                remote_skill.description,
                skill.description,
                sensitive=True,
            ),
            _field_change(
                "SKILL.md", remote_skill.document, skill.document, sensitive=True
            ),
        )
        if changes or file_changes:
            operations.append(
                Operation(
                    phase=10,
                    action="update",
                    resource_type="skill",
                    name=skill.name,
                    remote_id=remote_skill.id,
                    desired_slug=skill.slug,
                    changes=changes,
                    desired=skill,
                    file_changes=file_changes,
                )
            )

    runtimes = _runtime_records(current)
    runtimes_by_id = {value["id"]: value for value in runtimes}
    remote_agents = {value.id: value for value in current.agents}
    matched_agent_ids: set[str] = set()
    desired_skills_by_slug = {value.slug: value for value in desired.skills}
    skill_names_by_id = _remote_skill_names_by_id(current)
    target_skill_ids_by_slug = {
        value.slug: (
            matches_by_type_and_slug[("skill", value.slug)].remote_id
            if ("skill", value.slug) in matches_by_type_and_slug
            else None
        )
        for value in desired.skills
    }
    for agent in desired.agents:
        target_runtime_id, desired_runtime_pair = _resolve_runtime(agent, runtimes)
        desired_skill_names_for_agent = _desired_agent_skill_names(
            agent, desired_skills_by_slug
        )
        desired_skill_ids_for_agent = tuple(
            target_skill_ids_by_slug[value] for value in agent.skill_slugs
        )
        match = matches_by_type_and_slug.get(("agent", agent.slug))
        remote_agent = remote_agents.get(match.remote_id) if match is not None else None
        if remote_agent is None:
            operations.append(
                Operation(
                    phase=20,
                    action="create",
                    resource_type="agent",
                    name=agent.name,
                    remote_id=None,
                    desired_slug=agent.slug,
                    changes=_changes(
                        _field_change(
                            "description",
                            None,
                            agent.description,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change(
                            "instructions",
                            None,
                            agent.instructions,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change(
                            "runtime", None, desired_runtime_pair, always=True
                        ),
                        _field_change("model", None, agent.model, always=True),
                        _field_change(
                            "max_concurrent_tasks",
                            None,
                            agent.max_concurrent_tasks,
                            always=True,
                        ),
                        _field_change(
                            "skills", None, desired_skill_names_for_agent, always=True
                        ),
                        _field_change(
                            "permission_mode", None, "public_to", always=True
                        ),
                        _field_change(
                            "invocation_scope", None, "workspace", always=True
                        ),
                    ),
                    desired=agent,
                    dependencies=desired_skill_names_for_agent,
                    target_runtime_id=target_runtime_id,
                )
            )
            continue
        matched_agent_ids.add(remote_agent.id)
        current_runtime_pair = _runtime_pair(
            remote_agent.runtime_id, runtimes_by_id, f"agent {agent.name!r}"
        )
        changes = list(
            _changes(
                _field_change("name", remote_agent.name, agent.name),
                _field_change(
                    "description",
                    remote_agent.description,
                    agent.description,
                    sensitive=True,
                ),
                _field_change(
                    "instructions",
                    remote_agent.instructions,
                    _canonical_optional_text(agent.instructions),
                    sensitive=True,
                ),
                _field_change("runtime", current_runtime_pair, desired_runtime_pair),
                _field_change("model", remote_agent.model, agent.model),
                _field_change(
                    "max_concurrent_tasks",
                    remote_agent.max_concurrent_tasks,
                    agent.max_concurrent_tasks,
                ),
                _agent_skill_change(
                    remote_agent,
                    desired_skill_names_for_agent,
                    desired_skill_ids_for_agent,
                    skill_names_by_id,
                ),
            )
        )
        action = "restore" if remote_agent.archived else "update"
        if remote_agent.archived:
            changes.append(
                FieldChange(
                    field="permission_mode (preserved)",
                    before=remote_agent.permission_summary,
                    after=remote_agent.permission_summary,
                )
            )
        if changes:
            operations.append(
                Operation(
                    phase=20,
                    action=action,
                    resource_type="agent",
                    name=agent.name,
                    remote_id=remote_agent.id,
                    desired_slug=agent.slug,
                    changes=tuple(changes),
                    desired=agent,
                    dependencies=desired_skill_names_for_agent,
                    target_runtime_id=target_runtime_id,
                )
            )

    remote_squads = {value.id: value for value in current.squads}
    matched_squad_ids: set[str] = set()
    desired_agents_by_slug = {value.slug: value for value in desired.agents}
    agent_names_by_id = _remote_agent_names_by_id(current)
    target_agent_ids_by_slug = {
        value.slug: (
            matches_by_type_and_slug[("agent", value.slug)].remote_id
            if ("agent", value.slug) in matches_by_type_and_slug
            else None
        )
        for value in desired.agents
    }
    for squad in desired.squads:
        desired_member_targets = tuple(
            sorted(
                (
                    desired_agents_by_slug[value.agent_slug].name,
                    value.role,
                    value.leader,
                )
                for value in squad.members
            )
        )
        (
            desired_leader,
            desired_members,
            desired_roles,
            desired_leader_identity,
            desired_member_identities,
        ) = _desired_squad_projection(
            squad, desired_agents_by_slug, target_agent_ids_by_slug
        )
        match = matches_by_type_and_slug.get(("squad", squad.slug))
        remote_squad = remote_squads.get(match.remote_id) if match is not None else None
        if remote_squad is None:
            create_role_changes = tuple(
                FieldChange(
                    field=f"role[{name}]",
                    before=None,
                    after=role,
                    sensitive=True,
                )
                for name, role in desired_roles
                if role is not None
            )
            operations.append(
                Operation(
                    phase=30,
                    action="create",
                    resource_type="squad",
                    name=squad.name,
                    remote_id=None,
                    desired_slug=squad.slug,
                    changes=(
                        *_changes(
                            _field_change(
                                "description",
                                None,
                                squad.description,
                                sensitive=True,
                                always=True,
                            ),
                            _field_change(
                                "instructions",
                                None,
                                squad.instructions,
                                sensitive=True,
                                always=True,
                            ),
                            _field_change("leader", None, desired_leader, always=True),
                            _field_change(
                                "members", None, desired_members, always=True
                            ),
                        ),
                        *create_role_changes,
                    ),
                    desired=squad,
                    dependencies=desired_members,
                    member_targets=desired_member_targets,
                )
            )
            continue
        matched_squad_ids.add(remote_squad.id)
        (
            current_leader,
            current_members,
            current_roles,
            current_leader_identity,
            current_member_identities,
        ) = _remote_squad_projection(remote_squad, agent_names_by_id)
        leader_identity_changed = (
            current_leader_identity[1] != desired_leader_identity[1]
        )
        member_identities_changed = {
            resource_id for _, resource_id in current_member_identities
        } != {resource_id for _, resource_id in desired_member_identities}
        changes = (
            *_changes(
                _field_change("name", remote_squad.name, squad.name),
                _field_change(
                    "description",
                    remote_squad.description,
                    squad.description,
                    sensitive=True,
                ),
                _field_change(
                    "instructions",
                    remote_squad.instructions,
                    _canonical_optional_text(squad.instructions),
                    sensitive=True,
                ),
                (
                    _field_change("leader", current_leader, desired_leader)
                    if leader_identity_changed
                    else None
                ),
                (
                    _field_change("members", current_members, desired_members)
                    if member_identities_changed
                    else None
                ),
                (
                    _field_change(
                        "leader_identity",
                        current_leader_identity,
                        desired_leader_identity,
                    )
                    if leader_identity_changed
                    else None
                ),
                (
                    _field_change(
                        "member_identities",
                        current_member_identities,
                        desired_member_identities,
                    )
                    if member_identities_changed
                    else None
                ),
            ),
            *_role_changes_by_identity(
                current_roles,
                current_member_identities,
                desired_roles,
                desired_member_identities,
            ),
        )
        if changes:
            operations.append(
                Operation(
                    phase=30,
                    action="update",
                    resource_type="squad",
                    name=squad.name,
                    remote_id=remote_squad.id,
                    desired_slug=squad.slug,
                    changes=changes,
                    desired=squad,
                    dependencies=desired_members,
                    member_targets=desired_member_targets,
                )
            )

    remote_autopilots = {value.id: value for value in current.autopilots}
    matched_autopilot_ids: set[str] = set()
    desired_squads_by_slug = {value.slug: value for value in desired.squads}
    trigger_matches_by_slug = {
        value.slug: value for value in current.autopilot_trigger_matches
    }
    matched_trigger_ids: set[str] = set()
    member_emails_by_id = dict(current.members)

    for autopilot in desired.autopilots:
        target_resource = (
            desired_agents_by_slug[autopilot.assignee_slug]
            if autopilot.assignee_type == "agent"
            else desired_squads_by_slug[autopilot.assignee_slug]
        )
        target_match = matches_by_type_and_slug.get(
            (autopilot.assignee_type, autopilot.assignee_slug)
        )
        target_id = target_match.remote_id if target_match is not None else None
        desired_project_id, desired_project_key = _resolve_autopilot_project(
            autopilot, current
        )
        subscriber_targets = _resolve_autopilot_subscribers(autopilot, current)
        desired_subscriber_emails = tuple(
            sorted((value[1] for value in subscriber_targets), key=str.casefold)
        )
        match = matches_by_type_and_slug.get(("autopilot", autopilot.slug))
        remote_autopilot = (
            remote_autopilots.get(match.remote_id) if match is not None else None
        )
        desired_assignee = (autopilot.assignee_type, target_resource.name)
        if remote_autopilot is None:
            operations.append(
                Operation(
                    phase=35,
                    action="create",
                    resource_type="autopilot",
                    name=autopilot.name,
                    remote_id=None,
                    desired_slug=autopilot.slug,
                    changes=_changes(
                        _field_change(
                            "prompt",
                            None,
                            autopilot.prompt,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change("assignee", None, desired_assignee, always=True),
                        _field_change(
                            "execution_mode",
                            None,
                            autopilot.execution_mode,
                            always=True,
                        ),
                        _field_change(
                            "project", None, desired_project_key, always=True
                        ),
                        _field_change(
                            "subscribers",
                            None,
                            desired_subscriber_emails,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change("status", None, autopilot.status, always=True),
                    ),
                    desired=autopilot,
                    dependencies=(target_resource.name,),
                    target_project_id=desired_project_id,
                    subscriber_targets=subscriber_targets,
                )
            )
        else:
            matched_autopilot_ids.add(remote_autopilot.id)
            current_assignee = (
                remote_autopilot.assignee_type,
                _quick_action_assignee_name(
                    remote_autopilot.assignee_type,
                    remote_autopilot.assignee_id,
                    current,
                ),
            )
            assignee_changed = (
                target_id is None
                or remote_autopilot.assignee_type != autopilot.assignee_type
                or remote_autopilot.assignee_id != target_id
            )
            current_subscribers = tuple(
                sorted(
                    member_emails_by_id.get(value, "<missing>")
                    for value in remote_autopilot.subscribers
                )
            )
            changes = _changes(
                _field_change("name", remote_autopilot.name, autopilot.name),
                _field_change(
                    "prompt", remote_autopilot.prompt, autopilot.prompt, sensitive=True
                ),
                (
                    _field_change("assignee", current_assignee, desired_assignee)
                    if assignee_changed
                    else None
                ),
                _field_change(
                    "execution_mode",
                    remote_autopilot.execution_mode,
                    autopilot.execution_mode,
                ),
                (
                    _field_change(
                        "project",
                        _remote_autopilot_project_key(
                            remote_autopilot.project_id, current
                        ),
                        desired_project_key,
                        always=True,
                    )
                    if remote_autopilot.project_id != desired_project_id
                    else None
                ),
                _field_change(
                    "subscribers",
                    current_subscribers,
                    desired_subscriber_emails,
                    sensitive=True,
                ),
                _field_change("status", remote_autopilot.status, autopilot.status),
            )
            if changes:
                if not remote_autopilot.can_write:
                    raise ExportError(
                        f"autopilot {_terminal_text(remote_autopilot.name)!r} cannot be "
                        "updated by the current user"
                    )
                operations.append(
                    Operation(
                        phase=35,
                        action="update",
                        resource_type="autopilot",
                        name=autopilot.name,
                        remote_id=remote_autopilot.id,
                        desired_slug=autopilot.slug,
                        changes=changes,
                        desired=autopilot,
                        dependencies=(target_resource.name,),
                        target_project_id=desired_project_id,
                        subscriber_targets=subscriber_targets,
                    )
                )

        remote_triggers = (
            {
                value.id: value
                for value in remote_autopilot.triggers
                if value.kind == "schedule"
            }
            if remote_autopilot is not None
            else {}
        )
        for trigger in autopilot.triggers:
            binding_slug = _autopilot_trigger_binding_slug(autopilot.slug, trigger.key)
            trigger_match = trigger_matches_by_slug.get(binding_slug)
            remote_trigger = (
                remote_triggers.get(trigger_match.remote_id)
                if trigger_match is not None
                else None
            )
            if remote_trigger is None:
                if remote_autopilot is not None and not remote_autopilot.can_write:
                    raise ExportError(
                        f"autopilot {_terminal_text(remote_autopilot.name)!r} triggers "
                        "cannot be created by the current user"
                    )
                operations.append(
                    Operation(
                        phase=37,
                        action="create",
                        resource_type="autopilot-trigger",
                        name=f"{autopilot.name}/{trigger.key}",
                        remote_id=None,
                        desired_slug=binding_slug,
                        changes=_changes(
                            _field_change(
                                "configuration",
                                None,
                                _autopilot_trigger_plan_projection(trigger),
                                always=True,
                            )
                        ),
                        desired=trigger,
                        dependencies=(autopilot.name,),
                        parent_slug=autopilot.slug,
                    )
                )
                continue
            matched_trigger_ids.add(remote_trigger.id)
            if remote_trigger.kind != trigger.kind:
                raise ExportError(
                    f"autopilot {_terminal_text(autopilot.name)!r} trigger "
                    f"{_terminal_text(trigger.key)!r} changes immutable kind; "
                    "use a new trigger key so plan shows an explicit replacement"
                )
            trigger_changes = _changes(
                _field_change("enabled", remote_trigger.enabled, trigger.enabled),
                _field_change("label", remote_trigger.label, trigger.label),
                _field_change(
                    "cron_expression",
                    remote_trigger.cron_expression,
                    trigger.cron_expression,
                ),
                _field_change("timezone", remote_trigger.timezone, trigger.timezone),
            )
            if trigger_changes:
                if remote_autopilot is not None and not remote_autopilot.can_write:
                    raise ExportError(
                        f"autopilot {_terminal_text(remote_autopilot.name)!r} triggers "
                        "cannot be updated by the current user"
                    )
                operations.append(
                    Operation(
                        phase=37,
                        action="update",
                        resource_type="autopilot-trigger",
                        name=f"{autopilot.name}/{trigger.key}",
                        remote_id=remote_trigger.id,
                        desired_slug=binding_slug,
                        changes=trigger_changes,
                        desired=trigger,
                        dependencies=(autopilot.name,),
                        parent_slug=autopilot.slug,
                    )
                )

        if remote_autopilot is not None:
            for remote_trigger in remote_autopilot.triggers:
                if remote_trigger.kind != "schedule":
                    continue
                if remote_trigger.id in matched_trigger_ids:
                    continue
                if not remote_autopilot.can_write:
                    raise ExportError(
                        f"autopilot {_terminal_text(remote_autopilot.name)!r} triggers "
                        "cannot be deleted by the current user"
                    )
                operations.append(
                    Operation(
                        phase=40,
                        action="delete",
                        resource_type="autopilot-trigger",
                        name=(
                            f"{autopilot.name}/"
                            f"{remote_trigger.label or remote_trigger.kind}"
                        ),
                        remote_id=remote_trigger.id,
                        desired_slug=None,
                        changes=(),
                        dependencies=(autopilot.name,),
                        parent_slug=autopilot.slug,
                        destructive=True,
                    )
                )

    for resource_id, name in current.active_autopilot_summaries:
        if resource_id in matched_autopilot_ids:
            continue
        remote_autopilot = remote_autopilots[resource_id]
        if not remote_autopilot.can_write:
            raise ExportError(
                f"autopilot {_terminal_text(name)!r} cannot be archived by the current user"
            )
        operations.append(
            Operation(
                phase=40,
                action="archive",
                resource_type="autopilot",
                name=name,
                remote_id=resource_id,
                desired_slug=None,
                changes=(),
                destructive=True,
            )
        )

    remote_quick_actions = {value.id: value for value in current.quick_actions}
    matched_quick_action_ids: set[str] = set()

    def target_will_be_workspace_public(resource_type: str, resource_slug: str) -> bool:
        agent_slug = resource_slug
        if resource_type == "squad":
            squad = desired_squads_by_slug[resource_slug]
            agent_slug = next(
                value.agent_slug for value in squad.members if value.leader
            )
        agent_match = matches_by_type_and_slug.get(("agent", agent_slug))
        if agent_match is None:
            return True
        agent = remote_agents.get(agent_match.remote_id)
        return agent is not None and agent.workspace_public

    for quick_action in desired.quick_actions:
        target_resource = (
            desired_agents_by_slug[quick_action.assignee_slug]
            if quick_action.assignee_type == "agent"
            else desired_squads_by_slug[quick_action.assignee_slug]
        )
        target_match = matches_by_type_and_slug.get(
            (quick_action.assignee_type, quick_action.assignee_slug)
        )
        target_id = target_match.remote_id if target_match is not None else None
        if not target_will_be_workspace_public(
            quick_action.assignee_type, quick_action.assignee_slug
        ):
            raise ExportError(
                f"quick action {_terminal_text(quick_action.name)!r} requires a "
                "workspace-public assignee; update the target agent's invocation "
                "permission before apply"
            )
        desired_assignee = (quick_action.assignee_type, target_resource.name)
        match = matches_by_type_and_slug.get(("quick-action", quick_action.slug))
        remote_quick_action = (
            remote_quick_actions.get(match.remote_id) if match is not None else None
        )
        if remote_quick_action is None:
            operations.append(
                Operation(
                    phase=35,
                    action="create",
                    resource_type="quick-action",
                    name=quick_action.name,
                    remote_id=None,
                    desired_slug=quick_action.slug,
                    changes=_changes(
                        _field_change(
                            "description",
                            None,
                            quick_action.description,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change("assignee", None, desired_assignee, always=True),
                        _field_change(
                            "prompt",
                            None,
                            quick_action.prompt,
                            sensitive=True,
                            always=True,
                        ),
                        _field_change("visibility", None, "public", always=True),
                        _field_change("status", None, "active", always=True),
                    ),
                    desired=quick_action,
                    dependencies=(target_resource.name,),
                )
            )
            continue
        matched_quick_action_ids.add(remote_quick_action.id)
        current_assignee = (
            remote_quick_action.assignee_type,
            _quick_action_assignee_name(
                remote_quick_action.assignee_type,
                remote_quick_action.assignee_id,
                current,
            ),
        )
        assignee_changed = (
            target_id is None
            or remote_quick_action.assignee_type != quick_action.assignee_type
            or remote_quick_action.assignee_id != target_id
        )
        if not assignee_changed and (
            remote_quick_action.target_missing or not remote_quick_action.target_public
        ):
            raise ExportError(
                f"quick action {_terminal_text(quick_action.name)!r} targets an "
                "unavailable or non-public assignee; change assignee_slug or restore "
                "the target's workspace invocation permission"
            )
        changes = _changes(
            _field_change("name", remote_quick_action.name, quick_action.name),
            _field_change(
                "description",
                remote_quick_action.description,
                quick_action.description,
                sensitive=True,
            ),
            (
                _field_change("assignee", current_assignee, desired_assignee)
                if assignee_changed
                else None
            ),
            _field_change(
                "prompt",
                remote_quick_action.prompt,
                quick_action.prompt,
                sensitive=True,
            ),
            _field_change("status", remote_quick_action.status, "active"),
        )
        if changes:
            operations.append(
                Operation(
                    phase=35,
                    action=(
                        "restore"
                        if remote_quick_action.status == "archived"
                        else "update"
                    ),
                    resource_type="quick-action",
                    name=quick_action.name,
                    remote_id=remote_quick_action.id,
                    desired_slug=quick_action.slug,
                    changes=changes,
                    desired=quick_action,
                    dependencies=(target_resource.name,),
                )
            )

    for resource_id, name in current.active_quick_action_summaries:
        if resource_id not in matched_quick_action_ids:
            remote_quick_action = remote_quick_actions[resource_id]
            action = (
                "delete"
                if remote_quick_action.target_missing
                or not remote_quick_action.target_public
                else "archive"
            )
            operations.append(
                Operation(
                    phase=40,
                    action=action,
                    resource_type="quick-action",
                    name=name,
                    remote_id=resource_id,
                    desired_slug=None,
                    changes=(),
                    destructive=True,
                )
            )

    for resource_id, name in current.active_squad_summaries:
        if resource_id not in matched_squad_ids:
            operations.append(
                Operation(
                    phase=50,
                    action="archive",
                    resource_type="squad",
                    name=name,
                    remote_id=resource_id,
                    desired_slug=None,
                    changes=(),
                    destructive=True,
                )
            )
    for resource_id, name in current.active_agent_summaries:
        if resource_id not in matched_agent_ids:
            operations.append(
                Operation(
                    phase=60,
                    action="archive",
                    resource_type="agent",
                    name=name,
                    remote_id=resource_id,
                    desired_slug=None,
                    changes=(),
                    destructive=True,
                )
            )
    for resource_id, name in current.active_skill_summaries:
        if resource_id not in matched_skill_ids:
            operations.append(
                Operation(
                    phase=70,
                    action="delete",
                    resource_type="skill",
                    name=name,
                    remote_id=resource_id,
                    desired_slug=None,
                    changes=(),
                    destructive=True,
                )
            )

    operations.sort(
        key=lambda value: (
            value.phase,
            unicodedata.normalize("NFC", value.name),
            value.remote_id or "",
        )
    )
    desired_by_type_and_slug = {
        **{("skill", value.slug): value for value in desired.skills},
        **{("agent", value.slug): value for value in desired.agents},
        **{("squad", value.slug): value for value in desired.squads},
        **{("autopilot", value.slug): value for value in desired.autopilots},
        **{("quick-action", value.slug): value for value in desired.quick_actions},
    }
    resource_ids: list[tuple[str, str, str, str]] = []
    final_bindings: list[ResourceBinding] = [
        value
        for value in current.bindings
        if (value.resource_type == "quick-action" and not desired.quick_actions_managed)
        or (
            value.resource_type
            in {"autopilot", "autopilot-trigger", "autopilot-project"}
            and not desired.autopilots_managed
        )
    ]
    for match in current.matches:
        desired_resource = desired_by_type_and_slug[(match.resource_type, match.slug)]
        resource_ids.append(
            (
                match.resource_type,
                desired_resource.name,
                match.remote_id,
                match.lifecycle,
            )
        )
        final_bindings.append(
            ResourceBinding(
                resource_type=match.resource_type,
                slug=match.slug,
                remote_id=match.remote_id,
                last_known_name=desired_resource.name,
            )
        )
    desired_trigger_by_binding_slug = {
        _autopilot_trigger_binding_slug(autopilot.slug, trigger.key): trigger
        for autopilot in desired.autopilots
        for trigger in autopilot.triggers
    }
    for match in current.autopilot_trigger_matches:
        trigger = desired_trigger_by_binding_slug[match.slug]
        final_bindings.append(
            ResourceBinding(
                resource_type="autopilot-trigger",
                slug=match.slug,
                remote_id=match.remote_id,
                last_known_name=trigger.label or trigger.key,
            )
        )
    project_keys_by_id: dict[str, set[str]] = {}
    for autopilot in desired.autopilots:
        project_id, _ = _resolve_autopilot_project(autopilot, current)
        if project_id is not None:
            assert autopilot.project is not None
            project_keys_by_id.setdefault(project_id, set()).add(autopilot.project)
    used_project_slugs: set[str] = set()
    for project_id in sorted(project_keys_by_id):
        project_name = dict(current.projects)[project_id]
        project_keys = project_keys_by_id[project_id]
        if len(project_keys) != 1:
            raise ExportError(
                f"autopilot project {_terminal_text(project_name)!r} is referenced "
                "by multiple local keys"
            )
        project_slug = next(iter(project_keys))
        if project_slug.casefold() in used_project_slugs:
            raise ExportError("autopilot project local keys collide")
        used_project_slugs.add(project_slug.casefold())
        final_bindings.append(
            ResourceBinding(
                resource_type="autopilot-project",
                slug=project_slug,
                remote_id=project_id,
                last_known_name=project_name,
            )
        )
    return Plan(
        workspace_id=desired.workspace_id,
        local_fingerprint=desired.fingerprint,
        remote_fingerprint=current.fingerprint,
        resource_ids=tuple(sorted(resource_ids)),
        bindings=tuple(
            sorted(
                final_bindings,
                key=lambda value: (value.resource_type, value.slug),
            )
        ),
        operations=tuple(operations),
        unmanaged_webhook_triggers=sum(
            trigger.kind == "webhook"
            for autopilot in current.autopilots
            for trigger in autopilot.triggers
        ),
    )
