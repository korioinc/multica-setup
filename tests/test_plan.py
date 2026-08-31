from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CLI = PROJECT_ROOT / "bin" / "multica-setup"

WORKSPACE_ID = "10000000-0000-4000-8000-000000000001"
SIBLING_WORKSPACE_ID = "10000000-0000-4000-8000-000000000099"
RUNTIME_ID = "20000000-0000-4000-8000-000000000002"
AGENT_ID = "30000000-0000-4000-8000-000000000003"
STALE_AGENT_ID = "30000000-0000-4000-8000-000000000009"
SKILL_ID = "40000000-0000-4000-8000-000000000004"
STALE_SKILL_ID = "40000000-0000-4000-8000-000000000009"
SQUAD_ID = "50000000-0000-4000-8000-000000000005"
STALE_SQUAD_ID = "50000000-0000-4000-8000-000000000009"
SUPPORT_FILE_ID = "60000000-0000-4000-8000-000000000006"

SECRET_MARKERS = (
    "local-workspace-secret",
    "remote-workspace-secret",
    "local-skill-secret",
    "remote-skill-secret",
    "local-agent-secret",
    "remote-agent-secret",
    "local-squad-secret",
    "remote-squad-secret",
    "unmodeled-remote-secret",
    "new-skill-create-secret",
)


FAKE_MULTICA = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys


