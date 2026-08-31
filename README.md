# multica-setup

A tool for exporting Multica workspace agents, skills, squads, public Quick Actions, Autopilots, and workspace settings into the repository's `src/` structure, then planning and applying changes to the remote workspace from the local configuration.

## Requirements

- Python 3.10 or later
- The `multica` CLI, installed, authenticated, and available in the current shell
- Owner or admin access to the selected workspace when applying changes to public Quick Actions

There are no additional Python runtime dependencies.

## Installation

Homebrew is the recommended installation method. The fully qualified formula name automatically adds the public tap and installs the required Python and Multica CLI dependencies.

```bash
brew install korioinc/tap/multica-setup
```

After installation, authenticate the Multica CLI before using `multica-setup`.

### Python package

The synchronization implementation is provided as the `multica_setup` Python package. The installed command searches upward from the current directory for `multica-setup.toml`, then uses the configuration repository's `src/` and `.cache/` directories. You can also specify the repository root with `MULTICA_SETUP_ROOT`.

When running a local checkout directly, you can use the existing compatibility entry point.

```bash
bin/multica-setup --help
python3 -m multica_setup --help
```

Build a wheel and install it locally in isolation as follows.

```bash
uv build --wheel
uv tool install .
multica-setup --help
```

`bin/multica-setup` is only a thin wrapper around the package CLI and does not contain the reconciliation implementation.

## Usage

Run the following commands from anywhere inside the repository.

### Export

```bash
# Select a workspace in the terminal
multica-setup export

# For automation or to specify a workspace directly
multica-setup export --workspace <slug-or-uuid>
```

When invoked without arguments, `export` displays the available workspaces as a numbered list and exports only after the user explicitly selects one. It does not automatically select a workspace even when only one is available. The selection UI opens only when both stdin and stderr are TTYs and the `CI` environment variable is not set. If stdin or stderr is piped or redirected, or if the command is running in CI, it exits immediately with code `2` instead of waiting, so `--workspace` is required. Redirecting only the stdout summary does not disable interactive selection.

`--workspace` accepts a workspace slug, a full UUID, or an unambiguous UUID prefix of at least four characters. This path does not query the workspace list and proceeds directly to export as before. The output location is always the repository root's `src/`, not the current working directory.

Entering `q` or sending EOF on the selection screen exits with code `1`; pressing Ctrl-C exits with code `130`. In either case, the local `src/` remains unchanged. The menu and prompt are written to stderr, while the success summary is written to stdout.

This command uses the installed `multica` CLI to query agents, skills, squads, and related resources. For Quick Actions and Autopilots, it calls the official REST API directly using the same Multica login configuration's server URL and token so that the complete assignee/subscriber and schedule contracts are preserved. Webhook triggers are treated as manually managed: they are queried but not exported. If `MULTICA_SERVER_URL` and `MULTICA_TOKEN` are set, they take precedence over the configuration file. On success, the command writes the canonical workspace UUID, output path, resource count by type, and managed-scope boundaries to stdout.

### Plan

```bash
# Select a workspace in the terminal and inspect the local-to-remote diff
multica-setup plan

# For automation or to specify a workspace directly
multica-setup plan --workspace <slug-or-uuid>

# Disable color output (the Terraform-compatible alias is also supported)
multica-setup plan --workspace <slug-or-uuid> --no-color
multica-setup plan --workspace <slug-or-uuid> -no-color
```

`plan` uses the same workspace selection UI and `--workspace` format as `export`. The selection UI opens only when both stdin and stderr are TTYs and the `CI` environment variable is not set. It does not automatically select a workspace even when only one is available. In a non-TTY environment or CI, it exits immediately with code `2` instead of waiting, so `--workspace` is required. A local `src/workspace/<UUID>/` configuration matching the selected remote workspace's canonical UUID must exist. Other workspace directories may coexist. Plan reads only the selected workspace's metadata, instructions, and selectors, and does not touch sibling workspace contents.

