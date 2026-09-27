"""Date e istanti: dove si decide il fuso, e cosa succede a un datetime senza.

Quattro cose, tutte della classe di difetto di CLAUDE.md 7 — un meccanismo che
continua a funzionare mentre smette di dire il vero:

1. **L'API non lascia uscire un istante senza fuso.** Ogni campo temporale di
   ``api/models.py`` e' un ``Istante``: un datetime nudo e' un errore di
   validazione, e uno con un offset diverso da UTC esce convertito, con ``Z``. Il
   test non elenca i campi a mano: li trova, cosi' un campo nuovo dichiarato
   ``datetime`` lo fa fallire invece di sfuggirgli.
2. **La dashboard non indovina un fuso.** Fino al 27/09/2026 ``main.data_ora``,
   ``stato.giorno`` e ``coorti._giorno_utc`` trattavano un datetime nudo come
   UTC. Ora e' un errore, e le funzioni che scrivono una data (``data_breve``,
   ``data_estesa``, ``elenco.data``, ``data_numerica``) accettano solo un giorno
   gia' scelto da chi chiama.
3. **Nessun template formatta una data da se'**: niente ``strftime``, tutto
   passa dai filtri di ``dashboard/main.py``.
4. **L'orario con "UTC" sta solo nei Dettagli tecnici** (dashboard.md 4, "Quale
   fuso per cosa"): le viste mostrano giorni.
"""

from __future__ import annotations

import inspect
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

import api.models as modelli
from dashboard import main, stato
from dashboard.main import TEMPLATES_DIR
from tests.sessione_dashboard import (
    app_di_test,
    client_autenticato,
    senza_variabili_di_database,
)
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_LEFT,
    GUILD_MATURE,
    GUILD_NEW,
    GUILD_SCALE,
    GUILD_TODAY,
)
from tools.fixture_api import app as fixture_app

DASHBOARD_DIR = Path(main.__file__).parent
TUTTE_LE_GUILD = (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_SCALE, GUILD_LEFT, GUILD_NEW)

NUDO = datetime(2026, 9, 21, 0, 0)
ROMA_ESTATE = timezone(timedelta(hours=2))


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    senza_variabili_di_database(monkeypatch)


# --- 1. l'API ------------------------------------------------------------------


def _classi(tipo: Any) -> set[type]:
    """Le classi dentro un'annotazione: ``Optional[Annotated[X, ...]]`` -> {X, NoneType}."""
    if isinstance(tipo, type):
        return {tipo}
    return set().union(*(_classi(a) for a in get_args(tipo))) if get_args(tipo) else set()


def _campi_temporali() -> list[tuple[str, str, Any, str]]:
    """Ogni campo di ogni modello di ``api/models.py`` che contiene una data."""
    campi = []
    for nome, classe in inspect.getmembers(modelli, inspect.isclass):
        if not issubclass(classe, BaseModel) or classe.__module__ != modelli.__name__:
            continue
        for campo, info in classe.model_fields.items():
            tipo = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
            classi = _classi(info.annotation)
            # datetime e' una sottoclasse di date: si guarda prima lui. I tipi di
            # pydantic (AwareDatetime, NaiveDatetime...) non ereditano da
            # datetime a runtime, e si riconoscono dal nome.
            if any(issubclass(k, datetime) or k.__name__.endswith("Datetime") for k in classi):
                campi.append((nome, campo, tipo, "istante"))
            elif any(issubclass(k, date) for k in classi):
                campi.append((nome, campo, tipo, "data"))
    return campi


CAMPI = _campi_temporali()
ISTANTI = [c for c in CAMPI if c[3] == "istante"]
DATE = [c for c in CAMPI if c[3] == "data"]


def test_i_campi_temporali_si_trovano():
    """Se l'introspezione smettesse di trovarli, i test qui sotto passerebbero su
    un elenco vuoto: il numero minimo e' quello del 27/09/2026."""
    assert len(ISTANTI) >= 10
    assert {(m, c) for m, c, _, _ in DATE} >= {("CohortGroup", "cohort_start")}


