"""Vista Stato: dai dati dell'API ai fatti che un amministratore legge.

Tutto cio' che la vista DECIDE sta qui, in funzioni pure; il template decide solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4, "La
vista Stato, in dettaglio".

Tre cose che questo modulo fa e nessun altro modulo di vista fa:

- **Le date sono giorni, in forma breve**: sul calendario di Roma per l'arrivo
  e l'uscita del bot, sulla settimana ISO in UTC per ``as_of`` (``giorno`` e
  ``settimana``, qui sotto; le usano anche Elenco, Coorti, Robustezza e
  Community). ``main.data_ora``, completa e in UTC, resta ai soli Dettagli
  tecnici. I fusi sono FISSI e non quello del browser — la dashboard si rende sul
  server e dal client non riceve niente (dashboard.md §1 e §4, "Quale fuso per
  cosa").
- **L'orologio passa da ``adesso()``**, una funzione sola. Tre dei quattro
  numeri di questa pagina ("in osservazione da N giorni", "aggiornati a",
  "prossimo aggiornamento") dipendono da che giorno e' oggi: senza un punto solo
  da spostare, nessun test potrebbe guardarli, e un test che non li guarda e'
  peggio di nessun test (CLAUDE.md §7).
- **Lo stato delle tre viste si ricava dai dati della vista**, e da nient'altro:
  per Community e Coorti due campi tipizzati (``suppressed``, ``significant``)
  sulle righe dell'ultimo snapshot; per Robustezza, dal 30/09/2026, gli stati di
  riga della vista stessa (``robustezza.lettura``: ``n_effective`` contro la
  soglia dei ``params``). Nessuna soglia nuova, nessun conteggio di settimane,
  nessuna euristica — un giudizio che i dati non sostengono sarebbe un punteggio
  sintetico travestito, che stato-progetto.md §3 vieta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from api.models import (
    CohortGroup,
    CommunityRow,
    GuildRow,
    Quality,
    RobustnessRow,
    RunRow,
)
from . import coorti as _coorti
from . import domande as _domande
from . import regole as _regole
from . import robustezza as _robustezza

# Il fuso della community osservata. Scritto qui e non configurabile: finche' i
# server osservati sono italiani, una configurazione sarebbe una leva che
# nessuno tocca e che nessun test esercita. Il giorno in cui serve, e' questa
# riga a cambiare.
ROMA = ZoneInfo("Europe/Rome")

_MESI_BREVI = (
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
)
# I nomi estesi, per le date che sono il testo stesso di una frase ("leggibile
# dal calcolo di lunedi' 5 ottobre") e non l'etichetta di un punto su un asse.
# Li usa anche main.data_ora: una copia sola.
GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MESI = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)


def adesso() -> datetime:
    """L'orologio della vista, in un punto solo: i test lo spostano da qui."""
    return datetime.now(timezone.utc)


# --- date ---------------------------------------------------------------------


#
# I fusi a video sono tre, e dashboard.md 4 ("Quale fuso per cosa") dice quali e
# perche'. Qui ne vivono due, ciascuno in UNA funzione:
#
# - ``giorno``: il giorno di Roma, per gli istanti della community mostrati come
#   giorni (arrivo e uscita del bot) e per "oggi";
# - ``settimana``: il giorno UTC, per ``as_of`` e per cio' che se ne ricava. Il
#   job ancora ``as_of`` al lunedi' 00:00 UTC (modello-grafo.md 5.1): e'
#   l'etichetta di una settimana ISO in UTC, non un istante da convertire.
#
# Il terzo, UTC completo con l'orario, e' ``main.data_ora``, solo nei Dettagli
# tecnici.
#
# ``data_breve`` e ``data_estesa`` accettano solo un ``date``, non un
# ``datetime``: la scelta del fuso la fa chi chiama, passando per una delle due
# funzioni. Fino al 27/09/2026 convertivano da se' in Roma qualunque datetime, e
# un ``as_of`` passato cosi' finiva sul calendario sbagliato senza nessun errore.


def con_fuso(valore: datetime) -> datetime:
    """Il datetime com'e', se porta il fuso; altrimenti un errore.

    Fino al 27/09/2026 un datetime nudo si trattava come UTC, "per i modelli
    costruiti a mano nei test". Era una tolleranza che non diceva niente: lo
    schema e' tutto TIMESTAMPTZ e l'API rifiuta gli istanti senza fuso
    (``api.models.Istante``), quindi un datetime nudo qui e' un difetto a monte,
    e indovinarne il fuso lo nasconde.
    """
    if valore.tzinfo is None or valore.utcoffset() is None:
        raise ValueError(f"datetime senza fuso: {valore!r}")
    return valore


def giorno(valore: datetime) -> date:
    """Il giorno di calendario a Roma di un istante."""
    return con_fuso(valore).astimezone(ROMA).date()


