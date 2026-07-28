"""Durable user configuration for CLI and plugin-launched runtimes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CONFIG_VERSION = 1
DEFAULT_SETTINGS = {
    "OLLAMA_URL": "http://localhost:11434",
    "LITELLM_URL": "",
    "TLDREADME_EMBED_MODEL": "ollama/nomic-embed-text",
    "TLDREADME_CHAT_MODEL": "ollama/qwen2.5-coder:3b-instruct",
    "QDRANT_URL": "http://localhost:6333",
    "FALKORDB_URL": "redis://localhost:6379",
    "TLDREADME_TOOL_PROFILE": "router",
}
PROVIDER_MODEL_DEFAULTS = {
    "ollama": {
        "TLDREADME_EMBED_MODEL": "ollama/nomic-embed-text",
        "TLDREADME_CHAT_MODEL": "ollama/qwen2.5-coder:3b-instruct",
    },
    "litellm": {
        "TLDREADME_EMBED_MODEL": "embed",
        "TLDREADME_CHAT_MODEL": "chat",
    },
}
SUBSCRIPTION_TARGETS = ("codex", "claude", "gemini")


def _validate_endpoint(name: str, value: str, schemes: set[str]) -> str:
    """Return a normalized endpoint or raise a useful configuration error."""

    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in schemes or not parsed.hostname:
        expected = ", ".join(sorted(schemes))
        raise ValueError(f"{name} must be a valid {expected} URL")
    return normalized


def config_path() -> Path:
    """Return the per-user configuration path, with a test/automation override."""

    override = os.getenv("TLDREADME_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    xdg_root = os.getenv("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".config"
    return base / "tldreadme" / "config.json"


def load_config(path: Path | None = None) -> dict[str, object]:
    """Read the durable configuration, returning an empty dict when absent."""

    target = path or config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def get_setting(name: str, default: str | None = None) -> str:
    """Resolve an environment override, durable setting, then built-in default."""

    if name in os.environ:
        return os.environ[name]
    settings = load_config().get("settings", {})
    if isinstance(settings, dict) and name in settings:
        return str(settings[name])
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(name, "")


def configuration_status(
    path: Path | None = None,
    *,
    include_environment: bool = True,
) -> dict[str, object]:
    """Return the plugin-safe setup state without probing external services."""

    target = path or config_path()
    payload = load_config(target)
    provider = str(payload.get("provider", "")).strip().lower()
    explicit_override = (
        os.getenv("TLDREADME_SETUP_COMPLETE", "").strip().lower() if include_environment else ""
    )
    environment_ready = explicit_override in {"1", "true", "yes", "on"}
    if include_environment and not environment_ready:
        environment_ready = bool(
            os.getenv("LITELLM_URL", "").strip()
            or os.getenv("TLDREADME_CHAT_MODEL", "").strip()
            or os.getenv("TLDREADME_EMBED_MODEL", "").strip()
        )

    configured = environment_ready or (
        payload.get("version") == CONFIG_VERSION
        and provider in PROVIDER_MODEL_DEFAULTS
        and isinstance(payload.get("settings"), dict)
    )
    if configured:
        reason = (
            "Configuration supplied by environment variables."
            if environment_ready and not payload
            else f"Configuration completed for provider `{provider}`."
        )
    else:
        reason = "Must run Configuration - Setup first."

    return {
        "configured": configured,
        "status": "ready" if configured else "setup_required",
        "provider": provider or None,
        "path": str(target),
        "reason": reason,
        "setup_command": "tldr setup",
        "check_command": "tldr setup --check",
        "inference_policy": (
            payload.get("inference_policy", {})
            if isinstance(payload.get("inference_policy"), dict)
            else {}
        ),
    }


def write_configuration(
    *,
    provider: str,
    ollama_url: str | None = None,
    litellm_url: str | None = None,
    embed_model: str | None = None,
    chat_model: str | None = None,
    qdrant_url: str | None = None,
    falkordb_url: str | None = None,
    allow_cloud_non_code: bool = False,
    cloud_subscriptions: tuple[str, ...] | list[str] = (),
    allow_cloud_code: bool = False,
    acknowledge_cloud_warning: bool = False,
    tool_profile: str = "router",
    path: Path | None = None,
) -> dict[str, object]:
    """Persist a validated provider selection without storing provider secrets."""

    provider = provider.strip().lower()
    if provider not in PROVIDER_MODEL_DEFAULTS:
        raise ValueError("provider must be `ollama` or `litellm`")
    if provider == "litellm" and not (litellm_url or "").strip():
        raise ValueError("LiteLLM setup requires --litellm-url")
    tool_profile = tool_profile.strip().lower()
    if tool_profile not in {"router", "full"}:
        raise ValueError("tool profile must be `router` or `full`")
    subscriptions = sorted(
        {str(item).strip().lower() for item in cloud_subscriptions if str(item).strip()}
    )
    unknown_subscriptions = [item for item in subscriptions if item not in SUBSCRIPTION_TARGETS]
    if unknown_subscriptions:
        raise ValueError("cloud subscriptions must be selected from codex, claude, or gemini")
    if subscriptions and not (allow_cloud_non_code or allow_cloud_code):
        raise ValueError("subscription targets require an expanded cloud inference permission")
    if (allow_cloud_non_code or allow_cloud_code) and not subscriptions:
        raise ValueError("expanded cloud inference requires at least one subscription target")
    if (allow_cloud_non_code or allow_cloud_code) and not acknowledge_cloud_warning:
        raise ValueError(
            "cloud inference requires explicit acknowledgement of the non-local processing warning"
        )

    model_defaults = PROVIDER_MODEL_DEFAULTS[provider]
    settings = {
        "OLLAMA_URL": _validate_endpoint(
            "OLLAMA_URL",
            ollama_url or DEFAULT_SETTINGS["OLLAMA_URL"],
            {"http", "https"},
        ),
        "LITELLM_URL": (
            _validate_endpoint(
                "LITELLM_URL",
                litellm_url or "",
                {"http", "https"},
            )
            if provider == "litellm"
            else ""
        ),
        "TLDREADME_EMBED_MODEL": (embed_model or model_defaults["TLDREADME_EMBED_MODEL"]).strip(),
        "TLDREADME_CHAT_MODEL": (chat_model or model_defaults["TLDREADME_CHAT_MODEL"]).strip(),
        "QDRANT_URL": _validate_endpoint(
            "QDRANT_URL",
            qdrant_url or DEFAULT_SETTINGS["QDRANT_URL"],
            {"http", "https"},
        ),
        "FALKORDB_URL": _validate_endpoint(
            "FALKORDB_URL",
            falkordb_url or DEFAULT_SETTINGS["FALKORDB_URL"],
            {"redis", "rediss"},
        ),
        "TLDREADME_TOOL_PROFILE": tool_profile,
    }
    target = path or config_path()
    payload = {
        "version": CONFIG_VERSION,
        "provider": provider,
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "inference_policy": {
            "allow_cloud_non_code": allow_cloud_non_code,
            "cloud_subscriptions": subscriptions,
            "allow_cloud_code": allow_cloud_code,
            "warning_acknowledged": acknowledge_cloud_warning,
            "credentials_stored": False,
            "provider_access_granted": False,
            "purpose": "host_expanded_inference_preferences",
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return {
        **configuration_status(target),
        "settings": settings,
    }
