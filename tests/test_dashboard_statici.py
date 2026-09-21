"""Il foglio di stile servito dall'app: cache, impronta, CSP, niente stile in linea.

Cinque proprieta' che, sbagliate, non producono nessun errore — e sono la
ragione per cui questo file esiste invece di un "si e' visto che funziona".

1. **Le pagine restano fuori da ogni cache.** Il middleware ha smesso di mettere
   ``private, no-store`` su OGNI risposta: da oggi c'e' un'eccezione, e
   un'eccezione scritta larga per sbaglio (un ``in`` invece di uno
   ``startswith``, un ramo invertito) lascerebbe la pagina di un amministratore
   sul disco di un computer condiviso senza che niente lo dica.
2. **Il foglio ha la cache lunga davvero.** ``StaticFiles`` non mette nessun
   ``Cache-Control`` da se': se il ramo statico del middleware non scattasse, il
   foglio verrebbe riscaricato a ogni pagina e la dashboard continuerebbe a
   funzionare, solo piu' lenta.
3. **L'URL porta l'impronta del CONTENUTO, e ogni file la sua.** Con
   ``immutable`` e un anno, un URL che non cambia quando cambia il foglio da' a
   chi ha gia' visitato la dashboard le pagine nuove con lo stile vecchio. Il
   test non guarda che ci sia "una versione": guarda che cambi quando cambia il
   file. Dal 21/09/2026 i file statici sono due — il foglio e la favicon — e una
   versione sola per entrambi si invaliderebbe solo quando cambia il primo.
4. **La CSP c'e' su ogni pagina**, e ``form-action`` nomina l'host verso cui
   ``POST /login`` risponde davvero. Se un giorno l'URL di Discord cambiasse e la
   CSP no, il bottone "Accedi con Discord" non porterebbe da nessuna parte e
   l'unico segnale sarebbe una riga nella console di chi ci prova.
5. **Nessuna pagina ha stile o script in linea.** E' la condizione che rende
   ottenibile una CSP senza ``'unsafe-inline'``. Uno ``style="..."`` aggiunto
   domani per comodita' non si vedrebbe qui ma in produzione, come un elemento
   senza stile.

Piu' due che guardano il foglio invece delle risposte. **Ogni colore letterale
sta dentro ``:root``**: e' cio' che ha reso il tema scuro (21/09/2026) un secondo
blocco di variabili invece di una caccia nel foglio, e un ``#rrggbb`` scritto a
meta' strada lo vanificherebbe in silenzio. E **i due blocchi definiscono le
stesse variabili**: da quando i temi sono due, un colore puo' stare dentro
``:root`` e stare in uno solo dei due.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from dashboard import auth
from dashboard.main import (
    CACHE_PAGINE,
    CACHE_STATICI,
    CSP,
    PREFISSO_STATICI,
    STATIC_DIR,
    _impronta,
    impronta_statico,
)
from tests.sessione_dashboard import (
    app_di_test,
    client_autenticato,
    raccogli_pagine,
    senza_variabili_di_database,
)
from tools.fixture_api import GUILD_MATURE, GUILD_SCALE, GUILD_TODAY

URL_FOGLIO = f"{PREFISSO_STATICI}dashboard.css"
URL_MARCHIO = f"{PREFISSO_STATICI}marchio.svg"


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    senza_variabili_di_database(monkeypatch)


@pytest.fixture(autouse=True)
def _impronta_non_memorizzata():
    """``impronta_statico`` e' in cache per processo: qui si parte e si finisce puliti."""
    impronta_statico.cache_clear()
    yield
    impronta_statico.cache_clear()


@pytest.fixture
def pagine() -> dict:
    return raccogli_pagine()


# --- 1 e 2. la cache: le pagine mai, il foglio un anno -----------------------


def test_le_pagine_restano_private_e_senza_cache(pagine):
    # Tutte e tredici, non solo /login e una vista: l'eccezione di /static/ e' un
    # ramo nuovo in un middleware che prima non ne aveva, e "tutte tranne quelle
    # che mi sono ricordato di guardare" e' il modo in cui un'eccezione si
    # allarga senza che nessuno se ne accorga.
    for nome, (_, risposta) in pagine.items():
        assert risposta.headers["cache-control"] == CACHE_PAGINE, nome
    assert CACHE_PAGINE == "private, no-store"