def settimana(as_of: datetime) -> date:
    """Il giorno UTC di ``as_of``: il lunedi' della settimana ISO che etichetta.

    Anche per le date che si ricavano da ``as_of`` (il prossimo calcolo, il
    calcolo che rende leggibile una coorte). Oggi il lunedi' 00:00 UTC cade di
    lunedi' anche a Roma, e ``giorno`` darebbe lo stesso risultato: e' proprio
    per questo che la differenza non si vedrebbe il giorno in cui smettesse di
    essere vera.
    """
    return con_fuso(as_of).astimezone(timezone.utc).date()


def _solo_giorno(valore: Any) -> date:
    if isinstance(valore, datetime) or not isinstance(valore, date):
        raise TypeError(
            f"serve un date, non {type(valore).__name__}: il fuso lo sceglie chi "
            "chiama, con giorno() o settimana()"
        )
    return valore


# L'anno si scrive quando la data non e' nello stesso anno del ``riferimento``,
# e il riferimento e' OBBLIGATORIO: nelle viste e' il giorno (``settimana``)
# dell'``as_of`` piu' recente della pagina, e dove la pagina non ne ha nessuno,
# oggi; nell'elenco dei server e' oggi. Fino al 27/09/2026 le viste non
# scrivevano mai l'anno, "tanto mostrano dodici settimane": vero per Coorti, non
# per le serie di Robustezza e Community, che risalgono al primo snapshot — e a
# cavallo di capodanno "lunedì 28 dicembre" e "lunedì 4 gennaio" si sarebbero
# messi in ordine solo a intuito. Obbligatorio perche' un default ("senza anno")
# sarebbe proprio la strada per cui una vista nuova se ne dimentica.


def _con_anno(testo: str, g: date, riferimento: date) -> str:
    return testo if g.year == riferimento.year else f"{testo} {g.year}"


def data_breve(valore: Optional[date], *, riferimento: date) -> str:
    """``21 set``, o ``28 dic 2026`` fuori dall'anno del ``riferimento``.

    Nomi dei mesi scritti qui e non presi dal locale: il container gira con il
    locale C, e un rendering che cambia con la macchina e' una differenza fra
    sviluppo e produzione che nessuno cerca (come ``data_ora``)."""
    if valore is None:
        return "—"
    g = _solo_giorno(valore)
    return _con_anno(f"{g.day} {_MESI_BREVI[g.month - 1]}", g, riferimento)


def data_estesa(
    valore: Optional[date], *, riferimento: date, con_giorno: bool = False
) -> str:
    """``21 settembre``, o ``lunedì 21 settembre`` con ``con_giorno``; l'anno
    alle stesse condizioni di ``data_breve``."""
    if valore is None:
        return "—"
    g = _solo_giorno(valore)
    testo = f"{g.day} {MESI[g.month - 1]}"
    testo = f"{GIORNI[g.weekday()]} {testo}" if con_giorno else testo
    return _con_anno(testo, g, riferimento)


def con_preposizione(preposizione: str, testo_data: str) -> str:
    """``il 12 set``, ma ``l'8 set`` e ``l'11 set``; ``dal``/``dall'``,
    ``al``/``all'``, ``del``/``dell'``.

    Davanti a «otto» e «undici» l'articolo si elide, come si dice; davanti a
    «uno» no («il 1°», letto «il primo»). ``preposizione`` e' la forma davanti a
    consonante, anche maiuscola a inizio frase (``Dal`` -> ``Dall'``).

    Stava in elenco.py fino al 28/09/2026, e Stato scriveva "previsto il 11
    gen": una regola in un modulo solo e' una regola che gli altri non sanno di
    dover seguire. Una data con il giorno della settimana davanti ("di lunedì
    5 ottobre") non passa di qui: la preposizione cade sul nome del giorno.
    """
    if testo_data.split(" ", 1)[0] not in ("8", "11"):
        return f"{preposizione} {testo_data}"
    if preposizione == "il":
        return f"l'{testo_data}"
    if preposizione == "Il":
        return f"L'{testo_data}"
    return f"{preposizione}l'{testo_data}"


def relativo(quando: date, oggi: date) -> str:
    """"oggi", "ieri", "domani", "tra N giorni", "N giorni fa".

    Giorni di CALENDARIO a Roma, non multipli di 24 ore: chi legge alle 9 del
    mattino di martedi' un dato di lunedi' alle 23 si aspetta "ieri", non "oggi".
    """
    differenza = (quando - oggi).days
    if differenza == 0:
        return "oggi"
    if differenza == 1:
        return "domani"
    if differenza == -1:
        return "ieri"
    if differenza > 1:
        return f"tra {differenza} giorni"
    return f"{-differenza} giorni fa"


# --- la cadenza, osservata e non dichiarata ----------------------------------


