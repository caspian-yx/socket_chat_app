from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Settings:
    """Shared baseline settings (both client/server build on top)."""

    server_host: str = "127.0.0.1"
    server_port: int = 8080
    file_port: int = 9090
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    secret_key: str = "dev-secret"


SETTINGS = Settings()


def load_settings(env_path: str = ".env") -> Settings:
    """Load shared settings from env/.env."""
    if Path(env_path).exists():
        load_dotenv(env_path)
    SETTINGS.server_host = os.getenv("SOCKET_SERVER_HOST", SETTINGS.server_host)
    SETTINGS.server_port = int(os.getenv("SOCKET_SERVER_PORT", SETTINGS.server_port))
    SETTINGS.file_port = int(os.getenv("SOCKET_FILE_PORT", SETTINGS.file_port))
    SETTINGS.log_level = os.getenv("SOCKET_LOG_LEVEL", SETTINGS.log_level)
    SETTINGS.secret_key = os.getenv("SOCKET_SECRET_KEY", SETTINGS.secret_key)
    data_dir = os.getenv("SOCKET_DATA_DIR")
    if data_dir:
        SETTINGS.data_dir = Path(data_dir)
    SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
    return SETTINGS


__all__ = ["Settings", "SETTINGS", "load_settings"]
