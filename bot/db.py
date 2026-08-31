"""Accesso a Postgres: pool di connessioni e scrittura append-only su raw_events.

Nessuna query di aggregazione o di metrica qui: questo modulo serve solo
all'ingestion. Il calcolo del grafo legge raw_events separatamente, a batch
(package job/, vedi docs/architettura/architettura.md). Le uniche letture
presenti sono quelle che servono all'ingestion stessa per riconciliare il
proprio stato all'avvio (modello-grafo.md 4.4).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from . import event_types

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool(database_url: str) -> asyncpg.Pool:
    """Crea (una sola volta) il pool di connessioni condiviso dal bot."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=5)
        logger.info("Pool Postgres inizializzato")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool Postgres non inizializzato: chiamare init_pool() prima.")
    return _pool


_INSERT_RAW_EVENT = """
    INSERT INTO raw_events (
        guild_id, event_type, author_id, channel_id,
        message_id, referenced_message_id, referenced_channel_id, dedup_key,
        payload, occurred_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
    ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
"""


async def insert_raw_event(
    *,
    guild_id: int,
    event_type: str,
    payload: dict[str, Any],
    author_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    message_id: Optional[int] = None,
    referenced_message_id: Optional[int] = None,
    referenced_channel_id: Optional[int] = None,
    dedup_key: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> None:
    """Scrive un evento grezzo, immutabile, in raw_events.

    Non aggiorna mai una riga esistente: raw_events e' append-only per
    definizione. ``dedup_key``, se passato, rende l'insert idempotente
    (utile in caso di reconnect o backfill che rivedono lo stesso evento).
    """
    pool = get_pool()
    try:
        await pool.execute(
            _INSERT_RAW_EVENT,
            guild_id,
            event_type,
            author_id,
            channel_id,
            message_id,
            referenced_message_id,
            referenced_channel_id,
            dedup_key,
            json.dumps(payload, default=str),
            occurred_at or datetime.now(timezone.utc),
        )
    except Exception:
        # L'ingestion non deve mai far cadere il bot: un evento perso si può
        # sempre ricostruire da Discord entro i limiti dell'audit log; un bot
        # offline no.
        logger.exception("Scrittura raw_events fallita per event_type=%s", event_type)


# ---- letture per la riconciliazione all'avvio (modello-grafo.md 4.4) ------

_LAST_EVENT_AT = """
    SELECT max(occurred_at) FROM raw_events WHERE guild_id = $1
"""

_OPEN_VOICE_SESSIONS = """
    SELECT author_id, channel_id, occurred_at
    FROM (
        SELECT DISTINCT ON (author_id, channel_id)
               author_id, channel_id, event_type, occurred_at
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($2, $3)
          AND author_id IS NOT NULL
          AND channel_id IS NOT NULL
          AND forgotten_at IS NULL
        ORDER BY author_id, channel_id, occurred_at DESC, id DESC
    ) AS ultimo
    WHERE event_type = $2
"""


async def last_event_at(*, guild_id: int) -> Optional[datetime]:
    """Timestamp dell'ultimo evento noto per questa guild.

    E' la migliore approssimazione disponibile dell'istante in cui il bot era
    ancora vivo prima di fermarsi: modello-grafo.md 4.3 chiede di chiudere le
    sessioni orfane "all'inizio del downtime del bot se noto", e questo e' il
    solo dato che lo rende noto senza introdurre un heartbeat. Va letto
    *prima* di scrivere il marcatore di riavvio, altrimenti restituisce il
    marcatore stesso.
    """
    pool = get_pool()
    try:
        return await pool.fetchval(_LAST_EVENT_AT, guild_id)
    except Exception:
        logger.exception("Lettura ultimo evento fallita per guild_id=%s", guild_id)
        return None


async def fetch_open_voice_sessions(
    *, guild_id: int
) -> list[tuple[int, int, datetime]]:
    """Sessioni vocali rimaste aperte nel DB: (author_id, channel_id, joined_at).

    "Aperta" = per quella coppia (membro, canale) l'ultimo evento vocale
    registrato e' un ``voice_join`` senza ``voice_leave`` successivo. Al
    riavvio alcune di queste sono reali (la persona e' tuttora in canale) e
    altre no (il leave e' andato perso durante il downtime): distinguerle
    confrontandole con lo stato vocale corrente e' il lavoro della
    riconciliazione in bot/cogs/ingestion.py.
    """
    pool = get_pool()
    try:
        rows = await pool.fetch(
            _OPEN_VOICE_SESSIONS, guild_id, event_types.VOICE_JOIN, event_types.VOICE_LEAVE
        )
    except Exception:
        logger.exception("Lettura sessioni vocali aperte fallita per guild_id=%s", guild_id)
        return []
    return [(r["author_id"], r["channel_id"], r["occurred_at"]) for r in rows]


# ---- members: stato corrente (non append-only, vedi migrations/0002) -------

_UPSERT_MEMBER_JOIN = """
    INSERT INTO members (guild_id, author_id, joined_at, left_at)
    VALUES ($1, $2, $3, NULL)
    ON CONFLICT (guild_id, author_id) DO UPDATE
        SET joined_at = EXCLUDED.joined_at,
            left_at = NULL
"""

_MARK_MEMBER_LEFT = """
    UPDATE members
    SET left_at = $3
    WHERE guild_id = $1 AND author_id = $2
"""


async def upsert_member_join(
    *, guild_id: int, author_id: int, joined_at: Optional[datetime]
) -> None:
    """Registra o aggiorna l'ingresso di un membro in ``members``.

    Usato sia dall'evento ``member_join`` in tempo reale sia dal backfill una
    tantum (comando ``!backfill_members``, ``bot/cogs/admin.py``) per i
    membri già presenti quando il bot viene aggiunto a un server. In caso di
    rientro dopo un'uscita, sovrascrive joined_at/left_at: nessuno storico
    dei rientri multipli per l'MVP (vedi CLAUDE.md).
    """
    if joined_at is None:
        # In teoria discord.py valorizza sempre joined_at; per sicurezza non
        # scriviamo mai una riga con la colonna NOT NULL mancante.
        logger.warning(
            "joined_at mancante per author_id=%s in guild_id=%s: membro non registrato in members",
            author_id,
            guild_id,
        )
        return

    pool = get_pool()
    try:
        await pool.execute(_UPSERT_MEMBER_JOIN, guild_id, author_id, joined_at)
    except Exception:
        logger.exception(
            "Upsert members (join) fallito per guild_id=%s author_id=%s", guild_id, author_id
        )


async def mark_member_left(
    *, guild_id: int, author_id: int, left_at: Optional[datetime] = None
) -> None:
    """Segna l'uscita di un membro già tracciato in ``members``.

    Se il membro non risulta mai entrato (nessuna riga esistente — tipicamente
    perché era già presente prima che il backfill fosse lanciato su questo
    server), non inserisce una riga incompleta: joined_at è NOT NULL e non
    possiamo inventarlo. Logga solo un warning: è esattamente il buco di dati
    che il backfill dovrebbe prevenire (vedi CLAUDE.md).
    """
    pool = get_pool()
    try:
        result = await pool.execute(
            _MARK_MEMBER_LEFT, guild_id, author_id, left_at or datetime.now(timezone.utc)
        )
        if result == "UPDATE 0":
            logger.warning(
                "member_remove per author_id=%s in guild_id=%s senza riga members "
                "corrispondente: il suo joined_at è perso (backfill mai lanciato su questo server?)",
                author_id,
                guild_id,
            )
    except Exception:
        logger.exception(
            "Update members (leave) fallito per guild_id=%s author_id=%s", guild_id, author_id
        )
