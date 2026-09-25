"""I tre parametri del grafo, dalla vista di Postgres alla griglia di Stato.

`decay_half_life_days`, `decay_cutoff_days` e `min_overlap_minutes` stanno in
`graph_snapshots.params`. Fino al 22/09/2026 la vista Stato non poteva mostrarli
perche' `graph_snapshots` non e' leggibile da `kindling_api`; dal 25/09/2026 lo
puo' fare attraverso la vista stretta `graph_snapshot_params` (migration `0014`).

I test stanno insieme perche' la garanzia e' la CATENA, non un anello: la vista
espone tre colonne, il GRANT e' solo sulla vista, l'API li serve tipizzati, e la
dashboard li legge da li' invece che da `job/config.py`. Spezzarli su quattro
file nasconderebbe che a reggere e' la sequenza — e ognuno dei quattro anelli, se
cede, cede in silenzio:

1. **Un GRANT su `graph_snapshots`** renderebbe leggibile una tabella intera
   invece di tre colonne, e nessun endpoint fallirebbe: funzionerebbe *meglio*.
   E' il motivo per cui il controllo e' sul TESTO delle migration e non sui
   permessi di un database che qui non c'e' (`tests/test_api_role_schema.py` fa
   l'altra meta', ma si salta senza Postgres — cioe' quasi sempre).
2. **Un campo non tipizzato** li rimetterebbe in un dizionario accanto a
   `metric_runs.params`, indistinguibile da cio' che `api.md` §3 dichiara
   diagnostica.
3. **Un valore letto da `job/config.py`** invece che dalla risposta dell'API
   mostrerebbe la configurazione di oggi su una run di settimane fa, e sarebbe
   giusto per caso finche' nessuno cambia il job.
4. **L'avviso "metodo di calcolo aggiornato"** deve continuare a guardare solo
   `metric_runs.params`: `MetricParams` e `GraphParams` sono separati in
   `job/config.py` proprio perche' i primi possano cambiare senza rendere
   incomparabili gli snapshot, e far scattare l'avviso su un cambio del grafo
   rimetterebbe insieme cio' che quella divisione tiene separato.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from api import assemble
from api.models import GraphParamsValues, RunRow
from dashboard import config, regole, stato
from dashboard.main import crea_app
from job.config import DEFAULT_PARAMS
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tools.fixture_api import GUILD_EDGE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
VISTA = "graph_snapshot_params"
COLONNE = ("decay_half_life_days", "decay_cutoff_days", "min_overlap_minutes")

ADESSO = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture(autouse=True)
def _orologio_fermo(monkeypatch):
    monkeypatch.setattr(stato, "adesso", lambda: ADESSO)


def _sql_senza_commenti(testo: str) -> str:
    return "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("--"))


def _tutte_le_migration() -> dict[str, str]:
    return {
        p.name: _sql_senza_commenti(p.read_text(encoding="utf-8"))
        for p in sorted(MIGRATIONS.glob("*.sql"))
    }


# --- 1. il permesso: la vista si', la tabella no -----------------------------


def test_nessuna_migration_da_a_kindling_api_un_permesso_su_graph_snapshots():
    """Il controllo che regge anche senza Postgres, cioe' quasi sempre.

    ``tests/test_api_role_schema.py`` prova la stessa cosa sul database vero, ma
    si salta senza ``KINDLING_TEST_DATABASE_URL``: un GRANT aggiunto per comodita'
    non incontrerebbe nessun controllo prima della droplet.
    """
    for nome, sql in _tutte_le_migration().items():
        for grant in re.findall(r"GRANT\s+[\w\s,]+?\s+ON\s+([^\s;]+)\s+TO\s+kindling_api", sql):
            assert grant != "graph_snapshots", f"{nome}: GRANT su graph_snapshots"
        # Anche nella forma "ON ALL TABLES IN SCHEMA", che prenderebbe tutto.
        assert not re.search(
            r"GRANT[^;]*ON\s+ALL\s+TABLES[^;]*TO\s+kindling_api", sql, re.I | re.S
        ), f"{nome}: GRANT su tutte le tabelle"


def test_la_0014_concede_la_vista_e_soltanto_quella():
    sql = _tutte_le_migration()["0014_graph_snapshot_params.sql"]
    concessi = re.findall(r"GRANT\s+SELECT\s+ON\s+([^\s;]+)\s+TO\s+kindling_api", sql)

    assert concessi == [VISTA]
    # E il ledger: una migration senza la propria riga e' incompleta (CLAUDE.md).
    assert "INSERT INTO schema_migrations (filename) VALUES ('0014_graph_snapshot_params.sql')" in sql


def test_la_vista_espone_tre_colonne_e_lo_snapshot_e_nient_altro():
    """Superficie DECISA: aggiungere un parametro deve costare una migration.

    Con ``SELECT *`` o con il blob ``params`` intero, una colonna aggiunta domani
    a ``graph_snapshots`` diventerebbe leggibile dall'API senza che nessuno
    l'abbia deciso — la stessa ragione per cui ``0010`` scrive i GRANT tabella
    per tabella invece di usare ALTER DEFAULT PRIVILEGES.
    """
    sql = _tutte_le_migration()["0014_graph_snapshot_params.sql"]
    corpo = sql[sql.index("CREATE OR REPLACE VIEW"):sql.index("COMMENT ON VIEW")]
    # Dopo la SELECT: l'"AS" di "CREATE VIEW ... AS SELECT" non e' un alias di
    # colonna, e contarlo farebbe passare il test per la ragione sbagliata.
    proiezione = corpo[corpo.index("SELECT"):]

    assert "SELECT *" not in corpo
    assert "params ->>" in corpo  # estrae chiavi, non serve il blob
    alias = set(re.findall(r"AS\s+(\w+)", proiezione))
    assert alias == {"snapshot_id", *COLONNE}
    # Il filtro di forma: una chiave scritta come stringa da un codice futuro
    # esce NULL invece di far fallire il cast dentro una query dell'API.
    assert corpo.count("jsonb_typeof") == len(COLONNE)


def test_la_query_delle_run_usa_la_vista_e_non_la_tabella():
    sorgente = (Path(__file__).resolve().parent.parent / "api" / "db.py").read_text(
        encoding="utf-8"
    )
    funzione = sorgente[sorgente.index("async def fetch_runs"):sorgente.index("# ---- metriche")]
    # Solo il SQL: la docstring della funzione NOMINA graph_snapshots per dire
    # che non e' leggibile, ed e' il posto giusto per dirlo.
    query = funzione[funzione.index('"""', funzione.index('"""') + 3):]

    assert VISTA in query
    assert "graph_snapshots" not in query.replace(VISTA, "")
    # LEFT JOIN: una run il cui snapshot non c'e' piu' resta nello storico.
    assert "LEFT JOIN" in query


# --- 2. l'API li serve tipizzati ---------------------------------------------


def test_i_tre_parametri_sono_campi_tipizzati_non_un_secondo_dizionario():
    campi = GraphParamsValues.model_fields if hasattr(GraphParamsValues, "model_fields") else GraphParamsValues.__fields__
    assert set(campi) == set(COLONNE)

    riga = assemble.run(
        {
            "snapshot_id": 1,
            "as_of": ADESSO,
            "decay_half_life_days": 7.0,
            "decay_cutoff_days": 30.0,
            "min_overlap_minutes": 5.0,
        }
    )
    assert riga.graph_params.decay_half_life_days == 7.0
    # E NON dentro params: i due blocchi restano separati nella risposta.
    assert riga.params == {}


def test_graph_params_c_e_sempre_anche_quando_i_valori_non_ci_sono():
    """Come ``values`` su una riga soppressa: assente e vuoto non si confondono.

    Se il campo sparisse dalla risposta quando lo snapshot non c'e' piu', a
    livello di JSON "non lo so" somiglierebbe a "non esiste il concetto".
    """
    riga = assemble.run({"snapshot_id": 1, "as_of": ADESSO})

    assert riga.graph_params is not None
    assert riga.graph_params.decay_half_life_days is None


# --- 3. la dashboard li legge dall'API, non da job/config.py -----------------


def _api_con(graph_params: dict, params: dict) -> httpx.MockTransport:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(
                200,
                json=[
                    RunRow(
                        snapshot_id=2,
                        as_of=ADESSO - timedelta(days=1),
                        params=params,
                        graph_params=GraphParamsValues(**graph_params),
                    ).model_dump(mode="json"),
                    RunRow(
                        snapshot_id=1,
                        as_of=ADESSO - timedelta(days=8),
                        params=params,
                        graph_params=GraphParamsValues(**graph_params),
                    ).model_dump(mode="json"),
                ],
            )
        for coda in ("/robustness", "/communities", "/cohorts"):
            if request.url.path.endswith(coda):
                return httpx.Response(200, json=[])
        return httpx.Response(
            200, json={"guild_id": 5, "first_seen_at": "2026-08-01T00:00:00Z"}
        )

    return httpx.MockTransport(api)


def _pagina(transport: httpx.MockTransport) -> str:
    http = httpx.AsyncClient(transport=transport, base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        return client.get("/guilds/5").text


def test_i_valori_mostrati_vengono_dall_api_e_non_da_job_config():
    """Il test che distingue le due sorgenti, ed e' l'unico che puo' farlo.

    Contro il fixture i due insiemi coincidono — il fixture DERIVA i suoi valori
    da ``job/config.py``, come CLAUDE.md impone alle sue costanti — quindi una
    dashboard che leggesse la configurazione locale passerebbe lo stesso. Qui i
    valori sono deliberatamente DIVERSI da quelli del job: se la pagina mostra i
    numeri del job, li sta leggendo dal posto sbagliato.
    """
    diversi = {
        "decay_half_life_days": 3.0,
        "decay_cutoff_days": 12.0,
        "min_overlap_minutes": 2.0,
    }
    assert diversi["decay_half_life_days"] != DEFAULT_PARAMS.decay_half_life_days
    assert diversi["min_overlap_minutes"] != DEFAULT_PARAMS.min_overlap_minutes

    html = _pagina(_api_con(diversi, {}))
    regola = html[html.index("memoria delle interazioni") - 400 : html.index("</ul>")]

    assert "3 · 12 giorni" in regola
    assert "2 minuti" in regola
    # I numeri del job non compaiono da nessuna parte nella griglia.
    griglia = html[html.index('<ul class="regole"'):html.index("</ul>", html.index('<ul class="regole"'))]
    assert "7 · 30 giorni" not in griglia
    assert "5 minuti" not in griglia


def test_le_frasi_si_compongono_dai_valori():
    diversi = {
        "decay_half_life_days": 3.0,
        "decay_cutoff_days": 12.0,
        "min_overlap_minutes": 2.0,
    }
    (memoria,) = [
        r for r in regole.costruisci({}, GraphParamsValues(**diversi)) if r.codice == "memoria"
    ]
    (vocale,) = [
        r
        for r in regole.costruisci({}, GraphParamsValues(**diversi))
        if r.codice == "sovrapposizione_vocale"
    ]

    assert "metà dopo 3 giorni" in memoria.spiegazione
    assert "dopo 12 giorni" in memoria.spiegazione
    assert "almeno 2 minuti" in vocale.spiegazione


@pytest.mark.parametrize(
    "graph_params, attese",
    [
        ({}, set()),
        ({"min_overlap_minutes": 5.0}, {"sovrapposizione_vocale"}),
        # L'emivita senza il cutoff non basta: la regola dice due numeri, e
        # mostrarne uno solo direbbe una soglia dove ce ne sono due.
        ({"decay_half_life_days": 7.0}, set()),
        (
            {"decay_half_life_days": 7.0, "decay_cutoff_days": 30.0},
            {"memoria"},
        ),
    ],
)
def test_un_valore_mancante_fa_sparire_la_regola(graph_params, attese):
    codici = {
        r.codice
        for r in regole.costruisci({}, GraphParamsValues(**graph_params))
    }
    assert codici == attese


def test_una_run_senza_snapshot_non_mostra_le_due_regole(dashboard_fixture):
    """GUILD_EDGE: la run non porta i parametri del grafo, come una scritta da
    un codice che non li registrava o il cui snapshot e' stato cancellato."""
    html = dashboard_fixture.get(f"/guilds/{GUILD_EDGE}").text

    assert "memoria delle interazioni" not in html
    assert "tempo minimo insieme in vocale" not in html
    # Le regole che vengono da params invece ci sono: le due assenze sono
    # indipendenti.
    assert "rete minima" in html


