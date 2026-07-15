"""Read non-secret runtime settings without requiring another dependency."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def runtime_value(name: str) -> str | None:
    """Read a value from the process, project .env, or user ~/.env."""
    if value := os.environ.get(name, "").strip():
        return value

    for path in (ROOT / ".env", Path.home() / ".env"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            key, separator, value = stripped.partition("=")
            if separator and key.strip() == name:
                return value.strip().strip("'\"") or None
    return None
