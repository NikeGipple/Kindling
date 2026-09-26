"""La vista Coorti per chi amministra il server (dashboard.md 4, riscritta il 26/09/2026).

Quello che qui non si puo' verificare guardando la pagina piena: che una coorte
ANTERIORE all'arrivo del bot non compaia (e' l'assenza a dover essere provata),
che una coorte in osservazione con la curva "bella" non mostri nemmeno una barra,
che i bordi delle fasce reggano la virgola mobile del job, e che le date si
ricavino da ``as_of`` e dalla cadenza osservata e non da un numero battuto a mano.

I dati vengono dal fixture, che chiama le funzioni del job. Dove uno stato non
c'e' — due coorti leggibili, piu' di dodici coorti posteriori all'ancora, un
ambito solo senza numero — si costruisce qui: con le funzioni del fixture e del
job quando si puo', con una copia modificata di righe vere quando serve una
combinazione precisa. Niente di tutto questo entra in ``tools/fixture_api.py``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import unescape

import httpx
import pytest
from fastapi.testclient import TestClient

from api.models import RunRow
from dashboard import config, coorti, domande
from dashboard.main import STATIC_DIR, cornice, crea_app, crea_templates
from job.cohorts import CohortMember, compute_cohort
from job.config import MetricParams
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tests.test_dashboard_robustezza import Nodo, _albero
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_MATURE,
    GUILD_SCALE,
    GUILD_TODAY,
    SCENARIOS,
    _coorti,
    _membri_generati,
)
from tools.fixture_api import app as fixture_app

UTC = timezone.utc
GUILDS = (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_SCALE)


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def dashboard():
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture_app), base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        yield client


# --- aiuti -------------------------------------------------------------------


def _ultimo(guild_id: int) -> list:
    """Le coorti dell'ultimo snapshot, come le serve ``/cohorts?limit=1``."""
    gruppi = SCENARIOS[guild_id]["cohorts"]
    ultimo = max((g.as_of, g.snapshot_id) for g in gruppi)[1]
    return [g for g in gruppi if g.snapshot_id == ultimo]


def _settimanali(gruppi, guild_id: int, quante: int = 2) -> list[RunRow]:
    """Run a cadenza esattamente settimanale che finiscono sullo snapshot di ``gruppi``.

    Il fixture di …004 ha due run a 49 giorni di distanza, e la vista — come
    Stato — data i calcoli con la cadenza OSSERVATA: 49 giorni. Per leggere le
    date in un caso settimanale, qui si danno alla funzione run settimanali con i
    params veri dello scenario. E' un argomento della funzione pura, non un dato
    del fixture.
    """
    vera = next(r for r in SCENARIOS[guild_id]["runs"] if r.snapshot_id == gruppi[0].snapshot_id)
    return [
        vera.model_copy(update={"snapshot_id": vera.snapshot_id - k,
                                "as_of": vera.as_of - timedelta(weeks=k)})
        for k in range(quante)
    ]


def _pagina(gruppi, guild_id: int, runs=None) -> coorti.Pagina:
    return coorti.pagina(
        gruppi, SCENARIOS[guild_id]["guild"],
        SCENARIOS[guild_id]["runs"] if runs is None else runs,
    )


def _rendi(vista: coorti.Pagina, guild_id: int = GUILD_SCALE) -> str:
    return crea_templates().get_template("coorti.html").render(
        **cornice(guild_id=guild_id, vista=vista, vista_corrente="coorti"))


def _main(html: str) -> str:
    return html[html.index("<main"):html.index("</main>")]


def _righe(html: str) -> list[Nodo]:
    return list(_albero(html).radice.trova("tr", classe="coorte"))


def _riga(html: str, coorte: str) -> Nodo:
    return next(_albero(html).radice.trova("tr", data_coorte=coorte))


def _stato(tr: Nodo) -> str:
    return next(c for c in tr.classi if c.startswith("coorte--")).removeprefix("coorte--")


def _cella(tr: Nodo, etichetta: str) -> Nodo:
    return next(tr.trova("td", data_etichetta=etichetta))


def _testo(nodo: Nodo) -> str:
    return " ".join(nodo.testo().split())


def _barre(nodo: Nodo) -> list[Nodo]:
    return list(nodo.trova("span", classe="barra"))


