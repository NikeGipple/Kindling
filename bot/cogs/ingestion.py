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
from datetime import datetime, timezone
from typing import Optional

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
            # Il canale del messaggio a cui si risponde non coincide sempre
            # con quello della reply (Discord permette reply cross-canale):
            # senza questa colonna il recupero via API del messaggio target
            # cercherebbe l'id nel canale sbagliato. message.reference lo
            # espone gia', non serve una chiamata API in piu'.
            referenced_channel_id=message.reference.channel_id if is_reply else None,
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

        if before.channel == after.channel:
            # Mute, deafen, avvio di uno streaming: cambia lo stato del
            # membro, non la sua presenza in canale. La co-presenza vocale,
            # unica cosa che il grafo ricostruisce da qui, non e' toccata.
            return

        # Uno spostamento tra canali e' "a tutti gli effetti un leave dal
        # canale precedente e un join nel nuovo" (modello-grafo.md 3.1): non
        # emetterlo lasciava chi si sposta aperto per sempre nel canale
        # sbagliato. I due eventi condividono lo stesso timestamp, calcolato
        # una volta sola qui: se differissero, la ricostruzione degli
        # intervalli vedrebbe tra i due canali un buco (o una
        # sovrapposizione) che nella realta' non esiste.
        occurred_at = datetime.now(timezone.utc)

        if before.channel is not None:
            await self._emit_voice_event(
                guild_id=member.guild.id,
                author_id=member.id,
                channel_id=before.channel.id,
                channel_name=before.channel.name,
                event_type=event_types.VOICE_LEAVE,
                occurred_at=occurred_at,
            )
        if after.channel is not None:
            await self._emit_voice_event(
                guild_id=member.guild.id,
                author_id=member.id,
                channel_id=after.channel.id,
                channel_name=after.channel.name,
                event_type=event_types.VOICE_JOIN,
                occurred_at=occurred_at,
            )

    async def _emit_voice_event(
        self,
        *,
        guild_id: int,
        author_id: int,
        channel_id: int,
        channel_name: Optional[str],
        event_type: str,
        occurred_at: datetime,
        reconstruction_reason: Optional[str] = None,
    ) -> None:
        """Scrive un voice_join/voice_leave.

        Prende id e non oggetti discord.py perche' la riconciliazione
        all'avvio deve poter chiudere la sessione di un membro che nel
        frattempo ha lasciato il server, o in un canale che nel frattempo e'
        stato cancellato: in quei casi un oggetto ``Member``/``VoiceChannel``
        non esiste piu', ma gli id letti dal DB si.
        """
        payload: dict[str, object] = {"channel_name": channel_name}
        dedup_key = None
        if reconstruction_reason is not None:
            # Marcato come ricostruito, non osservato: il job di calcolo legge
            # questa chiave per marcare gli archi risultanti come
            # is_reconciled ed escluderli dalle verifiche di sensibilita'
            # (modello-grafo.md 4.3).
            payload[event_types.RECONSTRUCTED_KEY] = True
            payload[event_types.RECONSTRUCTION_REASON_KEY] = reconstruction_reason
            dedup_key = (
                f"{event_type}:reconstructed:{guild_id}:{author_id}:"
                f"{channel_id}:{occurred_at.isoformat()}"
            )

        await db.insert_raw_event(
            guild_id=guild_id,
            event_type=event_type,
            author_id=author_id,
            channel_id=channel_id,
            occurred_at=occurred_at,
            dedup_key=dedup_key,
            payload=payload,
        )

    # ---- Riconciliazione all'avvio (modello-grafo.md 4.4) -------------------
    #
    # Senza sapere quando il bot era spento non si distingue "e' ancora in
    # canale" da "abbiamo perso il leave": e' il prerequisito della
    # riconciliazione delle sessioni orfane. E' l'equivalente vocale di
    # !backfill_members, ma automatico, perche' un downtime non e' un evento
    # che qualcuno si ricorda di annunciare.
    #
    # on_ready si ripete a ogni riconnessione al gateway, non solo al primo
    # avvio del processo: va bene, perche' anche una riconnessione lunga
    # apre un buco. La seconda esecuzione di fila e' naturalmente un no-op
    # (nessuna sessione risulta disallineata), quindi non serve un flag.

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._reconcile_voice_state(guild)
            except Exception:
                # Una riconciliazione fallita degrada i dati di una guild;
                # un'eccezione non gestita fermerebbe anche le altre.
                logger.exception(
                    "Riconciliazione vocale all'avvio fallita per guild_id=%s", guild.id
                )

    async def _reconcile_voice_state(self, guild: discord.Guild) -> None:
        now = datetime.now(timezone.utc)

        # Da leggere prima di scrivere il marcatore, altrimenti l'ultimo
        # evento risulta essere il marcatore stesso.
        last_event = await db.last_event_at(guild_id=guild.id)
        downtime_start = last_event or now

        await db.insert_raw_event(
            guild_id=guild.id,
            event_type=event_types.BOT_RESTART,
            occurred_at=now,
            dedup_key=f"{event_types.BOT_RESTART}:{guild.id}:{now.isoformat()}",
            payload={
                # Il job non puo' dedurre il downtime dall'assenza di righe:
                # un'ora senza eventi puo' essere silenzio della community
                # o bot spento. Il marcatore rende esplicita la differenza.
                "downtime_start": downtime_start.isoformat(),
                "restarted_at": now.isoformat(),
            },
        )

        # Stato vocale corrente: chi e' in canale adesso, e dove.
        current: dict[int, discord.abc.GuildChannel] = {}
        for channel in [*guild.voice_channels, *guild.stage_channels]:
            for occupant in channel.members:
                if not occupant.bot:
                    current[occupant.id] = channel

        open_sessions = await db.fetch_open_voice_sessions(guild_id=guild.id)

        closed = 0
        still_open: set[int] = set()
        for author_id, channel_id, joined_at in open_sessions:
            channel = current.get(author_id)
            if channel is not None and channel.id == channel_id:
                # Presente adesso nello stesso canale: la sessione non si e'
                # mai interrotta. Lasciarla aperta invece di chiuderla e
                # riaprirla preserva la continuita' attraverso un riavvio
                # breve, che e' il caso normale di un deploy.
                still_open.add(author_id)
                continue

            # Il leave e' andato perso. Non sappiamo quando sia avvenuto: il
            # confine noto piu' stretto e' l'ultimo istante in cui il bot era
            # vivo. Chiudere all'ora del riavvio gonfierebbe la sessione di
            # tutto il downtime. max() perche' il join stesso puo' essere
            # l'ultimo evento registrato.
            await self._emit_voice_event(
                guild_id=guild.id,
                author_id=author_id,
                channel_id=channel_id,
                channel_name=self._channel_name(guild, channel_id),
                event_type=event_types.VOICE_LEAVE,
                occurred_at=max(downtime_start, joined_at),
                reconstruction_reason=event_types.REASON_BOT_RESTART,
            )
            closed += 1

        opened = 0
        for author_id, channel in current.items():
            if author_id in still_open:
                continue
            # In canale adesso senza sessione aperta nel DB: il join e'
            # avvenuto durante il downtime. Da quando sia li' non e'
            # osservabile, quindi la sessione parte adesso: sottostima la
            # durata reale, che e' il verso giusto in cui sbagliare.
            await self._emit_voice_event(
                guild_id=guild.id,
                author_id=author_id,
                channel_id=channel.id,
                channel_name=channel.name,
                event_type=event_types.VOICE_JOIN,
                occurred_at=now,
                reconstruction_reason=event_types.REASON_BOT_RESTART,
            )
            opened += 1

        logger.info(
            "Riconciliazione vocale guild_id=%s: %d sessioni chiuse, %d join sintetici, "
            "%d gia' allineate (downtime a partire da %s)",
            guild.id,
            closed,
            opened,
            len(still_open),
            downtime_start.isoformat(),
        )

    @staticmethod
    def _channel_name(guild: discord.Guild, channel_id: int) -> Optional[str]:
        channel = guild.get_channel(channel_id)
        return getattr(channel, "name", None)

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
