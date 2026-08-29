"""Cog di amministrazione: comandi operativi, non legati all'ingestion in
tempo reale — per ora solo il backfill una tantum di ``members``.
"""

from __future__ import annotations

import logging

from discord.ext import commands

from .. import db

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="backfill_members")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def backfill_members(self, ctx: commands.Context) -> None:
        """Popola/aggiorna ``members`` con tutti i membri attuali del server.

        Da lanciare una tantum ogni volta che Kindling viene aggiunto a un
        nuovo server Discord (vedi CLAUDE.md, "Gap noto: tabella members"):
        senza questo passaggio, il joined_at dei membri già presenti al
        momento dell'aggiunta del bot non viene mai osservato via evento, e
        se qualcuno di loro esce prima del backfill quel joined_at è perso
        per sempre. Rilanciabile in sicurezza: aggiorna solo, non duplica
        (vedi db.upsert_member_join).
        """
        guild = ctx.guild
        assert guild is not None  # garantito da @commands.guild_only()

        await ctx.send(f"Backfill membri avviato per **{guild.name}**...")

        seen = 0
        skipped = 0
        # fetch_members chiama l'API Discord direttamente (non la cache):
        # richiede l'intent privilegiato Server Members, già abilitato in
        # bot/client.py e nel Developer Portal (vedi README).
        async for member in guild.fetch_members(limit=None):
            if member.bot or member.joined_at is None:
                skipped += 1
                continue
            await db.upsert_member_join(
                guild_id=guild.id,
                author_id=member.id,
                joined_at=member.joined_at,
            )
            seen += 1

        await ctx.send(f"Backfill completato: {seen} membri aggiornati, {skipped} ignorati.")
        logger.info(
            "Backfill members completato per guild_id=%s: %d membri, %d ignorati",
            guild.id,
            seen,
            skipped,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