# --- solo le coorti posteriori all'arrivo del bot ----------------------------


def test_le_due_forme_del_filtro_sull_ancora_coincidono_su_ogni_riga_pubblicata():
    """Il flag del job e il confronto di date della vista dicono la stessa cosa.

    La vista filtra le pubblicate su ``is_survivors_only`` e le soppresse sulla
    data, perche' ``suppress()`` azzera il flag. Le due forme sono equivalenti
    solo se il confronto di date e' LO STESSO del job — stesso operatore, stessa
    data UTC. Si verifica dove si puo': su ogni riga pubblicata di ogni snapshot.
    """
    viste = 0
    for gid in GUILDS:
        ancora = SCENARIOS[gid]["guild"].first_seen_at
        for g in SCENARIOS[gid]["cohorts"]:
            q = g.onboarding[0].quality
            if q.suppressed:
                continue
            per_data = g.cohort_start < ancora.astimezone(UTC).date()
            assert per_data is q.is_survivors_only, (gid, g.cohort_start)
            viste += 1
    assert viste > 50


def test_le_coorti_anteriori_all_ancora_non_compaiono_nemmeno_se_soppresse():
    gruppi = _ultimo(GUILD_SCALE)
    ancora = SCENARIOS[GUILD_SCALE]["guild"].first_seen_at.date()
    html = _rendi(_pagina(gruppi, GUILD_SCALE))
    in_pagina = {tr.attrs["data-coorte"] for tr in _righe(html)}

    posteriori = {str(g.cohort_start) for g in gruppi if g.cohort_start >= ancora}
    assert in_pagina == posteriori
    # Le due soppresse anteriori all'ancora del 16/07 non ci sono; la soppressa
    # del 17/08, posteriore, si': con la sua frase e senza numero di persone.
    soppresse = {str(g.cohort_start) for g in gruppi if g.onboarding[0].quality.suppressed}
    assert {"2026-06-22", "2026-05-04"} <= soppresse - in_pagina
    tr = _riga(html, "2026-08-17")
    assert _stato(tr) == coorti.SOTTO_SOGLIA
    assert _testo(tr) == "17 agosto meno di 5 ingressi: non mostrata"
    assert not list(tr.trova("td", classe="persone"))


def test_la_soglia_della_frase_viene_dai_params():
    gruppi = _ultimo(GUILD_SCALE)
    runs = [r.model_copy(update={"params": {}}) for r in SCENARIOS[GUILD_SCALE]["runs"]]
    tr = _riga(_rendi(_pagina(gruppi, GUILD_SCALE, runs)), "2026-08-17")
    assert "troppo pochi ingressi" in _testo(tr)
    assert re.search(r"\d+ ingressi", _testo(tr)) is None


# --- i quattro stati --------------------------------------------------------


def test_il_fixture_004_esercita_i_quattro_stati_posteriori_all_ancora():
    """Leggibile, in osservazione, sotto la soglia, e il caso difensivo.

    La richiesta per la vista era di verificarlo prima di inventare uno scenario:
    …004 li ha gia' tutti, calcolati dal job — incluso il caso senza copertura di
    snapshot (la coorte del 03/08, che due settimane senza run lasciano scoperta).
    """
    stati = {r.cohort_start.isoformat(): r.stato for r in _pagina(_ultimo(GUILD_SCALE), GUILD_SCALE).righe}
    assert stati == {
        "2026-09-07": coorti.IN_OSSERVAZIONE,
        "2026-08-31": coorti.IN_OSSERVAZIONE,
        "2026-08-24": coorti.LEGGIBILE,
        "2026-08-17": coorti.SOTTO_SOGLIA,
        "2026-08-10": coorti.LEGGIBILE,
        "2026-08-03": coorti.NON_OSSERVATA,
        "2026-07-27": coorti.LEGGIBILE,
        "2026-07-20": coorti.SOTTO_SOGLIA,
    }