def cadenza_osservata(runs: Sequence[RunRow]) -> Optional[timedelta]:
    """La distanza tipica fra due ``as_of`` consecutivi, o ``None``.

    **Osservata, non dichiarata**, ed e' la differenza che conta. Fino al
    22/09/2026 qui c'era ``default_window_days`` importato da ``job/config.py``:
    se il cron passasse a quindicinale, la pagina avrebbe continuato a dire sette
    giorni e nessun test sarebbe fallito — un meccanismo che sembra funzionare e
    non dice che ha smesso (CLAUDE.md §7). La cadenza vera non e' leggibile da
    nessuna parte (sta in /etc/cron.d/kindling), ma cio' che e' SUCCESSO si', ed
    e' nelle run che la pagina ha gia' in mano.

    **La mediana, non l'ultimo intervallo.** Lo storico contiene gia' un ``as_of``
    delle 04:15 invece che di mezzanotte — le run scritte prima dell'ancoraggio
    al lunedi' (``modello-grafo.md`` §5.1) — e un solo intervallo anomalo in
    mezzo a intervalli regolari sposterebbe la previsione di ore o di giorni.
    Sulle distanze, la mediana ignora l'anomalia; la media no. **Ma non con due
    intervalli soli**, dove la mediana E' la media: in produzione, il 27/09/2026,
    6,82 e 7 giorni davano 6,9114, e la previsione cadeva la domenica sera. Per
    questo nessuna previsione usa questo valore cosi' com'e': passa da
    ``cadenza_in_giorni``, che lo arrotonda.

    **Con una run sola la cadenza non esiste**, e la funzione torna ``None``: un
    intervallo si misura fra due punti. Il ripiego qui sarebbe di nuovo un numero
    inventato con l'aria di essere misurato.
    """
    ordinate = sorted({r.as_of for r in runs}, reverse=True)
    intervalli = [
        (recente - precedente).total_seconds()
        for recente, precedente in zip(ordinate, ordinate[1:])
    ]
    if not intervalli:
        return None
    return timedelta(seconds=median(intervalli))


# --- il prossimo calcolo: un punto solo, per Stato e Coorti -------------------
#
# Fino al 28/09/2026 Stato e Coorti sommavano all'as_of la cadenza osservata
# cosi' com'era, 6,9114 giorni in produzione: la previsione cadeva domenica
# 27/09 alle 21:53 UTC, Stato diceva "in ritardo" dalla mezzanotte di Roma e
# Coorti prometteva letture "dal calcolo di domenica". Due errori insieme: una
# cadenza non intera, e "in ritardo" deciso sul giorno, senza contare che il job
# gira ore dopo l'as_of che scrive.


def cadenza_in_giorni(runs: Sequence[RunRow]) -> Optional[int]:
    """La cadenza osservata arrotondata a giorni interi (mezzo giorno in su), o
    ``None`` se non c'e'. Mai meno di un giorno.

    Giorni interi perche' gli ``as_of`` sono confini di giorno (il lunedi' 00:00
    UTC): una cadenza con le ore dentro porta la previsione fuori dal confine, e
    l'etichetta sul giorno sbagliato.
    """
    cadenza = cadenza_osservata(runs)
    if cadenza is None:
        return None
    return max(1, math.floor(cadenza / timedelta(days=1) + 0.5))


def as_of_previsto(as_of: datetime, cadenza_giorni: int, passi: int = 1) -> datetime:
    """L'``as_of`` che il calcolo scrivera' fra ``passi`` cadenze."""
    return as_of + timedelta(days=cadenza_giorni * passi)


# L'ora a cui il job parte, come distanza dall'as_of che scrive: ops/kindling.cron
# lo lancia il lunedi' alle 04:15 e l'as_of e' il lunedi' 00:00 (UTC entrambi,
# runbook-droplet.md, "Il fuso del cron e' quello della droplet").
#
# Dichiarata, non osservata, al contrario della cadenza — e per una ragione
# misurata: l'alternativa era la mediana di created_at - as_of sulle run, ma
# created_at si riscrive a ogni rilancio (job/db.py, ON CONFLICT ... created_at =
# now()). In produzione le run 11 e 12 portano il 15/09 09:52, il giorno del
# ricalcolo delle metriche, e la mediana verrebbe circa un giorno e mezzo: un
# job fermo sembrerebbe puntuale fino al martedi' pomeriggio, senza nessun
# segnale. Un orario dichiarato che diverge dal cron sbaglia invece nel verso
# rumoroso — "in ritardo" troppo presto — e non puo' divergere in silenzio:
# tests/test_orari.py lo confronta con la riga di ops/kindling.cron.
ORARIO_DEL_JOB = timedelta(hours=4, minutes=15)
# Quanto si aspetta oltre l'orario del job prima di dire "in ritardo". Il job
# dura minuti (le durate sono in metric_runs.stats); quattro ore coprono un avvio
# lento, un lock di flock ancora preso, un rilancio a mano la mattina stessa, e
# fanno scattare il ritardo lunedi' alle 08:15 UTC — le 10:15 a Roma d'estate —
# non il giorno dopo.
MARGINE_DEL_JOB = timedelta(hours=4)


def in_ritardo(atteso: datetime, ora: datetime) -> bool:
    """Il calcolo con ``as_of`` = ``atteso`` avrebbe dovuto esserci, a ``ora``?"""
    return con_fuso(ora) > con_fuso(atteso) + ORARIO_DEL_JOB + MARGINE_DEL_JOB


