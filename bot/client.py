"""Classe del bot Discord: solo cablaggio (intents, pool DB, cog). Nessuna
logica di business qui — quella vive nei cog di ingestion."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from . import db
from .config import Settings

logger = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    # Privilegiati: vanno abilitati anche nel Discord Developer Portal
    # (Bot > Privileged Gateway Intents) prima che il bot possa riceverli.
    intents.message_content = True
    intents.members = True
    intents.voice_states = True
    return intents


class KindlingBot(commands.Bot):
    """Bot di ingestion: cattura eventi grezzi e li scrive su raw_events."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(command_prefix="!", intents=build_intents())
        self.settings = settings

    async def setup_hook(self) -> None:
        await db.init_pool(self.settings.database_url)
        await self.load_extension("bot.cogs.ingestion")
        await self.load_extension("bot.cogs.admin")
        logger.info("Cog di ingestion e amministrazione caricati")

    async def on_ready(self) -> None:
        logger.info("Connesso come %s (guild collegate: %d)", self.user, len(self.guilds))

    async def close(self) -> None:
        await db.close_pool()
        await super().close()
