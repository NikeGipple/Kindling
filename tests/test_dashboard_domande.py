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
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_SCALE, GUILD_TODAY
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


def test_ogni_domanda_ha_un_id_stabile_ed_e_aperta(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text
    corpo = _corpo_domande(html)

    trovati = re.findall(r'<details id="([\w-]+)" open>', corpo)
    assert trovati == list(domande.TITOLI)
    # Aperte, e non e' una svista: senza JavaScript non esiste un modo di aprire
    # quella raggiunta da un'ancora, e un rimando che atterra su una risposta
    # invisibile fallisce proprio nel compito per cui gli id esistono.
    assert corpo.count("<details") == corpo.count(" open>") == len(domande.TITOLI)
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


def test_ogni_ancora_di_ogni_pagina_esiste_fra_le_domande(dashboard):
    """Ogni rimando a /domande#... scritto in una pagina atterra su un id vero.

    Un id rinominato a meta' non da' nessun errore: il link porta in cima alla
    pagina, e chi ci arriva non sa di aver perso la risposta. E' successo quasi il
    26/09/2026, quando le tre domande di Coorti hanno cambiato prefisso prima del
    deploy. Il controllo di Stato (test_dashboard_stato.py) guardava solo Stato;
    questo guarda tutte le pagine che rimandano, su tutte le guild del fixture —
    Coorti in particolare, i cui rimandi cambiano con i dati (l'avviso, la nota).
    """
    viste = ("", "/robustezza", "/community", "/coorti", "/dettagli-tecnici")
    trovate: set[str] = set()
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_SCALE):
        ids = set(re.findall(r'<details id="([\w-]+)"', dashboard.get(f"/guilds/{gid}/domande").text))
        assert ids == set(domande.TITOLI), gid
        for vista in viste:
            html = dashboard.get(f"/guilds/{gid}{vista}").text
            for ancora in re.findall(rf'href="/guilds/{gid}/domande#([\w-]+)"', html):
                assert ancora in ids, (gid, vista or "stato", ancora)
                trovate.add(ancora)
    # Il test non deve passare perche' non ha trovato niente da controllare: i tre
    # rimandi di Coorti e quelli di Stato devono esserci davvero.
    assert {"q-coorte-leggibile", "q-vocale", "q-barre", "q-segnate"} <= trovate


def test_senza_run_le_domande_si_rendono_lo_stesso():
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"guild_id": 5, "first_seen_at": "2026-08-28T00:00:00Z"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(api), base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        html = client.get("/guilds/5/domande").text

    assert html.count("<details") == len(domande.TITOLI)
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
        # Anche /cohorts, dal 26/09/2026: la pagina porta la tabella delle coorti.
        # E /robustness dal 30/09/2026, per la tabella che era la vista Robustezza.
        if request.url.path.endswith(("/runs", "/cohorts", "/robustness")):
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
    # Solo la sezione dei parametri: sotto, dal 26/09/2026, ci sono le coorti con
    # i loro titoli di terzo livello.
    parametri = html[html.index("Parametri dell'ultima esecuzione"):html.index('id="coorti"')]
    aree = re.findall(r"<h3>([^<]*)</h3>", parametri)

    assert aree == ["Soppressione", "Coorti", "Calcolabilità"]
    assert "<code>seed</code>" not in html


# --- i gruppi e le domande di Robustezza (30/09/2026) ------------------------


def test_ogni_domanda_sta_in_un_gruppo_e_in_uno_solo():
    """Una domanda nuova senza gruppo sparirebbe dalla pagina senza nessun errore.

    E l'ordine dei gruppi e' l'ordine della mappa: il test degli id sopra lo
    confronta con la pagina resa.
    """
    in_gruppi = [c for _titolo, codici in domande.GRUPPI for c in codici]
    assert in_gruppi == list(domande.TITOLI)
    assert len(in_gruppi) == len(set(in_gruppi))
    costruite = {d.codice for d in domande.costruisci(1, PARAMS_COMPLETI, privacy_url="x")}
    assert costruite == set(domande.TITOLI)


def test_i_gruppi_sono_titoli_sopra_le_loro_domande(dashboard):
    corpo = _corpo_domande(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)
    titoli = re.findall(r'<h2 class="domande__gruppo">(.*?)</h2>', corpo)
    assert titoli == [t for t, _c in domande.GRUPPI] == [
        "In generale", "Robustezza", "Coorti", "Community", "Dati e aggiornamenti"
    ]
    # Ogni domanda sta sotto il titolo del suo gruppo.
    for titolo, codici in domande.GRUPPI:
        sezione = corpo.split(f'<h2 class="domande__gruppo">{titolo}</h2>', 1)[1]
        sezione = sezione.split('<h2 class="domande__gruppo">', 1)[0]
        assert re.findall(r'<details id="([\w-]+)"', sezione) == list(codici)


def _robustezza(params) -> dict[str, str]:
    return {
        d.codice: " ".join(d.paragrafi)
        for d in domande.costruisci(1, params, privacy_url="x")
        if d.codice.startswith("q-robustezza-")
    }