# --- i tre fatti in cima ------------------------------------------------------


@dataclass(frozen=True)
class Fatto:
    etichetta: str
    valore: str
    dettaglio: str


# --- il calendario ------------------------------------------------------------


# --- il diradamento delle etichette del calendario ---------------------------
#
# Misure prese nel browser il 25/09/2026, non scelte a occhio (dashboard.md 4,
# "Il calendario"). Stanno qui e non dentro la soglia perche' il test possa
# rifare il conto invece di fidarsi del 15.
#
# ETICHETTA_PIU_LARGA_PX: "30 mag" a 12px/600/tabular-nums, la piu' larga che
# questo formato di data possa produrre — provate tutte e dodici le abbreviazioni
# di mese, le altre stanno fra 33,0 e 38,5.
ETICHETTA_PIU_LARGA_PX = 40.8
# La stessa misura con l'anno, "30 mag 2026", presa il 27/09/2026 nello stesso
# modo (e "30 mag" ridava 40,8). Le cifre sono tabulari, quindi l'anno pesa uguale
# per ogni anno; con gli altri undici mesi si sta fra 62,4 e 67,9.
ETICHETTA_CON_ANNO_PX = 70.7
# Lo spazio fra due etichette vicine perche' si leggano come due. Non un margine
# di sicurezza sulla misura: quello e' l'arrotondamento della soglia.
SPAZIO_FRA_ETICHETTE_PX = 8.0
# La larghezza dell'SVG a 34rem di viewport: 544 - 56, cioe' il respiro del
# contenitore (1.25rem per lato) piu' il margine della figura (0.5rem per lato).
#
# E' l'ultima larghezza a cui le date dei calcoli sono ancora SPENTE — la media
# query e' `max-width: 34rem`, che comprende i 544 — quindi quando compaiono
# l'SVG e' largo almeno un pixel di piu'. Tarare la soglia su questa misura e'
# dal lato prudente di un pixel, non sul filo.
LARGHEZZA_SVG_AL_BREAKPOINT_PX = 488.0

# Scarto minimo fra due date scritte, in percentuale dell'arco del calendario.
#
# Due etichette CENTRATE non si toccano a (40,8 + 8) / 488 = 10,0%. Una non tocca
# un ESTREMO — che e' ancorato al bordo, quindi sporge di una larghezza intera
# invece che di mezza — a (1,5 x 40,8 + 8) / 488 = 14,2%. Una soglia sola per
# entrambi i casi, arrotondata per eccesso.
#
# Una media query da sola non basterebbe, ed e' il motivo per cui questo numero
# sta qui e non nel foglio: la collisione dipende dalla larghezza E dalla
# spaziatura dei pallini, e il CSS la spaziatura non la conosce.
SCARTO_MINIMO_ETICHETTE = 15.0
# Lo stesso conto con l'anno: (1,5 x 70,7 + 8) / 488 = 23,4%, per eccesso 24.
# Vale per TUTTO il calendario appena una delle date sopra l'asse porta l'anno:
# una soglia per coppia di etichette sarebbe piu' fine, ma due soglie sulla
# stessa linea sono una regola che nessuno rilegge.
SCARTO_MINIMO_ETICHETTE_CON_ANNO = 24.0


@dataclass(frozen=True)
class Punto:
    """Un calcolo sulla linea del tempo. ``x`` e' una percentuale pronta da
    scrivere come ATTRIBUTO SVG (``cx="37,9%"`` no: il punto decimale, non la
    virgola — e' una coordinata, non un numero da leggere).

    ``etichettata`` dice se questo calcolo porta la propria data scritta, o solo
    il pallino con il ``titolo`` (che il browser mostra al passaggio del mouse).
    Fino al 25/09/2026 nessuno la portava mai, a nessuna larghezza: erano tre
    punti muti su una pagina di produzione, cioe' l'informazione per cui il
    calendario esiste.
    """

    x: str
    titolo: str
    data: str
    ancoraggio: str
    etichettata: bool


@dataclass(frozen=True)
class Pietra:
    """Una tappa con etichetta: arrivo, oggi, prossimo calcolo.

    ``ancoraggio`` e' il ``text-anchor`` dell'SVG, calcolato qui e non nel
    template: al bordo destro un'etichetta centrata esce dal riquadro, e la
    condizione dipende dalla posizione, che e' un dato.
    """

    x: str
    ancoraggio: str
    data: str
    testo: str


@dataclass(frozen=True)
class Calendario:
    x_oggi: str
    punti: tuple[Punto, ...]
    arrivo: Pietra
    oggi: Pietra
    prossimo: Optional[Pietra]
    descrizione: str


