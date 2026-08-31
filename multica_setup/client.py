"""Adapters for the installed Multica CLI and authenticated REST API."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .constants import TIMEOUT_SECONDS
from .errors import ExportError
from .filesystem import _actual_file, _read_local_json
from .validation import _brief, _object, _string


def _multica_config_path() -> Path | None:
    task_root = os.environ.get("MULTICA_TASK_CONFIG_ROOT", "").strip()
    if task_root:
        return Path(task_root) / "config.json"
    if any(
        os.environ.get(value, "").strip()
        for value in ("MULTICA_AGENT_ID", "MULTICA_TASK_ID", "MULTICA_DAEMON_PORT")
    ):
        return None
    return Path.home() / ".multica" / "config.json"


def _multica_config() -> dict[str, Any]:
    path = _multica_config_path()
    if path is None or not path.exists():
        return {}
    if not _actual_file(path):
        raise ExportError("Multica API: config must be a regular file")
    return _object(_read_local_json(path, "Multica config"), "Multica config")


def _normalize_server_url(raw: str) -> str:
    value = raw.strip()
    try:
        parsed = urllib_parse.urlsplit(value)
    except ValueError as exc:
        raise ExportError("Multica API: invalid server URL") from exc
    schemes = {"ws": "http", "wss": "https", "http": "http", "https": "https"}
    scheme = schemes.get(parsed.scheme.casefold())
    if scheme is None or not parsed.netloc:
        raise ExportError("Multica API: server URL must use ws, wss, http, or https")
    path = "" if parsed.path == "/ws" else parsed.path.rstrip("/")
    return urllib_parse.urlunsplit((scheme, parsed.netloc, path, "", ""))


def _multica_api_config() -> tuple[str, str]:
    server_url = os.environ.get("MULTICA_SERVER_URL", "").strip()
    token = os.environ.get("MULTICA_TOKEN", "").strip()
    if not server_url or not token:
        config = _multica_config()
        if not server_url:
            server_url = _string(
                config.get("server_url", ""), "Multica config.server_url"
            )
        if not token:
            token = _string(config.get("token", ""), "Multica config.token")
    if not server_url:
        raise ExportError(
            "Multica API: no server configured; run 'multica setup' first"
        )
    if not token:
        raise ExportError(
            "Multica API: no authentication token configured; run 'multica login' first"
        )
    return _normalize_server_url(server_url), token


def _multica_api_request(
    method: str,
    path: str,
    workspace_id: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    server_url, token = _multica_api_config()
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib_request.Request(
        f"{server_url}{path}",
        data=encoded,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Workspace-ID": workspace_id,
            "X-Client-Platform": "cli",
        },
    )
    agent_id = os.environ.get("MULTICA_AGENT_ID", "").strip()
    task_id = os.environ.get("MULTICA_TASK_ID", "").strip()
    if agent_id:
        request.add_header("X-Agent-ID", agent_id)
    if task_id:
        request.add_header("X-Task-ID", task_id)
    try:
        with urllib_request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib_error.HTTPError as exc:
        raise ExportError(
            f"Multica API: {method} {path} returned HTTP {exc.code}"
        ) from exc
    except (urllib_error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise ExportError(
            f"Multica API: {method} {path} failed: {_brief(str(exc))}"
        ) from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ExportError(
            f"Multica API: {method} {path} returned invalid JSON"
        ) from exc


class MulticaClient:
    def _invoke(
        self,
        args: Sequence[str],
        endpoint: str,
        workspace_id: str | None,
        *,
        expect_json: bool,
        input_text: str | None = None,
        redact_stderr: bool = False,
    ) -> Any:
        command = ["multica", *args]
        if expect_json:
            command.extend(("--output", "json"))
        if workspace_id is not None:
            command.extend(("--workspace-id", workspace_id))
        try:
            result = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExportError(
                f"{endpoint}: multica command timed out after {TIMEOUT_SECONDS}s"
            ) from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise ExportError(
                f"{endpoint}: could not run multica: {_brief(str(exc))}"
            ) from exc

        if result.returncode != 0:
            if redact_stderr:
                raise ExportError(f"{endpoint}: multica exited {result.returncode}")
            detail = _brief(result.stderr) or "no error detail"
            raise ExportError(
                f"{endpoint}: multica exited {result.returncode}: {detail}"
            )
        if not expect_json:
            return None
        if not result.stdout.strip():
            raise ExportError(f"{endpoint}: multica returned empty output")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ExportError(f"{endpoint}: multica returned invalid JSON") from exc

    def run(
        self, args: Sequence[str], endpoint: str, workspace_id: str | None = None
    ) -> Any:
        return self._invoke(args, endpoint, workspace_id, expect_json=True)

    def write_json(
        self,
        args: Sequence[str],
        endpoint: str,
        workspace_id: str,
        *,
        input_text: str | None = None,
    ) -> Any:
        return self._invoke(
            args,
            endpoint,
            workspace_id,
            expect_json=True,
            input_text=input_text,
            redact_stderr=True,
        )

    def write_no_output(
        self, args: Sequence[str], endpoint: str, workspace_id: str
    ) -> None:
        self._invoke(
            args,
            endpoint,
            workspace_id,
            expect_json=False,
            redact_stderr=True,
        )

    def workspace_get(self, selector: str) -> Any:
        return self.run(["workspace", "get", selector], "workspace get")

    def workspace_list(self) -> Any:
        return self.run(["workspace", "list"], "workspace list")

    def runtime_list(self, workspace_id: str) -> Any:
        return self.run(["runtime", "list"], "runtime list", workspace_id)

    def agent_list(self, workspace_id: str, include_archived: bool = False) -> Any:
        args = ["agent", "list"]
        if include_archived:
            args.append("--include-archived")
        return self.run(args, "agent list", workspace_id)

    def agent_get(self, resource_id: str, workspace_id: str) -> Any:
        return self.run(["agent", "get", resource_id], "agent get", workspace_id)

    def skill_list(self, workspace_id: str) -> Any:
        return self.run(["skill", "list"], "skill list", workspace_id)

    def skill_get(self, resource_id: str, workspace_id: str) -> Any:
        return self.run(["skill", "get", resource_id], "skill get", workspace_id)

    def squad_list(self, workspace_id: str) -> Any:
        return self.run(["squad", "list"], "squad list", workspace_id)

    def squad_get(self, resource_id: str, workspace_id: str) -> Any:
        return self.run(["squad", "get", resource_id], "squad get", workspace_id)

    def squad_members(self, resource_id: str, workspace_id: str) -> Any:
        return self.run(
            ["squad", "member", "list", resource_id],
            "squad member list",
            workspace_id,
        )

    def quick_action_list(self, workspace_id: str, include_archived: bool) -> Any:
        query = "?include_archived=true" if include_archived else ""
        return _multica_api_request("GET", f"/api/quick-actions{query}", workspace_id)

    def quick_action_create(self, workspace_id: str, payload: dict[str, Any]) -> Any:
        return _multica_api_request("POST", "/api/quick-actions", workspace_id, payload)

    def quick_action_update(
        self, resource_id: str, workspace_id: str, payload: dict[str, Any]
    ) -> Any:
        return _multica_api_request(
            "PATCH", f"/api/quick-actions/{resource_id}", workspace_id, payload
        )

    def quick_action_delete(self, resource_id: str, workspace_id: str) -> None:
        _multica_api_request(
            "DELETE", f"/api/quick-actions/{resource_id}", workspace_id
        )

    def autopilot_list(self, workspace_id: str) -> Any:
        return _multica_api_request("GET", "/api/autopilots", workspace_id)

    def autopilot_get(self, resource_id: str, workspace_id: str) -> Any:
        return _multica_api_request(
            "GET", f"/api/autopilots/{resource_id}", workspace_id
        )

    def autopilot_create(self, workspace_id: str, payload: dict[str, Any]) -> Any:
        return _multica_api_request("POST", "/api/autopilots", workspace_id, payload)

    def autopilot_update(
        self, resource_id: str, workspace_id: str, payload: dict[str, Any]
    ) -> Any:
        return _multica_api_request(
            "PATCH", f"/api/autopilots/{resource_id}", workspace_id, payload
        )

    def autopilot_delete(self, resource_id: str, workspace_id: str) -> None:
        _multica_api_request("DELETE", f"/api/autopilots/{resource_id}", workspace_id)

    def autopilot_trigger_create(
        self, autopilot_id: str, workspace_id: str, payload: dict[str, Any]
    ) -> Any:
        return _multica_api_request(
            "POST",
            f"/api/autopilots/{autopilot_id}/triggers",
            workspace_id,
            payload,
        )

    def autopilot_trigger_update(
        self,
        autopilot_id: str,
        trigger_id: str,
        workspace_id: str,
        payload: dict[str, Any],
    ) -> Any:
        return _multica_api_request(
            "PATCH",
            f"/api/autopilots/{autopilot_id}/triggers/{trigger_id}",
            workspace_id,
            payload,
        )

    def autopilot_trigger_delete(
        self, autopilot_id: str, trigger_id: str, workspace_id: str
    ) -> None:
        _multica_api_request(
            "DELETE",
            f"/api/autopilots/{autopilot_id}/triggers/{trigger_id}",
            workspace_id,
        )

    def project_list(self, workspace_id: str) -> Any:
        return self.run(["project", "list"], "project list", workspace_id)

    def current_user(self, workspace_id: str) -> Any:
        return _multica_api_request("GET", "/api/me", workspace_id)

    def workspace_members(self, workspace_id: str) -> Any:
        return _multica_api_request(
            "GET", f"/api/workspaces/{workspace_id}/members", workspace_id
        )
