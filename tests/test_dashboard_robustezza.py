"""Vista Robustezza: le regole che passerebbero per sempre confermando nulla.

Quasi tutto qui si verifica sul MARKUP prodotto, non sul motore. Le regole di
questa vista sono invarianti di template — la regola 6 non puo' essere imposta da
``cella()``, che solleva ``KeyError`` su ``removal_fraction`` di proposito; le
etichette di riga possono sparire del tutto dal macro che le omette sulle celle di
valore — e un test sul motore resterebbe verde mentre la pagina le viola.

Il fixture si monta in-process come per la vista Stato. Per le soglie della
regola 4 si chiede all'API fixture un ``limit`` diverso e si rende la stessa vista.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.models import RobustnessRow
from dashboard import config, robustezza
from dashboard.main import crea_app, crea_templates
from dashboard.qualifica import NON_VALUTATO, cella
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_TODAY, SCENARIOS, _robustness_layer
from tools.fixture_api import app as fixture_app


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


def _client(transport: httpx.AsyncBaseTransport) -> TestClient:
    return TestClient(crea_app(api_http=httpx.AsyncClient(transport=transport, base_url="http://api.test")))


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


def _rendi(righe: list[RobustnessRow], guild_id: int = 1) -> str:
    return crea_templates().get_template("robustezza.html").render(
        guild_id=guild_id, vista=robustezza.costruisci(righe), vista_corrente="robustezza",
    )


# --- un albero minimo del markup ------------------------------------------


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


_PERCENTUALE = re.compile(r"\b\d+(?:[.,]\d+)?\s?%")


# --- regola 6 sul markup ----------------------------------------------------


def _pagine(api) -> list[tuple[str, str]]:
    pagine = []
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE):
        for limit in (1, 2, 3, 4, 12):
            pagine.append((f"{gid}?limit={limit}", _rendi(_righe(api, gid, limit), gid)))
    return pagine


def test_regola_6_nessuna_percentuale_senza_i_nodi_rimossi_della_stessa_riga(api):
    viste = 0
    for nome, html in _pagine(api):
        albero = _albero(html)
        for testo, contenitore in albero.testi:
            if contenitore.tag in ("style", "script") or not _PERCENTUALE.search(testo):
                continue
            viste += 1
            catena = list(_antenati(contenitore))

            riga = next((n for n in catena if "riga-rimozione" in n.classi), None)
            voce = next((n for n in catena if "voce-serie" in n.classi), None)
            titolo = next((n for n in catena if n.tag == "title"), None)

            if riga is not None:
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
            elif voce is not None:
                assert re.search(r"\d+ nod[oi] rimoss[oi]", voce.testo()), (nome, voce.testo())
            elif titolo is not None:
                assert re.search(r"\d+ nod[oi] rimoss[oi]", titolo.testo()), (nome, titolo.testo())
            else:
                pytest.fail(f"{nome}: percentuale fuori da riga, legenda o <title>: {testo!r}")
    assert viste > 100


def test_regola_6_intervallo_costante_diventa_valore_singolo():
    righe = [
        r for i, sid in enumerate((1, 2, 3))
        for r in _robustness_layer(sid, datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(weeks=i),
                                   "voice", n=10, excess={0.05: 0.1, 0.10: 0.1, 0.20: 0.2})
    ]
    vista = robustezza.costruisci(righe)
    legende = {s.removal_fraction: s.legenda for s in vista.blocchi[0].grafico.serie}
    assert legende[0.05] == "5% · 1 nodo rimosso"
    assert legende[0.20] == "20% · 2 nodi rimossi"


# --- regola 4 ---------------------------------------------------------------


def test_regola_4_guild_di_oggi_mostra_tabelle_e_nessun_grafico(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    assert "<svg" not in html
    assert html.count('class="tabella-serie"') == 4


def test_regola_4_guild_matura_mostra_i_grafici(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    assert html.count("<svg") == 4


@pytest.mark.parametrize("limit,grafici", [(1, 0), (2, 0), (3, 3)])
def test_regola_4_soglia_con_limit(api, limit, grafici):
    # limit=3: reply, mention e reaction hanno tre punti. voice no (ultimo
    # snapshot assente): il suo blocco mostra la tabella, e una serie corta non
    # cancella il grafico degli altri layer.
    html = _rendi(_righe(api, GUILD_MATURE, limit), GUILD_MATURE)
    assert html.count("<svg") == grafici
    if limit == 3:
        voice = next(_albero(html).radice.trova("section", data_layer="voice"))
        assert "<svg" not in voice.html()
        assert voice.conta_html('class="tabella-serie"') == 1


# --- la qualificazione della serie -----------------------------------------


def _figura(html: str, layer: str) -> Nodo:
    return next(_albero(html).radice.trova("figure", data_grafico=layer))


def test_punto_soppresso_o_layer_assente_non_si_disegna_e_la_linea_si_interrompe(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    voice = _figura(html, "voice")
    disegnati = {c.attrs["data-snapshot"] for c in voice.trova("circle")}
    assert "33" not in disegnati  # soppresso
    assert "35" not in disegnati  # layer assente
    assert "40" not in disegnati  # layer assente nell'ultimo snapshot
    assert "soppresso" not in voice.testo()

    # Drawable: 29-32 | 34 | 36-39. Segmenti solo tra consecutivi: 3 + 0 + 3.
    for g in voice.trova("g", classe="serie"):
        assert g.attrs["data-segmenti"] == "6"

    vista = robustezza.costruisci(SCENARIOS[GUILD_MATURE]["robustness"])
    voice_blocco = next(b for b in vista.blocchi if b.layer == "voice")
    x_di = {p.snapshot.snapshot_id: p.x for p in voice_blocco.grafico.serie[0].punti}
    for seg in voice_blocco.grafico.serie[0].segmenti:
        # Nessun segmento scavalca lo snapshot soppresso (33) o quello assente (35).
        for saltato in (33, 35):
            assert not (seg.x1 < x_di[saltato] < seg.x2)


def test_il_segmento_eredita_la_qualificazione_peggiore():
    t0 = datetime(2026, 7, 6, tzinfo=timezone.utc)
    # significativo (n=40), non significativo (n=20), significativo, significativo
    righe = [
        r for i, n in enumerate((40, 20, 40, 40))
        for r in _robustness_layer(i + 1, t0 + timedelta(weeks=i), "reply", n=n,
                                   excess={0.05: 0.1, 0.10: 0.2, 0.20: 0.3})
    ]
    serie = robustezza.costruisci(righe).blocchi[1].grafico.serie[2]
    assert [s.dequalificato for s in serie.segmenti] == [True, True, False]

    html = _rendi(righe)
    g = next(_albero(html).radice.trova("g", classe="serie--2"))
    stili = ["segmento--dequalificato" in l.classi for l in g.trova("line")]
    assert stili == [True, True, False]


def test_tutti_non_significativi_etichetta_una_volta_sola(api):
    # limit=4 su ...002: voice ha tre punti, tutti non significativi.
    html = _rendi(_righe(api, GUILD_MATURE, 4), GUILD_MATURE)
    voice = _figura(html, "voice")
    assert voice.testo().count("non significativo") == 1
    assert voice.conta_html("etichetta-grafico") == 1
    assert voice.conta_html("voce-dequalificato") == 0


def test_caso_misto_la_legenda_spiega_lo_stile(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    voice = _figura(html, "voice")
    assert voice.conta_html("voce-dequalificato") == 1
    assert voice.conta_html("etichetta-grafico") == 0
    # I layer tutti significativi non hanno ne' etichetta ne' voce di legenda.
    reply = _figura(html, "reply")
    assert "non significativo" not in reply.testo()


# --- asse y -----------------------------------------------------------------


def test_asse_y_include_il_negativo_e_non_parte_da_zero(dashboard):
    valori_003 = [r.values.targeted_excess for r in SCENARIOS[GUILD_EDGE]["robustness"]
                  if r.layer == "voice"]
    assert robustezza.dominio_y(valori_003)[0] == -0.07

    vista = robustezza.costruisci(SCENARIOS[GUILD_MATURE]["robustness"])
    reply = next(b for b in vista.blocchi if b.layer == "reply").grafico
    assert reply.y_min == -0.03
    negativo = next(p for s in reply.serie for p in s.disegnabili
                    if p.riga.values.targeted_excess < 0)
    assert negativo.y > reply.y_zero  # sotto la linea dello zero

    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    assert "-0,03" in _figura(html, "reply").testo()


def test_dominio_senza_negativi_include_comunque_lo_zero():
    assert robustezza.dominio_y([0.2, 0.4]) == (0.0, 0.4)
    assert robustezza.dominio_y([0.0, 0.0]) == (-0.1, 0.1)


# --- n_effective per blocco ---------------------------------------------------


def test_le_tre_righe_di_un_snapshot_layer_condividono_n_effective():
    # Vero per costruzione nel job, ma un'assunzione della vista: si verifica.
    for scenario in SCENARIOS.values():
        parti: dict[tuple[int, str], set] = {}
        for r in scenario["robustness"]:
            if not r.quality.suppressed:
                parti.setdefault((r.snapshot_id, r.layer), set()).add(r.quality.n_effective)
        assert all(len(v) == 1 for v in parti.values())


def test_n_effective_compare_una_volta_per_blocco(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_EDGE}/robustezza").text
    albero = _albero(html).radice
    atteso = {"voice": "61", "mention": "10", "reaction": "44"}
    for layer, n in atteso.items():
        sezione = next(albero.trova("section", data_layer=layer))
        assert [s.attrs["data-n-effective"] for s in sezione.trova("strong") if "data-n-effective" in s.attrs] == [n]
    reply = next(albero.trova("section", data_layer="reply"))
    assert "data-n-effective" not in reply.html()
    assert next(reply.trova("p", classe="dimensione")).testo().count("soppresso") == 1


# --- assenze ------------------------------------------------------------------


def test_layer_assente_ha_la_sua_frase_non_la_soppressione_e_non_sparisce(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/robustezza").text
    albero = _albero(html).radice
    assert [s.attrs["data-layer"] for s in albero.trova("section", classe="blocco")] == [
        "voice", "reply", "mention", "reaction"
    ]
    voice = next(albero.trova("section", data_layer="voice"))
    frase = next(voice.trova("p", classe="frase-assente"))
    assert "Nessuna interazione di questo tipo in questa settimana" in frase.testo()
    assert "tabella-blocco" not in voice.html()
    assert "⊘" not in voice.testo() and "soppresso" not in voice.testo()


def test_layer_assente_ovunque_nessuna_cornice_di_grafico():
    righe = [
        r for i in range(4)
        for r in _robustness_layer(i + 1, datetime(2026, 7, 6, tzinfo=timezone.utc) + timedelta(weeks=i),
                                   "reply", n=40, excess={0.05: 0.1, 0.10: 0.2, 0.20: 0.3})
    ]
    html = _rendi(righe)
    voice = next(_albero(html).radice.trova("section", data_layer="voice"))
    assert "<svg" not in voice.html()
    assert "tabella-serie" not in voice.html()
    assert "Nessuna interazione di questo tipo" in voice.testo()


def test_guild_osservata_senza_snapshot_il_calcolo_non_e_ancora_girato():
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(api)) as client:
        r = client.get("/guilds/5/robustezza")
    assert r.status_code == 200
    assert "Il calcolo non è ancora girato" in r.text
    assert 'class="errore"' not in r.text
    assert "soppresso" not in r.text.split("<body>", 1)[1]
    assert 'class="blocco"' not in r.text


def test_guild_non_osservata_e_api_giu(dashboard):
    assert dashboard.get("/guilds/123/robustezza").status_code == 404

    def giu(request):
        raise httpx.ConnectError("rifiutata", request=request)

    with _client(httpx.MockTransport(giu)) as client:
        r = client.get(f"/guilds/{GUILD_EDGE}/robustezza")
    assert r.status_code == 503 and 'class="errore"' in r.text


# --- etichette di riga: esattamente una volta per riga ----------------------


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


def test_non_significativo_dequalifica_ogni_cella_della_riga(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/robustezza").text
    tr = next(_albero(html).radice.trova("tr", classe="riga-rimozione"))
    valori = [s for s in tr.trova("span", classe="cella")]
    assert valori and all("cella--dequalificata" in s.classi for s in valori)


# --- navigazione e perimetro --------------------------------------------------


def test_navigazione_solo_con_le_viste_che_esistono(dashboard):
    for percorso in (f"/guilds/{GUILD_EDGE}", f"/guilds/{GUILD_EDGE}/robustezza"):
        html = dashboard.get(percorso).text
        nav = next(_albero(html).radice.trova("nav", classe="viste"))
        assert [a.testo().strip() for a in nav.trova("a")] == ["Stato", "Robustezza"]
        assert "Community" not in nav.testo() and "Coorti" not in nav.testo()


def test_la_vista_non_legge_details():
    import inspect

    sorgente = inspect.getsource(robustezza)
    codice = "\n".join(l for l in sorgente.splitlines() if not l.strip().startswith(("#", '"', "``")))
    assert ".details" not in codice
    template = (robustezza.__file__.rsplit("robustezza.py", 1)[0] + "templates/robustezza.html")
    assert ".details" not in open(template, encoding="utf-8").read().split("-#}", 1)[1]