def _percento(quando: date, inizio: date, fine: date) -> str:
    """La posizione di un giorno sulla linea, in percentuale del suo arco.

    Una cifra decimale: su una linea larga 992px sono 10px di risoluzione, e su
    una larga 280px meno di 3. Il punto decimale e non la virgola — e' una
    coordinata SVG, che la virgola renderebbe invalida.
    """
    arco = (fine - inizio).days
    if arco <= 0:
        return "0%"
    quota = (quando - inizio).days / arco
    return f"{max(0.0, min(1.0, quota)) * 100:.1f}%"


def etichette_da_scrivere(
    posizioni: Sequence[float],
    *,
    con_prossimo: bool,
    scarto: float = SCARTO_MINIMO_ETICHETTE,
) -> list[bool]:
    """Quali calcoli portano la propria data, date le loro posizioni in percento.

    Un calcolo la porta se dista almeno ``scarto`` — ``SCARTO_MINIMO_ETICHETTE``,
    o ``SCARTO_MINIMO_ETICHETTE_CON_ANNO`` quando le date portano l'anno:

    - dall'arrivo del bot, che e' sempre allo 0% e sempre etichettato;
    - dalla data gia' scritta del calcolo precedente — non dal calcolo
      precedente: saltarne uno non consuma lo scarto, altrimenti su una serie
      fitta non si scriverebbe piu' niente dopo il primo;
    - dal prossimo calcolo, che e' al 100%, quando c'e'.

    **"Oggi" non entra nel conto**, e non e' una dimenticanza: sta sulla riga
    sotto l'asse, dove non puo' toccare niente di questa riga. E' sotto proprio
    perche' cade dove cade — in produzione a quattro giorni dall'ultimo calcolo —
    e nessuna regola di diradamento potrebbe separarla, visto che non e' una
    tappa che si possa togliere.
    """
    ultima = 0.0  # l'arrivo del bot
    scritte: list[bool] = []
    for p in posizioni:
        abbastanza_dopo = p - ultima >= scarto
        abbastanza_prima = not con_prossimo or 100.0 - p >= scarto
        scrivi = abbastanza_dopo and abbastanza_prima
        scritte.append(scrivi)
        if scrivi:
            ultima = p
    return scritte


def _ancoraggio(x: str) -> str:
    quota = float(x.rstrip("%"))
    if quota <= 15.0:
        return "start"
    if quota >= 85.0:
        return "end"
    return "middle"


def _calendario(
    arrivo: date,
    calcoli: Sequence[date],
    oggi: date,
    prossimo: Optional[date],
    *,
    riferimento: date,
) -> Optional[Calendario]:
    """La linea del tempo, o None quando non c'e' niente da disegnare.

    Niente da disegnare vuol dire nessun calcolo: la linea avrebbe due estremi e
    nessun contenuto, e una pagina con un grafico vuoto dice meno di una pagina
    senza (dashboard.md §4: se un dato non c'e', la riga non compare).
    """
    if not calcoli:
        return None
    # Un "prossimo" gia' passato non e' un prossimo: il cron non ha girato, e
    # disegnarlo darebbe una tappa futura a sinistra di "oggi" e un tratteggio
    # lungo zero. Il ritardo si legge nel fatto in cima, che lo dice a parole.
    if prossimo is not None and prossimo <= oggi:
        prossimo = None
    fine = max([oggi, *calcoli] + ([prossimo] if prossimo else []))
    inizio = min([arrivo, *calcoli])

    def breve(g: date) -> str:
        return data_breve(g, riferimento=riferimento)

    x_oggi = _percento(oggi, inizio, fine)
    ordinati = sorted(calcoli)
    ascisse = [_percento(c, inizio, fine) for c in ordinati]
    # "Oggi" non entra: sta sotto l'asse e non tocca le date di sopra (vedi
    # etichette_da_scrivere).
    sopra = [arrivo, *calcoli] + ([prossimo] if prossimo else [])
    con_anno = any(g.year != riferimento.year for g in sopra)
    scritte = etichette_da_scrivere(
        [float(x.rstrip("%")) for x in ascisse],
        con_prossimo=prossimo is not None,
        scarto=SCARTO_MINIMO_ETICHETTE_CON_ANNO if con_anno else SCARTO_MINIMO_ETICHETTE,
    )
    punti = tuple(
        Punto(
            x,
            f"calcolo {con_preposizione('del', breve(c))}",
            breve(c),
            _ancoraggio(x),
            scrivi,
        )
        for c, x, scrivi in zip(ordinati, ascisse, scritte)
    )
    pietra_arrivo = Pietra(_percento(inizio, inizio, fine), "start", breve(arrivo),
                           "arrivo del bot")
    pietra_oggi = Pietra(x_oggi, _ancoraggio(x_oggi), breve(oggi), "oggi")
    pietra_prossimo = (
        Pietra(_percento(prossimo, inizio, fine), "end", breve(prossimo), "prossimo")
        if prossimo
        else None
    )

    descrizione = (
        f"Linea del tempo: arrivo del bot {con_preposizione('il', breve(arrivo))}, "
        f"{_regole.plurale(len(punti), 'calcolo', 'calcoli')} "
        f"fino {con_preposizione('al', breve(max(calcoli)))}, oggi {breve(oggi)}"
    )
    if prossimo:
        descrizione += f", prossimo calcolo previsto {con_preposizione('il', breve(prossimo))}"
    return Calendario(x_oggi, punti, pietra_arrivo, pietra_oggi, pietra_prossimo,
                      descrizione + ".")


