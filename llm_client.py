"""Model-provider abstraction for JobSiphon scoring and dashboard health."""

from __future__ import annotations

from typing import Any

import requests

from ollama_config import installed_models, select_model
from runtime_config import runtime_value


def provider_name() -> str:
    value = (runtime_value("LLM_PROVIDER") or "freellmapi").strip().lower()
    aliases = {"free": "freellmapi", "freellm": "freellmapi"}
    value = aliases.get(value, value)
    if value not in {"freellmapi", "ollama"}:
        raise ValueError("LLM_PROVIDER must be 'freellmapi' or 'ollama'")
    return value


def freellm_base_url() -> str:
    return (runtime_value("FREELLMAPI_BASE_URL") or "http://localhost:3001/v1").rstrip(
        "/"
    )


def configured_model() -> str:
    if provider_name() == "freellmapi":
        return (runtime_value("FREELLMAPI_MODEL") or "auto").strip()
    return select_model(installed_models(timeout=2.0) or ()) or (
        runtime_value("OLLAMA_MODEL") or ""
    )


def provider_status(timeout: float = 1.5) -> dict[str, Any]:
    provider = provider_name()
    if provider == "ollama":
        models = installed_models(timeout=timeout)
        selected = select_model(models or ())
        return {
            "provider": "ollama",
            "label": "Ollama",
            "configured": selected is not None,
            "online": models is not None,
            "model": selected or runtime_value("OLLAMA_MODEL") or "No model",
            "models": list(models or ()),
        }

    key = runtime_value("FREELLMAPI_UNIFIED_API_KEY")
    status = {
        "provider": "freellmapi",
        "label": "FreeLLM API",
        "configured": bool(key),
        "online": False,
        "model": configured_model(),
        "models": [],
    }
    if not key:
        return status
    try:
        response = requests.get(
            f"{freellm_base_url()}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        status["models"] = [
            str(item.get("id", ""))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        status["online"] = True
    except (requests.RequestException, ValueError, TypeError):
        pass
    return status


def chat_json(
    messages: list[dict[str, str]], schema: dict[str, Any]
) -> dict[str, Any]:
    """Return one structured chat response using the configured provider."""
    if provider_name() == "ollama":
        import ollama

        model = configured_model()
        if not model:
            raise RuntimeError(
                "No Ollama model is installed. Set LLM_PROVIDER=freellmapi or "
                "install/configure an Ollama model."
            )
        response = ollama.chat(
            model=model,
            options={"temperature": 0, "num_ctx": 8192},
            messages=messages,
            format=schema,
        )
        content = response["message"]["content"]
    else:
        key = runtime_value("FREELLMAPI_UNIFIED_API_KEY")
        if not key:
            raise RuntimeError(
                "FREELLMAPI_UNIFIED_API_KEY is missing from .env or ~/.env"
            )
        response = requests.post(
            f"{freellm_base_url()}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": configured_model(),
                "messages": messages,
                "temperature": 0,
                "max_tokens": 350,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "job_fit_score",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]

    if isinstance(content, dict):
        return content
    import json

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"Model returned invalid JSON: {content[:200]}")
        return json.loads(content[start : end + 1])