Plan is a read-only preview that does not modify local files or remote resources. It does not create a local stage, backup, or temporary state. It exits with code `0` on success, whether or not there are differences, and writes a Terraform-like diff to stdout: green `+` for create, yellow `~` for update/restore, red `-` for archive/delete, followed by color-coded totals. Color is enabled automatically only when stdout is a TTY, `CI` is not set, and `TERM` is not `dumb`. Output is automatically uncolored when piped or redirected, and color can also be disabled with the `NO_COLOR` environment variable or the `--no-color`/`-no-color` option.

### Apply

```bash
# Select a workspace in the terminal, review the plan, and approve with yes
multica-setup apply

# Specify a workspace directly
multica-setup apply --workspace <slug-or-uuid>

# Approve the displayed plan without a separate prompt in automation
multica-setup apply --workspace <slug-or-uuid> --auto-approve
multica-setup apply --workspace <slug-or-uuid> -auto-approve
```

`apply` first displays the complete diff using the same planner. If changes are present, it executes them only after the user enters exactly `yes`; any other input or EOF cancels before remote changes begin. `--auto-approve` skips only this approval step, so the plan is still displayed before execution. Workspace selection and color options behave the same as in `plan`.

If the plan contains any public Quick Action operation, apply first queries the current user's workspace role. It stops before the first remote mutation unless the user is an owner or admin. Applying Autopilot and Quick Action changes is blocked in agent/task execution contexts. The plan stage also checks remote `can_write` permission for changes, archives, and trigger operations on existing Autopilots. Because a permission change immediately after the final preflight cannot be included in a single transaction, earlier upserts may remain if permission is revoked before the server rejects a later write. Recover as with any partial apply: restore permission, then rerun plan and apply.

Immediately after approval, local and remote fingerprints and the complete operation set are recalculated. If they differ from the approved plan, execution stops before the first remote write and a new plan must be reviewed. Operations run in this order: workspace update; skill create/update; agent create/update/restore; squad create/update; Autopilot/Quick Action upserts; schedule trigger upserts; stale schedule, Autopilot, and Quick Action cleanup; then squad and agent archives and skill deletion. Webhook triggers are not included in this sequence and are never created, modified, or deleted. UUIDs of newly created resources are passed to relationship operations within the same execution. Before approval and immediately before each destructive operation, apply checks whether an Autopilot or active public Quick Action is still assigned to an agent or squad targeted for pruning. Target pruning is allowed only after the Autopilot/Quick Action cleanup included in the plan has completed; if a reference reappears immediately before target pruning, execution stops. The Multica API cannot combine this check and archive/delete into one atomic request, so a narrow race remains if an assignment changes immediately after the final check.

Execution stops immediately on the first mutation failure and reports completed, failed, and pending operations. Completed mutations are not rolled back automatically. Resolve the cause, then rerun `plan` and `apply` to recover. After all operations complete, apply runs plan again and succeeds only when the managed diff is empty.

New agents are created with `permission_mode=public_to` and a workspace-wide invocation target so workspace members can invoke them. An existing agent's permissions and fields not represented in the local schema are preserved during update/restore. Values the current Multica CLI cannot represent—`max_concurrent_tasks: null` on a new or changed agent, a null leader role, an empty skill support file, or an empty workspace issue prefix—cause a safe failure before mutation.

## Output structure

```text
src/
├── autopilots/
│   └── <autopilot-title-slug>/
│       ├── metadata.json
│       └── prompt.md
├── agent/
│   └── <agent-name-slug>/
│       ├── instructions.md
│       └── metadata.json
├── quick-actions/
│   └── <quick-action-name-slug>/
│       ├── metadata.json
│       └── prompt.md
├── skills/
│   └── <skill-name-slug>/
│       ├── SKILL.md
│       └── <optional nested reference files>
├── squad/
│   └── <squad-name-slug>/
│       ├── instructions.md
│       └── metadata.json
└── workspace/
    ├── <canonical-workspace-uuid-a>/
    │   └── ...
    └── <canonical-workspace-uuid-b>/
        ├── instructions.md
        ├── metadata.json
        ├── autopilot.json
        ├── quick-action.json
        ├── squad.json
        ├── agent.json
        └── skill.json
```

