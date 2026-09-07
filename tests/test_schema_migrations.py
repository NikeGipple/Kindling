"""``schema_migrations``: il ledger, e la convenzione che lo tiene vero.

"Quali migration sono applicate sulla droplet" era un fatto sul database
custodito FUORI dal database, scritto a mano altrove. Poteva divergere dalla
realta' senza che niente lo segnalasse — la stessa forma dei difetti che
`CLAUDE.md` §7 registra.

Il rimedio (migration `0012`) e' una tabella piu' una convenzione: ogni
migration, a partire da `0012`, finisce registrando il proprio nome file. La
convenzione da sola non basta — e' una frase in un commento finche' niente la
fa rispettare. Il test che conta in questo file e'
``test_ogni_file_di_migrations_ha_una_riga_nel_ledger``: confronta i file
REALMENTE presenti in ``migrations/`` con le righe scritte nel database dopo
averli applicati tutti. Una migration futura scritta senza la propria riga di
autoregistrazione fa fallire QUESTO test, invece di scoprirsi in produzione
mesi dopo cercando "e' stata applicata o no".

Non blocca ``pytest`` senza Postgres: si salta da solo. Per eseguirlo serve un
database di prova (mai quello di produzione — crea e distrugge uno schema):

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_schema_migrations.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_schema_migrations_test"


async def _con_tutte_le_migration_applicate(body):
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


def test_ogni_file_di_migrations_ha_una_riga_nel_ledger():
    # Il punto di tutta la convenzione: non un elenco atteso scritto qui a
    # mano (sarebbe la stessa lista-che-ci-si-dimentica-di-aggiornare di
    # CLAUDE.md 7), ma i file VERI presenti in migrations/ in questo momento.
    # Una nuova migration senza la propria riga di autoregistrazione lascia il
    # proprio filename fuori da schema_migrations, e questo test cade.
    attesi = {path.name for path in MIGRATIONS.glob("*.sql")}
    assert attesi, "nessun file di migration trovato: il test non prova nulla"

    async def body(conn):
        righe = await conn.fetch("SELECT filename FROM schema_migrations")
        return {r["filename"] for r in righe}

    registrati = asyncio.run(_con_tutte_le_migration_applicate(body))

    mancanti = attesi - registrati
    assert not mancanti, (
        f"migration senza autoregistrazione nel ledger: {sorted(mancanti)}"
    )
    # Anche il verso opposto: una riga nel ledger senza un file corrispondente
    # sarebbe un residuo di una migration rinominata o rimossa.
    fantasma = registrati - attesi
    assert not fantasma, f"righe nel ledger senza file corrispondente: {sorted(fantasma)}"


def test_riapplicare_0012_non_duplica_nessuna_riga():
    async def body(conn):
        prima = await conn.fetchval("SELECT count(*) FROM schema_migrations")

        migrazione_0012 = MIGRATIONS / "0012_schema_migrations.sql"
        await conn.execute(migrazione_0012.read_text(encoding="utf-8"))

        dopo = await conn.fetchval("SELECT count(*) FROM schema_migrations")
        assert dopo == prima, "riapplicare 0012 ha cambiato il numero di righe"

    asyncio.run(_con_tutte_le_migration_applicate(body))


def test_applied_at_non_si_muove_alla_riapplicazione():
    # ON CONFLICT DO NOTHING, non DO UPDATE: la data di applicazione e' quella
    # vera, la prima, non quella dell'ultima volta che qualcuno ha rilanciato
    # il file per sicurezza.
    async def body(conn):
        prima = await conn.fetchval(
            "SELECT applied_at FROM schema_migrations WHERE filename = '0001_raw_events.sql'"
        )

        migrazione_0012 = MIGRATIONS / "0012_schema_migrations.sql"
        await conn.execute(migrazione_0012.read_text(encoding="utf-8"))

        dopo = await conn.fetchval(
            "SELECT applied_at FROM schema_migrations WHERE filename = '0001_raw_events.sql'"
        )
        assert dopo == prima

    asyncio.run(_con_tutte_le_migration_applicate(body))
