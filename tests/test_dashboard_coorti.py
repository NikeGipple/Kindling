"""Vista Coorti: le regole si verificano sul MARKUP, come per Robustezza e Community.

Quello che qui non si puo' verificare altrove: il GRUPPO. Cinque righe dell'API
(due ambiti di onboarding, tre orizzonti di retention) diventano una riga di
tabella con una qualificazione condivisa, e la condivisione e' un fatto del job —
``job/metrics.py`` calcola ``cohort_members`` una volta sola e lo passa identico a
tutte e cinque le chiamate. Il template lo sfrutta; questi test verificano che sia
vero sui dati, invece di lasciarlo come assunzione implicita nel markup
(dashboard.md 4, "I rami difensivi").

Il fixture si monta in-process. Per gli stati che l'ultimo snapshot di una guild
non mostra — la coorte non matura E anteriore all'ancora, che esiste solo al run
del 27/07 di ``…004`` — si rendono i gruppi di quello snapshot con
``coorti.costruisci``: e' la stessa risposta che ``/cohorts?limit=1`` avrebbe
servito allora, non una fixture scritta a mano.

Le combinazioni di motivi non sono elencate a mano da nessuna parte: si leggono
dalle tre colonne tipizzate (``is_mature``, ``has_snapshot_coverage``,
``is_survivors_only``), mai da ``details``.
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.models import CohortGroup
from dashboard import config, coorti
from dashboard.client import DEFAULT_COHORTS_LIMIT, ApiClient
from dashboard.main import cornice, crea_app, crea_templates
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from dashboard.qualifica import NON_VALUTATO
from tests.test_dashboard_robustezza import Nodo, _albero
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_MATURE,
    GUILD_SCALE,
    GUILD_TODAY,
    PARAMS,
    SCENARIOS,
)
from tools.fixture_api import app as fixture_app

GUILDS = (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_SCALE)

# I campi che le cinque righe di un gruppo condividono per costruzione.
CONDIVISI = coorti.CONDIVISI_ONBOARDING


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


def _client(transport: httpx.AsyncBaseTransport) -> TestClient:
    # Dietro la guardia, con una sessione vera: vedi tests/sessione_dashboard.py.
    return client_autenticato(crea_app(
        api_http=httpx.AsyncClient(transport=transport, base_url="http://api.test"),
        oauth=OAUTH_DI_TEST,
    ))


@pytest.fixture
def dashboard():
    with _client(httpx.ASGITransport(app=fixture_app)) as client:
        yield client


@pytest.fixture
def api():
    with TestClient(fixture_app) as client:
        yield client


def _gruppi(api, guild_id: int, limit: int = DEFAULT_COHORTS_LIMIT) -> list[CohortGroup]:
    risposta = api.get(f"/guilds/{guild_id}/cohorts", params={"limit": limit})
    return TypeAdapter(list[CohortGroup]).validate_json(risposta.content)


def _rendi(gruppi: list[CohortGroup], guild_id: int = 1) -> str:
    return crea_templates().get_template("coorti.html").render(
        **cornice(guild_id=guild_id, vista=coorti.costruisci(gruppi), vista_corrente="coorti")
    )


def _dello_snapshot(guild_id: int, snapshot_id: int) -> list[CohortGroup]:
    return [g for g in SCENARIOS[guild_id]["cohorts"] if g.snapshot_id == snapshot_id]


def _righe(html: str) -> list[Nodo]:
    return list(_albero(html).radice.trova("tr", classe="riga-coorte"))


def _riga(html: str, coorte: str) -> Nodo:
    return next(_albero(html).radice.trova("tr", data_coorte=coorte))


def _celle(tr: Nodo) -> list[Nodo]:
    return [f for f in tr.figli if isinstance(f, Nodo) and f.tag == "td"]


def _cella(tr: Nodo, **attrs) -> Nodo:
    return next(d for d in tr.discendenti() if d.tag == "td" and all(
        d.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items()))


def _testo(nodo: Nodo) -> str:
    return " ".join(nodo.testo().split())


def _etichette(tr: Nodo) -> set[str]:
    return {
        c for d in tr.discendenti() if d.tag == "span"
        for c in d.classi if c.startswith("etichetta--")
    }


def _tabella(html: str) -> Nodo:
    """Il corpo della tabella. I controlli sul markup si fanno QUI, non sul testo
    della pagina: i nomi delle classi compaiono anche nel CSS di ``base.html``, e
    un ``in html`` li troverebbe li' senza che nessuna riga li porti."""
    return next(_albero(html).radice.trova("table", classe="tabella-coorti"))


