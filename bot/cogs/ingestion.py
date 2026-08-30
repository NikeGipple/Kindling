"""Cog di ingestion: cattura gli eventi Discord grezzi e li scrive su
raw_events, senza interpretarli.

Copre il set di eventi elencato in docs/architettura/stack-tecnologico-mvp.md:
messaggi, reply, reazioni, thread, voice join/leave, eventi/RSVP, membri
(join/remove). Aggiungere
un nuovo tipo di evento significa aggiungere un listener qui e un valore in
``bot/event_types.py`` — non richiede modifiche allo schema di raw_events.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .. import db, event_types

logger = logging.getLogger(__name__)


class IngestionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---- Messaggi e reply --------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        is_reply = message.reference is not None
        await db.insert_raw_event(
            guild_id=message.guild.id,
            event_type=event_types.MESSAGE_REPLY if is_reply else event_types.MESSAGE_CREATE,
            author_id=message.author.id,
            channel_id=message.channel.id,
            message_id=message.id,
            referenced_message_id=message.reference.message_id if is_reply else None,
            occurred_at=message.created_at,
            payload={
                # Deliberatamente NON il testo del messaggio: per l'MVP alle
                # metriche SNA serve la struttura dell'interazione (chi
                # scrive/risponde a chi, quando), non il contenuto. Se in
                # futuro servisse il testo, va valutato insieme alla privacy
                # policy e alla colonna forgotten_at.
                "content_length": len(message.content),
                "has_attachments": bool(message.attachments),
                "mention_ids": [m.id for m in message.mentions],
                "channel_name": getattr(message.channel, "name", None),
            },
        )

    # ---- Reazioni -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, event_types.REACTION_ADD)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, event_types.REACTION_REMOVE)

    async def _handle_reaction(
        self, payload: discord.RawReactionActionEvent, event_type: str
    ) -> None:
        if payload.guild_id is None:
            return
        if payload.member is not None and payload.member.bot:
            return

        emoji = str(payload.emoji)
        await db.insert_raw_event(
            guild_id=payload.guild_id,
            event_type=event_type,
            author_id=payload.user_id,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
            dedup_key=f"{event_type}:{payload.message_id}:{payload.user_id}:{emoji}",
            payload={"emoji": emoji},
        )

    # ---- Thread ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        await db.insert_raw_event(
            guild_id=thread.guild.id,
            event_type=event_types.THREAD_CREATE,
            author_id=thread.owner_id,
            channel_id=thread.parent_id,
            message_id=thread.id,
            occurred_at=thread.created_at,
            payload={"name": thread.name, "parent_channel_id": thread.parent_id},
        )

    # ---- Voice ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        if before.channel is None and after.channel is not None:
            await db.insert_raw_event(
                guild_id=member.guild.id,
                event_type=event_types.VOICE_JOIN,
                author_id=member.id,
                channel_id=after.channel.id,
                payload={"channel_name": after.channel.name},
            )
        elif before.channel is not None and after.channel is None:
            await db.insert_raw_event(
                guild_id=member.guild.id,
                event_type=event_types.VOICE_LEAVE,
                author_id=member.id,
                channel_id=before.channel.id,
                payload={"channel_name": before.channel.name},
            )
        # TODO: spostamento tra canali voice (before.channel e after.channel
        # entrambi valorizzati ma diversi). Per ora non modellato: decidere
        # se trattarlo come leave+join o come evento dedicato "voice_move"
        # quando servirà alle metriche di presenza.

    # ---- Eventi / RSVP -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(
        self, event: discord.ScheduledEvent, user: discord.User
    ) -> None:
        await db.insert_raw_event(
            guild_id=event.guild_id,
            event_type=event_types.EVENT_RSVP_ADD,
            author_id=user.id,
            message_id=event.id,
            payload={"event_name": event.name},
        )

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(
        self, event: discord.ScheduledEvent, user: discord.User
    ) -> None:
        await db.insert_raw_event(
            guild_id=event.guild_id,
            event_type=event_types.EVENT_RSVP_REMOVE,
            author_id=user.id,
            message_id=event.id,
            payload={"event_name": event.name},
        )

    # ---- Membri -------------------------------------------------------------
    #
    # Oltre a raw_events (fonte di verità, coerente col resto del bot),
    # aggiornano anche members: quella tabella non è un log ma uno stato
    # corrente (joined_at/left_at), serve alle metriche di retention. Vedi
    # CLAUDE.md, sezione "Gap noto: tabella members".

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        await db.insert_raw_event(
            guild_id=member.guild.id,
            event_type=event_types.MEMBER_JOIN,
            author_id=member.id,
            occurred_at=member.joined_at,
            payload={},
        )
        await db.upsert_member_join(
            guild_id=member.guild.id,
            author_id=member.id,
            joined_at=member.joined_at,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        await db.insert_raw_event(
            guild_id=member.guild.id,
            event_type=event_types.MEMBER_REMOVE,
            author_id=member.id,
            payload={},
        )
        await db.mark_member_left(guild_id=member.guild.id, author_id=member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IngestionCog(bot))
