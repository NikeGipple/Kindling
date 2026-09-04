"""Onboarding e retention per coorte di ingresso (modello-metriche.md 5).

Il punto centrale e' la **censura a destra**: la coorte entrata la settimana
scorsa non ha ancora avuto il tempo di raggiungere k connessioni, e contarla
come "non integrata" e' un errore, non un dato. Su una coorte giovane la
frazione grezza "quanti hanno raggiunto k" non misura l'integrazione, misura
quanto poco tempo e' passato — quindi si stima con Kaplan-Meier e non con una
proporzione.

Il tempo all'evento e' misurato **in snapshot**, non in giorni: il grafo esiste
solo a snapshot, e ricostruire l'istante esatto dagli eventi grezzi
significherebbe rifare qui la ricostruzione delle sessioni.

Il conteggio dei partner del singolo membro e il suo tempo all'evento restano
dentro questo modulo: escono solo aggregati per coorte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional

from .admission import admitted_pairs
from .config import SCOPE_ANY, MetricParams

_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class CohortMember:
    """Un membro come lo vede questo layer: solo date, nessun contenuto."""

    author_id: int
    joined_at: datetime
    left_at: Optional[datetime] = None
    # Esiste attivita' della persona in questa guild ANTERIORE al suo
    # joined_at: o e' un rientro o e' un dato incoerente, e in entrambi i casi
    # non e' un nuovo membro di cui misurare l'onboarding (members sovrascrive
    # joined_at/left_at sui rientri, quindi il rientrante e' altrimenti
    # indistinguibile da un nuovo arrivato con relazioni gia' pronte).
    has_prior_activity: bool = False


@dataclass
class CohortResult:
    """Una riga di ``metric_cohorts``, prima della soppressione."""

    cohort_start: date
    layer_scope: str
    k: int
    n_effective: Optional[int] = None
    observation_days: Optional[int] = None
    is_mature: Optional[bool] = None
    event_count: Optional[int] = None
    censored_count: Optional[int] = None
    censored_by_leave: Optional[int] = None
    median_days_to_k: Optional[float] = None
    median_reached: Optional[bool] = None
    p25_days_to_k: Optional[float] = None
    p75_days_to_k: Optional[float] = None
    reached_by_14d: Optional[float] = None
    reached_by_28d: Optional[float] = None
    excluded_rejoins: Optional[int] = None
    # La coorte precede l'istante da cui le uscite sono osservabili: e' composta
    # per costruzione dai soli sopravvissuti, quindi n_effective non e' "quanti
    # sono entrati" ma "quanti erano ancora presenti al backfill".
    is_survivors_only: Optional[bool] = None
    # Esiste almeno uno snapshot confrontabile che copre i primi
    # min_observation_days della coorte. Senza, nessuno POTEVA raggiungere k.
    has_snapshot_coverage: Optional[bool] = None
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None
    is_significant: Optional[bool] = None
    details: dict[str, Any] = field(default_factory=dict)

    KEY_FIELDS = ("cohort_start", "layer_scope", "k")


@dataclass
class RetentionResult:
    """Una riga di ``metric_cohort_retention``."""

    cohort_start: date
    horizon_days: int
    n_effective: Optional[int] = None
    excluded_rejoins: Optional[int] = None
    is_survivors_only: Optional[bool] = None
    retained_fraction: Optional[float] = None
    is_computable: Optional[bool] = None
    not_computable_reason: Optional[str] = None
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None

    KEY_FIELDS = ("cohort_start", "horizon_days")


@dataclass(frozen=True)
class Observation:
    """Un membro nell'analisi di sopravvivenza."""

    days: float
    event: bool
    censored_by_leave: bool = False