def _pagine(api) -> list[tuple[str, int, list[CohortGroup], str]]:
    """(nome, guild, gruppi resi, html) per ogni pagina che vale la pena guardare."""
    pagine = [(str(gid), gid, _gruppi(api, gid)) for gid in GUILDS]
    # Gli snapshot che nessun "ultimo" mostra: il run vecchio di …004 (l'unico
    # con una coorte insieme immatura e anteriore all'ancora) e l'11 di …001.
    pagine.append(("…004@119", GUILD_SCALE, _dello_snapshot(GUILD_SCALE, 119)))
    pagine.append(("…001@11", GUILD_TODAY, _dello_snapshot(GUILD_TODAY, 11)))
    return [(nome, gid, gruppi, _rendi(gruppi, gid)) for nome, gid, gruppi in pagine]


def _combinazione(gruppo) -> tuple[str, ...]:
    """I motivi di non significativita' di una coorte, letti dalle COLONNE.

    Mai da ``details.not_significant_because``, che pure li duplica (regola 2).
    Una coorte soppressa non ha motivi: non ha nemmeno i campi da cui leggerli.
    """
    q = gruppo.onboarding[0].quality
    if q.suppressed:
        return ("soppressa",)
    return tuple(
        nome for nome, vero in (
            ("cohort_not_mature", q.is_mature is False),
            ("no_snapshot_coverage", q.has_snapshot_coverage is False),
            ("survivors_only_cohort", q.is_survivors_only is True),
        ) if vero
    )


def _tutte_le_coorti():
    for gid in GUILDS:
        for g in SCENARIOS[gid]["cohorts"]:
            yield gid, g


# --- l'invariante di gruppo -------------------------------------------------


def test_le_cinque_righe_di_una_coorte_condividono_la_qualificazione():
    """Il fatto su cui il layout a gruppo poggia, verificato su OGNI coorte.

    Non e' un'ovvieta': sarebbe bastato che ``compute_cohort`` usasse
    ``reached_at`` per uno solo di questi campi perche' ``any`` e ``voice``
    divergessero, e il gruppo mostrerebbe il valore di una riga sola come se fosse
    di tutte e cinque.
    """
    viste = 0
    for gid, g in _tutte_le_coorti():
        eg = f"{gid} {g.cohort_start}@{g.snapshot_id}"
        righe = g.onboarding
        assert len(righe) == len(PARAMS.cohort_layer_scopes), eg
        for campo in CONDIVISI:
            valori = {getattr(r.quality, campo) for r in righe}
            assert len(valori) == 1, f"{eg}: {campo} diverge fra gli ambiti: {valori}"
        # La soppressione e' per coorte: la soglia guarda lo stesso n_effective.
        tutte = [*g.onboarding, *g.retention]
        assert len({r.quality.suppressed for r in tutte}) == 1, eg
        for campo in coorti.CONDIVISI_RETENTION:
            assert len({getattr(r.quality, campo) for r in tutte}) == 1, f"{eg}: {campo}"
        viste += 1
    assert viste > 100


def test_nessun_gruppo_dichiara_divergenze(api):
    """Il ramo difensivo del template non scatta su nessuno scenario.

    Se scattasse, la pagina direbbe "le righe di questa coorte non concordano": e'
    il modo in cui la vista fallisce mostrando di piu' invece che di meno, e deve
    restare inerte finche' il job si comporta come si comporta.
    """
    for nome, _gid, _gruppi_resi, html in _pagine(api):
        assert not list(_tabella(html).trova("tr", classe="riga-divergenza")), nome
    for gid in GUILDS:
        vista = coorti.costruisci(SCENARIOS[gid]["cohorts"])
        assert all(g.divergenze == () for g in vista.gruppi), gid