def test_su_una_coorte_posteriore_all_ancora_non_significativa_vuol_dire_immatura_o_scoperta():
    """La verifica per esecuzione dietro al quarto stato, su ogni snapshot del fixture.

    Se il job aggiungesse un motivo, questo test lo direbbe; la vista no — ed e'
    voluto: la sua condizione e' "matura e non significativa", non "senza
    copertura", quindi un motivo nuovo cadrebbe nel caso difensivo da solo.
    """
    visti = set()
    for gid in GUILDS:
        ancora = SCENARIOS[gid]["guild"].first_seen_at.date()
        for g in SCENARIOS[gid]["cohorts"]:
            q = g.onboarding[0].quality
            if q.suppressed or g.cohort_start < ancora or q.significant is True:
                continue
            motivi = (q.is_mature is False, q.has_snapshot_coverage is False)
            assert any(motivi), (gid, g.cohort_start)
            visti.add(motivi)
    assert visti == {(True, False), (False, True)}


def test_il_caso_difensivo_senza_copertura_ha_una_frase_sua():
    q = next(g for g in _ultimo(GUILD_SCALE) if g.cohort_start == date(2026, 8, 3)).onboarding[0].quality
    assert (q.is_mature, q.has_snapshot_coverage, q.significant) == (True, False, False)

    tr = _riga(_rendi(_pagina(_ultimo(GUILD_SCALE), GUILD_SCALE)), "2026-08-03")
    assert _stato(tr) == coorti.NON_OSSERVATA
    assert "Kindling non ha osservato abbastanza questa settimana per leggerla" in _testo(tr)
    assert _testo(next(tr.trova("td", classe="persone"))) == "11"
    assert not _barre(tr)


def test_una_coorte_in_osservazione_con_la_curva_bella_non_mostra_nessuna_barra():
    """La coorte del 24/08 di …002: non matura, e ``reached_by`` a 1,000 su tutto.

    E' la forma della coorte del 07/09 in produzione: la coda della curva di
    Kaplan-Meier che va a zero su una persona sola a rischio. Una barra "tutti"
    su quella riga sarebbe il numero piu' bello della pagina e il meno vero —
    nemmeno la retention a 7 giorni, che pure e' calcolabile, compare.
    """
    gruppo = next(g for g in _ultimo(GUILD_MATURE) if g.cohort_start == date(2026, 8, 24))
    q, v = gruppo.onboarding[0].quality, gruppo.onboarding[0].values
    assert q.is_mature is False
    assert (v.reached_by_14d, v.reached_by_28d) == (1.0, 1.0)
    assert next(r for r in gruppo.retention if r.horizon_days == 7).values.is_computable is True

    tr = _riga(_rendi(_pagina(_ultimo(GUILD_MATURE), GUILD_MATURE), GUILD_MATURE), "2026-08-24")
    assert _stato(tr) == coorti.IN_OSSERVAZIONE
    assert not _barre(tr)
    assert 'role="img"' not in tr.html()
    assert "tutti" not in _testo(tr)
    assert _testo(tr).startswith("24 agosto 24 in osservazione")


def test_la_data_di_leggibilita_viene_da_as_of_e_dalla_cadenza_osservata():
    gruppi = _ultimo(GUILD_SCALE)
    righe = {r.cohort_start.isoformat(): r for r in _pagina(gruppi, GUILD_SCALE, _settimanali(gruppi, GUILD_SCALE)).righe}
    # as_of 14/09. 07/09: un giorno di osservazione, ne mancano 13 -> il secondo
    # calcolo settimanale, il 28/09. 31/08: sette giorni, ne mancano 7 -> il 21/09.
    assert righe["2026-09-07"].frase == "in osservazione · leggibile dal calcolo di lunedì 28 settembre"
    assert righe["2026-08-31"].frase == "in osservazione · leggibile dal calcolo di lunedì 21 settembre"
    assert righe["2026-08-31"].leggibile_dal == date(2026, 9, 21)


def test_le_date_della_produzione_del_21_settembre():
    """I tre casi letti in produzione (snapshot 13), rifatti con la funzione della vista."""
    as_of = datetime(2026, 9, 21, tzinfo=UTC)
    settimana = timedelta(days=7)
    assert coorti.calcolo_che_vede(as_of, 14 - 0, settimana).date() == date(2026, 10, 5)
    assert coorti.calcolo_che_vede(as_of, 14 - 8, settimana).date() == date(2026, 9, 28)
    # La retention a 28 giorni del 31/08, quattordici giorni osservati.
    assert coorti.calcolo_che_vede(as_of, 28 - 14, settimana).date() == date(2026, 10, 5)