def test_il_foglio_di_stile_risponde_ed_e_in_cache_per_un_anno():
    with TestClient(app_di_test()) as c:
        risposta = c.get(URL_FOGLIO)
    assert risposta.status_code == 200
    assert risposta.headers["content-type"].startswith("text/css")
    assert risposta.headers["cache-control"] == CACHE_STATICI
    assert "no-store" not in risposta.headers["cache-control"]
    assert CACHE_STATICI == "public, max-age=31536000, immutable"
    # Il file servito e' quello del repo, non una pagina d'errore con stato 200.
    assert risposta.text.startswith("/*")
    assert ":root" in risposta.text


def test_una_404_sotto_static_non_si_fa_ricordare_per_un_anno():
    """L'eccezione e' scritta stretta: il prefisso E lo stato.

    Un file rinominato senza aggiornare il template darebbe una 404 sotto
    ``/static/``; con la cache lunga anche su quella, ogni browser che l'ha
    chiesta smetterebbe di richiederla per un anno — e il foglio rimesso a posto
    il giorno dopo non arriverebbe a nessuno di loro.
    """
    with TestClient(app_di_test()) as c:
        risposta = c.get(f"{PREFISSO_STATICI}non-esiste.css")
    assert risposta.status_code == 404
    assert risposta.headers["cache-control"] == CACHE_PAGINE


def test_il_foglio_si_legge_anche_senza_essere_entrati():
    # Fuori dalla guardia come /login: senza foglio la pagina d'accesso sarebbe
    # illeggibile proprio a chi non e' ancora entrato.
    with TestClient(app_di_test(), follow_redirects=False) as c:
        assert c.get(URL_FOGLIO).status_code == 200


# --- 3. l'impronta del contenuto ---------------------------------------------


def test_l_impronta_distingue_contenuti_diversi():
    # Il controllo non deve poter passare perche' non distingue niente.
    assert _impronta(b"a") != _impronta(b"b")
    assert _impronta(b"a") == _impronta(b"a")


def test_ogni_pagina_punta_al_foglio_con_l_impronta_di_oggi(pagine):
    atteso = f'href="{URL_FOGLIO}?v={impronta_statico("dashboard.css")}"'
    for nome, (_, risposta) in pagine.items():
        assert atteso in risposta.text, nome
    # L'impronta e' quella del file che l'app serve davvero, non un numero
    # qualunque scritto nel template.
    assert impronta_statico("dashboard.css") == _impronta(
        (STATIC_DIR / "dashboard.css").read_bytes()
    )


def test_la_versione_cambia_se_cambia_il_contenuto_del_foglio(monkeypatch, tmp_path):
    """Il punto dell'intera scelta: l'URL cambia quando cambia il foglio.

    Con ``KINDLING_CODE_VERSION`` al suo posto questo test non potrebbe esistere
    in sviluppo, dove quella variabile e' vuota e quindi costante.
    """
    (tmp_path / "dashboard.css").write_bytes(b"body { color: red; }")
    monkeypatch.setattr("dashboard.main.STATIC_DIR", tmp_path)
    impronta_statico.cache_clear()
    prima = impronta_statico("dashboard.css")

    (tmp_path / "dashboard.css").write_bytes(b"body { color: blue; }")
    impronta_statico.cache_clear()
    assert impronta_statico("dashboard.css") != prima


def test_la_favicon_risponde_e_porta_la_propria_impronta(pagine):
    """Due file statici, due impronte — non una versione sola per tutti.

    E' il caso di CLAUDE.md 7 nella forma "un meccanismo che sembra coprire
    tutto": con una ``versione_css`` sola appesa a entrambi gli URL, la cache si
    invaliderebbe ancora — ma solo quando cambia il FOGLIO. Un marchio nuovo
    servito con la versione di un CSS fermo resterebbe per un anno quello vecchio
    su ogni browser che l'ha gia' chiesto, e nessun errore lo direbbe.
    """
    atteso = f'href="{URL_MARCHIO}?v={impronta_statico("marchio.svg")}"'
    for nome, (_, risposta) in pagine.items():
        assert atteso in risposta.text, nome
    # Le due impronte sono davvero due: se lo fossero per caso, il test sopra
    # passerebbe anche con una variabile sola.
    assert impronta_statico("marchio.svg") != impronta_statico("dashboard.css")

    with TestClient(app_di_test(), follow_redirects=False) as c:
        risposta = c.get(URL_MARCHIO)
    # Fuori dalla guardia come il foglio: il browser la chiede prima del login.
    assert risposta.status_code == 200
    assert risposta.headers["content-type"].startswith("image/svg+xml")
    assert risposta.headers["cache-control"] == CACHE_STATICI
    assert risposta.text.lstrip().startswith("<svg")