def test_una_divergenza_costruita_si_dichiara_invece_di_sparire():
    """Il ramo difensivo esiste e funziona, provato senza passare dal fixture.

    La riga modificata qui NON entra in ``tools/fixture_api.py``: e' una risposta
    che l'API non produce, e insegnarla al fixture sarebbe il difetto che il
    fixture esiste per non commettere (dashboard.md 5, "I rami difensivi"). Vive
    dentro questo test, dove nessuno la puo' scambiare per un dato.
    """
    gruppi = [g.model_copy(deep=True) for g in _dello_snapshot(GUILD_SCALE, 126)]
    guasto = next(g for g in gruppi if not g.onboarding[0].quality.suppressed)
    guasto.onboarding[1].quality.is_mature = not guasto.onboarding[0].quality.is_mature

    vista = coorti.costruisci(gruppi)
    gruppo = next(g for g in vista.gruppi if g.cohort_start == guasto.cohort_start)
    assert gruppo.divergenze == ("is_mature",)
    assert "riga-divergenza" in _rendi(gruppi, GUILD_SCALE)


# --- la composizione delle etichette (regola 8) -----------------------------


def test_soppressione_azzera_solo_sopravvissuti_e_non_lo_nasconde():
    """La coorte del 10/08 di …001: soppressa, e per questo senza etichette.

    Prima della soppressione ``is_survivors_only`` sarebbe True — la coorte
    precede davvero l'ancora del 28/08. Dopo ``suppress()`` il campo e' None: non
    e' nascosta dietro il simbolo di soppressione, a livello di dato non esiste.
    """
    gruppo = next(g for g in _dello_snapshot(GUILD_TODAY, 12)
                  if g.cohort_start == date(2026, 8, 10))
    assert gruppo.onboarding[0].quality.suppressed is True
    assert gruppo.onboarding[0].quality.is_survivors_only is None
    assert all(r.quality.is_survivors_only is None for r in gruppo.retention)

    tr = _riga(_rendi(_dello_snapshot(GUILD_TODAY, 12), GUILD_TODAY), "2026-08-10")
    assert _etichette(tr) == set()
    assert "solo sopravvissuti" not in tr.html()
    assert "cella--soppresso" in tr.html()
    # E nemmeno le colonne condivise: n, maturita' e copertura non ci sono piu'.
    assert not list(tr.trova("td", data_campo="is_mature"))
    assert not list(tr.trova("td", data_campo="has_snapshot_coverage"))


def test_due_etichette_insieme_nella_stessa_posizione_di_riga():
    """La coorte del 17/08 di …001: ``no_snapshot_coverage`` E ``survivors_only``.

    Due etichette, non una scelta fra le due — e non tre: la copertura mancante si
    legge dalla colonna ``copertura``, che infatti dice "no" sulla stessa riga.
    """
    gruppi = _dello_snapshot(GUILD_TODAY, 12)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 17))
    q = gruppo.onboarding[0].quality
    assert (q.is_mature, q.has_snapshot_coverage, q.is_survivors_only) == (True, False, True)

    tr = _riga(_rendi(gruppi, GUILD_TODAY), "2026-08-17")
    assert _etichette(tr) == {"etichetta--non_significativo", "etichetta--solo_sopravvissuti"}
    assert _testo(_cella(tr, data_campo="has_snapshot_coverage")) == "no"
    # Le due etichette stanno nella stessa colonna, l'ultima: non attaccate a un
    # numero, e non in due posti diversi della riga.
    qualificazione = _celle(tr)[-1]
    assert "colonna-qualificazione" in qualificazione.classi
    assert qualificazione.html().count("etichetta--") == 2


def test_una_sola_etichetta_quando_il_solo_motivo_e_la_maturita():
    """La coorte del 07/09 di …001: non matura, ma posteriore all'ancora."""
    gruppi = _dello_snapshot(GUILD_TODAY, 12)
    q = next(g for g in gruppi if g.cohort_start == date(2026, 9, 7)).onboarding[0].quality
    assert (q.is_mature, q.is_survivors_only) == (False, False)

    tr = _riga(_rendi(gruppi, GUILD_TODAY), "2026-09-07")
    assert _etichette(tr) == {"etichetta--non_significativo"}
    assert _testo(_cella(tr, data_campo="is_mature")) == "no · 1 giorno su 14"


