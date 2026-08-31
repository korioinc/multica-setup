from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

from quick_action_server import QuickActionServer

from multica_setup.constants import MANAGED_CATEGORIES
from multica_setup.errors import ExportError
from multica_setup.export import publish_snapshot
from multica_setup.local_state import load_desired_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CLI = PROJECT_ROOT / "bin" / "multica-setup"

WORKSPACE_ID = "10000000-0000-4000-8000-000000000001"
OTHER_WORKSPACE_ID = "10000000-0000-4000-8000-000000000009"
RUNTIME_ID = "20000000-0000-4000-8000-000000000002"
AGENT_ID = "30000000-0000-4000-8000-000000000003"
SKILL_ID = "40000000-0000-4000-8000-000000000004"
ADDITIONAL_SKILL_ID = "40000000-0000-4000-8000-000000000006"
SQUAD_ID = "50000000-0000-4000-8000-000000000005"
SECRET_VALUES = (
    "auth-token-must-not-be-exported",
    "custom-env-must-not-be-exported",
    "mcp-config-must-not-be-exported",
    "owner-id-must-not-be-exported",
)


FAKE_MULTICA = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys


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
if tokens == ["workspace", "list"]:
    endpoint = "workspace_list"
elif tokens[:2] == ["workspace", "get"]:
    endpoint = "workspace_get"
elif tokens == ["runtime", "list"]:
    endpoint = "runtime_list"
elif tokens == ["agent", "list"]:
    endpoint = "agent_list"
elif tokens[:2] == ["agent", "get"] and len(tokens) == 3:
    endpoint = "agent_get"
elif tokens == ["skill", "list"]:
    endpoint = "skill_list"
elif tokens[:2] == ["skill", "get"] and len(tokens) == 3:
    endpoint = "skill_get"
elif tokens == ["squad", "list"]:
    endpoint = "squad_list"
elif tokens[:2] == ["squad", "get"] and len(tokens) == 3:
    endpoint = "squad_get"
elif tokens[:3] == ["squad", "member", "list"] and len(tokens) == 4:
    endpoint = "squad_member_list"
elif tokens == ["autopilot", "list"]:
    endpoint = "autopilot_list"
elif tokens[:2] == ["autopilot", "get"] and len(tokens) == 3:
    endpoint = "autopilot_get"
else:
    print("unexpected fake multica command", file=sys.stderr)
    raise SystemExit(64)

state = json.loads(Path(os.environ["FAKE_MULTICA_STATE"]).read_text(encoding="utf-8"))
response = state["responses"][endpoint]
if "by_selector" in response:
    response = response["by_selector"][tokens[-1]]