def series_spacing(moments: Iterable[datetime]) -> Optional[dict[str, float]]:
    """Cadenza osservata e spaziatura massima di una serie di snapshot.

    Serve al punto 2 di modello-metriche.md 5.3: il tempo all'evento e' misurato
    **in snapshot**, quindi una settimana in cui il job non e' stato eseguito e'
    censura intervallare — l'evento e' avvenuto in una finestra piu' larga — e
    due coorti sono confrontabili solo se la serie sotto di loro ha la stessa
    cadenza.

    Non e' un doppione di ``snapshots_skipped_params``: quello conta gli
    snapshot che **esistono** ma non sono confrontabili, questo le settimane in
    cui lo snapshot **non c'e'**. Un cron settimanale che fallisce una volta
    produce il secondo, non il primo.

    Si riportano due numeri e nessun flag: la cadenza osservata (mediana delle
    spaziature) e la spaziatura massima. Un flag "c'e' un buco" richiederebbe
    una soglia, e una soglia inventata oggi su tre giorni di dati sarebbe un
    parametro senza base messo davanti a un dato che si legge benissimo da solo:
    se il massimo e' il doppio della mediana, una settimana manca.

    ``None`` con meno di due snapshot: una spaziatura non esiste, e non e' zero.
    """
    ordered = sorted(moments)
    if len(ordered) < 2:
        return None
    gaps = [
        (later - earlier).total_seconds() / _SECONDS_PER_DAY
        for earlier, later in zip(ordered, ordered[1:])
    ]
    return {
        "cadence_days_median": _median(gaps),
        "max_gap_days": max(gaps),
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def is_survivors_only(
    cohort_start: date, *, observability_anchor: Optional[datetime]
) -> bool:
    """La coorte precede l'istante da cui le uscite sono osservabili?

    ``members`` non e' un log: e' stata popolata da ``!backfill_members``, e
    contiene chi era presente **quel giorno**. Chi e' entrato prima e uscito
    prima del backfill non ha un ``left_at`` — non e' mai stato scritto. Una
    coorte anteriore all'ancora e' quindi composta per costruzione dai soli
    sopravvissuti: sbagliato il numeratore E il denominatore, e ``n_effective``
    non significa "quanti sono entrati" ma "quanti erano ancora presenti".

    Senza ancora (nessun evento noto) la risposta e' ``True``: non poter
    stabilire da quando si osserva non e' una prova che si osservasse da sempre,
    e l'errore conservativo e' dichiarare il limite.
    """
    if observability_anchor is None:
        return True
    return cohort_start < observability_anchor.date()


def has_snapshot_coverage(
    cohort_start: date,
    windows: Iterable[tuple[datetime, datetime]],
    *,
    params: MetricParams,
) -> bool:
    """Esiste uno snapshot che copre i primi giorni di osservazione della coorte?

    Il tempo trascorso e la copertura di grafo sono due cose diverse, e solo la
    seconda dice se il numero vuol dire qualcosa: una coorte di marzo con un
    solo snapshot ad agosto ha 170 giorni di calendario alle spalle e **zero**
    dato di grafo sulla propria finestra. In quel caso ``reached_by_14d = 0.0``
    non misura l'integrazione, misura l'assenza di osservazione — e letto da un
    admin direbbe "a marzo nessuno costruiva connessioni".

    Copertura = almeno una finestra di snapshot che interseca
    ``[cohort_start, cohort_start + min_observation_days)``.
    """
    start = datetime.combine(cohort_start, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=params.min_observation_days)
    return any(
        window_start < end and window_end > start for window_start, window_end in windows
    )


def cohort_start_of(moment: datetime) -> date:
    """Lunedi' della settimana ISO in cui cade ``moment``."""
    day = moment.date()
    return day - timedelta(days=day.weekday())


def split_cohorts(
    members: Iterable[CohortMember], *, params: MetricParams
) -> tuple[dict[date, list[CohortMember]], dict[date, int]]:
    """Raggruppa i membri per coorte, escludendo i rientri sospetti.

    Ritorna anche, per coorte, quanti ne sono stati esclusi: il conteggio esce,
    l'elenco no.
    """
    cohorts: dict[date, list[CohortMember]] = {}
    excluded: dict[date, int] = {}
    for member in members:
        start = cohort_start_of(member.joined_at)
        cohorts.setdefault(start, [])
        excluded.setdefault(start, 0)
        if params.exclude_suspected_rejoins and member.has_prior_activity:
            excluded[start] += 1
            continue
        cohorts[start].append(member)
    return cohorts, excluded


def observations_for(
    members: Iterable[CohortMember],
    *,
    reached_at: dict[int, datetime],
    as_of: datetime,
) -> list[Observation]:
    """Osservazioni di sopravvivenza per una coorte.

    ``reached_at`` mappa author_id -> istante (l'``as_of`` dello snapshot) in
    cui il membro ha raggiunto k partner distinti; assente = non raggiunto.

    Un membro e' censurato quando non ha ancora raggiunto k all'``as_of`` della
    run, oppure quando e' uscito dal server prima di raggiungerlo. Il secondo
    caso e' propriamente un RISCHIO COMPETITIVO e non una censura: chi se ne va
    prima di integrarsi non e' "uno di cui non sappiamo ancora", e' uno che non
    si integrera'. Trattarlo come censura fa si' che Kaplan-Meier sovrastimi la
    probabilita' di integrazione. E' una semplificazione consapevole di v0, e il
    contatore separato esiste per renderla visibile.
    """
    observations: list[Observation] = []
    for member in members:
        reached = reached_at.get(member.author_id)
        if reached is not None:
            observations.append(
                Observation(days=_days_between(member.joined_at, reached), event=True)
            )
            continue
        if member.left_at is not None and member.left_at <= as_of:
            observations.append(
                Observation(
                    days=_days_between(member.joined_at, member.left_at),
                    event=False,
                    censored_by_leave=True,
                )
            )
            continue
        observations.append(
            Observation(days=_days_between(member.joined_at, as_of), event=False)
        )
    return observations


def _days_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / _SECONDS_PER_DAY)