def test_le_domande_di_robustezza_citano_le_soglie_da_params():
    p = MetricParams()
    testi = _robustezza(PARAMS_COMPLETI)
    assert set(testi) == {
        "q-robustezza-come", "q-robustezza-barra", "q-robustezza-chi",
        "q-robustezza-leggibile", "q-robustezza-tipi",
    }
    assert f"{p.baseline_repetitions} volte" in testi["q-robustezza-come"]
    assert "tre soglie: una persona su 20, una su 10, una su 5." in testi["q-robustezza-come"]
    assert f"almeno {p.min_nodes_structural} persone interagiscono" in testi["q-robustezza-leggibile"]
    assert f"Sotto le {p.min_nodes_structural} persone attive" in testi["q-robustezza-chi"]

    # I valori della run, non quelli del job: la frase li segue.
    altri = _robustezza({**PARAMS_COMPLETI, "min_nodes_structural": 41,
                         "baseline_repetitions": 20, "removal_fractions": [0.1, 0.15]})
    assert "almeno 41 persone" in altri["q-robustezza-leggibile"]
    assert "20 volte" in altri["q-robustezza-come"]
    # Una frazione che non e' "una su N" resta una percentuale, non un "1 su 7".
    assert "due soglie: una persona su 10, il 15% delle persone." in altri["q-robustezza-come"]


def test_senza_chiavi_le_frasi_di_robustezza_non_inventano_numeri():
    testi = _robustezza({})
    tutto = " ".join(testi.values())
    assert not re.search(r"\d", tutto), tutto
    assert "soglie" not in testi["q-robustezza-come"]
    assert all(testi.values())


def test_le_risposte_di_robustezza_non_giudicano_e_non_promettono_protezione():
    """Decisioni del 29 e 30/09: nessuna parola di giudizio, "leggibile" non vuol
    dire "concentrato su pochi", e la soglia e' leggibilita', non protezione."""
    testi = _robustezza(PARAMS_COMPLETI)
    tutto = " ".join(testi.values()).lower()
    for parola in ("fragil", "solid", "robusto", "debole"):
        assert parola not in tutto, parola
    # Entrambe le letture, non solo l'allarme.
    assert "passano da poche persone" in testi["q-robustezza-come"]
    assert "distribuiti" in testi["q-robustezza-come"]
    assert "non vuol dire che il server dipenda da poche persone" in testi["q-robustezza-leggibile"]
    assert "non una protezione" in testi["q-robustezza-chi"]
    # E la barra oltre la tacca non e' negata: succede.
    assert "Se la barra supera la tacca" in testi["q-robustezza-barra"]


def test_la_domanda_sulla_barra_porta_ai_dettagli_tecnici():
    (barra,) = [d for d in domande.costruisci(7, PARAMS_COMPLETI, privacy_url="x")
                if d.codice == "q-robustezza-barra"]
    assert barra.link is not None and barra.link.url == "/guilds/7/dettagli-tecnici#robustezza"


# --- q-leggibile: le tre condizioni del job (30/09/2026) ----------------------


def _leggibile(params) -> str:
    (d,) = [d for d in domande.costruisci(1, params, privacy_url="x") if d.codice == "q-leggibile"]
    return " ".join(d.paragrafi)


def test_q_leggibile_nomina_le_tre_condizioni_che_il_job_usa():
    """too_few_nodes, modularity_z e node_overlap (job/communities.py): la risposta
    ne diceva due, e la terza e' quella che blocca i layer testuali di Arco."""
    p = MetricParams()
    testo = _leggibile(PARAMS_COMPLETI)
    assert f"almeno {p.min_nodes_structural} persone attive" in testo
    assert f"almeno {p.min_modularity_z:g} volte la variazione tipica del caso" in testo
    assert "almeno metà delle persone è la stessa della settimana prima" in testo
    assert "La prima settimana, senza un precedente, questa condizione non si applica." in testo
    assert "tre condizioni" in testo

    # I valori della run, non quelli del job.
    altri = _leggibile({**PARAMS_COMPLETI, "min_node_overlap": 0.6, "min_modularity_z": 2.5})
    assert "almeno il 60% delle persone" in altri
    assert "almeno 2,5 volte" in altri


@pytest.mark.parametrize("chiave", ["min_nodes_structural", "min_modularity_z", "min_node_overlap"])
def test_q_leggibile_senza_una_chiave_la_sua_frase_non_ha_numero(chiave):
    senza = {k: v for k, v in PARAMS_COMPLETI.items() if k != chiave}
    completo, ridotto = _leggibile(PARAMS_COMPLETI), _leggibile(senza)
    assert completo != ridotto
    frase = {
        "min_nodes_structural": "persone attive",
        "min_modularity_z": "volte la variazione",
        "min_node_overlap": "delle persone è la stessa",
    }[chiave]
    assert frase in completo and frase not in ridotto
    # La condizione resta nominata, senza il numero.
    assert len(re.findall(r"\bquando\b", ridotto.lower())) == len(re.findall(r"\bquando\b", completo.lower()))


# --- le ripetizioni del baseline: due valori, non uno (30/09/2026) -------------


def test_le_ripetizioni_dicono_anche_la_riduzione_sulle_reti_grandi():
    """Oltre baseline_downgrade_nodes il job scende a baseline_repetitions_reduced:
    "100 volte" da solo sarebbe falso su una rete di 600 persone."""
    p = MetricParams()
    assert p.baseline_repetitions_for(p.baseline_downgrade_nodes + 1)[0] == p.baseline_repetitions_reduced
    testo = _robustezza(PARAMS_COMPLETI)["q-robustezza-come"]
    assert (
        f"{p.baseline_repetitions} volte ({p.baseline_repetitions_reduced} nelle reti con più "
        f"di {p.baseline_downgrade_nodes} persone attive)"
    ) in testo


@pytest.mark.parametrize("chiave", [
    "baseline_repetitions", "baseline_repetitions_reduced", "baseline_downgrade_nodes",
])
def test_senza_una_chiave_delle_ripetizioni_nessun_numero_di_volte(chiave):
    senza = {k: v for k, v in PARAMS_COMPLETI.items() if k != chiave}
    testo = _robustezza(senza)["q-robustezza-come"]
    assert "volte" not in testo
    assert "persone scelte a caso e confronta" in testo