def test_zero_etichette_su_una_coorte_significativa():
    """La coorte significativa di …004: matura, coperta, posteriore all'ancora.

    Si mostra come una qualunque altra riga — nessuna etichetta, nessuna
    dequalificazione visiva — ed e' il caso che nessuna vista aveva ancora reso:
    in produzione non esiste ancora nessuna riga ``is_significant = true``.
    """
    gruppi = _dello_snapshot(GUILD_SCALE, 126)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 24))
    q = gruppo.onboarding[0].quality
    assert q.significant is True
    assert (q.is_mature, q.has_snapshot_coverage, q.is_survivors_only) == (True, True, False)

    tr = _riga(_rendi(gruppi, GUILD_SCALE), "2026-08-24")
    assert _etichette(tr) == set()
    assert "cella--dequalificata" not in tr.html()
    assert "cella--soppresso" not in tr.html()
    assert _testo(_cella(tr, data_campo="is_mature")) == "sì · 14 giorni"


def test_ogni_etichetta_compare_esattamente_una_volta_per_riga(api):
    """La verifica di dashboard.md 5: una volta per riga, mai zero e mai sette.

    Il macro che toglie le etichette dalle celle di valore e' anche il modo in cui
    potrebbero sparire del tutto, e la differenza fra "una volta" e "mai" non si
    vede guardando una pagina piena di grigio.
    """
    viste = 0
    for nome, _gid, gruppi, html in _pagine(api):
        per_coorte = {str(g.cohort_start): g.onboarding[0].quality for g in gruppi}
        for tr in _righe(html):
            coorte = tr.attrs["data-coorte"]
            q = per_coorte[coorte]
            attese = set()
            if not q.suppressed:
                if q.significant is False:
                    attese.add("etichetta--non_significativo")
                if q.is_survivors_only is True:
                    attese.add("etichetta--solo_sopravvissuti")
            for etichetta in attese:
                assert tr.html().count(etichetta) == 1, (nome, coorte, etichetta)
            assert tr.html().count("etichetta--") == len(attese), (nome, coorte)
            viste += 1
    assert viste > 50


def test_nessuna_cella_porta_mai_non_valutato(api):
    """``significant is None`` non produce etichette, mai (dashboard.md 5).

    Sulle coorti e' la meta' della tabella: ogni riga di retention ha
    ``significant`` None per costruzione — ``metric_cohort_retention`` non ha
    quella colonna — e le righe soppresse pure.
    """
    for nome, _gid, _gruppi_resi, html in _pagine(api):
        assert NON_VALUTATO not in html, nome
        assert "non valutat" not in html.lower(), nome


# --- la retention: tre stati, e il motivo non si indovina -------------------


def test_tre_stati_di_retention_sulla_stessa_riga():
    """La coorte del 31/08 di …001: 7g calcolabile, 14g e 28g no.

    Una cella piena e due vuote con motivo, sulla stessa riga di gruppo: la
    calcolabilita' varia per orizzonte, a differenza di tutto il resto della
    qualificazione, che e' condivisa.
    """
    gruppi = _dello_snapshot(GUILD_TODAY, 12)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 31))
    per_orizzonte = {r.horizon_days: r for r in gruppo.retention}
    assert per_orizzonte[7].values.retained_fraction == 12 / 14
    assert per_orizzonte[7].values.is_computable is True
    assert [per_orizzonte[h].values.not_computable_reason for h in (14, 28)] == [
        "horizon_not_reached", "horizon_not_reached"]

    tr = _riga(_rendi(gruppi, GUILD_TODAY), "2026-08-31")
    assert _testo(_cella(tr, data_orizzonte="7", data_campo="retained_fraction")) == "0,857"
    assert _testo(_cella(tr, data_orizzonte="7", data_campo="not_computable_reason")) == "calcolabile"
    for orizzonte in ("14", "28"):
        valore = _cella(tr, data_orizzonte=orizzonte, data_campo="retained_fraction")
        stato = _cella(tr, data_orizzonte=orizzonte, data_campo="not_computable_reason")
        assert "non calcolabile" in _testo(valore)
        assert _testo(stato) == "l'orizzonte non è ancora trascorso per tutta la coorte"
        # "non calcolabile" non e' "soppresso": parole diverse, simboli diversi.
        assert "cella--soppresso" not in valore.html()
    # Il motivo si dice UNA volta per cella, nella colonna dello stato.
    assert tr.html().count("l'orizzonte non è ancora trascorso") == 2