def test_la_pagina_e_il_middleware_parlano_dello_stesso_prefisso(pagine):
    # Il mount, il ramo del middleware e l'URL nel template: tre posti, una
    # costante. Due stringhe uguali per caso sono due stringhe che divergono.
    _, accesso = pagine["accesso"]
    (href,) = re.findall(r'<link rel="stylesheet" href="([^"]+)"', accesso.text)
    assert href.startswith(PREFISSO_STATICI)


# --- 4. la CSP ---------------------------------------------------------------


def test_ogni_pagina_porta_la_csp(pagine):
    for nome, (_, risposta) in pagine.items():
        assert risposta.headers["content-security-policy"] == CSP, nome


def test_il_foglio_non_porta_la_csp():
    # Un foglio di stile non ha sottorisorse da governare, e una CSP su una
    # risposta text/css non protegge niente: metterla darebbe solo l'idea che
    # qualcosa la stia applicando.
    with TestClient(app_di_test()) as c:
        assert "content-security-policy" not in c.get(URL_FOGLIO).headers


def test_la_csp_parte_da_default_src_none():
    direttive = dict(d.split(" ", 1) for d in CSP.split("; ") if " " in d)
    assert direttive["default-src"] == "'none'"
    assert direttive["base-uri"] == "'none'"
    assert direttive["frame-ancestors"] == "'none'"
    # Senza 'unsafe-inline': e' l'unica ragione per cui il CSS e' uscito da
    # base.html e non ci puo' rientrare "solo per una regola".
    for nome in ("style-src", "script-src"):
        assert direttive[nome] == "'self'", nome


def test_form_action_nomina_l_host_verso_cui_il_login_risponde_davvero():
    """``POST /login`` risponde 303 verso Discord, e ``form-action`` vale anche
    sulla destinazione del redirect.

    Il valore non si ricopia a mano dalla CSP: si prende dall'URL che
    ``auth.avvia_flusso`` usa davvero. Se un giorno Discord cambiasse host e la
    CSP restasse indietro, il bottone "Accedi con Discord" smetterebbe di portare
    da qualche parte senza nessun errore fuori dalla console del browser — e
    questo test e' l'unico posto in cui le due cose si guardano insieme.
    """
    parti = urlsplit(auth.DISCORD_AUTHORIZE_URL)
    origine = f"{parti.scheme}://{parti.netloc}"

    direttive = dict(d.split(" ", 1) for d in CSP.split("; ") if " " in d)
    consentiti = direttive["form-action"].split()
    assert "'self'" in consentiti
    assert origine in consentiti

    # E il 303 va davvero li': il legame fra la direttiva e il comportamento,
    # non fra la direttiva e una stringa.
    with TestClient(app_di_test(), follow_redirects=False) as c:
        risposta = c.post("/login")
    assert risposta.status_code == 303
    assert risposta.headers["location"].startswith(origine + "/")


# --- 5. niente stile ne' script in linea -------------------------------------

# Le pagine con i grafici SVG, che il fixture disegna solo dove ci sono almeno
# tre snapshot: GUILD_TODAY ne ha pochi e mostra le tabelle. Gli SVG li genera
# il template da valori calcolati in Python, ed e' il posto dove uno style=""
# entrerebbe piu' facilmente senza che nessuno lo rilegga.
PAGINE_CON_GRAFICI = (
    (GUILD_MATURE, "robustezza"),
    (GUILD_MATURE, "community"),
    (GUILD_SCALE, "robustezza"),
    (GUILD_SCALE, "community"),
    (GUILD_TODAY, "coorti"),
)

IN_LINEA = ("<style", 'style="', "<script")


def test_nessuna_delle_tredici_pagine_ha_stile_o_script_in_linea(pagine):
    for nome, (_, risposta) in pagine.items():
        for forma in IN_LINEA:
            assert forma not in risposta.text, f"{nome}: {forma}"


def test_nemmeno_le_pagine_con_i_grafici_svg():
    """Gli SVG sono attributi di presentazione, non stile in linea — e lo restano.

    ``stroke="currentColor"`` e ``fill="..."`` sono attributi che la CSP non
    guarda; uno ``style="stroke: ..."`` sullo stesso elemento invece si', e
    l'elemento resterebbe senza colore. Le due cose si somigliano abbastanza da
    scambiarle scrivendo.
    """
    trovate = 0
    with client_autenticato(app_di_test()) as c:
        for guild_id, vista in PAGINE_CON_GRAFICI:
            risposta = c.get(f"/guilds/{guild_id}/{vista}")
            assert risposta.status_code == 200, (guild_id, vista)
            for forma in IN_LINEA:
                assert forma not in risposta.text, f"{guild_id}/{vista}: {forma}"
            trovate += risposta.text.count('<figure class="grafico"')
    # Il test non deve passare perche' non ha trovato nessun grafico da guardare.
    assert trovate > 0