The `autopilots`, `agent`, `quick-actions`, `skills`, `squad`, and `workspace` directories and the workspace's fixed files are created even when there are no resources.

Remote resource UUIDs are stored only in the per-workspace binding cache, never in shared definition files.

```text
.cache/
└── workspaces/
    └── <canonical-workspace-uuid>/
        └── bindings.json
```

A binding maps `(resource type, local slug) -> remote UUID`. Even when multiple workspaces select the same definition, every UUID—including the UUIDs for each workspace's Autopilots, triggers, and project references—is isolated in a separate cache file. Remote UUIDs are not added to `metadata.json`, `SKILL.md`, or `prompt.md`. Binding versions 1 (agent/skill/squad) and 2 (+Quick Action) remain readable; the next successful export/apply writes version 3 (+Autopilot/trigger/project reference).

A successful export updates bindings with UUIDs observed in the snapshot. A successful apply retains only selected resources that converged during postflight. `plan` reads bindings but never creates or modifies them. When no cache exists, an exactly matching NFC resource name is adopted as the initial binding. A definition that does not exist remotely receives the create response UUID during the next postflight.

Once a binding exists, `name` is a mutable field rather than the identity. Renaming an agent, skill, squad, Quick Action, or Autopilot while keeping its local directory slug performs a rename update against the same remote UUID. A trigger's `key` serves as its local identity in the same way. If a bound UUID does not exist as the same resource type in the selected workspace, the command fails instead of writing to the wrong target. If the local name is changed before an initial binding exists, the previous UUID cannot be identified reliably, so plan may show a create and an archive/delete.

Agent runtime values are stored in `metadata.json` as follows.

```json
{
  "runtime": "macmini-local",
  "provider": "codex",
  "model": null,
  "max_concurrent_tasks": 6
}
```

`runtime` prefers the device name in the final parentheses of the runtime display name; if absent, it uses the custom name or runtime name. `provider` preserves the original value in a separate top-level field, and an empty `model` string is normalized to `null`. If no runtime is connected or the runtime cannot be resolved from the list, both `runtime` and `provider` are `null`.

The `agents` entries in squad `metadata.json` reference agent directories through `agent_slug`.

Only `public` Quick Actions can be managed deterministically in a shared repository. Another user's private actions do not appear in API listings and are therefore never preserved, changed, or archived by export/plan/apply. A definition has the following format, and `assignee_slug` must be the slug of an agent or squad selected by the same workspace selector.

```json
{
  "name": "Review issue",
  "description": "Review the current issue",
  "assignee_type": "agent",
  "assignee_slug": "backend-engineer"
}
```

The execution prompt is stored in `prompt.md` in the same directory. Remote UUID, creator, usage count, last-used time, timestamp, status, and visibility are not stored in the definition. A selected local definition always represents the active/public desired state. An active public action removed from the selector is archived during apply. However, a broken action whose target is already missing or has become private may be rejected by the server when an archive update is attempted; plan marks and deletes such an action as irreversible.

The API exposes only the current user's private Quick Actions and hides other users' actions, so complete workspace-wide dependency checking is impossible. Agent/squad prune safety therefore applies only to public actions. Archiving a target may leave a user's private action in a `target_missing` state, so check with workspace users before changing the target lifecycle.

## Workspace selector format

The workspace's `agent.json`, `squad.json`, `skill.json`, and `quick-action.json` files are JSON arrays that select slugs from the corresponding `src/agent`, `src/squad`, `src/skills`, and `src/quick-actions` directories.

- `[]`: Select no resources of this type
- `["*"]`: Select every definition of this type using the format-level shorthand
- `["slug-a", "slug-b"]`: Select only the listed definitions as an explicit subset

Mixing `"*"` with slugs, duplicate values, empty strings, unknown slugs, and non-string elements is invalid. `"*"` selects every global definition of that type in the repository, not a workspace-specific set, so explicit slug selectors are recommended when managing multiple workspaces. Because the exporter records a point-in-time snapshot, it writes `[]` when there are no resources and otherwise writes only a sorted, deduplicated array of explicit slugs. Export never writes `["*"]`.