def test_senza_cadenza_o_senza_soglia_la_data_non_compare():
    """Una run sola: nessun intervallo, nessuna data. Mai un ripiego inventato."""
    gruppi = _ultimo(GUILD_TODAY)
    una = [max(SCENARIOS[GUILD_TODAY]["runs"], key=lambda r: r.as_of)]
    vista = _pagina(gruppi, GUILD_TODAY, una)
    assert {r.frase for r in vista.righe} == {"in osservazione"}
    assert vista.avviso is None
    assert coorti.calcolo_che_vede(datetime(2026, 9, 21, tzinfo=UTC), 7, None) is None

    # …002 registra tre params soli, e min_observation_days non e' fra quelli:
    # niente "leggibile dal", e nemmeno la frase sulle settimane in osservazione.
    maturo = _pagina(_ultimo(GUILD_MATURE), GUILD_MATURE)
    assert maturo.giorni_maturita is None and maturo.frase_osservazione is None
    osservate = [r for r in maturo.righe if r.stato == coorti.IN_OSSERVAZIONE]
    assert osservate and all(r.frase == "in osservazione" for r in osservate)


def test_observation_days_e_troncato_dall_ultimo_ingresso():
    """Il fatto su cui poggia la derivazione delle date, eseguito sul job."""
    params = MetricParams()
    inizio = datetime(2026, 8, 31, tzinfo=UTC)
    as_of = datetime(2026, 9, 21, tzinfo=UTC)

    def osservati(ultimo: datetime) -> tuple[int, bool]:
        membri = [CohortMember(i, inizio) for i in range(5)] + [CohortMember(9, ultimo)]
        r = compute_cohort(cohort_start=inizio.date(), layer_scope="any", members=membri,
                           reached_at={}, excluded_rejoins=0, as_of=as_of, params=params,
                           observability_anchor=inizio,
                           snapshot_windows=[(as_of - timedelta(days=7), as_of)])
        return r.observation_days, r.is_mature

    assert osservati(as_of - timedelta(days=14)) == (14, True)
    assert osservati(as_of - timedelta(days=13, hours=23)) == (13, False)
    assert osservati(as_of - timedelta(days=7, hours=12)) == (7, False)


# --- la riga leggibile --------------------------------------------------------


def test_la_riga_leggibile_ha_cinque_celle_e_nessun_altro_numero():
    gruppi = _ultimo(GUILD_SCALE)
    tr = _riga(_rendi(_pagina(gruppi, GUILD_SCALE)), "2026-07-27")
    assert _stato(tr) == coorti.LEGGIBILE
    etichette = [td.attrs["data-etichetta"] for td in tr.trova("td")]
    assert etichette == ["persone", "dopo 7 gg", "dopo 14 gg", "dopo 28 gg", "entro 14 gg", "entro 28 gg"]
    assert _testo(next(tr.trova("td", classe="persone"))) == "13"
    # Tre barre grigie di presenza, due per cella di integrazione.
    for etichetta in ("dopo 7 gg", "dopo 14 gg", "dopo 28 gg"):
        assert [b.classi & {"misura--presenti"} for b in _barre(_cella(tr, etichetta))] == [{"misura--presenti"}]
    for etichetta in ("entro 14 gg", "entro 28 gg"):
        misure = [next(c for c in b.classi if c.startswith("misura--")) for b in _barre(_cella(tr, etichetta))]
        assert misure == ["misura--any", "misura--voice"]


def test_retention_a_28_giorni_non_raggiunta_dice_da_quando():
    gruppi = _ultimo(GUILD_SCALE)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 24))
    assert next(r for r in gruppo.retention if r.horizon_days == 28).values.not_computable_reason == "horizon_not_reached"
    assert gruppo.onboarding[0].quality.observation_days == 14

    html = _rendi(_pagina(gruppi, GUILD_SCALE, _settimanali(gruppi, GUILD_SCALE)))
    cella = _cella(_riga(html, "2026-08-24"), "dopo 28 gg")
    # as_of 14/09 + (28 - 14) giorni = 28/09, che e' un calcolo settimanale.
    assert _testo(cella) == "dal 28 settembre"
    assert not _barre(cella)