class SurvivalCurve:
    """Stimatore di Kaplan-Meier.

    ``S(t) = prod (1 - d_i / n_i)`` sui tempi di evento ``t_i <= t``, dove
    ``d_i`` e' il numero di membri che raggiungono k al tempo ``t_i`` e ``n_i``
    il numero ancora a rischio appena prima.

    Poche righe di produttoria: non giustifica una dipendenza da ``lifelines``
    su una droplet da 1 GB.

    La curva completa **non viene pubblicata**: i suoi gradini sono di ampiezza
    ``1/n_i`` e su una coorte piccola raccontano quante persone hanno fatto cosa
    e quando. Vive qui e viene buttata.
    """

    def __init__(self, observations: Iterable[Observation]) -> None:
        items = sorted(observations, key=lambda o: (o.days, not o.event))
        self.total = len(items)
        self.events = sum(1 for o in items if o.event)
        self.censored = self.total - self.events
        self.censored_by_leave = sum(1 for o in items if o.censored_by_leave)
        # Il tempo piu' lungo che qualcuno di questa coorte ha effettivamente
        # vissuto sotto osservazione: oltre, la curva non e' informata da nulla.
        self.max_observed = max((o.days for o in items), default=0.0)

        self._steps: list[tuple[float, float]] = []
        at_risk = self.total
        survival = 1.0
        index = 0
        while index < len(items):
            time = items[index].days
            deaths = 0
            leaving = 0
            while index < len(items) and items[index].days == time:
                leaving += 1
                if items[index].event:
                    deaths += 1
                index += 1
            if deaths and at_risk > 0:
                survival *= 1.0 - deaths / at_risk
                self._steps.append((time, survival))
            at_risk -= leaving

    def survival_at(self, time: float) -> float:
        value = 1.0
        for step_time, step_value in self._steps:
            if step_time <= time:
                value = step_value
            else:
                break
        return value

    def quantile(self, level: float) -> Optional[float]:
        """Primo ``t`` con ``S(t) <= 1 - level``, oppure ``None``.

        ``None`` significa "non raggiunto nell'osservazione disponibile", e va
        scritto come NULL: un quantile estrapolato oltre i dati e' un numero
        inventato che a valle nessuno distingue da uno osservato.
        """
        target = 1.0 - level
        for step_time, step_value in self._steps:
            if step_value <= target:
                return step_time
        return None

    def reached_by(self, time: float) -> Optional[float]:
        """``1 - S(t)``: frazione stimata che ha raggiunto k entro ``t``.

        **Nessuna estrapolazione finche' la curva non e' arrivata a zero.**

        - Dentro l'osservazione il valore c'e' sempre.
        - Oltre, conta se restano censurati. Se ``S(max_observed) = 0`` tutti
          hanno avuto l'evento: la curva resta a zero per costruzione, e
          ``1 - S(t) = 1`` e' **esatto** per qualunque ``t`` successivo, non
          estrapolato. Se invece ``S(max_observed) > 0`` restano persone di cui
          non sappiamo, e li' l'unica risposta onesta e' ``None``.

        Il caso limite conta in entrambe le direzioni. Senza la regola,
        basterebbe un singolo evento perche' la curva rispondesse a qualunque
        orizzonte: una coorte osservata tre giorni, con un evento al giorno 2,
        pubblicherebbe un ``reached_by_28d`` ricavato da due giorni di dati. Ma
        con la regola applicata al solo ``max_observed``, una coorte tutta
        integrata entro il giorno 3 e osservata per un mese sparirebbe anche
        lei — e quello e' il caso che un admin piu' vorrebbe vedere, reso
        indistinguibile da quello in cui il numero davvero non si puo' dire.

        ``survival`` vale esattamente ``0.0`` quando ``deaths == at_risk``,
        quindi il confronto non ha bisogno di epsilon.
        """
        if self.total == 0:
            return None
        if time <= self.max_observed:
            return 1.0 - self.survival_at(time)
        if self.survival_at(self.max_observed) == 0.0:
            return 1.0
        return None


