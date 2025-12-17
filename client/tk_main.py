from __future__ import annotations

from client.config import load_config
from client.ui import run_tk_app


def main() -> None:
    load_config()
    run_tk_app()


if __name__ == "__main__":
    main()
