from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse


class QuickActionServer:
    def __init__(
        self,
        workspace_id: str,
        *,
        actions: list[dict[str, Any]] | None = None,
        created_ids: list[str] | None = None,
        allow_any_workspace: bool = False,
        target_state_path: Path | None = None,
        role: str = "owner",
    ) -> None:
        self.workspace_id = workspace_id
        self.actions = {
            value["id"]: self._complete(value)
            for value in (actions or [])
        }
        self.created_ids = list(created_ids or [])
        self.allow_any_workspace = allow_any_workspace
        self.target_state_path = target_state_path
        self.role = role
        self.user_id = "80000000-0000-4000-8000-000000000001"
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _complete(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "description": "",
            "visibility": "public",
            "status": "active",
            "last_used_at": None,
            "use_count": 0,
            "created_by_id": "80000000-0000-4000-8000-000000000001",
            "created_at": "2026-08-31T00:00:00Z",
            "updated_at": "2026-08-31T00:00:00Z",
            "target_public": True,
            "target_missing": False,
            **value,
        }

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def _valid_public_target(self, assignee_type: str, assignee_id: str) -> bool:
        if self.target_state_path is None:
            return True
        state = json.loads(self.target_state_path.read_text(encoding="utf-8"))
        remote = state["remote"]
        if assignee_type == "squad":
            squad = remote["squads"].get(assignee_id)
            if squad is None:
                return False
            assignee_id = squad["leader_id"]
        agent = remote["agents"].get(assignee_id)
        return bool(
            agent
            and not agent.get("archived_at")
            and agent.get("permission_mode") == "public_to"
            and any(
                value.get("target_type") == "workspace"
                and value.get("target_id") == self.workspace_id
                for value in agent.get("invocation_targets", [])
            )
        )

    def _load_target_state(self) -> dict[str, Any]:
        assert self.target_state_path is not None
        return json.loads(self.target_state_path.read_text(encoding="utf-8"))

    def _save_target_state(self, state: dict[str, Any]) -> None:
        assert self.target_state_path is not None
        self.target_state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _next_target_id(state: dict[str, Any], kind: str) -> str:
        return state["created_ids"][kind].pop(0)

    def __enter__(self) -> QuickActionServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _write_json(self, status: int, value: object) -> None:
                encoded = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _authorized(self) -> bool:
                return (
                    self.headers.get("Authorization") == "Bearer test-token"
                    and (
                        owner.allow_any_workspace
                        or self.headers.get("X-Workspace-ID") == owner.workspace_id
                    )
                )

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length))

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if not self._authorized():
                    self.send_error(404)
                    return
                if parsed.path == "/api/me":
                    self._write_json(200, {"id": owner.user_id})
                    return
                if parsed.path == f"/api/workspaces/{owner.workspace_id}/members":
                    members = None
                    if owner.target_state_path is not None and owner.target_state_path.exists():
                        state = owner._load_target_state()
                        members = state["remote"].get("members")
                    self._write_json(
                        200,
                        members or [
                            {
                                "workspace_id": owner.workspace_id,
                                "user_id": owner.user_id,
                                "role": owner.role,
                                "name": "Owner",
                                "email": "owner@example.com",
                            }
                        ],
                    )
                    return
                if parsed.path == "/api/autopilots":
                    with owner._lock:
                        values = []
                        if owner.target_state_path is not None:
                            state = owner._load_target_state()
                            values = [
                                dict(value)
                                for value in state["remote"]["autopilots"].values()
                                if value.get("status") != "archived"
                            ]
                    self._write_json(200, {"autopilots": values, "total": len(values)})
                    return
                autopilot_prefix = "/api/autopilots/"
                if (
                    owner.target_state_path is not None
                    and parsed.path.startswith(autopilot_prefix)
                    and "/triggers/" not in parsed.path
                ):
                    autopilot_id = parsed.path[len(autopilot_prefix) :]
                    with owner._lock:
                        state = owner._load_target_state()
                        autopilot = dict(state["remote"]["autopilots"][autopilot_id])
                        triggers = [
                            dict(value)
                            for value in state["remote"]["autopilot_triggers"].values()
                            if value["autopilot_id"] == autopilot_id
                        ]
                    self._write_json(
                        200,
                        {"autopilot": autopilot, "triggers": triggers, "collaborators": []},
                    )
                    return
                if parsed.path != "/api/quick-actions":
                    self.send_error(404)
                    return
                include_archived = parse_qs(parsed.query).get(
                    "include_archived"
                ) == ["true"]
                with owner._lock:
                    values = [
                        dict(value)
                        for value in owner.actions.values()
                        if include_archived or value["status"] == "active"
                    ]
                self._write_json(200, {"quick_actions": values})

            def do_POST(self) -> None:
                if owner.target_state_path is not None and self._authorized():
                    if self.path == "/api/autopilots":
                        body = self._body()
                        with owner._lock:
                            state = owner._load_target_state()
                            resource_id = owner._next_target_id(state, "autopilot")
                            value = {
                                "id": resource_id,
                                "workspace_id": owner.workspace_id,
                                "title": body["title"],
                                "description": body.get("description"),
                                "project_id": body.get("project_id"),
                                "assignee_type": body.get("assignee_type", "agent"),
                                "assignee_id": body["assignee_id"],
                                "execution_mode": body["execution_mode"],
                                "issue_title_template": body.get("issue_title_template"),
                                "subscribers": list(body.get("subscribers", [])),
                                "status": "active",
                                "can_write": True,
                            }
                            state["remote"]["autopilots"][resource_id] = value
                            owner._save_target_state(state)
                        self._write_json(201, value)
                        return
                    trigger_prefix = "/api/autopilots/"
                    trigger_suffix = "/triggers"
                    if self.path.startswith(trigger_prefix) and self.path.endswith(
                        trigger_suffix
                    ):
                        autopilot_id = self.path[
                            len(trigger_prefix) : -len(trigger_suffix)
                        ]
                        body = self._body()
                        with owner._lock:
                            state = owner._load_target_state()
                            if not state["remote"]["autopilots"][autopilot_id].get(
                                "can_write", False
                            ):
                                self.send_error(403)
                                return
                            trigger_id = owner._next_target_id(
                                state, "autopilot-trigger"
                            )
                            kind = body["kind"]
                            value = {
                                "id": trigger_id,
                                "autopilot_id": autopilot_id,
                                "kind": kind,
                                "enabled": True,
                                "label": body.get("label"),
                                "cron_expression": body.get("cron_expression"),
                                "timezone": body.get("timezone"),
                                "provider": (
                                    body.get("provider", "generic")
                                    if kind == "webhook"
                                    else None
                                ),
                                "event_filters": list(body.get("event_filters", [])),
                            }
                            if kind == "webhook":
                                value["signing_secret"] = "preserved-webhook-secret"
                            state["remote"]["autopilot_triggers"][trigger_id] = value
                            owner._save_target_state(state)
                        self._write_json(201, value)
                        return
                if self.path != "/api/quick-actions" or not self._authorized():
                    self.send_error(404)
                    return
                if owner.role not in {"owner", "admin"}:
                    self.send_error(403)
                    return
                body = self._body()
                if not owner._valid_public_target(
                    body["assignee_type"], body["assignee_id"]
                ):
                    self.send_error(400)
                    return
                with owner._lock:
                    resource_id = owner.created_ids.pop(0)
                    value = owner._complete(
                        {
                            "id": resource_id,
                            "name": body["name"].strip(),
                            "description": body.get("description", "").strip(),
                            "assignee_type": body["assignee_type"],
                            "assignee_id": body["assignee_id"],
                            "prompt": body["prompt"].strip(),
                            "visibility": body.get("visibility", "public"),
                        }
                    )
                    owner.actions[resource_id] = value
                self._write_json(201, value)

            def do_PATCH(self) -> None:
                autopilot_prefix = "/api/autopilots/"
                if (
                    owner.target_state_path is not None
                    and self._authorized()
                    and self.path.startswith(autopilot_prefix)
                ):
                    body = self._body()
                    remainder = self.path[len(autopilot_prefix) :]
                    with owner._lock:
                        state = owner._load_target_state()
                        if "/triggers/" in remainder:
                            autopilot_id, trigger_id = remainder.split("/triggers/", 1)
                            if not state["remote"]["autopilots"][autopilot_id].get(
                                "can_write", False
                            ):
                                self.send_error(403)
                                return
                            value = state["remote"]["autopilot_triggers"][trigger_id]
                            if value["autopilot_id"] != autopilot_id:
                                self.send_error(404)
                                return
                            for field in (
                                "enabled",
                                "label",
                                "cron_expression",
                                "timezone",
                                "event_filters",
                            ):
                                if field in body:
                                    if field == "label" and body[field] is None:
                                        continue
                                    value[field] = body[field]
                        else:
                            value = state["remote"]["autopilots"][remainder]
                            if not value.get("can_write", False):
                                self.send_error(403)
                                return
                            for field in (
                                "title",
                                "description",
                                "project_id",
                                "assignee_type",
                                "assignee_id",
                                "execution_mode",
                                "issue_title_template",
                                "subscribers",
                                "status",
                            ):
                                if field in body:
                                    if field == "description" and body[field] is None:
                                        continue
                                    value[field] = body[field]
                        owner._save_target_state(state)
                        response = dict(value)
                    self._write_json(200, response)
                    return
                prefix = "/api/quick-actions/"
                if not self.path.startswith(prefix) or not self._authorized():
                    self.send_error(404)
                    return
                if owner.role not in {"owner", "admin"}:
                    self.send_error(403)
                    return
                resource_id = self.path[len(prefix) :]
                body = self._body()
                with owner._lock:
                    value = owner.actions[resource_id]
                    resulting_type = body.get("assignee_type", value["assignee_type"])
                    resulting_id = body.get("assignee_id", value["assignee_id"])
                    resulting_visibility = body.get("visibility", value["visibility"])
                    if resulting_visibility == "public" and not owner._valid_public_target(
                        resulting_type, resulting_id
                    ):
                        self.send_error(400)
                        return
                    for field in (
                        "name",
                        "description",
                        "assignee_type",
                        "assignee_id",
                        "prompt",
                        "visibility",
                        "status",
                    ):
                        if field in body:
                            replacement = body[field]
                            if field in {"name", "description", "prompt"}:
                                replacement = replacement.strip()
                            value[field] = replacement
                    response = dict(value)
                self._write_json(200, response)

            def do_DELETE(self) -> None:
                autopilot_prefix = "/api/autopilots/"
                if (
                    owner.target_state_path is not None
                    and self._authorized()
                    and self.path.startswith(autopilot_prefix)
                ):
                    remainder = self.path[len(autopilot_prefix) :]
                    with owner._lock:
                        state = owner._load_target_state()
                        if "/triggers/" in remainder:
                            autopilot_id, trigger_id = remainder.split("/triggers/", 1)
                            if not state["remote"]["autopilots"][autopilot_id].get(
                                "can_write", False
                            ):
                                self.send_error(403)
                                return
                            trigger = state["remote"]["autopilot_triggers"][trigger_id]
                            if trigger["autopilot_id"] != autopilot_id:
                                self.send_error(404)
                                return
                            del state["remote"]["autopilot_triggers"][trigger_id]
                        else:
                            autopilot = state["remote"]["autopilots"][remainder]
                            if not autopilot.get("can_write", False):
                                self.send_error(403)
                                return
                            autopilot["status"] = "archived"
                        owner._save_target_state(state)
                    self.send_response(204)
                    self.end_headers()
                    return
                prefix = "/api/quick-actions/"
                if not self.path.startswith(prefix) or not self._authorized():
                    self.send_error(404)
                    return
                if owner.role not in {"owner", "admin"}:
                    self.send_error(403)
                    return
                resource_id = self.path[len(prefix) :]
                with owner._lock:
                    del owner.actions[resource_id]
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        assert self._thread is not None
        self._thread.join(timeout=5)
