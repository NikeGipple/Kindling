"""Il vincolo di soppressione e' imposto dal DATABASE, non dal codice del job.

Tutti gli altri test del job girano su fixture sintetiche e senza database, ed
e' la scelta giusta: le funzioni di calcolo sono pure apposta. Questo no, e non
puo' esserlo — modello-metriche.md 9.1 giustifica le tabelle tipizzate proprio
con il fatto che "una riga soppressa non contiene mai un valore" e' una garanzia
del database e non del codice che scrive. Verificarla altrove significherebbe
verificare qualcos'altro, e lasciarla non verificata la lascerebbe essere
un'intenzione.

Non blocca ``pytest`` senza Postgres: si salta da solo. Per eseguirlo davvero
serve un database di prova (mai quello di produzione — crea e distrugge uno
schema):

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_metrics_schema.py

Applica le migration in ordine dentro uno schema temporaneo, quindi verifica
anche che si applichino pulite e che siano idempotenti.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_metrics_test"

T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


async def _with_schema(body):
    conn = await asyncpg.connect(dsn=DSN)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await conn.execute(f'SET search_path TO "{SCHEMA}"')

        files = sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name)
        for path in files:
            await conn.execute(path.read_text(encoding="utf-8"))
        # Idempotenza: le migration si applicano due volte senza rompere niente,
        # perche' e' cosi' che vengono lanciate a mano sulla droplet.
        for path in files:
            await conn.execute(path.read_text(encoding="utf-8"))

        snapshot_id = await conn.fetchval(
            """
            INSERT INTO graph_snapshots (guild_id, as_of, window_start, window_end)
            VALUES ($1, $2, $2, $2) RETURNING id
            """,
            111,
            T0,
        )
        await conn.execute(
            """
            INSERT INTO metric_runs (snapshot_id, guild_id, as_of)
            VALUES ($1, $2, $3)
            """,
            snapshot_id,
            111,
            T0,
        )
        return await body(conn, snapshot_id)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await conn.close()


def test_riga_soppressa_con_un_valore_viene_rifiutata():
    async def body(conn, snapshot_id):
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await conn.execute(
                """
                INSERT INTO metric_robustness
                    (snapshot_id, layer, removal_fraction, is_suppressed, giant_before)
                VALUES ($1, 'voice', 0.10, TRUE, 0.9)
                """,
                snapshot_id,
            )
        assert "giant_before" in str(excinfo.value)

    _run(_with_schema(body))


def test_il_vincolo_copre_anche_le_colonne_da_cui_si_ricava_la_numerosita():
    async def body(conn, snapshot_id):
        # nodes_removed e' ceil(X*n) e removal_fraction sta in chiave: da solo
        # restituisce la dimensione del grafo. Non e' "il valore", ma pubblica
        # esattamente cio' che la soppressione doveva nascondere.
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await conn.execute(
                """
                INSERT INTO metric_robustness
                    (snapshot_id, layer, removal_fraction, is_suppressed, nodes_removed)
                VALUES ($1, 'voice', 0.10, TRUE, 3)
                """,
                snapshot_id,
            )
        assert "nodes_removed" in str(excinfo.value)

        # Lo stesso su una tabella diversa, per verificare che il vincolo sia
        # davvero condiviso e non copiato su una sola.
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await conn.execute(
                """
                INSERT INTO metric_cohorts
                    (snapshot_id, cohort_start, layer_scope, k, is_suppressed, event_count)
                VALUES ($1, DATE '2026-08-03', 'any', 5, TRUE, 2)
                """,
                snapshot_id,
            )
        assert "event_count" in str(excinfo.value)

    _run(_with_schema(body))


def test_riga_soppressa_ben_formata_viene_accettata():
    async def body(conn, snapshot_id):
        await conn.execute(
            """
            INSERT INTO metric_robustness
                (snapshot_id, layer, removal_fraction, is_suppressed, suppression_reason)
            VALUES ($1, 'voice', 0.10, TRUE, 'below_threshold')
            """,
            snapshot_id,
        )
        row = await conn.fetchrow(
            "SELECT * FROM metric_robustness WHERE snapshot_id = $1", snapshot_id
        )
        assert row["n_effective"] is None
        # NULL e non false: su una riga soppressa la metrica non e' stata
        # valutata affatto.
        assert row["is_significant"] is None
        # details resta '{}' e non NULL — l'unica eccezione al vincolo.
        assert row["details"] == "{}"

    _run(_with_schema(body))


def test_una_colonna_nuova_finisce_sotto_vincolo_senza_toccare_il_trigger():
    async def body(conn, snapshot_id):
        # E' la ragione per cui il vincolo e' un trigger che legge il catalogo e
        # non un CHECK che enumera le colonne: un elenco ci si dimentica di
        # aggiornarlo, e il suo fallimento e' silenzioso.
        await conn.execute(
            "ALTER TABLE metric_robustness ADD COLUMN colonna_futura INTEGER"
        )
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await conn.execute(
                """
                INSERT INTO metric_robustness
                    (snapshot_id, layer, removal_fraction, is_suppressed, colonna_futura)
                VALUES ($1, 'voice', 0.10, TRUE, 7)
                """,
                snapshot_id,
            )
        assert "colonna_futura" in str(excinfo.value)

    _run(_with_schema(body))


def test_riga_non_soppressa_puo_avere_valori():
    async def body(conn, snapshot_id):
        await conn.execute(
            """
            INSERT INTO metric_robustness
                (snapshot_id, layer, removal_fraction,
                 n_effective, nodes_removed, giant_before, is_significant)
            VALUES ($1, 'voice', 0.10, 9, 1, 1.0, FALSE)
            """,
            snapshot_id,
        )
        row = await conn.fetchrow(
            "SELECT * FROM metric_robustness WHERE snapshot_id = $1", snapshot_id
        )
        # Il caso reale di oggi: pubblicata e non significativa.
        assert (row["n_effective"], row["is_suppressed"], row["is_significant"]) == (
            9,
            False,
            False,
        )

    _run(_with_schema(body))


def test_suppression_reason_solo_su_righe_soppresse():
    async def body(conn, snapshot_id):
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                """
                INSERT INTO metric_robustness
                    (snapshot_id, layer, removal_fraction, is_suppressed, suppression_reason)
                VALUES ($1, 'voice', 0.10, FALSE, 'below_threshold')
                """,
                snapshot_id,
            )

    _run(_with_schema(body))
