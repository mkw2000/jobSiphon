"""Runtime selection for whichever Ollama model is installed locally."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable


OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"


def configured_model() -> str | None:
    """Return the optional explicit model override."""
    return os.environ.get("OLLAMA_MODEL", "").strip() or None


def select_model(models: Iterable[str]) -> str | None:
    """Select an override when present, otherwise the first installed model."""
    installed = tuple(name.strip() for name in models if name.strip())
    requested = configured_model()
    if requested:
        requested_base = requested.removesuffix(":latest")
        for name in installed:
            if name == requested or name.removesuffix(":latest") == requested_base:
                return name
        return requested
    return installed[0] if installed else None


def installed_models(timeout: float = 1.0) -> tuple[str, ...] | None:
    """Return installed model names, or ``None`` when Ollama is offline."""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return tuple(
            str(model.get("name", "")).strip()
            for model in payload.get("models", [])
            if str(model.get("name", "")).strip()
        )
    except (OSError, ValueError, urllib.error.URLError):
        return None
