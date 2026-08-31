from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

from quick_action_server import QuickActionServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CLI = PROJECT_ROOT / "bin" / "multica-setup"

WORKSPACE_ID = "10000000-0000-4000-8000-000000000001"
RUNTIME_ID = "20000000-0000-4000-8000-000000000002"
DESIRED_AGENT_ID = "30000000-0000-4000-8000-000000000003"
MEMBER_AGENT_ID = "30000000-0000-4000-8000-000000000004"
STALE_AGENT_ID = "30000000-0000-4000-8000-000000000009"
DESIRED_SKILL_ID = "40000000-0000-4000-8000-000000000004"
STALE_SKILL_ID = "40000000-0000-4000-8000-000000000009"
DESIRED_SQUAD_ID = "50000000-0000-4000-8000-000000000005"
STALE_SQUAD_ID = "50000000-0000-4000-8000-000000000009"
SUPPORT_FILE_ID = "60000000-0000-4000-8000-000000000006"
AGENT_AUTOPILOT_ID = "70000000-0000-4000-8000-000000000001"
SQUAD_AUTOPILOT_ID = "70000000-0000-4000-8000-000000000002"
DESIRED_AUTOPILOT_ID = "70000000-0000-4000-8000-000000000003"
SCHEDULE_TRIGGER_ID = "71000000-0000-4000-8000-000000000001"
WEBHOOK_TRIGGER_ID = "71000000-0000-4000-8000-000000000002"
PROJECT_ID = "72000000-0000-4000-8000-000000000001"
DESIRED_QUICK_ACTION_ID = "90000000-0000-4000-8000-000000000001"
UNMANAGED_QUICK_ACTION_ID = "90000000-0000-4000-8000-000000000009"

DESIRED_SKILL_DOCUMENT = """---
name: "Desired Skill"
description: "Desired skill description"
---
Desired skill body.
"""


def workspace_member_can_invoke(agent: dict[str, object], workspace_id: str) -> bool:
    return agent.get("permission_mode") == "public_to" and any(
        target.get("target_type") == "workspace"
        and target.get("target_id") == workspace_id
        for target in agent.get("invocation_targets", [])
    )


FAKE_MULTICA = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys


STATE_PATH = Path(os.environ["FAKE_MULTICA_STATE"])


def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def parse_command(argv):
    tokens = []
    workspace_id = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--workspace-id":
            workspace_id = argv[index + 1]
            index += 2
            continue
        if token == "--output":
            index += 2
            continue
        if token == "--with-content":
            index += 1
            continue
        tokens.append(token)
        index += 1
    return tokens, workspace_id


def flag(tokens, name, default=None):
    try:
        return tokens[tokens.index(name) + 1]
    except (ValueError, IndexError):
        return default


def content_value(tokens):
    path = flag(tokens, "--content-file")
    if path is not None:
        return Path(path).read_text(encoding="utf-8")
    if "--content-stdin" in tokens:
        return sys.stdin.read()
    value = flag(tokens, "--content")
    if value is None:
        raise KeyError("content")
    return value


def public_agent(agent):
    return {
        "id": agent["id"],
        "name": agent["name"],
        "description": agent.get("description"),
        "instructions": agent.get("instructions"),
        "runtime_id": agent.get("runtime_id"),
        "model": agent.get("model"),
        "max_concurrent_tasks": agent.get("max_concurrent_tasks"),
        "skills": [{"id": value} for value in agent.get("skill_ids", [])],
        "permission_mode": agent.get("permission_mode", "private"),
        "invocation_targets": agent.get("invocation_targets", []),
    }


def active_agents(remote):
    return [
        value
        for value in remote["agents"].values()
        if not value.get("archived_at")
    ]


def resolve_agent(remote, selector):
    if selector in remote["agents"]:
        return selector
    matches = [value["id"] for value in active_agents(remote) if value["name"] == selector]
    if len(matches) != 1:
        raise KeyError(selector)
    return matches[0]


def next_id(state, kind):
    ids = state["created_ids"][kind]
    if not ids:
        raise KeyError(f"no fake id for {kind}")
    return ids.pop(0)


def maybe_fail(state, operation):
    if state.get("control", {}).get("fail_on") == operation:
        print(f"injected failure for {operation}", file=sys.stderr)
        raise SystemExit(70)


def maybe_inject_autopilot_assignment(state, operation):
    control = state.get("control", {})
    if control.get("inject_autopilot_on") != operation:
        return
    autopilot = control.pop("injected_autopilot")
    control["inject_autopilot_on"] = None
    state["remote"]["autopilots"][autopilot["id"]] = autopilot


state = load_state()
with_content = "--with-content" in sys.argv[1:]
tokens, workspace_id = parse_command(sys.argv[1:])
remote = state["remote"]

if tokens == ["workspace", "list"]:
    response = [
        {
            "id": remote["workspace"]["id"],
            "name": remote["workspace"]["name"],
            "slug": "fixture",
        }
    ]
elif tokens[:2] == ["workspace", "get"] and len(tokens) == 3:
    control = state.setdefault("control", {})
    control["workspace_get_count"] = control.get("workspace_get_count", 0) + 1
    if (
        control.get("drift_on_second_workspace_get")
        and control["workspace_get_count"] == 2
    ):
        remote["workspace"]["issue_prefix"] = "DRIFT"
    save_state(state)
    response = remote["workspace"]
elif workspace_id != remote["workspace"]["id"]:
    print("wrong workspace", file=sys.stderr)
    raise SystemExit(66)
elif tokens == ["runtime", "list"]:
    response = remote["runtimes"]
elif tokens == ["agent", "list", "--include-archived"]:
    response = [
        {
            "id": value["id"],
            "name": value["name"],
            "archived_at": value.get("archived_at"),
        }
        for value in remote["agents"].values()
    ]
elif tokens == ["agent", "list"]:
    response = [
        {
            "id": value["id"],
            "name": value["name"],
            "archived_at": None,
        }
        for value in active_agents(remote)
    ]
elif tokens[:2] == ["agent", "get"] and len(tokens) == 3:
    response = public_agent(remote["agents"][tokens[2]])
elif tokens == ["skill", "list"]:
    response = [
        {"id": value["id"], "name": value["name"]}
        for value in remote["skills"].values()
    ]
elif tokens[:2] == ["skill", "get"] and len(tokens) == 3:
    response = remote["skills"][tokens[2]]
    if not with_content:
        response = dict(response)
        response.pop("content", None)
        response["files"] = [
            {key: value for key, value in item.items() if key != "content"}
            for item in response["files"]
        ]
elif tokens == ["squad", "list"]:
    response = [
        {"id": value["id"], "name": value["name"]}
        for value in remote["squads"].values()
    ]
elif tokens[:2] == ["squad", "get"] and len(tokens) == 3:
    squad = remote["squads"][tokens[2]]
    response = {
        "id": squad["id"],
        "name": squad["name"],
        "description": squad.get("description"),
        "instructions": squad.get("instructions"),
        "leader_id": squad["leader_id"],
    }
elif tokens[:3] == ["squad", "member", "list"] and len(tokens) == 4:
    response = remote["squads"][tokens[3]]["members"]
elif tokens == ["project", "list"]:
    response = list(remote.get("projects", {}).values())
