"""L'``ON CONFLICT`` di ``write_snapshot``, eseguito per la prima volta.

``db.write_snapshot`` promette nel proprio docstring di essere idempotente:
sullo stesso ``(guild_id, as_of, finestra)`` riscrive lo snapshot esistente
invece di affiancargliene un altro. Quella promessa non era mai stata verificata
**ne' in produzione ne' in test**, perche' ``as_of`` veniva preso da ``now()`` al
microsecondo: la chiave non si ripeteva mai, quindi l'``ON CONFLICT`` non veniva
mai raggiunto e ogni esecuzione inseriva. In produzione il risultato sono tre
snapshot (8, 9, 10) nati da lanci manuali di sabato e domenica.

Con ``as_of`` ancorato al lunedi' della settimana ISO quel ramo diventa
raggiungibile — ed e' la prima volta. Questo test lo colpisce davvero: due
scritture consecutive con la stessa chiave devono dare lo **stesso id**, e la
seconda deve **sostituire** gli archi invece di accumularli.

Non blocca ``pytest`` senza Postgres: si salta da solo. Serve un database di
prova, mai quello di produzione — crea e distrugge uno schema:

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_write_snapshot_upsert.py

Stesso impianto di ``tests/test_api_db.py``: schema usa-e-getta, tutte le
migration applicate dentro, connessione del proprietario. Nessun ruolo
``kindling_api`` — qui la domanda e' cosa scrive il job, non chi puo' leggerlo.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

from job import db  # noqa: E402  (dopo importorskip: job.db importa asyncpg)
from job.config import LAYER_VOICE  # noqa: E402
from job.edges import Edge  # noqa: E402

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_upsert_test"

GUILD = 515151
# Un as_of ancorato come li produce ora il job: lunedi' 00:00 UTC.
AS_OF = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
WINDOW_START = AS_OF - timedelta(days=7)
PARAMS = {"half_life_days": 7.0, "cutoff_days": 30.0}


def _edge(src: int, dst: int, *, weight: float) -> Edge:
    low, high = (src, dst) if src < dst else (dst, src)
    return Edge(
        layer=LAYER_VOICE,
        src_author_id=low,
        dst_author_id=high,
        weight=weight,
        weight_undecayed=weight,
        raw_units=weight,
        interaction_count=1,
        last_interaction_at=AS_OF - timedelta(hours=3),
    )


async def _scrivi(conn, *, as_of=AS_OF, edges, code_version="test"):
    return await db.write_snapshot(
        conn,
        guild_id=GUILD,
        as_of=as_of,
        window_start=WINDOW_START,
        window_end=as_of,
        params=PARAMS,
        stats={"closed_observed": 1},
        code_version=code_version,
        edges=edges,
        # Nessuna sessione: qui si prova la chiave dello snapshot e la
        # sostituzione degli archi, e le sessioni hanno una chiave propria
        # (voice_sessions_identity) con un ciclo di vita diverso.
        sessions=[],
    )


async def _in_uno_schema_usa_e_getta(body):
    conn = await asyncpg.connect(dsn=DSN)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await conn.execute(f'SET search_path TO "{SCHEMA}"')

        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name):
            await conn.execute(path.read_text(encoding="utf-8"))

        return await body(conn)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.close()


def test_due_scritture_sulla_stessa_chiave_danno_lo_stesso_id():
    async def body(conn):
        primo = await _scrivi(conn, edges=[_edge(1, 2, weight=3.0)])
        secondo = await _scrivi(conn, edges=[_edge(1, 2, weight=3.0)])

        assert primo == secondo, (
            "la seconda esecuzione ha inserito una riga nuova invece di "
            "riscrivere: e' il difetto che ha prodotto gli snapshot 8, 9 e 10"
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM graph_snapshots WHERE guild_id = $1", GUILD
        ) == 1

    asyncio.run(_in_uno_schema_usa_e_getta(body))


def test_la_riscrittura_aggiorna_params_stats_e_code_version():
    async def body(conn):
        await _scrivi(conn, edges=[_edge(1, 2, weight=3.0)], code_version="vecchio")
        creato_prima = await conn.fetchval(
            "SELECT created_at FROM graph_snapshots WHERE guild_id = $1", GUILD
        )

        snapshot_id = await _scrivi(
            conn, edges=[_edge(1, 2, weight=3.0)], code_version="nuovo"
        )

        row = await conn.fetchrow(
            "SELECT code_version, created_at FROM graph_snapshots WHERE id = $1",
            snapshot_id,
        )
        assert row["code_version"] == "nuovo"
        # created_at si sposta a ogni riscrittura: e' l'unico modo, a valle, di
        # sapere che quella riga e' stata ricalcolata dopo un deploy.
        assert row["created_at"] >= creato_prima

    asyncio.run(_in_uno_schema_usa_e_getta(body))


def test_la_seconda_scrittura_sostituisce_gli_archi_e_non_li_accumula():
    async def body(conn):
        snapshot_id = await _scrivi(
            conn,
            edges=[_edge(1, 2, weight=3.0), _edge(2, 3, weight=1.0)],
        )
        # Un insieme diverso: un arco sparisce (sotto soglia dopo un ricalcolo),
        # uno cambia peso, uno e' nuovo. E' il caso che un UPSERT sugli archi
        # gestirebbe male, lasciando in tabella il fantasma del primo.
        di_nuovo = await _scrivi(
            conn,
            edges=[_edge(1, 2, weight=9.0), _edge(3, 4, weight=2.0)],
        )

        assert di_nuovo == snapshot_id
        righe = await conn.fetch(
            """
            SELECT src_author_id, dst_author_id, weight
            FROM graph_edges
            WHERE snapshot_id = $1
            ORDER BY src_author_id, dst_author_id
            """,
            snapshot_id,
        )
        assert [(r["src_author_id"], r["dst_author_id"]) for r in righe] == [
            (1, 2),
            (3, 4),
        ], "gli archi della prima scrittura sono sopravvissuti alla seconda"
        assert righe[0]["weight"] == 9.0, "il peso non e' stato aggiornato"
        # Nessun arco orfano nemmeno guardando la tabella intera: la DELETE e'
        # per snapshot_id, e questo e' l'unico snapshot in giro.
        assert await conn.fetchval("SELECT count(*) FROM graph_edges") == 2

    asyncio.run(_in_uno_schema_usa_e_getta(body))


def test_un_as_of_diverso_di_un_microsecondo_crea_comunque_una_riga_nuova():
    """La riproduzione del difetto, non un comportamento da cambiare.

    La chiave e' esatta e deve restarlo: due snapshot con `as_of` diverso sono
    due snapshot, punto. E' precisamente per questo che l'ancoraggio non poteva
    essere una tolleranza applicata qui — un "arrotonda al giorno piu' vicino"
    nel SQL avrebbe reso impossibile il caso legittimo di due finestre diverse
    sullo stesso istante — ma doveva stare a monte, in chi decide `as_of`.
    """

    async def body(conn):
        primo = await _scrivi(conn, edges=[_edge(1, 2, weight=3.0)])
        secondo = await _scrivi(
            conn,
            as_of=AS_OF + timedelta(microseconds=1),
            edges=[_edge(1, 2, weight=3.0)],
        )

        assert primo != secondo
        assert await conn.fetchval(
            "SELECT count(*) FROM graph_snapshots WHERE guild_id = $1", GUILD
        ) == 2

    asyncio.run(_in_uno_schema_usa_e_getta(body))


def test_due_finestre_diverse_sullo_stesso_as_of_restano_due_snapshot():
    # Caso legittimo e gia' previsto (--as-of T --window-days 7 accanto a 14):
    # l'ancoraggio non deve averlo reso impossibile.
    async def body(conn):
        settimana = await _scrivi(conn, edges=[_edge(1, 2, weight=3.0)])
        due_settimane = await db.write_snapshot(
            conn,
            guild_id=GUILD,
            as_of=AS_OF,
            window_start=AS_OF - timedelta(days=14),
            window_end=AS_OF,
            params=PARAMS,
            stats={},
            code_version="test",
            edges=[_edge(1, 2, weight=5.0)],
            sessions=[],
        )

        assert settimana != due_settimane

    asyncio.run(_in_uno_schema_usa_e_getta(body))
