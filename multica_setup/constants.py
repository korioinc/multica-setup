"""Shared reconciliation constants."""

TIMEOUT_SECONDS = 180
LEGACY_MANAGED_CATEGORIES = ("autopilots", "agent", "skills", "squad", "workspace")
MANAGED_CATEGORIES = (
    "autopilots",
    "agent",
    "quick-actions",
    "skills",
    "squad",
    "workspace",
)
BINDING_RESOURCE_TYPES_V1 = ("agent", "skill", "squad")
BINDING_RESOURCE_TYPES_V2 = (*BINDING_RESOURCE_TYPES_V1, "quick-action")
BINDING_RESOURCE_TYPES = (
    *BINDING_RESOURCE_TYPES_V2,
    "autopilot",
    "autopilot-trigger",
    "autopilot-project",
)
BINDING_SCHEMA_VERSION = 3
WORKSPACE_FILES = (
    "instructions.md",
    "metadata.json",
    "squad.json",
    "agent.json",
    "skill.json",
)
QUICK_ACTION_SELECTOR_FILE = "quick-action.json"
AUTOPILOT_SELECTOR_FILE = "autopilot.json"