# --- cosa puoi leggere oggi ---------------------------------------------------

IN_RACCOLTA = "in_raccolta"
SOTTO_SOGLIA = "sotto_soglia"
CON_CAUTELA = "con_cautela"
LEGGIBILE = "leggibile"

ETICHETTE_STATO = {
    IN_RACCOLTA: "in raccolta",
    SOTTO_SOGLIA: "sotto la soglia",
    CON_CAUTELA: "con cautela",
    LEGGIBILE: "leggibile",
}

FRASI_STATO = {
    IN_RACCOLTA: "Il calcolo non c'è ancora: i numeri arrivano con la prima esecuzione settimanale.",
    SOTTO_SOGLIA: "I numeri esistono, ma riguardano troppe poche persone perché mostrarli sia prudente.",
    # Solo cio' che il flag dice (30/09/2026). Era "nessuno di essi e' ancora
    # distinguibile dal caso": una causa ricavata da significant is False, che di
    # cause ne ha piu' d'una — voice il 28/09, z 2,56 e 11 persone, e' distinguibile
    # dal caso ed e' non leggibile per le persone (dashboard.md 4).
    CON_CAUTELA: "I numeri ci sono, ma nessuno è ancora leggibile.",
    LEGGIBILE: "Almeno una misura è distinguibile dal caso: si può leggere.",
}


@dataclass(frozen=True)
class Lettura:
    nome: str
    percorso: str
    domanda: str
    stato: str
    etichetta: str
    frase: str
    ancora: str
    rimando: str


def stato_da_qualita(qualita: Sequence[Quality]) -> str:
    """Uno dei quattro stati, da ``suppressed`` e ``significant`` e basta.

    L'ordine delle condizioni e' quello di §5: prima "non c'e' niente", poi "c'e'
    e non si mostra", poi "si mostra e regge", poi il resto. ``significant is
    True`` e non ``if q.significant``: il campo e' ``Optional[bool]`` e None
    significa "non valutata" (riga soppressa), che non e' un no.
    """
    if not qualita:
        return IN_RACCOLTA
    if all(q.suppressed for q in qualita):
        return SOTTO_SOGLIA
    if any(q.significant is True for q in qualita):
        return LEGGIBILE
    return CON_CAUTELA


def _ultimo_snapshot(righe: Sequence[Any]) -> list[Any]:
    """Le righe dello snapshot piu' recente, per ``(as_of, snapshot_id)``.

    Il tiebreaker su ``snapshot_id`` non e' decorazione: due snapshot possono
    pareggiare su ``as_of`` (CLAUDE.md §7, il caso di ``api/db.py``), e senza di
    lui la vista direbbe "leggibile" o "con cautela" a seconda dell'ordine in cui
    il database ha restituito le righe.
    """
    if not righe:
        return []
    massimo = max((r.as_of, r.snapshot_id) for r in righe)
    return [r for r in righe if (r.as_of, r.snapshot_id) == massimo]


def _qualita_coorti(gruppi: Sequence[CohortGroup], ancora: datetime) -> list[Quality]:
    """Le righe che la vista Coorti mostra, e solo quelle: onboarding e retention.

    **Le stesse coorti della vista**, prese da ``coorti.gruppi_visibili`` e non da
    una copia della condizione: posteriori all'arrivo del bot, al piu' dodici.
    Fino al 26/09/2026 qui contavano tutte le coorti dello snapshot, anteriori
    comprese — diciassette su venticinque in produzione — e la pillola poteva dire
    "con cautela" di una vista che mostrava solo righe sotto la soglia.

    Tutte le righe di un gruppo contano: sono cinque righe per coorte con
    ``quality`` propri, e leggerne una sola sarebbe scegliere quale delle due
    domande della vista rappresenta lo stato dell'altra (dashboard.md §4, "Coorti
    non si divide"). Il massimo per ``(as_of, snapshot_id)`` lo sceglie
    ``coorti.costruisci``, con lo stesso ordinamento di ``_ultimo_snapshot``.
    """
    return [
        r.quality
        for g in _coorti.gruppi_visibili(gruppi, ancora)
        for r in (*g.onboarding.values(), *g.retention.values())
    ]


def _da_qualita(qualita: Sequence[Quality]) -> tuple[str, str]:
    stato = stato_da_qualita(qualita)
    return stato, FRASI_STATO[stato]


