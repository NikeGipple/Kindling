"""``code_version`` e' NULL per tutte le run da sempre: verificato in produzione.

`_code_version()` (job/main.py) prova `KINDLING_CODE_VERSION` e poi
`git rev-parse --short HEAD`. Nel container il repository git non c'e' (vedi
`.dockerignore`), quindi il ripiego non scatta mai: e' la variabile d'ambiente
o e' `None`. Sulla droplet la variabile non e' mai stata valorizzata nel
`.env`, quindi ogni riga di `metric_runs` (e di `graph_snapshots`) ha
`code_version = NULL` da sempre — scoperto durante il deploy del fix di
`previous_gap_days`, cercando in `metric_runs` quale codice avesse scritto
cosa.

Non e' un bug di `docker-compose.yml`: `env_file: - .env` sui servizi `job` e
`api` carica GIA' ogni variabile di `.env` nel container, `KINDLING_CODE_VERSION`
inclusa — e' lo stesso meccanismo che gia' porta `KINDLING_PSEUDONYM_SALT` al
job senza che compaia nella sezione `environment:`. Il gap era solo che sulla
droplet quella riga di `.env` e' rimasta vuota, e nessun passo del deploy la
scriveva: a differenza di un segreto impostato una volta, il valore giusto
CAMBIA a ogni deploy (e' l'hash del commit), quindi il modello "si imposta una
volta in `.env` e non si tocca piu'" non le si applica.

Qui si prova solo `_code_version()` (nessun database) e che `write_metrics`
persista quello che gli viene passato (Postgres reale, si salta senza
`KINDLING_TEST_DATABASE_URL`). Il passo di deploy che tiene il valore aggiornato
sta in `runbook-droplet.md`, e non e' verificabile da un test.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job import main

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg non installato")

from job import db  # noqa: E402  (dopo importorskip: job.db importa asyncpg)

DSN = os.environ.get("KINDLING_TEST_DATABASE_URL")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "kindling_code_version_test"

GUILD = 626262
AS_OF = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


# --- _code_version(): nessun database ---------------------------------------


def test_variabile_impostata_vince_e_git_non_viene_nemmeno_chiamato(monkeypatch):
    # Il caso normale in produzione: se la variabile c'e', il ripiego su git
    # non serve e non deve scattare — e' quello che protegge dal caso reale
    # (nessun repository nel container) senza doverlo simulare.
    monkeypatch.setenv("KINDLING_CODE_VERSION", "abc1234")

    def _mai_chiamato(*args, **kwargs):
        raise AssertionError("git non doveva essere invocato: la variabile c'era gia'")

    monkeypatch.setattr(main.subprocess, "check_output", _mai_chiamato)

    assert main._code_version() == "abc1234"


def test_senza_variabile_ripiega_su_git(monkeypatch):
    monkeypatch.delenv("KINDLING_CODE_VERSION", raising=False)
    monkeypatch.setattr(
        main.subprocess,
        "check_output",
        lambda *a, **k: "deadbee\n",
    )

    assert main._code_version() == "deadbee"


def test_senza_variabile_e_senza_git_e_none(monkeypatch):
    # E' esattamente il caso del container: .dockerignore esclude .git, quindi
    # `git rev-parse` fallisce (repository non trovato) e non deve far cadere
    # il job — deve solo tornare None, che e' cio' che si e' visto in
    # produzione su tutte le run.
    monkeypatch.delenv("KINDLING_CODE_VERSION", raising=False)

    def _fallisce_come_senza_git(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(main.subprocess, "check_output", _fallisce_come_senza_git)

    assert main._code_version() is None


def test_variabile_vuota_e_come_assente(monkeypatch):
    # ``KINDLING_CODE_VERSION=`` in .env (la riga c'e', il valore no) e' lo
    # stato in cui la droplet e' rimasta: os.environ la vede come stringa
    # vuota, non come chiave assente, e va trattata uguale — altrimenti il
    # sintomo (code_version sempre NULL) non avrebbe la causa che ha davvero.
    monkeypatch.setenv("KINDLING_CODE_VERSION", "")

    def _fallisce_come_senza_git(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(main.subprocess, "check_output", _fallisce_come_senza_git)

    assert main._code_version() is None


# --- write_metrics persiste code_version: Postgres reale --------------------
#
# Un decoratore per funzione, non un `pytestmark` di modulo: quest'ultimo
# salterebbe anche i quattro test di `_code_version()` sopra, che non toccano
# Postgres e devono girare sempre.
_richiede_db = pytest.mark.skipif(
    not DSN,
    reason="KINDLING_TEST_DATABASE_URL non impostata: test di schema saltato",
)


def _edge():
    from job.config import LAYER_VOICE
    from job.edges import Edge

    return Edge(
        layer=LAYER_VOICE,
        src_author_id=1,
        dst_author_id=2,
        weight=1.0,
        weight_undecayed=1.0,
        raw_units=1.0,
        interaction_count=1,
        last_interaction_at=AS_OF - timedelta(hours=1),
    )


async def _con_uno_snapshot(owner, *, code_version_snapshot="test"):
    """Uno snapshot gia' scritto: metric_runs ha bisogno di un id a cui appoggiarsi."""
    return await db.write_snapshot(
        owner,
        guild_id=GUILD,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=7),
        window_end=AS_OF,
        params={},
        stats={},
        code_version=code_version_snapshot,
        edges=[_edge()],
        sessions=[],
    )


