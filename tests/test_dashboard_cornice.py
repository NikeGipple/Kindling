"""La cornice del sito: testata, menu a schede, piede e legenda delle qualificazioni.

Tre cose che nessun test guardava, e che sbagliate non darebbero nessun errore.

1. **base.html si rende su tutte e nove le pagine.** ``crea_templates()`` usa
   ``StrictUndefined``: una variabile aggiunta alla cornice e mai impostata non
   diventa una stringa vuota, fa fallire il rendering — ma solo sulle pagine che
   la incontrano, e tre categorie non hanno ne' sessione ne' guild (la pagina
   d'accesso, i quattro casi di accesso negato, i due di errore). Senza questo
   test, una variabile nuova si scopre rotta in produzione, sulla pagina che
   qualcuno vede per prima.
2. **Il menu appartiene alla vista, non alla guild.** Finche' la condizione era
   ``guild_id is defined``, la pagina 404 di una guild non osservata mostrava
   quattro link verso viste che per quella guild non esistono.
3. **La legenda del piede sta dove i simboli POSSONO comparire**, e in nessun
   altro posto. Guarda la vista, non i dati del giorno: una legenda che va e
   viene con quello che c'e' oggi in tabella sarebbe essa stessa un segnale.

Piu' una quarta, che e' la difesa contro il modo in cui una legenda si sbaglia:
elencare simboli che il codice non rende. Un piede che documenta cose che la
pagina non contiene e' il difetto di CLAUDE.md 7 nella forma peggiore — non tace,
risponde di si'.

Le nove pagine si raggiungono **dalle rotte**, non rendendo i template a mano: un
contesto costruito qui sarebbe una copia di quello che passa la rotta, e la copia
che diverge e' esattamente cio' che questi test devono poter vedere.
"""

from __future__ import annotations

import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard import auth, config, qualifica
from dashboard.main import TEMPLATES_DIR, crea_app
from tests.sessione_dashboard import (
    GUILD_DI_TEST,
    OAUTH_DI_TEST,
    client_autenticato,
    dati_di_sessione,
    entra,
)
from tools.fixture_api import GUILD_TODAY
from tools.fixture_api import app as fixture_app

# Fuori dall'insieme autorizzato della sessione di test: la guardia nega prima
# ancora di chiedere all'API se quel server esista.
FUORI_DALL_INSIEME = 424242
assert FUORI_DALL_INSIEME not in GUILD_DI_TEST

# I nove template che estendono base.html. Scritti qui perche' il test possa
# dire "nove", non "quelli che mi sono ricordato di raggiungere".
TEMPLATE_CON_CORNICE = {
    "accesso.html",
    "accesso_negato.html",
    "community.html",
    "coorti.html",
    "errore.html",
    "guilds.html",
    "non_osservata.html",
    "robustezza.html",
    "stato.html",
}


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


def _app(transport: httpx.AsyncBaseTransport = None):
    return crea_app(
        api_http=httpx.AsyncClient(
            transport=transport or httpx.ASGITransport(app=fixture_app),
            base_url="http://api.test",
        ),
        oauth=OAUTH_DI_TEST,
    )