def _letture(
    guild_id: int,
    ancora: datetime,
    runs: Sequence[RunRow],
    robustness: Sequence[RobustnessRow],
    communities: Sequence[CommunityRow],
    cohorts: Sequence[CohortGroup],
) -> tuple[Lettura, ...]:
    # Robustezza, dal 30/09/2026, dalla stessa funzione degli stati di riga della
    # vista (robustezza.lettura), con le sue frasi: la vista decide da n_effective
    # contro la soglia dei params, non da ``significant``, e sopra soglia una
    # cella con baseline degenere e' leggibile e non significativa. Con
    # stato_da_qualita le due pagine avrebbero detto cose diverse — lo stesso
    # difetto trovato il 26/09 con Coorti (dashboard.md 4).
    #
    # Per Community si guarda il ``quality`` della riga di layer e non quello dei
    # ``sizes[]``: la soppressione di un bucket e' secondaria (api/models.py), e
    # un bucket soppresso accanto a una riga pubblicata non e' "la vista non si
    # legge".
    lettura_robustezza = _robustezza.lettura(robustness, runs)
    definizioni = (
        (
            "Robustezza",
            "robustezza",
            # La domanda della vista, con le stesse parole (dashboard.md 4).
            "Se le poche persone che tengono insieme il server smettessero di "
            "esserci, gli altri resterebbero in contatto fra loro?",
            (lettura_robustezza.stato, lettura_robustezza.frase),
            "q-robustezza-leggibile",
        ),
        (
            "Community",
            "community",
            "Quali gruppi si formano da soli, e se restano gli stessi di settimana in settimana.",
            _da_qualita([r.quality for r in _ultimo_snapshot(communities)]),
            "q-leggibile",
        ),
        (
            "Coorti",
            "coorti",
            "Chi entra nello stesso periodo: si lega agli altri, e resta?",
            _da_qualita(_qualita_coorti(cohorts, ancora)),
            "q-segnate",
        ),
    )
    letture = []
    for nome, percorso, domanda, (stato, frase), ancora in definizioni:
        letture.append(
            Lettura(
                nome=nome,
                percorso=f"/guilds/{guild_id}/{percorso}",
                domanda=domanda,
                stato=stato,
                etichetta=ETICHETTE_STATO[stato],
                frase=frase,
                ancora=f"/guilds/{guild_id}/domande#{ancora}",
                # Il testo del rimando E' il testo della domanda, preso dalla
                # mappa che la pagina Domande usa per renderla: tre link che
                # dicono tutti "Perche'?" sono tre nomi accessibili identici, e
                # un testo ricopiato qui direbbe una cosa e atterrerebbe su
                # un'altra al primo ritocco (CLAUDE.md 7).
                rimando=_domande.TITOLI[ancora],
            )
        )
    return tuple(letture)


# --- avvisi -------------------------------------------------------------------


@dataclass(frozen=True)
class Avviso:
    codice: str
    testo: str


def cambio_di_parametri(runs: Sequence[RunRow]) -> Optional[datetime]:
    """L'``as_of`` della run piu' recente con parametri diversi dalla precedente.

    Piu' recente e non la piu' antica, e la differenza conta: la frase
    dell'avviso promette un confine oltre il quale i numeri sono confrontabili, e
    con due cambi di parametri solo l'ultimo lo e'. Dire il primo darebbe un
    confine piu' generoso del vero, cioe' esattamente l'errore che l'avviso esiste
    per evitare.

    L'ordine si rifa' qui invece di fidarsi di quello dell'API: ``runs`` arriva
    gia' dal piu' recente, ma un confronto "con la precedente" su una sequenza
    ordinata per caso confronterebbe due run qualunque.
    """
    ordinate = sorted(runs, key=lambda r: (r.as_of, r.snapshot_id), reverse=True)
    for recente, precedente in zip(ordinate, ordinate[1:]):
        if recente.params != precedente.params:
            return recente.as_of
    return None


def _avvisi(
    guild: GuildRow, runs: Sequence[RunRow], *, riferimento: date
) -> tuple[Avviso, ...]:
    def breve(g: date) -> str:
        return data_breve(g, riferimento=riferimento)

    avvisi: list[Avviso] = []
    if guild.left_at is not None and guild.rejoined_at is not None:
        avvisi.append(
            Avviso(
                "buco",
                f"{con_preposizione('Dal', breve(giorno(guild.left_at)))} "
                f"{con_preposizione('al', breve(giorno(guild.rejoined_at)))} il bot "
                "non era sul server: chi se n'è andato in quei giorni non ha lasciato traccia.",
            )
        )
    elif guild.left_at is not None:
        avvisi.append(
            Avviso(
                "uscito",
                f"Il bot ha lasciato il server {con_preposizione('il', breve(giorno(guild.left_at)))} e non è "
                "rientrato: da quella data non c'è osservazione.",
            )
        )
    elif guild.rejoined_at is not None:
        avvisi.append(
            Avviso(
                "rientro",
                f"Risulta un rientro del bot {con_preposizione('il', breve(giorno(guild.rejoined_at)))} senza una data "
                "di uscita: non si sa per quanto tempo il bot sia stato assente.",
            )
        )
    cambio = cambio_di_parametri(runs)
    if cambio is not None:
        avvisi.append(
            Avviso(
                "parametri",
                f"Metodo di calcolo aggiornato {con_preposizione('il', breve(settimana(cambio)))}: confronta i numeri "
                "solo da quella data.",
            )
        )
    return tuple(avvisi)


