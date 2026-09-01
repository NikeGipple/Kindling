"""Tutto il SQL del job, e nient'altro.

Il job legge SOLO Postgres: non chiama mai l'API Discord (modello-grafo.md 6).
Il recupero via API degli autori mancanti e' un comando di backfill lato bot,
separato e opzionale — tenerlo fuori di qui e' cio' che rende il calcolo
riproducibile: rieseguire il job su dati fermi da' sempre lo stesso risultato.

Le letture escludono sempre le righe con ``forgotten_at`` valorizzato: una
richiesta di cancellazione non deve poter rientrare dalla finestra di un
derivato ricalcolato.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable, Optional

import asyncpg

from .config import LAYER_MENTION, LAYER_REACTION, LAYER_REPLY
from .edges import DirectedInteraction, Edge
from .intervals import RestartMarker, VoiceEvent
from .sessions import VoiceSession

logger = logging.getLogger(__name__)

# Tipi di evento, duplicati qui come stringhe invece che importati da bot/:
# il job e' un consumatore dello schema canonico degli eventi, non un pezzo
# dell'ingestion, e non deve dipendere da discord.py per girare.
EVENT_VOICE_JOIN = "voice_join"
EVENT_VOICE_LEAVE = "voice_leave"
EVENT_BOT_RESTART = "bot_restart"
EVENT_MESSAGE_CREATE = "message_create"
EVENT_MESSAGE_REPLY = "message_reply"
EVENT_REACTION_ADD = "reaction_add"


async def connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn=dsn)


# ---- letture -------------------------------------------------------------


async def list_guilds(
    conn: asyncpg.Connection, *, window_start: datetime, window_end: datetime
) -> list[int]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT guild_id
        FROM raw_events
        WHERE occurred_at >= $1 AND occurred_at < $2
        ORDER BY guild_id
        """,
        window_start,
        window_end,
    )
    return [r["guild_id"] for r in rows]


