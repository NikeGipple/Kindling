"""Entrypoint del bot Kindling.

Uso:
    python -m bot.main
"""

from __future__ import annotations

import logging

from .client import KindlingBot
from .config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = KindlingBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
