"""La pillola di Robustezza in Stato dice quello che dice la vista (30/09/2026).

Fino al 30/09/2026 Stato decideva da ``quality.significant`` sull'ultimo snapshot
(``stato.stato_da_qualita``), mentre la vista riscritta decide da ``n_effective``
contro ``min_nodes_structural``. Le due cose coincidono solo finche' nessuna
cella sopra soglia ha la baseline degenere — che sul job e' frequente sui grafi
densi (dashboard.md 4). E' lo stesso difetto trovato il 26/09 fra la pillola e la
vista Coorti: due pagine che leggono le stesse righe con due criteri, e dicono
cose diverse senza nessun errore (CLAUDE.md 7).

Questi test sono stati eseguiti contro lo ``stato.py`` di prima, e falliscono
li'. Il confronto con la vista si fa sul MARKUP delle due pagine, non chiamando
``robustezza.lettura``: un test che chiamasse la stessa funzione in due posti non
potrebbe mai vederle divergere.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Optional

import httpx
import pytest

from api.models import GuildRow, RobustnessRow, RunRow
from dashboard import config, stato
from dashboard.main import cornice, crea_app, crea_templates
from job.config import MetricParams
from tests.sessione_dashboard import OAUTH_DI_TEST, client_autenticato
from tests.test_dashboard_robustezza import _albero
from tools.fixture_api import SCENARIOS, _robustness_layer, graph_params
from tools.fixture_api import app as fixture_app

PARAMS = MetricParams().as_run_params()
GUILD = GuildRow(guild_id=5, first_seen_at=datetime(2026, 8, 28, tzinfo=timezone.utc))
ORA = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)

# Dalla vista alla pillola: e' la corrispondenza scritta in dashboard.md 4.
DALLA_VISTA = {"leggibile": "leggibile", "con_cautela": "con_cautela"}


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def dashboard():
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture_app), base_url="http://api.test")
    with client_autenticato(crea_app(api_http=http, oauth=OAUTH_DI_TEST)) as client:
        yield client


def _lunedi(i: int) -> datetime:
    return datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(weeks=i)


def _runs(righe: list[RobustnessRow], params: Optional[dict] = None) -> list[RunRow]:
    visti = {r.snapshot_id: r.as_of for r in righe}
    return [
        RunRow(snapshot_id=sid, as_of=as_of, params=PARAMS if params is None else params,
               graph_params=graph_params(), stats={}, code_version=None, created_at=as_of)
        for sid, as_of in visti.items()
    ]


def _lettura(righe: list[RobustnessRow], runs: Optional[list[RunRow]] = None):
    vista = stato.costruisci(GUILD, _runs(righe) if runs is None else runs, righe, [], [], ora=ORA)
    (robustezza,) = [l for l in vista.letture if l.nome == "Robustezza"]
    return robustezza


def _layer(sid, as_of, layer, n, **kw) -> list[RobustnessRow]:
    return _robustness_layer(sid, as_of, layer, n=n,
                             excess=kw.pop("excess", {0.05: 0.1, 0.10: 0.2, 0.20: 0.3}), **kw)


# --- i casi in cui i due criteri divergono ------------------------------------


def test_sopra_soglia_con_baseline_degenere_la_pillola_dice_leggibile():
    """Tutte le celle sopra 21 nodi e non significative per degenerate_baseline:
    la vista le mostra con barra e tacca, e la pillola non puo' dire "con
    cautela". Con lo stato.py di prima diceva "con cautela"."""
    righe = _layer(1, _lunedi(0), "reply", 30, degenerate=frozenset({0.05, 0.10, 0.20}))
    assert all(r.quality.significant is False for r in righe)
    assert _lettura(righe).stato == stato.LEGGIBILE


def test_la_soglia_e_quella_dei_params_non_la_significativita():
    """Righe significative con una soglia di run piu' alta del loro n: la vista le
    mostra in osservazione, e la pillola non dice "leggibile"."""
    righe = _layer(1, _lunedi(0), "reply", 25)
    assert all(r.quality.significant is True for r in righe)
    assert _lettura(righe, _runs(righe, {**PARAMS, "min_nodes_structural": 30})).stato == stato.CON_CAUTELA


