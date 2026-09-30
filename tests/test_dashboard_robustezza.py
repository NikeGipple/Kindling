"""Robustezza: la vista per chi amministra, e la tabella dei Dettagli tecnici.

Quasi tutto qui si verifica sul MARKUP prodotto, non sul motore. Le regole di
questa pagina sono invarianti di template — la regola 6, la barra solo sopra la
soglia, nessun numero della simulazione sotto — e un test sul motore resterebbe
verde mentre la pagina le viola.

Dal 30/09/2026 le pagine sono due (dashboard.md 4, "La vista Robustezza, in
dettaglio"): la vista (``robustezza.pagina``) e la tabella che fino ad allora era
la vista, nei Dettagli tecnici (``robustezza.tecnica``). I test della seconda sono
quelli di prima, sul macro ``_robustezza_tecnica.html``; quelli del grafico SVG
sono usciti con il grafico.

Il fixture si monta in-process come per la vista Stato.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.models import RobustnessRow, RunRow
from dashboard import config, fasce, robustezza
from dashboard.main import STATIC_DIR, cornice, crea_app, crea_templates
from dashboard.qualifica import NON_VALUTATO, cella
from job.config import ALL_LAYERS, MetricParams
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_LEFT,
    GUILD_MATURE,
    GUILD_SCALE,
    GUILD_TODAY,
    SCENARIOS,
    _robustness_layer,
    graph_params,
)
from tools.fixture_api import app as fixture_app

PARAMS = MetricParams().as_run_params()


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


def _righe(api, guild_id: int, limit: int = 12) -> list[RobustnessRow]:
    risposta = api.get(f"/guilds/{guild_id}/robustness", params={"limit": limit})
    return TypeAdapter(list[RobustnessRow]).validate_json(risposta.content)


def _runs(righe: list[RobustnessRow], params: Optional[dict] = None) -> list[RunRow]:
    """Una run per snapshot, con i params dati (di default quelli del job)."""
    visti = {r.snapshot_id: r.as_of for r in righe}
    return [
        RunRow(snapshot_id=sid, as_of=as_of, params=PARAMS if params is None else params,
               graph_params=graph_params(), stats={}, code_version=None, created_at=as_of)
        for sid, as_of in visti.items()
    ]


def _rendi(righe: list[RobustnessRow], runs: Optional[list[RunRow]] = None, guild_id: int = 1) -> str:
    """La vista, resa dal template vero."""
    return crea_templates().get_template("robustezza.html").render(
        **cornice(
            guild_id=guild_id,
            vista=robustezza.pagina(righe, _runs(righe) if runs is None else runs),
            vista_corrente="robustezza",
        )
    )


def _rendi_tecnica(righe: list[RobustnessRow]) -> str:
    """La tabella dei Dettagli tecnici, dal macro vero."""
    modello = crea_templates().from_string(
        '{% from "_robustezza_tecnica.html" import robustezza_tecnica %}'
        "{{ robustezza_tecnica(vista) }}"
    )
    return modello.render(vista=robustezza.tecnica(righe))


def _lunedi(i: int) -> datetime:
    return datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(weeks=i)


def _layer(sid: int, as_of: datetime, layer: str, n: Optional[int], **kw) -> list[RobustnessRow]:
    """Le righe di un layer, derivate dal fixture come le deriva il job."""
    return _robustness_layer(sid, as_of, layer, n=n,
                             excess=kw.pop("excess", {0.05: 0.1, 0.10: 0.2, 0.20: 0.3}), **kw)


# --- un albero minimo del markup ------------------------------------------
# Lo usano anche i test di Community e Coorti.


class Nodo:
    def __init__(self, tag: str, attrs: dict[str, Optional[str]], padre: Optional["Nodo"]):
        self.tag, self.attrs, self.padre = tag, attrs, padre
        self.figli: list[Nodo | str] = []

    @property
    def classi(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def testo(self) -> str:
        return " ".join(f if isinstance(f, str) else f.testo() for f in self.figli)

    def discendenti(self):
        for f in self.figli:
            if isinstance(f, Nodo):
                yield f
                yield from f.discendenti()

    def trova(self, tag: Optional[str] = None, classe: Optional[str] = None, **attrs):
        for d in self.discendenti():
            if tag and d.tag != tag:
                continue
            if classe and classe not in d.classi:
                continue
            if any(d.attrs.get(k.replace("_", "-")) != v for k, v in attrs.items()):
                continue
            yield d

    def conta_html(self, frammento: str) -> int:
        return self.html().count(frammento)

    def html(self) -> str:
        attrs = "".join(f' {k}="{v}"' for k, v in self.attrs.items())
        dentro = "".join(f if isinstance(f, str) else f.html() for f in self.figli)
        return f"<{self.tag}{attrs}>{dentro}</{self.tag}>"


_VUOTI = {"meta", "br", "hr", "img", "input", "link", "line", "circle"}


class _Costruttore(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.radice = Nodo("#", {}, None)
        self.corrente = self.radice
        self.testi: list[tuple[str, Nodo]] = []  # (testo, nodo che lo contiene)

    def handle_starttag(self, tag, attrs):
        nodo = Nodo(tag, dict(attrs), self.corrente)
        self.corrente.figli.append(nodo)
        if tag not in _VUOTI or tag in ("circle",):
            self.corrente = nodo
        if tag in _VUOTI and tag != "circle":
            self.corrente = nodo.padre  # type: ignore[assignment]

    def handle_startendtag(self, tag, attrs):
        self.corrente.figli.append(Nodo(tag, dict(attrs), self.corrente))

    def handle_endtag(self, tag):
        nodo = self.corrente
        while nodo is not self.radice and nodo.tag != tag:
            nodo = nodo.padre  # type: ignore[assignment]
        if nodo is not self.radice:
            self.corrente = nodo.padre  # type: ignore[assignment]

    def handle_data(self, data):
        if data.strip():
            self.corrente.figli.append(data)
            self.testi.append((data, self.corrente))


def _albero(html: str) -> _Costruttore:
    c = _Costruttore()
    c.feed(html)
    return c


def _antenati(nodo: Nodo):
    while nodo is not None:
        yield nodo
        nodo = nodo.padre  # type: ignore[assignment]


def _riga(html: str, layer: str) -> Nodo:
    """La riga di un tipo di interazione nella tabella della settimana chiusa."""
    tabella = next(_albero(html).radice.trova("table", classe="robustezza-lettura"))
    return next(tabella.trova("tr", data_layer=layer))


_PERCENTUALE = re.compile(r"\b\d+(?:[.,]\d+)?\s?%")
_SU = re.compile(r"\b1 su \d+\b")


# =============================================================================
# La vista (dashboard.md 4, "Riscritta il 30/09/2026")
# =============================================================================


# --- stati di riga ------------------------------------------------------------


def test_una_riga_per_tipo_nell_ordine_del_job_con_i_nomi_per_chi_amministra(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    tabella = next(_albero(html).radice.trova("table", classe="robustezza-lettura"))
    righe = list(tabella.trova("tr", classe="robustezza-riga"))
    assert [r.attrs["data-layer"] for r in righe] == list(ALL_LAYERS)
    assert [next(r.trova("th")).testo().strip() for r in righe] == [
        "In vocale", "Risposte", "Menzioni", "Reazioni"
    ]


@pytest.mark.parametrize("guild_id, attesi", [
    # ...001, la produzione del 14/09 con la soglia a 21: voice 15 nodi, gli
    # altri tre 23-29.
    (GUILD_TODAY, {"voice": "in_osservazione", "reply": "leggibile",
                   "mention": "leggibile", "reaction": "leggibile"}),
    # ...002: voice assente nell'ultimo snapshot, gli altri grandi.
    (GUILD_MATURE, {"voice": "nessuna", "reply": "leggibile",
                    "mention": "leggibile", "reaction": "leggibile"}),
    # ...003: reply soppresso (3 nodi), mention a 10, voice e reaction grandi.
    (GUILD_EDGE, {"voice": "leggibile", "reply": "sotto_soglia",
                  "mention": "in_osservazione", "reaction": "leggibile"}),
])
def test_gli_stati_di_riga_sul_fixture(dashboard, guild_id, attesi):
    html = dashboard.get(f"/guilds/{guild_id}/robustezza").text
    for layer, stato in attesi.items():
        assert _riga(html, layer).attrs["data-stato"] == stato, layer


def test_sotto_la_soglia_nessun_risultato_della_simulazione(dashboard):
    """Decisione 1: la riga dice le persone e "in osservazione", e basta.

    Nessuna barra, nessuna tacca, nessun "senza N persone", nessun numero con la
    virgola. La soglia e' quella dei params, citata nella frase.
    """
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    voice = _riga(html, "voice")
    assert "barra" not in voice.html() and "quanti" not in voice.html()
    testo = " ".join(voice.testo().split())
    assert testo == "In vocale 15 sett. prima 9 in osservazione · si legge da 21 persone attive in una settimana"


def test_nessun_numero_con_la_virgola_nella_vista(dashboard):
    """Le barre mostrano la fascia, mai il valore: un 0,833 nella vista e' un
    numero dei Dettagli tecnici finito nel posto sbagliato."""
    for gid in SCENARIOS:
        html = dashboard.get(f"/guilds/{gid}/robustezza").text
        corpo = html[html.index("<h1>Robustezza"):html.index("</main>")]
        assert not re.search(r"\d,\d", corpo), gid


def test_la_soglia_viene_dai_params_della_run_dello_snapshot(api):
    righe = _righe(api, GUILD_TODAY)
    # Con 30, come prima del 30/09: nessuna riga di ...001 si legge (massimo 29).
    a_30 = _rendi(righe, _runs(righe, {**PARAMS, "min_nodes_structural": 30}))
    assert {_riga(a_30, l).attrs["data-stato"] for l in ALL_LAYERS} == {"in_osservazione"}
    assert "si legge da 30 persone attive" in a_30

    # Solo i params della run che ha scritto QUELLO snapshot: una run di un altro
    # snapshot con una soglia diversa non conta.
    runs = _runs(righe)
    altra = RunRow(snapshot_id=999, as_of=_lunedi(10), params={**PARAMS, "min_nodes_structural": 99},
                   graph_params=graph_params(), stats={}, code_version=None, created_at=_lunedi(10))
    assert _riga(_rendi(righe, [*runs, altra]), "reply").attrs["data-stato"] == "leggibile"


def test_senza_la_soglia_in_params_niente_barre_e_nessun_numero_inventato(api):
    """Ipotesi 1 del 30/09: la chiave c'e' in ogni run, ma se mancasse la riga
    resta in osservazione, e ne' la frase ne' l'avviso nominano un numero."""
    righe = _righe(api, GUILD_TODAY)
    senza = {k: v for k, v in PARAMS.items() if k != "min_nodes_structural"}
    html = _rendi(righe, _runs(righe, senza))
    for layer in ALL_LAYERS:
        riga = _riga(html, layer)
        assert riga.attrs["data-stato"] == "in_osservazione"
        assert next(riga.trova("td", classe="robustezza-frase")).testo().strip() == "in osservazione"
    avviso = next(_albero(html).radice.trova("p", classe="avviso")).testo()
    assert "Nell'ultima settimana nessun tipo di interazione è leggibile." in avviso
    assert not re.search(r"\d", avviso)


