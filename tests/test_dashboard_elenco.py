"""L'elenco dei server, la pagina ``/`` (dashboard.md 4, «L'elenco dei server, in dettaglio»).

Cosa prova ogni gruppo, e perche' senza non se ne accorgerebbe nessuno:

1. **L'ordine e' totale**, anche sui casi scomodi: accenti, iniziali minuscole,
   nomi uguali con ID diversi, righe senza nome. Un ordine parziale non da'
   errori: da' due pagine diverse per gli stessi dati (CLAUDE.md 7).
2. **Le date**: brevi, a Roma, l'anno solo quando serve, e il dettaglio sotto.
3. **I due stati per riga**, piu' i due casi di confine: uscito senza calcolo, e
   rientrato — che non mostra la nota.
4. **L'icona**: l'URL si compone da un intero e da un hash valido, e con
   qualunque altra cosa non si compone; l'origine e' la stessa che la CSP ammette.
5. **La pagina**: il nome accessibile del link e' il solo nome, un server solo
   resta un elenco, l'elenco vuoto ha il suo riquadro, i minuti vengono dal TTL,
   e l'elenco costa una chiamata all'API.

Nomi e hash inventati; gli id vengono dal fixture o sono piccoli interi.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser

import httpx
import pytest
from fastapi.testclient import TestClient

from api.models import GuildRow
from dashboard import auth, elenco, stato
from dashboard.main import CSP
from tests.sessione_dashboard import (
    app_di_test,
    client_autenticato,
    dati_di_sessione,
    entra,
    senza_variabili_di_database,
)
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_LEFT,
    GUILD_MATURE,
    GUILD_NEW,
    GUILD_TODAY,
)
from tools.fixture_api import app as fixture_app

U = timezone.utc
# Domenica 27 settembre 2026, a mezzogiorno: il giorno in cui l'elenco v2 e'
# stato scritto, e un "oggi" fisso per ogni data relativa.
ADESSO = datetime(2026, 9, 27, 12, 0, tzinfo=U)
OGGI = date(2026, 9, 27)

STATICA = "0123456789abcdef0123456789abcdef"
ANIMATA = "a_fedcba9876543210fedcba9876543210"


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    senza_variabili_di_database(monkeypatch)


@pytest.fixture
def orologio_fermo(monkeypatch):
    monkeypatch.setattr(stato, "adesso", lambda: ADESSO)


def _guild(gid: int, **campi) -> GuildRow:
    base = dict(
        guild_id=gid,
        first_seen_at=datetime(2026, 8, 30, 10, tzinfo=U),
        backfilled_at=None,
        left_at=None,
        rejoined_at=None,
        latest_metrics_as_of=datetime(2026, 9, 21, tzinfo=U),
    )
    base.update(campi)
    return GuildRow(**base)


# --- 1. l'ordine ---------------------------------------------------------------


def _ordine(nomi: dict[int, str], ids=None) -> list[int]:
    ids = ids if ids is not None else list(nomi)
    return [g.guild_id for g in elenco.ordina([_guild(i) for i in ids], nomi)]


def test_gli_accenti_non_mandano_un_nome_in_fondo():
    # Con casefold() da solo, "élite" > "zefiro": é viene dopo z in Unicode.
    nomi = {1: "Zefiro", 2: "Élite", 3: "Delta", 4: "Fenice"}
    assert _ordine(nomi) == [3, 2, 4, 1]


def test_le_iniziali_minuscole_contano_come_maiuscole():
    nomi = {1: "Raid del Venerdì", 2: "l'Ordine dei Sussurri", 3: "Gilda del Grifone"}
    assert _ordine(nomi) == [3, 2, 1]


def test_a_parita_di_nome_decide_l_id_in_qualunque_ordine_arrivino():
    # "Fucina" e "fucina" pareggiano anche con casefold(): le maiuscole non
    # contano, quindi decide l'ID, per tutte e tre.
    nomi = {30: "Fucina", 10: "Fucina", 20: "fucina"}
    for ids in ([30, 10, 20], [20, 30, 10], [10, 20, 30]):
        assert _ordine(nomi, ids) == [10, 20, 30], ids


def test_nomi_diversi_solo_per_un_accento_non_pareggiano():
    # "Elite" ed "Élite" hanno la stessa chiave senza accenti: decide il nome
    # vero, prima dell'ID, cosi' l'ordine non dipende dagli ID.
    assert _ordine({1: "Élite", 2: "Elite"}) == [2, 1]
    assert _ordine({2: "Élite", 1: "Elite"}) == [1, 2]


def test_le_righe_senza_nome_vanno_in_fondo_ordinate_per_id():
    nomi = {5: "Zefiro", 7: "Arco"}
    assert _ordine(nomi, [300, 5, 100, 7, 200]) == [7, 5, 100, 200, 300]


def test_l_ordine_e_una_funzione_pura_e_non_tocca_l_ingresso():
    guilds = [_guild(2), _guild(1)]
    elenco.ordina(guilds, {1: "A", 2: "B"})
    assert [g.guild_id for g in guilds] == [2, 1]


@pytest.mark.parametrize(
    "nome, atteso",
    [("l'Ordine", "L"), ("Élite", "É"), ("[ITA] Gilda", "I"), ("7 Mari", "7"), (None, "#"), ("", "#")],
)
def test_il_monogramma(nome, atteso):
    assert elenco.monogramma(nome) == atteso


# --- 2. le date ------------------------------------------------------------------


@pytest.mark.parametrize(
    "dal, atteso",
    [
        (date(2026, 9, 27), "oggi"),
        (date(2026, 9, 28), "oggi"),  # orologi disallineati: mai "tra 1 giorno"
        (date(2026, 9, 26), "1 giorno"),
        (date(2026, 9, 22), "5 giorni"),
        (date(2026, 9, 20), "1 settimana"),
        (date(2026, 8, 30), "4 settimane"),
        (date(2026, 6, 12), "15 settimane"),
        (date(2026, 3, 30), "25 settimane"),
        (date(2026, 3, 3), "6 mesi"),  # 208 giorni: settimane finite, mesi compiuti
        (date(2025, 9, 28), "11 mesi"),
        (date(2024, 9, 28), "23 mesi"),
        (date(2024, 9, 27), "2 anni"),
    ],
)
def test_la_durata(dal, atteso):
    assert elenco.durata(dal, OGGI) == atteso


def test_la_data_breve_a_roma_con_l_anno_solo_se_diverso():
    assert elenco.data(date(2026, 8, 30), OGGI) == "30 ago"
    assert elenco.data(date(2025, 3, 3), OGGI) == "3 mar 2025"
    # 23:30 UTC del 31 dicembre e' gia' l'1 gennaio a Roma: il giorno e l'anno
    # sono quelli che sceglie il chiamante, qui Roma.
    assert elenco.data(stato.giorno(datetime(2025, 12, 31, 23, 30, tzinfo=U)), date(2026, 1, 5)) == "1 gen"


def test_la_data_non_sceglie_il_fuso_da_se():
    """Un istante passato dritto e' un errore: il fuso lo sceglie chi chiama,
    con ``stato.giorno`` o ``stato.settimana`` (dashboard.md 4, "Quale fuso per
    cosa")."""
    with pytest.raises(TypeError):
        elenco.data(datetime(2026, 8, 30, 10, tzinfo=U), OGGI)


# --- 3. i due stati per riga -------------------------------------------------------


def _riga(guild: GuildRow, nome="Fucina", icona=None) -> elenco.Riga:
    return elenco.riga(guild, nome, icona, OGGI)


def test_riga_normale():
    r = _riga(_guild(1))
    assert (r.osserva.valore, r.osserva.dettaglio) == ("30 ago", "4 settimane")
    assert (r.calcolo.valore, r.calcolo.dettaglio) == ("21 set", "lunedì")
    assert not r.calcolo.in_attesa
    assert r.nota_uscita is None


def test_nessun_calcolo_ancora_senza_data():
    r = _riga(_guild(1, first_seen_at=datetime(2026, 9, 22, 9, tzinfo=U), latest_metrics_as_of=None))
    assert (r.osserva.valore, r.osserva.dettaglio) == ("22 set", "5 giorni")
    assert (r.calcolo.valore, r.calcolo.dettaglio) == ("non ancora", "arriva con il primo calcolo settimanale")
    assert r.calcolo.in_attesa
    # Il "lunedì 28 set" del mockup non c'e': la data del prossimo calcolo
    # richiederebbe la cadenza, e l'elenco non legge le run.
    assert "28" not in r.calcolo.dettaglio


def test_bot_uscito():
    r = _riga(_guild(1, first_seen_at=datetime(2026, 8, 18, 15, tzinfo=U),
                     left_at=datetime(2026, 9, 12, 16, 40, tzinfo=U),
                     latest_metrics_as_of=datetime(2026, 9, 14, tzinfo=U)))
    assert (r.osserva.valore, r.osserva.dettaglio) == ("18 ago", "fino al 12 set")
    assert r.nota_uscita == "Il bot non è più nel server dal 12 settembre: i dati si fermano a quel giorno."
    assert (r.calcolo.valore, r.calcolo.dettaglio) == ("14 set", "lunedì")


def test_bot_uscito_l_8_o_l_11_si_elide():
    r = _riga(_guild(1, left_at=datetime(2026, 9, 8, 12, tzinfo=U)))
    assert r.osserva.dettaglio == "fino all'8 set"
    assert r.nota_uscita.startswith("Il bot non è più nel server dall'8 settembre:")
    r = _riga(_guild(1, left_at=datetime(2026, 9, 11, 12, tzinfo=U)))
    assert r.osserva.dettaglio == "fino all'11 set"


def test_bot_uscito_e_nessun_calcolo_dice_nessuno_senza_promettere():
    r = _riga(_guild(1, left_at=datetime(2026, 9, 25, 12, tzinfo=U), latest_metrics_as_of=None))
    assert (r.calcolo.valore, r.calcolo.dettaglio) == ("nessuno", None)
    assert r.calcolo.in_attesa
    assert r.nota_uscita is not None


def test_bot_rientrato_nessuna_nota():
    r = _riga(_guild(1, left_at=datetime(2026, 5, 3, tzinfo=U), rejoined_at=datetime(2026, 6, 19, tzinfo=U)))
    assert r.nota_uscita is None
    assert r.osserva.dettaglio == "4 settimane"


# --- 4. l'icona --------------------------------------------------------------------


@pytest.mark.parametrize("hash_icona", [STATICA, ANIMATA])
def test_l_url_dell_icona(hash_icona):
    # Statica anche per gli hash animati: .webp senza ?animated=true.
    assert elenco.url_icona(GUILD_TODAY, hash_icona) == (
        f"https://cdn.discordapp.com/icons/{GUILD_TODAY}/{hash_icona}.webp?size=128"
    )


@pytest.mark.parametrize(
    "gid, hash_icona",
    [
        ("900000000000000001", STATICA),  # una stringa, non un intero
        (True, STATICA),
        (0, STATICA),
        (-5, STATICA),
        (GUILD_TODAY, STATICA.upper()),
        (GUILD_TODAY, "../" + STATICA),
        (GUILD_TODAY, STATICA + "\n"),
        (GUILD_TODAY, ""),
    ],
)
def test_l_url_non_si_compone_con_niente_altro(gid, hash_icona):
    with pytest.raises(ValueError):
        elenco.url_icona(gid, hash_icona)


def test_il_cdn_e_l_unica_origine_che_la_csp_ammette_per_le_immagini():
    direttive = dict(d.split(" ", 1) for d in CSP.split("; ") if " " in d)
    assert direttive["img-src"].split() == ["'self'", elenco.CDN_DISCORD]


def test_la_dimensione_e_documentata():
    # Discord documenta solo potenze di due fra 16 e 4096, e vogliamo almeno il 2x.
    lato = elenco.DIMENSIONE_ICONA
    assert 16 <= lato <= 4096 and lato & (lato - 1) == 0
    assert lato >= 2 * elenco.LATO_ICONA_PX


# --- 5. la pagina ------------------------------------------------------------------


class _Righe(HTMLParser):
    """Per ogni <li class="server">: il testo del link, gli <img>, i link."""

    def __init__(self):
        super().__init__()
        self.righe: list[dict] = []
        self._in_riga = False
        self._in_link = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and a.get("class") == "server":
            self.righe.append({"link": "", "hrefs": [], "img": []})
            self._in_riga = True
        elif not self._in_riga:
            return
        elif tag == "a":
            self.righe[-1]["hrefs"].append(a["href"])
            self._in_link = True
        elif tag == "img":
            self.righe[-1]["img"].append(a)

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_link = False
        elif tag == "li":
            self._in_riga = False

    def handle_data(self, data):
        if self._in_link and self.righe:
            self.righe[-1]["link"] += data


def _righe(html: str) -> list[dict]:
    p = _Righe()
    p.feed(html)
    return p.righe


NOMI = {GUILD_TODAY: "Fucina dei Corvi", GUILD_MATURE: "Élite del Nord", GUILD_EDGE: "l'Ordine"}


def _pagina(guilds, *, nomi=None, icone=None, transport=None) -> str:
    with TestClient(app_di_test(transport)) as client:
        entra(client, dati_di_sessione(guilds, nomi=nomi, icone=icone))
        risposta = client.get("/")
    assert risposta.status_code == 200
    return risposta.text


def test_il_nome_accessibile_del_link_e_il_solo_nome_del_server(orologio_fermo):
    html = _pagina([GUILD_TODAY, GUILD_MATURE, GUILD_EDGE], nomi=NOMI)

    righe = _righe(html)
    # Un link per riga, e il suo testo e' il nome e basta: niente date, niente ID.
    assert [r["link"] for r in righe] == ["Élite del Nord", "Fucina dei Corvi", "l'Ordine"]
    assert all(len(r["hrefs"]) == 1 for r in righe)


def test_l_icona_valida_diventa_un_img_e_l_assente_no(orologio_fermo):
    html = _pagina(
        [GUILD_TODAY, GUILD_MATURE], nomi={GUILD_TODAY: "Fucina", GUILD_MATURE: "Borgo"},
        icone={GUILD_TODAY: ANIMATA},
    )

    borgo, fucina = _righe(html)
    assert borgo["img"] == []
    (img,) = fucina["img"]
    assert img == {
        "src": f"https://cdn.discordapp.com/icons/{GUILD_TODAY}/{ANIMATA}.webp?size=128",
        "alt": "",
        "width": "40",
        "height": "40",
        "loading": "lazy",
        "decoding": "async",
        "referrerpolicy": "no-referrer",
    }
    # Il monogramma c'e' sempre, anche sotto l'immagine.
    assert html.count('class="monogramma"') == 2


def test_con_un_server_solo_la_pagina_e_l_elenco_non_un_redirect(orologio_fermo):
    with TestClient(app_di_test(), follow_redirects=False) as client:
        entra(client, dati_di_sessione([GUILD_TODAY], nomi={GUILD_TODAY: "Fucina"}))
        risposta = client.get("/")

    assert risposta.status_code == 200
    assert '<ul class="server-elenco">' in risposta.text
    assert len(_righe(risposta.text)) == 1


def test_gli_scenari_del_fixture_nei_loro_stati(orologio_fermo):
    html = _pagina([GUILD_LEFT, GUILD_NEW, GUILD_EDGE])

    # Una nota sola: l'uscito. Il rientrato (...003) non ne ha.
    assert html.count('class="server__avviso"') == 1
    assert "Il bot non è più nel server dal 12 settembre: i dati si fermano a quel giorno." in html
    assert "fino al 12 set" in html
    assert "non ancora <small>arriva con il primo calcolo settimanale</small>" in html
    assert 'class="fatto fatto--attesa"' in html


def test_l_elenco_vuoto_ha_il_suo_riquadro(orologio_fermo):
    # La guardia nega una sessione senza server: qui la sessione ne ha uno che
    # l'API non osserva (123), e l'elenco filtrato resta vuoto.
    html = _pagina([123])

    assert "Nessun server da mostrare" in html
    assert "server-elenco" not in html
    assert "Manca un server?" not in html
    assert f"entro {elenco.MINUTI_RICONTROLLO} minuti" in html


def test_i_minuti_vengono_dal_ttl_del_ricontrollo(orologio_fermo):
    assert elenco.MINUTI_RICONTROLLO * 60 == auth.TTL_RICONTROLLO_SECONDI == 15 * 60
    html = _pagina([GUILD_TODAY])
    assert f"L'elenco si aggiorna da solo ogni {elenco.MINUTI_RICONTROLLO} minuti." in html


def test_i_testi_della_pagina(orologio_fermo):
    html = _pagina([GUILD_TODAY])
    assert "<title>I tuoi server — Kindling</title>" in html
    assert "<h1>I tuoi server</h1>" in html
    assert (
        "Qui compaiono i server di cui sei amministratore e che Kindling osserva. "
        "Scegline uno per vedere cosa sta succedendo fra i suoi membri." in html
    )


def test_l_elenco_costa_una_chiamata_all_api(orologio_fermo):
    chiamate: list[str] = []
    asgi = httpx.ASGITransport(app=fixture_app)

    class Contatore(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            chiamate.append(request.url.path)
            return await asgi.handle_async_request(request)

    _pagina([GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_LEFT, GUILD_NEW], transport=Contatore())

    assert chiamate == ["/guilds"]


def test_nessuno_stato_di_salute_per_server(orologio_fermo):
    # Niente pillole di lettura e niente etichette di Stato nell'elenco: in
    # confronto fra server sarebbero un punteggio sintetico (invariante 3).
    html = _pagina([GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_LEFT, GUILD_NEW])
    for etichetta in stato.ETICHETTE_STATO.values():
        assert etichetta not in html, etichetta
    assert "pillola" not in html
