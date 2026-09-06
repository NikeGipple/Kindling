"""Il perimetro dell'API e' imposto dal DATABASE, non dal codice che scrive query.

`docs/architettura/api.md` §4 giustifica il ruolo dedicato dicendo che con esso
"un endpoint scritto per errore contro una tabella interna fallisce con un errore
di permessi invece di funzionare". Questa e' la frase da provare: verificarla
altrove — leggendo `api/db.py` e constatando che non nomina `graph_edges` —
verificherebbe la convenzione, non la garanzia.

Non blocca ``pytest`` senza Postgres: si salta da solo. Per eseguirlo serve un
database di prova (mai quello di produzione — crea e distrugge uno schema e un
ruolo):

    export KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test
    pytest tests/test_api_role_schema.py

Serve un utente con privilegi di creazione ruoli: la migration crea
``kindling_api``. Nell'immagine ufficiale di Postgres il POSTGRES_USER lo e'.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_api_test"
ROLE = "kindling_api"

# Tabelle che l'API DEVE poter leggere: le sei metric_* piu' guilds. E' il
# criterio di api.md 1 applicato alle tabelle di oggi.
LEGGIBILI = (
    "guilds",
    "metric_runs",
    "metric_robustness",
    "metric_communities",
    "metric_community_sizes",
    "metric_cohorts",
    "metric_cohort_retention",
)

# Tabelle che l'API NON deve poter leggere, perche' contengono dati riferibili a
# una persona. members e' il caso non ovvio: sembra una tabella di date, ma la
# chiave e' (guild_id, author_id).
VIETATE = (
    "raw_events",
    "members",
    "graph_edges",
    "graph_snapshots",
    "voice_sessions",
    "voice_session_participants",
    "message_authors",
)


def _api_dsn(password: str) -> str:
    """Lo stesso database, ma come ``kindling_api``."""
    parts = urlsplit(DSN)
    netloc = f"{ROLE}:{password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _with_role(body):
    """Applica le migration in uno schema temporaneo e apre una connessione API.

    La password del ruolo e' generata qui, casuale e usa e getta: la migration
    non ne contiene nessuna (una migration sta in git), e questo test non
    introduce una credenziale fissa da nessuna parte.
    """
    password = secrets.token_urlsafe(24)
    owner = await asyncpg.connect(dsn=DSN)
    api = None
    try:
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await owner.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await owner.execute(f'SET search_path TO "{SCHEMA}"')

        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name):
            await owner.execute(path.read_text(encoding="utf-8"))
        # Idempotenza: rilanciate due volte non rompono niente, ed e' cosi' che
        # vengono applicate a mano sulla droplet.
        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name):
            await owner.execute(path.read_text(encoding="utf-8"))

        await owner.execute(f"ALTER ROLE {ROLE} PASSWORD '{password}'")

        api = await asyncpg.connect(dsn=_api_dsn(password))
        await api.execute(f'SET search_path TO "{SCHEMA}"')
        return await body(owner, api)
    finally:
        if api is not None:
            await api.close()
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        # Il ruolo e' globale al database, non allo schema: va tolto o
        # resterebbe in giro tra un'esecuzione e l'altra.
        await owner.execute(f"DROP ROLE IF EXISTS {ROLE}")
        await owner.close()


def test_il_ruolo_non_puo_leggere_le_tabelle_con_dati_personali():
    async def body(owner, api):
        for tabella in VIETATE:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await api.fetch(f"SELECT * FROM {tabella} LIMIT 1")

    asyncio.run(_with_role(body))


def test_il_ruolo_puo_leggere_le_tabelle_di_metrica_e_guilds():
    async def body(owner, api):
        for tabella in LEGGIBILI:
            # Non deve sollevare: il contenuto non interessa, il permesso si'.
            await api.fetch(f"SELECT * FROM {tabella} LIMIT 1")

    asyncio.run(_with_role(body))


def test_il_ruolo_non_puo_scrivere_sulle_tabelle_di_metrica():
    async def body(owner, api):
        # SELECT non implica INSERT: l'API legge e basta.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute(
                "INSERT INTO guilds (guild_id, first_seen_at) VALUES (1, now())"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("DELETE FROM metric_runs")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("UPDATE metric_robustness SET layer = 'x'")

    asyncio.run(_with_role(body))


def test_il_ruolo_legge_davvero_una_riga_scritta_dal_proprietario():
    async def body(owner, api):
        await owner.execute(
            "INSERT INTO guilds (guild_id, first_seen_at) VALUES (111, now())"
        )
        assert await api.fetchval(
            "SELECT guild_id FROM guilds WHERE guild_id = 111"
        ) == 111

    asyncio.run(_with_role(body))


def test_una_tabella_nuova_non_e_leggibile_senza_un_grant_esplicito():
    async def body(owner, api):
        # E' la conseguenza voluta di non usare ALTER DEFAULT PRIVILEGES: una
        # tabella aggiunta domani non e' leggibile finche' non le si da' il
        # GRANT, e il sintomo e' un errore di permessi chiaro. L'alternativa
        # renderebbe leggibile in automatico anche una tabella interna.
        await owner.execute("CREATE TABLE tabella_futura (id INTEGER)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetch("SELECT * FROM tabella_futura")

    asyncio.run(_with_role(body))
