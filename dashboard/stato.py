"""Vista Stato: dai dati dell'API ai fatti che un amministratore legge.

Tutto cio' che la vista DECIDE sta qui, in funzioni pure; il template decide solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4, "La
vista Stato, in dettaglio".

Tre cose che questo modulo fa e nessun altro modulo di vista fa:

- **Le date sono in Europe/Rome e in forma breve.** E' l'unica pagina in cui il
  lettore e' una persona che vive in un fuso, non qualcuno che confronta una
  riga con un log. ``main.data_ora`` resta al suo posto per tutte le altre
  viste, e per i Dettagli tecnici: le due forme rispondono a due domande
  diverse. Il fuso e' FISSO e non quello del browser — la dashboard si rende sul
  server e dal client non riceve niente (dashboard.md §1).
- **L'orologio passa da ``adesso()``**, una funzione sola. Tre dei quattro
  numeri di questa pagina ("in osservazione da N giorni", "aggiornati a",
  "prossimo aggiornamento") dipendono da che giorno e' oggi: senza un punto solo
  da spostare, nessun test potrebbe guardarli, e un test che non li guarda e'
  peggio di nessun test (CLAUDE.md §7).
- **Lo stato delle tre viste si ricava da ``quality``**, e da nient'altro: due
  campi tipizzati (``suppressed``, ``significant``) sulle righe dell'ultimo
  snapshot. Nessuna soglia nuova, nessun conteggio di settimane, nessuna
  euristica — un giudizio che i dati non sostengono sarebbe un punteggio
  sintetico travestito, che stato-progetto.md §3 vieta.
"""

from __future__ import annotations

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
from . import domande as _domande
from . import regole as _regole

# Il fuso della community osservata. Scritto qui e non configurabile: finche' i
# server osservati sono italiani, una configurazione sarebbe una leva che
# nessuno tocca e che nessun test esercita. Il giorno in cui serve, e' questa
# riga a cambiare.
ROMA = ZoneInfo("Europe/Rome")

_MESI_BREVI = (
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
)


def adesso() -> datetime:
    """L'orologio della vista, in un punto solo: i test lo spostano da qui."""
    return datetime.now(timezone.utc)


# --- date ---------------------------------------------------------------------


def giorno(valore: datetime) -> date:
    """Il giorno di calendario a Roma.

    Un ``datetime`` senza fuso si tratta come UTC, come fa ``main.data_ora``:
    tutto lo schema e' TIMESTAMPTZ e l'API serializza con il fuso, ma un modello
    costruito a mano in un test puo' arrivare nudo.
    """
    if valore.tzinfo is None:
        valore = valore.replace(tzinfo=timezone.utc)
    return valore.astimezone(ROMA).date()