def test_il_motivo_della_retention_e_quello_che_sceglie_il_job():
    """Precedenza: ``before_observability_anchor`` vince su ``horizon_not_reached``.

    La coorte del 13/07 al run del 27/07 di …004 ha le due condizioni insieme —
    precede l'ancora del 16/07 **e** ha otto giorni di osservazione, quindi
    l'orizzonte a 14 e 28 giorni non e' trascorso per nessuno. ``compute_retention``
    controlla ``survivors_only`` per primo, e la UI non prova a indovinare quale
    delle due "avrebbe reso" la riga non calcolabile: mostra quella che il job ha
    scritto.
    """
    gruppi = _dello_snapshot(GUILD_SCALE, 119)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 7, 13))
    q = gruppo.onboarding[0].quality
    assert (q.is_mature, q.is_survivors_only) == (False, True)
    assert q.observation_days < PARAMS.min_observation_days

    tr = _riga(_rendi(gruppi, GUILD_SCALE), "2026-07-13")
    for orizzonte in ("7", "14", "28"):
        stato = _testo(_cella(tr, data_orizzonte=orizzonte, data_campo="not_computable_reason"))
        assert stato == "coorte anteriore all'inizio dell'osservazione"
        assert "orizzonte" not in stato
    # La riga porta comunque le sue due etichette: "chi e' stato contato" e
    # "quanto vale il numero" sono due domande diverse anche quando la causa a
    # monte e' la stessa (dashboard.md 4).
    assert _etichette(tr) == {"etichetta--non_significativo", "etichetta--solo_sopravvissuti"}


def test_empty_cohort_non_compare_su_nessuna_riga_pubblicata(api):
    """Il ramo difensivo di ``compute_retention``: ``n_effective = 0`` e' sempre soppressa."""
    for gid in GUILDS:
        for g in SCENARIOS[gid]["cohorts"]:
            for r in g.retention:
                assert r.values.not_computable_reason != "empty_cohort", (gid, g.cohort_start)


# --- integrazione: mediana, censure, quantili -------------------------------


def test_la_mediana_si_mostra_anche_con_un_evento_solo():
    """La coorte del 07/09 di …001: un evento su otto, mediana 4,58 giorni.

    Non si nasconde perche' "poggia su poco": si mostra con ``event_count``
    accanto, che e' il numero che disinnesca la lettura (regola 7). Nascondere il
    valore toglierebbe il dato; mostrarlo da solo mentirebbe.
    """
    gruppi = _dello_snapshot(GUILD_TODAY, 12)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 9, 7))
    valori = gruppo.onboarding[0].values
    assert (valori.event_count, valori.median_reached) == (1, True)
    assert valori.median_days_to_k == pytest.approx(4.5824878930)

    tr = _riga(_rendi(gruppi, GUILD_TODAY), "2026-09-07")
    assert _testo(_cella(tr, data_ambito="any", data_campo="median_days_to_k")) == "4,582"
    assert _testo(_cella(tr, data_ambito="any", data_campo="event_count")) == "1"


def test_mediana_non_raggiunta_e_una_frase_non_una_cella_vuota():
    """``median_reached = False`` non e' ne' soppressione ne' incalcolabilita'.

    E la frase non dice "meno di metà della coorte ha raggiunto k", che
    dashboard.md 5 dichiara falsa: dice che la curva non e' scesa al 50%.
    """
    gruppi = _dello_snapshot(GUILD_SCALE, 126)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 31))
    assert gruppo.onboarding[0].values.median_reached is False
    assert gruppo.onboarding[0].values.p25_days_to_k is not None

    tr = _riga(_rendi(gruppi, GUILD_SCALE), "2026-08-31")
    mediana = _cella(tr, data_ambito="any", data_campo="median_days_to_k")
    assert "cella--mediana_non_raggiunta" in mediana.html()
    assert "50%" in _testo(mediana)
    assert "meno di metà" not in _testo(mediana)
    assert "cella--soppresso" not in mediana.html()
    # Gli altri quantili restano leggibili: il flag e' puntuale.
    assert _testo(_cella(tr, data_ambito="any", data_campo="p25_days_to_k")).startswith("11,")


