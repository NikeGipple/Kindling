"""Configurazione del bot, letta da variabili d'ambiente (.env in locale)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_token: str
    database_url: str
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Legge e valida la configurazione. Solleva RuntimeError se manca qualcosa."""
    token = os.environ.get("DISCORD_TOKEN")
    database_url = os.environ.get("DATABASE_URL")

    missing = [
        name
        for name, value in (("DISCORD_TOKEN", token), ("DATABASE_URL", database_url))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Variabili d'ambiente mancanti: {', '.join(missing)}. Vedi .env.example."
        )

    return Settings(
        discord_token=token,  # type: ignore[arg-type]
        database_url=database_url,  # type: ignore[arg-type]
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
