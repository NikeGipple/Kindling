"""Coorti: dai gruppi dell'API alle righe da rendere, in due forme.

Tutto cio' che si DECIDE sta qui, in funzioni pure; i template decidono solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4 "La
vista Coorti, in dettaglio" e §5.

**Due forme, due lettori** (riscrittura del 26/09/2026):

- ``pagina()`` costruisce la VISTA, per chi amministra il server: solo le coorti
  posteriori all'arrivo del bot, le ultime ``COORTI_VISIBILI``, una riga per
  coorte in uno di quattro stati, e barre a fasce invece di numeri. E' la parte
  in fondo a questo modulo.
- ``costruisci()`` costruisce la TABELLA TECNICA, che fino al 26/09 era la vista
  e oggi sta nei Dettagli tecnici: tutte le coorti, tutti i numeri. Tutto quello
  che segue vale per lei.

- **Due domande, una popolazione condivisa.** *Con quanta rapidita' un nuovo
  membro si integra?* (l'onboarding, due ambiti) e *quanti restano?* (la
  retention, tre orizzonti) sono domande diverse, e l'incrocio fra le due e' il
  senso della metrica (``modello-metriche.md`` §5.5). Stanno nella stessa vista e
  nella stessa riga di gruppo, mai in due tabelle.
- **Un gruppo per coorte, non cinque righe indipendenti.** ``n_effective``,
  ``excluded_rejoins``, ``observation_days``, ``is_mature``,
  ``has_snapshot_coverage``, ``is_survivors_only`` e ``significant`` NON
  dipendono dal ``layer_scope`` ne' dall'orizzonte: ``job/metrics.py`` calcola
  ``cohort_members`` una volta sola e lo passa identico a tutte e cinque le
  chiamate. Sono quindi **colonne condivise**, scritte una volta per gruppo;
  divergono solo le uscite della curva di sopravvivenza e, per la retention,
  ``is_computable``/``not_computable_reason``.
- **Le etichette di riga si compongono** (regola 8): zero, una o due
  ("non significativo", "solo sopravvissuti"), mai scelte fra loro. Una riga
  soppressa non ne porta nessuna — ``suppress()`` azzera anche
  ``is_survivors_only``, quindi l'informazione a livello di dato non esiste.
- **Nessun grafico di serie**, e non per dimenticanza: ``limit=1`` porta un solo
  snapshot, e venticinque coorti indipendenti non sono poche grandezze
  comparabili su molti snapshot (§4, "Rotta e dati"). La tabella E' la vista.
- **``censored_by_leave`` non e' una colonna**: e' un sotto-conteggio di
  ``censored_count``, e si mostra come nota della cella.

``quality.details`` non si legge in questo modulo, per nessuna ragione (regola 2):
qui non servirebbe comunque, perche' i tre motivi di non significativita' sono
tutti colonne tipizzate (``is_mature``, ``has_snapshot_coverage``,
``is_survivors_only``) — l'unica vista in cui il follow-up di §5 e' gia' fatto,
dalla migration ``0007``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from api.models import (
    CohortGroup,
    GuildRow,
    OnboardingRow,
    OnboardingValues,
    RetentionRow,
    RunRow,
)
from job.config import LAYER_VOICE, SCOPE_ANY, MetricParams

from . import stato as _stato
from .qualifica import precisione_colonna
from .robustezza import Snapshot

_PARAMS = MetricParams()

# Gli ambiti e gli orizzonti del job, nell'ordine del job: due gruppi di colonne
# di integrazione e tre di retention nella tabella tecnica. Importati, non
# riscritti — un ambito aggiunto al job compare qui senza che nessuno se ne
# ricordi. Le SOGLIE (k, maturita') invece non si importano: si leggono dalla
# riga o dai params della run, che sono cio' con cui quei numeri sono stati
# calcolati, mentre job/config.py e' cio' con cui lo sarebbero oggi.
AMBITI = tuple(_PARAMS.cohort_layer_scopes)
ORIZZONTI = tuple(_PARAMS.retention_horizons_days)

NOMI_AMBITO = {"any": "qualunque interazione", "voice": "solo voce"}

# Le colonne numeriche della curva, per ambito. Nessuna sta in un gruppo di
# precisione aritmetica (dashboard.md 5, "Le colonne legate da un'operazione"):
# mediana, quartili e frazioni raggiunte sono uscite indipendenti della stessa
# curva di Kaplan-Meier, non l'una calcolata dall'altra con una formula che si
# legge in tabella. Stesso criterio gia' applicato a node_overlap e
# stability_jaccard in Community, stesso esito (nessun gruppo).
COLONNE_INTEGRAZIONE = (
    "event_count",
    "censored_count",
    "median_days_to_k",
    "p25_days_to_k",
    "p75_days_to_k",
    "reached_by_14d",
    "reached_by_28d",
)
COLONNA_RETENTION = "retained_fraction"

# I campi che le cinque righe di un gruppo condividono per costruzione. Sono
# anche l'elenco su cui si cercano le divergenze: vedi Gruppo.divergenze.
CONDIVISI_ONBOARDING = (
    "n_effective",
    "excluded_rejoins",
    "observation_days",
    "is_mature",
    "has_snapshot_coverage",
    "is_survivors_only",
    "significant",
)
CONDIVISI_RETENTION = ("n_effective", "excluded_rejoins", "is_survivors_only")


def maturita(riga: OnboardingRow, minimo: Optional[int] = None) -> str:
    """``is_mature`` con ``observation_days`` accanto, mai da solo.

    "Non matura" senza il numero di giorni non dice quanto manca, e la domanda
    che un admin si fa davanti a una coorte immatura e' esattamente quella
    (dashboard.md 4: per una coorte immatura la domanda e' "quando matura").
    Il numero e' in ``quality``, non in ``values``: ``cella()`` non lo rende, e
    non deve — non e' un valore di metrica.

    ``minimo`` e' ``min_observation_days`` dei params della run: senza, il "su
    14" non compare, invece di prenderlo da job/config.py — che direbbe la soglia
    di oggi accanto a un numero calcolato con quella di allora.
    """
    q = riga.quality
    if q.observation_days is None:
        return "—"
    giorni = "1 giorno" if q.observation_days == 1 else f"{q.observation_days} giorni"
    if q.is_mature:
        return f"sì · {giorni}"
    return f"no · {giorni} su {minimo}" if minimo is not None else f"no · {giorni}"


def esclusi(riga: OnboardingRow) -> str:
    """``excluded_rejoins``: quanti rientri sospetti sono usciti dal conteggio.

    Sta accanto a ``n`` perche' i due si leggono insieme: ``n`` e' chi e' stato
    contato, questo e' chi e' stato tolto. Un trattino dove il job non lo ha
    scritto, mai uno zero: "nessuno escluso" e "non lo sappiamo" sono cose
    diverse, ed e' la stessa distinzione fra zero e NULL che il layer di calcolo
    tiene ferma in tabella.
    """
    quanti = riga.quality.excluded_rejoins
    return "—" if quanti is None else str(quanti)


def copertura(riga: OnboardingRow) -> str:
    """``has_snapshot_coverage``: c'era un grafo sui primi giorni della coorte?

    Non ha un'etichetta di riga propria, e non deve averne una (§4): si legge da
    questa colonna, come ``maturita``. Un "no" qui e' la ragione per cui un
    ``raggiunta a 14g`` pari a zero sulla stessa riga misura l'assenza di
    osservazione e non l'assenza di integrazione.
    """
    if riga.quality.has_snapshot_coverage is None:
        return "—"
    return "sì" if riga.quality.has_snapshot_coverage else "no"


def censura(riga: OnboardingRow) -> Optional[str]:
    """La nota della cella ``censurati``: quanti dei censurati sono usciti.

    ``censored_by_leave`` NON e' una colonna a se' (§4): non e' un terzo esito
    accanto a eventi e censure, e' un dettaglio del secondo — in
    ``SurvivalCurve`` e' un sottoinsieme filtrato dello stesso insieme, non un
    conteggio indipendente. Come colonna propria suggerirebbe il contrario.
    ``None`` quando non c'e' niente da dire: zero uscite, o riga soppressa.
    """
    quante = riga.values.censored_by_leave
    if not quante:
        return None
    return f"di cui {quante} per uscita dal server"


@dataclass
class Gruppo:
    """Una coorte: una riga di qualificazione condivisa, due curve, tre orizzonti."""

    cohort_start: date
    onboarding: dict[str, OnboardingRow]
    retention: dict[int, RetentionRow]
    # La riga da cui si leggono le colonne condivise. E' una riga di onboarding
    # perche' is_mature/has_snapshot_coverage vivono solo li' (OnboardingQuality).
    guida: OnboardingRow

    @property
    def soppressa(self) -> bool:
        """La soppressione e' per coorte, non per riga: stessa soglia sullo stesso
        ``n_effective`` per tutte e cinque. Se una sola riga risultasse soppressa,
        ``divergenze`` lo direbbe; qui basta la guida."""
        return self.guida.quality.suppressed

    @property
    def divergenze(self) -> tuple[str, ...]:
        """I campi condivisi che NON coincidono fra le righe del gruppo.

        Il job non puo' produrne (stessa chiamata sugli stessi ``members``), e un
        test lo verifica su ogni coorte del fixture. Ma il contratto dell'API non
        lo vieta, e il template non deve *assumerlo*: se un giorno divergessero,
        questa vista lo dichiara invece di mostrare il valore di una riga sola
        spacciandolo per quello del gruppo. Ramo difensivo, nel codice e non nella
        fixture (dashboard.md 5, "I rami difensivi").
        """
        fuori: list[str] = []
        righe = list(self.onboarding.values())
        for campo in CONDIVISI_ONBOARDING:
            if len({getattr(r.quality, campo) for r in righe}) > 1:
                fuori.append(campo)
        tutte = [*righe, *self.retention.values()]
        if len({r.quality.suppressed for r in tutte}) > 1:
            fuori.append("suppressed")
        for campo in CONDIVISI_RETENTION:
            if len({getattr(r.quality, campo) for r in tutte}) > 1:
                fuori.append(campo)
        return tuple(dict.fromkeys(fuori))


@dataclass
class Vista:
    """La tabella di uno snapshot: un gruppo per coorte, dalla piu' recente."""

    snapshot: Optional[Snapshot]
    gruppi: list[Gruppo]
    # Decimali per (ambito, campo) e per orizzonte: la precisione si sceglie per
    # COLONNA, e le colonne di 'any' e di 'voice' sono colonne diverse della
    # stessa tabella — come i blocchi di Robustezza, uniformarle creerebbe una
    # colonna sola che attraversa i due ambiti (dashboard.md 5).
    decimali: dict[tuple[str, str], int] = field(default_factory=dict)
    decimali_retention: dict[int, int] = field(default_factory=dict)

    @property
    def vuota(self) -> bool:
        """Guild osservata, nessuno snapshot: il calcolo non e' ancora girato."""
        return self.snapshot is None

    @property
    def pubblicate(self) -> list[Gruppo]:
        return [g for g in self.gruppi if not g.soppressa]

    @property
    def soppresse(self) -> int:
        return sum(1 for g in self.gruppi if g.soppressa)

    @property
    def sopravvissuti(self) -> int:
        """Quante coorti pubblicate contano solo chi era gia' presente.

        E' un conteggio di righe in vista, non un aggregato di metriche: nessuna
        somma fra ambiti o fra orizzonti (regola 3 e §4). Serve alla frase che §6
        chiede a questa vista — in produzione sono 17 su 25, e senza il numero la
        pagina sembra una tabella grigia invece di dirlo.
        """
        return sum(1 for g in self.pubblicate if g.guida.quality.is_survivors_only is True)

    @property
    def significative(self) -> int:
        return sum(1 for g in self.pubblicate if g.guida.quality.significant is True)


def costruisci(gruppi: list[CohortGroup]) -> Vista:
    """La vista dallo snapshot piu' recente fra quelli ricevuti.

    Con ``limit=1`` lo snapshot e' uno solo. La selezione c'e' lo stesso, e non e'
    difensiva per abitudine: rende la funzione totale su qualunque risposta
    dell'endpoint (che accetta ``limit`` maggiori) invece di dipendere da cosa ha
    chiesto il chiamante — e permette di rendere uno snapshot piu' vecchio
    passandogli le sole righe di quello, come i test fanno per gli stati che
    l'ultimo snapshot non mostra.
    """
    if not gruppi:
        return Vista(snapshot=None, gruppi=[])

    ultimo = max(gruppi, key=lambda g: (g.as_of, g.snapshot_id))
    snapshot = Snapshot(ultimo.snapshot_id, ultimo.as_of)
    scelti = [g for g in gruppi if g.snapshot_id == snapshot.snapshot_id]

    costruiti: list[Gruppo] = []
    # cohort_start DESC: le coorti recenti — le uniche che possono ancora
    # cambiare, e le uniche che possono diventare significative — in cima, e non
    # sepolte sotto la sequenza delle coorti anteriori all'ancora (§4).
    for g in sorted(scelti, key=lambda g: g.cohort_start, reverse=True):
        onboarding = {r.layer_scope: r for r in g.onboarding}
        if not onboarding:
            # Nessuna riga di onboarding: senza non c'e' niente da qualificare, e
            # inventare una riga vuota mostrerebbe una coorte che l'API non ha
            # servito. Il job scrive sempre un ambito per coorte.
            continue
        guida = onboarding.get(AMBITI[0]) or next(iter(onboarding.values()))
        costruiti.append(
            Gruppo(
                cohort_start=g.cohort_start,
                onboarding=onboarding,
                retention={r.horizon_days: r for r in g.retention},
                guida=guida,
            )
        )

    return Vista(
        snapshot=snapshot,
        gruppi=costruiti,
        decimali=decimali_integrazione(costruiti),
        decimali_retention=decimali_retention(costruiti),
    )


def decimali_integrazione(gruppi: list[Gruppo]) -> dict[tuple[str, str], int]:
    """La precisione di ogni colonna di curva, per ambito.

    Si calcola sulle righe PUBBLICATE di questa tabella: una riga soppressa non ha
    valori, e contarla come colonna di zeri abbasserebbe la risoluzione dichiarata
    dalla colonna.
    """
    fuori: dict[tuple[str, str], int] = {}
    for ambito in AMBITI:
        righe = [
            g.onboarding[ambito]
            for g in gruppi
            if ambito in g.onboarding and not g.onboarding[ambito].quality.suppressed
        ]
        for campo in COLONNE_INTEGRAZIONE:
            fuori[(ambito, campo)] = precisione_colonna(getattr(r.values, campo) for r in righe)
    return fuori


def decimali_retention(gruppi: list[Gruppo]) -> dict[int, int]:
    """La precisione della colonna ``trattenuti``, un orizzonte alla volta.

    Tre colonne, tre precisioni: 7, 14 e 28 giorni sono letture diverse della
    stessa coorte, e non si sommano ne' si confrontano come fossero una serie.
    """
    fuori: dict[int, int] = {}
    for orizzonte in ORIZZONTI:
        righe = [
            g.retention[orizzonte]
            for g in gruppi
            if orizzonte in g.retention and not g.retention[orizzonte].quality.suppressed
        ]
        fuori[orizzonte] = precisione_colonna(r.values.retained_fraction for r in righe)
    return fuori


def frase_di_stato(vista: Vista) -> str:
    """Cosa dice questa pagina, in una riga, prima della tabella.

    §6 chiede a questa vista una frase precisa — "il 68% delle coorti conta solo
    chi e' rimasto da prima che Kindling iniziasse a osservare" — e la chiede
    perche' la tabella da sola somiglia a "non c'e' niente", che e' un'altra cosa
    (regola 1). I numeri sono conteggi di righe in vista, non metriche.
    """
    if not vista.gruppi:
        return ""
    pubblicate = len(vista.pubblicate)
    parti = [f"{len(vista.gruppi)} coorti su questo snapshot"]
    if vista.soppresse:
        parti.append(f"{vista.soppresse} sotto la soglia di pubblicazione")
    if vista.sopravvissuti:
        # Il denominatore sono le coorti PUBBLICATE, non tutte: su una riga
        # soppressa is_survivors_only e' None — non "false" — perche' suppress()
        # lo azzera, quindi di quelle coorti questa vista non sa se contino solo
        # chi era gia' presente. Metterle al denominatore darebbe una percentuale
        # piu' bassa del vero, che e' il tipo di numero che sembra migliore.
        quota = round(vista.sopravvissuti / pubblicate * 100)
        parti.append(
            f"{vista.sopravvissuti} delle {pubblicate} pubblicate contano solo chi "
            f"era già presente quando l'osservazione è iniziata ({quota}%)"
        )
    parti.append(
        f"{vista.significative} distinguibili dal rumore"
        if vista.significative
        else "nessuna ancora distinguibile dal rumore"
    )
    return ", ".join(parti) + "."


# =============================================================================
# La vista per chi amministra il server (dashboard.md 4, "Riscritta il
# 26/09/2026"). Tutto quello che sta sopra serve alla tabella tecnica; da qui in
# giu' serve alla pagina Coorti.
# =============================================================================

# Quante coorti si vedono, dalla piu' recente. E' una costante DELLA VISTA, non
# un parametro del job: il job calcola fino a cohort_max_age_days, questa pagina
# ne mostra una parte e rimanda il resto ai Dettagli tecnici. Conta righe, non
# settimane di calendario: una settimana senza ingressi non ha riga nel job.
COORTI_VISIBILI = 12

# La larghezza di una coorte: la settimana ISO di job.cohorts.cohort_start_of.
# Non e' un parametro — e' la definizione di coorte — e serve solo a dire quante
# settimane sono sempre in osservazione.
GIORNI_PER_COORTE = 7

# I quattro stati di una riga, piu' il ramo difensivo delle righe discordi.
SOTTO_SOGLIA = "sotto_soglia"
IN_OSSERVAZIONE = "in_osservazione"
LEGGIBILE = "leggibile"
NON_OSSERVATA = "non_osservata"
DISCORDE = "discorde"

# Le parole delle due misure di integrazione, per ambito. La chiave e' il
# layer_scope del job, importato: e' un'identita', non una soglia.
NOMI_MISURA = {SCOPE_ANY: "in qualunque modo", LAYER_VOICE: "in vocale"}

FASCE = (
    "nessuno",
    "pochissimi",
    "pochi",
    "circa metà",
    "la maggior parte",
    "quasi tutti",
    "tutti",
)

# Le cifre con cui una frazione si arrotonda prima di confrontarla con i bordi.
# Il job non arrotonda, e un bordo interno arriva spesso APPENA SOTTO: 1 - 0.8 in
# Python e' 0.19999999999999996, e il fixture ha davvero 0.050000000000000044.
# Nove cifre assorbono l'errore di rappresentazione senza spostare una frazione
# vera: 0.99999 resta "quasi tutti".
_CIFRE_DI_CONFRONTO = 9

_NUMERI = {1: "una", 2: "due", 3: "tre", 4: "quattro", 5: "cinque", 6: "sei"}


@dataclass(frozen=True)
class Fascia:
    """Una delle sette fasce: l'indice e' la classe CSS, la parola l'aria-label."""

    indice: int
    parola: str


def fascia(frazione: Optional[float]) -> Optional[Fascia]:
    """La fascia di una frazione, o ``None`` se la frazione non c'e'.

    Le barre mostrano la fascia e non il valore: i numeri esatti stanno nei
    Dettagli tecnici. Bordi: 0 e 1 sono fasce a se'; 0,2 e 0,8 aprono la fascia
    successiva; "circa meta'" comprende entrambi i suoi estremi, 0,4 e 0,6.
    """
    if frazione is None:
        return None
    f = round(frazione, _CIFRE_DI_CONFRONTO)
    if f <= 0:
        indice = 0
    elif f >= 1:
        indice = 6
    elif f < 0.2:
        indice = 1
    elif f < 0.4:
        indice = 2
    elif f <= 0.6:
        indice = 3
    elif f < 0.8:
        indice = 4
    else:
        indice = 5
    return Fascia(indice, FASCE[indice])


@dataclass(frozen=True)
class Voce:
    """Una barra dentro una cella, o il posto di una barra che non c'e'.

    ``misura`` e' "presenti" o il ``layer_scope``, ed e' la classe del colore.
    ``fascia`` a ``None`` vuol dire che il numero non c'e' ancora: il template
    scrive "non ancora" accanto al campione di quel colore invece di una barra
    vuota, che si leggerebbe "nessuno".
    """

    misura: str
    fascia: Optional[Fascia]
    nome: str  # "in qualunque modo", "in vocale"; vuoto per "presenti"

    @property
    def etichetta(self) -> str:
        parola = self.fascia.parola if self.fascia else "non ancora"
        return f"{self.nome}: {parola}" if self.nome else parola


@dataclass(frozen=True)
class CellaVista:
    """Una cella della riga leggibile: barre, oppure una frase al loro posto."""

    gruppo: str  # "presenti" | "integra"
    etichetta: str  # data-etichetta, per le schede su telefono
    voci: tuple[Voce, ...] = ()
    testo: Optional[str] = None


@dataclass(frozen=True)
class Riga:
    cohort_start: date
    settimana: str
    stato: str
    persone: Optional[int] = None
    frase: Optional[str] = None
    celle: tuple[CellaVista, ...] = ()
    # Il calcolo da cui una coorte in osservazione diventera' leggibile. Serve
    # anche all'avviso "la prima coorte sara' leggibile dal ...".
    leggibile_dal: Optional[date] = None


@dataclass(frozen=True)
class Avviso:
    titolo: str
    testo: Optional[str] = None


@dataclass(frozen=True)
class Pagina:
    vuota: bool
    arrivo: str = ""
    k: Optional[int] = None
    giorni_maturita: Optional[int] = None
    frase_osservazione: Optional[str] = None
    aggiornati_a: str = ""
    orizzonti_presenti: tuple[int, ...] = ()
    orizzonti_integra: tuple[int, ...] = ()
    ambiti: tuple[str, ...] = ()
    righe: tuple[Riga, ...] = ()
    leggibili: int = 0
    avviso: Optional[Avviso] = None


def _intero(params: Mapping[str, Any], chiave: str) -> Optional[int]:
    """Una soglia intera da ``params``, o ``None``: chiave mancante o di forma
    inattesa, e la frase che la citava non la cita (dashboard.md 4, Stato)."""
    valore = params.get(chiave)
    if isinstance(valore, bool) or not isinstance(valore, int):
        return None
    return valore


def _giorno_utc(istante: datetime) -> date:
    """La data dell'ancora come la vede il job: ``observability_anchor.date()``
    su un TIMESTAMPTZ letto in UTC. Non il giorno di Roma — il confronto deve
    essere quello di ``job.cohorts.is_survivors_only``, al giorno."""
    if istante.tzinfo is None:
        istante = istante.replace(tzinfo=timezone.utc)
    return istante.astimezone(timezone.utc).date()


def anteriore_all_ancora(gruppo: Gruppo, ancora: datetime) -> bool:
    """La coorte precede l'arrivo del bot?

    Due forme, perche' il dato ne ha due. Su una riga pubblicata lo dice il job,
    con ``is_survivors_only``. Su una soppressa quel flag non c'e' piu' —
    ``suppress()`` lo azzera — e si rifa' il confronto del job:
    ``cohort_start < observability_anchor.date()``, stesso operatore, stessa data.
    """
    if not gruppo.soppressa:
        return gruppo.guida.quality.is_survivors_only is True
    return gruppo.cohort_start < _giorno_utc(ancora)


def calcolo_che_vede(
    as_of: datetime, giorni: int, cadenza: Optional[timedelta]
) -> Optional[datetime]:
    """Il primo calcolo, con la cadenza osservata, che cade ``giorni`` dopo ``as_of``.

    ``as_of + m * cadenza`` con ``m`` il piu' piccolo intero che ci arriva. Con la
    cadenza settimanale e' esatto: ``observation_days`` e' troncato, il valore
    vero sta in ``[obs, obs + 1)``, e un multiplo intero di sette giorni supera la
    soglia sul valore vero se e solo se la supera su ``obs`` (dashboard.md 4).

    ``None`` senza cadenza — con una run sola un intervallo non si misura, e la
    data sarebbe inventata — e ``None`` se lo scarto non e' positivo: una data
    uguale o anteriore a ``as_of`` direbbe che il calcolo c'e' gia' stato, e le
    righe che arrivano qui sono quelle per cui non c'e' stato.
    """
    if cadenza is None or cadenza <= timedelta(0) or giorni <= 0:
        return None
    scarto = timedelta(days=giorni)
    passi = -((-scarto) // cadenza)
    return as_of + passi * cadenza


def orizzonti_integrazione() -> tuple[int, ...]:
    """Gli orizzonti di "si integrano", dai NOMI dei campi del contratto.

    ``reached_by_14d`` e ``reached_by_28d`` sono campi di ``OnboardingValues``: i
    due numeri stanno nel contratto dell'API, non in una lista di questa vista.
    Un orizzonte aggiunto al modello compare qui da solo.
    """
    trovati = []
    for nome in OnboardingValues.model_fields:
        corrisponde = re.fullmatch(r"reached_by_(\d+)d", nome)
        if corrisponde:
            trovati.append(int(corrisponde.group(1)))
    return tuple(sorted(trovati))


def _run_dello_snapshot(runs: Sequence[RunRow], snapshot: Snapshot) -> Optional[RunRow]:
    """La run che ha scritto QUESTE coorti. Non "l'ultima run": i params sono
    quelli con cui questi numeri sono stati calcolati."""
    candidate = [r for r in runs if r.snapshot_id == snapshot.snapshot_id]
    return max(candidate, key=lambda r: r.as_of) if candidate else None


def _k(gruppi: Sequence[Gruppo]) -> Optional[int]:
    """``k`` dalla riga: ``OnboardingRow.k``, la stessa per tutte le righe di una
    run. Se due righe dicessero due cose diverse, la frase non ne cita nessuna."""
    valori = {r.k for g in gruppi for r in g.onboarding.values()}
    return valori.pop() if len(valori) == 1 else None


def _frase_osservazione(giorni_maturita: Optional[int]) -> Optional[str]:
    if giorni_maturita is None or giorni_maturita <= 0:
        return None
    settimane = math.ceil(giorni_maturita / GIORNI_PER_COORTE)
    giorni = "1 giorno" if giorni_maturita == 1 else f"{giorni_maturita} giorni"
    if settimane == 1:
        inizio = "L'ultima settimana è sempre in osservazione"
    else:
        quante = _NUMERI.get(settimane, str(settimane))
        inizio = f"Le ultime {quante} settimane sono sempre in osservazione"
    return f"{inizio}: una coorte si legge quando sono passati {giorni} dall'ultimo ingresso."


def _cella_presenti(
    gruppo: Gruppo, orizzonte: int, as_of: datetime, cadenza: Optional[timedelta]
) -> CellaVista:
    etichetta = f"dopo {orizzonte} gg"
    riga = gruppo.retention.get(orizzonte)
    if riga is None or riga.quality.suppressed:
        # Ramo difensivo: su una riga leggibile la retention esiste sempre ed e'
        # pubblicata (stessa soglia sullo stesso n_effective, dashboard.md 4).
        return CellaVista("presenti", etichetta, testo="non disponibile")
    v = riga.values
    if v.is_computable is True and v.retained_fraction is not None:
        voce = Voce("presenti", fascia(v.retained_fraction), "")
        return CellaVista("presenti", etichetta, voci=(voce,))
    if v.not_computable_reason == "horizon_not_reached":
        osservati = gruppo.guida.quality.observation_days
        quando = (
            calcolo_che_vede(as_of, orizzonte - osservati, cadenza)
            if osservati is not None
            else None
        )
        testo = f"dal {_stato.data_estesa(_stato.giorno(quando))}" if quando else "non ancora"
        return CellaVista("presenti", etichetta, testo=testo)
    # Un altro motivo su una coorte posteriore all'ancora non esiste (dashboard.md
    # 4): lo si dice, invece di inventare una barra.
    return CellaVista("presenti", etichetta, testo="non calcolabile")


def _cella_integra(gruppo: Gruppo, orizzonte: int, ambiti: Sequence[str]) -> CellaVista:
    etichetta = f"entro {orizzonte} gg"
    campo = f"reached_by_{orizzonte}d"
    voci = tuple(
        Voce(
            ambito,
            fascia(getattr(gruppo.onboarding[ambito].values, campo)),
            NOMI_MISURA.get(ambito, ambito),
        )
        for ambito in ambiti
        if ambito in gruppo.onboarding
    )
    # "non ancora" senza data: quando diventa calcolabile dipende dal primo
    # ingresso della coorte e dagli eventi, e l'API non espone ne' l'uno ne' gli
    # altri. Se manca un ambito solo, la sua voce resta vuota accanto all'altra:
    # succede davvero, e non solo con l'arancio mancante (dashboard.md 4, "La
    # barra blu puo' superare l'arancio").
    if not any(v.fascia for v in voci):
        return CellaVista("integra", etichetta, testo="non ancora")
    return CellaVista("integra", etichetta, voci=voci)


def _riga(
    gruppo: Gruppo,
    *,
    as_of: datetime,
    cadenza: Optional[timedelta],
    giorni_maturita: Optional[int],
    soglia: Optional[int],
    orizzonti_presenti: Sequence[int],
    orizzonti_integra: Sequence[int],
    ambiti: Sequence[str],
) -> Riga:
    settimana = _stato.data_estesa(gruppo.cohort_start)
    if gruppo.soppressa:
        frase = (
            f"meno di {soglia} ingressi: non mostrata"
            if soglia is not None
            else "troppo pochi ingressi: non mostrata"
        )
        return Riga(gruppo.cohort_start, settimana, SOTTO_SOGLIA, frase=frase)

    q = gruppo.guida.quality
    if gruppo.divergenze:
        # Ramo difensivo (dashboard.md 4, "I rami difensivi"): le cinque righe
        # non concordano su cio' che il job calcola una volta sola. Nessuna barra
        # e nessuna persona: non si sa quale riga dica il vero.
        return Riga(
            gruppo.cohort_start, settimana, DISCORDE,
            frase="i dati di questa settimana non concordano fra loro: "
                  "si leggono solo nei dettagli tecnici",
        )

    if q.is_mature is not True:
        quando = None
        if giorni_maturita is not None and q.observation_days is not None:
            istante = calcolo_che_vede(as_of, giorni_maturita - q.observation_days, cadenza)
            quando = _stato.giorno(istante) if istante else None
        frase = (
            "in osservazione · leggibile dal calcolo di "
            f"{_stato.data_estesa(quando, con_giorno=True)}"
            if quando
            else "in osservazione"
        )
        return Riga(gruppo.cohort_start, settimana, IN_OSSERVAZIONE, persone=q.n_effective,
                    frase=frase, leggibile_dal=quando)

    if q.significant is not True:
        # Matura e non significativa. Oggi vuol dire solo "senza copertura di
        # snapshot" (verificato per esecuzione, dashboard.md 4), ma la condizione
        # non nomina il motivo: un motivo nuovo del job finirebbe qui da solo,
        # invece che fra le leggibili per esclusione.
        return Riga(
            gruppo.cohort_start, settimana, NON_OSSERVATA, persone=q.n_effective,
            frase="Kindling non ha osservato abbastanza questa settimana per leggerla",
        )

    celle = tuple(
        _cella_presenti(gruppo, h, as_of, cadenza) for h in orizzonti_presenti
    ) + tuple(_cella_integra(gruppo, h, ambiti) for h in orizzonti_integra)
    return Riga(gruppo.cohort_start, settimana, LEGGIBILE, persone=q.n_effective, celle=celle)


def _avviso(righe: Sequence[Riga]) -> Optional[Avviso]:
    """Con una o due coorti leggibili, un avviso; con zero, la data della prima."""
    leggibili = sum(1 for r in righe if r.stato == LEGGIBILE)
    if leggibili == 1:
        return Avviso(
            "Per ora una sola settimana è leggibile.",
            "Dice com'è andata a quel gruppo, non ancora come va di solito: il "
            "confronto fra settimane diventa possibile con più coorti.",
        )
    if leggibili == 2:
        return Avviso(
            "Per ora due settimane sono leggibili.",
            "Dicono com'è andata a quei due gruppi, non ancora come va di solito: il "
            "confronto fra settimane diventa possibile con più coorti.",
        )
    if leggibili == 0:
        prossime = [r.leggibile_dal for r in righe if r.leggibile_dal is not None]
        if prossime:
            return Avviso(
                "La prima coorte sarà leggibile dal calcolo di "
                f"{_stato.data_estesa(min(prossime), con_giorno=True)}."
            )
    return None


def pagina(
    gruppi: Sequence[CohortGroup], guild: GuildRow, runs: Sequence[RunRow]
) -> Pagina:
    """La vista Coorti per chi amministra il server (dashboard.md 4).

    Parte dalla stessa ``costruisci()`` della tabella tecnica — gruppi, guida,
    divergenze — e ne tiene solo cio' che risponde alla domanda della vista.
    """
    tecnica_ = costruisci(list(gruppi))
    if tecnica_.snapshot is None:
        return Pagina(vuota=True)

    run = _run_dello_snapshot(runs, tecnica_.snapshot)
    params = run.params if run is not None else {}
    giorni_maturita = _intero(params, "min_observation_days")
    soglia = _intero(params, "min_cardinality")
    cadenza = _stato.cadenza_osservata(runs)
    as_of = tecnica_.snapshot.as_of

    posteriori = [
        g for g in tecnica_.gruppi if not anteriore_all_ancora(g, guild.first_seen_at)
    ]
    orizzonti_presenti = tuple(sorted({h for g in posteriori for h in g.retention}))
    orizzonti_integra = orizzonti_integrazione()
    ambiti = tuple(a for a in AMBITI if any(a in g.onboarding for g in posteriori))

    # Gia' dalla piu' recente: costruisci() ordina per cohort_start DESC.
    tutte = [
        _riga(
            g, as_of=as_of, cadenza=cadenza, giorni_maturita=giorni_maturita,
            soglia=soglia, orizzonti_presenti=orizzonti_presenti,
            orizzonti_integra=orizzonti_integra, ambiti=ambiti,
        )
        for g in posteriori
    ]
    return Pagina(
        vuota=False,
        arrivo=_stato.data_estesa(guild.first_seen_at),
        k=_k(posteriori),
        giorni_maturita=giorni_maturita,
        frase_osservazione=_frase_osservazione(giorni_maturita),
        aggiornati_a=_stato.data_estesa(as_of, con_giorno=True),
        orizzonti_presenti=orizzonti_presenti,
        orizzonti_integra=orizzonti_integra,
        ambiti=ambiti,
        righe=tuple(tutte[:COORTI_VISIBILI]),
        # Contate su TUTTE le posteriori, non sulle dodici visibili: l'avviso
        # dice quante settimane si possono leggere, non quante stanno in pagina.
        leggibili=sum(1 for r in tutte if r.stato == LEGGIBILE),
        avviso=_avviso(tutte),
    )


# --- la tabella tecnica, divisa dall'ancora ----------------------------------


@dataclass(frozen=True)
class Tecnica:
    """Le coorti dei Dettagli tecnici: due tabelle, dopo e prima dell'arrivo del bot."""

    tutte: Vista
    posteriori: Vista
    anteriori: Vista
    giorni_maturita: Optional[int]
    k: Optional[int]

    @property
    def vuota(self) -> bool:
        return self.tutte.vuota


def tecnica(
    gruppi: Sequence[CohortGroup], ancora: datetime, params: Optional[Mapping[str, Any]]
) -> Tecnica:
    """La stessa ``costruisci()`` di sempre, chiamata due volte.

    Due volte e non una tabella con un separatore: la precisione di una colonna
    si calcola sulle righe di QUELLA tabella (dashboard.md 5), e le coorti
    anteriori all'ancora non devono cambiare la risoluzione delle colonne delle
    altre.
    """
    tutte = costruisci(list(gruppi))
    if tutte.snapshot is None:
        vuota = Vista(snapshot=None, gruppi=[])
        return Tecnica(vuota, vuota, vuota, None, None)
    scelti = [g for g in gruppi if g.snapshot_id == tutte.snapshot.snapshot_id]
    prima = {g.cohort_start for g in tutte.gruppi if anteriore_all_ancora(g, ancora)}
    p = params or {}
    return Tecnica(
        tutte=tutte,
        posteriori=costruisci([g for g in scelti if g.cohort_start not in prima]),
        anteriori=costruisci([g for g in scelti if g.cohort_start in prima]),
        giorni_maturita=_intero(p, "min_observation_days"),
        k=_k(tutte.gruppi),
    )