def test_la_frase_e_della_vista_e_non_parla_di_caso():
    """"distinguibile dal caso" sarebbe falso qui: una cella leggibile puo' essere
    non significativa, e la significativita' non guarda targeted_z."""
    for righe in (
        _layer(1, _lunedi(0), "reply", 30),
        _layer(1, _lunedi(0), "reply", 12),
    ):
        frase = _lettura(righe).frase
        assert "caso" not in frase
        assert "Nell'ultima settimana" in frase


def test_con_cautela_non_contraddice_le_settimane_leggibili_prima():
    """Decisione 11: in produzione dopo il ricalcolo del 30/09 l'ultima settimana
    non si legge e le precedenti si'. La pillola dice "con cautela", e lo dice."""
    righe = _layer(11, _lunedi(0), "reply", 24) + _layer(12, _lunedi(1), "reply", 14)
    lettura = _lettura(righe)
    assert lettura.stato == stato.CON_CAUTELA
    assert lettura.frase.endswith("Le settimane precedenti si leggono.")


def test_il_rimando_e_la_domanda_della_riga_sono_quelli_della_vista():
    lettura = _lettura(_layer(1, _lunedi(0), "reply", 30))
    assert lettura.ancora.endswith("#q-robustezza-leggibile")
    assert lettura.rimando == "Quando sarà leggibile Robustezza?"
    assert lettura.domanda == (
        "Se le poche persone che tengono insieme il server smettessero di esserci, "
        "gli altri resterebbero in contatto fra loro?"
    )


def test_in_raccolta_e_sotto_la_soglia_restano_quelli_di_sempre():
    assert _lettura([]).stato == stato.IN_RACCOLTA
    soppresse = _layer(1, _lunedi(0), "reply", 3) + _layer(1, _lunedi(0), "voice", 2)
    assert _lettura(soppresse).stato == stato.SOTTO_SOGLIA


# --- la pillola e la vista, pagina contro pagina -----------------------------


def _stati_della_vista(html: str) -> list[str]:
    tabella = next(_albero(html).radice.trova("table", classe="robustezza-lettura"))
    return [tr.attrs["data-stato"] for tr in tabella.trova("tr", classe="robustezza-riga")]


def _attesa(stati: list[str]) -> str:
    """La corrispondenza di dashboard.md 4, dagli stati di riga della vista."""
    presenti = [s for s in stati if s != "nessuna"]
    if presenti and all(s == "sotto_soglia" for s in presenti):
        return "sotto_soglia"
    return "leggibile" if "leggibile" in stati else "con_cautela"


def _pillola(html: str) -> str:
    return dict(re.findall(
        r'<h3><a href="[^"]*">(\w+)</a></h3>\s*<span class="pillola pillola--(\w+)"', html
    ))["Robustezza"]


@pytest.mark.parametrize("guild_id", [g for g in SCENARIOS if SCENARIOS[g]["robustness"]])
def test_la_pillola_dice_quello_che_mostra_la_vista(dashboard, guild_id):
    vista = dashboard.get(f"/guilds/{guild_id}/robustezza").text
    pagina_stato = dashboard.get(f"/guilds/{guild_id}").text
    assert _pillola(pagina_stato) == _attesa(_stati_della_vista(vista))


def test_la_pillola_e_la_vista_sul_caso_che_divergeva():
    """Lo stesso confronto, sul caso costruito in cui i due criteri si separano,
    rendendo le due pagine dai template veri."""
    righe = (
        _layer(1, _lunedi(0), "reply", 30, degenerate=frozenset({0.05, 0.10, 0.20}))
        + _layer(1, _lunedi(0), "voice", 12)
    )
    runs = _runs(righe)
    from dashboard import robustezza

    modelli = crea_templates()
    vista = modelli.get_template("robustezza.html").render(**cornice(
        guild_id=5, vista=robustezza.pagina(righe, runs), vista_corrente="robustezza"))
    pagina_stato = modelli.get_template("stato.html").render(**cornice(
        guild_id=5, vista=stato.costruisci(GUILD, runs, righe, [], [], ora=ORA),
        vista_corrente="stato"))
    assert _stati_della_vista(vista) == ["in_osservazione", "leggibile", "nessuna", "nessuna"]
    assert _pillola(pagina_stato) == "leggibile"
    assert "caso" not in unescape(pagina_stato.split("pillola--leggibile", 1)[1].split("</p>", 1)[0])
