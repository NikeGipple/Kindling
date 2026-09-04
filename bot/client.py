"""Classe del bot Discord: solo cablaggio (intents, pool DB, cog). Nessuna
logica di business qui — quella vive nei cog di ingestion."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from . import backfill, db
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
        logger.info("Cog di ingestion caricato")

    async def on_ready(self) -> None:
        logger.info("Connesso come %s (guild collegate: %d)", self.user, len(self.guilds))
        # All'avvio si guarda lo STATO, non ci si fida di aver visto gli eventi:
        # una guild aggiunta mentre il bot era spento non produrrebbe mai un
        # on_guild_join, perche' il flusso OAuth funziona anche a bot spento e
        # Discord non ritrasmette gli eventi del gateway. E' lo stesso pattern
        # della riconciliazione vocale nel cog di ingestion.
        await backfill.reconcile_guilds(self)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Kindling e' stato aggiunto a un server mentre era connesso."""
        logger.info("Aggiunto alla guild %s (guild_id=%s)", guild.name, guild.id)
        await backfill.ensure_backfilled(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Kindling e' stato rimosso da un server.

        La data viene registrata perche' una rimozione seguita da una
        riaggiunta lascia un buco di osservazione, e senza questo dato non
        resterebbe traccia di quando e' cominciato.
        """
        logger.info("Rimosso dalla guild %s (guild_id=%s)", guild.name, guild.id)
        await db.mark_guild_left(guild_id=guild.id)

    async def close(self) -> None:
        await db.close_pool()
        await super().close()