def _api_giu(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("nessuna rete", request=request)


def _api_fuori_contratto(request: httpx.Request) -> httpx.Response:
    # I nomi del database invece di quelli del modello: RispostaNonConforme.
    return httpx.Response(200, json={"guild_id": GUILD_TODAY, "is_suppressed": False})


@pytest.fixture
def pagine() -> dict:
    """Ogni pagina presa dalla rotta che la produce, con il contesto che le passa.

    Se ``StrictUndefined`` alza dentro un template, il TestClient rialza qui: la
    raccolta stessa fallisce, e si vede quale pagina.
    """
    raccolta: dict[str, tuple[str, httpx.Response]] = {}

    with client_autenticato(_app(), follow_redirects=False) as c:
        raccolta["elenco"] = ("guilds.html", c.get("/"))
        raccolta["stato"] = ("stato.html", c.get(f"/guilds/{GUILD_TODAY}"))
        raccolta["robustezza"] = ("robustezza.html", c.get(f"/guilds/{GUILD_TODAY}/robustezza"))
        raccolta["community"] = ("community.html", c.get(f"/guilds/{GUILD_TODAY}/community"))
        raccolta["coorti"] = ("coorti.html", c.get(f"/guilds/{GUILD_TODAY}/coorti"))
        # 123 e' nell'insieme autorizzato ma il fixture non lo osserva: la
        # guardia passa e l'API risponde 404. E' la pagina del difetto 1c.
        raccolta["non_osservata"] = ("non_osservata.html", c.get("/guilds/123"))
        raccolta["negato_server"] = (
            "accesso_negato.html", c.get(f"/guilds/{FUORI_DALL_INSIEME}")
        )
        # verifica_fallita senza passare da Discord: uno ``state`` che non e'
        # quello del flusso nega prima di qualunque chiamata.
        raccolta["negato_verifica"] = (
            "accesso_negato.html",
            c.get("/oauth/callback", params={"code": "x", "state": "non-mio"}),
        )

    # Senza cookie: la pagina d'accesso, che per definizione non ha sessione.
    with TestClient(_app(), follow_redirects=False) as c:
        raccolta["accesso"] = ("accesso.html", c.get("/login"))

    with TestClient(_app(), follow_redirects=False) as c:
        entra(c, dati_di_sessione(login_at=time.time() - auth.DURATA_SESSIONE_SECONDI - 1))
        raccolta["negato_scaduta"] = ("accesso_negato.html", c.get("/"))

    with TestClient(_app(), follow_redirects=False) as c:
        entra(c, dati_di_sessione(guilds=[]))
        raccolta["negato_nessun_server"] = ("accesso_negato.html", c.get("/"))

    with client_autenticato(_app(httpx.MockTransport(_api_giu)), follow_redirects=False) as c:
        raccolta["errore_api_giu"] = ("errore.html", c.get(f"/guilds/{GUILD_TODAY}"))

    transport = httpx.MockTransport(_api_fuori_contratto)
    with client_autenticato(_app(transport), follow_redirects=False) as c:
        raccolta["errore_contratto"] = ("errore.html", c.get(f"/guilds/{GUILD_TODAY}"))

    return raccolta


# --- 1. la rete sotto StrictUndefined ----------------------------------------


def test_tutte_e_nove_le_pagine_si_rendono_fino_in_fondo(pagine):
    # I quattro casi di accesso_negato.html e i due di errore.html sono pagine
    # diverse dello stesso template: si contano i template, non le pagine.
    assert {template for template, _ in pagine.values()} == TEMPLATE_CON_CORNICE
    assert len(pagine) == 13

    for nome, (template, risposta) in sorted(pagine.items()):
        assert "text/html" in risposta.headers["content-type"], nome
        # Fino in fondo vuol dire fino al piede, che sta in base.html DOPO il
        # blocco del contenuto: una pagina troncata a meta' non arriva qui.
        assert risposta.text.rstrip().endswith("</html>"), nome
        assert 'class="piede"' in risposta.text, nome
        assert "nessun dato per singola persona esce da qui" in risposta.text, nome


def test_le_nove_pagine_hanno_gli_stati_che_dichiarano(pagine):
    # Non e' decorazione: se una pagina d'errore rispondesse 200 il test sopra
    # resterebbe verde guardando la pagina sbagliata.
    attesi = {
        "elenco": 200, "stato": 200, "robustezza": 200, "community": 200, "coorti": 200,
        "accesso": 200, "non_osservata": 404, "negato_server": 404, "negato_verifica": 403,
        "negato_scaduta": 401, "negato_nessun_server": 403,
        "errore_api_giu": 503, "errore_contratto": 502,
    }
    assert {nome: r.status_code for nome, (_, r) in pagine.items()} == attesi


# --- 2. il menu appartiene alla vista, non alla guild ------------------------


def test_il_menu_e_la_riga_di_contesto_stanno_solo_nelle_quattro_viste(pagine):
    viste = {"stato", "robustezza", "community", "coorti"}
    con_menu = {nome for nome, (_, r) in pagine.items() if 'class="viste"' in r.text}
    assert con_menu == viste
    # Menu e riga di contesto dicono la stessa cosa — "sei dentro una vista di un
    # server" — e devono comparire insieme o non comparire.
    con_contesto = {nome for nome, (_, r) in pagine.items() if 'class="contesto-guild"' in r.text}
    assert con_contesto == viste


def test_la_guild_non_osservata_non_offre_viste_che_per_lei_non_esistono(pagine):
    _, risposta = pagine["non_osservata"]
    # La pagina dice quale guild e': e' proprio l'avere un guild_id che le faceva
    # arrivare addosso il menu.
    assert "123" in risposta.text
    for percorso in ("robustezza", "community", "coorti"):
        assert f"/guilds/123/{percorso}" not in risposta.text


# --- 3. la legenda sta dove i simboli possono comparire ----------------------


def test_la_legenda_e_solo_nelle_tre_viste_con_simboli(pagine):
    con_legenda = {nome for nome, (_, r) in pagine.items() if 'class="legenda-simboli"' in r.text}
    # Stato no: mostra il contesto, non metriche, e nessuna sua cella passa da
    # cella(). Elenco, accesso e pagine d'errore nemmeno.
    assert con_legenda == {"robustezza", "community", "coorti"}


def test_la_legenda_nomina_solo_quello_che_il_codice_rende():
    """Il piede non documenta cose che la pagina non contiene.

    Esiste un disegno di questa legenda con cinque voci, e due di quelle non
    hanno nessun rendering nel codice. La legenda si costruisce leggendo
    _cella.html e qualifica.py, e questo test e' il modo di dirlo una volta sola.
    """
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    cella = (TEMPLATES_DIR / "_cella.html").read_text(encoding="utf-8")
    legenda = base[base.index('class="legenda-simboli"'):base.index("</details>")]

    glifi_elencati = set(re.findall(r"<dt>(\S)</dt>", legenda))
    glifi_resi = set(
        re.findall(r'class="cella__simbolo" aria-hidden="true">(\S+?)</span>', cella)
    )
    assert glifi_resi, "nessun glifo in _cella.html: il test sta guardando la cosa sbagliata"
    assert glifi_elencati == glifi_resi

    tipi_elencati = set(re.findall(r"etichetta etichetta--(\w+)", legenda))
    # I tipi che _etichette_di_riga() sa produrre, e nessun altro. NON_VALUTATO
    # non ha rendering (dashboard.md 5) e non deve comparire nemmeno qui.
    assert tipi_elencati == {qualifica.NON_SIGNIFICATIVO, qualifica.SOLO_SOPRAVVISSUTI}
