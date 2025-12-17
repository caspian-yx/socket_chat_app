"""Protocol-wide constants shared by client and server."""

DEFAULT_VERSION = "1.0"
ENCODING = "utf-8"
FRAME_DELIMITER = b"\n"
DEFAULT_HEARTBEAT_INTERVAL = 15  # seconds
MAX_PAYLOAD_SIZE = 256 * 1024  # 256 KB upper bound for control channel

__all__ = [
    "DEFAULT_VERSION",
    "ENCODING",
    "FRAME_DELIMITER",
    "DEFAULT_HEARTBEAT_INTERVAL",
    "MAX_PAYLOAD_SIZE",
]