@dataclass(frozen=True)
class PartnerEdge:
    """Un arco come lo vede il conteggio dei partner: chi, con chi, quanto.

    Porta ``weight`` e ``interaction_count`` perche' l'ammissione e' la stessa
    del grafo strutturale (``admission.admitted_pairs``) e ha bisogno di
    entrambi: filtrarli nel SQL riga per riga darebbe una regola diversa non
    appena ``min_edge_weight`` si alza.
    """

    layer: str
    src_author_id: int
    dst_author_id: int
    weight: float
    interaction_count: int


class PartnerTracker:
    """Accumula partner distinti snapshot per snapshot, e registra quando si arriva a k.

    La logica sta qui e non nel SQL perche' e' logica: ``db.py`` guida il ciclo
    — uno snapshot alla volta, archi filtrati ai soli membri ancora aperti, come
    impone il vincolo di memoria di modello-metriche.md 11.1 — e questo oggetto
    decide cosa conta come partner e quando l'evento e' avvenuto.

    Gli insiemi di partner sono un dato per-nodo: vivono qui, e cio' che esce e'
    solo l'istante dell'evento per membro, che il calcolo della coorte consuma
    per costruire la curva di sopravvivenza e poi butta.
    """

    def __init__(
        self, members: Iterable[CohortMember], *, params: MetricParams
    ) -> None:
        self._params = params
        self._scopes = tuple(params.cohort_layer_scopes)
        self._joined_at = {member.author_id: member.joined_at for member in members}
        self._partners: dict[str, dict[int, set[int]]] = {
            scope: {author_id: set() for author_id in self._joined_at}
            for scope in self._scopes
        }
        self.reached: dict[str, dict[int, datetime]] = {
            scope: {} for scope in self._scopes
        }
        self.snapshots_used = 0

    def pending(self, as_of: datetime) -> list[int]:
        """Membri gia' entrati e non ancora arrivati a k in tutti gli ambiti.

        Chi ha gia' raggiunto k esce dalla scansione: il suo insieme di partner
        viene buttato invece di continuare a crescere, perche' qui l'evento e'
        "averle fatte", non mantenerle.
        """
        return [
            author_id
            for author_id, joined_at in self._joined_at.items()
            if joined_at <= as_of
            and any(author_id not in self.reached[scope] for scope in self._scopes)
        ]

    def partners_of(self, author_id: int, *, scope: str) -> set[int]:
        """I partner accumulati finora. Dato per-nodo: solo per i test."""
        return set(self._partners[scope].get(author_id, set()))

    def observe(self, as_of: datetime, edges: Iterable[PartnerEdge]) -> None:
        """Aggiunge i partner visti in uno snapshot e chiude chi arriva a k.

        L'ammissione passa da ``admission.admitted_pairs``, la stessa funzione
        del grafo strutturale: proiezione non diretta, poi ``min_edge_weight``
        sulla somma degli orientamenti, poi ``partner_min_interactions`` sulla
        stessa somma. Una relazione fatta di una reply per verso ha DUE
        interazioni, non una per direzione: la soglia riguarda la relazione.
        """
        watched = set(self.pending(as_of))
        if not watched:
            return
        self.snapshots_used += 1

        # Un layer alla volta, e la soglia non si somma mai tra layer: 'any' e'
        # un'unione di insiemi di persone, quindi un partner qualifica se
        # qualifica in ALMENO UN layer, non mettendo insieme pesi di relazioni
        # di tipo diverso per superare la soglia (modello-grafo.md 1).
        pairs = admitted_pairs(
            edges,
            params=self._params,
            min_interactions=self._params.partner_min_interactions,
        )

        for pair in pairs:
            for author_id, partner in ((pair.low, pair.high), (pair.high, pair.low)):
                if author_id not in watched:
                    continue
                for scope in self._scopes:
                    if scope != SCOPE_ANY and scope != pair.layer:
                        continue
                    if author_id in self.reached[scope]:
                        continue
                    self._partners[scope][author_id].add(partner)

        for scope in self._scopes:
            for author_id in watched:
                if author_id in self.reached[scope]:
                    continue
                if len(self._partners[scope][author_id]) >= self._params.k_connections:
                    self.reached[scope][author_id] = as_of
                    self._partners[scope][author_id] = set()