# --- 6. il foglio: colori in :root, e nessuna risorsa da terzi ---------------


def _codice_del_foglio() -> str:
    """Il CSS senza i commenti.

    Senza questo passaggio i due test sotto guarderebbero anche la prosa, e il
    commento che spiega perche' non ci sono ``@import`` li farebbe fallire
    proprio dicendo la cosa giusta.
    """
    return re.sub(r"/\*.*?\*/", "", (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8"),
                  flags=re.S)


def _blocchi_root() -> list[str]:
    """Il corpo di ogni blocco ``:root`` del foglio: il tema chiaro e lo scuro.

    Dal 21/09/2026 sono due, e i due test sotto contano su questo: il secondo
    vive dentro ``@media (prefers-color-scheme: dark)``, ma fra ``:root {`` e la
    prima ``}`` non ci sono graffe annidate — i commenti li toglie gia'
    ``_codice_del_foglio()``.
    """
    css = _codice_del_foglio()
    return [css[m.end() : css.index("}", m.end())] for m in re.finditer(r":root\s*\{", css)]


def test_ogni_colore_letterale_sta_dentro_root():
    """Il tema scuro e' un secondo blocco :root, non una caccia nel foglio.

    Un ``#rrggbb`` lasciato in una regola in fondo al foglio non darebbe nessun
    errore: darebbe un tema scuro con dentro tre colori chiari, ed e' esattamente
    com'erano scritte le serie dei grafici fino al 21/09/2026.
    """
    css = _codice_del_foglio()
    dentro = [
        range(m.end(), css.index("}", m.end())) for m in re.finditer(r":root\s*\{", css)
    ]
    fuori = [
        m.group(0)
        for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", css)
        if not any(m.start() in r for r in dentro)
    ]
    assert fuori == [], f"colori fuori da :root: {fuori}"
    # Il controllo non passa perche' non trova colori: dentro ce ne sono.
    for corpo in _blocchi_root():
        assert re.findall(r"#[0-9a-fA-F]{3,8}\b", corpo)


def test_i_due_temi_definiscono_esattamente_le_stesse_variabili():
    """La meta' mancante del test sopra, dal giorno del tema scuro.

    Un colore puo' stare dentro ``:root`` e stare in UN solo tema: la variabile
    nuova aggiunta al blocco chiaro e dimenticata in quello scuro non da' nessun
    errore — ricade sul valore chiaro, cioe' un colore da carta dentro una pagina
    nera, e lo si scopre solo aprendo quella pagina con quel tema di sistema.
    E' la forma di CLAUDE.md 7 applicata a una palette: il meccanismo sembra
    coprire tutto (c'e' un blocco per tema) ed esclude un caso in silenzio.
    """
    blocchi = _blocchi_root()
    assert len(blocchi) == 2, "un tema chiaro e un tema scuro, non di piu' e non di meno"
    chiaro, scuro = (set(re.findall(r"(--[\w-]+)\s*:", corpo)) for corpo in blocchi)
    assert chiaro == scuro, f"solo in chiaro: {chiaro - scuro} | solo in scuro: {scuro - chiaro}"
    assert len(chiaro) > 10, "il confronto non deve passare su due insiemi vuoti"


def test_il_foglio_non_carica_niente_da_terzi():
    """Il divieto che NON e' caduto il 21/09/2026.

    E' caduto quello sul ``<link>`` proprio; niente CDN, niente font remoti,
    nessun ``@import``. La dashboard si rende anche senza internet, e la sua CSP
    (``default-src 'none'``) rifiuterebbe comunque un'altra origine — ma la
    rifiuterebbe in console, con la pagina gia' sbagliata sotto gli occhi.
    """
    css = _codice_del_foglio()
    assert "@import" not in css
    assert "url(" not in css.replace("url(#", "")  # url(#id) e' un riferimento interno
    for schema in ("http://", "https://", "//fonts."):
        assert schema not in css, schema


def test_base_html_non_ha_piu_un_blocco_di_stile():
    # La meta' mancante del test sopra: il foglio puo' essere pulitissimo e il
    # <style> essere tornato dentro il template accanto al <link>.
    from dashboard.main import TEMPLATES_DIR

    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    assert "<style" not in base
    assert f'href="{URL_FOGLIO}?v=' in base
