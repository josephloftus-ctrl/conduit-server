"""Config read/write/reload helpers for the Settings API."""

import logging
import os
from pathlib import Path

import yaml

from . import config

log = logging.getLogger("conduit.settings")

CONFIG_PATH = config.SERVER_DIR / "config.yaml"
ENV_PATH = config.SERVER_DIR / ".env"


def get_config() -> dict:
    """Read current config.yaml as dict."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(data: dict):
    """Write config dict back to config.yaml and reload."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    config.reload()
    log.info("Config saved and reloaded")


def get_env_vars() -> dict:
    """Read .env file as dict."""
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def set_env_var(key: str, value: str):
    """Update a single env var in .env file."""
    lines = []
    found = False

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                env_key = stripped.split("=", 1)[0].strip()
                if env_key == key:
                    lines.append(f"{key}={value}")
                    found = True
                    continue
            lines.append(line)

    if not found:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.environ[key] = value
    log.info("Env var %s updated", key)


def mask_key(key: str) -> str:
    """Mask an API key for display — show last 4 chars."""
    if not key or len(key) < 8:
        return "***" if key else ""
    return f"***{key[-4:]}"


def sanitize_provider(name: str, prov_cfg: dict) -> dict:
    """Return provider config with masked API key."""
    result = dict(prov_cfg)
    # Resolve and mask the actual key
    api_key = config.get_api_key(name)
    result["api_key_masked"] = mask_key(api_key)
    result["has_key"] = bool(api_key)
    return result


def get_full_settings() -> dict:
    """Return full settings for the API, with keys masked."""
    raw = get_config()

    # Mask provider API keys
    providers = raw.get("models", {}).get("providers", {})
    sanitized_providers = {}
    for name, pcfg in providers.items():
        sanitized_providers[name] = sanitize_provider(name, pcfg)

    return {
        "server": raw.get("server", {}),
        "personality": raw.get("personality", {}),
        "providers": sanitized_providers,
        "routing": raw.get("models", {}).get("routing", {}),
        "classifier": raw.get("classifier", {}),
        "memory": raw.get("memory", {}),
        "scheduler": raw.get("scheduler", {}),
        "tools": raw.get("tools", {}),
        "ntfy": {
            "server": config.NTFY_SERVER,
            "topic": config.NTFY_TOPIC,
            "has_token": bool(config.NTFY_TOKEN),
            "enabled": config.NTFY_ENABLED,
        },
    }
