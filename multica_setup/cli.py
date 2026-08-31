"""Command-line interface for Multica workspace reconciliation."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence

from . import __version__
from .apply import (
    _plan_counts,
    _print_apply_failure,
    apply_workspace,
)
from .client import MulticaClient
from .context import current_repository_root
from .errors import (
    ApplyExecutionError,
    ApplyInterrupted,
    ExportCancelled,
    ExportError,
)
from .export import export_workspace
from .normalization import _terminal_text
from .rendering import render_plan
from .snapshot import _validate_workspace_list
from .validation import _brief
from .workflow import plan_workspace


def _selector(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("workspace selector must not be blank")
    return value.strip()


def _ci_environment() -> bool:
    value = os.environ.get("CI")
    return value is not None and value.strip().casefold() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _interactive_selection_available() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty() and not _ci_environment()


def _plan_color_enabled(no_color: bool) -> bool:
    return (
        not no_color
        and sys.stdout.isatty()
        and not _ci_environment()
        and not bool(os.environ.get("NO_COLOR"))
        and os.environ.get("TERM", "").casefold() != "dumb"
    )


def choose_workspace(client: MulticaClient) -> str:
    workspaces = _validate_workspace_list(client.workspace_list())
    if not workspaces:
        raise ExportError("workspace list: no available workspaces")

    print("Available workspaces:", file=sys.stderr)
    for index, workspace in enumerate(workspaces, 1):
        name = _terminal_text(workspace["name"])
        slug = _terminal_text(workspace["slug"])
        print(
            f"  {index}. {name} — {slug} — {workspace['id']}",
            file=sys.stderr,
        )

    while True:
        print(
            f"Select workspace [1-{len(workspaces)}, q to cancel]: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        response = sys.stdin.readline()
        if response == "":
            raise ExportCancelled
        selection = response.strip()
        if selection.casefold() in {"q", "quit"}:
            raise ExportCancelled
        if re.fullmatch(r"[0-9]+", selection):
            selected_index = int(selection)
            if 1 <= selected_index <= len(workspaces):
                return workspaces[selected_index - 1]["id"]
        print("Invalid selection. Enter a workspace number or q.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multica-setup",
        description="Export, plan, or apply reconciliation for a Multica workspace.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser(
        "export",
        help="export one workspace",
        description="Export one Multica workspace snapshot.",
    )
    plan_parser = subparsers.add_parser(
        "plan",
        help="preview local-to-remote reconciliation",
        description="Preview a read-only reconciliation plan for one workspace.",
    )
    plan_parser.add_argument(
        "-no-color",
        "--no-color",
        action="store_true",
        help="disable ANSI colors in plan output",
    )
    apply_parser = subparsers.add_parser(
        "apply",
        help="apply local-to-remote reconciliation",
        description="Preview, approve, and apply reconciliation for one workspace.",
    )
    apply_parser.add_argument(
        "-auto-approve",
        "--auto-approve",
        action="store_true",
        help="skip interactive approval of the displayed plan",
    )
    apply_parser.add_argument(
        "-no-color",
        "--no-color",
        action="store_true",
        help="disable ANSI colors in apply plan output",
    )
    for command_parser in (export_parser, plan_parser, apply_parser):
        command_parser.add_argument(
            "--workspace",
            type=_selector,
            metavar="SLUG_OR_UUID",
            help=(
                "workspace slug, full UUID, or unique UUID prefix; "
                "omit in an interactive terminal to choose"
            ),
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workspace is None and not _interactive_selection_available():
        parser.error(
            "--workspace is required when interactive selection is unavailable"
        )
    try:
        root = current_repository_root()
        selector = args.workspace
        expected_workspace_id = None
        if selector is None:
            selector = choose_workspace(MulticaClient())
            expected_workspace_id = selector
        if args.command == "plan":
            plan = plan_workspace(
                selector,
                expected_workspace_id,
                repository_root=root,
            )
            snapshot = None
            output_root = None
            changed = False
            warnings: list[str] = []
        elif args.command == "apply":
            plan = apply_workspace(
                selector,
                expected_workspace_id,
                auto_approve=args.auto_approve,
                color=_plan_color_enabled(args.no_color),
                repository_root=root,
            )
            snapshot = None
            output_root = None
            changed = False
            warnings = []
        else:
            snapshot, output_root, changed, warnings = export_workspace(
                selector,
                expected_workspace_id,
                repository_root=root,
            )
    except ExportCancelled:
        detail = " No remote changes were made." if args.command == "apply" else ""
        print(f"{args.command.capitalize()} cancelled.{detail}", file=sys.stderr)
        return 1
    except ApplyInterrupted as exc:
        _print_apply_failure(exc)
        return 130
    except ApplyExecutionError as exc:
        _print_apply_failure(exc)
        return 1
    except ExportError as exc:
        print(f"error: {_terminal_text(str(exc))}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(
            f"error: filesystem failure: {_terminal_text(_brief(str(exc)))}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130

    if args.command == "plan":
        print(render_plan(plan, color=_plan_color_enabled(args.no_color)), end="")
        return 0
    if args.command == "apply":
        added, changed_count, destroyed = _plan_counts(plan)
        print(
            "Apply complete! Resources: "
            f"{added} added, {changed_count} changed, {destroyed} destroyed."
        )
        return 0

    for warning in warnings:
        print(f"warning: {_terminal_text(warning)}", file=sys.stderr)
    assert snapshot is not None and output_root is not None
    action = "updated" if changed else "already current"
    print(
        f"Workspace {snapshot['workspace']['id']} exported to "
        f"{_terminal_text(str(output_root))} ({action})."
    )
    print(
        "Resources: "
        f"agents={len(snapshot['agents'])}, "
        f"skills={len(snapshot['skills'])}, "
        f"squads={len(snapshot['squads'])}, "
        f"quick_actions={len(snapshot['quick_actions'])}, "
        f"autopilots={len(snapshot['autopilots'])}."
    )
    print(
        "Managed categories: autopilots, agent, quick-actions, skills, squad, "
        "workspace."
    )
    return 0
