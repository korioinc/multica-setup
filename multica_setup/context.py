"""Discovery of the repository that owns desired state and workspace bindings."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ExportError

REPOSITORY_MARKER = "multica-setup.toml"
ROOT_ENVIRONMENT_VARIABLE = "MULTICA_SETUP_ROOT"


def discover_repository_root(start: Path | None = None) -> Path:
    """Resolve a configuration repository independently from the package location."""
    override = os.environ.get(ROOT_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise ExportError(
                f"{ROOT_ENVIRONMENT_VARIABLE} must reference an existing directory"
            )
        return root

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / REPOSITORY_MARKER).is_file():
            return candidate
        if (candidate / "bin" / "multica-setup").is_file():
            return candidate
    raise ExportError(
        f"could not find {REPOSITORY_MARKER}; run inside a multica-setup repository "
        f"or set {ROOT_ENVIRONMENT_VARIABLE}"
    )