def test_censored_by_leave_e_una_nota_della_cella_non_una_colonna():
    """La coorte del 31/08 di …001: un evento, due uscite fra i censurati.

    ``censored_count = censored + censored_by_leave`` nel senso che entrambi sono
    censure: una colonna propria farebbe sembrare le uscite un terzo esito accanto
    a eventi e censure, invece di un dettaglio del secondo.
    """
    gruppi = _dello_snapshot(GUILD_TODAY, 12)
    valori = next(g for g in gruppi
                  if g.cohort_start == date(2026, 8, 31)).onboarding[0].values
    assert (valori.event_count, valori.censored_count, valori.censored_by_leave) == (1, 13, 2)

    html = _rendi(gruppi, GUILD_TODAY)
    tr = _riga(html, "2026-08-31")
    censurati = _cella(tr, data_ambito="any", data_campo="censored_count")
    assert _testo(censurati) == "13 di cui 2 per uscita dal server"
    assert next(censurati.trova("span", classe="cella__nota")).attrs["data-nota"] == "censored_by_leave"
    # Nessuna colonna: l'intestazione non la nomina, e la riga ha le sette celle
    # di integrazione previste per ambito, non otto.
    intestazioni = [_testo(th) for th in _albero(html).radice.trova("th")]
    assert not any("uscita" in t for t in intestazioni)
    assert len([d for d in tr.discendenti()
                if d.tag == "td" and d.attrs.get("data-ambito") == "any"]) == 7


def test_la_mediana_non_compare_mai_senza_gli_eventi_accanto(api):
    """Regola 7 sul markup, su ogni riga di ogni pagina.

    Una mediana di 4,58 giorni si legge come "in media ci mettono cinque giorni",
    e il numero che disinnesca quella lettura e' ``event_count``: dev'essere nella
    stessa riga E nello stesso ambito, perche' gli eventi di ``voice`` non dicono
    niente sulla mediana di ``any``.
    """
    viste = 0
    for nome, _gid, _gruppi_resi, html in _pagine(api):
        for tr in _righe(html):
            mediane = [td for td in tr.discendenti()
                       if td.tag == "td" and td.attrs.get("data-campo") == "median_days_to_k"]
            for td in mediane:
                ambito = td.attrs["data-ambito"]
                eventi = _cella(tr, data_ambito=ambito, data_campo="event_count")
                assert _testo(eventi) != "", (nome, tr.attrs["data-coorte"], ambito)
                viste += 1
    # Due celle di mediana per riga pubblicata, su sei pagine: nessuna riga
    # soppressa ne ha (li' non c'e' nessun numero da qualificare).
    assert viste > 80


def test_nessuna_colonna_di_curva_condivide_la_precisione_con_le_altre():
    """Mediana, quartili e frazioni raggiunte non formano un gruppo aritmetico.

    Sono cinque uscite indipendenti della stessa curva di Kaplan-Meier: nessuna si
    ottiene dalle altre con un'operazione visibile in tabella, quindi ognuna
    dichiara la propria risoluzione (dashboard.md 5, "Le colonne legate da
    un'operazione" — stesso criterio di node_overlap/stability_jaccard).
    """
    vista = coorti.costruisci(_dello_snapshot(GUILD_SCALE, 126))
    decimali = {campo: vista.decimali[("any", campo)] for campo in coorti.COLONNE_INTEGRAZIONE}
    assert min(decimali.values()) >= 3
    # Le due colonne dello stesso campo in ambiti diversi sono colonne DIVERSE:
    # uniformarle creerebbe una colonna sola che attraversa i due ambiti.
    assert set(vista.decimali) == {
        (ambito, campo) for ambito in coorti.AMBITI for campo in coorti.COLONNE_INTEGRAZIONE}
    assert set(vista.decimali_retention) == set(coorti.ORIZZONTI)


# --- la scala reale ---------------------------------------------------------


