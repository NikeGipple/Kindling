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
   fuso per cosa"): le viste mostrano giorni. L'unica altra pagina in cui la
   sigla compare e' Domande, dentro ``q-date`` e da nessun'altra parte.

Piu' una quinta, dal 27/09/2026: **l'anno compare quando serve**, cioe' quando
la data non e' nello stesso anno dell'``as_of`` piu' recente della pagina — e
si prova su una serie a cavallo di capodanno, l'unico caso in cui la sua
assenza fa leggere male.
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


CON_FUSO = datetime(2026, 9, 21, tzinfo=timezone.utc)
RIFERIMENTO = date(2026, 9, 21)


@pytest.mark.parametrize(
    "funzione",
    [
        stato.giorno,
        stato.settimana,
        main.data_ora,
        lambda v: main.settimana(v, CON_FUSO),
        lambda v: main.settimana(CON_FUSO, v),
    ],
    ids=["giorno", "settimana", "data_ora", "filtro settimana", "filtro settimana, piu' recente"],
)
def test_un_datetime_senza_fuso_e_un_errore(funzione):
    with pytest.raises(ValueError, match="senza fuso"):
        funzione(NUDO)


@pytest.mark.parametrize(
    "funzione",
    [
        lambda v: stato.data_breve(v, riferimento=RIFERIMENTO),
        lambda v: stato.data_estesa(v, riferimento=RIFERIMENTO),
        main.data_numerica,
    ],
    ids=["data_breve", "data_estesa", "data_numerica"],
)
def test_chi_scrive_una_data_vuole_un_giorno_non_un_istante(funzione):
    # match: il TypeError deve essere il nostro, non quello di un argomento
    # mancante — che farebbe passare il test per la ragione sbagliata.
    with pytest.raises(TypeError, match="serve un date"):
        funzione(CON_FUSO)


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
    lunedi = datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert main.settimana(lunedi, lunedi) == "lunedì 26 ottobre"


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


ECCEZIONE_DOMANDE = "q-date"


class _Testo(HTMLParser):
    """Il testo visibile, diviso in due: quello dentro l'elemento con
    ``id=q-date`` e tutto il resto. L'elemento si riconosce dall'ID, non dal
    testo: una frase riscritta non deve spostare l'eccezione."""

    def __init__(self):
        super().__init__()
        self.fuori: list[str] = []
        self.dentro: list[str] = []
        self._tag: str | None = None
        self._profondita = 0

    def handle_starttag(self, tag, attrs):
        if self._tag is None and dict(attrs).get("id") == ECCEZIONE_DOMANDE:
            self._tag, self._profondita = tag, 1
        elif tag == self._tag:
            self._profondita += 1

    def handle_endtag(self, tag):
        if tag == self._tag:
            self._profondita -= 1
            if self._profondita == 0:
                self._tag = None

    def handle_data(self, data):
        (self.dentro if self._tag else self.fuori).append(data)


def _testo(html: str) -> _Testo:
    p = _Testo()
    p.feed(html)
    return p


_ORARIO_UTC = re.compile(r"\d{1,2}:\d{2} UTC")


def test_utc_compare_solo_nei_dettagli_tecnici_e_in_q_date():
    with client_autenticato(app_di_test(), follow_redirects=False) as c:
        pagine = {"elenco": c.get("/")}
        for g in TUTTE_LE_GUILD:
            for vista in ("", "/robustezza", "/community", "/coorti", "/domande"):
                pagine[f"{g}{vista}"] = c.get(f"/guilds/{g}{vista}")
        dettagli = c.get(f"/guilds/{GUILD_TODAY}/dettagli-tecnici")

    for nome, risposta in pagine.items():
        assert risposta.status_code == 200, nome
        testo = _testo(risposta.text)
        assert "UTC" not in " ".join(testo.fuori), nome
        if nome.endswith("/domande"):
            # L'eccezione esiste davvero e spiega la sigla: se q-date sparisse
            # o smettesse di dire UTC, l'eccezione ammetterebbe il niente.
            assert "tempo universale (UTC)" in " ".join(testo.dentro), nome
        else:
            assert not testo.dentro, nome

    # E il verso opposto: se i Dettagli tecnici smettessero di mostrare l'orario
    # UTC, il test sopra passerebbe comunque.
    assert dettagli.status_code == 200
    assert _ORARIO_UTC.search(" ".join(_testo(dettagli.text).fuori))