with Path(os.environ["FAKE_MULTICA_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")


def command_tokens(argv):
    tokens = []
    index = 0
    while index < len(argv):
        if argv[index] in {"--output", "--workspace-id"}:
            index += 2
            continue
        tokens.append(argv[index])
        index += 1
    return tokens


tokens = command_tokens(sys.argv[1:])
resource_id = None
if tokens == ["workspace", "list"]:
    endpoint = "workspace_list"
elif tokens[:2] == ["workspace", "get"] and len(tokens) == 3:
    endpoint = "workspace_get"
    resource_id = tokens[2]
elif tokens == ["runtime", "list"]:
    endpoint = "runtime_list"
elif tokens == ["agent", "list"]:
    endpoint = "agent_list"
elif tokens == ["agent", "list", "--include-archived"]:
    endpoint = "agent_list_with_archived"
elif tokens[:2] == ["agent", "get"] and len(tokens) == 3:
    endpoint = "agent_get"
    resource_id = tokens[2]
elif tokens == ["skill", "list"]:
    endpoint = "skill_list"
elif tokens[:2] == ["skill", "get"] and len(tokens) == 3:
    endpoint = "skill_get"
    resource_id = tokens[2]
elif tokens == ["squad", "list"]:
    endpoint = "squad_list"
elif tokens[:2] == ["squad", "get"] and len(tokens) == 3:
    endpoint = "squad_get"
    resource_id = tokens[2]
elif tokens[:3] == ["squad", "member", "list"] and len(tokens) == 4:
    endpoint = "squad_member_list"
    resource_id = tokens[3]
else:
    mutation_verbs = {"create", "update", "delete", "archive", "restore", "set-role", "add", "remove", "set"}
    if any(token in mutation_verbs for token in tokens[1:]):
        Path(os.environ["FAKE_MUTATION_SENTINEL"]).write_text(
            json.dumps(tokens), encoding="utf-8"
        )
    print("fake multica rejected non-read or unsupported command", file=sys.stderr)
    raise SystemExit(64)

state = json.loads(Path(os.environ["FAKE_MULTICA_STATE"]).read_text(encoding="utf-8"))
try:
    response = state["responses"][endpoint]
    if isinstance(response, dict) and "by_selector" in response:
        response = response["by_selector"][resource_id]
    elif isinstance(response, dict) and "by_id" in response:
        response = response["by_id"][resource_id]
except KeyError:
    print(f"fake multica has no response for {endpoint}", file=sys.stderr)
    raise SystemExit(65)
json.dump(response, sys.stdout, ensure_ascii=False)
"""


def _skill_document(name: str, description: str, body: str) -> str:
    return (
        "---\n"
        f"name: {json.dumps(name)}\n"
        f"description: {json.dumps(description)}\n"
        "---\n"
        f"{body}"
    )


class PlanBlackBoxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        temporary_root = Path(self._temporary_directory.name)
        self.repo = temporary_root / "repository"
        self.cli = self.repo / "bin" / "multica-setup"
        self.fake_bin = temporary_root / "fake-bin"
        self.state_path = temporary_root / "state.json"
        self.command_log = temporary_root / "commands.jsonl"
        self.mutation_sentinel = temporary_root / "mutation-attempted.json"

        self.cli.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_CLI, self.cli)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)

        self.fake_bin.mkdir()
        fake_multica = self.fake_bin / "multica"
        fake_multica.write_text(textwrap.dedent(FAKE_MULTICA), encoding="utf-8")
        fake_multica.chmod(fake_multica.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
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

    def write_desired_state(self, *, include_changes: bool) -> None:
        src = self.repo / "src"
        (src / "autopilots").mkdir(parents=True)
        workspace = src / "workspace" / WORKSPACE_ID
        self._write_json(
            workspace / "metadata.json",
            {
                "name": "Local Workspace",
                "description": (
                    "local-workspace-secret"
                    if include_changes
                    else "same workspace description"
                ),
                "issue_prefix": "LOC",
            },
        )
        self._write_text(
            workspace / "instructions.md",
            "local-workspace-secret\n"
            if include_changes
            else "same workspace instructions\n",
        )
        selected_skills = ["desired-skill"]
        if include_changes:
            selected_skills.append("new-skill")
        self._write_json(workspace / "skill.json", selected_skills)
        self._write_json(workspace / "agent.json", ["desired-agent"])
        self._write_json(workspace / "squad.json", ["desired-squad"])

        desired_skill = _skill_document(
            "Desired Skill",
            "local-skill-secret" if include_changes else "same skill description",
            "local-skill-secret\n" if include_changes else "same skill body\n",
        )
        self._write_text(src / "skills" / "desired-skill" / "SKILL.md", desired_skill)
        self._write_text(
            src / "skills" / "desired-skill" / "references" / "guide.md",
            "local-skill-secret\n" if include_changes else "same support body\n",
        )
        if include_changes:
            self._write_text(
                src / "skills" / "new-skill" / "SKILL.md",
                _skill_document(
                    "New Skill",
                    "new-skill-create-secret",
                    "new-skill-create-secret\n",
                ),
            )
            self._write_text(
                src / "skills" / "ignored-local-skill" / "SKILL.md",
                _skill_document(
                    "Ignored Local Skill", "not selected", "must remain unmanaged\n"
                ),
            )

        self._write_json(
            src / "agent" / "desired-agent" / "metadata.json",
            {
                "name": "Desired Agent",
                "description": (
                    "local-agent-secret" if include_changes else "same agent description"
                ),
                "skills": selected_skills,
                "runtime": "macmini-local",
                "provider": "codex",
                "model": None,
                "max_concurrent_tasks": 2,
            },
        )
        self._write_text(
            src / "agent" / "desired-agent" / "instructions.md",
            "local-agent-secret\n" if include_changes else "same agent instructions\n",
        )

        self._write_json(
            src / "squad" / "desired-squad" / "metadata.json",
            {
                "name": "Desired Squad",
                "description": (
                    "local-squad-secret" if include_changes else "same squad description"
                ),
                "agents": [
                    {
                        "agent_slug": "desired-agent",
                        "role": "new-role" if include_changes else "same-role",
                        "leader": True,
                    }
                ],
            },
        )
        self._write_text(
            src / "squad" / "desired-squad" / "instructions.md",
            "local-squad-secret\n" if include_changes else "same squad instructions\n",
        )

    @staticmethod
    def remote_state(*, include_changes: bool) -> dict[str, object]:
        skill_document = _skill_document(
            "Desired Skill",
            "remote-skill-secret" if include_changes else "same skill description",
            "remote-skill-secret\n" if include_changes else "same skill body\n",
        )
        active_agents = [
            {"id": AGENT_ID, "name": "Desired Agent", "archived_at": None}
        ]
        active_skills = [{"id": SKILL_ID, "name": "Desired Skill"}]
        active_squads = [{"id": SQUAD_ID, "name": "Desired Squad"}]
        if include_changes:
            active_agents.append(
                {"id": STALE_AGENT_ID, "name": "Stale Agent", "archived_at": None}
            )
            active_skills.append({"id": STALE_SKILL_ID, "name": "Stale Skill"})
            active_squads.append({"id": STALE_SQUAD_ID, "name": "Stale Squad"})

        return {
            "responses": {
                "workspace_get": {
                    "id": WORKSPACE_ID,
                    "name": "Local Workspace",
                    "description": (
                        "remote-workspace-secret"
                        if include_changes
                        else "same workspace description"
                    ),
                    "issue_prefix": "REM" if include_changes else "LOC",
                    "context": (
                        "remote-workspace-secret\n"
                        if include_changes
                        else "same workspace instructions\n"
                    ),
                    "unmodeled_secret": "unmodeled-remote-secret",
                },
                "runtime_list": [
                    {
                        "id": RUNTIME_ID,
                        "name": "Codex (macmini-local)",
                        "custom_name": None,
                        "provider": "codex",
                    }
                ],
                "agent_list": active_agents,
                "agent_list_with_archived": active_agents,
                "agent_get": {
                    "by_id": {
                        AGENT_ID: {
                            "id": AGENT_ID,
                            "name": "Desired Agent",
                            "description": (
                                "remote-agent-secret"
                                if include_changes
                                else "same agent description"
                            ),
                            "instructions": (
                                "remote-agent-secret\n"
                                if include_changes
                                else "same agent instructions\n"
                            ),
                            "runtime_id": RUNTIME_ID,
                            "model": None,
                            "max_concurrent_tasks": 2,
                            "skills": [{"id": SKILL_ID}],
                            "permission_mode": "private",
                            "unmodeled_secret": "unmodeled-remote-secret",
                        }
                    }
                },
                "skill_list": active_skills,
                "skill_get": {
                    "by_id": {
                        SKILL_ID: {
                            "id": SKILL_ID,
                            "name": "Desired Skill",
                            "description": (
                                "remote-skill-secret"
                                if include_changes
                                else "same skill description"
                            ),
                            "content": skill_document,
                            "files": [
                                {
                                    "id": SUPPORT_FILE_ID,
                                    "path": "references/guide.md",
                                    "content": (
                                        "remote-skill-secret\n"
                                        if include_changes
                                        else "same support body\n"
                                    ),
                                }
                            ],
                            "unmodeled_secret": "unmodeled-remote-secret",
                        }
                    }
                },
                "squad_list": active_squads,
                "squad_get": {
                    "by_id": {
                        SQUAD_ID: {
                            "id": SQUAD_ID,
                            "name": "Desired Squad",
                            "description": (
                                "remote-squad-secret"
                                if include_changes
                                else "same squad description"
                            ),
                            "instructions": (
                                "remote-squad-secret\n"
                                if include_changes
                                else "same squad instructions\n"
                            ),
                            "leader_id": AGENT_ID,
                            "unmodeled_secret": "unmodeled-remote-secret",
                        }
                    }
                },
                "squad_member_list": {
                    "by_id": {
                        SQUAD_ID: [
                            {
                                "member_id": AGENT_ID,
                                "member_type": "agent",
                                "role": "old-role" if include_changes else "same-role",
                            }
                        ]
                    }
                },
            }
        }

    def write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def run_plan(self) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(PROJECT_ROOT), environment.get("PYTHONPATH", ""))
        )
        environment["PATH"] = os.pathsep.join(
            (str(self.fake_bin), environment.get("PATH", ""))
        )
        environment["FAKE_MULTICA_STATE"] = str(self.state_path)
        environment["FAKE_MULTICA_LOG"] = str(self.command_log)
        environment["FAKE_MUTATION_SENTINEL"] = str(self.mutation_sentinel)
        return subprocess.run(
            [str(self.cli), "plan", "--workspace", WORKSPACE_ID],
            cwd=self.repo,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def tree_manifest(root: Path) -> dict[str, tuple[str, bytes | None]]:
        result: dict[str, tuple[str, bytes | None]] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                result[relative] = ("directory", None)
            elif path.is_file():
                result[relative] = ("file", path.read_bytes())
            else:
                result[relative] = ("other", None)
        return result

    @staticmethod
    def operation_records(output: str) -> list[tuple[str, str, str]]:
        pattern = re.compile(
            r'^\s*(?:[+~-]\s+)?(workspace|skill|agent|squad)\s+"([^"]+)"\s+\(([^)]+)\)',
            re.MULTILINE | re.IGNORECASE,
        )
        return [
            (kind.casefold(), name, action.casefold())
            for kind, name, action in pattern.findall(output)
        ]

    def test_selector_managed_resources_reconcile_in_dependency_safe_order(self) -> None:
        self.write_desired_state(include_changes=True)
        self.write_state(self.remote_state(include_changes=True))

        result = self.run_plan()

        operations = self.operation_records(result.stdout)
        by_name = {name: (kind, action) for kind, name, action in operations}
        self.assertIn("New Skill", by_name, result.stderr)
        self.assertEqual(("workspace", "update"), by_name["Local Workspace"])
        self.assertEqual(("skill", "create"), by_name["New Skill"])
        self.assertEqual(("agent", "update"), by_name["Desired Agent"])
        self.assertEqual(("squad", "update"), by_name["Desired Squad"])
        self.assertEqual(("squad", "archive"), by_name["Stale Squad"])
        self.assertEqual(("agent", "archive"), by_name["Stale Agent"])
        self.assertEqual(("skill", "delete"), by_name["Stale Skill"])
        self.assertNotIn("Ignored Local Skill", by_name)

        positions = {name: index for index, (_, name, _) in enumerate(operations)}
        self.assertLess(positions["Local Workspace"], positions["New Skill"])
        self.assertLess(positions["New Skill"], positions["Desired Agent"])
        self.assertLess(positions["Desired Agent"], positions["Desired Squad"])
        self.assertLess(positions["Desired Squad"], positions["Stale Squad"])
        self.assertLess(positions["Stale Squad"], positions["Stale Agent"])
        self.assertLess(positions["Stale Agent"], positions["Stale Skill"])

    def test_changed_plan_does_not_mutate_remote_or_local_state(self) -> None:
        self.write_desired_state(include_changes=True)
        self.write_state(self.remote_state(include_changes=True))
        before = self.tree_manifest(self.repo)

        result = self.run_plan()

        self.assertTrue(self.operation_records(result.stdout), result.stderr)
        self.assertEqual(before, self.tree_manifest(self.repo))
        self.assertFalse(self.mutation_sentinel.exists())

    def test_changed_plan_redacts_free_text(self) -> None:
        self.write_desired_state(include_changes=True)
        self.write_state(self.remote_state(include_changes=True))

        result = self.run_plan()

        self.assertTrue(self.operation_records(result.stdout), result.stderr)
        combined_output = result.stdout + result.stderr
        for secret in SECRET_MARKERS:
            self.assertNotIn(secret, combined_output)

    def test_restore_plan_does_not_disclose_permission_member_ids(self) -> None:
        member_ids = (
            "70000000-0000-4000-8000-000000000001",
            "70000000-0000-4000-8000-000000000002",
        )
        self.write_desired_state(include_changes=False)
        workspace = self.repo / "src" / "workspace" / WORKSPACE_ID
        self._write_json(workspace / "squad.json", [])
        state = self.remote_state(include_changes=False)
        state["responses"]["agent_list_with_archived"] = [
            {
                "id": AGENT_ID,
                "name": "Desired Agent",
                "archived_at": "2026-08-01T00:00:00Z",
            }
        ]
        agent = state["responses"]["agent_get"]["by_id"][AGENT_ID]
        agent["permission_mode"] = "public_to"
        agent["invocation_targets"] = [
            {"target_type": "member", "target_id": member_id}
            for member_id in member_ids
        ]
        state["responses"]["squad_list"] = []
        state["responses"].pop("squad_get")
        state["responses"].pop("squad_member_list")
        self.write_state(state)

        result = self.run_plan()

        by_name = {
            name: (kind, action)
            for kind, name, action in self.operation_records(result.stdout)
        }
        self.assertEqual(("agent", "restore"), by_name["Desired Agent"])
        for member_id in member_ids:
            self.assertNotIn(member_id, result.stdout + result.stderr)

    def test_terminal_control_characters_are_escaped(self) -> None:
        unsafe_name = "Desired\x1b[31m Skill"
        self.write_desired_state(include_changes=False)
        skill_path = self.repo / "src" / "skills" / "desired-skill" / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                json.dumps("Desired Skill"), json.dumps(unsafe_name)
            ),
            encoding="utf-8",
        )
        state = self.remote_state(include_changes=False)
        for skill in state["responses"]["skill_list"]:
            if skill["id"] == SKILL_ID:
                skill["name"] = unsafe_name
        state["responses"]["skill_get"]["by_id"][SKILL_ID]["name"] = unsafe_name
        self.write_state(state)

        result = self.run_plan()

        combined_output = result.stdout + result.stderr
        self.assertNotIn("\x1b", combined_output)
        self.assertTrue(self.operation_records(result.stdout), result.stderr)

    def test_squad_replaces_archived_same_name_agent_identity(self) -> None:
        self.write_desired_state(include_changes=False)
        state = self.remote_state(include_changes=False)
        state["responses"]["agent_list_with_archived"] = [
            {"id": AGENT_ID, "name": "Desired Agent", "archived_at": None},
            {
                "id": STALE_AGENT_ID,
                "name": "Desired Agent",
                "archived_at": "2026-08-01T00:00:00Z",
            },
        ]
        state["responses"]["squad_get"]["by_id"][SQUAD_ID][
            "leader_id"
        ] = STALE_AGENT_ID
        state["responses"]["squad_member_list"]["by_id"][SQUAD_ID] = [
            {
                "member_id": STALE_AGENT_ID,
                "member_type": "agent",
                "role": "same-role",
            }
        ]
        self.write_state(state)

        result = self.run_plan()

        by_name = {
            name: (kind, action)
            for kind, name, action in self.operation_records(result.stdout)
        }
        self.assertEqual(("squad", "update"), by_name["Desired Squad"])

    def test_equal_managed_projection_has_no_operations(self) -> None:
        self.write_desired_state(include_changes=False)
        self.write_state(self.remote_state(include_changes=False))

        first = self.run_plan()
        self.assertTrue(first.stdout, first.stderr)
        self.assertEqual([], self.operation_records(first.stdout))

    def test_exported_skill_with_empty_remote_description_has_no_operations(
        self,
    ) -> None:
        self.write_desired_state(include_changes=False)
        state = self.remote_state(include_changes=False)
        state["responses"]["skill_get"]["by_id"][SKILL_ID]["description"] = ""
        self.write_state(state)

        result = self.run_plan()

        result.check_returncode()
        self.assertEqual([], self.operation_records(result.stdout))

    def test_selected_workspace_is_isolated_from_malformed_sibling(self) -> None:
        self.write_desired_state(include_changes=True)
        sibling = self.repo / "src" / "workspace" / SIBLING_WORKSPACE_ID
        self._write_text(sibling / "metadata.json", "{malformed sibling metadata")
        self._write_text(sibling / "skill.json", "{malformed sibling selector")
        self._write_text(sibling / "agent.json", "{malformed sibling selector")
        self._write_text(sibling / "squad.json", "{malformed sibling selector")
        self.write_state(self.remote_state(include_changes=True))

        result = self.run_plan()

        planned_resources = {
            name: (kind, action)
            for kind, name, action in self.operation_records(result.stdout)
        }
        self.assertEqual(("skill", "create"), planned_resources.get("New Skill"))

    def test_selected_workspace_ignores_malformed_unselected_skill(self) -> None:
        self.write_desired_state(include_changes=True)
        self._write_text(
            self.repo / "src" / "skills" / "other-workspace-skill" / "SKILL.md",
            "malformed skill definition without frontmatter",
        )
        self.write_state(self.remote_state(include_changes=True))

        result = self.run_plan()

        planned_resources = {
            name: (kind, action)
            for kind, name, action in self.operation_records(result.stdout)
        }
        self.assertEqual(("skill", "create"), planned_resources.get("New Skill"))


if __name__ == "__main__":
    unittest.main()