def test_venticinque_coorti_si_rendono_tutte_e_in_ordine(api):
    """La voce H di stato-progetto.md, sul markup: la tabella regge la scala vera.

    Venticinque gruppi come in produzione (14/09/2026), nessuno troncato, e le
    coorti recenti in cima: sono le uniche che possono ancora cambiare, e
    seppellirle sotto diciassette righe "solo sopravvissuti" e' esattamente il
    muro grigio che la voce H temeva.
    """
    gruppi = _gruppi(api, GUILD_SCALE)
    assert len(gruppi) == 25
    assert len({g.snapshot_id for g in gruppi}) == 1, "limit=1 porta uno snapshot solo"

    html = _rendi(gruppi, GUILD_SCALE)
    righe = _righe(html)
    assert len(righe) == len(gruppi)
    date_in_pagina = [r.attrs["data-coorte"] for r in righe]
    assert date_in_pagina == sorted((str(g.cohort_start) for g in gruppi), reverse=True)
    # Nessuna riga collassata su un'altra: niente rowspan, e ogni riga ha la sua
    # settimana e il suo n.
    assert "rowspan" not in html
    assert len(set(date_in_pagina)) == len(date_in_pagina)


def test_il_muro_di_solo_sopravvissuti_resta_leggibile_riga_per_riga(api):
    """Piu' righe consecutive con la stessa etichetta restano distinguibili.

    Ognuna porta la propria etichetta e i propri numeri: l'etichetta non si
    "collassa" a livello di blocco quando tutte le righe dicono la stessa cosa
    (dashboard.md 5, "Dove va l'etichetta di riga"), perche' una regola che sposta
    l'etichetta a seconda dei dati costringe a imparare due layout.
    """
    html = _rendi(_gruppi(api, GUILD_SCALE), GUILD_SCALE)
    righe = _righe(html)
    consecutive = 0
    massimo = 0
    firme = []
    for tr in righe:
        if "etichetta--solo_sopravvissuti" in tr.html():
            consecutive += 1
            massimo = max(massimo, consecutive)
            firme.append((tr.attrs["data-coorte"], _testo(_celle(tr)[1])))
        else:
            consecutive = 0
    assert massimo >= 5, "il fixture non esercita piu' una sequenza di sopravvissuti"
    assert len(set(firme)) == len(firme), "due righe del muro sono indistinguibili"


def test_la_frase_di_stato_dice_quante_coorti_e_in_che_stato(api):
    """Regola 1: una tabella grigia non deve somigliare a "non c'e' niente"."""
    html = _rendi(_gruppi(api, GUILD_SCALE), GUILD_SCALE)
    frase = _testo(next(_albero(html).radice.trova("p", classe="riepilogo")))
    assert frase.startswith("25 coorti su questo snapshot")
    assert "6 sotto la soglia di pubblicazione" in frase
    # Il denominatore sono le pubblicate: su una riga soppressa is_survivors_only
    # e' None, e contarla come "non sopravvissuti" abbasserebbe la percentuale.
    assert "13 delle 19 pubblicate" in frase and "(68%)" in frase
    assert "3 distinguibili dal rumore" in frase


def test_il_fixture_esercita_ogni_combinazione_raggiungibile():
    """Le combinazioni di motivi che il job puo' produrre sono tutte nel fixture.

    Due delle sette non ci sono, e non e' un buco: sono IRRAGGIUNGIBILI, verificato
    per esecuzione (tools/fixture_api.py::_scenario_scala). Con finestre da 7
    giorni e ``min_observation_days`` 14, una coorte non matura e' iniziata da meno
    di 21 giorni e la finestra dello snapshot su cui la run gira la copre sempre:
    ``cohort_not_mature`` + ``no_snapshot_coverage`` non puo' esistere, e nemmeno
    la tripla che la implica.

    Il test serve al caso in cui quei parametri cambino: allora la coppia diventa
    possibile, questo elenco diventa falso, e qualcuno lo scopre qui invece che
    davanti a una riga mai renderizzata.
    """
    finestra = PARAMS.default_window_days if hasattr(PARAMS, "default_window_days") else 7.0
    assert PARAMS.min_observation_days == 14 and finestra == 7.0, (
        "i parametri che rendono irraggiungibili due combinazioni sono cambiati: "
        "vanno rilette le assenze, non aggiornato l'elenco"
    )
    viste = {_combinazione(g) for _gid, g in _tutte_le_coorti()}
    assert viste == {
        ("soppressa",),
        (),                                                  # significativa
        ("cohort_not_mature",),
        ("no_snapshot_coverage",),
        ("survivors_only_cohort",),
        ("cohort_not_mature", "survivors_only_cohort"),
        ("no_snapshot_coverage", "survivors_only_cohort"),
    }