def test_integrazione_a_28_giorni_null_dice_non_ancora_senza_data():
    gruppi = _ultimo(GUILD_MATURE)
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 10))
    assert {r.values.reached_by_28d for r in gruppo.onboarding} == {None}
    assert gruppo.onboarding[0].quality.significant is True

    cella = _cella(_riga(_rendi(_pagina(gruppi, GUILD_MATURE), GUILD_MATURE), "2026-08-10"), "entro 28 gg")
    assert _testo(cella) == "non ancora"
    assert not _barre(cella)


def test_un_ambito_solo_senza_numero_lascia_il_campione_e_non_una_barra_vuota():
    """``any`` a None e ``voice`` a 1,0: succede davvero (sotto, la barra blu).

    Una barra vuota si leggerebbe "nessuno", che e' un numero; qui il numero non
    c'e'. Il campione del colore arancio con "non ancora" dice quale dei due manca.
    """
    gruppi = [g.model_copy(deep=True) for g in _ultimo(GUILD_SCALE)]
    gruppo = next(g for g in gruppi if g.cohort_start == date(2026, 8, 24))
    per_ambito = {r.layer_scope: r for r in gruppo.onboarding}
    per_ambito["any"].values.reached_by_28d = None
    assert per_ambito["voice"].values.reached_by_28d == 1.0

    cella = _cella(_riga(_rendi(_pagina(gruppi, GUILD_SCALE)), "2026-08-24"), "entro 28 gg")
    barre = _barre(cella)
    assert len(barre) == 1 and "misura--voice" in barre[0].classi
    assente = next(cella.trova("span", classe="barra-assente"))
    assert "misura--any" in next(assente.trova("span", classe="campione-barra")).classi
    assert _testo(assente) == "in qualunque modo: non ancora"


# --- le fasce -----------------------------------------------------------------


@pytest.mark.parametrize("frazione, indice", [
    (0.0, 0), (-0.0, 0),
    (0.05, 1), (0.050000000000000044, 1), (0.19999, 1),
    (0.2, 2), (1 - 0.8, 2), (0.39999, 2),
    (0.4, 3), (0.5, 3), (0.6, 3), (1 - 0.4, 3),
    (0.60001, 4), (0.79999, 4),
    (0.8, 5), (0.99999, 5), (1 - 1e-6, 5),
    (1.0, 6), (0.9999999999999999, 6),
])
def test_i_bordi_delle_fasce(frazione, indice):
    """0 e 1 esatti sono fasce a se'; 0,2 e 0,8 aprono la successiva; "circa
    meta'" comprende 0,4 e 0,6. ``1 - 0.8`` in Python e' 0,19999999999999996, e
    senza l'arrotondamento a nove cifre un quinto esatto sarebbe "pochissimi"."""
    assert coorti.fascia(frazione).indice == indice
    assert coorti.fascia(frazione).parola == coorti.FASCE[indice]


def test_una_frazione_assente_non_ha_fascia():
    assert coorti.fascia(None) is None


def test_la_parola_della_fascia_sta_nell_aria_label_e_la_larghezza_nella_classe(dashboard):
    viste = 0
    for gid in (GUILD_SCALE, GUILD_MATURE, GUILD_EDGE):
        html = dashboard.get(f"/guilds/{gid}/coorti").text
        for tr in _righe(html):
            for barra in _barre(tr):
                indice = int(next(c for c in barra.classi if c.startswith("fascia-")).removeprefix("fascia-"))
                assert barra.attrs["role"] == "img"
                assert unescape(barra.attrs["aria-label"]).endswith(coorti.FASCE[indice])
                # La parola non e' nel testo visibile: le barre non hanno testo.
                assert _testo(barra) == ""
                viste += 1
    assert viste > 40


def test_sette_larghezze_fisse_nel_foglio():
    """Le larghezze sono classi del foglio, mai style="" (la CSP lo vieta)."""
    foglio = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
    larghezze = dict(re.findall(r"\.fascia-(\d) \.barra__pieno \{ width: ([\d%]+); \}", foglio))
    assert larghezze == {"0": "0", "1": "10%", "2": "30%", "3": "50%", "4": "70%", "5": "90%", "6": "100%"}
    for token in ("--integra-voce",):
        # Nel tema chiaro e nel tema scuro.
        assert foglio.count(f"{token}: #") == 2


# --- la barra blu puo' superare l'arancio -----------------------------------