async def _in_uno_schema_usa_e_getta(body):
    owner = await asyncpg.connect(dsn=DSN)
    try:
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await owner.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await owner.execute(f'SET search_path TO "{SCHEMA}"')

        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name):
            await owner.execute(path.read_text(encoding="utf-8"))

        return await body(owner)
    finally:
        await owner.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await owner.close()


@_richiede_db
def test_write_metrics_persiste_code_version_quando_impostata():
    async def body(conn):
        snapshot_id = await _con_uno_snapshot(conn)

        await db.write_metrics(
            conn,
            snapshot_id=snapshot_id,
            guild_id=GUILD,
            as_of=AS_OF,
            params={},
            stats={},
            code_version="cafe123",
            robustness=[],
            communities=[],
            community_sizes=[],
            cohorts=[],
            cohort_retention=[],
        )

        assert await conn.fetchval(
            "SELECT code_version FROM metric_runs WHERE snapshot_id = $1", snapshot_id
        ) == "cafe123"

    asyncio.run(_in_uno_schema_usa_e_getta(body))


@_richiede_db
def test_write_metrics_senza_code_version_scrive_null_e_non_un_valore_vecchio():
    # Il verso pericoloso non e' "scrive NULL la prima volta" — e' atteso,
    # riproduce esattamente il difetto visto in produzione — ma "una riscrittura
    # senza code_version cancella silenziosamente quello buono di prima": qui si
    # verifica che l'UPSERT lo aggiorni davvero, in entrambe le direzioni, e non
    # lasci sopravvivere un valore vecchio che diventerebbe fuorviante.
    async def body(conn):
        snapshot_id = await _con_uno_snapshot(conn)

        await db.write_metrics(
            conn,
            snapshot_id=snapshot_id,
            guild_id=GUILD,
            as_of=AS_OF,
            params={},
            stats={},
            code_version="prima",
            robustness=[],
            communities=[],
            community_sizes=[],
            cohorts=[],
            cohort_retention=[],
        )
        await db.write_metrics(
            conn,
            snapshot_id=snapshot_id,
            guild_id=GUILD,
            as_of=AS_OF,
            params={},
            stats={},
            code_version=None,
            robustness=[],
            communities=[],
            community_sizes=[],
            cohorts=[],
            cohort_retention=[],
        )

        assert await conn.fetchval(
            "SELECT code_version FROM metric_runs WHERE snapshot_id = $1", snapshot_id
        ) is None

    asyncio.run(_in_uno_schema_usa_e_getta(body))
