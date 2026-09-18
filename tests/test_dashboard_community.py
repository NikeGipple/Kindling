"""Vista Community: le regole si verificano sul MARKUP, come per Robustezza.

La regola 5 estesa (``stability_jaccard`` insieme a ``previous_gap_days`` e
``node_overlap``) e l'etichetta di riga unica sono invarianti di template:
``cella()`` non puo' imporle, e un test sul motore resterebbe verde mentre la
pagina le viola.

Il fixture si monta in-process. Dove serve uno stato che l'ultimo snapshot di una
guild non mostra (lo snapshot 29 di ``…002``, l'11 di ``…001``) si rendono le righe
di quello snapshot con ``community.costruisci``, come i test di Robustezza fanno
con ``_robustness_layer``. Le righe sintetiche passano da ``_community_layer``,
che le deriva come ``compute_communities``: nessuna riga che il job non produce.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.models import CommunityRow
from dashboard import community, config
from dashboard.client import ApiClient
from dashboard.main import crea_app, crea_templates
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from dashboard.qualifica import NON_VALUTATO
from tests.test_dashboard_robustezza import Nodo, _albero
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_MATURE,
    GUILD_TODAY,
    PARAMS,
    SCENARIOS,
    _community_layer,
    _Precedente,
)
from tools.fixture_api import app as fixture_app

TRE_CAMPI = ("previous_gap_days", "node_overlap", "stability_jaccard")


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


def _righe(api, guild_id: int, limit: int = 12) -> list[CommunityRow]:
    risposta = api.get(f"/guilds/{guild_id}/communities", params={"limit": limit})
    return TypeAdapter(list[CommunityRow]).validate_json(risposta.content)


def _rendi(righe: list[CommunityRow], guild_id: int = 1) -> str:
    return crea_templates().get_template("community.html").render(
        guild_id=guild_id, vista=community.costruisci(righe), vista_corrente="community",
    )


def _dello_snapshot(guild_id: int, snapshot_id: int) -> list[CommunityRow]:
    return [c for c in SCENARIOS[guild_id]["communities"] if c.snapshot_id == snapshot_id]


def _sezione(html: str, layer: str) -> Nodo:
    return next(_albero(html).radice.trova("section", data_layer=layer))


def _pagine(api) -> list[tuple[str, str]]:
    pagine = []
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE):
        for limit in (1, 2, 3, 4, 12):
            pagine.append((f"{gid}?limit={limit}", _rendi(_righe(api, gid, limit), gid)))
    # Gli snapshot che nessun "ultimo" mostra nel blocco.
    pagine.append(("…002@29", _rendi(_dello_snapshot(GUILD_MATURE, 29), GUILD_MATURE)))
    pagine.append(("…001@11", _rendi(_dello_snapshot(GUILD_TODAY, 11), GUILD_TODAY)))
    return pagine


def _celle(tr: Nodo) -> list[Nodo]:
    return [f for f in tr.figli if isinstance(f, Nodo) and f.tag == "td"]


def _testo_cella(td: Nodo) -> str:
    return " ".join(s.testo().strip() for s in td.trova("span", classe="cella__testo"))


# --- 0.2: significativa senza nessun dato di stabilita' ----------------------


def test_prima_osservazione_significativa_si_pubblica_per_intero_senza_stabilita():
    righe = _dello_snapshot(GUILD_MATURE, 29)
    reply = next(r for r in righe if r.layer == "reply")
    # Lo stato che la correzione di modello-metriche.md 4.6 rende possibile.
    assert reply.quality.significant is True
    assert all(getattr(reply.values, c) is None for c in TRE_CAMPI)

    tr = next(_sezione(_rendi(righe, GUILD_MATURE), "reply").trova("tr", classe="riga-community"))
    markup = tr.html()
    # Nessuna etichetta di riga: e' significativa.
    assert "etichetta--" not in markup
    # Nessun simbolo di soppressione, e niente "non disponibile" ripetuto sette volte.
    assert "⊘" not in markup and "soppresso" not in markup
    assert "non disponibile" not in markup
    # La meta' struttura e' piena e non dequalificata.
    assert "cella--dequalificata" not in markup
    assert "4,400" in tr.testo()
    # La meta' stabilita' e' una frase sola, che nomina i tre campi.
    frase = next(tr.trova("td", classe="senza-confronto"))
    assert frase.attrs["colspan"] == "7"
    assert "gap" in frase.testo() and "sovrapposizione" in frase.testo() and "stabilità" in frase.testo()


# --- voice@12: il flag e' uno, e l'etichetta anche --------------------------


def test_voice_12_non_significativa_con_z_e_stabilita_visibili_e_una_sola_etichetta(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/community").text
    tr = next(_sezione(html, "voice").trova("tr", classe="riga-community"))
    markup = tr.html()

    assert markup.count("etichetta--") == 1
    assert markup.count("etichetta--non_significativo") == 1
    assert "⊘" not in markup and "∅" not in markup and "non disponibile" not in markup

    per_campo = {td.attrs["data-campo"]: _testo_cella(td) for td in _celle(tr) if "data-campo" in td.attrs}
    assert per_campo == {"previous_gap_days": "6,823", "node_overlap": "0,500", "stability_jaccard": "0,333"}
    assert "5,624" in [_testo_cella(td) for td in _celle(tr)]


# --- regola 5 estesa ---------------------------------------------------------


def test_regola_5_stabilita_mai_senza_gap_e_sovrapposizione_della_stessa_riga(api):
    stati = {"senza_confronto": 0, "sovrapposizione_bassa": 0, "calcolata": 0}
    titoli = legende = 0
    for nome, html in _pagine(api):
        albero = _albero(html).radice

        for tr in [*albero.trova("tr", classe="riga-community"), *albero.trova("tr", classe="riga-serie")]:
            celle = _celle(tr)
            campi = {td.attrs.get("data-campo") for td in celle}
            frase = [td for td in celle if "senza-confronto" in td.classi]
            if frase:
                # Nessuno dei tre in una cella propria, e la frase li nomina tutti.
                assert not campi & set(TRE_CAMPI), (nome, tr.html())
                assert frase[0].attrs["data-campi-confronto"].split() == list(TRE_CAMPI)
                stati["senza_confronto"] += 1
            elif campi & set(TRE_CAMPI):
                # Dove uno dei tre compare, compaiono tutti e tre, ADIACENTI.
                indici = [i for i, td in enumerate(celle) if td.attrs.get("data-campo") in TRE_CAMPI]
                assert [celle[i].attrs["data-campo"] for i in indici] == list(TRE_CAMPI), (nome, tr.html())
                assert indici == list(range(indici[0], indici[0] + 3)), (nome, tr.html())
                esiti = {celle[i].attrs["data-campo"]: next(celle[i].trova("span", classe="cella")).attrs["data-esito"]
                         for i in indici}
                if esiti["stability_jaccard"] == "valore":
                    stati["calcolata"] += 1
                else:
                    assert esiti["node_overlap"] == "valore", (nome, tr.html())
                    stati["sovrapposizione_bassa"] += 1

        for figura in albero.trova("figure", data_campo_grafico="stability_jaccard"):
            for titolo in figura.trova("title"):
                assert "stabilità" in titolo.testo() and "gap" in titolo.testo() and "sovrapposizione" in titolo.testo()
                titoli += 1
            voce = next(figura.trova("li", classe="voce-confronto"))
            assert "gap" in voce.testo() and "sovrapposizione" in voce.testo()
            legende += 1

        # Il grafico della modularita' non porta la stabilita', e viceversa.
        for figura in albero.trova("figure", data_campo_grafico="modularity_z"):
            assert "stabilità" not in figura.testo()

    assert all(n > 0 for n in stati.values()), stati
    assert titoli > 20 and legende > 0


# --- distribuzione delle dimensioni -------------------------------------------


def test_classi_a_righe_variabili_reaction_12_ha_una_riga_sola(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/community").text
    tabella = next(_sezione(html, "reaction").trova("table", classe="tabella-classi"))
    righe = list(tabella.trova("tr", classe="riga-classe"))
    assert [tr.attrs["data-classe"] for tr in righe] == ["5-9"]
    assert [_testo_cella(td) for td in _celle(righe[0])[1:3]] == ["4", "25"]
    assert "—" not in tabella.testo()


def test_soppressione_a_cascata_reaction_11():
    html = _rendi(_dello_snapshot(GUILD_TODAY, 11), GUILD_TODAY)
    tabella = next(_sezione(html, "reaction").trova("table", classe="tabella-classi"))
    righe = {tr.attrs["data-classe"]: tr for tr in tabella.trova("tr", classe="riga-classe")}
    # Nell'ordine del job, non in quello alfabetico dell'API.
    assert list(righe) == ["small", "5-9", "10-19"]
    assert len(list(tabella.trova("tr"))) == 4  # intestazione + tre classi: nessun totale

    assert [_testo_cella(td) for td in _celle(righe["10-19"])[1:3]] == ["1", "12"]
    for classe in ("small", "5-9"):
        markup = righe[classe].html()
        assert markup.count("⊘") == 1 and "cella--soppresso" in markup, classe
    spiegazioni = {c: next(righe[c].trova("span", classe="cella__spiegazione")).testo() for c in ("small", "5-9")}
    assert spiegazioni["small"] != spiegazioni["5-9"]
    assert "secondaria" in spiegazioni["5-9"]
    # Mai "il resto": community_count di riga (3) meno quello pubblicato (1).
    sezione = _sezione(html, "reaction")
    assert "resto" not in sezione.testo() and "totale" not in sezione.testo()


def test_riga_madre_soppressa_tutte_le_classi_soppresse(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_EDGE}/community").text
    reply = _sezione(html, "reply")
    tr = next(reply.trova("tr", classe="riga-community"))
    assert tr.html().count("⊘") == 1 and "data-n-effective" not in tr.html()
    classi = list(reply.trova("tr", classe="riga-classe"))
    assert classi and all("⊘" in tr.html() for tr in classi)


def test_le_classi_non_leggono_n_effective_ne_significant():
    sorgente = open(community.__file__.rsplit("community.py", 1)[0] + "templates/community.html",
                    encoding="utf-8").read()
    classi = sorgente[sorgente.index('class="tabella-classi"'):]
    classi = classi[:classi.index("</table>")]
    assert "n_effective" not in classi and "significant" not in classi


# --- la serie ------------------------------------------------------------------


def _serie_sintetica(overlaps: list[float]) -> list[CommunityRow]:
    """reply a 40 nodi, un precedente per snapshot. Un overlap sotto il minimo
    lascia la riga pubblicata e stability_jaccard a None: il terzo caso."""
    t0 = datetime(2026, 7, 6, tzinfo=timezone.utc)
    righe = []
    for i, overlap in enumerate(overlaps):
        as_of = t0 + timedelta(weeks=i)
        righe.append(_community_layer(
            100 + i, as_of, "reply", n=40, dimensioni=(10, 10, 10, 10), modularity_z=3.1,
            precedente=_Precedente(99 + i, as_of - timedelta(weeks=1)),
            overlap=overlap, stability=0.6 + i / 100,
        ))
    return righe


def test_terzo_caso_stabilita_assente_su_riga_pubblicata_interrompe_la_linea_senza_simbolo():
    righe = _serie_sintetica([0.8, 0.8, 0.3, 0.8, 0.8])
    assert not righe[2].quality.suppressed and righe[2].values.stability_jaccard is None

    vista = community.costruisci(righe)
    g = next(b for b in vista.blocchi if b.layer == "reply").stabilita.grafico
    assert g is not None
    x_mancante = g.punti[2].x
    assert len(g.segmenti) == 2
    assert not any(s.x1 < x_mancante < s.x2 for s in g.segmenti)

    figura = next(_sezione(_rendi(righe), "reply").trova("figure", data_campo_grafico="stability_jaccard"))
    assert {c.attrs["data-snapshot"] for c in figura.trova("circle")} == {"100", "101", "103", "104"}
    assert "⊘" not in figura.html() and "soppresso" not in figura.testo()


def test_terzo_caso_in_tabella_la_cella_non_porta_la_soppressione(dashboard):
    # …001: mention@12 ha node_overlap 0,196 e nessuna stabilita'.
    html = dashboard.get(f"/guilds/{GUILD_TODAY}/community").text
    tabella = next(_sezione(html, "mention").trova("table", data_campo_serie="stability_jaccard"))
    tr = next(tabella.trova("tr", data_snapshot="12"))
    stabilita = next(td for td in _celle(tr) if td.attrs.get("data-campo") == "stability_jaccard")
    assert next(stabilita.trova("span", classe="cella")).attrs["data-esito"] == "assente"
    assert "⊘" not in tr.html()


def test_voice_002_riappare_e_stabilita_non_disegnata(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/community").text
    voice = _sezione(html, "voice")
    stabilita = next(voice.trova("figure", data_campo_grafico="stability_jaccard"))
    disegnati = {c.attrs["data-snapshot"] for c in stabilita.trova("circle")}
    assert "36" not in disegnati  # layer riapparso: overlap 0, riga pubblicata
    assert "33" not in disegnati  # soppresso
    assert "⊘" not in stabilita.html()
    modularita = next(voice.trova("figure", data_campo_grafico="modularity_z"))
    assert "36" in {c.attrs["data-snapshot"] for c in modularita.trova("circle")}
    # Caso misto su entrambi: voice@31 e' la sola settimana significativa.
    assert modularita.conta_html("voce-dequalificato") == 1
    assert stabilita.conta_html("voce-dequalificato") == 1


@pytest.mark.parametrize("guild_id,svg,tabelle", [(GUILD_TODAY, 0, 8), (GUILD_MATURE, 8, 0)])
def test_regola_4_su_entrambi_i_grafici(dashboard, guild_id, svg, tabelle):
    html = dashboard.get(f"/guilds/{guild_id}/community").text
    albero = _albero(html).radice
    assert html.count("<svg") == svg
    assert len(list(albero.trova("table", classe="tabella-serie"))) == tabelle
    for campo in ("modularity_z", "stability_jaccard"):
        grafici = len(list(albero.trova("figure", data_campo_grafico=campo)))
        serie = len(list(albero.trova("table", data_campo_serie=campo)))
        assert grafici + serie == 4, (campo, grafici, serie)


def test_un_solo_snapshot_nessuna_tabella_della_serie(api):
    html = _rendi(_righe(api, GUILD_EDGE), GUILD_EDGE)
    assert "tabella-serie" not in html and "<svg" not in html


def test_riferimento_solo_sulla_modularita(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/community").text
    albero = _albero(html).radice
    for figura in albero.trova("figure", data_campo_grafico="modularity_z"):
        assert figura.conta_html('class="riferimento"') == 1
        assert "soglia di significatività" in figura.testo()
    for figura in albero.trova("figure", data_campo_grafico="stability_jaccard"):
        # Nel job non esiste una soglia su stability_jaccard: nessuna linea, nessuna etichetta.
        assert "riferimento" not in figura.html()
        ticks = {t.testo().strip() for t in figura.trova("text", classe="tick-y")}
        assert ticks == {"0,000", "1,000"}


def test_dominio_della_modularita_include_zero_e_soglia():
    assert community.dominio_modularita([-1.009, 0.187]) == (-1.009, PARAMS.min_modularity_z)
    assert community.dominio_modularita([2.5, 5.5]) == (0.0, 5.5)


def test_tutti_non_significativi_etichetta_una_volta_sola(api):
    # limit=4 su …002: voice a 37-39, nessuna settimana significativa.
    html = _rendi(_righe(api, GUILD_MATURE, 4), GUILD_MATURE)
    figura = next(_sezione(html, "voice").trova("figure", data_campo_grafico="modularity_z"))
    assert figura.testo().count("non significativo") == 1
    assert figura.conta_html("voce-dequalificato") == 0


# --- etichette e precisione ------------------------------------------------------


def test_ogni_riga_ha_al_piu_un_etichetta_ed_e_quella_del_flag(api):
    per_chiave = {(str(c.snapshot_id), c.layer): c for s in SCENARIOS.values() for c in s["communities"]}
    viste = 0
    for nome, html in _pagine(api):
        albero = _albero(html).radice
        for tr in [*albero.trova("tr", classe="riga-community"), *albero.trova("tr", classe="riga-serie")]:
            riga = per_chiave[(tr.attrs["data-snapshot"], tr.attrs["data-layer"])]
            attesa = 1 if riga.quality.significant is False else 0
            assert tr.html().count("etichetta--non_significativo") == attesa, (nome, tr.html())
            assert tr.html().count("etichetta--") == attesa, (nome, tr.html())
            viste += 1
        # Le classi non hanno significativita': nessuna etichetta, mai.
        for tr in albero.trova("tr", classe="riga-classe"):
            assert "etichetta--" not in tr.html()
        assert NON_VALUTATO not in html
    assert viste > 50


def test_node_overlap_e_stabilita_non_condividono_la_precisione():
    riga = _community_layer(1, datetime(2026, 9, 7, tzinfo=timezone.utc), "reply", n=40,
                            dimensioni=(20, 20), modularity_z=3.0,
                            precedente=_Precedente(0, datetime(2026, 8, 31, tzinfo=timezone.utc)),
                            overlap=0.9, stability=0.0004)
    decimali = community.decimali_per_campo([riga], community.COLONNE_BLOCCO)
    assert decimali["stability_jaccard"] == 4
    assert decimali["node_overlap"] == 3


def test_le_colonne_dichiarate_sono_quelle_del_template():
    sorgente = open(community.__file__.rsplit("community.py", 1)[0] + "templates/community.html",
                    encoding="utf-8").read()
    blocco = sorgente[sorgente.index('class="tabella-community"'):sorgente.index('class="tabella-classi"')]
    serie = sorgente[sorgente.index('class="tabella-serie"'):]
    decimali = lambda testo, dove: set(re.findall(dove + r'\.decimali\["(\w+)"\]', testo))  # noqa: E731
    assert decimali(blocco, "b") == set(community.COLONNE_BLOCCO) & {
        "modularity", "modularity_random_mean", "modularity_z", *TRE_CAMPI}
    assert set(re.findall(r'cella\(r, "(\w+)"', blocco)) == set(community.COLONNE_BLOCCO)
    assert decimali(serie, "s") == {*community.COLONNE_SERIE_MODULARITA, *community.COLONNE_SERIE_STABILITA}


# --- assenze, rotta, perimetro ---------------------------------------------------


def test_layer_assente_ha_la_sua_frase(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}/community").text
    albero = _albero(html).radice
    assert [s.attrs["data-layer"] for s in albero.trova("section", classe="blocco")] == [
        "voice", "reply", "mention", "reaction"
    ]
    voice = _sezione(html, "voice")
    assert "Nessuna interazione di questo tipo in questa settimana" in voice.testo()
    assert "tabella-community" not in voice.html() and "tabella-classi" not in voice.html()


def test_guild_osservata_senza_snapshot_il_calcolo_non_e_ancora_girato():
    with _client(httpx.MockTransport(lambda request: httpx.Response(200, json=[]))) as client:
        r = client.get("/guilds/5/community")
    assert r.status_code == 200
    assert "Il calcolo non è ancora girato" in r.text
    assert 'class="errore"' not in r.text and 'class="blocco"' not in r.text


def test_guild_non_osservata_e_api_giu(dashboard):
    assert dashboard.get("/guilds/123/community").status_code == 404

    def giu(request):
        raise httpx.ConnectError("rifiutata", request=request)

    with _client(httpx.MockTransport(giu)) as client:
        r = client.get(f"/guilds/{GUILD_EDGE}/community")
    assert r.status_code == 503 and 'class="errore"' in r.text


def test_una_sola_chiamata_con_limit_dodici():
    chiamate: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        chiamate.append(request)
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(api)) as client:
        client.get("/guilds/5/community")
    assert [(r.url.path, dict(r.url.params)) for r in chiamate] == [("/guilds/5/communities", {"limit": "12"})]


def test_navigazione_include_community(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_EDGE}/community").text
    nav = next(_albero(html).radice.trova("nav", classe="viste"))
    assert [a.testo().strip() for a in nav.trova("a")] == [
        "Stato", "Robustezza", "Community", "Coorti"]
    corrente = [a for a in nav.trova("a") if a.attrs.get("aria-current") == "page"]
    assert [a.testo().strip() for a in corrente] == ["Community"]


def test_la_vista_non_legge_details():
    sorgente = inspect.getsource(community)
    codice = "\n".join(l for l in sorgente.splitlines() if not l.strip().startswith(("#", '"', "``")))
    assert ".details" not in codice
    template = community.__file__.rsplit("community.py", 1)[0] + "templates/community.html"
    assert ".details" not in open(template, encoding="utf-8").read().split("-#}", 1)[1]


def test_client_communities_ha_la_forma_di_robustness():
    firma = inspect.signature(ApiClient.communities)
    assert list(firma.parameters) == list(inspect.signature(ApiClient.robustness).parameters)
    assert firma.parameters["limit"].default == 12