# --- la vista -----------------------------------------------------------------


@dataclass(frozen=True)
class Vista:
    fatti: tuple[Fatto, ...]
    senza_run: bool
    calendario: Optional[Calendario]
    letture: tuple[Lettura, ...]
    regole: tuple[_regole.Regola, ...]
    avvisi: tuple[Avviso, ...]


def costruisci(
    guild: GuildRow,
    runs: Sequence[RunRow],
    robustness: Sequence[RobustnessRow],
    communities: Sequence[CommunityRow],
    cohorts: Sequence[CohortGroup],
    *,
    ora: Optional[datetime] = None,
) -> Vista:
    """Tutto quello che la vista Stato mostra, dai dati dell'API."""
    oggi = giorno(ora or adesso())
    inizio = giorno(guild.first_seen_at)

    # ``runs`` arriva dal piu' recente, ma la vista non si fida dell'ordine: la
    # run di riferimento e' quella con l'``as_of`` piu' alto, a parita' quella con
    # lo snapshot piu' alto (lo stesso tiebreaker di _ultimo_snapshot).
    ultima = max(runs, key=lambda r: (r.as_of, r.snapshot_id)) if runs else None
    # L'anno delle date della pagina si confronta con l'as_of piu' recente, con
    # oggi se non ce n'e' nessuno (data_breve).
    riferimento = settimana(ultima.as_of) if ultima is not None else oggi

    def breve(g: date) -> str:
        return data_breve(g, riferimento=riferimento)

    fatti = [
        Fatto(
            "In osservazione da",
            _giorni((oggi - inizio).days),
            con_preposizione("dal", breve(inizio)),
        )
    ]

    cadenza = cadenza_in_giorni(runs)
    prossimo: Optional[date] = None
    if ultima is not None:
        calcolo = settimana(ultima.as_of)
        fatti.append(
            Fatto("Dati aggiornati a", relativo(calcolo, oggi), breve(calcolo))
        )
    # Il fatto esiste solo se una cadenza c'e': con una run sola non si sa ogni
    # quanto il calcolo si ripeta, e il riquadro non compare invece di mostrare
    # una data ricavata da un numero che nessuno ha misurato.
    #
    # La previsione e il ritardo vengono da as_of_previsto e in_ritardo, le
    # stesse che usa Coorti: giorni interi di cadenza, e il ritardo misurato
    # sull'istante, dopo l'orario del job piu' il margine — non sul giorno.
    if ultima is not None and cadenza is not None:
        atteso = as_of_previsto(ultima.as_of, cadenza)
        prossimo = settimana(atteso)
        ritardo = in_ritardo(atteso, ora or adesso())
        # "in ritardo" e non "ieri": sotto l'etichetta "Prossimo aggiornamento"
        # una forma relativa al passato si legge come un errore di rendering,
        # mentre il fatto da riferire e' che il calcolo atteso non e' arrivato.
        fatti.append(
            Fatto(
                "Prossimo aggiornamento",
                "in ritardo" if ritardo else relativo(prossimo, oggi),
                f"era previsto {con_preposizione('il', breve(prossimo))}"
                if ritardo
                else f"previsto {con_preposizione('il', breve(prossimo))}",
            )
        )

    return Vista(
        fatti=tuple(fatti),
        senza_run=ultima is None,
        calendario=_calendario(
            inizio, [settimana(r.as_of) for r in runs], oggi, prossimo,
            riferimento=riferimento,
        ),
        letture=_letture(guild.guild_id, guild.first_seen_at, runs, robustness, communities, cohorts),
        regole=_regole.costruisci(
            ultima.params if ultima is not None else None,
            ultima.graph_params if ultima is not None else None,
        ),
        avvisi=_avvisi(guild, runs, riferimento=riferimento),
    )


def _giorni(quanti: int) -> str:
    """``1 giorno`` / ``12 giorni``. Anche zero: il bot arrivato stamattina
    osserva da "oggi", non da "0 giorni"."""
    if quanti <= 0:
        return "oggi"
    return f"{quanti} giorno" if quanti == 1 else f"{quanti} giorni"


def parametri_ultima_run(runs: Sequence[RunRow]) -> Optional[RunRow]:
    """La run di riferimento, con lo stesso criterio di ``costruisci``.

    Esiste perche' la pagina dei Dettagli tecnici deve mostrare i parametri
    della STESSA run da cui Stato ricava le regole: due criteri diversi per "la
    piu' recente" sono due criteri che un giorno scelgono due run diverse, e la
    divergenza non darebbe nessun errore.
    """
    return max(runs, key=lambda r: (r.as_of, r.snapshot_id)) if runs else None


def storico(runs: Sequence[RunRow]) -> list[RunRow]:
    """Le run dalla piu' recente. Stesso ordinamento del resto del modulo."""
    return sorted(runs, key=lambda r: (r.as_of, r.snapshot_id), reverse=True)
