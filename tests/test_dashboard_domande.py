"""Le due pagine nuove: Domande e Dettagli tecnici (dashboard.md 4).

Quattro cose che, sbagliate, non darebbero nessun errore — e sono la ragione per
cui questo file esiste.

1. **Gli ``id`` delle domande sono l'indirizzo dei rimandi delle viste.** Un
   ``id`` rinominato non rompe niente: il link atterra in cima alla pagina, e chi
   ci arriva non sa di aver perso la risposta. Il legame fra i due posti lo
   guarda ``tests/test_dashboard_stato.py``; qui si guarda che gli ``id`` ci
   siano tutti e che siano quelli della mappa.
2. **Le risposte sono generiche.** Nessuna data di *questo* server, nessun nome:
   una risposta che nomina il 28 agosto e' giusta per un server e falsa per il
   successivo, e nessuno se ne accorge finche' il secondo server non esiste.
3. **Le soglie citate vengono da ``params``**, con la stessa regola di Stato:
   chiave mancante, frase che non compare. Una soglia battuta a mano resterebbe
   giusta finche' nessuno cambia il job.
4. **I Dettagli tecnici non perdono un parametro per strada.** Il
   raggruppamento per area e' una mappa di etichette, non un filtro: una chiave
   nuova del job finisce sotto "Altri parametri" e si vede. Il test che conta e'
   quello che fallisce quando il job ne aggiunge una — cosi' la decisione su dove
   metterla si prende, invece di non prendersi.
"""

from __future__ import annotations

import re

import httpx
import pytest

from dashboard import config, domande, regole
from dashboard.main import SITO_PUBBLICO, crea_app
from job.config import MetricParams
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

PARAMS_COMPLETI = MetricParams().as_run_params()


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def dashboard():
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture_app), base_url="http://api.test"
    )
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        yield client


def _corpo_domande(html: str) -> str:
    return html[html.index('<div class="domande"'):html.index("</main>")]


# --- la pagina Domande --------------------------------------------------------