def data_breve(valore: Any) -> str:
    """``21 set``. Nomi dei mesi scritti qui e non presi dal locale: il container
    gira con il locale C, e un rendering che cambia con la macchina e' una
    differenza fra sviluppo e produzione che nessuno cerca (come ``data_ora``)."""
    if valore is None:
        return "—"
    g = valore if isinstance(valore, date) and not isinstance(valore, datetime) else giorno(valore)
    return f"{g.day} {_MESI_BREVI[g.month - 1]}"


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
    Sulle distanze, la mediana ignora l'anomalia; la media no.

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
    posizioni: Sequence[float], *, con_prossimo: bool
) -> list[bool]:
    """Quali calcoli portano la propria data, date le loro posizioni in percento.

    Un calcolo la porta se dista almeno ``SCARTO_MINIMO_ETICHETTE``:

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
        abbastanza_dopo = p - ultima >= SCARTO_MINIMO_ETICHETTE
        abbastanza_prima = not con_prossimo or 100.0 - p >= SCARTO_MINIMO_ETICHETTE
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
    arrivo: date, calcoli: Sequence[date], oggi: date, prossimo: Optional[date]
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

    x_oggi = _percento(oggi, inizio, fine)
    ordinati = sorted(calcoli)
    ascisse = [_percento(c, inizio, fine) for c in ordinati]
    scritte = etichette_da_scrivere(
        [float(x.rstrip("%")) for x in ascisse], con_prossimo=prossimo is not None
    )
    punti = tuple(
        Punto(
            x,
            f"calcolo del {data_breve(c)}",
            data_breve(c),
            _ancoraggio(x),
            scrivi,
        )
        for c, x, scrivi in zip(ordinati, ascisse, scritte)
    )
    pietra_arrivo = Pietra(_percento(inizio, inizio, fine), "start", data_breve(arrivo),
                           "arrivo del bot")
    pietra_oggi = Pietra(x_oggi, _ancoraggio(x_oggi), data_breve(oggi), "oggi")
    pietra_prossimo = (
        Pietra(_percento(prossimo, inizio, fine), "end", data_breve(prossimo), "prossimo")
        if prossimo
        else None
    )

    descrizione = (
        f"Linea del tempo: arrivo del bot il {data_breve(arrivo)}, "
        f"{_regole.plurale(len(punti), 'calcolo', 'calcoli')} "
        f"fino al {data_breve(max(calcoli))}, oggi {data_breve(oggi)}"
    )
    if prossimo:
        descrizione += f", prossimo calcolo previsto il {data_breve(prossimo)}"
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
    CON_CAUTELA: "I numeri ci sono, e nessuno di essi è ancora distinguibile dal caso.",
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


def _qualita_coorti(gruppi: Sequence[CohortGroup]) -> list[Quality]:
    """Tutte le righe di un gruppo di coorte contano: onboarding e retention.

    Sono cinque righe per coorte con ``quality`` propri, e leggerne una sola
    sarebbe scegliere quale delle due domande della vista rappresenta lo stato
    dell'altra (dashboard.md §4, "Coorti non si divide").
    """
    return [q for g in gruppi for r in (*g.onboarding, *g.retention) for q in (r.quality,)]


def _letture(
    guild_id: int,
    robustness: Sequence[RobustnessRow],
    communities: Sequence[CommunityRow],
    cohorts: Sequence[CohortGroup],
) -> tuple[Lettura, ...]:
    # Per Community si guarda il ``quality`` della riga di layer e non quello dei
    # ``sizes[]``: la soppressione di un bucket e' secondaria (api/models.py), e
    # un bucket soppresso accanto a una riga pubblicata non e' "la vista non si
    # legge".
    definizioni = (
        (
            "Robustezza",
            "robustezza",
            "Quanto la rete regge se alcune persone smettono di partecipare.",
            [r.quality for r in _ultimo_snapshot(robustness)],
            "q-mancanti",
        ),
        (
            "Community",
            "community",
            "Quali gruppi si formano da soli, e se restano gli stessi di settimana in settimana.",
            [r.quality for r in _ultimo_snapshot(communities)],
            "q-leggibile",
        ),
        (
            "Coorti",
            "coorti",
            "Chi entra nello stesso periodo: si lega agli altri, e resta?",
            _qualita_coorti(_ultimo_snapshot(cohorts)),
            "q-segnate",
        ),
    )
    letture = []
    for nome, percorso, domanda, qualita, ancora in definizioni:
        stato = stato_da_qualita(qualita)
        letture.append(
            Lettura(
                nome=nome,
                percorso=f"/guilds/{guild_id}/{percorso}",
                domanda=domanda,
                stato=stato,
                etichetta=ETICHETTE_STATO[stato],
                frase=FRASI_STATO[stato],
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


def _avvisi(guild: GuildRow, runs: Sequence[RunRow]) -> tuple[Avviso, ...]:
    avvisi: list[Avviso] = []
    if guild.left_at is not None and guild.rejoined_at is not None:
        avvisi.append(
            Avviso(
                "buco",
                f"Dal {data_breve(guild.left_at)} al {data_breve(guild.rejoined_at)} il bot "
                "non era sul server: chi se n'è andato in quei giorni non ha lasciato traccia.",
            )
        )
    elif guild.left_at is not None:
        avvisi.append(
            Avviso(
                "uscito",
                f"Il bot ha lasciato il server il {data_breve(guild.left_at)} e non è "
                "rientrato: da quella data non c'è osservazione.",
            )
        )
    elif guild.rejoined_at is not None:
        avvisi.append(
            Avviso(
                "rientro",
                f"Risulta un rientro del bot il {data_breve(guild.rejoined_at)} senza una data "
                "di uscita: non si sa per quanto tempo il bot sia stato assente.",
            )
        )
    cambio = cambio_di_parametri(runs)
    if cambio is not None:
        avvisi.append(
            Avviso(
                "parametri",
                f"Metodo di calcolo aggiornato il {data_breve(cambio)}: confronta i numeri "
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

    fatti = [
        Fatto(
            "In osservazione da",
            _giorni((oggi - inizio).days),
            f"dal {data_breve(inizio)}",
        )
    ]

    # ``runs`` arriva dal piu' recente, ma la vista non si fida dell'ordine: la
    # run di riferimento e' quella con l'``as_of`` piu' alto, a parita' quella con
    # lo snapshot piu' alto (lo stesso tiebreaker di _ultimo_snapshot).
    ultima = max(runs, key=lambda r: (r.as_of, r.snapshot_id)) if runs else None
    cadenza = cadenza_osservata(runs)
    prossimo: Optional[date] = None
    if ultima is not None:
        calcolo = giorno(ultima.as_of)
        fatti.append(
            Fatto("Dati aggiornati a", relativo(calcolo, oggi), data_breve(calcolo))
        )
    # Il fatto esiste solo se una cadenza c'e': con una run sola non si sa ogni
    # quanto il calcolo si ripeta, e il riquadro non compare invece di mostrare
    # una data ricavata da un numero che nessuno ha misurato.
    #
    # La somma si fa sull'ISTANTE e non sul giorno: il job ancora ``as_of`` al
    # lunedi' 00:00 UTC, ma lo storico contiene anche ``as_of`` delle 04:15, e
    # sommare giorni interi a quelli porterebbe la previsione a cadere sul giorno
    # sbagliato per quattro ore di scarto.
    if ultima is not None and cadenza is not None:
        prossimo = giorno(ultima.as_of + cadenza)
        # "in ritardo" e non "ieri": sotto l'etichetta "Prossimo aggiornamento"
        # una forma relativa al passato si legge come un errore di rendering,
        # mentre il fatto da riferire e' che il calcolo atteso non e' arrivato.
        fatti.append(
            Fatto(
                "Prossimo aggiornamento",
                relativo(prossimo, oggi) if prossimo >= oggi else "in ritardo",
                f"previsto il {data_breve(prossimo)}"
                if prossimo >= oggi
                else f"era previsto il {data_breve(prossimo)}",
            )
        )

    return Vista(
        fatti=tuple(fatti),
        senza_run=ultima is None,
        calendario=_calendario(
            inizio, [giorno(r.as_of) for r in runs], oggi, prossimo
        ),
        letture=_letture(guild.guild_id, robustness, communities, cohorts),
        regole=_regole.costruisci(
            ultima.params if ultima is not None else None,
            ultima.graph_params if ultima is not None else None,
        ),
        avvisi=_avvisi(guild, runs),
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
