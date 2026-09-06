"""Tutti gli endpoint di serie devono rispondere sullo STESSO snapshot.

`docs/architettura/api.md` §5 dice che lo "stato dell'ultimo snapshot" e'
`?limit=1` su piu' endpoint, e non un endpoint dedicato. Quella scelta regge
solo se "l'ultimo" vuol dire la stessa cosa per tutte le query: se `/runs` e
`/robustness` scelgono snapshot diversi, un dashboard mostra i parametri di una
run accanto ai numeri di un'altra — senza nessun errore, che e' la parte
peggiore.

Il caso non e' di laboratorio. `graph_snapshots_identity` e' su
`(guild_id, as_of, window_start, window_end)`: due snapshot con lo stesso
`as_of` sono ammessi se differisce la finestra, ed e' proprio cio' che produce
il confronto tra larghezze di finestra a parita' di as_of
(`--as-of T --window-days 7` accanto a `--as-of T --window-days 14`). Con quel
pareggio, un `ORDER BY as_of DESC` senza tiebreaker lascia l'ordine indefinito.

Il test RIPRODUCE il difetto invece di ispezionare il SQL: costruisce il
pareggio e guarda quale snapshot esce dalle sei funzioni. Un test sul testo
della query passerebbe anche con un ordinamento sbagliato, purche' scritto
uguale in tutti e sei i punti.

Non blocca ``pytest`` senza Postgres: si salta da solo. Per eseguirlo serve un
database di prova (mai quello di produzione — crea e distrugge uno schema):

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_api_db.py

Qui non c'e' nessun ruolo ``kindling_api``: la domanda e' QUALE snapshot viene
scelto, non chi ha il permesso di leggerlo (quello e' `test_api_role_schema.py`),
e basta la connessione del proprietario. Le migration si applicano comunque
tutte, 0010 compresa: e' idempotente e crea il ruolo se manca, ma questo test
non lo usa e non lo gestisce.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

from api import db  # noqa: E402  (dopo importorskip: api.db importa asyncpg)

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_api_db_test"

GUILD = 424242
# Un as_of solo, condiviso dai due snapshot: e' il pareggio da riprodurre.
AS_OF = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
COHORT_START = date(2026, 8, 24)


async def _snapshot(owner, *, window_days: int) -> int:
    """Uno snapshot sulla stessa guild e sullo stesso as_of, altra finestra."""
    return await owner.fetchval(
        """
        INSERT INTO graph_snapshots (guild_id, as_of, window_start, window_end,
                                     params, code_version)
        VALUES ($1, $2, $3, $2, $4::jsonb, 'test')
        RETURNING id
        """,
        GUILD,
        AS_OF,
        AS_OF - timedelta(days=window_days),
        '{"window_days": %d}' % window_days,
    )


async def _metriche(owner, snapshot_id: int, *, window_days: int) -> None:
    """Una riga in OGNI tabella di metrica, per lo snapshot dato.

    Serve che ogni tabella sia popolata per entrambi gli snapshot: se una lo
    fosse per uno solo, la query su quella tabella risponderebbe "giusto" per
    mancanza di alternative invece che per ordinamento.
    """
    await owner.execute(
        """
        INSERT INTO metric_runs (snapshot_id, guild_id, as_of, params, stats,
                                 code_version)
        VALUES ($1, $2, $3, $4::jsonb, '{}'::jsonb, 'test')
        """,
        snapshot_id,
        GUILD,
        AS_OF,
        '{"window_days": %d}' % window_days,
    )
    await owner.execute(
        """
        INSERT INTO metric_robustness (snapshot_id, layer, removal_fraction,
                                       n_effective, nodes_removed, giant_before)
        VALUES ($1, 'voice', 0.05, 30, 2, 0.9)
        """,
        snapshot_id,
    )
    await owner.execute(
        """
        INSERT INTO metric_communities (snapshot_id, layer, n_effective,
                                        community_count, modularity)
        VALUES ($1, 'voice', 30, 3, 0.42)
        """,
        snapshot_id,
    )
    await owner.execute(
        """
        INSERT INTO metric_community_sizes (snapshot_id, layer, bucket,
                                            community_count, member_count)
        VALUES ($1, 'voice', '10-19', 2, 24)
        """,
        snapshot_id,
    )
    await owner.execute(
        """
        INSERT INTO metric_cohorts (snapshot_id, cohort_start, layer_scope, k,
                                    n_effective, observation_days, is_mature)
        VALUES ($1, $2, 'any', 5, 12, 28, TRUE)
        """,
        snapshot_id,
        COHORT_START,
    )
    await owner.execute(
        """
        INSERT INTO metric_cohort_retention (snapshot_id, cohort_start,
                                             horizon_days, n_effective,
                                             retained_fraction, is_computable)
        VALUES ($1, $2, 28, 12, 0.75, TRUE)
        """,
        snapshot_id,
        COHORT_START,
    )


async def _con_due_run_allo_stesso_as_of(body):
    """Schema temporaneo, migration applicate, il pareggio dentro, e via.

    Il pool viene costruito qui e assegnato a ``db._pool`` invece di passare da
    ``db.init_pool``: le tabelle vivono nello schema temporaneo, quindi serve un
    ``search_path`` che ``init_pool`` — giustamente — non espone.
    """
    owner = await asyncpg.connect(dsn=DSN)
    pool = None
    try:
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await owner.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await owner.execute(f'SET search_path TO "{SCHEMA}"')

        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name):
            await owner.execute(path.read_text(encoding="utf-8"))

        # In quest'ordine: il piu' vecchio per id e' inserito per primo, cioe'
        # e' quello che un ordinamento senza tiebreaker tende a restituire.
        primo = await _snapshot(owner, window_days=7)
        secondo = await _snapshot(owner, window_days=14)
        await _metriche(owner, primo, window_days=7)
        await _metriche(owner, secondo, window_days=14)

        pool = await asyncpg.create_pool(
            dsn=DSN,
            min_size=1,
            max_size=2,
            server_settings={"search_path": SCHEMA},
        )
        db._pool = pool
        return await body(owner, primo, secondo)
    finally:
        db._pool = None
        if pool is not None:
            await pool.close()
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await owner.close()


def test_la_fixture_costruisce_davvero_due_run_allo_stesso_as_of():
    """Se questa cade, il test vero non sta piu' provando niente."""

    async def body(owner, primo, secondo):
        assert primo != secondo
        assert await owner.fetchval(
            "SELECT count(DISTINCT as_of) FROM metric_runs WHERE guild_id = $1",
            GUILD,
        ) == 1
        assert await owner.fetchval(
            "SELECT count(*) FROM metric_runs WHERE guild_id = $1", GUILD
        ) == 2

    asyncio.run(_con_due_run_allo_stesso_as_of(body))


def test_con_limit_1_tutte_le_query_rispondono_sullo_stesso_snapshot():
    async def body(owner, primo, secondo):
        atteso = max(primo, secondo)

        risposte = {
            "runs": await db.fetch_runs(GUILD, limit=1),
            "robustness": await db.fetch_robustness(GUILD, limit=1),
            "communities": await db.fetch_communities(GUILD, limit=1),
            "community_sizes": await db.fetch_community_sizes(GUILD, limit=1),
            "cohorts": await db.fetch_cohorts(GUILD, limit=1),
            "cohort_retention": await db.fetch_cohort_retention(GUILD, limit=1),
        }

        for nome, righe in risposte.items():
            # Una risposta vuota renderebbe l'assert sotto vero per niente.
            assert righe, f"{nome}: nessuna riga, il confronto non prova nulla"
            scelti = {riga["snapshot_id"] for riga in righe}
            assert scelti == {atteso}, (
                f"{nome} risponde sullo snapshot {sorted(scelti)} invece di "
                f"{atteso}: con due run allo stesso as_of gli endpoint si "
                f"sfasano tra loro"
            )

    asyncio.run(_con_due_run_allo_stesso_as_of(body))