def _raggiunti_due_ambiti(membri, any_, voice, as_of_giorno):
    inizio = datetime(2026, 8, 3, tzinfo=UTC)
    giorno = lambda d: inizio + timedelta(days=d)  # noqa: E731
    persone = [CohortMember(i, giorno(ingresso), None if uscita is None else giorno(uscita))
               for i, (ingresso, uscita) in enumerate(membri)]
    kw = dict(cohort_start=inizio.date(), members=persone, excluded_rejoins=0,
              as_of=giorno(as_of_giorno), params=MetricParams(),
              observability_anchor=inizio - timedelta(days=1),
              snapshot_windows=[(giorno(d - 7), giorno(d)) for d in range(7, as_of_giorno + 1, 7)])
    a = compute_cohort(layer_scope="any", reached_at={i: giorno(d) for i, d in any_.items()}, **kw)
    v = compute_cohort(layer_scope="voice", reached_at={i: giorno(d) for i, d in voice.items()}, **kw)
    return a, v


def test_la_barra_blu_puo_superare_l_arancio_e_le_domande_non_lo_negano():
    """L'ipotesi del mockup, verificata per esecuzione: e' falsa.

    Le due garanzie che il job da' davvero — partner vocali sottoinsieme dei
    partner di ogni tipo, raggiungimento vocale mai anteriore — non bastano a
    Kaplan-Meier. Primo caso: cinque persone, coorte matura e significativa,
    "circa meta'" in qualunque modo e "la maggior parte" in vocale. Secondo
    caso, nessuna uscita: ``any`` e' None e ``voice`` e' 1,0.

    Se un giorno il job lo garantisse, questo test fallirebbe e la frase si
    potrebbe rimettere — decidendolo, non per inerzia.
    """
    # (giorno di ingresso, giorno di uscita) dal lunedi' della coorte.
    a, v = _raggiunti_due_ambiti(
        [(4, 5), (6, None), (3, None), (0, 6), (3, 10)],
        any_={1: 7, 4: 7}, voice={1: 14, 4: 7}, as_of_giorno=21,
    )
    assert a.is_significant is True
    assert coorti.fascia(a.reached_by_14d).parola == "circa metà"
    assert coorti.fascia(v.reached_by_14d).parola == "la maggior parte"

    a, v = _raggiunti_due_ambiti(
        [(0, None)] + [(6, None)] * 5, any_={0: 7}, voice={0: 28}, as_of_giorno=28,
    )
    assert a.is_significant is True
    assert (a.reached_by_28d, v.reached_by_28d) == (None, 1.0)

    risposta = next(d for d in domande.costruisci(1, None, privacy_url="x") if d.codice == "d-vocale")
    testo = " ".join(risposta.paragrafi)
    assert "mai più lunga" not in testo and "non supera" not in testo


# --- l'avviso ------------------------------------------------------------------


def _avviso(html: str):
    trovati = list(_albero(_main(html)).radice.trova("p", classe="coorti-avviso"))
    return _testo(trovati[0]) if trovati else None


def test_zero_coorti_leggibili_dice_la_data_della_prima():
    html = _rendi(_pagina(_ultimo(GUILD_TODAY), GUILD_TODAY), GUILD_TODAY)
    # …001: la cadenza osservata e' 6 giorni e 19h45m (lo snapshot 11 ha as_of
    # alle 04:15), quindi i calcoli previsti cadono di domenica — come in Stato.
    assert _avviso(html) == "La prima coorte sarà leggibile dal calcolo di domenica 27 settembre."


def test_una_coorte_leggibile():
    vista = _pagina(_ultimo(GUILD_EDGE), GUILD_EDGE)
    assert vista.leggibili == 1
    assert _avviso(_rendi(vista, GUILD_EDGE)).startswith("Per ora una sola settimana è leggibile.")


def test_due_coorti_leggibili():
    senza_una = [g for g in _ultimo(GUILD_SCALE) if g.cohort_start != date(2026, 7, 27)]
    vista = _pagina(senza_una, GUILD_SCALE)
    assert vista.leggibili == 2
    assert _avviso(_rendi(vista)).startswith("Per ora due settimane sono leggibili.")


def test_tre_coorti_leggibili_nessun_avviso():
    vista = _pagina(_ultimo(GUILD_SCALE), GUILD_SCALE)
    assert vista.leggibili == 3
    assert vista.avviso is None
    assert _avviso(_rendi(vista)) is None


