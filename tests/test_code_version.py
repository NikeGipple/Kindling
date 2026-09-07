"""``code_version`` e' NULL per tutte le run da sempre: verificato in produzione.

`_code_version()` (job/main.py) prova `KINDLING_CODE_VERSION` e poi
`git rev-parse --short HEAD`. Nel container il repository git non c'e' (vedi
`.dockerignore`), quindi il ripiego non scatta mai: e' la variabile d'ambiente
o e' `None`. Sulla droplet la variabile non e' mai stata valorizzata, quindi
ogni riga di `metric_runs` (e di `graph_snapshots`) ha `code_version = NULL` da
sempre — scoperto durante il deploy del fix di `previous_gap_days`, cercando in
`metric_runs` quale codice avesse scritto cosa.

**La versione arriva ora dall'IMMAGINE, non da `.env`.** Un primo tentativo
(06/09/2026) l'aveva messa in `.env`, letta da `env_file: - .env` come
`KINDLING_PSEUDONYM_SALT` — ma `code_version` risponde a "quale codice ha
prodotto questi numeri", e la risposta corretta e' il codice CHE STA
NELL'IMMAGINE in esecuzione, non l'ultimo commit fatto `git pull` sulla
droplet: dopo un pull senza rebuild i due possono divergere, ed e' esattamente
lo scenario del deploy del 07/09/2026 (`docker compose build` che salta `job`,
vedi `CLAUDE.md` §7). Un `.env` avrebbe anche potuto mentire nel verso
peggiore: `env_file`/`environment` vincono SEMPRE sul valore incastonato
nell'immagine con l'`ARG` (`Dockerfile`), quindi una riga `KINDLING_CODE_VERSION=`
anche vuota in `.env` avrebbe sovrascritto in silenzio il commit vero con una
stringa vuota. Per questo `.env.example` non la elenca piu' (con un commento
che spiega perche', non un'assenza silenziosa), e `ops/kindling-deploy.sh` la
calcola da `git rev-parse --short HEAD` e la passa come build-arg
(`docker-compose.yml`, `job.build.args`).

`_code_version()` non e' cambiata: legge ancora `os.environ.get(...)`, perche'
l'`ENV` scritto dal `Dockerfile` a partire dall'`ARG` e' comunque una variabile
d'ambiente del processo dentro il container — cambia solo CHI la imposta.

Qui si prova `_code_version()` (nessun database), che `write_metrics` persista
quello che gli viene passato (Postgres reale, si salta senza
`KINDLING_TEST_DATABASE_URL`), e la forma di `Dockerfile`/`docker-compose.yml`/
`.env.example` che tiene i due meccanismi separati.
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

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "migrations"
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
    # ``KINDLING_CODE_VERSION=""`` (l'ARG del Dockerfile senza build-arg
    # passato, o una riga vuota reintrodotta per errore in .env) e' uno stato
    # concreto e non solo teorico: os.environ la vede come stringa vuota, non
    # come chiave assente, e va trattata uguale.
    monkeypatch.setenv("KINDLING_CODE_VERSION", "")

    def _fallisce_come_senza_git(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(main.subprocess, "check_output", _fallisce_come_senza_git)

    assert main._code_version() is None


# --- Dockerfile / docker-compose.yml / .env.example: i due meccanismi non si
# sovrappongono -------------------------------------------------------------


def test_dockerfile_incide_kindling_code_version_dopo_i_copy():
    # ARG e ENV devono stare DOPO i tre COPY: e' quello che fa invalidare solo
    # l'ultimo layer quando cambia il commit, non anche `pip install` e i COPY,
    # che restano in cache tra un deploy e l'altro.
    testo = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG KINDLING_CODE_VERSION=" in testo
    assert "ENV KINDLING_CODE_VERSION=$KINDLING_CODE_VERSION" in testo

    posizione_arg = testo.index("ARG KINDLING_CODE_VERSION=")
    ultimo_copy = max(
        testo.index(riga)
        for riga in ("COPY bot/ ./bot/", "COPY job/ ./job/", "COPY api/ ./api/")
    )
    assert posizione_arg > ultimo_copy, (
        "ARG KINDLING_CODE_VERSION prima di un COPY: cambiare commit "
        "invaliderebbe anche i layer del codice, non solo il proprio"
    )


def test_docker_compose_passa_kindling_code_version_come_build_arg_al_job():
    import yaml

    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))

    build = compose["services"]["job"]["build"]
    assert build["args"]["KINDLING_CODE_VERSION"] == "${KINDLING_CODE_VERSION:-}"


def test_env_example_non_contiene_piu_la_riga_kindling_code_version():
    # Non un'assenza casuale: e' la trappola descritta in cima a questo file.
    # Una riga KINDLING_CODE_VERSION= (anche vuota) tornata in .env.example
    # verrebbe copiata nei nuovi .env e vincerebbe in silenzio sul valore
    # incastonato nell'immagine (env_file vince sempre su ARG/ENV del
    # Dockerfile) — esattamente il difetto gia' visto una volta.
    righe = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()

    assert not any(riga.startswith("KINDLING_CODE_VERSION=") for riga in righe), (
        "KINDLING_CODE_VERSION e' tornata in .env.example: sovrascriverebbe "
        "in silenzio il valore incastonato nell'immagine"
    )
    # Il commento che spiega perche' non c'e' deve restare, non solo la riga
    # deve mancare: un'assenza senza spiegazione e' quella che si reintroduce
    # per errore alla prima occasione.
    assert "KINDLING_CODE_VERSION" in "\n".join(righe), (
        "manca anche la spiegazione: qualcuno la rimettera' senza sapere perche' non c'era"
    )


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
