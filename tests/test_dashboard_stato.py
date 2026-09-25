"""Vista Stato: i fatti, il calendario, gli stati delle tre viste, le regole.

Il fixture (``tools/fixture_api.py``) si monta con ``httpx.ASGITransport``: stesse
rotte e stessi modelli dell'API, nessuna rete e nessun uvicorn. La dashboard non
sa di parlare con un fixture — riceve solo un client httpx, come in produzione.

**L'orologio si ferma.** Tre dei fatti di questa pagina dipendono da che giorno
e' oggi, e il fixture ha date fisse: senza fermare l'orologio, un test su "8
giorni fa" sarebbe vero il giorno in cui e' stato scritto e falso il giorno dopo,
e la reazione ovvia — togliere l'asserzione — lascerebbe la pagina senza nessun
controllo proprio sui numeri che la aprono. ``dashboard.stato.adesso`` esiste per
questo, ed e' l'unico posto da spostare.

**Cosa NON deve ricomparire.** ``code_version``, ``stats`` e le chiavi grezze di
``params`` sono uscite da questa vista il 22/09/2026 e stanno nei Dettagli
tecnici. Un test qui sotto legge il TEMPLATE e fallisce se tornano: erano li' per
una ragione, e una ragione torna piu' facilmente di quanto si creda.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from api.models import GuildRow, Quality, RunRow
from dashboard import config, regole, stato
from dashboard.main import TEMPLATES_DIR, crea_app, data_ora
from job.config import MetricParams
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_SCALE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

# Un mercoledi' dentro la storia del fixture: due giorni dopo l'ultimo as_of di
# GUILD_TODAY (lunedi' 14 settembre), cinque prima del successivo. Scelto cosi'
# perche' esercita il ramo che conta: un "prossimo aggiornamento" ancora nel
# FUTURO, cioe' la linea del tempo con il tratteggio e il pallino finale. Con
# l'orologio vero, il fixture e' sempre nel passato e quel ramo non si vedrebbe
# mai (CLAUDE.md 7: un controllo che smette di controllare).
ADESSO = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)

PARAMS_COMPLETI = MetricParams().as_run_params()


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    # Il processo rifiuta di partire con una variabile di database: i test non
    # devono dipendere da cosa c'e' nella shell di chi li lancia.
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture(autouse=True)
def _orologio_fermo(monkeypatch):
    monkeypatch.setattr(stato, "adesso", lambda: ADESSO)


def _client(transport: httpx.AsyncBaseTransport) -> TestClient:
    http = httpx.AsyncClient(transport=transport, base_url="http://api.test")
    # Dietro la guardia, con una sessione vera: vedi tests/sessione_dashboard.py.
    return client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST))


@pytest.fixture
def dashboard():
    with _client(httpx.ASGITransport(app=fixture_app)) as client:
        yield client


def _api_finta(
    guild: dict[str, Any],
    runs: Optional[list[dict[str, Any]]] = None,
) -> httpx.MockTransport:
    """Un'API che risponde solo quello che serve alla vista Stato.

    Le tre serie tornano vuote: chi usa questo transport sta guardando i fatti,
    il calendario o gli avvisi, e tre viste "in raccolta" sono lo sfondo giusto
    per quei casi.
    """

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json=runs or [])
        for coda in ("/robustness", "/communities", "/cohorts"):
            if request.url.path.endswith(coda):
                return httpx.Response(200, json=[])
        return httpx.Response(200, json=guild)

    return httpx.MockTransport(api)


def _guild(**campi: Any) -> dict[str, Any]:
    base = {"guild_id": 5, "first_seen_at": "2026-08-28T15:28:00Z"}
    return GuildRow(**{**base, **campi}).model_dump(mode="json")


def _run(as_of: str, **campi: Any) -> dict[str, Any]:
    base = {"snapshot_id": 1, "as_of": as_of, "params": PARAMS_COMPLETI, "stats": {}}
    return RunRow(**{**base, **campi}).model_dump(mode="json")


def _qualita(*, soppressa: bool = False, significativa: Optional[bool] = None) -> Quality:
    return Quality(suppressed=soppressa, significant=significativa)


# --- le date: fuso di Roma, forma breve, giorni di calendario ----------------


def test_il_giorno_e_quello_di_roma_anche_a_cavallo_della_mezzanotte_utc():
    """Lo stesso istante, due giorni diversi — ed e' il motivo del fuso fisso.

    Le 23:30 UTC di lunedi' sono l'1:30 di martedi' a Roma. Un amministratore che
    guarda la pagina a quell'ora deve leggere il giorno in cui si trova lui, non
    quello in cui si trova il server. ``data_ora`` (le altre viste) continua a
    dire l'altro, e lo dichiara scrivendo UTC.
    """
    istante = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)

    assert stato.giorno(istante) == date(2026, 9, 22)
    assert stato.data_breve(istante) == "22 set"
    assert data_ora(istante) == "lunedì 21 settembre 2026, 23:30 UTC"

    # E il verso opposto: le 00:30 UTC sono ancora le 2:30 dello stesso giorno a
    # Roma, quindi qui i due giorni coincidono. Senza questa meta', il test
    # passerebbe anche con un fuso sbagliato di segno.
    mattina = datetime(2026, 9, 22, 0, 30, tzinfo=timezone.utc)
    assert stato.giorno(mattina) == date(2026, 9, 22)


def test_relativo_conta_i_giorni_di_calendario_non_le_24_ore():
    oggi = date(2026, 9, 16)
    assert stato.relativo(oggi, oggi) == "oggi"
    assert stato.relativo(date(2026, 9, 15), oggi) == "ieri"
    assert stato.relativo(date(2026, 9, 17), oggi) == "domani"
    assert stato.relativo(date(2026, 9, 23), oggi) == "tra 7 giorni"
    assert stato.relativo(date(2026, 9, 9), oggi) == "7 giorni fa"

    # Dodici ore di distanza e due giorni diversi: la sera del 15 e' "ieri".
    mattina = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)
    sera = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)
    assert stato.relativo(stato.giorno(sera), stato.giorno(mattina)) == "ieri"
    # Tre ore piu' tardi sono NOVE ore di distanza e lo stesso giorno: le 23 UTC
    # del 15 sono l'una di notte del 16 a Roma. E' il caso che un conteggio a
    # multipli di 24 ore sbaglierebbe, e l'unico modo di vederlo e' misurarlo sul
    # calendario di Roma invece che sulla differenza fra i due istanti.
    tardi = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
    assert stato.relativo(stato.giorno(tardi), stato.giorno(mattina)) == "oggi"


# --- i quattro stati di "Cosa puoi leggere oggi" -----------------------------


def test_i_quattro_stati_vengono_da_due_campi_soli():
    """``suppressed`` e ``significant``, niente altro: nessuna soglia nuova.

    ``significant is None`` significa "non valutata" (riga soppressa), che non e'
    un no: una riga soppressa non puo' far dire "con cautela" a una vista in cui
    tutto il resto e' significativo.
    """
    assert stato.stato_da_qualita([]) == stato.IN_RACCOLTA
    assert stato.stato_da_qualita([_qualita(soppressa=True)] * 3) == stato.SOTTO_SOGLIA
    assert stato.stato_da_qualita([_qualita(significativa=False)] * 3) == stato.CON_CAUTELA
    assert (
        stato.stato_da_qualita([_qualita(significativa=False), _qualita(significativa=True)])
        == stato.LEGGIBILE
    )
    # Una soppressa accanto a una significativa: la vista si legge lo stesso.
    assert (
        stato.stato_da_qualita([_qualita(soppressa=True), _qualita(significativa=True)])
        == stato.LEGGIBILE
    )
    # Ogni stato ha un'etichetta e una frase: una pillola senza testo non si
    # vedrebbe fallire da nessun'altra parte.
    for codice in (stato.IN_RACCOLTA, stato.SOTTO_SOGLIA, stato.CON_CAUTELA, stato.LEGGIBILE):
        assert stato.ETICHETTE_STATO[codice] and stato.FRASI_STATO[codice]


def test_lo_stato_guarda_solo_l_ultimo_snapshot(dashboard):
    """Uno snapshot significativo di dieci settimane fa non rende leggibile oggi."""
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    # dashboard.md 6: su ...001 nessuna riga e' oggi is_significant = true.
    assert html.count('class="pillola pillola--con_cautela"') == 3
    assert "pillola--leggibile" not in html

    # ...002 ha dodici settimane di serie e righe significative: leggibile.
    maturo = dashboard.get(f"/guilds/{GUILD_MATURE}").text
    assert maturo.count('class="pillola pillola--leggibile"') == 3


def test_ogni_lettura_rimanda_a_una_domanda_che_esiste(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    domande = dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text
    import re

    ancore = re.findall(rf'href="/guilds/{GUILD_TODAY}/domande#([\w-]+)"', html)
    assert len(ancore) == 3
    for ancora in ancore:
        # L'ancora esiste davvero sulla pagina Domande: un id rinominato la' non
        # darebbe nessun errore qui, porterebbe solo in cima alla pagina.
        assert f'id="{ancora}"' in domande
    # E il testo del rimando e' il testo della domanda, non un "Perche'?" ripetuto.
    for ancora in ancore:
        from dashboard.domande import TITOLI

        assert TITOLI[ancora] in html


# --- i tre fatti in cima ------------------------------------------------------


def test_i_tre_fatti_con_le_date_di_roma(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    fatti = html[html.index('<dl class="fatti-stato"'):html.index("</dl>")]

    # first_seen_at: venerdi' 28 agosto 2026, 15:28 UTC -> 19 giorni fino al 16/09.
    assert "In osservazione da" in fatti
    assert "19 giorni" in fatti and "dal 28 ago" in fatti
    # L'ultima run e' lo snapshot 12, as_of lunedi' 14 settembre 00:00 UTC.
    assert "2 giorni fa" in fatti and "14 set" in fatti
    # 14 set piu' la cadenza OSSERVATA, che qui e' 6 giorni e 19h45m: lo
    # snapshot 11 ha as_of alle 04:15, scritto prima dell'ancoraggio al lunedi'
    # (modello-grafo.md 5.1), e con due sole run quell'intervallo E' la mediana.
    # La previsione cade quindi di domenica 20 e non di lunedi' 21 — ed e'
    # corretta: la pagina descrive quello che e' successo su questo server, non
    # quello che il cron promette.
    assert "tra 4 giorni" in fatti and "previsto il 20 set" in fatti
    assert "era previsto" not in fatti


def test_un_prossimo_aggiornamento_gia_passato_si_chiama_in_ritardo():
    """Il cron non ha girato: il fatto lo dice, e il calendario non finge un futuro.

    Con la sola forma relativa il riquadro "Prossimo aggiornamento" direbbe
    "3 giorni fa", che sotto quell'etichetta si legge come un errore di
    rendering invece che come un calcolo mancato.
    """
    # Due run a sette giorni, l'ultima dieci giorni prima di ADESSO: il prossimo
    # calcolo cadeva tre giorni fa. Due e non una, perche' con una sola la
    # cadenza non esiste e il fatto non comparirebbe affatto (test sotto).
    def quando(giorni_fa: int) -> str:
        return (ADESSO - timedelta(days=giorni_fa)).date().isoformat() + "T00:00:00Z"

    runs = [_run(quando(10), snapshot_id=2), _run(quando(17), snapshot_id=1)]
    with _client(_api_finta(_guild(), runs)) as client:
        html = client.get("/guilds/5").text

    assert "in ritardo" in html
    assert "era previsto il" in html
    # Nessuna tappa futura e nessun tratteggio: il futuro non c'e'.
    assert "asse--futuro" not in html
    assert "tappa--futura" not in html


def test_senza_run_restano_un_fatto_solo_e_nessun_calendario():
    with _client(_api_finta(_guild())) as client:
        html = client.get("/guilds/5").text

    assert "In osservazione da" in html
    assert "Dati aggiornati a" not in html
    assert "Prossimo aggiornamento" not in html
    assert "Il primo calcolo non è ancora stato fatto" in html
    # Niente linea del tempo: una linea con due estremi e nessun calcolo dice
    # meno di nessuna linea.
    assert 'class="linea-tempo"' not in html
    # E nessuna regola: i valori vengono da params, e params non c'e'.
    assert "Le regole del calcolo" not in html
    # Le tre viste ci sono lo stesso, tutte "in raccolta".
    assert html.count("pillola--in_raccolta") == 3


# --- il calendario ------------------------------------------------------------


def test_il_calendario_ha_un_punto_per_ogni_run_e_il_futuro_tratteggiato(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    linea = html[html.index('<figure class="linea-tempo"'):html.index("</figure>")]

    # Due run nel fixture di ...001: due pallini di calcolo, con la loro data.
    assert linea.count("tappa--calcolo") == 2
    assert "calcolo del 7 set" in linea and "calcolo del 14 set" in linea
    # Arrivo, oggi, prossimo: uno ciascuno.
    for classe in ("tappa--arrivo", "tappa--oggi", "tappa--futura", "asse--futuro"):
        assert linea.count(classe) == 1, classe
    # Le ascisse sono attributi, non uno style: e' il vincolo della CSP.
    assert "style=" not in linea
    assert 'cx="0.0%"' in linea
    # Dodici settimane di run: dodici pallini, non dodici etichette sovrapposte.
    maturo = dashboard.get(f"/guilds/{GUILD_MATURE}").text
    assert maturo[maturo.index('<figure class="linea-tempo"'):].count("tappa--calcolo") == 12


def test_il_calendario_non_parla_di_ricalcoli(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}").text
    for parola in ("ricalcol", "rifatt", "rieseg"):
        assert parola not in html.lower(), parola


# --- gli avvisi ---------------------------------------------------------------


def test_il_buco_di_osservazione_e_una_frase_sola(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_EDGE}").text

    avvisi = [
        html[m:html.index("</p>", m)]
        for m in _posizioni(html, '<p class="avviso" role="note">')
    ]
    assert len(avvisi) == 1
    assert "Dal 3 mag al 19 giu il bot non era sul server" in avvisi[0]
    # Una frase, non un pannello con il titolo in grassetto.
    assert "<strong>" not in avvisi[0]


def test_uscita_e_rientro_sono_due_avvisi_diversi():
    # Solo left_at: il bot e' uscito e non e' rientrato. Non e' un "buco", che
    # ha un inizio e una fine.
    with _client(_api_finta(_guild(left_at="2026-09-01T00:00:00Z"))) as client:
        uscito = client.get("/guilds/5").text
    assert "ha lasciato il server il 1 set e non è rientrato" in uscito
    assert "non era sul server" not in uscito

    # Solo rejoined_at: un rientro senza uscita registrata.
    with _client(_api_finta(_guild(rejoined_at="2026-09-01T00:00:00Z"))) as client:
        rientro = client.get("/guilds/5").text
    assert "rientro del bot il 1 set senza una data di uscita" in rientro


def test_l_avviso_sui_parametri_nomina_il_cambio_piu_recente():
    """Con due cambi, il confine utile e' l'ultimo.

    La frase promette che da quella data i numeri sono confrontabili: indicare il
    primo cambio darebbe un confine piu' generoso del vero, cioe' proprio
    l'errore che l'avviso esiste per evitare.
    """
    diversi = {**PARAMS_COMPLETI, "k_connections": 7}
    ancora_diversi = {**PARAMS_COMPLETI, "k_connections": 9}
    runs = [
        _run("2026-09-14T00:00:00Z", snapshot_id=3, params=ancora_diversi),
        _run("2026-09-07T00:00:00Z", snapshot_id=2, params=diversi),
        _run("2026-08-31T00:00:00Z", snapshot_id=1),
    ]
    with _client(_api_finta(_guild(), runs)) as client:
        html = client.get("/guilds/5").text

    assert "Metodo di calcolo aggiornato il 14 set: confronta i numeri solo da quella data." in html
    assert "7 set" not in html.split("Metodo di calcolo")[1]


def test_senza_cambi_di_parametri_l_avviso_non_c_e_(dashboard):
    # GUILD_MATURE: dodici run con gli stessi params.
    html = dashboard.get(f"/guilds/{GUILD_MATURE}").text
    assert "Metodo di calcolo aggiornato" not in html


def test_cambio_di_parametri_su_zero_o_una_run():
    assert stato.cambio_di_parametri([]) is None
    sola = RunRow(snapshot_id=1, as_of=ADESSO, params={"a": 1}, stats={})
    assert stato.cambio_di_parametri([sola]) is None


# --- le regole del calcolo ----------------------------------------------------


def test_le_otto_regole_leggono_i_valori_dai_dati_della_run(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    blocco = html[html.index('<ul class="regole"'):html.index("</ul>", html.index('<ul class="regole"'))]

    # Sei da metric_runs.params, due dai campi tipizzati del grafo.
    assert blocco.count('class="regola"') == 8
    # Le chiavi grezze non compaiono: solo etichette e frasi.
    for chiave in regole.chiavi_delle_regole():
        assert chiave not in blocco, chiave
    # I valori sono quelli del job, non numeri scritti nel template.
    p = MetricParams()
    assert f"{p.min_cardinality} persone" in blocco
    assert f"{p.min_nodes_structural} persone" in blocco
    assert f"{p.voice_structural_min_sessions} serate" in blocco
    assert "7 · 14 · 28 giorni" in blocco
    assert "5 · 10 · 20%" in blocco
    # Le due che vengono dalla vista graph_snapshot_params, non da params.
    assert "memoria delle interazioni" in blocco
    assert "tempo minimo insieme in vocale" in blocco
    # Ogni regola porta la sua spiegazione, raggiungibile da tastiera.
    assert blocco.count('tabindex="0"') == 8
    assert blocco.count('class="regola__popup"') == 8


def test_una_regola_sparisce_quando_manca_una_delle_sue_chiavi():
    """Nessun valore di ripiego: una regola con un numero inventato e' peggio di
    una regola assente, perche' ha l'aria di essere misurata."""
    senza_partner = {k: v for k, v in PARAMS_COMPLETI.items() if k != "partner_min_interactions"}
    codici = {r.codice for r in regole.costruisci(senza_partner)}

    assert "integrato" not in codici
    assert "rete_minima" in codici  # le altre restano
    assert regole.costruisci({}) == ()
    assert regole.costruisci(None) == ()


def test_una_run_con_parametri_parziali_mostra_solo_le_regole_che_puo(dashboard):
    """GUILD_MATURE registra tre parametri soli in ``params``: da li' esce una
    regola sola. I parametri del grafo ci sono, e portano le altre due."""
    html = dashboard.get(f"/guilds/{GUILD_MATURE}").text
    blocco = html[html.index('<ul class="regole"'):html.index("</ul>", html.index('<ul class="regole"'))]

    assert blocco.count('class="regola"') == 3
    assert "rete minima" in blocco
    assert "memoria delle interazioni" in blocco
    assert "tempo minimo insieme in vocale" in blocco
    assert "per dire" not in blocco


def test_le_frasi_si_compongono_dal_valore_e_non_sono_scritte_a_mano():
    """Il numero nella frase cambia con ``params``. Senza questo test, una frase
    con "5" battuto dentro resterebbe "5" il giorno in cui params dice 8."""
    diversi = {**PARAMS_COMPLETI, "min_cardinality": 8, "min_nodes_publish": 12}
    (soglia,) = [r for r in regole.costruisci(diversi) if r.codice == "soglia_pubblicazione"]

    assert soglia.valore == "8 persone"
    assert "meno di 8 membri" in soglia.spiegazione
    # Le due soglie divergono: la seconda si dice, invece di restare nascosta.
    assert "12" in soglia.spiegazione
    # E quando coincidono non si dice due volte.
    (uguale,) = [r for r in regole.costruisci(PARAMS_COMPLETI) if r.codice == "soglia_pubblicazione"]
    assert "Per le misure sulla forma della rete" not in uguale.spiegazione


# --- la cadenza: osservata, non dichiarata -----------------------------------


def test_la_cadenza_e_la_mediana_degli_intervalli_fra_le_run():
    def run_a(*giorni_fa: int) -> list[RunRow]:
        return [
            RunRow(snapshot_id=i, as_of=ADESSO - timedelta(days=g), params={}, stats={})
            for i, g in enumerate(giorni_fa)
        ]

    # Con una sola run la cadenza non esiste: un intervallo si misura fra due
    # punti, e il ripiego sarebbe un numero inventato.
    assert stato.cadenza_osservata([]) is None
    assert stato.cadenza_osservata(run_a(0)) is None

    assert stato.cadenza_osservata(run_a(0, 7)) == timedelta(days=7)
    # Quindicinale: la pagina segue i dati, non un valore scritto nel codice.
    assert stato.cadenza_osservata(run_a(0, 14, 28)) == timedelta(days=14)

    # Un intervallo anomalo in mezzo a intervalli regolari NON sposta la
    # previsione: e' la ragione per cui si usa la mediana e non l'ultimo
    # intervallo ne' la media. Qui gli intervalli sono 7, 7, 40, 7, 7.
    con_anomalia = run_a(0, 7, 14, 54, 61, 68)
    assert stato.cadenza_osservata(con_anomalia) == timedelta(days=7)
    # La media sarebbe 13,6 giorni, cioe' quasi il doppio: il test non passa
    # perche' i due criteri coincidono per caso.
    intervalli = [7, 7, 40, 7, 7]
    assert sum(intervalli) / len(intervalli) != 7

    # Due run allo stesso as_of (finestre diverse a parita' di as_of: il caso
    # che graph_snapshots_identity ammette) non fanno un intervallo di zero.
    stesso = [
        RunRow(snapshot_id=1, as_of=ADESSO, params={}, stats={}),
        RunRow(snapshot_id=2, as_of=ADESSO, params={}, stats={}),
    ]
    assert stato.cadenza_osservata(stesso) is None


def test_la_pagina_segue_una_cadenza_diversa_da_sette_giorni(dashboard):
    """GUILD_SCALE ha due run a quarantanove giorni di distanza.

    Con ``default_window_days`` importato da ``job/config.py`` — com'era fino al
    22/09/2026 — questa pagina avrebbe detto "fra sette giorni" su una guild che
    non ha mai avuto quella cadenza, e nessun test sarebbe fallito.
    """
    html = dashboard.get(f"/guilds/{GUILD_SCALE}").text
    fatti = html[html.index('<dl class="fatti-stato"'):html.index("</dl>")]

    # 14 set + 49 giorni = 2 novembre.
    assert "2 nov" in fatti
    assert "21 set" not in fatti


def test_con_una_run_sola_il_prossimo_aggiornamento_non_compare(dashboard):
    # GUILD_EDGE ha una run sola: nessuna cadenza, nessuna previsione.
    html = dashboard.get(f"/guilds/{GUILD_EDGE}").text

    assert "In osservazione da" in html
    assert "Dati aggiornati a" in html
    assert "Prossimo aggiornamento" not in html
    # E nemmeno il tratteggio del futuro sulla linea del tempo.
    assert "asse--futuro" not in html


def test_nessun_modulo_della_dashboard_legge_piu_la_cadenza_da_job_config():
    """Il difetto che questo cambiamento ha tolto, scritto come controllo.

    ``default_window_days`` e' l'ampiezza di finestra del job: se il cron
    passasse a quindicinale resterebbe sette, e la pagina avrebbe continuato a
    dire sette senza che niente fallisse (CLAUDE.md 7). Ora la cadenza si misura
    sulle run, e questo test impedisce che la scorciatoia rientri.
    """
    for modulo in (stato, __import__("dashboard.domande", fromlist=["x"])):
        sorgente = Path(modulo.__file__).read_text(encoding="utf-8")
        codice = re.sub(r'""".*?"""', "", sorgente, flags=re.S)
        codice = "\n".join(
            r for r in codice.splitlines() if not r.lstrip().startswith("#")
        )
        assert "default_window_days" not in codice, modulo.__name__
        assert "DEFAULT_PARAMS" not in codice, modulo.__name__


# --- cosa non deve ricomparire ------------------------------------------------


def test_il_template_di_stato_non_nomina_code_version_stats_ne_chiavi_di_params():
    """Il controllo sta sul TESTO del template, non sulla pagina resa.

    Una pagina resa non contiene la chiave ``min_cardinality`` nemmeno se il
    template la scrive dentro una condizione mai presa: guardare solo il
    rendering di oggi sarebbe un controllo che passa senza controllare
    (CLAUDE.md 7).
    """
    import re

    sorgente = (TEMPLATES_DIR / "stato.html").read_text(encoding="utf-8")
    # I commenti Jinja NOMINANO cio' che e' uscito, ed e' il posto giusto per
    # dirlo: si tolgono, e si guarda tutto il resto. Toglierli per righe — come
    # faceva una prima versione di questo test — escludeva anche le righe
    # indentate dell'SVG, cioe' meta' del template: il controllo sarebbe passato
    # senza guardare (CLAUDE.md 7).
    markup = re.sub(r"\{#.*?#\}", "", sorgente, flags=re.S)
    assert "fatti-stato" in markup and "linea-tempo" in markup, "il markup non e' rimasto"

    for vietato in ("code_version", "stats", "snapshot_id"):
        assert vietato not in markup, vietato
    for chiave in PARAMS_COMPLETI:
        assert chiave not in markup, chiave


def test_la_pagina_resa_non_porta_piu_storico_parametri_ne_diagnostica(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text

    for uscito in (
        "Storico delle esecuzioni",
        "Diagnostica dell",
        "Versione del codice",
        "snapshot_gaps",
        "Cosa Kindling non fa",
    ):
        assert uscito not in html, uscito
    # E le chiavi di params non compaiono da nessuna parte nella pagina.
    for chiave in PARAMS_COMPLETI:
        assert chiave not in html, chiave


def test_stato_non_dice_mai_messaggi(dashboard):
    """I quattro layer si nominano per quello che sono su Discord.

    "Messaggi" prometterebbe una lettura dei contenuti che Kindling non fa:
    misura chi interagisce con chi, non cosa si sono detti.
    """
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    contenuto = html[html.index("<h1>"):html.index("<footer")]
    assert "messagg" not in contenuto.lower()
    for layer in ("risposte", "menzioni", "reazioni", "vocale"):
        assert layer in contenuto, layer


# --- errori: un aspetto diverso da ogni stato -------------------------------


def test_api_irraggiungibile_e_una_pagina_di_errore_distinta():
    def giu(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client(httpx.MockTransport(giu)) as client:
        r = client.get(f"/guilds/{GUILD_EDGE}")
        h = client.get("/health")

    assert r.status_code == 503
    assert 'class="errore"' in r.text
    assert "API di Kindling non risponde" in r.text  # l'apostrofo arriva escapato
    # Nessun rendering di stato dentro una pagina d'errore.
    assert 'class="cella' not in r.text and 'class="avviso"' not in r.text
    assert h.status_code == 503


def test_api_in_errore_5xx_e_irraggiungibile():
    with _client(httpx.MockTransport(lambda req: httpx.Response(503))) as client:
        assert client.get(f"/guilds/{GUILD_EDGE}").status_code == 503


def test_risposta_fuori_contratto_non_si_rende_come_dato():
    # Una risposta con i nomi del database invece di quelli del modello: e'
    # esattamente il caso in cui un accesso tollerante leggerebbe None.
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"guild_id": 1, "is_suppressed": False})

    with _client(httpx.MockTransport(api)) as client:
        r = client.get("/guilds/1")
    assert r.status_code == 502
    assert 'class="errore"' in r.text


def test_guild_sconosciuta_e_404_e_non_un_errore_di_sistema(dashboard):
    r = dashboard.get("/guilds/123")

    assert r.status_code == 404
    assert "Guild non osservata" in r.text
    assert 'class="errore"' not in r.text


def test_elenco_delle_guild_osservate(dashboard):
    html = dashboard.get("/").text
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE):
        assert f'href="/guilds/{gid}"' in html


def test_health_raggiunge_l_api(dashboard):
    r = dashboard.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api": "ok"}


# --- configurazione ----------------------------------------------------------


def test_nessun_default_per_l_indirizzo_dell_api(monkeypatch):
    monkeypatch.delenv("KINDLING_API_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "   ")
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "api:8000")
    with pytest.raises(RuntimeError, match="http"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "http://127.0.0.1:8899/")
    assert config.api_base_url() == "http://127.0.0.1:8899"


@pytest.mark.parametrize("nome", ["DATABASE_URL", "API_DATABASE_URL"])
def test_la_dashboard_non_parte_con_una_variabile_di_database(monkeypatch, nome):
    monkeypatch.setenv(nome, "")  # anche vuota: e' gia' una variabile ricevuta
    app = crea_app(
        api_http=httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture_app)),
        oauth=OAUTH_DI_TEST,
    )
    with pytest.raises(RuntimeError, match=nome):
        with TestClient(app):
            pass


def test_la_dashboard_non_parte_senza_indirizzo_dell_api(monkeypatch):
    monkeypatch.delenv("KINDLING_API_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        with TestClient(crea_app(oauth=OAUTH_DI_TEST)):
            pass


def test_data_ora_dichiara_sempre_utc():
    roma = timezone(timedelta(hours=2))
    assert data_ora(datetime(2026, 9, 7, 6, 15, tzinfo=roma)) == "lunedì 7 settembre 2026, 04:15 UTC"
    assert data_ora(None) == "—"


def _posizioni(testo: str, ago: str) -> list[int]:
    trovate, da = [], 0
    while True:
        i = testo.find(ago, da)
        if i < 0:
            return trovate
        trovate.append(i)
        da = i + 1