# --- la rotta e il limite ---------------------------------------------------


def test_la_vista_chiede_sempre_uno_snapshot_solo():
    """``limit=1`` viaggia nella richiesta, non si eredita dal default dell'API.

    Si verifica sulla CHIAMATA e non sul risultato: con il fixture montato il
    risultato sarebbe lo stesso anche con ``limit`` assente, perche' il default del
    server e' un valore del server — e il giorno in cui cambia, questa vista
    cambierebbe con lui senza che nessuno l'avesse chiesto.
    """
    richieste: list[httpx.Request] = []

    def risponde(request: httpx.Request) -> httpx.Response:
        richieste.append(request)
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(risponde)) as client:
        assert client.get(f"/guilds/{GUILD_TODAY}/coorti").status_code == 200

    assert len(richieste) == 1
    assert richieste[0].url.path == f"/guilds/{GUILD_TODAY}/cohorts"
    assert richieste[0].url.params["limit"] == "1"
    # E il default sta nella firma, dove si legge: non nel server.
    assert inspect.signature(ApiClient.cohorts).parameters["limit"].default == 1


def test_la_rotta_risponde_e_la_navigazione_porta_a_coorti(dashboard):
    risposta = dashboard.get(f"/guilds/{GUILD_SCALE}/coorti")
    assert risposta.status_code == 200
    albero = _albero(risposta.text).radice
    voci = [(_testo(a), a.attrs.get("href"), a.attrs.get("aria-current"))
            for a in albero.trova("a") if "/guilds/" in (a.attrs.get("href") or "")]
    assert voci == [
        ("Stato", f"/guilds/{GUILD_SCALE}", None),
        ("Robustezza", f"/guilds/{GUILD_SCALE}/robustezza", None),
        ("Community", f"/guilds/{GUILD_SCALE}/community", None),
        ("Coorti", f"/guilds/{GUILD_SCALE}/coorti", "page"),
        # Domande e' nella stessa barra ma non e' una vista: non mostra nessun
        # numero di nessun server, e il foglio la stacca dalle quattro.
        ("Domande", f"/guilds/{GUILD_SCALE}/domande", None),
    ]


def test_le_tre_assenze_hanno_tre_pagine_diverse(dashboard):
    """Guild mai vista, guild senza snapshot, API che non risponde: tre cose diverse."""
    assert dashboard.get("/guilds/123456789/coorti").status_code == 404

    vuota = crea_templates().get_template("coorti.html").render(
        **cornice(guild_id=1, vista=coorti.costruisci([]), vista_corrente="coorti"))
    assert list(_albero(vuota).radice.trova("p", classe="frase-vuota"))
    assert not list(_albero(vuota).radice.trova("table"))

    def rotta(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nessuna rete")

    with _client(httpx.MockTransport(rotta)) as client:
        risposta = client.get(f"/guilds/{GUILD_TODAY}/coorti")
    assert risposta.status_code == 503
    assert "errore" in risposta.text


def test_details_non_si_legge_mai_in_questa_vista():
    """Regola 2, sul sorgente: qui non serve nemmeno, i tre motivi sono colonne.

    Sull'AST e non sul testo: il modulo PARLA di ``quality.details`` nella
    docstring, per dire che non lo legge, e un controllo testuale non saprebbe
    distinguere la spiegazione dall'accesso — cioe' fallirebbe sul commento giusto
    e passerebbe su quello sbagliato.
    """
    albero = ast.parse(inspect.getsource(coorti))
    accessi = [n for n in ast.walk(albero)
               if isinstance(n, ast.Attribute) and n.attr == "details"]
    assert accessi == []

    template = open(coorti.__file__.replace("coorti.py", "templates/coorti.html"),
                    encoding="utf-8").read()
    # Senza i commenti Jinja, che dicono la stessa cosa a parole.
    codice = re.sub(r"\{#.*?#\}", "", template, flags=re.S)
    assert not re.search(r"\.details\b", codice)
    assert "not_significant_because" not in codice
