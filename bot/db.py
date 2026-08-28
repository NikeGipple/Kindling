"""Accesso a Postgres: pool di connessioni e scrittura append-only su raw_events.

Nessuna query di lettura/aggregazione qui: questo modulo serve solo
all'ingestion. Il calcolo del grafo legge raw_events separatamente, a batch
(vedi architettura/stack-tecnologico-mvp.md).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

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
        message_id, referenced_message_id, dedup_key,
        payload, occurred_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
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
            dedup_key,
            json.dumps(payload, default=str),
            occurred_at or datetime.now(timezone.utc),
        )
    except Exception:
        # L'ingestion non deve mai far cadere il bot: un evento perso si può
        # sempre ricostruire da Discord entro i limiti dell'audit log; un bot
        # offline no.
        logger.exception("Scrittura raw_events fallita per event_type=%s", event_type)
