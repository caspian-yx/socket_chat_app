from __future__ import annotations

import logging
import os
from typing import Any, Dict

from dotenv import load_dotenv

DEFAULT_CONFIG: Dict[str, Any] = {
    "server_host": "172.20.115.57",
    "server_port": 8088

    ,
    "file_port": 9090,
    "heartbeat_interval": 15,
    "reconnect_backoff": 1,
    "max_reconnect_backoff": 30,
    "max_reconnect_retries": 5,
    "request_timeout": 10,
    "log_level": "INFO",
    "debug_mode": False,
    "encryption_key": "",
    "file_chunk_size": 65536,
    "local_db_path": "client_data.db",
    "token_expiry": 3600,
}

CLIENT_CONFIG: Dict[str, Any] = DEFAULT_CONFIG.copy()


class ConfigError(Exception):
    """Raised when configuration values are invalid."""

    pass


def load_config(env_path: str = ".env") -> Dict[str, Any]:
    """Load client configuration from env file/environment variables."""
    if os.path.exists(env_path):
        load_dotenv(env_path)

    for key, default_value in DEFAULT_CONFIG.items():
        env_key = f"CLIENT_{key.upper()}"
        value = os.getenv(env_key, default_value)
        CLIENT_CONFIG[key] = _coerce_type(value, type(default_value))

    _validate_config()
    logging.getLogger().setLevel(CLIENT_CONFIG["log_level"])
    return CLIENT_CONFIG


def _coerce_type(value: Any, target_type: type) -> Any:
    if isinstance(value, target_type):
        return value
    try:
        if target_type is bool:
            return str(value).lower() in ("1", "true", "yes", "on")
        return target_type(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Cannot convert {value} to {target_type}") from exc


def _validate_config() -> None:
    if not (1 <= int(CLIENT_CONFIG["server_port"]) <= 65535):
        raise ConfigError("server_port must be between 1 and 65535")
    if CLIENT_CONFIG["heartbeat_interval"] <= 0:
        raise ConfigError("heartbeat_interval must be positive")
    if CLIENT_CONFIG["token_expiry"] <= 0:
        raise ConfigError("token_expiry must be positive")


def get(key: str, default: Any = None) -> Any:
    return CLIENT_CONFIG.get(key, default)


__all__ = ["CLIENT_CONFIG", "DEFAULT_CONFIG", "ConfigError", "get", "load_config"]