The `skills` field in agent `metadata.json` and the `agents` field in squad `metadata.json` are actual relationship lists, not selectors, and therefore do not allow wildcards.

For safe migration from existing snapshots, a workspace without `quick-action.json` treats Quick Actions as `unmanaged`. In this state, plan does not query Quick Actions and apply does not change them. However, if apply includes agent/squad pruning, it queries the list only as a safety check to avoid breaking referenced active public Quick Actions. Exporting the workspace creates the selector and category, enabling public Quick Action management. If `quick-action.json` exists with the value `[]`, it explicitly declares the desired state of archiving every active public Quick Action.

## Scope managed by Plan

In plan and apply, only the local definitions selected by the workspace's `skill.json`, `agent.json`, `squad.json`, `autopilot.json`, and `quick-action.json` selectors are authoritative remote state. `[]` means the desired set for that type is empty, `["*"]` manages every definition in the corresponding local directory, and an explicit subset manages only the listed slugs. Local definitions absent from the selector are ignored.

Remote resources are matched first by the selected workspace's binding UUIDs. Only unbound slugs are initially matched by exact NFC-normalized name. Case conflicts and duplicate names fail safely. Renaming a bound agent, skill, squad, or Quick Action appears as an update to the existing UUID.

Plan compares the following fields:

- Workspace: `name`, `description`, `issue_prefix`, and context from `instructions.md`
- Skill: name, description, `SKILL.md`, and the relative path and contents of every support file
- Agent: name, description, instructions, `(provider, runtime device name)`, model, `max_concurrent_tasks`, and skill relationships
- Squad: name, description, instructions, leader, members, and per-member role
- Autopilot: name, `prompt.md`, agent/squad assignee, `execution_mode`, project, subscriber email set, active/paused state, and schedule trigger settings
- Quick Action: name, description, agent/squad assignee relationship, `prompt.md`, and active state; only public items are compared

An agent runtime resolves only when exactly one remote runtime matches `(provider, runtime device name)`. Existing agent invocation permissions are not managed by plan and are preserved. An archived agent with the same name is treated as a restore candidate and retains its existing permissions. For new agents, the diff explicitly states that apply will create them with `permission_mode=public_to` and `invocation_scope=workspace`.

Operations are ordered as follows: workspace update; skill create/update; agent create/update/restore; squad create/update; Autopilot/Quick Action create/update; schedule trigger create/update. Next, stale schedule triggers are deleted, Autopilots and active public Quick Actions outside the selector's authoritative set are archived, squads and agents are archived, and skills are listed for deletion in reverse dependency order. Webhook triggers are excluded from plan operations. `plan` is preview-only; after approval and drift revalidation, `apply` executes operations in exactly this order.

For safe migration from existing snapshots, a workspace without `autopilot.json` treats Autopilots as `unmanaged`. In this state, plan/apply does not change Autopilots, but remaining assignments are queried as a safety check when pruning agents or squads. Exporting the workspace creates the selector and complete metadata, enabling management. If `autopilot.json` exists with the value `[]`, it explicitly declares the desired state of archiving every active or paused Autopilot.

Diff output has a stable order for identical input. Prompts, subscriber emails, descriptions, instructions, context, `SKILL.md`, support file contents, squad roles, and other sensitive or lengthy text are displayed as digests rather than raw values. If managed projections are identical, no operations are emitted and the command prints `No changes.`. When remote webhooks exist, it reports only how many are preserved as manually managed; all webhook fields are excluded from both the diff and fingerprint.

## Updates and ownership

The exporter manages the following six categories as a single export-managed snapshot.

- `src/autopilots`
- `src/agent`
- `src/quick-actions`
- `src/skills`
- `src/squad`
- `src/workspace`

On the first export, none of the managed categories may exist. An existing five-category snapshot is accepted as migration input, and the first successful export adds `quick-actions`. Thereafter, an existing snapshot is merged only when all six categories are actual directories and every workspace directory under `src/workspace/` has a canonical UUID and the required fixed files. If any category is missing or a workspace configuration is incomplete, the command fails without changing the existing tree. There is no `--force` option.

