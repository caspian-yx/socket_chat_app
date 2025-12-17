from __future__ import annotations

import os
from typing import Any, Dict

DEFAULT_SERVER_CONFIG: Dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 8088,
    "file_port": 9090,
    "max_connections": 200,
    "log_level": "INFO",
    "db_path": "data/server.db",
    "session_timeout": 30,
    "presence_scan_interval": 5,
}

SERVER_CONFIG = DEFAULT_SERVER_CONFIG.copy()


def load_server_config() -> Dict[str, Any]:
    SERVER_CONFIG["host"] = os.getenv("SERVER_HOST", SERVER_CONFIG["host"])
    SERVER_CONFIG["port"] = int(os.getenv("SERVER_PORT", SERVER_CONFIG["port"]))
    SERVER_CONFIG["file_port"] = int(os.getenv("SERVER_FILE_PORT", SERVER_CONFIG["file_port"]))
    SERVER_CONFIG["log_level"] = os.getenv("SERVER_LOG_LEVEL", SERVER_CONFIG["log_level"])
    SERVER_CONFIG["db_path"] = os.getenv("SERVER_DB_PATH", SERVER_CONFIG["db_path"])
    SERVER_CONFIG["session_timeout"] = int(os.getenv("SERVER_SESSION_TIMEOUT", SERVER_CONFIG["session_timeout"]))
    SERVER_CONFIG["presence_scan_interval"] = int(
        os.getenv("SERVER_PRESENCE_SCAN_INTERVAL", SERVER_CONFIG["presence_scan_interval"])
    )
    return SERVER_CONFIG


__all__ = ["SERVER_CONFIG", "load_server_config"]