def compute_cohort(
    *,
    cohort_start: date,
    layer_scope: str,
    members: list[CohortMember],
    reached_at: dict[int, datetime],
    excluded_rejoins: int,
    as_of: datetime,
    params: MetricParams,
    observability_anchor: Optional[datetime] = None,
    snapshot_windows: Iterable[tuple[datetime, datetime]] = (),
    details: Optional[dict[str, Any]] = None,
) -> CohortResult:
    """Una riga di onboarding per coorte, ambito e k."""
    observations = observations_for(members, reached_at=reached_at, as_of=as_of)
    curve = SurvivalCurve(observations)

    # Osservazione della coorte: dal membro entrato piu' tardi, cosi' la
    # maturita' non e' piu' generosa di quanto lo sia il membro meno osservato.
    if members:
        latest_join = max(member.joined_at for member in members)
        observation_days = int(_days_between(latest_join, as_of))
    else:
        observation_days = 0
    is_mature = observation_days >= params.min_observation_days
    survivors_only = is_survivors_only(
        cohort_start, observability_anchor=observability_anchor
    )
    coverage = has_snapshot_coverage(cohort_start, snapshot_windows, params=params)

    median = curve.quantile(0.5)
    reach = {
        horizon: curve.reached_by(float(horizon))
        for horizon in params.reach_horizons_days
    }

    result_details: dict[str, Any] = dict(details or {})
    reasons: list[str] = []
    if not is_mature:
        reasons.append("cohort_not_mature")
    if not coverage:
        # Il tempo e' passato ma il grafo non c'era: nessuno POTEVA raggiungere
        # k, e uno zero qui misura l'assenza di osservazione.
        reasons.append("no_snapshot_coverage")
    if survivors_only:
        # I numeri si calcolano, ma su una popolazione che non e' quella che
        # sembra: una stima su soli sopravvissuti non e' affidabile.
        reasons.append("survivors_only_cohort")
    if reasons:
        result_details["not_significant_because"] = reasons

    return CohortResult(
        cohort_start=cohort_start,
        layer_scope=layer_scope,
        k=params.k_connections,
        n_effective=len(members),
        observation_days=observation_days,
        is_mature=is_mature,
        event_count=curve.events,
        censored_count=curve.censored,
        censored_by_leave=curve.censored_by_leave,
        median_days_to_k=median,
        median_reached=median is not None,
        p25_days_to_k=curve.quantile(0.25),
        p75_days_to_k=curve.quantile(0.75),
        reached_by_14d=reach.get(14),
        reached_by_28d=reach.get(28),
        excluded_rejoins=excluded_rejoins,
        is_survivors_only=survivors_only,
        has_snapshot_coverage=coverage,
        is_significant=not reasons,
        details=result_details,
    )


def compute_retention(
    *,
    cohort_start: date,
    horizon_days: int,
    members: list[CohortMember],
    excluded_rejoins: int,
    as_of: datetime,
    observability_anchor: Optional[datetime] = None,
) -> RetentionResult:
    """Retention della coorte a un orizzonte fisso.

    Calcolabile solo se **tutti** i membri hanno avuto ``horizon_days`` di
    osservazione. Senza questa regola una coorte entrata ieri risulterebbe con
    "100% di retention a 28 giorni", che e' il modo piu' diretto di trasformare
    l'assenza di dati in un ottimo risultato.

    La popolazione e' la stessa di ``compute_cohort``, rientri esclusi:
    ``n_effective`` ed ``excluded_rejoins`` sono ripetuti qui apposta perche'
    una divergenza tra i due denominatori sia verificabile con una query invece
    di restare un numero plausibile.
    """
    horizon = timedelta(days=horizon_days)
    survivors_only = is_survivors_only(
        cohort_start, observability_anchor=observability_anchor
    )

    reason: Optional[str] = None
    if survivors_only:
        # Su una coorte fatta di soli sopravvissuti la retention e' 1.0 per
        # costruzione: e' una tautologia, non un risultato, ed e' esattamente il
        # tipo di numero che sembra buono.
        reason = "before_observability_anchor"
    elif not members:
        reason = "empty_cohort"
    elif not all(member.joined_at + horizon <= as_of for member in members):
        reason = "horizon_not_reached"
    computable = reason is None

    retained: Optional[float] = None
    if computable:
        survivors = sum(
            1
            for member in members
            if member.left_at is None or member.left_at >= member.joined_at + horizon
        )
        retained = survivors / len(members)

    return RetentionResult(
        cohort_start=cohort_start,
        horizon_days=horizon_days,
        n_effective=len(members),
        excluded_rejoins=excluded_rejoins,
        is_survivors_only=survivors_only,
        retained_fraction=retained,
        is_computable=computable,
        not_computable_reason=reason,
    )