@pytest.mark.parametrize("modello,campo,tipo,_", ISTANTI, ids=[f"{m}.{c}" for m, c, _, _ in ISTANTI])
def test_un_istante_senza_fuso_non_arriva_alla_serializzazione(modello, campo, tipo, _):
    with pytest.raises(ValidationError):
        TypeAdapter(tipo).validate_python(NUDO)
    # E nemmeno dal JSON: e' la strada del client della dashboard.
    with pytest.raises(ValidationError):
        TypeAdapter(tipo).validate_json('"2026-09-21T00:00:00"')


@pytest.mark.parametrize("modello,campo,tipo,_", ISTANTI, ids=[f"{m}.{c}" for m, c, _, _ in ISTANTI])
def test_un_istante_esce_in_utc_con_la_z(modello, campo, tipo, _):
    adattatore = TypeAdapter(tipo)
    # Le 02:00 di Roma d'estate sono la mezzanotte UTC: stesso istante, e deve
    # uscire nella forma UTC, non con l'offset con cui e' entrato.
    valore = adattatore.validate_python(datetime(2026, 9, 21, 2, 0, tzinfo=ROMA_ESTATE))
    assert adattatore.dump_json(valore) == b'"2026-09-21T00:00:00Z"'


@pytest.mark.parametrize("modello,campo,tipo,_", DATE, ids=[f"{m}.{c}" for m, c, _, _ in DATE])
def test_una_data_esce_come_anno_mese_giorno(modello, campo, tipo, _):
    adattatore = TypeAdapter(tipo)
    assert adattatore.dump_json(adattatore.validate_python(date(2026, 9, 7))) == b'"2026-09-07"'


def test_un_modello_intero_rifiuta_l_istante_nudo():
    """Non solo il tipo: il modello costruito come lo costruisce api/assemble.py."""
    with pytest.raises(ValidationError):
        modelli.GuildRow(guild_id=1, first_seen_at=NUDO)


_ISTANTE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Diagnostica, non contratto (api/models.py, Quality): il loro contenuto lo
# decide il job, e api.md lo dice.
_CHIAVI_DIAGNOSTICHE = {"details", "stats", "params"}


def _stringhe_temporali(valore: Any, percorso: str = ""):
    if isinstance(valore, dict):
        for k, v in valore.items():
            if k not in _CHIAVI_DIAGNOSTICHE:
                yield from _stringhe_temporali(v, f"{percorso}.{k}")
    elif isinstance(valore, list):
        for i, v in enumerate(valore):
            yield from _stringhe_temporali(v, f"{percorso}[{i}]")
    elif isinstance(valore, str) and (_ISTANTE_ISO.match(valore) or _DATA_ISO.match(valore)):
        yield percorso, valore


def test_ogni_istante_delle_risposte_finisce_con_la_z():
    """Le risposte vere, endpoint per endpoint, su tutte le guild del fixture."""
    visti = 0
    with TestClient(fixture_app) as c:
        percorsi = ["/guilds"] + [
            f"/guilds/{g}{coda}"
            for g in TUTTE_LE_GUILD
            for coda in ("", "/runs", "/robustness", "/communities", "/cohorts")
        ]
        for p in percorsi:
            r = c.get(p)
            assert r.status_code == 200, p
            for dove, testo in _stringhe_temporali(r.json(), p):
                visti += 1
                assert _DATA_ISO.match(testo) or testo.endswith("Z"), (dove, testo)
    assert visti > 100


# --- 2. la dashboard non indovina un fuso ---------------------------------------


@pytest.mark.parametrize("funzione", [stato.giorno, stato.settimana, main.data_ora, main.settimana])
def test_un_datetime_senza_fuso_e_un_errore(funzione):
    with pytest.raises(ValueError, match="senza fuso"):
        funzione(NUDO)


@pytest.mark.parametrize(
    "funzione",
    [stato.data_breve, stato.data_estesa, main.data_numerica],
)
def test_chi_scrive_una_data_vuole_un_giorno_non_un_istante(funzione):
    with pytest.raises(TypeError):
        funzione(datetime(2026, 9, 21, tzinfo=timezone.utc))


