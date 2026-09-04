"""Le garanzie del backfill imposte dallo SCHEMA, non dal codice che chiama.

Il secondo dei due livelli: ``tests/test_backfill.py`` verifica che il percorso
di backfill non chiami mai l'aggiornamento, questo verifica che nemmeno
potrebbe: la query e' ``ON CONFLICT DO NOTHING``, e su una riga esistente non
scrive comunque. Sono due cose diverse e vanno provate separatamente, altrimenti
la seconda resta un'intenzione — come per il vincolo di soppressione delle
metriche.

File separato da ``test_metrics_schema.py``, che ha il proprio harness ed e'
gia' stato verificato in produzione: non lo si tocca per farci entrare altro.

Non blocca ``pytest`` senza Postgres: si salta da solo. Per eseguirlo serve un
database di prova (mai quello di produzione — crea e distrugge uno schema):

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_backfill_schema.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_backfill_test"

GUILD = 111
OSSERVATO = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
STORICO = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

_INSERT_IF_ABSENT = """
    INSERT INTO members (guild_id, author_id, joined_at, left_at)
    VALUES ($1, $2, $3, NULL)
    ON CONFLICT (guild_id, author_id) DO NOTHING
"""


async def _with_schema(body):
    conn = await asyncpg.connect(dsn=DSN)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await conn.execute(f'SET search_path TO "{SCHEMA}"')
        files = sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name)
        for path in files:
            await conn.execute(path.read_text(encoding="utf-8"))
        for path in files:  # idempotenza
            await conn.execute(path.read_text(encoding="utf-8"))
        return await body(conn)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.close()


def test_il_backfill_non_puo_sovrascrivere_un_joined_at_osservato():
    async def body(conn):
        # Un membro osservato in tempo reale.
        await conn.execute(
            "INSERT INTO members (guild_id, author_id, joined_at) VALUES ($1, $2, $3)",
            GUILD,
            1,
            OSSERVATO,
        )
        # Il backfill legge dall'API il joined_at storico e prova a scriverlo.
        await conn.execute(_INSERT_IF_ABSENT, GUILD, 1, STORICO)

        assert (
            await conn.fetchval(
                "SELECT joined_at FROM members WHERE guild_id = $1 AND author_id = $2",
                GUILD,
                1,
            )
            == OSSERVATO
        ), "la riga osservata deve restare intatta"

        # E su un membro assente inserisce davvero.
        await conn.execute(_INSERT_IF_ABSENT, GUILD, 2, STORICO)
        assert await conn.fetchval("SELECT count(*) FROM members") == 2

    asyncio.run(_with_schema(body))


def test_first_seen_at_non_viene_mai_riscritto():
    async def body(conn):
        registra = """
            INSERT INTO guilds (guild_id, first_seen_at)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET left_at = NULL
            RETURNING first_seen_at, backfilled_at
        """
        row = await conn.fetchrow(registra, GUILD, OSSERVATO)
        assert row["first_seen_at"] == OSSERVATO
        assert row["backfilled_at"] is None

        # Una seconda registrazione (riavvio, riaggiunta) non sposta l'ancora:
        # spostarla in avanti cancellerebbe osservazioni valide.
        piu_tardi = OSSERVATO + timedelta(days=30)
        row = await conn.fetchrow(registra, GUILD, piu_tardi)
        assert row["first_seen_at"] == OSSERVATO

    asyncio.run(_with_schema(body))


def test_lancora_di_osservabilita_viene_da_guilds_non_da_raw_events():
    async def body(conn):
        # raw_events ha un evento MOLTO piu' vecchio dell'arrivo dichiarato:
        # e' il caso che distingue le due fonti. Con il vecchio MIN(occurred_at)
        # l'ancora sarebbe il 2024 e le coorti del 2025 risulterebbero
        # osservate; con guilds.first_seen_at no.
        await conn.execute(
            """
            INSERT INTO raw_events (guild_id, event_type, occurred_at, payload)
            VALUES ($1, 'message_create', $2, '{}'::jsonb)
            """,
            GUILD,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        await conn.execute(
            "INSERT INTO guilds (guild_id, first_seen_at) VALUES ($1, $2)",
            GUILD,
            OSSERVATO,
        )

        from job import db as job_db

        anchor = await job_db.fetch_observability_anchor(conn, guild_id=GUILD)
        assert anchor == OSSERVATO

        # Guild non registrata: nessuna ancora, e a valle la coorte viene
        # trattata come anteriore all'osservazione (verso prudente).
        assert await job_db.fetch_observability_anchor(conn, guild_id=999) is None

    asyncio.run(_with_schema(body))