json.dump(response, sys.stdout, ensure_ascii=False)
"""


def valid_state() -> dict[str, object]:
    return {
        "responses": {
            "workspace_get": {
                "id": WORKSPACE_ID,
                "name": "Export Workspace",
                "description": "workspace-description-marker",
                "issue_prefix": "EXP",
                "context": "워크스페이스 지침 marker",
                "auth_token": SECRET_VALUES[0],
                "owner_id": SECRET_VALUES[3],
            },
            "runtime_list": [
                {
                    "id": RUNTIME_ID,
                    "name": "codex",
                    "custom_name": None,
                    "provider": "openai",
                }
            ],
            "agent_list": [{"id": AGENT_ID, "name": "Delivery Agent"}],
            "agent_get": {
                "id": AGENT_ID,
                "name": "Delivery Agent",
                "description": "agent-description-marker",
                "instructions": "에이전트 지침 marker",
                "runtime_id": RUNTIME_ID,
                "model": "fixture-model",
                "max_concurrent_tasks": 2,
                "skills": [{"id": SKILL_ID}],
                "custom_environment": {"API_KEY": SECRET_VALUES[1]},
            },
            "skill_list": [{"id": SKILL_ID, "name": "Review Skill"}],
            "skill_get": {
                "id": SKILL_ID,
                "name": "Review Skill",
                "description": "skill-description-marker",
                "content": "# Skill body marker\n",
                "files": [
                    {
                        "path": "references/checklist.md",
                        "content": "support-file marker\n",
                    }
                ],
                "mcp_config": {"credential": SECRET_VALUES[2]},
            },
            "squad_list": [{"id": SQUAD_ID, "name": "Delivery Squad"}],
            "squad_get": {
                "id": SQUAD_ID,
                "name": "Delivery Squad",
                "description": "squad-description-marker",
                "instructions": "스쿼드 지침 marker",
                "leader_id": AGENT_ID,
            },
            "squad_member_list": [
                {
                    "member_id": AGENT_ID,
                    "member_type": "agent",
                    "role": "Lead",
                }
            ],
            "autopilot_list": {"autopilots": [], "total": 0},
            "autopilot_get": {},
        }
    }


class ExportBlackBoxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary_directory.name) / "repository"
        self.cli = self.repo / "bin" / "multica-setup"
        self.fake_bin = Path(self._temporary_directory.name) / "fake-bin"
        self.state_path = Path(self._temporary_directory.name) / "state.json"

        self.cli.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_CLI, self.cli)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)

        self.fake_bin.mkdir()
        fake_multica = self.fake_bin / "multica"
        fake_multica.write_text(textwrap.dedent(FAKE_MULTICA), encoding="utf-8")
        fake_multica.chmod(fake_multica.stat().st_mode | stat.S_IXUSR)
        self.quick_action_server = QuickActionServer(
            WORKSPACE_ID, allow_any_workspace=True
        )
        self.quick_action_server.__enter__()

    def tearDown(self) -> None:
        self.quick_action_server.__exit__()
        self._temporary_directory.cleanup()

    def write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def run_export(
        self, selector: str = "export-workspace"
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(PROJECT_ROOT), environment.get("PYTHONPATH", ""))
        )
        environment["PATH"] = os.pathsep.join(
            (str(self.fake_bin), environment.get("PATH", ""))
        )
        environment["FAKE_MULTICA_STATE"] = str(self.state_path)
        environment["MULTICA_SERVER_URL"] = self.quick_action_server.url
        environment["MULTICA_TOKEN"] = "test-token"
        return subprocess.run(
            [str(self.cli), "export", "--workspace", selector],
            cwd=self.repo,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_interactive_export(self, answer: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(PROJECT_ROOT), environment.get("PYTHONPATH", ""))
        )
        environment["PATH"] = os.pathsep.join(
            (str(self.fake_bin), environment.get("PATH", ""))
        )
        environment["FAKE_MULTICA_STATE"] = str(self.state_path)
        environment["MULTICA_SERVER_URL"] = self.quick_action_server.url
        environment["MULTICA_TOKEN"] = "test-token"
        environment.pop("CI", None)

        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                [str(self.cli), "export"],
                cwd=self.repo,
                env=environment,
                stdin=slave_fd,
                stdout=subprocess.PIPE,
                stderr=slave_fd,
                text=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            os.write(master_fd, answer.encode("utf-8"))
            stdout, _ = process.communicate(timeout=10)
            return subprocess.CompletedProcess(
                process.args,
                process.returncode,
                stdout,
                None,
            )
        finally:
            if slave_fd >= 0:
                os.close(slave_fd)
            os.close(master_fd)

    @staticmethod
    def snapshot_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_export_preserves_requested_text_without_leaking_unrequested_secrets(
        self,
    ) -> None:
        self.write_state(valid_state())

        self.run_export()

        source = self.repo / "src"
        exported_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in source.rglob("*")
            if path.is_file()
        )
        for marker in (
            "워크스페이스 지침 marker",
            "에이전트 지침 marker",
            "스쿼드 지침 marker",
            "# Skill body marker",
            "support-file marker",
        ):
            self.assertIn(marker, exported_text)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, exported_text)

    def test_publish_failure_restores_the_previous_managed_snapshot(self) -> None:
        source = Path(self._temporary_directory.name) / "managed"
        stage = Path(self._temporary_directory.name) / "stage"
        for category in MANAGED_CATEGORIES:
            (source / category).mkdir(parents=True)
            (stage / category).mkdir(parents=True)
            (source / category / "payload").write_text(
                f"old-{category}", encoding="utf-8"
            )
            (stage / category / "payload").write_text(
                f"new-{category}", encoding="utf-8"
            )
        original = self.snapshot_bytes(source)

        real_replace = os.replace
        replace_calls = 0

        def fail_during_install(source_path: Path, destination_path: Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 7:
                raise OSError("injected install failure")
            real_replace(source_path, destination_path)

        publish_snapshot.__globals__["os"].replace = fail_during_install
        try:
            with self.assertRaises(ExportError):
                publish_snapshot(source, stage, True)
        finally:
            publish_snapshot.__globals__["os"].replace = real_replace

        self.assertEqual(original, self.snapshot_bytes(source))

    def test_interactive_selection_exports_the_selected_workspace_content(self) -> None:
        state = valid_state()
        selected_workspace = state["responses"]["workspace_get"]
        selected_workspace = {
            **selected_workspace,
            "context": "explicitly-selected-workspace-marker",
        }
        state["responses"]["workspace_list"] = [
            {
                "id": WORKSPACE_ID,
                "name": "Export Workspace",
                "slug": "export-workspace",
            },
        ]
        state["responses"]["workspace_get"] = {
            "by_selector": {
                WORKSPACE_ID: selected_workspace,
            }
        }
        self.write_state(state)

        self.run_interactive_export("1\n")

        exported_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.repo / "src").rglob("*")
            if path.is_file()
        )
        self.assertIn("explicitly-selected-workspace-marker", exported_text)

    def test_export_updates_selected_workspace_without_changing_sibling_desired_state(
        self,
    ) -> None:
        state = valid_state()
        self.write_state(state)
        self.run_export()

        target_before = load_desired_state(self.repo, WORKSPACE_ID)

        source = self.repo / "src"
        sibling_workspace = source / "workspace" / OTHER_WORKSPACE_ID
        shutil.copytree(source / "workspace" / WORKSPACE_ID, sibling_workspace)
        sibling_skill_slug = "sibling-only-skill"
        sibling_skill = source / "skills" / sibling_skill_slug
        sibling_skill.mkdir()
        (sibling_skill / "SKILL.md").write_text(
            "---\n"
            'name: "Sibling Only Skill"\n'
            'description: "Selected only by the sibling workspace"\n'
            "---\n"
            "Sibling-only instructions.\n",
            encoding="utf-8",
        )
        sibling_skills = [
            *(skill.slug for skill in target_before.skills),
            sibling_skill_slug,
        ]
        (sibling_workspace / "skill.json").write_text(
            json.dumps(sibling_skills, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        sibling_before = load_desired_state(self.repo, OTHER_WORKSPACE_ID)

        updated_context = "updated-selected-workspace-marker"
        state["responses"]["workspace_get"] = {
            **state["responses"]["workspace_get"],
            "context": updated_context,
        }
        self.write_state(state)

        self.run_export()

        target_after = load_desired_state(self.repo, WORKSPACE_ID)
        sibling_after = load_desired_state(self.repo, OTHER_WORKSPACE_ID)
        self.assertEqual(updated_context, target_after.workspace_context)
        self.assertEqual(sibling_before, sibling_after)

    def test_export_preserves_workspaces_when_shared_agent_would_break_sibling(
        self,
    ) -> None:
        state = valid_state()
        original_skill = state["responses"]["skill_get"]
        additional_skill_name = "Additional Agent Skill"
        state["responses"]["skill_list"].append(
            {"id": ADDITIONAL_SKILL_ID, "name": additional_skill_name}
        )
        state["responses"]["skill_get"] = {
            "by_selector": {
                SKILL_ID: original_skill,
                ADDITIONAL_SKILL_ID: {
                    "id": ADDITIONAL_SKILL_ID,
                    "name": additional_skill_name,
                    "description": "Not selected by the sibling workspace",
                    "content": "Additional skill instructions.\n",
                    "files": [],
                },
            }
        }
        self.write_state(state)
        self.run_export()

        source = self.repo / "src"
        sibling_workspace = source / "workspace" / OTHER_WORKSPACE_ID
        shutil.copytree(source / "workspace" / WORKSPACE_ID, sibling_workspace)
        target_before = load_desired_state(self.repo, WORKSPACE_ID)
        sibling_selected_skills = target_before.agents[0].skill_slugs
        additional_skill_slug = next(
            skill.slug
            for skill in target_before.skills
            if skill.name == additional_skill_name
        )
        (sibling_workspace / "skill.json").write_text(
            json.dumps(sibling_selected_skills, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        sibling_before = load_desired_state(self.repo, OTHER_WORKSPACE_ID)

        state["responses"]["workspace_get"] = {
            **state["responses"]["workspace_get"],
            "context": "must-not-be-published",
        }
        state["responses"]["agent_get"] = {
            **state["responses"]["agent_get"],
            "skills": [{"id": SKILL_ID}, {"id": ADDITIONAL_SKILL_ID}],
        }
        self.write_state(state)

        self.run_export()

        target_after_rejected = load_desired_state(self.repo, WORKSPACE_ID)
        sibling_after_rejected = load_desired_state(self.repo, OTHER_WORKSPACE_ID)
        self.assertEqual(target_before, target_after_rejected)
        self.assertEqual(sibling_before, sibling_after_rejected)

        (sibling_workspace / "skill.json").write_text(
            json.dumps(
                [*sibling_selected_skills, additional_skill_slug],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.run_export()

        target_after_allowed = load_desired_state(self.repo, WORKSPACE_ID)
        sibling_after_allowed = load_desired_state(self.repo, OTHER_WORKSPACE_ID)
        self.assertEqual(
            state["responses"]["workspace_get"]["context"],
            target_after_allowed.workspace_context,
        )
        self.assertNotEqual(sibling_before.agents, sibling_after_allowed.agents)
        self.assertEqual(target_after_allowed.agents, sibling_after_allowed.agents)

    def test_interactive_identity_mismatch_preserves_existing_managed_snapshot(
        self,
    ) -> None:
        state = valid_state()
        existing_workspace = {
            **state["responses"]["workspace_get"],
            "id": OTHER_WORKSPACE_ID,
            "name": "Existing Workspace",
            "context": "original-existing-workspace-marker",
        }
        state["responses"]["workspace_get"] = {
            "by_selector": {"existing-workspace": existing_workspace}
        }
        self.write_state(state)
        self.run_export("existing-workspace").check_returncode()
        source = self.repo / "src"
        original = self.snapshot_bytes(source)

        state["responses"]["workspace_list"] = [
            {
                "id": WORKSPACE_ID,
                "name": "Selected Workspace",
                "slug": "selected-workspace",
            },
        ]
        state["responses"]["workspace_get"] = {
            "by_selector": {
                WORKSPACE_ID: {
                    **existing_workspace,
                    "context": "changed-mismatched-workspace-marker",
                }
            }
        }
        self.write_state(state)

        self.run_interactive_export("1\n")

        self.assertEqual(original, self.snapshot_bytes(source))


if __name__ == "__main__":
    unittest.main()