@pytest.fixture
def dashboard_fixture():
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture_app), base_url="http://api.test"
    )
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        yield client


def test_il_fixture_deriva_i_parametri_del_grafo_da_job_config(dashboard_fixture):
    """Nel fixture i valori DEVONO coincidere con job/config.py (CLAUDE.md: una
    costante ricopiata a mano diverge in silenzio). E' proprio questa coincidenza
    che rende necessario il test con i valori divergenti, qui sopra."""
    html = dashboard_fixture.get(f"/guilds/{GUILD_TODAY}").text

    emivita = regole.numero(DEFAULT_PARAMS.decay_half_life_days)
    cutoff = regole.numero(DEFAULT_PARAMS.decay_cutoff_days)
    assert f"{emivita} · {cutoff} giorni" in html
    assert f"{regole.numero(DEFAULT_PARAMS.min_overlap_minutes)} minuti" in html


# --- 4. l'avviso guarda solo metric_runs.params ------------------------------


def test_un_cambio_dei_parametri_del_grafo_non_fa_scattare_l_avviso():
    """La divisione di ``job/config.py``, difesa a valle.

    ``MetricParams`` e ``GraphParams`` sono separati perche' i parametri delle
    metriche possano cambiare senza rendere incomparabili gli snapshot del
    grafo. Se l'avviso "metodo di calcolo aggiornato" scattasse su un cambio del
    grafo, la dashboard direbbe "non confrontare i numeri prima di questa data"
    per un cambiamento che quei numeri non li tocca.
    """
    fissi = {"min_cardinality": 5}
    prima = RunRow(
        snapshot_id=1,
        as_of=ADESSO - timedelta(days=7),
        params=fissi,
        graph_params=GraphParamsValues(decay_half_life_days=7.0),
    )
    dopo = RunRow(
        snapshot_id=2,
        as_of=ADESSO,
        params=fissi,
        graph_params=GraphParamsValues(decay_half_life_days=14.0),
    )

    assert stato.cambio_di_parametri([dopo, prima]) is None

    # Il controllo non passa perche' non distingue niente: con params diversi
    # l'avviso scatta ancora.
    dopo_con_params = RunRow(
        snapshot_id=2, as_of=ADESSO, params={"min_cardinality": 8},
        graph_params=GraphParamsValues(decay_half_life_days=7.0),
    )
    assert stato.cambio_di_parametri([dopo_con_params, prima]) == dopo_con_params.as_of


def test_l_avviso_non_compare_sulla_pagina_per_un_cambio_del_grafo():
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(
                200,
                json=[
                    RunRow(
                        snapshot_id=2, as_of=ADESSO, params={"min_cardinality": 5},
                        graph_params=GraphParamsValues(decay_half_life_days=14.0,
                                                       decay_cutoff_days=60.0),
                    ).model_dump(mode="json"),
                    RunRow(
                        snapshot_id=1, as_of=ADESSO - timedelta(days=7),
                        params={"min_cardinality": 5},
                        graph_params=GraphParamsValues(decay_half_life_days=7.0,
                                                       decay_cutoff_days=30.0),
                    ).model_dump(mode="json"),
                ],
            )
        for coda in ("/robustness", "/communities", "/cohorts"):
            if request.url.path.endswith(coda):
                return httpx.Response(200, json=[])
        return httpx.Response(
            200, json={"guild_id": 5, "first_seen_at": "2026-08-01T00:00:00Z"}
        )

    html = _pagina(httpx.MockTransport(api))

    assert "Metodo di calcolo aggiornato" not in html
    # Ma i valori nuovi si vedono: il cambio c'e' stato, non e' un avviso.
    assert "14 · 60 giorni" in html