elif tokens == ["autopilot", "list"]:
    response = {
        "autopilots": list(remote["autopilots"].values()),
        "total": len(remote["autopilots"]),
    }
elif tokens[:2] == ["autopilot", "get"] and len(tokens) == 3:
    response = {
        "autopilot": remote["autopilots"][tokens[2]],
        "collaborators": [],
        "triggers": [],
    }
elif tokens[:2] == ["skill", "create"]:
    maybe_fail(state, "skill:create")
    resource_id = next_id(state, "skill")
    remote["skills"][resource_id] = {
        "id": resource_id,
        "name": flag(tokens, "--name"),
        "description": flag(tokens, "--description"),
        "content": content_value(tokens),
        "files": [],
    }
    maybe_inject_autopilot_assignment(state, "skill:create")
    save_state(state)
    response = {"id": resource_id}
elif tokens[:2] == ["skill", "update"] and len(tokens) >= 3:
    maybe_fail(state, "skill:update")
    skill = remote["skills"][tokens[2]]
    if "--name" in tokens:
        skill["name"] = flag(tokens, "--name")
    if "--description" in tokens:
        skill["description"] = flag(tokens, "--description") or None
    if (
        "--content-file" in tokens
        or "--content-stdin" in tokens
        or "--content" in tokens
    ):
        skill["content"] = content_value(tokens)
    save_state(state)
    response = skill
elif tokens[:3] == ["skill", "files", "upsert"]:
    maybe_fail(state, "skill:files:upsert")
    skill = remote["skills"][tokens[3]]
    path = flag(tokens, "--path")
    existing = next((value for value in skill["files"] if value["path"] == path), None)
    if existing is None:
        existing = {
            "id": next_id(state, "file"),
            "path": path,
            "content": "",
        }
        skill["files"].append(existing)
    existing["content"] = content_value(tokens)
    save_state(state)
    response = existing
elif tokens[:2] == ["agent", "create"]:
    maybe_fail(state, "agent:create")
    resource_id = next_id(state, "agent")
    model = flag(tokens, "--model")
    permission_mode = flag(tokens, "--permission-mode", "private")
    invocation_targets = []
    if permission_mode == "public_to" and "--public-to-workspace" in tokens:
        invocation_targets.append(
            {"target_type": "workspace", "target_id": workspace_id}
        )
    remote["agents"][resource_id] = {
        "id": resource_id,
        "name": flag(tokens, "--name"),
        "description": flag(tokens, "--description"),
        "instructions": flag(tokens, "--instructions"),
        "runtime_id": flag(tokens, "--runtime-id"),
        "model": model if model else None,
        "max_concurrent_tasks": int(flag(tokens, "--max-concurrent-tasks", "6")),
        "skill_ids": [],
        "permission_mode": permission_mode,
        "invocation_targets": invocation_targets,
        "archived_at": None,
    }
    save_state(state)
    response = {"id": resource_id}
elif tokens[:2] == ["agent", "update"] and len(tokens) >= 3:
    maybe_fail(state, "agent:update")
    agent = remote["agents"][tokens[2]]
    for option, field in (
        ("--name", "name"),
        ("--description", "description"),
        ("--instructions", "instructions"),
        ("--runtime-id", "runtime_id"),
        ("--model", "model"),
    ):
        if option in tokens:
            agent[field] = flag(tokens, option) or None
    if "--max-concurrent-tasks" in tokens:
        agent["max_concurrent_tasks"] = int(flag(tokens, "--max-concurrent-tasks"))
    save_state(state)
    response = public_agent(agent)
elif tokens[:3] == ["agent", "skills", "set"] and len(tokens) >= 4:
    maybe_fail(state, "agent:skills:set")
    raw_ids = flag(tokens, "--skill-ids", "")
    remote["agents"][tokens[3]]["skill_ids"] = sorted(
        value for value in raw_ids.split(",") if value
    )
    save_state(state)
    response = public_agent(remote["agents"][tokens[3]])
elif tokens[:2] == ["agent", "archive"] and len(tokens) == 3:
    maybe_fail(state, "agent:archive")
    remote["agents"][tokens[2]]["archived_at"] = "2026-08-30T00:00:00Z"
    save_state(state)
    response = public_agent(remote["agents"][tokens[2]])
elif tokens[:2] == ["agent", "restore"] and len(tokens) == 3:
    maybe_fail(state, "agent:restore")
    remote["agents"][tokens[2]]["archived_at"] = None
    save_state(state)
    response = public_agent(remote["agents"][tokens[2]])
elif tokens[:2] == ["squad", "create"]:
    maybe_fail(state, "squad:create")
    resource_id = next_id(state, "squad")
    leader_id = resolve_agent(remote, flag(tokens, "--leader"))
    remote["squads"][resource_id] = {
        "id": resource_id,
        "name": flag(tokens, "--name"),
        "description": flag(tokens, "--description"),
        "instructions": None,
        "leader_id": leader_id,
        "members": [
            {
                "member_id": leader_id,
                "member_type": "agent",
                "role": None,
            }
        ],
    }
    save_state(state)
    response = {"id": resource_id}
elif tokens[:2] == ["squad", "update"] and len(tokens) >= 3:
    maybe_fail(state, "squad:update")
    squad = remote["squads"][tokens[2]]
    for option, field in (
        ("--name", "name"),
        ("--description", "description"),
        ("--instructions", "instructions"),
    ):
        if option in tokens:
            squad[field] = flag(tokens, option) or None
    if "--leader" in tokens:
        leader_id = resolve_agent(remote, flag(tokens, "--leader"))
        squad["leader_id"] = leader_id
        if not any(value["member_id"] == leader_id for value in squad["members"]):
            squad["members"].append(
                {
                    "member_id": leader_id,
                    "member_type": "agent",
                    "role": "leader",
                }
            )
    save_state(state)
    response = squad
elif tokens[:3] == ["squad", "member", "add"] and len(tokens) >= 4:
    maybe_fail(state, "squad:member:add")
    squad = remote["squads"][tokens[3]]
    member_id = flag(tokens, "--member-id")
    member = next(
        (value for value in squad["members"] if value["member_id"] == member_id), None
    )
    if member is None:
        member = {
            "member_id": member_id,
            "member_type": flag(tokens, "--type", "agent"),
            "role": flag(tokens, "--role"),
        }
        squad["members"].append(member)
    save_state(state)
    response = member
elif tokens[:3] == ["squad", "member", "set-role"] and len(tokens) >= 4:
    maybe_fail(state, "squad:member:set-role")
    squad = remote["squads"][tokens[3]]
    member_id = flag(tokens, "--member-id")
    member = next(value for value in squad["members"] if value["member_id"] == member_id)
    member["role"] = flag(tokens, "--role")
    save_state(state)
    response = member
elif tokens[:3] == ["squad", "member", "remove"] and len(tokens) >= 4:
    maybe_fail(state, "squad:member:remove")
    squad = remote["squads"][tokens[3]]
    member_id = flag(tokens, "--member-id")
    if squad["leader_id"] == member_id:
        print("cannot remove squad leader", file=sys.stderr)
        raise SystemExit(71)
    squad["members"] = [
        value for value in squad["members"] if value["member_id"] != member_id
    ]
    save_state(state)
    response = {"member_id": member_id}