When multiple workspaces exist, export replaces only the selected `src/workspace/<UUID>/` with the new snapshot and preserves sibling workspace directories. `src/agent`, `src/skills`, `src/squad`, `src/autopilots`, and `src/quick-actions` are updated with the selected workspace's definitions while definitions referenced by sibling selectors are preserved. Before publishing, the merged result revalidates selectors and dependencies for every workspace. Resources with existing bindings retain the slug associated with their remote UUID.

If the selected workspace and a sibling share the same slug, the selected export's definition becomes the shared desired state, and the command warns which sibling UUIDs are affected. Publishing fails if a sibling's `"*"` selector would unintentionally select a new global definition, a new unbound slug conflicts with a sibling-only definition, or the merged result breaks a sibling dependency.

Re-exporting the same workspace succeeds as a no-op when the snapshot is identical. When the snapshot differs, all six categories are replaced by the new result. Stale paths—including resources deleted from Multica and skill reference files—are therefore removed, and manual edits inside the six categories are overwritten or removed by the next successful export.

Other top-level files and directories under `src/` are unmanaged and preserved on both success and recoverable failure. Place any manual files that must be preserved outside the six managed categories.

## Autopilots and data review

An Autopilot prompt is stored in `prompt.md`, and all other managed fields are stored in `metadata.json`. `execution_mode` is either `create_issue` or `run_only`. Projects are stored as portable key strings produced by export, subscribers as email addresses, and assignees as local slugs; actual UUIDs are managed only in the workspace cache. A project key alone cannot safely resolve a UUID in another workspace, so plan/apply fails without that project binding. Export the selected workspace first to create the binding. The legacy `{ "key": ..., "name": ... }` project format is still accepted as migration input, but `name` is ignored and subsequent exports write only the string. The legacy `issue_title_template` metadata field is read for migration compatibility but ignored; its remote value is neither compared nor changed. A schedule trigger's `key` is also its local identity, while cron, timezone, enabled state, and label are managed fields.

Webhook triggers are completely manually managed. Export does not record webhook entries or UUIDs in source/bindings and only emits a warning. Plan/apply does not compare or change any webhook field, including `enabled`, `label`, `provider`, `event_filters`, URL/path/token, or signing secret. Local webhook entries written by older versions are ignored for compatibility, and a successful managed export/apply removes only the old webhook bindings. Removing an Autopilot from the selector and archiving it also stops its associated webhook execution, but does not directly delete the trigger row.

```json
{
  "name": "Daily review",
  "assignee_type": "agent",
  "assignee_slug": "reviewer",
  "execution_mode": "create_issue",
  "project": "example-project",
  "subscribers": ["owner@example.com"],
  "status": "active",
  "triggers": [
    {
      "key": "weekday-morning",
      "kind": "schedule",
      "enabled": true,
      "label": "Weekday morning",
      "cron_expression": "30 8 * * 1-5",
      "timezone": "Asia/Seoul"
    }
  ]
}
```

Agent and squad instructions, workspace context, descriptions, skill bodies and reference files, and Quick Action prompts are stored as raw text. This text may contain secrets or sensitive information, so always review the generated `src/` before storing or committing it.

## Exit codes and scope

- `0`: Successful export (including an identical-snapshot no-op), successful plan (with either a diff or `No changes.`), or successful postflight convergence after apply
- `2`: Command usage error, such as a missing `--workspace` in non-interactive/CI mode or a blank value
- `1`: Workspace/apply selection canceled; `multica` CLI, timeout, JSON/response format, local schema/reference integrity, workspace mismatch, apply drift/partial/postflight, or filesystem/snapshot ownership error
- `130`: User interrupt

If only cleanup of a temporary backup or empty stage fails after all six managed categories have been installed, the fully applied new snapshot is not rolled back. This post-commit cleanup residual retains exit code `0` and writes the remaining path and a manual-cleanup warning to stderr.

`export` and `plan` never create, modify, or delete remote resources. Only `apply` writes the displayed managed operations to the remote workspace after explicit approval.