def test_l_eccezione_si_trova_per_id_non_per_testo():
    """La stessa frase fuori da q-date conta come fuori; dentro, con un id
    qualunque diverso, pure."""
    fuori = _testo('<details id="q-altro"><p>in tempo universale (UTC)</p></details>')
    assert "UTC" in " ".join(fuori.fuori) and not fuori.dentro
    dentro = _testo(
        '<div><details id="q-date"><details><p>a</p></details><p>UTC</p></details>'
        "<p>dopo</p></div>"
    )
    assert " ".join(dentro.dentro) == "a UTC"
    assert " ".join(dentro.fuori) == "dopo"


# --- 5. l'anno, a cavallo di capodanno ------------------------------------------


U = timezone.utc
# Una serie settimanale che attraversa capodanno: l'as_of piu' recente e' nel
# 2027, gli altri nel 2026.
SERIE = [datetime(2026, 12, 14, tzinfo=U), datetime(2026, 12, 21, tzinfo=U),
         datetime(2026, 12, 28, tzinfo=U), datetime(2027, 1, 4, tzinfo=U)]


def test_l_anno_compare_solo_fuori_dall_anno_dell_as_of_piu_recente():
    piu_recente = max(SERIE)
    assert [main.settimana(a, piu_recente) for a in SERIE] == [
        "lunedì 14 dicembre 2026",
        "lunedì 21 dicembre 2026",
        "lunedì 28 dicembre 2026",
        "lunedì 4 gennaio",
    ]
    rif = date(2027, 1, 4)
    assert stato.data_breve(date(2026, 12, 28), riferimento=rif) == "28 dic 2026"
    assert stato.data_breve(date(2027, 1, 4), riferimento=rif) == "4 gen"
    assert stato.data_estesa(date(2026, 12, 28), riferimento=rif) == "28 dicembre 2026"


def test_la_serie_di_stato_a_cavallo_di_capodanno():
    """Stato, dall'inizio: fatti, calendario e soglia di diradamento."""
    guild = modelli.GuildRow(guild_id=5, first_seen_at=datetime(2026, 12, 9, 15, tzinfo=U))
    runs = [
        modelli.RunRow(snapshot_id=i, as_of=a, params={}, stats={})
        for i, a in enumerate(SERIE, start=1)
    ]
    vista = stato.costruisci(guild, runs, [], [], [], ora=datetime(2027, 1, 6, 10, tzinfo=U))

    fatti = {f.etichetta: f for f in vista.fatti}
    assert fatti["In osservazione da"].dettaglio == "dal 9 dic 2026"
    assert fatti["Dati aggiornati a"].dettaglio == "4 gen"
    # Solo la data: "il 11" invece di "l'11" e' un difetto a parte, gia' c'era.
    assert fatti["Prossimo aggiornamento"].dettaglio.endswith(" 11 gen")

    cal = vista.calendario
    assert cal.arrivo.data == "9 dic 2026"
    assert [p.data for p in cal.punti] == ["14 dic 2026", "21 dic 2026", "28 dic 2026", "4 gen"]
    assert cal.oggi.data == "6 gen"
    # Con l'anno sopra l'asse vale la soglia larga: nessuna coppia di date
    # scritte (arrivo compreso, allo 0%) sta piu' vicina di 24 punti.
    scritte = [0.0] + [float(p.x.rstrip("%")) for p in cal.punti if p.etichettata]
    if cal.prossimo:
        scritte.append(100.0)
    assert all(b - a >= stato.SCARTO_MINIMO_ETICHETTE_CON_ANNO
               for a, b in zip(scritte, scritte[1:])), scritte


def test_senza_date_di_un_altro_anno_la_soglia_resta_quella_stretta():
    """Il verso opposto: dentro un anno solo nessuna data porta l'anno, e il
    calendario non si dirada piu' del necessario."""
    serie = [datetime(2026, 9, 7, tzinfo=U) + timedelta(weeks=k) for k in range(4)]
    guild = modelli.GuildRow(guild_id=5, first_seen_at=datetime(2026, 9, 2, tzinfo=U))
    runs = [modelli.RunRow(snapshot_id=i, as_of=a, params={}, stats={})
            for i, a in enumerate(serie, start=1)]
    vista = stato.costruisci(guild, runs, [], [], [], ora=datetime(2026, 9, 30, tzinfo=U))
    assert all(not p.data.endswith("2026") for p in vista.calendario.punti)
    assert vista.calendario.arrivo.data == "2 set"
