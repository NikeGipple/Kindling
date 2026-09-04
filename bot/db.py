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
from dataclasses import dataclass
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

# INSERT-only: il backfill non deve MAI toccare una riga esistente. Se lo
# facesse, sovrascriverebbe il joined_at *osservato* di un membro con quello
# storico letto dall'API Discord — e is_survivors_only distingue i membri
# backfillati dagli osservati proprio confrontando joined_at con
# guilds.first_seen_at, quindi la distinzione si romperebbe senza nessun errore.
# La garanzia sta qui, nella forma della query, non nel codice che la chiama.
_INSERT_MEMBER_IF_ABSENT = """
    INSERT INTO members (guild_id, author_id, joined_at, left_at)
    VALUES ($1, $2, $3, NULL)
    ON CONFLICT (guild_id, author_id) DO NOTHING
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

    Solo per l'evento ``member_join`` in tempo reale. In caso di rientro dopo
    un'uscita sovrascrive joined_at/left_at — che li' e' il comportamento
    giusto: nessuno storico dei rientri multipli per l'MVP (vedi CLAUDE.md).

    Il **backfill non passa di qui**: usa ``insert_member_join_if_absent``, che
    non tocca le righe esistenti. Sono due chiamanti con esigenze opposte, e
    condividere la funzione basterebbe a rendere possibile la corruzione.
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


@dataclass(frozen=True)
class GuildRecord:
    """Lo stato di osservazione di una guild, come lo vede il bot."""

    guild_id: int
    first_seen_at: datetime
    backfilled_at: Optional[datetime]


# first_seen_at non viene MAI riscritto: e' l'ancora di osservabilita' del job,
# e spostarla in avanti cancellerebbe osservazioni valide. Su una guild gia'
# nota l'INSERT non fa nulla se non azzerare left_at, perche' il bot c'e' di
# nuovo.
_REGISTER_GUILD = """
    INSERT INTO guilds (guild_id, first_seen_at)
    VALUES ($1, $2)
    ON CONFLICT (guild_id) DO UPDATE SET left_at = NULL
    RETURNING first_seen_at, backfilled_at
"""

_MARK_GUILD_BACKFILLED = """
    UPDATE guilds SET backfilled_at = $2 WHERE guild_id = $1
"""

_MARK_GUILD_LEFT = """
    UPDATE guilds SET left_at = $2 WHERE guild_id = $1
"""


async def register_guild(
    *, guild_id: int, first_seen_at: datetime
) -> GuildRecord:
    """Registra la guild se non c'e', e ne ritorna lo stato di osservazione.

    ``first_seen_at`` viene usato solo alla prima registrazione: da li' in poi
    e' l'ancora, e non si tocca.
    """
    pool = get_pool()
    row = await pool.fetchrow(_REGISTER_GUILD, guild_id, first_seen_at)
    return GuildRecord(
        guild_id=guild_id,
        first_seen_at=row["first_seen_at"],
        backfilled_at=row["backfilled_at"],
    )


async def mark_guild_backfilled(*, guild_id: int) -> None:
    pool = get_pool()
    await pool.execute(_MARK_GUILD_BACKFILLED, guild_id, datetime.now(timezone.utc))


async def mark_guild_left(*, guild_id: int) -> None:
    """Il bot e' stato rimosso dalla guild.

    Registrato e non dedotto: una rimozione seguita da una riaggiunta lascia un
    buco di osservazione, e senza questa data non resterebbe nessuna traccia di
    quando e' cominciato (vedi modello-metriche.md 5.6).
    """
    pool = get_pool()
    try:
        await pool.execute(_MARK_GUILD_LEFT, guild_id, datetime.now(timezone.utc))
    except Exception:
        logger.exception("Update guilds (left) fallito per guild_id=%s", guild_id)


async def insert_member_join_if_absent(
    *, guild_id: int, author_id: int, joined_at: Optional[datetime]
) -> bool:
    """Registra un membro solo se non e' gia' in ``members``.

    E' il percorso del **backfill**, e non condivide la funzione con
    ``on_member_join`` solo perche' tocca la stessa tabella: sono due chiamanti
    con due esigenze opposte. Li' l'aggiornamento e' corretto (rientro dopo
    un'uscita), qui sarebbe una corruzione — il backfill legge dall'API Discord
    il ``joined_at`` storico, e sovrascrivere con quello un ingresso osservato
    in tempo reale renderebbe indistinguibili membri osservati e backfillati.

    Ritorna ``True`` se la riga e' stata inserita davvero.
    """
    if joined_at is None:
        logger.warning(
            "joined_at mancante per author_id=%s in guild_id=%s: membro non registrato in members",
            author_id,
            guild_id,
        )
        return False

    pool = get_pool()
    try:
        result = await pool.execute(
            _INSERT_MEMBER_IF_ABSENT, guild_id, author_id, joined_at
        )
    except Exception:
        logger.exception(
            "Insert members (backfill) fallito per guild_id=%s author_id=%s",
            guild_id,
            author_id,
        )
        return False
    return result.endswith(" 1")


async def mark_member_left(
    *, guild_id: int, author_id: int, left_at: Optional[datetime] = None
) -> None:
    """Segna l'uscita di un membro già tracciato in ``members``.

    Se il membro non risulta mai entrato (nessuna riga esistente), non
    inserisce una riga incompleta: joined_at è NOT NULL e non possiamo
    inventarlo. Logga solo un warning: è il buco di dati che il backfill
    automatico all'avvio esiste per prevenire (vedi CLAUDE.md).
    """
    pool = get_pool()
    try:
        result = await pool.execute(
            _MARK_MEMBER_LEFT, guild_id, author_id, left_at or datetime.now(timezone.utc)
        )
        if result == "UPDATE 0":
            logger.warning(
                "member_remove per author_id=%s in guild_id=%s senza riga members "
                "corrispondente: il suo joined_at è perso (era già uscito prima che "
                "Kindling arrivasse su questo server, oppure il backfill non è riuscito)",
                author_id,
                guild_id,
            )
    except Exception:
        logger.exception(
            "Update members (leave) fallito per guild_id=%s author_id=%s", guild_id, author_id
        )
