"""Resolution of the repository that owns desired state and workspace bindings."""

from __future__ import annotations

from pathlib import Path


def current_repository_root() -> Path:
    """Use the exact current working directory as the configuration repository."""
    return Path.cwd().resolve()