async def fetch_voice_events(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> list[VoiceEvent]:
    """Eventi vocali della guild, con il margine oltre i bordi della finestra.

    Il margine non e' un'ottimizzazione: senza di esso una sessione a cavallo
    del confine dello snapshot verrebbe ricostruita a meta', ed e' proprio
    quello che modello-grafo.md 4.1 vieta ("mai spezzare una sessione a meta'
    per far quadrare la finestra").
    """
    rows = await conn.fetch(
        """
        SELECT id, author_id, channel_id, event_type, occurred_at,
               payload ->> 'reconstructed' AS reconstructed
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($4, $5)
          AND author_id IS NOT NULL
          AND channel_id IS NOT NULL
          AND forgotten_at IS NULL
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY occurred_at, id
        """,
        guild_id,
        since,
        until,
        EVENT_VOICE_JOIN,
        EVENT_VOICE_LEAVE,
    )
    return [
        VoiceEvent(
            author_id=r["author_id"],
            channel_id=r["channel_id"],
            event_type=r["event_type"],
            occurred_at=r["occurred_at"],
            is_reconstructed=r["reconstructed"] == "true",
            event_id=r["id"],
        )
        for r in rows
    ]


async def fetch_restart_markers(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> list[RestartMarker]:
    rows = await conn.fetch(
        """
        SELECT occurred_at,
               payload ->> 'downtime_start' AS downtime_start,
               payload ->> 'voice_confirmed' AS voice_confirmed
        FROM raw_events
        WHERE guild_id = $1
          AND event_type = $4
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY occurred_at
        """,
        guild_id,
        since,
        until,
        EVENT_BOT_RESTART,
    )
    markers = []
    for row in rows:
        downtime_start = None
        if row["downtime_start"]:
            downtime_start = datetime.fromisoformat(row["downtime_start"])
        markers.append(
            RestartMarker(
                restarted_at=row["occurred_at"],
                downtime_start=downtime_start,
                voice_confirmed=_parse_voice_confirmed(row["voice_confirmed"]),
            )
        )
    return markers


def _parse_voice_confirmed(raw: Optional[str]) -> frozenset[tuple[int, int]]:
    """Le presenze che quel riavvio ha visto ancora in corso.

    Chiave assente = marcatore vecchio, scritto prima che l'ingestion la
    registrasse: nessuna conferma, cioe' esattamente il comportamento di
    allora. Nessuna migrazione dei marcatori gia' in produzione.
    """
    if not raw:
        return frozenset()
    try:
        entries = json.loads(raw) or []
    except (TypeError, ValueError):
        logger.warning("voice_confirmed non decodificabile, marcatore trattato senza conferme")
        return frozenset()
    confirmed = set()
    for entry in entries:
        try:
            confirmed.add((int(entry["author_id"]), int(entry["channel_id"])))
        except (KeyError, TypeError, ValueError):
            logger.warning("voce di voice_confirmed malformata, ignorata")
    return frozenset(confirmed)


async def fetch_member_activity(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> dict[int, list[datetime]]:
    """Timestamp di tutti gli eventi per autore, per chiudere le sessioni orfane.

    "Primo evento successivo dello stesso membro" (modello-grafo.md 4.3) e'
    volutamente qualunque evento, non solo quelli vocali: e' un confine
    superiore su quando quella presenza era ancora osservabile, non una prova
    che la persona fosse uscita dal canale.
    """
    rows = await conn.fetch(
        """
        SELECT author_id, occurred_at
        FROM raw_events
        WHERE guild_id = $1
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY author_id, occurred_at
        """,
        guild_id,
        since,
        until,
    )
    activity: dict[int, list[datetime]] = {}
    for row in rows:
        activity.setdefault(row["author_id"], []).append(row["occurred_at"])
    return activity


async def refresh_message_authors(conn: asyncpg.Connection, *, guild_id: int) -> int:
    """Ricostruisce ``message_authors`` da raw_events.

    Su tutto lo storico e non solo sulla finestra: una reply o una reazione di
    questa settimana puo' benissimo puntare a un messaggio di mesi fa, e senza
    il suo autore quell'interazione non genera un arco.
    """
    result = await conn.execute(
        """
        INSERT INTO message_authors (guild_id, message_id, author_id)
        SELECT DISTINCT guild_id, message_id, author_id
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($2, $3)
          AND message_id IS NOT NULL
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
        ON CONFLICT (guild_id, message_id) DO NOTHING
        """,
        guild_id,
        EVENT_MESSAGE_CREATE,
        EVENT_MESSAGE_REPLY,
    )
    return int(result.rsplit(" ", 1)[-1]) if result.startswith("INSERT") else 0


async def fetch_directed_interactions(
    conn: asyncpg.Connection, *, guild_id: int, window_start: datetime, window_end: datetime
) -> list[DirectedInteraction]:
    """Reply, menzioni e reazioni della finestra, con il bersaglio gia' risolto.

    La risoluzione e' un LEFT JOIN su message_authors per ``message_id``: gli
    id dei messaggi Discord sono globalmente univoci, quindi il canale non
    serve a risolvere l'autore. ``referenced_channel_id`` serve al backfill via
    API lato bot (``channel.fetch_message`` vuole il canale giusto), non qui.
    Un target non risolto resta ``None`` e viene contato nella copertura.
    """
    interactions: list[DirectedInteraction] = []

    reply_rows = await conn.fetch(
        """
        SELECT e.author_id, e.occurred_at, ma.author_id AS target_author_id
        FROM raw_events e
        LEFT JOIN message_authors ma
               ON ma.guild_id = e.guild_id
              AND ma.message_id = e.referenced_message_id
        WHERE e.guild_id = $1
          AND e.event_type = $4
          AND e.author_id IS NOT NULL
          AND e.referenced_message_id IS NOT NULL
          AND e.forgotten_at IS NULL
          AND e.occurred_at >= $2
          AND e.occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_MESSAGE_REPLY,
    )
    interactions.extend(
        DirectedInteraction(
            layer=LAYER_REPLY,
            src_author_id=r["author_id"],
            dst_author_id=r["target_author_id"],
            occurred_at=r["occurred_at"],
        )
        for r in reply_rows
    )

    reaction_rows = await conn.fetch(
        """
        SELECT e.author_id, e.occurred_at, ma.author_id AS target_author_id
        FROM raw_events e
        LEFT JOIN message_authors ma
               ON ma.guild_id = e.guild_id
              AND ma.message_id = e.message_id
        WHERE e.guild_id = $1
          AND e.event_type = $4
          AND e.author_id IS NOT NULL
          AND e.message_id IS NOT NULL
          AND e.forgotten_at IS NULL
          AND e.occurred_at >= $2
          AND e.occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_REACTION_ADD,
    )
    interactions.extend(
        DirectedInteraction(
            layer=LAYER_REACTION,
            src_author_id=r["author_id"],
            dst_author_id=r["target_author_id"],
            occurred_at=r["occurred_at"],
        )
        for r in reaction_rows
    )

    # Le menzioni non hanno il problema della risoluzione: payload.mention_ids
    # contiene gia' gli id dei destinatari.
    mention_rows = await conn.fetch(
        """
        SELECT author_id, occurred_at, payload ->> 'mention_ids' AS mention_ids
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($4, $5)
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
          AND payload ->> 'mention_ids' IS NOT NULL
          AND occurred_at >= $2
          AND occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_MESSAGE_CREATE,
        EVENT_MESSAGE_REPLY,
    )
    for row in mention_rows:
        try:
            mention_ids = json.loads(row["mention_ids"]) or []
        except (TypeError, ValueError):
            logger.warning("mention_ids non decodificabile, riga ignorata")
            continue
        for target in mention_ids:
            interactions.append(
                DirectedInteraction(
                    layer=LAYER_MENTION,
                    src_author_id=row["author_id"],
                    dst_author_id=int(target),
                    occurred_at=row["occurred_at"],
                )
            )

    return interactions


# ---- scritture -----------------------------------------------------------


async def write_snapshot(
    conn: asyncpg.Connection,
    *,
    guild_id: int,
    as_of: datetime,
    window_start: datetime,
    window_end: datetime,
    params: dict[str, Any],
    stats: dict[str, Any],
    code_version: Optional[str],
    edges: Iterable[Edge],
    sessions: Iterable[VoiceSession],
) -> int:
    """Scrive snapshot, archi e sessioni in un'unica transazione.

    Idempotente: sullo stesso ``(guild_id, as_of, finestra)`` riscrive lo
    snapshot esistente invece di affiancargliene un altro. Un job rilanciato
    dopo un fallimento a meta' non deve lasciare mezzo grafo in tabella.
    """
    async with conn.transaction():
        snapshot_id = await conn.fetchval(
            """
            INSERT INTO graph_snapshots
                (guild_id, as_of, window_start, window_end, params, stats, code_version)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
            ON CONFLICT (guild_id, as_of, window_start, window_end) DO UPDATE
                SET params = EXCLUDED.params,
                    stats = EXCLUDED.stats,
                    code_version = EXCLUDED.code_version,
                    created_at = now()
            RETURNING id
            """,
            guild_id,
            as_of,
            window_start,
            window_end,
            json.dumps(params, default=str),
            json.dumps(stats, default=str),
            code_version,
        )

        # Gli archi vengono riscritti per intero: un ricalcolo con parametri
        # diversi puo' far sparire un arco (sotto soglia), e un UPSERT
        # lascerebbe in tabella il fantasma di quello vecchio.
        await conn.execute("DELETE FROM graph_edges WHERE snapshot_id = $1", snapshot_id)
        await conn.executemany(
            """
            INSERT INTO graph_edges (
                snapshot_id, layer, src_author_id, dst_author_id,
                weight, weight_undecayed, raw_units,
                interaction_count, last_interaction_at, is_reconciled
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            [
                (
                    snapshot_id,
                    e.layer,
                    e.src_author_id,
                    e.dst_author_id,
                    e.weight,
                    e.weight_undecayed,
                    e.raw_units,
                    e.interaction_count,
                    e.last_interaction_at,
                    e.is_reconciled,
                )
                for e in edges
            ],
        )

        for session in sessions:
            session_id = await conn.fetchval(
                """
                INSERT INTO voice_sessions (
                    guild_id, channel_id, started_at, ended_at,
                    participant_count, is_complete, reconciliation_note
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (guild_id, channel_id, started_at) DO UPDATE
                    SET ended_at = EXCLUDED.ended_at,
                        participant_count = EXCLUDED.participant_count,
                        is_complete = EXCLUDED.is_complete,
                        reconciliation_note = EXCLUDED.reconciliation_note
                RETURNING id
                """,
                session.guild_id,
                session.channel_id,
                session.started_at,
                session.ended_at,
                session.participant_count,
                session.is_complete,
                session.reconciliation_note,
            )
            await conn.execute(
                "DELETE FROM voice_session_participants WHERE session_id = $1", session_id
            )
            await conn.executemany(
                """
                INSERT INTO voice_session_participants
                    (session_id, author_id, joined_at, left_at, is_reconciled)
                VALUES ($1, $2, $3, $4, $5)
                """,
                [
                    (session_id, i.author_id, i.start, i.end, i.is_reconciled)
                    for i in session.intervals
                ],
            )

    return snapshot_id


# ---- letture per l'export -------------------------------------------------


async def fetch_snapshot(
    conn: asyncpg.Connection, *, snapshot_id: Optional[int] = None, guild_id: Optional[int] = None
) -> Optional[asyncpg.Record]:
    if snapshot_id is not None:
        return await conn.fetchrow(
            "SELECT * FROM graph_snapshots WHERE id = $1", snapshot_id
        )
    return await conn.fetchrow(
        """
        SELECT * FROM graph_snapshots
        WHERE ($1::bigint IS NULL OR guild_id = $1)
        ORDER BY as_of DESC, id DESC
        LIMIT 1
        """,
        guild_id,
    )


async def fetch_snapshot_edges(
    conn: asyncpg.Connection, *, snapshot_id: int
) -> list[Edge]:
    rows = await conn.fetch(
        """
        SELECT layer, src_author_id, dst_author_id, weight, weight_undecayed,
               raw_units, interaction_count, last_interaction_at, is_reconciled
        FROM graph_edges
        WHERE snapshot_id = $1
        ORDER BY layer, src_author_id, dst_author_id
        """,
        snapshot_id,
    )
    return [
        Edge(
            layer=r["layer"],
            src_author_id=r["src_author_id"],
            dst_author_id=r["dst_author_id"],
            weight=r["weight"],
            weight_undecayed=r["weight_undecayed"],
            raw_units=r["raw_units"],
            interaction_count=r["interaction_count"],
            last_interaction_at=r["last_interaction_at"],
            is_reconciled=r["is_reconciled"],
        )
        for r in rows
    ]