elif tokens[:2] == ["squad", "delete"] and len(tokens) == 3:
    maybe_fail(state, "squad:delete")
    del remote["squads"][tokens[2]]
    save_state(state)
    response = {"id": tokens[2]}
elif tokens[:2] == ["skill", "delete"] and len(tokens) >= 3:
    maybe_fail(state, "skill:delete")
    del remote["skills"][tokens[2]]
    save_state(state)
    print("deleted")
    raise SystemExit(0)
else:
    print("unsupported fake multica command", file=sys.stderr)
    raise SystemExit(64)

json.dump(response, sys.stdout, ensure_ascii=False)
"""


class ApplyBlackBoxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        temporary_root = Path(self._temporary_directory.name)
        self.repo = temporary_root / "repository"
        self.cli = self.repo / "bin" / "multica-setup"
        self.fake_bin = temporary_root / "fake-bin"
        self.state_path = temporary_root / "state.json"

        self.cli.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_CLI, self.cli)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)

        self.fake_bin.mkdir()
        fake_multica = self.fake_bin / "multica"
        fake_multica.write_text(textwrap.dedent(FAKE_MULTICA), encoding="utf-8")
        fake_multica.chmod(fake_multica.stat().st_mode | stat.S_IXUSR)
        self.quick_action_server = QuickActionServer(
            WORKSPACE_ID, target_state_path=self.state_path
        )
        self.quick_action_server.__enter__()

    def tearDown(self) -> None:
        self.quick_action_server.__exit__()
        self._temporary_directory.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def write_desired_state(self) -> None:
        src = self.repo / "src"
        workspace = src / "workspace" / WORKSPACE_ID
        self._write_json(
            workspace / "metadata.json",
            {
                "name": "Desired Workspace",
                "description": "Desired workspace description",
                "issue_prefix": "NEW",
            },
        )
        self._write_text(workspace / "instructions.md", "Desired workspace context.\n")
        self._write_json(workspace / "skill.json", ["desired-skill"])
        self._write_json(workspace / "agent.json", ["desired-agent"])
        self._write_json(workspace / "squad.json", ["desired-squad"])

        self._write_text(
            src / "skills" / "desired-skill" / "SKILL.md", DESIRED_SKILL_DOCUMENT
        )
        self._write_text(
            src / "skills" / "desired-skill" / "references" / "guide.md",
            "Desired support file.\n",
        )
        self._write_json(
            src / "agent" / "desired-agent" / "metadata.json",
            {
                "name": "Desired Agent",
                "description": "Desired agent description",
                "skills": ["desired-skill"],
                "runtime": "macmini-local",
                "provider": "codex",
                "model": None,
                "max_concurrent_tasks": 3,
            },
        )
        self._write_text(
            src / "agent" / "desired-agent" / "instructions.md",
            "Desired agent instructions.\n",
        )
        self._write_json(
            src / "squad" / "desired-squad" / "metadata.json",
            {
                "name": "Desired Squad",
                "description": "Desired squad description",
                "agents": [
                    {
                        "agent_slug": "desired-agent",
                        "role": "Squad leader",
                        "leader": True,
                    }
                ],
            },
        )
        self._write_text(
            src / "squad" / "desired-squad" / "instructions.md",
            "Desired squad instructions.\n",
        )

    def write_desired_autopilot(
        self,
        *,
        slug: str = "desired-autopilot",
        selected_slugs: list[str] | None = None,
        name: str = "Desired Autopilot",
        prompt: str = "Initial autopilot prompt.\n",
        assignee_type: str = "agent",
        assignee_slug: str = "desired-agent",
        execution_mode: str = "create_issue",
        project: str | None = "desired-project",
        subscribers: list[str] | None = None,
        status: str = "active",
        include_schedule: bool = True,
        schedule_cron: str = "30 8 * * 1-5",
    ) -> None:
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(
            workspace / "autopilot.json",
            selected_slugs if selected_slugs is not None else [slug],
        )
        triggers: list[dict[str, object]] = []
        if include_schedule:
            triggers.append(
                {
                    "key": "weekday-morning",
                    "kind": "schedule",
                    "enabled": True,
                    "label": "Weekday morning",
                    "cron_expression": schedule_cron,
                    "timezone": "Asia/Seoul",
                }
            )
        self._write_json(
            self.repo / "src" / "autopilots" / slug / "metadata.json",
            {
                "name": name,
                "assignee_type": assignee_type,
                "assignee_slug": assignee_slug,
                "execution_mode": execution_mode,
                "project": project,
                "subscribers": subscribers or [],
                "status": status,
                "triggers": triggers,
            },
        )
        self._write_text(
            self.repo / "src" / "autopilots" / slug / "prompt.md",
            prompt,
        )

    @staticmethod
    def base_state() -> dict[str, object]:
        return {
            "control": {},
            "created_ids": {
                "skill": [DESIRED_SKILL_ID],
                "agent": [DESIRED_AGENT_ID],
                "squad": [DESIRED_SQUAD_ID],
                "file": [SUPPORT_FILE_ID],
                "autopilot": [],
                "autopilot-trigger": [],
            },
            "remote": {
                "workspace": {
                    "id": WORKSPACE_ID,
                    "name": "Desired Workspace",
                    "description": "Desired workspace description",
                    "issue_prefix": "NEW",
                    "context": "Desired workspace context.\n",
                },
                "runtimes": [
                    {
                        "id": RUNTIME_ID,
                        "name": "Codex (macmini-local)",
                        "custom_name": None,
                        "provider": "codex",
                    }
                ],
                "skills": {},
                "agents": {},
                "squads": {},
                "autopilots": {},
                "autopilot_triggers": {},
                "projects": {},
                "members": [],
            },
        }

    def write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run_apply(
        self, *, auto_approve: bool, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(PROJECT_ROOT), environment.get("PYTHONPATH", ""))
        )
        environment["PATH"] = os.pathsep.join(
            (str(self.fake_bin), environment.get("PATH", ""))
        )
        environment["FAKE_MULTICA_STATE"] = str(self.state_path)
        environment["MULTICA_SERVER_URL"] = getattr(
            self, "quick_action_server_url", self.quick_action_server.url
        )
        environment["MULTICA_TOKEN"] = "test-token"
        command = [str(self.cli), "apply", "--workspace", WORKSPACE_ID]
        if auto_approve:
            command.append("--auto-approve")
        return subprocess.run(
            command,
            cwd=self.repo,
            env=environment,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_auto_approved_apply_converges_created_relationships_and_prunes_stale_state(
        self,
    ) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["skills"][STALE_SKILL_ID] = {
            "id": STALE_SKILL_ID,
            "name": "Stale Skill",
            "description": "stale",
            "content": "stale",
            "files": [],
        }
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Stale Agent",
            "description": "stale",
            "instructions": "stale",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [STALE_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["squads"][STALE_SQUAD_ID] = {
            "id": STALE_SQUAD_ID,
            "name": "Stale Squad",
            "description": "stale",
            "instructions": "stale",
            "leader_id": STALE_AGENT_ID,
            "members": [
                {
                    "member_id": STALE_AGENT_ID,
                    "member_type": "agent",
                    "role": "stale",
                }
            ],
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        skills_by_name = {value["name"]: value for value in remote["skills"].values()}
        self.assertNotIn("Stale Skill", skills_by_name, result.stdout + result.stderr)
        desired_skill = skills_by_name["Desired Skill"]
        self.assertEqual(DESIRED_SKILL_DOCUMENT, desired_skill["content"])
        self.assertEqual(
            [("references/guide.md", "Desired support file.\n")],
            sorted((value["path"], value["content"]) for value in desired_skill["files"]),
        )

        active_agents_by_name = {
            value["name"]: value
            for value in remote["agents"].values()
            if not value.get("archived_at")
        }
        self.assertNotIn("Stale Agent", active_agents_by_name)
        desired_agent = active_agents_by_name["Desired Agent"]
        self.assertTrue(workspace_member_can_invoke(desired_agent, WORKSPACE_ID))
        assigned_skill_names = {
            remote["skills"][resource_id]["name"]
            for resource_id in desired_agent["skill_ids"]
        }
        self.assertEqual({"Desired Skill"}, assigned_skill_names)
        stale_agent = next(
            value for value in remote["agents"].values() if value["name"] == "Stale Agent"
        )
        self.assertIsNotNone(stale_agent["archived_at"])

        squads_by_name = {value["name"]: value for value in remote["squads"].values()}
        self.assertNotIn("Stale Squad", squads_by_name)
        desired_squad = squads_by_name["Desired Squad"]
        agents_by_id = {value["id"]: value for value in remote["agents"].values()}
        self.assertEqual("Desired Agent", agents_by_id[desired_squad["leader_id"]]["name"])
        self.assertEqual(
            [("Desired Agent", "Squad leader")],
            sorted(
                (agents_by_id[value["member_id"]]["name"], value["role"])
                for value in desired_squad["members"]
            ),
        )

    def test_apply_renames_resources_without_replacing_their_remote_identities(
        self,
    ) -> None:
        self.write_desired_state()
        self.write_state(self.base_state())

        self.run_apply(auto_approve=True)

        renamed_skill_document = DESIRED_SKILL_DOCUMENT.replace(
            'name: "Desired Skill"', 'name: "Renamed Skill"'
        )
        self._write_text(
            self.repo / "src" / "skills" / "desired-skill" / "SKILL.md",
            renamed_skill_document,
        )
        agent_metadata_path = (
            self.repo / "src" / "agent" / "desired-agent" / "metadata.json"
        )
        agent_metadata = json.loads(agent_metadata_path.read_text(encoding="utf-8"))
        agent_metadata["name"] = "Renamed Agent"
        self._write_json(agent_metadata_path, agent_metadata)
        squad_metadata_path = (
            self.repo / "src" / "squad" / "desired-squad" / "metadata.json"
        )
        squad_metadata = json.loads(squad_metadata_path.read_text(encoding="utf-8"))
        squad_metadata["name"] = "Renamed Squad"
        self._write_json(squad_metadata_path, squad_metadata)

        state = self.read_state()
        state["created_ids"]["skill"].insert(
            0, "40000000-0000-4000-8000-000000000014"
        )
        state["created_ids"]["agent"].insert(
            0, "30000000-0000-4000-8000-000000000013"
        )
        state["created_ids"]["squad"].insert(
            0, "50000000-0000-4000-8000-000000000015"
        )
        state["created_ids"]["file"].insert(
            0, "60000000-0000-4000-8000-000000000016"
        )
        self.write_state(state)

        self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertEqual(
            "Renamed Skill",
            remote["skills"].get(DESIRED_SKILL_ID, {}).get("name"),
        )
        self.assertEqual(
            "Renamed Agent",
            remote["agents"].get(DESIRED_AGENT_ID, {}).get("name"),
        )
        self.assertIsNone(
            remote["agents"].get(DESIRED_AGENT_ID, {}).get("archived_at", "missing")
        )
        self.assertEqual(
            "Renamed Squad",
            remote["squads"].get(DESIRED_SQUAD_ID, {}).get("name"),
        )

    def test_agent_rename_preserves_bound_squad_leader_with_null_role(self) -> None:
        self.write_desired_state()
        self.write_state(self.base_state())

        self.run_apply(auto_approve=True)

        agent_metadata_path = (
            self.repo / "src" / "agent" / "desired-agent" / "metadata.json"
        )
        agent_metadata = json.loads(agent_metadata_path.read_text(encoding="utf-8"))
        agent_metadata["name"] = "Renamed Agent"
        self._write_json(agent_metadata_path, agent_metadata)
        squad_metadata_path = (
            self.repo / "src" / "squad" / "desired-squad" / "metadata.json"
        )
        squad_metadata = json.loads(squad_metadata_path.read_text(encoding="utf-8"))
        squad_metadata["agents"][0]["role"] = None
        self._write_json(squad_metadata_path, squad_metadata)
        state = self.read_state()
        bound_squad = state["remote"]["squads"][DESIRED_SQUAD_ID]
        bound_leader = next(
            member
            for member in bound_squad["members"]
            if member["member_id"] == DESIRED_AGENT_ID
        )
        bound_leader["role"] = None
        state["created_ids"]["agent"].append(
            "30000000-0000-4000-8000-000000000013"
        )
        self.write_state(state)

        self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        agent = remote["agents"].get(DESIRED_AGENT_ID, {})
        self.assertEqual(
            ("Renamed Agent", None),
            (agent.get("name"), agent.get("archived_at", "missing")),
        )
        squad = remote["squads"].get(DESIRED_SQUAD_ID, {})
        leader_member = next(
            (
                member
                for member in squad.get("members", ())
                if member.get("member_id") == DESIRED_AGENT_ID
            ),
            {},
        )
        self.assertEqual(
            ("Desired Squad", DESIRED_AGENT_ID, DESIRED_AGENT_ID, None),
            (
                squad.get("name"),
                squad.get("leader_id"),
                leader_member.get("member_id"),
                leader_member.get("role", "missing"),
            ),
        )

    def test_approval_time_fingerprint_drift_stops_before_first_remote_mutation(
        self,
    ) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["workspace"]["issue_prefix"] = "OLD"
        state["control"]["drift_on_second_workspace_get"] = True
        self.write_state(state)

        result = self.run_apply(auto_approve=False, input_text="yes\n")

        final_state = self.read_state()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("DRIFT", final_state["remote"]["workspace"]["issue_prefix"])
        self.assertNotIn(
            "Desired Skill",
            {value["name"] for value in final_state["remote"]["skills"].values()},
        )
        self.assertNotIn(
            "Desired Agent",
            {value["name"] for value in final_state["remote"]["agents"].values()},
        )
        self.assertNotIn(
            "Desired Squad",
            {value["name"] for value in final_state["remote"]["squads"].values()},
        )

    def test_forward_failure_preserves_completed_work_but_never_starts_prune(
        self,
    ) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["control"]["fail_on"] = "agent:update"
        state["remote"]["skills"][STALE_SKILL_ID] = {
            "id": STALE_SKILL_ID,
            "name": "Stale Skill",
            "description": "stale",
            "content": "stale",
            "files": [],
        }
        state["remote"]["agents"][DESIRED_AGENT_ID] = {
            "id": DESIRED_AGENT_ID,
            "name": "Desired Agent",
            "description": "Old agent description",
            "instructions": "Desired agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Stale Agent",
            "description": "stale",
            "instructions": "stale",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [STALE_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["squads"][STALE_SQUAD_ID] = {
            "id": STALE_SQUAD_ID,
            "name": "Stale Squad",
            "description": "stale",
            "instructions": "stale",
            "leader_id": STALE_AGENT_ID,
            "members": [
                {
                    "member_id": STALE_AGENT_ID,
                    "member_type": "agent",
                    "role": "stale",
                }
            ],
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertNotEqual(0, result.returncode)
        skills_by_name = {value["name"]: value for value in remote["skills"].values()}
        self.assertIn("Desired Skill", skills_by_name)
        self.assertEqual(
            "Old agent description",
            remote["agents"][DESIRED_AGENT_ID]["description"],
        )
        self.assertIn("Stale Skill", skills_by_name)
        agents_by_name = {value["name"]: value for value in remote["agents"].values()}
        self.assertIsNone(agents_by_name["Stale Agent"]["archived_at"])
        squads_by_name = {value["name"]: value for value in remote["squads"].values()}
        self.assertIn("Stale Squad", squads_by_name)
        self.assertNotIn("Desired Squad", squads_by_name)

    def test_autopilot_assignments_block_all_apply_mutations_before_prune(self) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["skills"][STALE_SKILL_ID] = {
            "id": STALE_SKILL_ID,
            "name": "Stale Skill",
            "description": "stale",
            "content": "stale",
            "files": [],
        }
        state["remote"]["agents"][DESIRED_AGENT_ID] = {
            "id": DESIRED_AGENT_ID,
            "name": "Desired Agent",
            "description": "Old agent description",
            "instructions": "Desired agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Stale Agent",
            "description": "stale",
            "instructions": "stale",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [STALE_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["squads"][STALE_SQUAD_ID] = {
            "id": STALE_SQUAD_ID,
            "name": "Stale Squad",
            "description": "stale",
            "instructions": "stale",
            "leader_id": STALE_AGENT_ID,
            "members": [
                {
                    "member_id": STALE_AGENT_ID,
                    "member_type": "agent",
                    "role": "stale",
                }
            ],
        }
        common_autopilot = {
            "workspace_id": WORKSPACE_ID,
            "description": "assignment safety fixture",
            "status": "active",
            "execution_mode": "create_issue",
            "issue_title_template": None,
            "created_by_type": "member",
            "created_by_id": "80000000-0000-4000-8000-000000000001",
            "last_run_at": None,
            "created_at": "2026-08-30T00:00:00Z",
            "updated_at": "2026-08-30T00:00:00Z",
            "subscribers": [],
        }
        state["remote"]["autopilots"][AGENT_AUTOPILOT_ID] = {
            **common_autopilot,
            "id": AGENT_AUTOPILOT_ID,
            "title": "Agent Autopilot",
            "assignee_type": "agent",
            "assignee_id": STALE_AGENT_ID,
        }
        state["remote"]["autopilots"][SQUAD_AUTOPILOT_ID] = {
            **common_autopilot,
            "id": SQUAD_AUTOPILOT_ID,
            "title": "Squad Autopilot",
            "assignee_type": "squad",
            "assignee_id": STALE_SQUAD_ID,
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        skills_by_name = {value["name"]: value for value in remote["skills"].values()}
        self.assertNotIn("Desired Skill", skills_by_name)
        self.assertIn("Stale Skill", skills_by_name)
        agents_by_name = {value["name"]: value for value in remote["agents"].values()}
        self.assertEqual(
            "Old agent description", agents_by_name["Desired Agent"]["description"]
        )
        self.assertIsNone(agents_by_name["Stale Agent"]["archived_at"])
        squads_by_name = {value["name"]: value for value in remote["squads"].values()}
        self.assertNotIn("Desired Squad", squads_by_name)
        self.assertIn("Stale Squad", squads_by_name)
        self.assertNotEqual(0, result.returncode)

    def test_unmanaged_public_quick_action_blocks_assignee_prune(self) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Stale Agent",
            "description": "Still referenced by a public quick action",
            "instructions": "stale",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [],
            "permission_mode": "public_to",
            "invocation_targets": [
                {"target_type": "workspace", "target_id": WORKSPACE_ID}
            ],
            "archived_at": None,
        }
        self.write_state(state)
        self.quick_action_server.actions[UNMANAGED_QUICK_ACTION_ID] = (
            self.quick_action_server._complete(
                {
                    "id": UNMANAGED_QUICK_ACTION_ID,
                    "name": "Keep assignee",
                    "assignee_type": "agent",
                    "assignee_id": STALE_AGENT_ID,
                    "prompt": "Keep using this agent.",
                }
            )
        )

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertNotEqual(0, result.returncode)
        self.assertIsNone(remote["agents"][STALE_AGENT_ID]["archived_at"])

    def test_apply_replaces_archived_same_name_squad_identity_and_preserves_role(
        self,
    ) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["skills"][DESIRED_SKILL_ID] = {
            "id": DESIRED_SKILL_ID,
            "name": "Desired Skill",
            "description": "Desired skill description",
            "content": DESIRED_SKILL_DOCUMENT,
            "files": [
                {
                    "id": SUPPORT_FILE_ID,
                    "path": "references/guide.md",
                    "content": "Desired support file.\n",
                }
            ],
        }
        state["remote"]["agents"][DESIRED_AGENT_ID] = {
            "id": DESIRED_AGENT_ID,
            "name": "Desired Agent",
            "description": "Desired agent description",
            "instructions": "Desired agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [DESIRED_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Desired Agent",
            "description": "archived identity",
            "instructions": "archived identity",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": "2026-08-01T00:00:00Z",
        }
        state["remote"]["squads"][DESIRED_SQUAD_ID] = {
            "id": DESIRED_SQUAD_ID,
            "name": "Desired Squad",
            "description": "Desired squad description",
            "instructions": "Desired squad instructions.\n",
            "leader_id": STALE_AGENT_ID,
            "members": [
                {
                    "member_id": STALE_AGENT_ID,
                    "member_type": "agent",
                    "role": "Squad leader",
                }
            ],
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        squad = next(
            value
            for value in self.read_state()["remote"]["squads"].values()
            if value["name"] == "Desired Squad"
        )
        self.assertEqual(DESIRED_AGENT_ID, squad["leader_id"])
        self.assertEqual(
            [(DESIRED_AGENT_ID, "Squad leader")],
            sorted(
                (value["member_id"], value["role"])
                for value in squad["members"]
            ),
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_apply_reconciles_autopilot_configuration_without_replacing_bound_identities(
        self,
    ) -> None:
        self.write_desired_state()
        self.write_desired_autopilot(subscribers=["owner@example.com"])
        state = self.base_state()
        state["created_ids"]["autopilot"] = [DESIRED_AUTOPILOT_ID]
        state["created_ids"]["autopilot-trigger"] = [
            SCHEDULE_TRIGGER_ID,
        ]
        state["remote"]["projects"] = {
            PROJECT_ID: {"id": PROJECT_ID, "title": "Desired Project"}
        }
        state["remote"]["members"] = [
            {
                "workspace_id": WORKSPACE_ID,
                "user_id": "80000000-0000-4000-8000-000000000001",
                "name": "Owner",
                "email": "owner@example.com",
                "role": "owner",
            }
        ]
        self.write_state(state)
        self._write_json(
            self.repo
            / ".cache"
            / "workspaces"
            / WORKSPACE_ID
            / "bindings.json",
            {
                "version": 3,
                "workspace_id": WORKSPACE_ID,
                "resources": {
                    "agent": {},
                    "skill": {},
                    "squad": {},
                    "quick-action": {},
                    "autopilot": {},
                    "autopilot-trigger": {},
                    "autopilot-project": {
                        "desired-project": {
                            "remote_id": PROJECT_ID,
                            "last_known_name": "Desired Project",
                        }
                    },
                },
            },
        )

        first = self.run_apply(auto_approve=True)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        initial_triggers = self.read_state()["remote"]["autopilot_triggers"]
        schedule_id = next(
            value["id"] for value in initial_triggers.values() if value["kind"] == "schedule"
        )
        server_only_issue_title_template = "Server-owned {{date}}"
        state = self.read_state()
        state["remote"]["autopilots"][DESIRED_AUTOPILOT_ID][
            "issue_title_template"
        ] = server_only_issue_title_template
        self.write_state(state)

        self.write_desired_autopilot(
            name="Renamed Autopilot",
            prompt="",
            assignee_type="squad",
            assignee_slug="desired-squad",
            execution_mode="run_only",
            status="paused",
            include_schedule=False,
        )
        second = self.run_apply(auto_approve=True)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)

        remote = self.read_state()["remote"]
        autopilot = remote["autopilots"][DESIRED_AUTOPILOT_ID]
        self.assertEqual(
            (
                "Renamed Autopilot",
                "",
                "squad",
                DESIRED_SQUAD_ID,
                "run_only",
                PROJECT_ID,
                "paused",
                (),
            ),
            (
                autopilot["title"],
                autopilot["description"],
                autopilot["assignee_type"],
                autopilot["assignee_id"],
                autopilot["execution_mode"],
                autopilot["project_id"],
                autopilot["status"],
                tuple(autopilot["subscribers"]),
            ),
        )
        self.assertEqual(
            server_only_issue_title_template,
            autopilot["issue_title_template"],
        )
        self.assertNotIn(schedule_id, remote["autopilot_triggers"])

    def test_apply_preserves_existing_webhook_triggers_as_unmanaged(self) -> None:
        self.write_desired_state()
        self.write_desired_autopilot(
            execution_mode="run_only",
            project=None,
            include_schedule=False,
        )
        state = self.base_state()
        state["created_ids"]["autopilot"] = [DESIRED_AUTOPILOT_ID]
        self.write_state(state)
        initial = self.run_apply(auto_approve=True)
        self.assertEqual(0, initial.returncode, initial.stdout + initial.stderr)

        state = self.read_state()
        state["remote"]["autopilot_triggers"][WEBHOOK_TRIGGER_ID] = {
            "id": WEBHOOK_TRIGGER_ID,
            "autopilot_id": DESIRED_AUTOPILOT_ID,
            "kind": "webhook",
            "enabled": False,
            "label": "Manually managed webhook",
            "cron_expression": None,
            "timezone": None,
            "provider": "github",
            "event_filters": [
                {
                    "event": "pull_request",
                    "actions": ["opened", "synchronize"],
                }
            ],
            "signing_secret": "must-remain-untouched",
        }
        self.write_state(state)
        webhook_before = dict(
            state["remote"]["autopilot_triggers"][WEBHOOK_TRIGGER_ID]
        )

        result = self.run_apply(auto_approve=True)

        webhook_after = self.read_state()["remote"]["autopilot_triggers"].get(
            WEBHOOK_TRIGGER_ID
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(webhook_before, webhook_after)

        self._write_json(
            self.repo / "src" / "workspace" / WORKSPACE_ID / "autopilot.json",
            [],
        )
        archived = self.run_apply(auto_approve=True)
        archived_remote = self.read_state()["remote"]
        self.assertEqual(0, archived.returncode, archived.stdout + archived.stderr)
        self.assertEqual(
            webhook_before,
            archived_remote["autopilot_triggers"].get(WEBHOOK_TRIGGER_ID),
        )
        self.assertEqual(
            "archived",
            archived_remote["autopilots"][DESIRED_AUTOPILOT_ID]["status"],
        )

    def test_project_key_without_binding_never_mutates_remote(self) -> None:
        self.write_desired_state()
        self.write_desired_autopilot(project="foo")
        state = self.base_state()
        state["remote"]["projects"] = {
            PROJECT_ID: {"id": PROJECT_ID, "title": "Foo"},
        }
        remote_before = state["remote"]
        self.write_state(state)

        self.run_apply(auto_approve=True)

        self.assertEqual(remote_before, self.read_state()["remote"])

    def test_duplicate_autopilot_titles_keep_trigger_updates_on_their_bound_parents(
        self,
    ) -> None:
        self.write_desired_state()
        self.write_state(self.base_state())
        initial = self.run_apply(auto_approve=True)
        self.assertEqual(0, initial.returncode, initial.stdout + initial.stderr)

        selected_slugs = ["first-autopilot", "second-autopilot"]
        self.write_desired_autopilot(
            slug="first-autopilot",
            selected_slugs=selected_slugs,
            name="Shared Title",
            execution_mode="run_only",
            project="first-project",
            schedule_cron="10 8 * * *",
        )
        self.write_desired_autopilot(
            slug="second-autopilot",
            selected_slugs=selected_slugs,
            name="Shared Title",
            execution_mode="run_only",
            project="second-project",
            schedule_cron="20 8 * * *",
        )
        state = self.read_state()
        common = {
            "workspace_id": WORKSPACE_ID,
            "title": "Shared Title",
            "description": "Initial autopilot prompt.\n",
            "project_id": None,
            "assignee_type": "agent",
            "assignee_id": DESIRED_AGENT_ID,
            "execution_mode": "run_only",
            "issue_title_template": None,
            "subscribers": [],
            "status": "active",
            "can_write": True,
        }
        second_project_id = "72000000-0000-4000-8000-000000000002"
        state["remote"]["projects"] = {
            PROJECT_ID: {"id": PROJECT_ID, "title": "Shared Project"},
            second_project_id: {
                "id": second_project_id,
                "title": "Shared Project",
            },
        }
        state["remote"]["autopilots"] = {
            AGENT_AUTOPILOT_ID: {
                "id": AGENT_AUTOPILOT_ID,
                **common,
                "project_id": PROJECT_ID,
            },
            SQUAD_AUTOPILOT_ID: {
                "id": SQUAD_AUTOPILOT_ID,
                **common,
                "project_id": second_project_id,
            },
        }
        state["remote"]["autopilot_triggers"] = {
            SCHEDULE_TRIGGER_ID: {
                "id": SCHEDULE_TRIGGER_ID,
                "autopilot_id": AGENT_AUTOPILOT_ID,
                "kind": "schedule",
                "enabled": True,
                "label": "Weekday morning",
                "cron_expression": "0 8 * * *",
                "timezone": "Asia/Seoul",
                "provider": None,
                "event_filters": [],
            },
            WEBHOOK_TRIGGER_ID: {
                "id": WEBHOOK_TRIGGER_ID,
                "autopilot_id": SQUAD_AUTOPILOT_ID,
                "kind": "schedule",
                "enabled": True,
                "label": "Weekday morning",
                "cron_expression": "5 8 * * *",
                "timezone": "Asia/Seoul",
                "provider": None,
                "event_filters": [],
            },
        }
        binding_path = (
            self.repo
            / ".cache"
            / "workspaces"
            / WORKSPACE_ID
            / "bindings.json"
        )
        bindings = json.loads(binding_path.read_text(encoding="utf-8"))
        bindings["resources"]["autopilot"] = {
            "first-autopilot": {
                "remote_id": AGENT_AUTOPILOT_ID,
                "last_known_name": "Shared Title",
            },
            "second-autopilot": {
                "remote_id": SQUAD_AUTOPILOT_ID,
                "last_known_name": "Shared Title",
            },
        }
        bindings["resources"]["autopilot-trigger"] = {
            "first-autopilot::weekday-morning": {
                "remote_id": SCHEDULE_TRIGGER_ID,
                "last_known_name": "Weekday morning",
            },
            "second-autopilot::weekday-morning": {
                "remote_id": WEBHOOK_TRIGGER_ID,
                "last_known_name": "Weekday morning",
            },
        }
        bindings["resources"]["autopilot-project"] = {
            "first-project": {
                "remote_id": PROJECT_ID,
                "last_known_name": "Shared Project",
            },
            "second-project": {
                "remote_id": second_project_id,
                "last_known_name": "Shared Project",
            },
        }
        self._write_json(binding_path, bindings)
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        final_triggers = self.read_state()["remote"]["autopilot_triggers"]
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("10 8 * * *", final_triggers[SCHEDULE_TRIGGER_ID]["cron_expression"])
        self.assertEqual("20 8 * * *", final_triggers[WEBHOOK_TRIGGER_ID]["cron_expression"])
        final_autopilots = self.read_state()["remote"]["autopilots"]
        self.assertEqual(PROJECT_ID, final_autopilots[AGENT_AUTOPILOT_ID]["project_id"])
        self.assertEqual(
            second_project_id,
            final_autopilots[SQUAD_AUTOPILOT_ID]["project_id"],
        )

    def test_autopilot_trigger_create_requires_write_permission_before_mutation(
        self,
    ) -> None:
        self.write_desired_state()
        self.write_state(self.base_state())
        initial = self.run_apply(auto_approve=True)
        self.assertEqual(0, initial.returncode, initial.stdout + initial.stderr)

        self.write_desired_autopilot(
            execution_mode="run_only",
            project=None,
        )
        state = self.read_state()
        state["remote"]["autopilots"][DESIRED_AUTOPILOT_ID] = {
            "id": DESIRED_AUTOPILOT_ID,
            "workspace_id": WORKSPACE_ID,
            "title": "Desired Autopilot",
            "description": "Initial autopilot prompt.\n",
            "project_id": None,
            "assignee_type": "agent",
            "assignee_id": DESIRED_AGENT_ID,
            "execution_mode": "run_only",
            "issue_title_template": None,
            "subscribers": [],
            "status": "active",
            "can_write": False,
        }
        binding_path = (
            self.repo
            / ".cache"
            / "workspaces"
            / WORKSPACE_ID
            / "bindings.json"
        )
        bindings = json.loads(binding_path.read_text(encoding="utf-8"))
        bindings["resources"]["autopilot"] = {
            "desired-autopilot": {
                "remote_id": DESIRED_AUTOPILOT_ID,
                "last_known_name": "Desired Autopilot",
            }
        }
        self._write_json(binding_path, bindings)
        self.write_state(state)
        before = self.read_state()["remote"]

        result = self.run_apply(auto_approve=True)

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.read_state()["remote"])

    def test_apply_reconciles_public_quick_actions_without_replacing_bound_identity(
        self,
    ) -> None:
        self.write_desired_state()
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(workspace / "quick-action.json", ["review-issue"])
        quick_action = self.repo / "src" / "quick-actions" / "review-issue"
        self._write_json(
            quick_action / "metadata.json",
            {
                "name": "Review issue",
                "description": "Review the current issue",
                "assignee_type": "agent",
                "assignee_slug": "desired-agent",
            },
        )
        self._write_text(quick_action / "prompt.md", "Review this issue carefully.\n")
        self.write_state(self.base_state())
        with QuickActionServer(
            WORKSPACE_ID,
            created_ids=[DESIRED_QUICK_ACTION_ID],
            target_state_path=self.state_path,
        ) as server:
            self.quick_action_server_url = server.url
            try:
                created = self.run_apply(auto_approve=True)
                self.assertEqual(0, created.returncode, created.stdout + created.stderr)

                metadata_path = quick_action / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["name"] = "Review issue thoroughly"
                self._write_json(metadata_path, metadata)
                self._write_text(
                    quick_action / "prompt.md", "Perform a deeper review.\n"
                )
                updated = self.run_apply(auto_approve=True)

                desired = server.actions[DESIRED_QUICK_ACTION_ID]
            finally:
                del self.quick_action_server_url

        self.assertEqual(0, updated.returncode, updated.stdout + updated.stderr)
        self.assertEqual(
            (
                "Review issue thoroughly",
                "Desired Agent",
                DESIRED_AGENT_ID,
                "Perform a deeper review.",
            ),
            (
                desired["name"],
                self.read_state()["remote"]["agents"][desired["assignee_id"]]["name"],
                desired["assignee_id"],
                desired["prompt"],
            ),
        )

    def test_public_quick_action_with_private_assignee_stops_before_mutation(
        self,
    ) -> None:
        self.write_desired_state()
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(workspace / "quick-action.json", ["review-issue"])
        quick_action = self.repo / "src" / "quick-actions" / "review-issue"
        self._write_json(
            quick_action / "metadata.json",
            {
                "name": "Review issue",
                "description": "Review the current issue",
                "assignee_type": "agent",
                "assignee_slug": "desired-agent",
            },
        )
        self._write_text(quick_action / "prompt.md", "Review this issue carefully.\n")
        state = self.base_state()
        state["remote"]["agents"][DESIRED_AGENT_ID] = {
            "id": DESIRED_AGENT_ID,
            "name": "Desired Agent",
            "description": "Desired agent description",
            "instructions": "Desired agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(remote["skills"])
        self.assertFalse(remote["squads"])
        self.assertFalse(self.quick_action_server.actions)

    def test_public_quick_action_requires_admin_before_mutation(self) -> None:
        self.write_desired_state()
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(workspace / "quick-action.json", ["review-issue"])
        quick_action = self.repo / "src" / "quick-actions" / "review-issue"
        self._write_json(
            quick_action / "metadata.json",
            {
                "name": "Review issue",
                "description": "Review the current issue",
                "assignee_type": "agent",
                "assignee_slug": "desired-agent",
            },
        )
        self._write_text(quick_action / "prompt.md", "Review this issue carefully.\n")
        self.write_state(self.base_state())

        with QuickActionServer(
            WORKSPACE_ID,
            created_ids=[DESIRED_QUICK_ACTION_ID],
            target_state_path=self.state_path,
            role="member",
        ) as server:
            self.quick_action_server_url = server.url
            try:
                result = self.run_apply(auto_approve=True)
            finally:
                del self.quick_action_server_url

        remote = self.read_state()["remote"]
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(remote["skills"])
        self.assertFalse(remote["agents"])
        self.assertFalse(remote["squads"])
        self.assertFalse(server.actions)

    def test_autopilot_assignment_created_during_upserts_blocks_prune(self) -> None:
        self.write_desired_state()
        state = self.base_state()
        state["remote"]["agents"][STALE_AGENT_ID] = {
            "id": STALE_AGENT_ID,
            "name": "Stale Agent",
            "description": "stale",
            "instructions": "stale",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 1,
            "skill_ids": [STALE_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["skills"][STALE_SKILL_ID] = {
            "id": STALE_SKILL_ID,
            "name": "Stale Skill",
            "description": "stale",
            "content": "stale",
            "files": [],
        }
        state["remote"]["squads"][STALE_SQUAD_ID] = {
            "id": STALE_SQUAD_ID,
            "name": "Stale Squad",
            "description": "stale",
            "instructions": "stale",
            "leader_id": STALE_AGENT_ID,
            "members": [
                {
                    "member_id": STALE_AGENT_ID,
                    "member_type": "agent",
                    "role": "stale",
                }
            ],
        }
        state["control"]["inject_autopilot_on"] = "skill:create"
        state["control"]["injected_autopilot"] = {
            "id": SQUAD_AUTOPILOT_ID,
            "workspace_id": WORKSPACE_ID,
            "title": "Concurrent Squad Autopilot",
            "description": "created while apply is running",
            "assignee_type": "squad",
            "assignee_id": STALE_SQUAD_ID,
            "status": "active",
            "execution_mode": "create_issue",
            "issue_title_template": None,
            "created_by_type": "member",
            "created_by_id": "80000000-0000-4000-8000-000000000001",
            "last_run_at": None,
            "created_at": "2026-08-30T00:00:00Z",
            "updated_at": "2026-08-30T00:00:00Z",
            "subscribers": [],
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        skills_by_name = {value["name"]: value for value in remote["skills"].values()}
        self.assertIn("Desired Skill", skills_by_name)
        self.assertIn("Stale Skill", skills_by_name)
        agents_by_name = {value["name"]: value for value in remote["agents"].values()}
        self.assertIsNone(agents_by_name["Stale Agent"]["archived_at"])
        squads_by_name = {value["name"]: value for value in remote["squads"].values()}
        self.assertIn("Stale Squad", squads_by_name)
        concurrent_autopilot = next(
            value
            for value in remote["autopilots"].values()
            if value["title"] == "Concurrent Squad Autopilot"
        )
        self.assertEqual(
            "Stale Squad",
            remote["squads"][concurrent_autopilot["assignee_id"]]["name"],
        )
        self.assertNotEqual(0, result.returncode)

    def test_blank_agent_name_aborts_before_any_remote_mutation(self) -> None:
        self.write_desired_state()
        agent_metadata = self.repo / "src" / "agent" / "desired-agent" / "metadata.json"
        record = json.loads(agent_metadata.read_text(encoding="utf-8"))
        record["name"] = ""
        self._write_json(agent_metadata, record)
        self.write_state(self.base_state())

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertFalse(remote["skills"])
        self.assertFalse(remote["agents"])
        self.assertFalse(remote["squads"])
        self.assertNotEqual(0, result.returncode)

    def test_nul_in_agent_instructions_aborts_before_any_remote_mutation(self) -> None:
        self.write_desired_state()
        self._write_text(
            self.repo / "src" / "agent" / "desired-agent" / "instructions.md",
            "Unsafe\x00agent instructions.\n",
        )
        self.write_state(self.base_state())

        result = self.run_apply(auto_approve=True)

        remote = self.read_state()["remote"]
        self.assertFalse(remote["skills"])
        self.assertFalse(remote["agents"])
        self.assertFalse(remote["squads"])
        self.assertNotEqual(0, result.returncode)

    def test_apply_keeps_non_leader_member_while_clearing_its_role(self) -> None:
        self.write_desired_state()
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(workspace / "agent.json", ["desired-agent", "member-agent"])
        self._write_json(
            self.repo / "src" / "agent" / "member-agent" / "metadata.json",
            {
                "name": "Member Agent",
                "description": "Member agent description",
                "skills": ["desired-skill"],
                "runtime": "macmini-local",
                "provider": "codex",
                "model": None,
                "max_concurrent_tasks": 3,
            },
        )
        self._write_text(
            self.repo / "src" / "agent" / "member-agent" / "instructions.md",
            "Member agent instructions.\n",
        )
        self._write_json(
            self.repo / "src" / "squad" / "desired-squad" / "metadata.json",
            {
                "name": "Desired Squad",
                "description": "Desired squad description",
                "agents": [
                    {
                        "agent_slug": "desired-agent",
                        "role": "Squad leader",
                        "leader": True,
                    },
                    {
                        "agent_slug": "member-agent",
                        "role": None,
                        "leader": False,
                    },
                ],
            },
        )

        state = self.base_state()
        state["remote"]["skills"][DESIRED_SKILL_ID] = {
            "id": DESIRED_SKILL_ID,
            "name": "Desired Skill",
            "description": "Desired skill description",
            "content": DESIRED_SKILL_DOCUMENT,
            "files": [
                {
                    "id": SUPPORT_FILE_ID,
                    "path": "references/guide.md",
                    "content": "Desired support file.\n",
                }
            ],
        }
        state["remote"]["agents"][DESIRED_AGENT_ID] = {
            "id": DESIRED_AGENT_ID,
            "name": "Desired Agent",
            "description": "Desired agent description",
            "instructions": "Desired agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [DESIRED_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["agents"][MEMBER_AGENT_ID] = {
            "id": MEMBER_AGENT_ID,
            "name": "Member Agent",
            "description": "Member agent description",
            "instructions": "Member agent instructions.\n",
            "runtime_id": RUNTIME_ID,
            "model": None,
            "max_concurrent_tasks": 3,
            "skill_ids": [DESIRED_SKILL_ID],
            "permission_mode": "private",
            "invocation_targets": [],
            "archived_at": None,
        }
        state["remote"]["squads"][DESIRED_SQUAD_ID] = {
            "id": DESIRED_SQUAD_ID,
            "name": "Desired Squad",
            "description": "Desired squad description",
            "instructions": "Desired squad instructions.\n",
            "leader_id": DESIRED_AGENT_ID,
            "members": [
                {
                    "member_id": DESIRED_AGENT_ID,
                    "member_type": "agent",
                    "role": "Squad leader",
                },
                {
                    "member_id": MEMBER_AGENT_ID,
                    "member_type": "agent",
                    "role": "Old member role",
                },
            ],
        }
        self.write_state(state)

        result = self.run_apply(auto_approve=True)

        squad = self.read_state()["remote"]["squads"][DESIRED_SQUAD_ID]
        member = next(
            value for value in squad["members"] if value["member_id"] == MEMBER_AGENT_ID
        )
        self.assertIsNone(member["role"] or None)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
