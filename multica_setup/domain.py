"""Immutable domain models shared by reconciliation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DesiredSkill:
    slug: str
    name: str
    description: str | None
    document: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DesiredAgent:
    slug: str
    name: str
    description: str | None
    instructions: str
    skill_slugs: tuple[str, ...]
    runtime: str | None
    provider: str | None
    model: str | None
    max_concurrent_tasks: int | None


@dataclass(frozen=True)
class DesiredSquadMember:
    agent_slug: str
    role: str | None
    leader: bool


@dataclass(frozen=True)
class DesiredSquad:
    slug: str
    name: str
    description: str | None
    instructions: str
    members: tuple[DesiredSquadMember, ...]


@dataclass(frozen=True)
class DesiredQuickAction:
    slug: str
    name: str
    description: str
    assignee_type: str
    assignee_slug: str
    prompt: str


@dataclass(frozen=True)
class DesiredAutopilotTrigger:
    key: str
    kind: str
    enabled: bool
    label: str | None
    cron_expression: str | None
    timezone: str | None


@dataclass(frozen=True)
class DesiredAutopilot:
    slug: str
    name: str
    prompt: str
    assignee_type: str
    assignee_slug: str
    execution_mode: str
    project: str | None
    subscribers: tuple[str, ...]
    status: str
    triggers: tuple[DesiredAutopilotTrigger, ...]


@dataclass(frozen=True)
class DesiredState:
    workspace_id: str
    workspace_name: str
    workspace_description: str | None
    issue_prefix: str
    workspace_context: str
    skills: tuple[DesiredSkill, ...]
    agents: tuple[DesiredAgent, ...]
    squads: tuple[DesiredSquad, ...]
    autopilots: tuple[DesiredAutopilot, ...]
    autopilots_managed: bool
    quick_actions: tuple[DesiredQuickAction, ...]
    quick_actions_managed: bool
    fingerprint: str


@dataclass(frozen=True)
class RemoteSkillFile:
    id: str
    path: str
    content: str


@dataclass(frozen=True)
class RemoteSkill:
    id: str
    name: str
    description: str | None
    document: str
    files: tuple[RemoteSkillFile, ...]


@dataclass(frozen=True)
class RemoteAgent:
    id: str
    name: str
    description: str | None
    instructions: str | None
    runtime_id: str | None
    model: str | None
    max_concurrent_tasks: int | None
    skill_ids: tuple[str, ...]
    permission_summary: str
    workspace_public: bool
    archived: bool


@dataclass(frozen=True)
class RemoteSquadMember:
    agent_id: str
    role: str | None


@dataclass(frozen=True)
class RemoteSquad:
    id: str
    name: str
    description: str | None
    instructions: str | None
    leader_id: str
    members: tuple[RemoteSquadMember, ...]


@dataclass(frozen=True)
class RemoteQuickAction:
    id: str
    name: str
    description: str
    assignee_type: str
    assignee_id: str
    prompt: str
    status: str
    target_public: bool
    target_missing: bool


@dataclass(frozen=True)
class RemoteAutopilotTrigger:
    id: str
    autopilot_id: str
    kind: str
    enabled: bool
    label: str | None
    cron_expression: str | None
    timezone: str | None


@dataclass(frozen=True)
class RemoteAutopilot:
    id: str
    name: str
    prompt: str
    assignee_type: str
    assignee_id: str
    execution_mode: str
    project_id: str | None
    subscribers: tuple[str, ...]
    status: str
    can_write: bool
    triggers: tuple[RemoteAutopilotTrigger, ...]


@dataclass(frozen=True)
class ResourceBinding:
    resource_type: str
    slug: str
    remote_id: str
    last_known_name: str


@dataclass(frozen=True)
class ResourceMatch:
    resource_type: str
    slug: str
    remote_id: str
    lifecycle: str


@dataclass(frozen=True)
class RemoteState:
    workspace: tuple[tuple[str, Any], ...]
    runtimes: tuple[tuple[tuple[str, Any], ...], ...]
    active_skill_summaries: tuple[tuple[str, str], ...]
    active_agent_summaries: tuple[tuple[str, str], ...]
    archived_agent_summaries: tuple[tuple[str, str], ...]
    active_squad_summaries: tuple[tuple[str, str], ...]
    active_autopilot_summaries: tuple[tuple[str, str], ...]
    active_quick_action_summaries: tuple[tuple[str, str], ...]
    archived_quick_action_summaries: tuple[tuple[str, str], ...]
    skills: tuple[RemoteSkill, ...]
    agents: tuple[RemoteAgent, ...]
    squads: tuple[RemoteSquad, ...]
    autopilots: tuple[RemoteAutopilot, ...]
    projects: tuple[tuple[str, str], ...]
    members: tuple[tuple[str, str], ...]
    quick_actions: tuple[RemoteQuickAction, ...]
    autopilot_trigger_matches: tuple[ResourceMatch, ...]
    matches: tuple[ResourceMatch, ...]
    bindings: tuple[ResourceBinding, ...]
    fingerprint: str


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: Any
    after: Any
    sensitive: bool = False


@dataclass(frozen=True)
class FileChange:
    action: str
    path: str
    remote_id: str | None
    before: str | None
    after: str | None


@dataclass(frozen=True)
class Operation:
    phase: int
    action: str
    resource_type: str
    name: str
    remote_id: str | None
    desired_slug: str | None
    changes: tuple[FieldChange, ...]
    desired: Any | None = None
    file_changes: tuple[FileChange, ...] = ()
    dependencies: tuple[str, ...] = ()
    target_runtime_id: str | None = None
    target_project_id: str | None = None
    parent_slug: str | None = None
    subscriber_targets: tuple[tuple[str, str], ...] = ()
    member_targets: tuple[tuple[str, str | None, bool], ...] = ()
    destructive: bool = False


@dataclass(frozen=True)
class Plan:
    workspace_id: str
    local_fingerprint: str
    remote_fingerprint: str
    resource_ids: tuple[tuple[str, str, str, str], ...]
    bindings: tuple[ResourceBinding, ...]
    operations: tuple[Operation, ...]
    unmanaged_webhook_triggers: int = 0