def test_senza_run_la_vista_non_inventa_ne_soglia_ne_pubblicazione(api):
    html = _rendi(_righe(api, GUILD_EDGE), [])
    assert _riga(html, "reply").testo().count("troppo poche persone attive: non mostrata") == 1
    assert _riga(html, "voice").attrs["data-stato"] == "in_osservazione"


def test_soppresso_nomina_la_soglia_di_pubblicazione_dei_params(api):
    righe = _righe(api, GUILD_EDGE)
    html = _rendi(righe, _runs(righe))
    reply = _riga(html, "reply")
    assert "meno di 5 persone attive: non mostrata" in reply.testo()
    assert next(reply.trova("td", classe="persone")).testo().strip() == "—"
    # ...003 ha params ridotti, senza min_nodes_publish: niente numero.
    html = _rendi(righe, _runs(righe, SCENARIOS[GUILD_EDGE]["runs"][0].params))
    assert "troppo poche persone attive: non mostrata" in _riga(html, "reply").testo()


def test_layer_assente_ha_la_sua_frase(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    voice = _riga(html, "voice")
    assert voice.attrs["data-stato"] == "nessuna"
    assert "nessuna interazione di questo tipo in questa settimana" in voice.testo()
    assert "soppress" not in voice.testo() and "⊘" not in voice.testo()


def test_righe_discordi_non_scelgono_un_numero():
    """Ramo difensivo: n_effective diverso fra le frazioni non esce dal job, ma il
    contratto non lo vieta. La riga lo dice, senza barre."""
    righe = _layer(1, _lunedi(0), "reply", 40)
    righe[1] = righe[1].model_copy(update={"quality": righe[1].quality.model_copy(update={"n_effective": 41})})
    html = _rendi(righe)
    reply = _riga(html, "reply")
    assert reply.attrs["data-stato"] == "discorde"
    assert "barra" not in reply.html()


# --- le celle leggibili ------------------------------------------------------


def test_barra_tacca_e_collegati_sono_le_fasce_dei_valori(api):
    """Decisione 3: barra = giant_after_targeted, tacca = giant_after_random_mean,
    "Collegati fra loro" = giant_before, tutte sulle fasce di fasce.py."""
    righe = _righe(api, GUILD_TODAY)
    html = _rendi(righe)
    ultimo = max(r.snapshot_id for r in righe)
    celle_viste = 0
    for layer in ("reply", "mention", "reaction"):
        tr = _riga(html, layer)
        dati = sorted((r for r in righe if r.snapshot_id == ultimo and r.layer == layer),
                      key=lambda r: r.removal_fraction)
        collegati = next(next(tr.trova("td", classe="collegati")).trova("span", classe="barra"))
        assert f"fascia-{fasce.fascia(dati[0].values.giant_before).indice}" in collegati.classi
        for td, r in zip(tr.trova("td", classe="frazione"), dati):
            barra = next(td.trova("span", classe="barra"))
            assert f"fascia-{fasce.fascia(r.values.giant_after_targeted).indice}" in barra.classi
            assert f"tacca-{fasce.fascia(r.values.giant_after_random_mean).indice}" in barra.classi
            assert next(td.trova("span", classe="quanti")).testo() == f"senza {r.values.nodes_removed} persone"
            etichetta = _html.unescape(barra.attrs["aria-label"])
            assert etichetta == (
                f"senza le {r.values.nodes_removed} più centrali: "
                f"{fasce.fascia(r.values.giant_after_targeted).parola}; "
                f"senza {r.values.nodes_removed} persone qualunque: "
                f"{fasce.fascia(r.values.giant_after_random_mean).parola}"
            )
            celle_viste += 1
    assert celle_viste == 9


def test_regola_6_nessuna_frazione_senza_le_persone_tolte(dashboard):
    """"1 su 20" (o una percentuale) compare solo in intestazione, e ogni cella di
    frazione di una riga leggibile porta "senza N persone" nella stessa cella."""
    viste = 0
    for gid in SCENARIOS:
        html = dashboard.get(f"/guilds/{gid}/robustezza").text
        albero = _albero(html)
        for testo, contenitore in albero.testi:
            if not (_SU.search(testo) or _PERCENTUALE.search(testo)):
                continue
            assert any(n.tag == "thead" for n in _antenati(contenitore)), (gid, testo)
        for td in albero.radice.trova("td", classe="frazione"):
            viste += 1
            if next(td.trova("span", classe="barra"), None) is not None:
                assert re.fullmatch(r"senza \d+ person[ae]",
                                    next(td.trova("span", classe="quanti")).testo())
    assert viste > 50


@pytest.mark.parametrize("frazione, attesa", [
    (0.05, "1 su 20"), (0.10, "1 su 10"), (0.20, "1 su 5"),
    (0.1 + 0.1 + 0.05 - 0.05, "1 su 5"),  # l'errore di rappresentazione non conta
    (0.15, "15%"), (0.3, "30%"),
])
def test_intestazione_uno_su_n_solo_se_intero(frazione, attesa):
    """Ipotesi 5: "1 su N" solo se 1/frazione e' intero; altrimenti la
    percentuale, mai un "1 su 7" arrotondato in silenzio."""
    assert robustezza.intestazione_frazione(frazione) == attesa


def test_le_intestazioni_vengono_dalle_frazioni_delle_righe(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    testa = next(_albero(html).radice.trova("thead"))
    assert re.findall(r"1 su \d+", testa.testo()) == ["1 su 20", "1 su 10", "1 su 5"]
    assert [robustezza.intestazione_frazione(f) for f in MetricParams().removal_fractions] == [
        "1 su 20", "1 su 10", "1 su 5"
    ]


def test_baseline_degenere_sopra_soglia_si_mostra_come_le_altre():
    """Decisione del 30/09 (ipotesi 2): sopra 21 persone l'unico motivo di non
    significativita' e' degenerate_baseline, e la cella ha barra e tacca normali."""
    righe = _layer(1, _lunedi(0), "reply", 30, degenerate=frozenset({0.05, 0.10, 0.20}))
    assert all(r.quality.significant is False for r in righe)
    reply = _riga(_rendi(righe), "reply")
    assert reply.attrs["data-stato"] == "leggibile"
    assert len(list(reply.trova("span", classe="barra__tacca"))) == 3


def test_la_barra_puo_superare_la_tacca_e_non_ha_trattamenti_speciali():
    """Ipotesi 4: eccesso negativo, la barra oltre la tacca. Nessuna classe o
    frase in piu' nella cella: lo spiegano legenda e q-robustezza-barra."""
    # Nel fixture la tacca sta a 0,88 e la barra a 0,88 - eccesso: con -0,12 la
    # barra arriva a 1 ("tutti") e la tacca resta a "quasi tutti".
    righe = _layer(1, _lunedi(0), "reply", 40, excess={0.05: -0.12, 0.10: 0.0, 0.20: 0.3})
    td = next(_riga(_rendi(righe), "reply").trova("td", classe="frazione"))
    barra = next(td.trova("span", classe="barra"))
    fascia_barra = int(next(c for c in barra.classi if c.startswith("fascia-"))[7:])
    fascia_tacca = int(next(c for c in barra.classi if c.startswith("tacca-"))[6:])
    assert fascia_barra > fascia_tacca
    assert [s.attrs["class"] for s in td.trova("span")] == [
        "barra barra--con-tacca " + " ".join(sorted(barra.classi - {"barra", "barra--con-tacca"},
                                                   key=lambda c: not c.startswith("fascia"))),
        "barra__pieno", "barra__tacca", "quanti",
    ]


# --- "sett. prima" ------------------------------------------------------------


def test_settimana_prima_solo_quando_c_e_un_numero():
    """Decisione del 30/09: niente al primo snapshot, niente se il precedente e'
    assente o soppresso, niente se non cade sette giorni prima."""
    righe = (
        _layer(1, _lunedi(0), "reply", 30) + _layer(1, _lunedi(0), "mention", 3)
        + _layer(2, _lunedi(1), "reply", 33) + _layer(2, _lunedi(1), "mention", 25)
        + _layer(2, _lunedi(1), "reaction", 22)
    )
    html = _rendi(righe)
    prima = lambda l: [s.testo() for s in _riga(html, l).trova("small")]  # noqa: E731
    assert prima("reply") == ["sett. prima 30"]
    assert prima("mention") == []    # soppresso nel precedente
    assert prima("reaction") == []   # assente nel precedente

    # Un solo snapshot: nessun "sett. prima".
    assert "sett. prima" not in _rendi(_layer(1, _lunedi(0), "reply", 30))
    # Un buco di due settimane: "settimana prima" sarebbe falso.
    buco = _layer(1, _lunedi(0), "reply", 30) + _layer(2, _lunedi(2), "reply", 33)
    assert "sett. prima" not in _rendi(buco)


def test_settimana_prima_attraversa_la_run_anteriore_all_ancoraggio(dashboard):
    """...001: lo snapshot 11 ha as_of alle 04:15 del 7/09, il 12 la mezzanotte
    del 14. Sono settimane consecutive, e "sett. prima" c'e'."""
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    assert [s.testo() for s in _riga(html, "reply").trova("small")] == ["sett. prima 24"]


# --- avviso -------------------------------------------------------------------


def test_avviso_solo_con_zero_tipi_leggibili(dashboard):
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_LEFT):
        html = dashboard.get(f"/guilds/{gid}/robustezza").text
        assert 'class="avviso' not in html[html.index("<h1>Robustezza"):], gid


def test_avviso_dell_ultima_settimana_e_della_storia():
    """Decisione 11: in produzione, dopo il ricalcolo del 30/09, le settimane
    precedenti si leggono e l'ultima no. L'avviso non dice "per ora": dice
    l'ultima settimana, e che le precedenti ne hanno."""
    righe = (
        _layer(11, _lunedi(0), "reply", 24) + _layer(12, _lunedi(1), "reply", 14)
        + _layer(12, _lunedi(1), "voice", 11)
    )
    html = _rendi(righe)
    avviso = _html.unescape(next(_albero(html).radice.trova("p", classe="avviso")).testo())
    assert "Nell'ultima settimana nessun tipo di interazione è leggibile." in avviso
    assert "Serve che almeno 21 persone" in avviso
    assert "Le settimane precedenti ne hanno" in avviso
    assert "per ora" not in avviso.lower()
    link = next(next(_albero(html).radice.trova("p", classe="avviso")).trova("a"))
    assert link.attrs["href"].endswith("#q-robustezza-leggibile") and link.testo() == "Perché 21?"

    # Senza settimane leggibili prima, la seconda frase non c'e'.
    solo_piccole = _layer(11, _lunedi(0), "reply", 14) + _layer(12, _lunedi(1), "reply", 14)
    assert "ne hanno" not in _rendi(solo_piccole)


# --- le settimane precedenti ---------------------------------------------------


def test_le_settimane_precedenti_una_tabella_per_tipo_dalla_piu_recente(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    storia = next(_albero(html).radice.trova("details", classe="robustezza-storia"))
    assert "open" not in storia.attrs
    tabelle = list(storia.trova("table", classe="robustezza-lettura--storia"))
    assert len(tabelle) == len(ALL_LAYERS)
    snapshot = sorted({(r.as_of, r.snapshot_id) for r in SCENARIOS[GUILD_MATURE]["robustness"]},
                      reverse=True)
    for tabella, layer in zip(tabelle, ALL_LAYERS):
        righe = list(tabella.trova("tr", classe="robustezza-riga"))
        assert {r.attrs["data-layer"] for r in righe} == {layer}
        assert len(righe) == len(snapshot)
    # voice: assente, soppresso, in osservazione e leggibile, nella stessa colonna.
    stati_voice = [r.attrs["data-stato"] for r in tabelle[0].trova("tr", classe="robustezza-riga")]
    assert {"nessuna", "sotto_soglia", "in_osservazione", "leggibile"} <= set(stati_voice)


def test_con_uno_snapshot_nessuna_storia(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_EDGE}/robustezza").text
    assert "robustezza-storia" not in html


# --- testi, legenda, perimetro -----------------------------------------------


def test_la_legenda_ha_i_testi_del_mockup(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    legenda = next(_albero(html).radice.trova("aside", classe="robustezza-legenda"))
    testo = " ".join(_html.unescape(legenda.testo()).split())
    assert testo == (
        "Persone collegate: senza i più centrali escludendo a caso "
        "Barre più corte, meno collegati. Esempi di distribuzione dei contatti "
        "ben distribuiti concentrati su pochi Come si leggono?"
    )
    link = next(legenda.trova("a"))
    assert link.attrs["href"].endswith("/domande#q-robustezza-barra")


def test_nessuna_parola_di_giudizio_nella_vista(dashboard):
    for gid in SCENARIOS:
        html = dashboard.get(f"/guilds/{gid}/robustezza").text.lower()
        corpo = html[html.index("<h1>robustezza"):html.index("</main>")]
        for parola in ("fragil", "solid", "debole", "a rischio"):
            assert parola not in corpo, (gid, parola)


def test_la_domanda_della_vista_e_quella_di_stato(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    domanda = ("Se le poche persone che tengono insieme il server smettessero di esserci, "
               "gli altri resterebbero in contatto fra loro?")
    assert domanda in " ".join(_html.unescape(html).split())


def test_ogni_classe_di_fascia_e_di_tacca_esiste_nel_foglio(dashboard):
    """Larghezze e posizioni sono classi (CSP): una classe usata e non definita
    sarebbe una barra piena o una tacca a sinistra, senza nessun errore."""
    css = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
    for i in range(len(fasce.FASCE)):
        assert f".fascia-{i} .barra__pieno" in css
        assert f".tacca-{i} .barra__tacca" in css
    usate = set()
    for gid in SCENARIOS:
        usate |= set(re.findall(r"\b(?:fascia|tacca)-\d\b", dashboard.get(f"/guilds/{gid}/robustezza").text))
    assert usate and all(re.search(rf"\.{c} \.barra__", css) for c in usate)


def test_la_vista_non_porta_la_legenda_dei_simboli_e_i_dettagli_si(dashboard):
    """Robustezza non passa piu' da cella(): esce da _VISTE_CON_SIMBOLI, come Coorti
    il 26/09. I Dettagli tecnici, che portano la sua vecchia tabella, restano."""
    assert "legenda-simboli" not in dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    assert "legenda-simboli" in dashboard.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici").text
    assert 'data-esito="' not in dashboard.get(f"/guilds/{GUILD_EDGE}/robustezza").text


def test_guild_osservata_senza_snapshot_il_calcolo_non_e_ancora_girato():
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(api)) as client:
        r = client.get("/guilds/5/robustezza")
    assert r.status_code == 200
    assert "Il calcolo non è ancora girato" in r.text
    assert 'class="errore"' not in r.text
    assert "robustezza-lettura" not in r.text


def test_guild_non_osservata_e_api_giu(dashboard):
    assert dashboard.get("/guilds/123/robustezza").status_code == 404

    def giu(request):
        raise httpx.ConnectError("rifiutata", request=request)

    with _client(httpx.MockTransport(giu)) as client:
        r = client.get(f"/guilds/{GUILD_EDGE}/robustezza")
    assert r.status_code == 503 and 'class="errore"' in r.text


def test_navigazione_solo_con_le_viste_che_esistono(dashboard):
    for percorso in (f"/guilds/{GUILD_EDGE}", f"/guilds/{GUILD_EDGE}/robustezza"):
        html = dashboard.get(percorso).text
        nav = next(_albero(html).radice.trova("nav", classe="viste"))
        voci = [a.testo().strip() for a in nav.trova("a")]
        assert voci == ["Stato", "Robustezza", "Community", "Coorti", "Domande"]
        (domande,) = [a for a in nav.trova("a") if a.testo().strip() == "Domande"]
        assert domande.attrs.get("class") == "voce-domande"
        assert "dettagli-tecnici" not in html[html.index('<nav class="viste"'):]


def test_la_vista_non_legge_details():
    import inspect

    sorgente = inspect.getsource(robustezza)
    codice = "\n".join(l for l in sorgente.splitlines() if not l.strip().startswith(("#", '"', "``")))
    assert ".details" not in codice
    cartella = robustezza.__file__.rsplit("robustezza.py", 1)[0] + "templates/"
    for nome in ("robustezza.html", "_robustezza_tecnica.html"):
        assert ".details" not in open(cartella + nome, encoding="utf-8").read().split("-#}", 1)[1]


# =============================================================================
# La tabella dei Dettagli tecnici (fino al 30/09/2026 la vista)
# =============================================================================


def _pagine(api) -> list[tuple[str, str]]:
    pagine = []
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE):
        for limit in (1, 2, 3, 4, 12):
            pagine.append((f"{gid}?limit={limit}", _rendi_tecnica(_righe(api, gid, limit))))
    return pagine


def test_i_dettagli_tecnici_portano_la_tabella(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/dettagli-tecnici").text
    albero = _albero(html).radice
    assert [s.attrs["data-layer"] for s in albero.trova("section", classe="blocco")] == list(ALL_LAYERS)
    # Nessun grafico: la serie e' una tabella, una per layer presente.
    assert '<figure class="grafico"' not in html
    assert html.count('class="tabella-serie"') == 4


def test_regola_6_nessuna_percentuale_senza_i_nodi_rimossi_della_stessa_riga(api):
    viste = 0
    for nome, html in _pagine(api):
        albero = _albero(html)
        for testo, contenitore in albero.testi:
            if not _PERCENTUALE.search(testo):
                continue
            viste += 1
            riga = next((n for n in _antenati(contenitore) if "riga-rimozione" in n.classi), None)
            assert riga is not None, (nome, testo)
            # Colonne ADIACENTI: la cella dopo la percentuale porta i nodi
            # rimossi di QUESTA riga (o la sua soppressione).
            celle = [f for f in riga.figli if isinstance(f, Nodo) and f.tag == "td"]
            i = next(i for i, td in enumerate(celle) if _PERCENTUALE.search(td.testo()))
            accanto = celle[i + 1].testo()
            atteso = riga.attrs["data-nodes-removed"]
            if atteso == "soppresso":
                assert "soppresso" in accanto, (nome, accanto)
            else:
                assert accanto.split() == [atteso], (nome, atteso, accanto)
    assert viste > 100


def test_la_serie_e_una_tabella_da_due_snapshot_in_su(api):
    assert "tabella-serie" not in _rendi_tecnica(_righe(api, GUILD_MATURE, 1))
    assert _rendi_tecnica(_righe(api, GUILD_MATURE, 2)).count('class="tabella-serie"') == 4


def test_punto_soppresso_o_layer_assente_nella_serie_restano_in_tabella(api):
    html = _rendi_tecnica(_righe(api, GUILD_MATURE))
    voice = next(_albero(html).radice.trova("section", data_layer="voice"))
    serie = next(voice.trova("table", classe="tabella-serie"))
    snapshot = {tr.attrs.get("data-snapshot") for tr in serie.trova("tr", classe="riga-rimozione")}
    assert "33" in snapshot  # soppresso: resta, con la soppressione
    assert serie.testo().count("Nessuna interazione di questo tipo in quella settimana") == 2


def test_le_tre_righe_di_un_snapshot_layer_condividono_n_effective():
    # Vero per costruzione nel job, ma un'assunzione della pagina: si verifica.
    for scenario in SCENARIOS.values():
        parti: dict[tuple[int, str], set] = {}
        for r in scenario["robustness"]:
            if not r.quality.suppressed:
                parti.setdefault((r.snapshot_id, r.layer), set()).add(r.quality.n_effective)
        assert all(len(v) == 1 for v in parti.values())


def test_n_effective_compare_una_volta_per_blocco(api):
    html = _rendi_tecnica(_righe(api, GUILD_EDGE))
    albero = _albero(html).radice
    atteso = {"voice": "61", "mention": "10", "reaction": "44"}
    for layer, n in atteso.items():
        sezione = next(albero.trova("section", data_layer=layer))
        assert [s.attrs["data-n-effective"] for s in sezione.trova("strong") if "data-n-effective" in s.attrs] == [n]
    reply = next(albero.trova("section", data_layer="reply"))
    assert "data-n-effective" not in reply.html()
    assert next(reply.trova("p", classe="dimensione")).testo().count("soppresso") == 1


def test_layer_assente_nella_tabella_tecnica_ha_la_sua_frase(api):
    html = _rendi_tecnica(_righe(api, GUILD_MATURE))
    voice = next(_albero(html).radice.trova("section", data_layer="voice"))
    frase = next(voice.trova("p", classe="frase-assente"))
    assert "Nessuna interazione di questo tipo in questa settimana" in frase.testo()
    assert "tabella-blocco" not in voice.html()


def test_ogni_etichetta_di_riga_compare_esattamente_una_volta_nella_riga(api):
    per_chiave = {
        (str(r.snapshot_id), r.layer, str(r.removal_fraction)): r
        for s in SCENARIOS.values() for r in s["robustness"]
    }
    righe_viste = 0
    for nome, html in _pagine(api):
        for tr in _albero(html).radice.trova("tr", classe="riga-rimozione"):
            riga = per_chiave[(tr.attrs["data-snapshot"], tr.attrs["data-layer"], tr.attrs["data-frazione"])]
            attese = {e.tipo for e in cella(riga, "targeted_excess").etichette}
            markup = tr.html()
            for tipo in ("non_significativo", "solo_sopravvissuti", NON_VALUTATO):
                atteso = 1 if tipo in attese else 0
                assert markup.count(f"etichetta--{tipo}") == atteso, (nome, tipo, markup)
            righe_viste += 1
    assert righe_viste > 100


def test_non_significativo_dequalifica_ogni_cella_della_riga(api):
    html = _rendi_tecnica(_righe(api, GUILD_TODAY))
    voice = next(_albero(html).radice.trova("section", data_layer="voice"))
    tr = next(voice.trova("tr", classe="riga-rimozione"))
    valori = [s for s in tr.trova("span", classe="cella")]
    assert valori and all("cella--dequalificata" in s.classi for s in valori)


# --- precisione di colonna (dashboard.md 5) ---------------------------------


_NUMERO = re.compile(r"^-?\d+,(\d+)$")
_ZERO_CON_SEGNO = re.compile(r"(?<![\d,])-0,0+(?![\d])")


def _decimali(testo: str) -> Optional[int]:
    m = _NUMERO.match(testo.strip())
    return len(m.group(1)) if m else None


def test_nessuno_zero_con_segno_in_nessuna_pagina(api):
    for nome, html in _pagine(api):
        assert not _ZERO_CON_SEGNO.search(html), (nome, _ZERO_CON_SEGNO.search(html).group(0))


def test_un_valore_sotto_il_millesimo_si_vede(api):
    # ...003, reaction: -0,0004 al 5%, accanto a 0,33 e 0,44. E le altre due
    # righe salgono a 4 decimali: la colonna decide, non il valore.
    html = _rendi_tecnica(_righe(api, GUILD_EDGE))
    reaction = next(_albero(html).radice.trova("section", data_layer="reaction"))
    blocco = next(reaction.trova("table", classe="tabella-blocco"))
    eccessi = [td.testo().strip() for td in blocco.trova("td", classe="colonna-risposta")]
    assert eccessi == ["-0,0004", "0,3300", "0,4400"]


def test_ogni_colonna_ha_lo_stesso_numero_di_decimali(api):
    tabelle_viste = 0
    for nome, html in _pagine(api):
        albero = _albero(html).radice
        for classe in ("tabella-blocco", "tabella-serie"):
            for tabella in albero.trova("table", classe=classe):
                per_colonna: dict[int, set[int]] = {}
                for tr in tabella.trova("tr", classe="riga-rimozione"):
                    celle = [f for f in tr.figli if isinstance(f, Nodo) and f.tag == "td"]
                    if any("colspan" in td.attrs for td in celle):
                        continue  # riga soppressa: nessun valore
                    for i, td in enumerate(celle):
                        testo = next((s.testo() for s in td.trova("span", classe="cella__testo")), "")
                        d = _decimali(testo)
                        if d is not None:
                            per_colonna.setdefault(i, set()).add(d)
                disallineate = {i: ds for i, ds in per_colonna.items() if len(ds) > 1}
                assert not disallineate, (nome, classe, disallineate)
                tabelle_viste += 1
    assert tabelle_viste > 50


def _numero(testo: str) -> float:
    return float(testo.strip().replace(",", "."))


def test_la_riga_non_si_contraddice_eccesso_ricavabile_dai_giganti_resi(api):
    # eccesso mirato = (gigante a caso - gigante mirata) / gigante prima, sulle
    # STRINGHE rese. Il difetto del 14/09: 0,880 - 0,880 accanto a -0,0004.
    # Tolleranza di un'unita' sull'ultima cifra: tre valori arrotondati ciascuno
    # per conto proprio possono spostarla di uno.
    righe_viste = 0
    for nome, html in _pagine(api):
        for tabella in _albero(html).radice.trova("table", classe="tabella-blocco"):
            for tr in tabella.trova("tr", classe="riga-rimozione"):
                celle = [f for f in tr.figli if isinstance(f, Nodo) and f.tag == "td"]
                if any("colspan" in td.attrs for td in celle):
                    continue
                testi = [next((s.testo() for s in td.trova("span", classe="cella__testo")), "")
                         for td in celle]
                prima, mirata, a_caso, eccesso = testi[2], testi[3], testi[4], testi[6]
                decimali = {_decimali(t) for t in (prima, mirata, a_caso, eccesso)}
                assert len(decimali) == 1, (nome, prima, mirata, a_caso, eccesso)
                unita = 10 ** -decimali.pop()
                ricavato = (_numero(a_caso) - _numero(mirata)) / _numero(prima)
                assert abs(ricavato - _numero(eccesso)) <= unita * 1.0000001, (
                    nome, prima, mirata, a_caso, eccesso, ricavato)
                righe_viste += 1
    assert righe_viste > 100


def test_il_gruppo_dei_giganti_non_include_z_ne_componenti():
    vista = robustezza.tecnica(SCENARIOS[GUILD_EDGE]["robustness"])
    reaction = next(b for b in vista.blocchi if b.layer == "reaction")
    gruppo = {c: reaction.decimali[c] for c in
              ("giant_before", "giant_after_targeted", "giant_after_random_mean", "targeted_excess")}
    assert set(gruppo.values()) == {4}
    # z ha la propria precisione: il suo denominatore (giant_after_random_sd) non
    # si mostra, quindi non e' ricavabile dalle colonne visibili.
    assert reaction.decimali["targeted_z"] == 3
    assert set(reaction.decimali_serie) == {"nodes_removed", "targeted_excess"}


def test_le_colonne_dichiarate_sono_quelle_del_template():
    # Un gruppo vale tra colonne MOSTRATE: se l'elenco diverge dal template, il
    # gruppo si applica a colonne che non ci sono o manca quelle che ci sono.
    sorgente = open(robustezza.__file__.rsplit("robustezza.py", 1)[0] + "templates/_robustezza_tecnica.html",
                    encoding="utf-8").read()
    blocco = sorgente[sorgente.index('class="tabella-blocco"'):sorgente.index('class="tabella-serie"')]
    serie = sorgente[sorgente.index('class="tabella-serie"'):]
    campi = lambda testo: {c for c in re.findall(r'cella\(r, "(\w+)"', testo)}  # noqa: E731
    assert campi(blocco) == set(robustezza.COLONNE_BLOCCO)
    assert campi(serie) == set(robustezza.COLONNE_SERIE)


def test_la_colonna_e_la_tabella_non_la_pagina():
    # Due blocchi della stessa pagina possono avere decimali diversi per lo stesso
    # campo: una precisione comune sarebbe una colonna che attraversa i layer.
    vista = robustezza.tecnica(SCENARIOS[GUILD_EDGE]["robustness"])
    per_layer = {b.layer: b.decimali["targeted_excess"] for b in vista.blocchi}
    assert per_layer["reaction"] == 4
    assert per_layer["voice"] == 3


def test_la_scala_mostra_la_stessa_tabella(dashboard):
    """...004 ha due snapshot: la serie c'e', e i quattro blocchi."""
    html = dashboard.get(f"/guilds/{GUILD_SCALE}/dettagli-tecnici").text
    assert html.count('class="blocco"') == 4 and 'class="tabella-serie"' in html


def test_su_telefono_le_etichette_delle_celle_sono_solo_la_frazione(dashboard):
    """Il titolo del gruppo di colonne sta sulla riga (data-frazioni), non
    nell'etichetta della prima cella: li' andava su tre righe a 320px e
    disallineava la sua barra da quelle accanto (30/09/2026)."""
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    for layer in ("reply", "mention", "reaction"):
        tr = _riga(html, layer)
        assert _html.unescape(tr.attrs["data-frazioni"]) == "Restano collegati senza le più centrali"
        assert [td.attrs["data-etichetta"] for td in tr.trova("td", classe="frazione")] == [
            "1 su 20", "1 su 10", "1 su 5"
        ]
    assert "data-frazioni" not in _riga(html, "voice").html()


# --- l'etichetta delle settimane nelle tabelle storiche (30/09/2026) ----------


def test_le_settimane_precedenti_dicono_l_intervallo_coperto():
    """L'as_of e' il lunedi' in cui la settimana si CHIUDE: la riga del 7/09 copre
    31/08-06/09. "7 settembre" si leggeva con la convenzione di Coorti (il lunedi'
    d'inizio). L'intervallo solo se il calcolo prima e' della settimana prima;
    la riga piu' vecchia, senza precedente nella pagina, dice "chiusa il"."""
    righe = [r for i in range(3) for r in _layer(11 + i, _lunedi(i), "reply", 24)]
    html = _rendi(righe)
    storia = next(_albero(html).radice.trova("details", classe="robustezza-storia"))
    tabella = next(storia.trova("table", classe="robustezza-lettura--storia"))
    etichette = [_html.unescape(next(tr.trova("th")).testo()).strip()
                 for tr in tabella.trova("tr", classe="robustezza-riga")]
    assert etichette == ["14 set – 20 set", "7 set – 13 set", "chiusa il 7 settembre"]


def test_un_buco_nella_serie_non_inventa_l_intervallo():
    righe = _layer(11, _lunedi(0), "reply", 24) + _layer(12, _lunedi(2), "reply", 24)
    html = _rendi(righe)
    storia = next(_albero(html).radice.trova("details", classe="robustezza-storia"))
    tabella = next(storia.trova("table", classe="robustezza-lettura--storia"))
    etichette = [_html.unescape(next(tr.trova("th")).testo()).strip()
                 for tr in tabella.trova("tr", classe="robustezza-riga")]
    assert etichette == ["chiusa il 21 settembre", "chiusa il 7 settembre"]