# --- dodici coorti ------------------------------------------------------------


def test_con_piu_di_dodici_coorti_posteriori_se_ne_vedono_dodici():
    """Sedici settimane dopo l'ancora, calcolate dal job con i membri del fixture."""
    as_of = datetime(2026, 9, 14, tzinfo=UTC)
    ancora = datetime(2026, 5, 1, tzinfo=UTC)
    snapshot = [as_of - timedelta(weeks=k) for k in range(30)]
    membri = []
    for settimane in range(1, 17):
        membri += _membri_generati((as_of - timedelta(weeks=settimane)).date(), 8,
                                   snapshot_as_of=snapshot, seme=settimane, ore_ultimo=20)
    gruppi = _coorti(700, as_of, membri, ancora=ancora, snapshot_as_of=snapshot)
    guild = SCENARIOS[GUILD_SCALE]["guild"].model_copy(update={"first_seen_at": ancora})
    runs = [RunRow(snapshot_id=700 - k, as_of=as_of - timedelta(weeks=k),
                   params=MetricParams().as_run_params()) for k in range(2)]

    vista = coorti.pagina(gruppi, guild, runs)
    assert len(gruppi) == 16
    assert len(vista.righe) == coorti.COORTI_VISIBILI == 12
    attese = sorted((g.cohort_start for g in gruppi), reverse=True)[:12]
    assert [r.cohort_start for r in vista.righe] == attese
    # Le leggibili si contano su tutte le posteriori, non sulle dodici in pagina.
    assert vista.leggibili == sum(1 for g in gruppi if g.onboarding[0].quality.significant is True)
    assert len(_righe(_rendi(vista))) == 12


# --- cosa la vista NON mostra -------------------------------------------------

PAROLE_VIETATE = (
    "p25", "p75", "mediana", "censurat", "esclus", "k=", "is_",
    "horizon_not_reached", "before_observability_anchor", "empty_cohort",
    "cohort_not_mature", "no_snapshot_coverage", "survivors_only", "below_threshold",
    "significativ", "sopravvissut", "copertura", "soppress",
)


def test_nella_vista_non_ricompaiono_i_numeri_del_modello(dashboard):
    """Il test che fallisce se la tabella da ventisei colonne torna, anche a pezzi.

    Sul ``<main>`` reso, attributi compresi: un ``data-campo="median_days_to_k"``
    sarebbe gia' il primo passo. Nessun numero decimale: le barre mostrano la
    fascia, e i numeri esatti stanno nei Dettagli tecnici.
    """
    for gid in GUILDS:
        main = _main(dashboard.get(f"/guilds/{gid}/coorti").text)
        minuscolo = unescape(main).lower()
        for parola in PAROLE_VIETATE:
            assert parola not in minuscolo, (gid, parola)
        assert not re.search(r"\d[.,]\d", unescape(re.sub(r"<[^>]+>", " ", main))), gid
        assert "data-campo" not in main and "cella--" not in main, gid


def test_niente_stile_ne_script_in_linea(dashboard):
    for gid in GUILDS:
        html = dashboard.get(f"/guilds/{gid}/coorti").text
        for forma in ("<style", 'style="', "<script"):
            assert forma not in html, (gid, forma)


# --- testi, legenda, rimandi --------------------------------------------------


def test_k_viene_dalla_riga_e_non_dal_job():
    gruppi = [g.model_copy(deep=True) for g in _ultimo(GUILD_SCALE)]
    for g in gruppi:
        for r in g.onboarding:
            r.k = 7
    main = _main(_rendi(_pagina(gruppi, GUILD_SCALE)))
    assert "almeno 7 persone diverse" in main
    assert "almeno 5" not in main


def test_i_testi_in_ordine_e_i_rimandi_alle_domande(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_SCALE}/coorti").text
    testo = " ".join(unescape(re.sub(r"<[^>]+>", " ", _main(html))).split())
    ordine = [
        "Coorti",
        "Chi entra nel server riesce a integrarsi, e rimane?",
        "Conta solo chi è entrato dopo l'arrivo del bot (16 luglio).",
        "Le ultime due settimane sono sempre in osservazione: una coorte si legge "
        "quando sono passati 14 giorni dall'ultimo ingresso.",
        "Come si leggono le barre",
        "Dati aggiornati a lunedì 14 settembre",
        "Le barre mostrano una proporzione approssimata; i numeri esatti sono nei dettagli tecnici.",
    ]
    posizioni = [testo.index(t) for t in ordine]
    assert posizioni == sorted(posizioni)

    rimandi = re.findall(rf'href="/guilds/{GUILD_SCALE}/domande#([\w-]+)"', html)
    assert rimandi == ["d-leggibile", "d-parole", "d-vocale"]
    # I Dettagli tecnici restano raggiungibili solo dalle Domande.
    assert "dettagli-tecnici" not in _main(html)