def test_le_nove_domande_hanno_un_id_stabile_e_sono_aperte(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text
    corpo = _corpo_domande(html)

    trovati = re.findall(r'<details id="([\w-]+)" open>', corpo)
    assert trovati == list(domande.TITOLI)
    # Aperte, e non e' una svista: senza JavaScript non esiste un modo di aprire
    # quella raggiunta da un'ancora, e un rimando che atterra su una risposta
    # invisibile fallisce proprio nel compito per cui gli id esistono.
    assert corpo.count("<details") == corpo.count(" open>") == 9
    # Ogni domanda ha almeno un paragrafo: un <details> vuoto sarebbe una
    # domanda senza risposta, e nessun errore lo direbbe.
    for blocco in corpo.split("<details")[1:]:
        assert "<p>" in blocco


def test_il_titolo_del_riassunto_e_quello_della_mappa(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text
    riassunti = re.findall(r"<summary>(.*?)</summary>", _corpo_domande(html))
    import html as _html

    assert [_html.unescape(t) for t in riassunti] == list(domande.TITOLI.values())


def test_le_risposte_non_nominano_nessuna_data_di_questo_server(dashboard):
    """Generiche vuol dire che la stessa pagina vale per il server successivo."""
    corpo = _corpo_domande(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)

    # Nessun anno, nessun mese: dove servirebbe una data si rimanda a Stato.
    assert not re.search(r"\b20\d\d\b", corpo)
    for mese in ("gennaio", "agosto", "settembre", "ago", "set"):
        assert not re.search(rf"\b{mese}\b", corpo), mese
    assert "in cima a Stato" in corpo


def test_la_prima_domanda_nomina_il_destinatario(dashboard):
    """"gli amministratori vedono solo gruppi", non "Kindling non sa chi sei".

    La seconda frase sarebbe falsa: il bot registra ``author_id``
    pseudonimizzati. L'invariante riguarda cio' che ESCE da qui.
    """
    corpo = _corpo_domande(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)
    prima = corpo[corpo.index('id="q-persone"'):corpo.index('id="q-mancanti"')]

    assert "amministratori vedono" in prima
    assert "non sa" not in prima


def test_le_soglie_citate_vengono_da_params_e_spariscono_se_mancano():
    p = MetricParams()
    complete = domande.costruisci(1, PARAMS_COMPLETI, privacy_url="https://esempio")
    testo = " ".join(par for d in complete for par in d.paragrafi)
    assert f"{p.min_cardinality} membri" in testo
    assert f"{p.min_nodes_structural} persone attive" in testo

    # Un valore diverso da quello del job: la frase lo segue.
    diversi = domande.costruisci(
        1, {**PARAMS_COMPLETI, "min_cardinality": 9}, privacy_url="https://esempio"
    )
    assert "9 membri" in " ".join(diversi[0].paragrafi)

    # Chiave mancante: la frase che la nominava non c'e', e la domanda resta.
    senza = domande.costruisci(1, {}, privacy_url="https://esempio")
    assert len(senza) == len(complete)
    assert "La soglia è" not in " ".join(senza[0].paragrafi)
    assert senza[0].paragrafi  # la risposta non sparisce con la soglia


def test_senza_run_le_domande_si_rendono_lo_stesso():
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"guild_id": 5, "first_seen_at": "2026-08-28T00:00:00Z"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(api), base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        html = client.get("/guilds/5/domande").text

    assert html.count("<details") == 9
    assert "La soglia è" not in html


def test_i_due_rimandi_esterni_puntano_dove_devono(dashboard):
    corpo = _corpo_domande(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)

    # L'informativa sta su kindling.nexus, non su questo host: l'URL viene da
    # SITO_PUBBLICO, non e' una stringa scritta nel template.
    assert f'href="{SITO_PUBBLICO["privacy"]}"' in corpo
    # E la domanda sui dettagli tecnici e' l'UNICA porta per quella pagina.
    assert f'href="/guilds/{GUILD_TODAY}/dettagli-tecnici"' in corpo


def test_la_domanda_sul_bot_non_elenca_gli_eventi(dashboard):
    """Un elenco parallelo all'informativa e' un elenco che diverge da lei."""
    corpo = _corpo_domande(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)
    raccoglie = corpo[corpo.index('id="q-raccoglie"'):corpo.index('id="q-tecnico"')]

    for evento in ("message", "reaction", "voice_join", "member_join"):
        assert evento not in raccoglie, evento


def test_il_foglio_evidenzia_la_domanda_raggiunta_da_un_ancora():
    """Senza ``:target`` chi arriva da un rimando non sa quale fosse la sua."""
    from dashboard.main import STATIC_DIR

    css = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
    assert ".domande details:target" in css


# --- la voce nel menu ---------------------------------------------------------


def test_domande_e_nel_menu_e_dettagli_tecnici_no(dashboard):
    for percorso in ("", "/robustezza", "/community", "/coorti", "/domande"):
        html = dashboard.get(f"/guilds/{GUILD_TODAY}{percorso}").text
        menu = html[html.index('<nav class="viste"'):html.index("</nav>")]
        assert f'href="/guilds/{GUILD_TODAY}/domande"' in menu, percorso
        assert "dettagli-tecnici" not in menu, percorso

    # Anche la pagina dei Dettagli tecnici porta il menu — serve a tornare
    # indietro — ma nemmeno li' c'e' una voce che rimandi a se stessa.
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici").text
    menu = html[html.index('<nav class="viste"'):html.index("</nav>")]
    assert "dettagli-tecnici" not in menu
    assert 'aria-current="page"' not in menu


def test_la_voce_domande_e_marcata_sulla_sua_pagina(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text
    menu = html[html.index('<nav class="viste"'):html.index("</nav>")]
    assert 'class="voce-domande" href="/guilds/%d/domande" aria-current="page"' % GUILD_TODAY in menu


# --- la pagina Dettagli tecnici ----------------------------------------------


def test_i_dettagli_portano_lo_storico_i_parametri_e_la_diagnostica(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici").text

    assert "Storico delle esecuzioni" in html
    # Due run nel fixture di ...001, dalla piu' recente.
    storico = html[html.index("Storico delle esecuzioni"):html.index("Parametri dell")]
    assert storico.count("<tr>") == 3  # due righe piu' l'intestazione
    assert storico.index("<code>12</code>") < storico.index("<code>11</code>")
    # Qui i timestamp restano completi e in UTC.
    assert "lunedì 14 settembre 2026, 00:00 UTC" in storico
    assert "<code>9d0dc98</code>" in storico
    # La diagnostica, letterale.
    assert "snapshot_gaps" in html


def test_code_version_assente_si_dice_invece_di_restare_vuota(dashboard):
    # GUILD_EDGE ha una run scritta da un'immagine senza KINDLING_CODE_VERSION.
    html = dashboard.get(f"/guilds/{GUILD_EDGE}/dettagli-tecnici").text
    assert "non registrata" in html


def test_i_dettagli_senza_run_lo_dicono():
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"guild_id": 5, "first_seen_at": "2026-08-28T00:00:00Z"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(api), base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        html = client.get("/guilds/5/dettagli-tecnici").text

    assert "Nessuna esecuzione di metriche" in html
    assert "Storico delle esecuzioni" not in html


def test_ogni_parametro_del_job_ha_un_area_dichiarata():
    """Il test che fallisce quando il job aggiunge un parametro.

    Non per pignoleria: senza, la chiave nuova finirebbe in "Altri parametri" e
    nessuno deciderebbe mai dove va. E' la forma di CLAUDE.md 7 in cui un
    meccanismo sembra coprire tutto — il raggruppamento non perde niente — e in
    silenzio mette da parte proprio il caso nuovo.
    """
    assert set(PARAMS_COMPLETI) <= regole.chiavi_note()


def test_una_chiave_sconosciuta_resta_visibile_invece_di_sparire():
    aree = regole.per_area({**PARAMS_COMPLETI, "parametro_di_domani": 42})
    ultima = aree[-1]

    assert ultima.nome == regole.ALTRI
    assert ("parametro_di_domani", 42) in ultima.voci
    # Tutte le chiavi entrano in un'area, nessuna esclusa.
    dentro = {chiave for area in aree for chiave, _ in area.voci}
    assert dentro == set(PARAMS_COMPLETI) | {"parametro_di_domani"}


def test_un_area_vuota_non_compare():
    aree = regole.per_area({"min_cardinality": 5})
    assert [a.nome for a in aree] == ["Soppressione"]
    assert regole.per_area({}) == ()
    assert regole.per_area(None) == ()


def test_i_parametri_si_mostrano_con_la_chiave_grezza(dashboard):
    """Qui le chiavi SONO l'informazione, al contrario che in Stato."""
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici").text
    for chiave in ("min_cardinality", "removal_fractions", "voice_structural_min_sessions"):
        assert f"<code>{chiave}</code>" in html, chiave


def test_i_parametri_parziali_di_una_run_non_inventano_le_chiavi_mancanti(dashboard):
    # GUILD_MATURE registra tre parametri soli: le aree senza nemmeno una chiave
    # non compaiono, e le chiavi assenti non si prendono da job/config.py.
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/dettagli-tecnici").text
    aree = re.findall(r"<h3>([^<]*)</h3>", html)

    assert aree == ["Soppressione", "Coorti", "Calcolabilità"]
    assert "<code>seed</code>" not in html