def test_giorno_e_settimana_sono_due_fusi_diversi():
    """Le 23:30 UTC di lunedi' sono martedi' a Roma: ``giorno`` dice martedi'
    (il calendario di chi amministra), ``settimana`` dice lunedi' (la settimana
    ISO in UTC di ``as_of``). Se una delle due passasse per l'altra, qui si vede."""
    istante = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
    assert stato.giorno(istante) == date(2026, 9, 22)
    assert stato.settimana(istante) == date(2026, 9, 21)


def test_as_of_del_lunedi_e_lunedi_prima_e_dopo_il_cambio_dell_ora():
    """Il 25/10/2026 Roma passa da UTC+2 a UTC+1. ``as_of`` e' il lunedi' 00:00
    UTC, e resta lunedi' in tutte e due le forme su entrambi i lati del cambio."""
    for as_of in (datetime(2026, 10, 19, tzinfo=timezone.utc), datetime(2026, 10, 26, tzinfo=timezone.utc)):
        assert stato.settimana(as_of).weekday() == 0
        assert stato.giorno(as_of) == stato.settimana(as_of)
    assert main.settimana(datetime(2026, 10, 26, tzinfo=timezone.utc)) == "lunedì 26 ottobre"


def test_i_fusi_vivono_in_due_file():
    """``ZoneInfo`` solo in stato.py, ``astimezone`` solo in stato.py e main.py,
    e nessun ``replace(tzinfo=...)`` in tutta la dashboard: e' la forma che aveva
    la tolleranza tolta il 27/09/2026, e sarebbe la forma del suo ritorno."""
    for sorgente in DASHBOARD_DIR.glob("*.py"):
        testo = sorgente.read_text(encoding="utf-8")
        assert "replace(tzinfo" not in testo, sorgente.name
        if sorgente.name != "stato.py":
            assert "ZoneInfo(" not in testo, sorgente.name
        if sorgente.name not in {"stato.py", "main.py"}:
            assert ".astimezone(" not in testo, sorgente.name


# --- 3. nessuna formattazione nei template ---------------------------------------


def test_nessun_template_formatta_una_data_da_se():
    for template in TEMPLATES_DIR.glob("*.html"):
        testo = template.read_text(encoding="utf-8")
        assert "strftime" not in testo, template.name
        assert "isoformat" not in testo, template.name


def test_data_ora_si_usa_solo_nei_dettagli_tecnici():
    for template in TEMPLATES_DIR.glob("*.html"):
        if "data_ora" in template.read_text(encoding="utf-8"):
            assert template.name == "dettagli_tecnici.html", template.name


# --- 4. "UTC" a video, solo nei Dettagli tecnici ---------------------------------


class _Testo(HTMLParser):
    """Il testo visibile: niente tag, niente attributi."""

    def __init__(self):
        super().__init__()
        self.parti: list[str] = []

    def handle_data(self, data):
        self.parti.append(data)


def _visibile(html: str) -> str:
    p = _Testo()
    p.feed(html)
    return " ".join(p.parti)


_ORARIO_UTC = re.compile(r"\d{1,2}:\d{2} UTC")


def test_utc_compare_solo_nei_dettagli_tecnici():
    with client_autenticato(app_di_test(), follow_redirects=False) as c:
        pagine = {"elenco": c.get("/")}
        for g in TUTTE_LE_GUILD:
            for vista in ("", "/robustezza", "/community", "/coorti", "/domande"):
                pagine[f"{g}{vista}"] = c.get(f"/guilds/{g}{vista}")
        dettagli = c.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici")

    for nome, risposta in pagine.items():
        assert risposta.status_code == 200, nome
        assert "UTC" not in _visibile(risposta.text), nome

    # E il verso opposto: se i Dettagli tecnici smettessero di mostrare l'orario
    # UTC, il test sopra passerebbe comunque.
    assert dettagli.status_code == 200
    assert _ORARIO_UTC.search(_visibile(dettagli.text))