def test_la_legenda_ha_i_tre_colori_e_le_tre_lunghezze():
    main = _main(_rendi(_pagina(_ultimo(GUILD_SCALE), GUILD_SCALE)))
    legenda = next(_albero(main).radice.trova("aside", classe="coorti-legenda"))
    assert "Come si leggono le barre" in _testo(legenda)
    voci = [_testo(li) for li in legenda.trova("li")]
    assert voci == ["presenti", "si integrano, in qualunque modo", "si integrano, in vocale",
                    "nessuno", "circa metà", "tutti"]


# --- Domande e Dettagli tecnici -----------------------------------------------


def test_le_tre_domande_nuove_e_il_titolo_nuovo(dashboard):
    html = unescape(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)
    for codice in ("d-leggibile", "d-vocale", "d-parole"):
        assert f'<details id="{codice}" open>' in html
    assert domande.TITOLI["q-segnate"] == "Perché le coorti partono dall'arrivo del bot?"
    blocco = html[html.index('id="d-leggibile"'):html.index('id="d-vocale"')]
    assert "almeno 14 giorni dall'ingresso dell'ultimo arrivato" in blocco


def test_d_parole_non_promette_protezione_della_privacy_e_porta_ai_dettagli(dashboard):
    html = unescape(dashboard.get(f"/guilds/{GUILD_TODAY}/domande").text)
    blocco = html[html.index('id="d-parole"'):]
    blocco = blocco[:blocco.index("</details>")].lower()
    for parola in ("privacy", "proteg", "anonim", "riservat"):
        assert parola not in blocco, parola
    assert f'href="/guilds/{GUILD_TODAY}/dettagli-tecnici#coorti"' in blocco


def test_i_dettagli_tecnici_hanno_tutte_le_coorti_e_le_anteriori_a_parte(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_SCALE}/dettagli-tecnici").text
    albero = _albero(html).radice
    assert "Coorti — numeri esatti dell'ultimo calcolo" in unescape(html)
    tabelle = list(albero.trova("table", classe="tabella-coorti"))
    assert len(tabelle) == 2
    dopo, prima = ([tr.attrs["data-coorte"] for tr in t.trova("tr", classe="riga-coorte")] for t in tabelle)
    gruppi = _ultimo(GUILD_SCALE)
    ancora = SCENARIOS[GUILD_SCALE]["guild"].first_seen_at.date()
    assert set(dopo) == {str(g.cohort_start) for g in gruppi if g.cohort_start >= ancora}
    assert set(prima) == {str(g.cohort_start) for g in gruppi if g.cohort_start < ancora}
    assert len(dopo) + len(prima) == len(gruppi) == 25
    # Le anteriori stanno in una sezione marcata, con la loro spiegazione.
    sezione = next(albero.trova("section", classe="coorti-anteriori"))
    assert "Prima dell'arrivo del bot" in unescape(sezione.html())
    assert next(sezione.trova("table")) is not None


def test_la_rotta_chiede_guild_run_e_coorti():
    """L'ancora dalla guild, params e cadenza dalle run, le righe dalle coorti."""
    richieste: list[str] = []
    with TestClient(fixture_app) as api:
        def risponde(request: httpx.Request) -> httpx.Response:
            richieste.append(request.url.path)
            r = api.get(request.url.raw_path.decode())
            return httpx.Response(r.status_code, content=r.content,
                                  headers={"content-type": "application/json"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(risponde), base_url="http://api.test")
        with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
            assert client.get(f"/guilds/{GUILD_TODAY}/coorti").status_code == 200
    assert sorted(richieste) == sorted([
        f"/guilds/{GUILD_TODAY}", f"/guilds/{GUILD_TODAY}/runs", f"/guilds/{GUILD_TODAY}/cohorts"])
