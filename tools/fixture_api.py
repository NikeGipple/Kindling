"""Server di fixture per lo sviluppo della dashboard.

Espone le stesse sette rotte dell'API reale, con risposte sintetiche. Serve a
sviluppare la dashboard senza toccare la droplet e — soprattutto — a esercitare
gli stati che in produzione oggi NON esistono.

**Perche' non si sviluppa contro i dati veri.** Oggi la produzione ha due
snapshot, tutto sotto la soglia dei 30 nodi e le coorti non mature: esercita
pochi degli stati che la dashboard deve saper rendere. Gli altri — riga
soppressa, valore significativo, ``previous_gap_days`` anomalo, retention non
calcolabile, coorte di soli sopravvissuti, layer assente — comparirebbero per la
prima volta in produzione, da soli, quando nessuno sta guardando. Un percorso di
rendering mai eseguito non e' codice che funziona: e' codice di cui non si sa
niente. E' la classe di difetto di CLAUDE.md 7 applicata alla UI.

**Perche' le fixture si costruiscono con i modelli veri.** Ogni riga qui sotto e'
un'istanza di ``api.models``, non un dizionario scritto a mano. Se un campo
cambia nome, sparisce o cambia tipo, questo file smette di importare o di
validare — rumorosamente, adesso — invece di servire per mesi una forma che
l'API non produce piu'.

**Ma i modelli garantiscono la FORMA, non il CONTENUTO.** Tre volte i valori di
questo file sono divergenti dal job pur restando perfettamente validi per i
modelli: codici di motivo inventati, ``nodes_removed`` calcolato con
un'aritmetica diversa, bucket della distribuzione che il job non puo' emettere.
Per questo le soglie e le formule si IMPORTANO da ``job/config.py`` e si
DERIVANO, invece di essere passate a mano riga per riga, e ``--check`` verifica
anche il contenuto (``problemi_di_contenuto``). Per le coorti non si deriva
nemmeno: si CHIAMA il job (``compute_cohort``, ``compute_retention``) su membri
sintetici, e le coorti di ``...001`` riproducono al bit le righe di produzione.

Altre due, trovate costruendo la vista Community (16/09/2026), entrambe su
``voice`` in ``...002``: il layer che riappare dopo una settimana vuota era
servito come "nessun precedente", mentre il job lo confronta con una partizione
vuota (``node_overlap = 0``); e tre righe avevano ``node_overlap = 0,7`` tra grafi
di 33 e 12 nodi, dove il massimo possibile e' ``min(n)/max(n)``. ``--check``
ora verifica entrambe le cose, e che i campi del confronto siano tutti o nessuno.

**Nessun dato reale, mai.** Gli id sono sintetici e i numeri inventati. Questo
file puo' stare in git; un dump o un export no.

Uso::

    python -m tools.fixture_api --check      # valida e basta, exit 1 se rotto
    uvicorn tools.fixture_api:app --port 8899

Tre guild, tre scenari — si cambia scenario scegliendo la guild, che esercita
anche il selettore multi-guild del flusso di autorizzazione:

===================  =========================================================
``...001`` oggi      lo stato reale della produzione: due snapshot (7 e 14/09),
                     niente di significativo, stabilita' misurata a 7,5 ore
``...002`` maturo    dodici settimane di serie: layer grandi significativi,
                     ``voice`` volatile (assente, soppresso), eccesso negativo,
                     prima osservazione confrontabile significativa (snapshot 29)
``...003`` limite    uno snapshot, ogni riga progettata per rompere una vista
===================  =========================================================
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from api.models import (
    CohortGroup,
    CohortQuality,
    CommunityRow,
    CommunitySizeBucket,
    CommunitySizeValues,
    CommunityValues,
    GuildRow,
    Health,
    OnboardingQuality,
    OnboardingRow,
    OnboardingValues,
    Quality,
    RetentionRow,
    RetentionValues,
    RobustnessRow,
    RobustnessValues,
    RunRow,
)

# job.config e job.cohorts si possono importare, a differenza di
# job.suppression/job.communities: job.cohorts importa solo job.admission e
# job.config, niente igraph ne' leidenalg. Per le coorti il fixture quindi non
# riproduce le regole del job: CHIAMA il job (compute_cohort, compute_retention,
# split_cohorts) sui membri sintetici, e ne ritira l'intera classe di divergenza.
from job.cohorts import CohortMember, compute_cohort, compute_retention, split_cohorts
from job.config import ALL_LAYERS, DEFAULT_PARAMS, MetricParams

# --- compatibilita' pydantic v1/v2 ----------------------------------------
#
# api/models.py dichiara di girare su entrambe le versioni, e la ragione e'
# scritta li': una differenza di versione tra ambiente di sviluppo e droplet non
# deve manifestarsi come una risposta di forma diversa. Questo file ha il
# compito di verificare quel contratto, quindi non puo' essere piu' fragile del
# contratto stesso — usare un'API solo-v2 come TypeAdapter lo renderebbe
# inservibile proprio sull'ambiente dove la divergenza va scoperta.
#
# Nota pratica: se qui gira v1 e sulla droplet v2, questo file parte lo stesso
# ma le fixture che serve NON sono garantite identiche a cio' che l'API produce
# in produzione. Il posto giusto dove risolverlo e' l'ambiente (un venv da
# requirements.txt), non il codice.

_PYDANTIC_V2 = hasattr(BaseModel, "model_validate")


def _as_json(model: BaseModel) -> Any:
    """Il modello come struttura JSON-compatibile, su v1 e su v2."""
    if _PYDANTIC_V2:
        return model.model_dump(mode="json")
    return json.loads(model.json())


def _validate(cls: type[BaseModel], raw: Any) -> BaseModel:
    """Rilegge una struttura con il modello, su v1 e su v2."""
    if _PYDANTIC_V2:
        return cls.model_validate(raw)
    return cls.parse_obj(raw)


def _fields(model: BaseModel) -> dict[str, Any]:
    """I campi del modello come dizionario, su v1 e su v2."""
    return model.model_dump() if _PYDANTIC_V2 else model.dict()


# --- parametri del job: importati, non ricopiati ---------------------------
#
# Una costante ricopiata qui diverge dal job il giorno in cui il job la cambia, e
# nessuno riceve un segnale: le fixture continuano a validare, e descrivono una
# configurazione che la produzione non ha piu'.

PARAMS = MetricParams()
LAYERS = ALL_LAYERS
REMOVAL_FRACTIONS = PARAMS.removal_fractions
COHORT_SCOPES = PARAMS.cohort_layer_scopes
RETENTION_HORIZONS = PARAMS.retention_horizons_days
SIZE_BUCKETS = tuple(label for label, _low, _high in PARAMS.community_size_buckets())
K = PARAMS.k_connections

REASON_BELOW_THRESHOLD = "below_threshold"
REASON_SECONDARY = "secondary"

GUILD_TODAY = 900000000000000001
GUILD_MATURE = 900000000000000002
GUILD_EDGE = 900000000000000003

MONDAY = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _as_of(weeks_ago: int) -> datetime:
    return MONDAY - timedelta(weeks=weeks_ago)


# --- robustezza ------------------------------------------------------------


def nodes_removed_for(n: int, removal_fraction: float) -> int:
    """La formula di ``job/robustness.py``: ``max(1, ceil(X * n))``, mai zero.

    E' per questo che la percentuale mente: a 10 nodi il 5% e il 10% rimuovono lo
    stesso unico nodo. Una versione precedente di questo file usava
    ``max(0, int(n * X))`` e produceva ``nodes_removed = 0``, che il job non
    produce mai.
    """
    return max(1, math.ceil(removal_fraction * n))


def _robustness_layer(
    snapshot_id: int,
    as_of: datetime,
    layer: str,
    *,
    n: Optional[int],
    excess: dict[float, float],
    degenerate: frozenset[float] = frozenset(),
) -> list[RobustnessRow]:
    """Le righe di un layer in uno snapshot, derivate come le deriva il job.

    - ``n is None``: grafo vuoto, **nessuna riga** (``compute_robustness``
      restituisce None: una riga di zeri direbbe "rete perfettamente
      frammentata", che e' un'altra cosa).
    - ``n`` sotto la soglia di pubblicazione: **tutte e tre** le righe
      soppresse. La soglia guarda ``n_effective``, che e' lo stesso per le tre
      frazioni: la soppressione e' per layer, tutto o niente.
    - altrimenti ``nodes_removed``, ragioni e significativita' si ricavano da
      ``n`` e dalla frazione; ``degenerate`` elenca le frazioni con deviazione
      standard del baseline nulla, dove ``targeted_z`` e' None.
    """
    if n is None:
        return []

    rows: list[RobustnessRow] = []
    for rf in REMOVAL_FRACTIONS:
        if n < PARAMS.publish_threshold:
            rows.append(
                RobustnessRow(
                    snapshot_id=snapshot_id,
                    as_of=as_of,
                    layer=layer,
                    removal_fraction=rf,
                    quality=Quality(
                        n_effective=None,
                        suppressed=True,
                        suppression_reason=REASON_BELOW_THRESHOLD,
                        significant=None,
                        details={},
                    ),
                    values=RobustnessValues(),
                )
            )
            continue

        removed = nodes_removed_for(n, rf)
        is_degenerate = rf in degenerate
        value = excess[rf]
        giant_before = 1.0
        # Nessun arrotondamento qui ne' sotto: il job non arrotonda, e un round()
        # nel fixture renderebbe uguali gigante mirato e gigante a caso proprio
        # quando l'eccesso e' minuscolo (-0,0004), cioe' nascondendo la grandezza
        # che la precisione per colonna esiste per mostrare.
        giant_targeted = max(0.0, giant_before - value - 0.12)
        sd = 0.0 if is_degenerate else 0.041

        reasons: list[str] = []
        if n < PARAMS.min_nodes_structural:
            reasons.append("too_few_nodes")
        if removed < 2:
            reasons.append("too_few_nodes_removed")
        if is_degenerate:
            reasons.append("degenerate_baseline")

        details: dict[str, Any] = {"baseline_repetitions_used": 100, "baseline_degraded": False}
        if reasons:
            details["not_significant_because"] = reasons

        rows.append(
            RobustnessRow(
                snapshot_id=snapshot_id,
                as_of=as_of,
                layer=layer,
                removal_fraction=rf,
                quality=Quality(
                    n_effective=n,
                    suppressed=False,
                    suppression_reason=None,
                    significant=not reasons,
                    details=details,
                ),
                values=RobustnessValues(
                    nodes_removed=removed,
                    giant_before=giant_before,
                    giant_after_targeted=giant_targeted,
                    components_after_targeted=max(1, removed * 2),
                    giant_after_random_mean=giant_targeted + value,
                    giant_after_random_sd=sd,
                    components_after_random_mean=round(max(1.0, removed * 1.3), 2),
                    # Puo' essere negativo e non e' clampato. Su una riga
                    # pubblicata non e' mai None: il job lo calcola sempre che il
                    # grafo non sia vuoto.
                    targeted_excess=value,
                    # Senza dispersione lo z non esiste: e' un None che significa
                    # "non calcolabile", non "zero".
                    targeted_z=None if is_degenerate else value / sd,
                ),
            )
        )
    return rows


# --- community -------------------------------------------------------------


@dataclass(frozen=True)
class _Precedente:
    """Lo snapshot con cui si confronta la partizione. Lo stesso per tutti i layer."""

    snapshot_id: int
    as_of: datetime


def _sizes(dimensioni: tuple[int, ...], *, parent_suppressed: bool) -> list[CommunitySizeBucket]:
    """La distribuzione delle dimensioni, costruita come la costruisce il job.

    ``dimensioni`` sono le dimensioni delle singole community (la loro somma e'
    ``n``). Da li':

    - i bucket si ricavano come ``size_buckets()`` (``job/communities.py``): un
      bucket senza community **non esiste**, invece di comparire a zero;
    - con la riga madre soppressa, **tutti** i bucket sono soppressi con
      ``below_threshold`` (``apply_threshold(size, count=None, ...)``);
    - altrimenti primaria sotto ``min_cardinality`` membri, e se c'e' esattamente
      una primaria la secondaria cade sul piu' piccolo dei restanti
      (``apply_secondary_suppression``). La secondaria ARRIVA dall'algoritmo, non
      si piazza a mano.

    ``quality.n_effective`` e' sempre None: ``metric_community_sizes`` non ha
    quella colonna, e ``api/assemble.py`` la serve None in produzione.
    """
    grezzi: list[dict[str, Any]] = []
    for label, low, high in PARAMS.community_size_buckets():
        membri = [d for d in dimensioni if d >= low and (high is None or d < high)]
        if not membri:
            continue
        grezzi.append({
            "bucket": label,
            "community_count": len(membri),
            "member_count": sum(membri),
            "reason": None,
        })

    if parent_suppressed:
        for riga in grezzi:
            riga["reason"] = REASON_BELOW_THRESHOLD
    else:
        for riga in grezzi:
            if riga["member_count"] < PARAMS.min_cardinality:
                riga["reason"] = REASON_BELOW_THRESHOLD
        soppressi = [r for r in grezzi if r["reason"] is not None]
        restanti = [r for r in grezzi if r["reason"] is None]
        if len(soppressi) == 1 and restanti:
            vittima = min(restanti, key=lambda r: (r["member_count"], r["bucket"]))
            vittima["reason"] = REASON_SECONDARY

    out: list[CommunitySizeBucket] = []
    for riga in grezzi:
        soppresso = riga["reason"] is not None
        out.append(
            CommunitySizeBucket(
                bucket=riga["bucket"],
                quality=Quality(
                    n_effective=None,
                    suppressed=soppresso,
                    suppression_reason=riga["reason"],
                    significant=None,
                    details={},
                ),
                values=(
                    CommunitySizeValues()
                    if soppresso
                    else CommunitySizeValues(
                        community_count=riga["community_count"],
                        member_count=riga["member_count"],
                    )
                ),
            )
        )
    return out


def _community_layer(
    snapshot_id: int,
    as_of: datetime,
    layer: str,
    *,
    n: Optional[int],
    dimensioni: tuple[int, ...] = (),
    modularity_z: Optional[float] = None,
    precedente: Optional[_Precedente] = None,
    overlap: Optional[float] = None,
    stability: Optional[float] = None,
    ricomposizione: tuple[int, int, int, int] = (1, 0, 1, 0),
    senza_precedente: str = "no_previous_snapshot",
) -> Optional[CommunityRow]:
    """La riga di community di un layer, derivata come in ``compute_communities``.

    ``n`` e' lo stesso ``n`` della robustezza per lo stesso (snapshot, layer): e'
    ``graph.vcount()`` in entrambe. None = layer vuoto, nessuna riga.

    ``precedente=None`` e' il ramo ``previous is None`` del job: nessun
    precedente confrontabile, per uno dei quattro motivi di
    ``modello-metriche.md`` 4.6 (``senza_precedente`` e' il codice che finisce in
    ``details``). Tutti i campi del confronto restano None e **nessuna reason si
    aggiunge**. Un layer vuoto nel precedente NON passa di qui: nel job la sua
    partizione e' ``{}``, non None, e il confronto si fa con ``overlap=0.0``.
    """
    if n is None:
        return None
    if sum(dimensioni) != n:
        raise ValueError(f"{layer}@{snapshot_id}: dimensioni {dimensioni} non sommano a n={n}")

    if n < PARAMS.publish_threshold:
        return CommunityRow(
            snapshot_id=snapshot_id,
            as_of=as_of,
            layer=layer,
            previous_snapshot_id=None,
            quality=Quality(
                n_effective=None,
                suppressed=True,
                suppression_reason=REASON_BELOW_THRESHOLD,
                significant=None,
                details={},
            ),
            values=CommunityValues(),
            sizes=_sizes(dimensioni, parent_suppressed=True),
        )

    reasons: list[str] = []
    if n < PARAMS.min_nodes_structural:
        reasons.append("too_few_nodes")
    if modularity_z is None:
        reasons.append("degenerate_baseline")
    elif modularity_z < PARAMS.min_modularity_z:
        reasons.append("modularity_indistinguishable_from_random")

    details: dict[str, Any] = {
        "baseline_repetitions_used": 100,
        "baseline_degraded": False,
        "leiden_objective": "modularity",
        "seed": PARAMS.seed,
    }

    gap: Optional[float] = None
    previous_id: Optional[int] = None
    node_overlap: Optional[float] = None
    stability_jaccard: Optional[float] = None
    born = dissolved = merged = split = None
    if precedente is None:
        details["stability_unavailable"] = senza_precedente
    else:
        # La stessa aritmetica del job, arrotondamento compreso.
        gap = round((as_of - precedente.as_of).total_seconds() / 86400.0, 3)
        previous_id = precedente.snapshot_id
        node_overlap = overlap
        born, dissolved, merged, split = ricomposizione
        if overlap is None or overlap < PARAMS.min_node_overlap:
            details["stability_unavailable"] = "node_overlap_below_minimum"
            reasons.append("node_overlap_below_minimum")
        else:
            stability_jaccard = stability

    if reasons:
        details["not_significant_because"] = reasons

    return CommunityRow(
        snapshot_id=snapshot_id,
        as_of=as_of,
        layer=layer,
        previous_snapshot_id=previous_id,
        quality=Quality(
            n_effective=n,
            suppressed=False,
            suppression_reason=None,
            significant=not reasons,
            details=details,
        ),
        values=CommunityValues(
            community_count=len(dimensioni),
            modularity=0.412,
            modularity_random_mean=0.208,
            modularity_random_sd=0.037 if modularity_z is not None else 0.0,
            modularity_z=modularity_z,
            node_overlap=node_overlap,
            stability_jaccard=stability_jaccard,
            previous_gap_days=gap,
            communities_born=born,
            communities_dissolved=dissolved,
            communities_merged=merged,
            communities_split=split,
        ),
        sizes=_sizes(dimensioni, parent_suppressed=False),
    )


def _partizione(n: int) -> tuple[int, ...]:
    """Una partizione plausibile di ``n`` nodi in community da 6 circa.

    Il resto finisce nell'ultima community invece di formarne una sotto i 5
    membri: una community piccola farebbe scattare primaria e secondaria per caso,
    e ogni caso di soppressione della distribuzione deve essere voluto.
    """
    if n < 12:
        return (n,)
    pezzi = [6] * (n // 6)
    pezzi[-1] += n % 6
    return tuple(pezzi)


# --- coorti ----------------------------------------------------------------


@dataclass(frozen=True)
class _Membro:
    """Un membro sintetico, come lo vede ``job.cohorts``: solo date.

    ``raggiunto`` dice in quale ambito e a quale ``as_of`` di snapshot il membro
    ha raggiunto k partner distinti. E' un ``as_of`` e non un istante qualunque
    perche' il job misura il tempo all'evento IN SNAPSHOT: il raggiungimento si
    osserva quando gira uno snapshot, non quando accade.
    """

    joined_at: datetime
    left_at: Optional[datetime] = None
    raggiunto: tuple[tuple[str, datetime], ...] = ()


def _finestre(snapshot_as_of: list[datetime], fino_a: datetime) -> list[tuple[datetime, datetime]]:
    """Le finestre degli snapshot di grafo confrontabili esistenti a ``fino_a``.

    Finestre da ``default_window_days`` che terminano all'``as_of``, come le
    scrive ``job/main.py``. Sono gli snapshot di GRAFO, non i run di metriche:
    una guild puo' avere mesi di snapshot e un solo run, se il layer metriche e'
    arrivato dopo.
    """
    finestra = timedelta(days=DEFAULT_PARAMS.default_window_days)
    inizio_coorti = fino_a - timedelta(days=PARAMS.cohort_max_age_days)
    return [(t - finestra, t) for t in sorted(snapshot_as_of) if inizio_coorti <= t <= fino_a]


def _successivo(snapshot_as_of: list[datetime], dopo: datetime) -> Optional[datetime]:
    """Il primo ``as_of`` di snapshot non precedente a ``dopo``."""
    return next((t for t in sorted(snapshot_as_of) if t >= dopo), None)


def _coorti(
    snapshot_id: int,
    as_of: datetime,
    membri: list[_Membro],
    *,
    ancora: datetime,
    snapshot_as_of: list[datetime],
) -> list[CohortGroup]:
    """I gruppi di coorte di un run, calcolati DAL JOB.

    ``compute_cohort`` e ``compute_retention`` sono le funzioni che il job usa in
    produzione; qui ricevono i membri presenti a ``as_of`` e i raggiungimenti gia'
    osservati a ``as_of``. Resta a carico del fixture solo cio' che sta in
    ``job.suppression`` (non importabile: tira dentro igraph): la soglia su
    ``n_effective``, con ``suppress()`` che azzera ogni campo fuori dalla chiave —
    ``is_computable`` compreso, che su una riga soppressa e' assente, non False.
    """
    presenti = [
        CohortMember(author_id=i, joined_at=m.joined_at, left_at=m.left_at)
        for i, m in enumerate(membri, start=1)
        if m.joined_at <= as_of
    ]
    finestre = _finestre(snapshot_as_of, as_of)
    coorti, esclusi = split_cohorts(presenti, params=PARAMS)
    limite = (as_of - timedelta(days=PARAMS.cohort_max_age_days)).date()
    diagnostica = {"snapshots_used": len(finestre), "snapshots_skipped_params": 0, "snapshot_gaps": None}

    gruppi: list[CohortGroup] = []
    for start in sorted(coorti, reverse=True):
        if start < limite:
            continue  # fuori da cohort_max_age_days: il job non la ricalcola
        membri_coorte = coorti[start]
        onboarding: list[OnboardingRow] = []
        for scope in COHORT_SCOPES:
            raggiunti = {
                i: istante
                for i, m in enumerate(membri, start=1)
                for s, istante in m.raggiunto
                if s == scope and istante <= as_of
            }
            r = compute_cohort(
                cohort_start=start, layer_scope=scope, members=membri_coorte,
                reached_at=raggiunti, excluded_rejoins=esclusi.get(start, 0),
                as_of=as_of, params=PARAMS, observability_anchor=ancora,
                snapshot_windows=finestre, details=diagnostica,
            )
            if r.n_effective < PARAMS.min_cardinality:
                onboarding.append(OnboardingRow(
                    layer_scope=scope, k=r.k,
                    quality=OnboardingQuality(suppressed=True, suppression_reason=REASON_BELOW_THRESHOLD, details={}),
                    values=OnboardingValues(),
                ))
                continue
            onboarding.append(OnboardingRow(
                layer_scope=scope, k=r.k,
                quality=OnboardingQuality(
                    n_effective=r.n_effective, suppressed=False, significant=r.is_significant,
                    is_survivors_only=r.is_survivors_only, excluded_rejoins=r.excluded_rejoins,
                    is_mature=r.is_mature, has_snapshot_coverage=r.has_snapshot_coverage,
                    observation_days=r.observation_days, details=r.details,
                ),
                values=OnboardingValues(
                    event_count=r.event_count, censored_count=r.censored_count,
                    censored_by_leave=r.censored_by_leave, median_days_to_k=r.median_days_to_k,
                    median_reached=r.median_reached, p25_days_to_k=r.p25_days_to_k,
                    p75_days_to_k=r.p75_days_to_k, reached_by_14d=r.reached_by_14d,
                    reached_by_28d=r.reached_by_28d,
                ),
            ))

        retention: list[RetentionRow] = []
        for horizon in RETENTION_HORIZONS:
            t = compute_retention(
                cohort_start=start, horizon_days=horizon, members=membri_coorte,
                excluded_rejoins=esclusi.get(start, 0), as_of=as_of, observability_anchor=ancora,
            )
            if t.n_effective < PARAMS.min_cardinality:
                retention.append(RetentionRow(
                    horizon_days=horizon,
                    quality=CohortQuality(suppressed=True, suppression_reason=REASON_BELOW_THRESHOLD, details={}),
                    values=RetentionValues(),
                ))
                continue
            retention.append(RetentionRow(
                horizon_days=horizon,
                quality=CohortQuality(
                    n_effective=t.n_effective, suppressed=False, significant=None,
                    is_survivors_only=t.is_survivors_only, excluded_rejoins=t.excluded_rejoins,
                ),
                values=RetentionValues(
                    # Non arrotondato: il job non arrotonda (12/14 resta
                    # 0.8571428571428571). L'arrotondamento e' rendering.
                    retained_fraction=t.retained_fraction, is_computable=t.is_computable,
                    not_computable_reason=t.not_computable_reason,
                ),
            ))

        gruppi.append(CohortGroup(
            snapshot_id=snapshot_id, as_of=as_of, cohort_start=start,
            onboarding=onboarding, retention=retention,
        ))
    return gruppi


def _membri_generati(
    inizio: date,
    quanti: int,
    *,
    snapshot_as_of: list[datetime],
    seme: int,
) -> list[_Membro]:
    """Membri sintetici deterministici per le coorti degli scenari senza produzione.

    Ingressi distribuiti nella settimana, qualche uscita precoce (dentro i 7
    giorni: pesa sulla retention e su ``censored_by_leave``), raggiungimento di k
    per circa due membri su tre in ``any`` e uno su tre in ``voice``, osservato al
    primo snapshot utile. I VALORI li calcola il job; qui si scelgono solo i fatti.
    """
    base = datetime.combine(inizio, datetime.min.time(), tzinfo=timezone.utc)
    membri: list[_Membro] = []
    for i in range(quanti):
        joined = base + timedelta(hours=3 + (i * 37 + seme * 11) % 160)
        left = joined + timedelta(days=4) if (i + seme) % 7 == 3 else None
        raggiunto: list[tuple[str, datetime]] = []
        if left is None and i % 3 != 0:
            giorni_any = 3 + (i * 5 + seme) % 17
            quando = _successivo(snapshot_as_of, joined + timedelta(days=giorni_any))
            if quando is not None:
                raggiunto.append(("any", quando))
                if i % 3 == 1:
                    # 'any' e' l'unione degli ambiti: chi raggiunge k in voice lo
                    # ha raggiunto in any non piu' tardi.
                    giorni_voice = max(giorni_any, 9 + (i * 3 + seme) % 13)
                    quando_voice = _successivo(snapshot_as_of, joined + timedelta(days=giorni_voice))
                    if quando_voice is not None:
                        raggiunto.append(("voice", quando_voice))
        membri.append(_Membro(joined, left, tuple(raggiunto)))
    return membri


# --- gli scenari -----------------------------------------------------------


def _verbatim_robustness(
    snapshot_id: int, as_of: datetime, layer: str, righe: tuple[tuple[Any, ...], ...]
) -> list[RobustnessRow]:
    """Righe di ``metric_robustness`` copiate dalla produzione, non derivate.

    Ogni tupla: ``(removal_fraction, n_effective, nodes_removed, giant_before,
    giant_after_targeted, components_after_targeted, giant_after_random_mean,
    giant_after_random_sd, components_after_random_mean, targeted_excess,
    targeted_z, not_significant_because)``. Tutte pubblicate e non significative
    su ``...001``; i ``details`` costanti sono quelli che il job scrive oggi.
    """
    out: list[RobustnessRow] = []
    for rf, n, removed, gb, gt, ct, grm, grs, crm, excess, z, motivi in righe:
        out.append(RobustnessRow(
            snapshot_id=snapshot_id, as_of=as_of, layer=layer, removal_fraction=rf,
            quality=Quality(
                n_effective=n, suppressed=False, suppression_reason=None, significant=False,
                details={
                    "baseline_degraded": False,
                    "without_reconciled": {"identical": True},
                    "not_significant_because": list(motivi),
                    "baseline_repetitions_used": 100,
                },
            ),
            values=RobustnessValues(
                nodes_removed=removed, giant_before=gb, giant_after_targeted=gt,
                components_after_targeted=ct, giant_after_random_mean=grm,
                giant_after_random_sd=grs, components_after_random_mean=crm,
                targeted_excess=excess, targeted_z=z,
            ),
        ))
    return out


def _verbatim_bucket(bucket: str, conteggi: Optional[tuple[int, int]], motivo: Optional[str]) -> CommunitySizeBucket:
    """Un bucket di ``metric_community_sizes`` copiato dalla produzione."""
    soppresso = motivo is not None
    return CommunitySizeBucket(
        bucket=bucket,
        quality=Quality(n_effective=None, suppressed=soppresso, suppression_reason=motivo,
                        significant=None, details={}),
        values=(CommunitySizeValues() if conteggi is None
                else CommunitySizeValues(community_count=conteggi[0], member_count=conteggi[1])),
    )


def _scenario_today() -> dict[str, Any]:
    """Lo stato reale della produzione: due snapshot, niente di significativo.

    **Robustezza, community, bucket e run sono copiati verbatim dalla droplet**
    (query del 15/09/2026, dopo il rerun delle metriche su entrambi gli snapshot
    con ``voice_structural_min_sessions = 2``, immagine ``9d0dc98``). Non passano
    da ``_robustness_layer``/``_partizione``/``_sizes``, che ricavano i valori da
    parametri inventati. Vanno ricopiati se cambia una regola del grafo delle
    metriche (``voice_structural_min_sessions``, ``min_edge_weight``, ...):
    ``--check`` non se ne accorgerebbe, perche' questo file non costruisce grafi
    (CLAUDE.md 7).

    - **Snapshot 11**, ``as_of`` 07/09 04:15:06 UTC — non mezzanotte: e' il valore
      preso da ``now()`` prima dell'ancoraggio al lunedi'. Nessun precedente: lo
      snapshot 10 e' stato cancellato, e il rerun ha riscritto le community di
      tutti e quattro i layer con ``no_previous_snapshot``.
    - **Snapshot 12**, ``as_of`` 14/09 00:00 UTC, il primo ancorato. Distanza
      dall'11: 6,823 giorni, non 7. Stabilita' calcolata solo su ``voice``: gli
      altri tre layer hanno ``node_overlap`` sotto il minimo.
    """
    as_of_11 = datetime(2026, 9, 7, 4, 15, 6, 13734, tzinfo=timezone.utc)
    as_of_12 = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    as_of_per_sid = {11: as_of_11, 12: as_of_12}

    solo_nodi = ("too_few_nodes",)
    nodi_e_rimossi = ("too_few_nodes", "too_few_nodes_removed")
    # (sid, layer) -> righe di metric_robustness, in ordine di removal_fraction.
    robustezza_reale: dict[tuple[int, str], tuple[tuple[Any, ...], ...]] = {
        (11, "mention"): (
            (0.05, 26, 2, 0.8846153846153846, 0.4230769230769231, 12, 0.8, 0.04070386632407063, 2.44, 0.4260869565217392, 9.260129588726068, solo_nodi),
            (0.10, 26, 3, 0.8846153846153846, 0.38461538461538464, 12, 0.7496153846153847, 0.0673889619251485, 2.8, 0.412608695652174, 5.416317295485565, solo_nodi),
            (0.20, 26, 6, 0.8846153846153846, 0.11538461538461539, 16, 0.6342307692307692, 0.07663310731070921, 3.2, 0.5865217391304347, 6.770522194049761, solo_nodi),
        ),
        (11, "reaction"): (
            (0.05, 24, 2, 1.0, 0.7083333333333334, 6, 0.88625, 0.05829016259675001, 1.73, 0.1779166666666666, 3.0522588845306537, solo_nodi),
            (0.10, 24, 3, 1.0, 0.4583333333333333, 11, 0.8245833333333333, 0.07506825597784696, 2.21, 0.36625, 4.878893151694937, solo_nodi),
            (0.20, 24, 5, 1.0, 0.20833333333333334, 15, 0.7004166666666667, 0.09766279144757911, 3.19, 0.4920833333333333, 5.038595825898146, solo_nodi),
        ),
        (11, "reply"): (
            (0.05, 24, 2, 0.7916666666666666, 0.4166666666666667, 10, 0.72375, 0.026780356607035677, 3.13, 0.38789473684210524, 11.466738021429373, solo_nodi),
            (0.10, 24, 3, 0.7916666666666666, 0.375, 10, 0.6833333333333332, 0.041247895569215286, 3.23, 0.3894736842105262, 7.475128829686355, solo_nodi),
            (0.20, 24, 5, 0.7916666666666666, 0.16666666666666666, 13, 0.6145833333333334, 0.04836744485934957, 3.25, 0.5657894736842106, 9.260705583459886, solo_nodi),
        ),
        (11, "voice"): (
            (0.05, 9, 1, 1.0, 0.7777777777777778, 2, 0.8755555555555555, 0.03610683735393758, 1.12, 0.09777777777777774, 2.7080128015453204, nodi_e_rimossi),
            (0.10, 9, 1, 1.0, 0.7777777777777778, 2, 0.8722222222222221, 0.03967460238079359, 1.15, 0.09444444444444433, 2.3804761428476153, nodi_e_rimossi),
            (0.20, 9, 2, 1.0, 0.6666666666666666, 2, 0.7577777777777777, 0.04268749491621901, 1.18, 0.09111111111111103, 2.134374745810947, solo_nodi),
        ),
        (12, "mention"): (
            (0.05, 29, 2, 0.9310344827586207, 0.6551724137931034, 6, 0.8272413793103448, 0.0563442366912839, 2.9, 0.18481481481481482, 3.053887595638675, solo_nodi),
            (0.10, 29, 3, 0.9310344827586207, 0.27586206896551724, 10, 0.7841379310344827, 0.06572019382455968, 3.23, 0.5459259259259259, 7.733937356085861, solo_nodi),
            (0.20, 29, 6, 0.9310344827586207, 0.06896551724137931, 20, 0.6424137931034483, 0.08511794991854137, 4.23, 0.6159259259259259, 6.737101591507597, solo_nodi),
        ),
        (12, "reaction"): (
            (0.05, 25, 2, 1.0, 0.64, 7, 0.8732000000000001, 0.06300603145731369, 2.12, 0.23320000000000007, 3.701232955101958, solo_nodi),
            (0.10, 25, 3, 1.0, 0.4, 11, 0.8151999999999999, 0.07731080131521079, 2.49, 0.4151999999999999, 5.37053028731588, solo_nodi),
            (0.20, 25, 5, 1.0, 0.2, 14, 0.7048000000000001, 0.08481131999916049, 3.23, 0.5048000000000001, 5.952035648130426, solo_nodi),
        ),
        (12, "reply"): (
            (0.05, 23, 2, 0.9130434782608695, 0.6086956521739131, 5, 0.792608695652174, 0.05860863114433297, 2.81, 0.20142857142857146, 3.137985649679244, solo_nodi),
            (0.10, 23, 3, 0.9130434782608695, 0.34782608695652173, 9, 0.7265217391304348, 0.06457509805819077, 3.33, 0.41476190476190483, 5.864422409899523, solo_nodi),
            (0.20, 23, 5, 0.9130434782608695, 0.08695652173913043, 14, 0.6069565217391304, 0.1164892881483449, 4.03, 0.5695238095238095, 4.46392975925648, solo_nodi),
        ),
        (12, "voice"): (
            (0.05, 15, 1, 1.0, 0.9333333333333333, 1, 0.9293333333333333, 0.015832456116050553, 1.06, -0.0040000000000000036, -0.252645576319956, nodi_e_rimossi),
            (0.10, 15, 2, 1.0, 0.8, 2, 0.8513333333333334, 0.05321236280748635, 1.16, 0.05133333333333334, 0.9646881030081106, solo_nodi),
            (0.20, 15, 3, 1.0, 0.4666666666666667, 3, 0.764, 0.07625687582842032, 1.39, 0.29733333333333334, 3.899101951178016, solo_nodi),
        ),
    }

    # (sid, layer) -> (n, community_count, modularity, random_mean, random_sd, z,
    #                  node_overlap, stability_jaccard, (nate, dissolte, fuse, scisse),
    #                  not_significant_because)
    indistinguibile = "modularity_indistinguishable_from_random"
    sovrapposizione = "node_overlap_below_minimum"
    community_reali: dict[tuple[int, str], tuple[Any, ...]] = {
        (11, "mention"): (26, 4, 0.16389004581424416, 0.18612036651395258, 0.022042254467687428, -1.0085320778914286,
                          None, None, None, ("too_few_nodes", indistinguibile)),
        (11, "reaction"): (24, 3, 0.3149910767400356, 0.2464306960142772, 0.027374280569744697, 2.5045546147260027,
                           None, None, None, ("too_few_nodes",)),
        (11, "reply"): (24, 4, 0.17107750472589803, 0.18743856332703213, 0.023810439486219352, -0.6871380350036507,
                        None, None, None, ("too_few_nodes", indistinguibile)),
        (11, "voice"): (9, 3, 0.04475308641975306, 0.04486111111111117, 0.04199473894423033, -0.002572338680365804,
                        None, None, None, ("too_few_nodes", indistinguibile)),
        (12, "mention"): (29, 5, 0.4310941828254848, 0.4260595567867036, 0.026904517695240368, 0.18712939201552214,
                          0.1956521739130435, None, (1, 0, 0, 0), ("too_few_nodes", indistinguibile, sovrapposizione)),
        (12, "reaction"): (25, 4, 0.39554419284149006, 0.35063550036523006, 0.023517126960165585, 1.9096164489960215,
                           0.3611111111111111, None, (1, 0, 0, 0), ("too_few_nodes", indistinguibile, sovrapposizione)),
        (12, "reply"): (23, 5, 0.40368608799048755, 0.41120689655172415, 0.026865452977589896, -0.2799434860640602,
                        0.23684210526315788, None, (1, 0, 0, 0), ("too_few_nodes", indistinguibile, sovrapposizione)),
        (12, "voice"): (15, 2, 0.2221074380165289, 0.07289772727272725, 0.026530225458777885, 5.6241403215981185,
                        0.5, 0.3333333333333333, (0, 0, 1, 0), ("too_few_nodes",)),
    }

    # (sid, layer) -> bucket nell'ordine in cui li serve l'API (ORDER BY bucket):
    # (bucket, (community_count, member_count) o None se soppresso, motivo).
    bucket_reali: dict[tuple[int, str], tuple[tuple[str, Optional[tuple[int, int]], Optional[str]], ...]] = {
        (11, "mention"): (("10-19", (1, 18), None), ("small", (3, 8), None)),
        (11, "reaction"): (("10-19", (1, 12), None), ("5-9", None, REASON_SECONDARY),
                           ("small", None, REASON_BELOW_THRESHOLD)),
        (11, "reply"): (("10-19", (1, 16), None), ("small", (3, 8), None)),
        (11, "voice"): (("5-9", None, REASON_SECONDARY), ("small", None, REASON_BELOW_THRESHOLD)),
        (12, "mention"): (("10-19", None, REASON_SECONDARY), ("5-9", (3, 17), None),
                          ("small", None, REASON_BELOW_THRESHOLD)),
        (12, "reaction"): (("5-9", (4, 25), None),),
        (12, "reply"): (("5-9", (2, 15), None), ("small", (3, 8), None)),
        (12, "voice"): (("5-9", (2, 15), None),),
    }

    robustness: list[RobustnessRow] = []
    communities: list[CommunityRow] = []
    for (sid, layer), righe in robustezza_reale.items():
        robustness += _verbatim_robustness(sid, as_of_per_sid[sid], layer, righe)
    for (sid, layer), (n, count, mod, mod_mean, mod_sd, z, overlap, stability, ricomp, motivi) in community_reali.items():
        details: dict[str, Any] = {
            "seed": PARAMS.seed,
            "leiden_objective": PARAMS.leiden_objective,
            "baseline_degraded": False,
            "without_reconciled": {"identical": True},
            "not_significant_because": list(motivi),
            "baseline_repetitions_used": 100,
        }
        if sid == 11:
            details["stability_unavailable"] = "no_previous_snapshot"
        elif stability is None:
            details["stability_unavailable"] = sovrapposizione
        born, dissolved, merged, split = ricomp if ricomp is not None else (None, None, None, None)
        communities.append(CommunityRow(
            snapshot_id=sid, as_of=as_of_per_sid[sid], layer=layer,
            previous_snapshot_id=None if sid == 11 else 11,
            quality=Quality(n_effective=n, suppressed=False, suppression_reason=None,
                            significant=False, details=details),
            values=CommunityValues(
                community_count=count, modularity=mod, modularity_random_mean=mod_mean,
                modularity_random_sd=mod_sd, modularity_z=z, node_overlap=overlap,
                stability_jaccard=stability,
                previous_gap_days=None if sid == 11 else 6.823,
                communities_born=born, communities_dissolved=dissolved,
                communities_merged=merged, communities_split=split,
            ),
            sizes=[_verbatim_bucket(*b) for b in bucket_reali[(sid, layer)]],
        ))

    # Coorti: i membri sono scelti perche' compute_cohort riproduca ESATTAMENTE
    # le righe di produzione (query sulla droplet, 14/09/2026). Nessun valore e'
    # scritto a mano: n, observation_days, maturita', copertura, eventi,
    # censure, quantili e retention li calcola il job da questi fatti.
    u = timezone.utc
    membri = [
        # 10/08: tre membri, sotto soglia. Soppressa, is_computable compreso.
        *(_Membro(datetime(2026, 8, g, 10, tzinfo=u)) for g in (10, 12, 15)),
        # 17/08: otto, anteriore all'ancora. Nessuna uscita, nessuno a k.
        # reached_by_14d = 0 (orizzonte passato, nessuno a k) contro
        # reached_by_28d = None (orizzonte non passato): due cose diverse.
        *(_Membro(datetime(2026, 8, g, h, tzinfo=u))
          for g, h in ((17, 9), (18, 11), (19, 8), (19, 19), (20, 13), (21, 10), (22, 7), (22, 20))),
        # 31/08: quattordici. Un solo evento, in 'any' e non in 'voice', osservato
        # allo snapshot 12: p25 = 11.157546418090279 al microsecondo. Le due
        # uscite entro 7 giorni sono le stesse due di censored_by_leave e del
        # 12/14 della retention a 7 giorni.
        _Membro(datetime(2026, 9, 2, 20, 13, 7, 989477, tzinfo=u), raggiunto=(("any", as_of_12),)),
        *(_Membro(datetime(2026, m, g, h, tzinfo=u)) for m, g, h in ((8, 31, 6), (9, 1, 9), (9, 2, 8))),
        _Membro(datetime(2026, 8, 31, 12, tzinfo=u), left_at=datetime(2026, 9, 2, 12, tzinfo=u)),
        _Membro(datetime(2026, 9, 1, 15, tzinfo=u), left_at=datetime(2026, 9, 4, 15, tzinfo=u)),
        *(_Membro(datetime(2026, 9, g, h, tzinfo=u))
          for g, h in ((3, 8), (3, 17), (4, 9), (4, 21), (5, 10), (5, 16), (6, 7), (6, 10))),
        # 07/09: otto, esiste solo allo snapshot 12. Un evento su otto e mediana
        # RAGGIUNTA a 4,58 giorni: i membri osservati meno a lungo escono dal
        # gruppo a rischio prima dell'evento, e uno su due rimasti porta S a 0,5.
        _Membro(datetime(2026, 9, 9, 10, 1, 13, 46041, tzinfo=u),
                raggiunto=(("any", as_of_12), ("voice", as_of_12))),
        _Membro(datetime(2026, 9, 8, 9, tzinfo=u)),
        _Membro(datetime(2026, 9, 7, 12, tzinfo=u), left_at=datetime(2026, 9, 9, 12, tzinfo=u)),
        *(_Membro(datetime(2026, 9, g, h, tzinfo=u)) for g, h in ((10, 8), (11, 9), (11, 18), (12, 7), (12, 20))),
    ]
    ancora = datetime(2026, 8, 28, 15, 28, tzinfo=timezone.utc)
    # Lo snapshot 10 non entra nelle finestre: in produzione la copertura della
    # coorte del 17/08 e' falsa anche allo snapshot 11, quindi non era
    # confrontabile (o non c'era piu') quando l'11 ha girato.
    snapshot_grafo = [as_of_11, as_of_12]
    cohorts = (
        _coorti(12, as_of_12, membri, ancora=ancora, snapshot_as_of=snapshot_grafo)
        + _coorti(11, as_of_11, membri, ancora=ancora, snapshot_as_of=snapshot_grafo)
    )

    # I params li produce il codice di 9d0dc98, cioe' as_run_params(): e' la
    # stessa funzione che li ha scritti in produzione, non una copia a mano.
    params = PARAMS.as_run_params()
    soglie = {"min_cardinality": PARAMS.min_cardinality, "min_nodes_publish": PARAMS.publish_threshold}
    return {
        "guild": GuildRow(
            guild_id=GUILD_TODAY,
            first_seen_at=ancora,
            backfilled_at=datetime(2026, 8, 28, 15, 31, tzinfo=timezone.utc),
            left_at=None, rejoined_at=None, latest_metrics_as_of=as_of_12,
        ),
        "runs": [
            RunRow(
                snapshot_id=12, as_of=as_of_12, params=params,
                stats={"durations_ms": {"cohorts_ms": 3.6, "robustness_ms": 163.7,
                                        "communities_ms": 611.1, "structural_total_ms": 778.6},
                       "snapshot_gaps": {"max_gap_days": 6.822847063263889,
                                         "cadence_days_median": 6.822847063263889},
                       "snapshots_used": 2, "params_thresholds": soglie,
                       "snapshots_skipped_params": 0},
                code_version="9d0dc98",
                created_at=datetime(2026, 9, 15, 9, 52, 47, 737959, tzinfo=timezone.utc),
            ),
            RunRow(
                snapshot_id=11, as_of=as_of_11, params=params,
                stats={"durations_ms": {"cohorts_ms": 4.6, "robustness_ms": 182.0,
                                        "communities_ms": 586.5, "structural_total_ms": 773.1},
                       "snapshot_gaps": None,
                       "snapshots_used": 1, "params_thresholds": soglie,
                       "snapshots_skipped_params": 0},
                code_version="9d0dc98",
                created_at=datetime(2026, 9, 15, 9, 52, 40, 132957, tzinfo=timezone.utc),
            ),
        ],
        "robustness": robustness,
        "communities": communities,
        "cohorts": cohorts,
    }


# Oscillazione deterministica della serie: senza, dodici punti perfettamente
# monotoni sono il grafico meno impegnativo immaginabile.
_ZIGZAG = (0.012, -0.008, 0.015, -0.011, 0.004, -0.014, 0.009, -0.006, 0.013, -0.010, 0.007, -0.003)


def _scenario_mature() -> dict[str, Any]:
    """Dodici settimane di serie.

    ``reply``, ``mention`` e ``reaction`` sono grandi e significativi. ``voice`` e'
    il layer a basso traffico di una community matura con un canale vocale quasi
    spento: ``n`` piccolo e volatile, e per questo

    - **assente nell'ultimo snapshot** (i=0): la frase del blocco nella tabella;
    - **assente in uno snapshot intermedio** (i=5): l'interruzione della linea;
    - **soppresso** in un altro (i=7, n=3): il punto non disegnato;
    - **significativo** una volta sola (i=9, n=33): con il resto non
      significativo fa del grafico di ``voice`` il caso misto. Con ``limit=4``,
      invece, ``voice`` ha tre punti tutti non significativi.

    L'eccesso negativo sta su ``reply``, i=6, al 5%: un layer grande, dove e' un
    risultato e non rumore.

    Community, due casi del confronto tra partizioni:

    - **snapshot 29** (i=11, il piu' vecchio): prima osservazione confrontabile.
      Lo snapshot di grafo precedente esiste (le coorti ne usano da marzo) ma e'
      stato scritto con parametri diversi: ``params_differ``, e
      ``snapshots_used = 1`` sul run dice la stessa cosa. Nessun campo del
      confronto, e ``reply``/``mention``/``reaction`` sono comunque
      ``is_significant = true`` (n >= 30, z >= 2,0): nessun precedente non e' un
      motivo di non significativita' (modello-metriche.md 4.6);
    - **``voice`` che riappare** (i=4, dopo l'assenza di i=5): il precedente c'e'
      e il layer li' era vuoto. Il job confronta con una partizione ``{}``, non
      con None: ``node_overlap = 0``, tutte le community nate, e la riga non e'
      significativa per ``node_overlap_below_minimum``. E' la stabilita' assente
      su una riga pubblicata, in mezzo alla serie.
    """
    runs: list[RunRow] = []
    robustness: list[RobustnessRow] = []
    communities: list[CommunityRow] = []
    cohorts: list[CohortGroup] = []

    # i=10 a 30 nodi e non piu' a 12: la settimana significativa (i=9, 33 nodi)
    # deve avere un precedente di dimensione compatibile, altrimenti un
    # node_overlap >= 0,50 con cui calcolarne la stabilita' e' impossibile.
    n_voice = {0: None, 1: 12, 2: 9, 3: 11, 4: 14, 5: None, 6: 10, 7: 3, 8: 13, 9: 33, 10: 30, 11: 15}
    base = {"voice": 0.05, "reply": 0.22, "mention": 0.18, "reaction": 0.26}

    for i in range(12):
        sid = 40 - i
        as_of = _as_of(i)
        # Il piu' vecchio dei dodici non ha un precedente confrontabile (docstring).
        precedente = None if i == 11 else _Precedente(sid - 1, _as_of(i + 1))
        runs.append(
            RunRow(
                snapshot_id=sid, as_of=as_of,
                params={"min_cardinality": 5, "k_connections": 5, "min_nodes_structural": 30},
                stats={"durations_ms": {"robustness": 9100 + i * 40, "communities": 5200,
                                        "cohorts": 1400}, "snapshots_used": 12 - i},
                code_version="dfc1696",
                created_at=as_of + timedelta(hours=4, minutes=15),
            )
        )
        n_by_layer = {
            "voice": n_voice[i], "reply": 88 - i, "mention": 91 - i, "reaction": 79 - i,
        }
        for layer in LAYERS:
            n = n_by_layer[layer]
            fattore = 2.0 if layer == "voice" else 1.0
            excess = {
                rf: round(base[layer] + rf * 0.9 + _ZIGZAG[i] * fattore, 3)
                for rf in REMOVAL_FRACTIONS
            }
            if layer == "reply" and i == 6:
                excess[0.05] = -0.03
            robustness += _robustness_layer(sid, as_of, layer, n=n, excess=excess)

            if layer == "voice":
                dimensioni = _partizione(n) if n else ()
                if precedente is not None and n_voice[i + 1] is None:
                    # Nel precedente il layer era vuoto: il job confronta con la
                    # partizione {} (job/main.py::_previous_partition), non con
                    # None. Nucleo comune vuoto: overlap 0, ogni community nata.
                    overlap_voice, ricomposizione = 0.0, (len(dimensioni), 0, 0, 0)
                elif precedente is not None and n:
                    # node_overlap = |nucleo| / |unione| <= min(n)/max(n): su un
                    # layer che salta da 13 a 33 nodi 0,7 non esiste. Sotto il
                    # tetto si scende sotto il minimo, e la stabilita' non si calcola.
                    tetto = min(n, n_voice[i + 1]) / max(n, n_voice[i + 1])
                    overlap_voice = 0.7 if tetto >= 0.7 else round(tetto * 0.8, 3)
                    ricomposizione = (1, 0, 1, 0)
                else:
                    overlap_voice, ricomposizione = None, (1, 0, 1, 0)
                row = _community_layer(
                    sid, as_of, layer, n=n, dimensioni=dimensioni,
                    modularity_z=2.6 if (n or 0) >= PARAMS.min_nodes_structural else 1.4,
                    precedente=precedente, overlap=overlap_voice, stability=0.55,
                    ricomposizione=ricomposizione, senza_precedente="params_differ",
                )
            else:
                row = _community_layer(
                    sid, as_of, layer, n=n, dimensioni=_partizione(n),
                    modularity_z=round(5.5 - i * 0.1, 2), precedente=precedente,
                    overlap=0.88, stability=round(0.74 - i * 0.008 + _ZIGZAG[i] / 2, 3),
                    senza_precedente="params_differ",
                )
            if row is not None:
                communities.append(row)

    # Coorti su OGNI run, calcolate dal job su membri generati. Snapshot di grafo
    # settimanali da marzo: le coorti hanno copertura, e con il tempo maturano.
    ancora = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    snapshot_grafo = [datetime(2026, 3, 9, tzinfo=timezone.utc) + timedelta(weeks=w) for w in range(27)]
    membri: list[_Membro] = []
    for j, inizio in enumerate(date(2026, 6, 1) + timedelta(weeks=2 * k) for k in range(7)):
        membri += _membri_generati(inizio, 18 + j, snapshot_as_of=snapshot_grafo, seme=j)
    for run in runs:
        cohorts += _coorti(run.snapshot_id, run.as_of, membri, ancora=ancora, snapshot_as_of=snapshot_grafo)

    return {
        "guild": GuildRow(
            guild_id=GUILD_MATURE,
            first_seen_at=ancora,
            backfilled_at=datetime(2026, 3, 2, 9, 4, tzinfo=timezone.utc),
            left_at=None, rejoined_at=None, latest_metrics_as_of=MONDAY,
        ),
        "runs": runs,
        "robustness": robustness,
        "communities": communities,
        "cohorts": cohorts,
    }


def _scenario_edge() -> dict[str, Any]:
    """Gli stati che mordono. Uno snapshot, ogni riga esiste per rompere una vista.

    Robustezza e community condividono ``n`` per (snapshot, layer), come nel job:

    ============  ====  ======================================  ==========================================
    layer         n     robustezza                              community
    ============  ====  ======================================  ==========================================
    ``voice``     61    riga normale + eccesso NEGATIVO         stabilita' 0,97 su un gap di 1,2 giorni
    ``reply``     3     blocco interamente soppresso            riga soppressa e tutti i bucket soppressi
    ``mention``   10    5% e 10% rimuovono lo stesso nodo;      node_overlap sotto il minimo: stabilita'
                        baseline degenere al 20%                NULL, ricomposizione presente
    ``reaction``  44    significative; -0,0004 al 5% (colonna   modularity_z sotto 2,0 con nodi a
                        a 4 decimali)
                                                                sufficienza + soppressione secondaria
    ============  ====  ======================================  ==========================================

    Il gap di 1,2 giorni vale per TUTTI i layer: lo snapshot precedente e' uno solo
    per snapshot, e il job confronta ogni layer con quello.

    Per la stessa ragione qui non c'e' un quinto caso "prima osservazione
    confrontabile, significativa": ``previous is None`` vale per l'intero snapshot,
    quindi in uno scenario da uno snapshot solo toglierebbe il gap anomalo a tutti
    e quattro i layer. Il caso sta in ``...002``, snapshot 29.

    Coorti, calcolate dal job: 13/04 di soli sopravvissuti (anteriore all'ancora
    del 20/04 e dentro i 180 giorni), 10/08 significativa con mediana non
    raggiunta, 31/08 interamente soppressa.

    Se la dashboard rende questo scenario senza far sembrare un errore niente
    che errore non e', e senza far sembrare un valore niente che valore non e',
    allora regge anche i dati veri quando arriveranno.
    """
    as_of = MONDAY
    sid = 77
    precedente = _Precedente(76, as_of - timedelta(days=1.2))

    robustness = (
        # targeted_excess NEGATIVO e non clampato: i nodi centrali erano MENO
        # critici di nodi presi a caso. Un asse y ancorato a zero lo nasconde.
        _robustness_layer(sid, as_of, "voice", n=61,
                          excess={0.05: 0.19, 0.10: -0.07, 0.20: 0.31})
        # n sotto la soglia: tutte e tre le righe soppresse, values tutti None.
        + _robustness_layer(sid, as_of, "reply", n=3, excess={})
        # A 10 nodi il 5% e il 10% rimuovono lo stesso unico nodo. Al 20% il
        # baseline e' degenere: targeted_z None, non zero.
        + _robustness_layer(sid, as_of, "mention", n=10,
                            excess={0.05: 0.12, 0.10: 0.115, 0.20: 0.21},
                            degenerate=frozenset({0.20}))
        # -0.0004 al 5%: per test di rendering a 4 decimali, valore sintetico non
        # da produzione — prima viveva su ...001, spostato il 15/09/2026 quando
        # ...001 e' stato risincronizzato con la produzione reale. Uno solo
        # nella colonna basta: la precisione si decide sulla colonna, e le altre
        # due righe devono salire a 4 decimali con lui.
        + _robustness_layer(sid, as_of, "reaction", n=44,
                            excess={0.05: -0.0004, 0.10: 0.33, 0.20: 0.44})
    )

    communities = [
        # Gap anomalo: 1.2 giorni significa finestre sovrapposte all'85-95%, e
        # stability_jaccard misura la sovrapposizione, non la ricomposizione.
        _community_layer(sid, as_of, "voice", n=61, dimensioni=(22, 18, 12, 9),
                         modularity_z=5.51, precedente=precedente,
                         overlap=0.94, stability=0.97),
        # Riga madre soppressa: tutti i bucket soppressi, below_threshold.
        _community_layer(sid, as_of, "reply", n=3, dimensioni=(3,)),
        # Due community da 5: un solo bucket 5-9, nessuna cascata. La
        # sovrapposizione di nodi e' sotto il minimo: la stabilita' non si
        # calcola, ma born/dissolved/merged/split ci sono lo stesso.
        _community_layer(sid, as_of, "mention", n=10, dimensioni=(5, 5),
                         modularity_z=2.4, precedente=precedente,
                         overlap=0.21, ricomposizione=(2, 1, 0, 1)),
        # Modularita' indistinguibile dal caso con 44 nodi: significativa in
        # robustezza e non nelle community. Una community da 3 fa scattare la
        # primaria sul bucket small; essendo una sola, la secondaria cade sul
        # piu' piccolo dei restanti (5-9).
        _community_layer(sid, as_of, "reaction", n=44, dimensioni=(3, 6, 12, 23),
                         modularity_z=1.22, precedente=precedente,
                         overlap=0.86, stability=0.71),
    ]

    # Coorti, calcolate dal job. L'ancora e' il 20/04: una coorte di soli
    # sopravvissuti deve essere anteriore all'ancora E dentro cohort_max_age_days
    # (dal 7/09, non prima dell'11/03), altrimenti il job non la calcola.
    #
    # db.fetch_observability_anchor usa solo first_seen_at: il buco di
    # osservazione (left_at/rejoined_at) non sposta l'ancora. Il fixture segue il
    # codice; la divergenza con modello-metriche.md 5.6 e' un follow-up in
    # CLAUDE.md, non una scelta di questo file.
    u = timezone.utc
    ancora = datetime(2026, 4, 20, 11, 0, tzinfo=u)
    # Snapshot di grafo: uno prima dell'uscita, nessuno durante il buco, poi
    # settimanali dal rientro, e il 76 a 1,2 giorni dal 77.
    snapshot_grafo = (
        [datetime(2026, 4, 27, tzinfo=u)]
        + [datetime(2026, 6, 22, tzinfo=u) + timedelta(weeks=w) for w in range(11)]
        + [precedente.as_of, as_of]
    )
    membri = [
        # 13/04: nove membri entrati prima dell'arrivo del bot, dentro i 180
        # giorni. Soli sopravvissuti: numeri mostrabili, persone sbagliate.
        *(_Membro(datetime(2026, 4, 13, 4, tzinfo=u) + timedelta(hours=17 * i),
                  raggiunto=(("any", datetime(2026, 4, 27, tzinfo=u)),) if i % 2 == 0 else ())
          for i in range(9)),
        # 10/08: undici membri, matura, coperta, posteriore all'ancora:
        # significativa. Tre a k su undici, tre uscite: la mediana NON e'
        # raggiunta mentre il resto della riga e' valido.
        _Membro(datetime(2026, 8, 10, 9, tzinfo=u), raggiunto=(("any", datetime(2026, 8, 24, tzinfo=u)),
                                                               ("voice", datetime(2026, 8, 31, tzinfo=u)))),
        _Membro(datetime(2026, 8, 11, 14, tzinfo=u), raggiunto=(("any", datetime(2026, 8, 24, tzinfo=u)),)),
        _Membro(datetime(2026, 8, 12, 10, tzinfo=u), raggiunto=(("any", datetime(2026, 8, 31, tzinfo=u)),)),
        _Membro(datetime(2026, 8, 10, 16, tzinfo=u), left_at=datetime(2026, 8, 14, 16, tzinfo=u)),
        _Membro(datetime(2026, 8, 13, 8, tzinfo=u), left_at=datetime(2026, 8, 17, 8, tzinfo=u)),
        _Membro(datetime(2026, 8, 11, 20, tzinfo=u), left_at=datetime(2026, 8, 21, 20, tzinfo=u)),
        *(_Membro(datetime(2026, 8, g, h, tzinfo=u)) for g, h in ((12, 18), (13, 21), (14, 11), (15, 9), (16, 18))),
        # 31/08: tre membri. Coorte interamente soppressa, onboarding e retention.
        _Membro(datetime(2026, 8, 31, 12, tzinfo=u)),
        _Membro(datetime(2026, 9, 2, 12, tzinfo=u)),
        _Membro(datetime(2026, 9, 4, 12, tzinfo=u)),
    ]
    cohorts = _coorti(sid, as_of, membri, ancora=ancora, snapshot_as_of=snapshot_grafo)

    return {
        # left_at E rejoined_at entrambi valorizzati: l'ancora di osservabilita'
        # non e' piu' un istante solo, c'e' un buco in mezzo e le uscite
        # avvenute li' dentro sono invisibili. Cambia la lettura di OGNI coorte.
        "guild": GuildRow(
            guild_id=GUILD_EDGE,
            first_seen_at=ancora,
            backfilled_at=datetime(2026, 4, 20, 11, 6, tzinfo=timezone.utc),
            left_at=datetime(2026, 5, 3, 8, 20, tzinfo=timezone.utc),
            rejoined_at=datetime(2026, 6, 19, 17, 45, tzinfo=timezone.utc),
            latest_metrics_as_of=as_of,
        ),
        "runs": [
            RunRow(
                snapshot_id=sid, as_of=as_of,
                params={"min_cardinality": 5, "k_connections": 5, "min_nodes_structural": 30},
                stats={"durations_ms": {"robustness": 11200, "communities": 6400,
                                        "cohorts": 2100},
                       "snapshot_gaps": {"cadence_days_median": 7.0, "max_gap_days": 56.0}},
                # code_version assente: una run scritta da un'immagine che non
                # lo incideva. E' successo davvero, per mesi.
                code_version=None,
                created_at=as_of + timedelta(hours=4, minutes=15),
            )
        ],
        "robustness": robustness,
        "communities": [c for c in communities if c is not None],
        "cohorts": cohorts,
    }


SCENARIOS: dict[int, dict[str, Any]] = {
    GUILD_TODAY: _scenario_today(),
    GUILD_MATURE: _scenario_mature(),
    GUILD_EDGE: _scenario_edge(),
}

# --- le sette rotte --------------------------------------------------------

app = FastAPI(title="Kindling fixture API", docs_url="/docs")


def _guild(guild_id: int) -> dict[str, Any]:
    """Guild sconosciuta: 404. Guild senza metriche: serie vuota. Come l'API vera."""
    if guild_id not in SCENARIOS:
        raise HTTPException(status_code=404, detail="guild sconosciuta")
    return SCENARIOS[guild_id]


def _ultimi_snapshot(scenario: dict[str, Any], limit: int) -> set[int]:
    """Gli snapshot degli ultimi ``limit`` run, come ``_LATEST_SNAPSHOTS`` in api/db.py.

    Il limite conta SNAPSHOT, e li conta sui run: uno snapshot in cui un layer non
    ha righe resta uno snapshot. Una versione precedente affettava la lista per
    numero di righe (``limit * 12``), cioe' assumeva che ogni snapshot avesse
    tutte le sue righe — falso appena un layer manca, e il troncamento a meta'
    snapshot e' proprio cio' che la query dell'API esiste per evitare.
    """
    ordinati = sorted(scenario["runs"], key=lambda r: (r.as_of, r.snapshot_id), reverse=True)
    return {r.snapshot_id for r in ordinati[:limit]}


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", database="ok")


@app.get("/guilds", response_model=list[GuildRow])
def guilds() -> list[GuildRow]:
    return [s["guild"] for s in SCENARIOS.values()]


@app.get("/guilds/{guild_id}", response_model=GuildRow)
def guild(guild_id: int) -> GuildRow:
    return _guild(guild_id)["guild"]


@app.get("/guilds/{guild_id}/runs", response_model=list[RunRow])
def runs(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[RunRow]:
    ordinati = sorted(_guild(guild_id)["runs"], key=lambda r: (r.as_of, r.snapshot_id), reverse=True)
    return ordinati[:limit]


@app.get("/guilds/{guild_id}/robustness", response_model=list[RobustnessRow])
def robustness(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[RobustnessRow]:
    scenario = _guild(guild_id)
    ids = _ultimi_snapshot(scenario, limit)
    righe = [r for r in scenario["robustness"] if r.snapshot_id in ids]
    # ORDER BY run.as_of DESC, r.layer, r.removal_fraction
    righe.sort(key=lambda r: (r.layer, r.removal_fraction))
    righe.sort(key=lambda r: r.as_of, reverse=True)
    return righe


@app.get("/guilds/{guild_id}/communities", response_model=list[CommunityRow])
def communities(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[CommunityRow]:
    scenario = _guild(guild_id)
    ids = _ultimi_snapshot(scenario, limit)
    righe = [c for c in scenario["communities"] if c.snapshot_id in ids]
    # ORDER BY run.as_of DESC, c.layer
    righe.sort(key=lambda c: c.layer)
    righe.sort(key=lambda c: c.as_of, reverse=True)
    return righe


@app.get("/guilds/{guild_id}/cohorts", response_model=list[CohortGroup])
def cohorts(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[CohortGroup]:
    scenario = _guild(guild_id)
    ids = _ultimi_snapshot(scenario, limit)
    return [g for g in scenario["cohorts"] if g.snapshot_id in ids]


# --- verifica --------------------------------------------------------------


def _codici_estranei_al_job() -> list[str]:
    """Ogni codice usato nelle fixture deve esistere nel sorgente del job.

    Nasce da un difetto reale di questo file: le fixture usavano
    ``below_min_cardinality``, ``secondary_suppression`` e
    ``horizon_not_elapsed``, mentre il job emette ``below_threshold``,
    ``secondary`` e ``horizon_not_reached``. Tre codici inventati, serviti per
    ore senza che niente protestasse.

    Il motivo per cui il resto di ``check()`` non poteva accorgersene e' la
    parte che conta: ``suppression_reason`` e ``not_computable_reason`` sono
    tipizzati come stringhe libere, quindi una fixture con un codice
    fantasioso e' perfettamente valida per i modelli. Validare la FORMA non
    dice niente sul CONTENUTO, e una dashboard che traducesse quei codici
    mostrerebbe codice grezzo in produzione — senza errori, come sempre in
    CLAUDE.md 7.

    Si controlla contro il testo del sorgente invece di importare da ``job``
    perche' ``job.suppression`` tira dentro ``job.communities``, e con lui
    igraph e leidenalg: dipendenze pesanti che un attrezzo di sviluppo non
    deve pretendere. Il prezzo e' un controllo testuale; il guadagno e' che
    resta eseguibile ovunque.
    """
    radice = pathlib.Path(__file__).resolve().parent.parent
    sorgenti = ""
    mancanti_file: list[str] = []
    # job/metrics.py per i motivi di non confrontabilita' (snapshot_comparability),
    # che finiscono in stability_unavailable.
    for nome in ("job/suppression.py", "job/cohorts.py", "job/communities.py",
                 "job/robustness.py", "job/metrics.py"):
        f = radice / nome
        if not f.exists():
            mancanti_file.append(nome)
            continue
        sorgenti += f.read_text(encoding="utf-8")

    if mancanti_file:
        # Un controllo che non trova cosa ispezionare non "passa": lo dice.
        return [f"sorgente del job non trovato: {', '.join(mancanti_file)}"]

    usati: set[str] = set()

    def _raccogli(rows: list[Any]) -> None:
        for row in rows:
            q = getattr(row, "quality", None)
            if q is not None and q.suppression_reason:
                usati.add(q.suppression_reason)
            v = getattr(row, "values", None)
            motivo = getattr(v, "not_computable_reason", None)
            if motivo:
                usati.add(motivo)
            det = getattr(q, "details", None) or {}
            for chiave in ("not_significant_because",):
                for codice in det.get(chiave, []) or []:
                    usati.add(codice)
            for chiave in ("stability_unavailable",):
                if det.get(chiave):
                    usati.add(det[chiave])

    for s in SCENARIOS.values():
        _raccogli(s["robustness"])
        _raccogli(s["communities"])
        for c in s["communities"]:
            _raccogli(c.sizes)
        for g in s["cohorts"]:
            _raccogli(g.onboarding)
            _raccogli(g.retention)

    return [
        f"codice non presente nel sorgente del job: {c!r}"
        for c in sorted(usati)
        if f'"{c}"' not in sorgenti and f"'{c}'" not in sorgenti
    ]


def problemi_di_contenuto(scenari: dict[int, dict[str, Any]]) -> list[str]:
    """Le regole del job che la validazione dei modelli non puo' vedere.

    Ogni controllo qui corrisponde a una divergenza gia' trovata in questo file
    oppure a un'invariante del job da cui la dashboard dipende. Sono controlli di
    CONTENUTO: una riga che li viola e' valida per ``api/models.py`` e
    impossibile per il job.
    """
    problemi: list[str] = []

    for gid, s in scenari.items():
        run_ids = {r.snapshot_id for r in s["runs"]}

        # --- robustezza --------------------------------------------------
        parti_rob: dict[tuple[int, str], list[RobustnessRow]] = {}
        for r in s["robustness"]:
            parti_rob.setdefault((r.snapshot_id, r.layer), []).append(r)

        for (sid, layer), righe in parti_rob.items():
            etichetta = f"{gid}/robustness {layer}@{sid}"
            if sid not in run_ids:
                problemi.append(f"{etichetta}: snapshot senza run")
            if sorted(r.removal_fraction for r in righe) != sorted(REMOVAL_FRACTIONS):
                problemi.append(f"{etichetta}: frazioni incomplete o duplicate")
            if len({r.quality.suppressed for r in righe}) > 1:
                problemi.append(
                    f"{etichetta}: soppressione mista nel layer (la soglia guarda "
                    "n_effective, uguale sulle tre frazioni: tutto o niente)"
                )
            pubblicate = [r for r in righe if not r.quality.suppressed]
            if len({r.quality.n_effective for r in pubblicate}) > 1:
                problemi.append(f"{etichetta}: n_effective diverso tra le frazioni")
            for r in pubblicate:
                n = r.quality.n_effective
                v = r.values
                if n is None or n < PARAMS.publish_threshold:
                    problemi.append(f"{etichetta} {r.removal_fraction}: pubblicata con n={n}")
                    continue
                attesi = nodes_removed_for(n, r.removal_fraction)
                if v.nodes_removed != attesi:
                    problemi.append(
                        f"{etichetta} {r.removal_fraction}: nodes_removed={v.nodes_removed}, "
                        f"il job scriverebbe {attesi}"
                    )
                if v.targeted_excess is None:
                    problemi.append(f"{etichetta} {r.removal_fraction}: targeted_excess None su riga pubblicata")
                degenere = v.giant_after_random_sd == 0.0
                if (v.targeted_z is None) != degenere:
                    problemi.append(f"{etichetta} {r.removal_fraction}: targeted_z None senza baseline degenere, o viceversa")
                significativa = (
                    n >= PARAMS.min_nodes_structural and attesi >= 2 and not degenere
                )
                if r.quality.significant is not significativa:
                    problemi.append(
                        f"{etichetta} {r.removal_fraction}: significant={r.quality.significant}, "
                        f"il job scriverebbe {significativa}"
                    )

        # --- community ---------------------------------------------------
        parti_com = {(c.snapshot_id, c.layer): c for c in s["communities"]}
        if set(parti_com) != set(parti_rob):
            problemi.append(
                f"{gid}: (snapshot, layer) presenti in una sola tra robustezza e community: "
                f"{sorted(set(parti_com) ^ set(parti_rob))}"
            )

        confronti_per_snapshot: dict[int, set[tuple[int, Optional[float]]]] = {}
        for (sid, layer), c in parti_com.items():
            etichetta = f"{gid}/communities {layer}@{sid}"
            q, v = c.quality, c.values

            righe_rob = parti_rob.get((sid, layer), [])
            if righe_rob:
                if righe_rob[0].quality.suppressed != q.suppressed:
                    problemi.append(f"{etichetta}: soppressione diversa dalla robustezza")
                elif not q.suppressed and righe_rob[0].quality.n_effective != q.n_effective:
                    problemi.append(f"{etichetta}: n diverso dalla robustezza (e' lo stesso grafo)")

            if not q.suppressed:
                # I campi del confronto sono tutti o nessuno (compute_communities:
                # si scrivono insieme nel ramo `previous is not None`). La vista
                # Community riconosce "prima osservazione confrontabile" da
                # previous_gap_days None e ne deduce il resto: un fixture che
                # mescolasse le due forme le insegnerebbe una riga impossibile.
                confronto = {
                    "previous_snapshot_id": c.previous_snapshot_id,
                    "previous_gap_days": v.previous_gap_days,
                    "node_overlap": v.node_overlap,
                    "communities_born": v.communities_born,
                    "communities_dissolved": v.communities_dissolved,
                    "communities_merged": v.communities_merged,
                    "communities_split": v.communities_split,
                }
                assenti = sorted(k for k, x in confronto.items() if x is None)
                if assenti and len(assenti) != len(confronto):
                    problemi.append(
                        f"{etichetta}: campi del confronto in parte None ({assenti}): "
                        "il job li scrive tutti o nessuno"
                    )
                # stability_jaccard si scrive solo dentro il ramo che ha appena
                # scritto node_overlap, e solo con node_overlap al minimo o sopra.
                sopra_minimo = v.node_overlap is not None and v.node_overlap >= PARAMS.min_node_overlap
                if (v.stability_jaccard is not None) != sopra_minimo:
                    problemi.append(
                        f"{etichetta}: stability_jaccard={v.stability_jaccard} con "
                        f"node_overlap={v.node_overlap} (minimo {PARAMS.min_node_overlap})"
                    )
                if c.previous_snapshot_id is not None:
                    confronti_per_snapshot.setdefault(sid, set()).add(
                        (c.previous_snapshot_id, v.previous_gap_days)
                    )
                # node_overlap = |nucleo| / |unione| non supera min(n)/max(n).
                # Si verifica solo dove il precedente e' nello scenario: se il
                # layer li' non ha righe il grafo era vuoto e l'overlap e' zero;
                # se e' soppresso la sua n non e' nota e il controllo si salta.
                if c.previous_snapshot_id in run_ids and v.node_overlap is not None:
                    prec = parti_com.get((c.previous_snapshot_id, layer))
                    if prec is None:
                        if v.node_overlap != 0.0:
                            problemi.append(
                                f"{etichetta}: node_overlap={v.node_overlap} con il layer assente "
                                "nel precedente (partizione vuota: il job scrive 0)"
                            )
                    elif prec.quality.n_effective and q.n_effective:
                        a, b = q.n_effective, prec.quality.n_effective
                        tetto = min(a, b) / max(a, b)
                        if v.node_overlap > tetto + 1e-9:
                            problemi.append(
                                f"{etichetta}: node_overlap={v.node_overlap} impossibile tra {a} e "
                                f"{b} nodi (al massimo {tetto:.3f})"
                            )
                z = v.modularity_z
                sovrapposizione_bassa = (
                    c.previous_snapshot_id is not None
                    and (v.node_overlap is None or v.node_overlap < PARAMS.min_node_overlap)
                )
                significativa = (
                    (q.n_effective or 0) >= PARAMS.min_nodes_structural
                    and z is not None and z >= PARAMS.min_modularity_z
                    and not sovrapposizione_bassa
                )
                if q.significant is not significativa:
                    problemi.append(
                        f"{etichetta}: significant={q.significant}, il job scriverebbe {significativa}"
                    )

            # --- bucket: i cinque controlli ------------------------------
            soppressi = [b for b in c.sizes if b.quality.suppressed]
            for b in c.sizes:
                eb = f"{etichetta} bucket {b.bucket}"
                # 1. metric_community_sizes non ha n_effective: in produzione None.
                if b.quality.n_effective is not None:
                    problemi.append(f"{eb}: quality.n_effective valorizzato (in produzione e' sempre None)")
                if not b.quality.suppressed:
                    # 2. size_buckets() non emette bucket vuoti.
                    if not b.values.community_count or not b.values.member_count:
                        problemi.append(f"{eb}: bucket vuoto pubblicato")
                    # 4. primaria sotto min_cardinality membri.
                    elif b.values.member_count < PARAMS.min_cardinality:
                        problemi.append(f"{eb}: pubblicato con {b.values.member_count} membri, sotto soglia")
            # 5. secondaria solo accanto a una primaria; madre soppressa => tutti.
            motivi = {b.quality.suppression_reason for b in soppressi}
            if REASON_SECONDARY in motivi and REASON_BELOW_THRESHOLD not in motivi:
                problemi.append(f"{etichetta}: soppressione secondaria senza una primaria")
            if q.suppressed and len(soppressi) != len(c.sizes):
                problemi.append(f"{etichetta}: riga madre soppressa con bucket pubblicati")
            # 3. senza bucket soppressi la somma dei membri e' n. Con un bucket
            # soppresso la somma non e' verificabile, e il controllo si salta.
            if not q.suppressed and not soppressi:
                if sum(b.values.member_count or 0 for b in c.sizes) != q.n_effective:
                    problemi.append(f"{etichetta}: la somma dei member_count non e' n_effective")
                if sum(b.values.community_count or 0 for b in c.sizes) != v.community_count:
                    problemi.append(f"{etichetta}: la somma dei community_count non e' community_count")

        for sid, confronti in confronti_per_snapshot.items():
            if len(confronti) > 1:
                problemi.append(
                    f"{gid}/communities @{sid}: layer confrontati con precedenti o gap diversi "
                    f"{sorted(confronti, key=str)} (il precedente e' uno per snapshot)"
                )

        # --- coorti ------------------------------------------------------
        #
        # Il job scrive le coorti a OGNI run, per ogni coorte dentro
        # cohort_max_age_days: uno snapshot con un run e senza coorti descrive una
        # guild che l'API non puo' produrre. Il controllo di coerenza tra
        # endpoint copriva robustezza e community e si fermava li'.
        gruppi = s["cohorts"]
        ancora = s["guild"].first_seen_at
        as_of_run = {r.snapshot_id: r.as_of for r in s["runs"]}
        if gruppi:
            senza = sorted(set(as_of_run) - {g.snapshot_id for g in gruppi})
            if senza:
                problemi.append(f"{gid}/cohorts: snapshot con un run e nessuna coorte: {senza}")

        for g in gruppi:
            eg = f"{gid}/cohorts {g.cohort_start}@{g.snapshot_id}"
            if g.snapshot_id not in as_of_run:
                problemi.append(f"{eg}: snapshot senza run")
                continue
            if g.cohort_start < (as_of_run[g.snapshot_id] - timedelta(days=PARAMS.cohort_max_age_days)).date():
                problemi.append(f"{eg}: coorte oltre cohort_max_age_days, il job non la calcola")
            if sorted(o.layer_scope for o in g.onboarding) != sorted(COHORT_SCOPES):
                problemi.append(f"{eg}: ambiti di onboarding incompleti")
            if sorted(t.horizon_days for t in g.retention) != sorted(RETENTION_HORIZONS):
                problemi.append(f"{eg}: orizzonti di retention incompleti")

            righe = [*g.onboarding, *g.retention]
            if len({r.quality.suppressed for r in righe}) > 1:
                problemi.append(f"{eg}: soppressione diversa tra righe della stessa coorte (stessa n)")
            pubblicate = [r for r in righe if not r.quality.suppressed]
            for campo in ("n_effective", "is_survivors_only", "excluded_rejoins"):
                if len({getattr(r.quality, campo) for r in pubblicate}) > 1:
                    problemi.append(f"{eg}: {campo} diverso tra onboarding e retention (stessa popolazione)")
            sopravvissuti = g.cohort_start < ancora.date()

            for r in g.retention:
                if r.quality.suppressed:
                    if r.values.is_computable is not None:
                        problemi.append(f"{eg} retention {r.horizon_days}: soppressa con is_computable non nullo")
                    continue
                v, n = r.values, r.quality.n_effective
                if r.quality.is_survivors_only is not sopravvissuti:
                    problemi.append(f"{eg}: is_survivors_only non coerente con l'ancora {ancora.date()}")
                if (v.not_computable_reason is None) is not bool(v.is_computable):
                    problemi.append(f"{eg} retention {r.horizon_days}: is_computable e not_computable_reason incoerenti")
                if sopravvissuti and v.not_computable_reason != "before_observability_anchor":
                    problemi.append(f"{eg} retention {r.horizon_days}: coorte di soli sopravvissuti calcolabile")
                if v.retained_fraction is not None and n:
                    # E' un controllo sui CONTEGGI, non sui float: la frazione e'
                    # persone_rimaste / n e deve ricomporre un intero. Serve la
                    # tolleranza, perche' il primo dato vero la farebbe suonare a
                    # vuoto: 0.8571428571428571 * 14 = 11.999999999999998.
                    persone = v.retained_fraction * n
                    if not math.isclose(persone, round(persone), abs_tol=1e-9):
                        problemi.append(f"{eg} retention {r.horizon_days}: {v.retained_fraction} * {n} non e' un conteggio")

            eventi_per_ambito: dict[str, int] = {}
            for o in g.onboarding:
                if o.quality.suppressed:
                    continue
                q, v = o.quality, o.values
                eo = f"{eg} {o.layer_scope}"
                if q.is_mature is not (q.observation_days is not None and q.observation_days >= PARAMS.min_observation_days):
                    problemi.append(f"{eo}: is_mature={q.is_mature} con observation_days={q.observation_days}")
                if (v.event_count or 0) + (v.censored_count or 0) != q.n_effective:
                    problemi.append(f"{eo}: event_count + censored_count non e' n_effective")
                if (v.censored_by_leave or 0) > (v.censored_count or 0):
                    problemi.append(f"{eo}: censored_by_leave oltre censored_count")
                if v.median_reached is not (v.median_days_to_k is not None):
                    problemi.append(f"{eo}: median_reached incoerente con median_days_to_k")
                if q.significant is not bool(q.is_mature and q.has_snapshot_coverage and not sopravvissuti):
                    problemi.append(f"{eo}: significant non coerente con maturita', copertura e ancora")
                eventi_per_ambito[o.layer_scope] = v.event_count or 0
            # 'any' e' l'unione degli ambiti: non puo' avere meno eventi di un layer.
            if "any" in eventi_per_ambito and any(e > eventi_per_ambito["any"] for e in eventi_per_ambito.values()):
                problemi.append(f"{eg}: un ambito ha piu' eventi di 'any'")

    return problemi


def check() -> int:
    """Round-trip con i modelli veri, piu' i controlli di contenuto.

    Non e' una formalita'. Le fixture sono costruite istanziando i modelli,
    quindi un campo sbagliato fallisce gia' all'import; questo controllo prende
    il caso diverso e piu' insidioso — una fixture che l'API non potrebbe
    produrre, per esempio valori non nulli su una riga soppressa, che in
    produzione un trigger Postgres rifiuta e qui passerebbe.
    """
    problems: list[str] = _codici_estranei_al_job() + problemi_di_contenuto(SCENARIOS)
    checks: list[tuple[str, type[BaseModel], list[Any]]] = [
        ("guilds", GuildRow, [s["guild"] for s in SCENARIOS.values()]),
    ]
    for gid, s in SCENARIOS.items():
        checks += [
            (f"{gid}/runs", RunRow, s["runs"]),
            (f"{gid}/robustness", RobustnessRow, s["robustness"]),
            (f"{gid}/communities", CommunityRow, s["communities"]),
            (f"{gid}/cohorts", CohortGroup, s["cohorts"]),
        ]

    total = 0
    for name, model_cls, payload in checks:
        for model in payload:
            try:
                _validate(model_cls, _as_json(model))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: {exc}")
            total += 1

    # L'invariante del trigger metric_suppressed_row_is_empty, riprodotta qui:
    # una riga soppressa ha TUTTI i values a None e details vuoto. In produzione
    # e' il database a imporlo; una fixture che la viola descriverebbe una
    # risposta che l'API non puo' emettere.
    def _audit(rows: list[Any], label: str) -> None:
        for row in rows:
            q = getattr(row, "quality", None)
            if q is None or not q.suppressed:
                continue
            leaked = [k for k, v in _fields(row.values).items() if v is not None]
            if leaked:
                problems.append(f"{label}: riga soppressa con valori non nulli: {leaked}")
            if q.details:
                problems.append(f"{label}: riga soppressa con details non vuoto")

    for gid, s in SCENARIOS.items():
        _audit(s["robustness"], f"{gid}/robustness")
        _audit(s["communities"], f"{gid}/communities")
        for c in s["communities"]:
            _audit(c.sizes, f"{gid}/communities.sizes")
        for group in s["cohorts"]:
            _audit(group.onboarding, f"{gid}/cohorts.onboarding")
            _audit(group.retention, f"{gid}/cohorts.retention")

    if problems:
        print("FIXTURE NON VALIDE:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(f"fixture valide: {total} righe, {len(SCENARIOS)} scenari")
    for gid, s in SCENARIOS.items():
        print(
            f"  {gid}: runs={len(s['runs'])} robustness={len(s['robustness'])} "
            f"communities={len(s['communities'])} cohorts={len(s['cohorts'])}"
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="valida le fixture ed esce")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check())
    parser.error("usa --check, oppure servi con: uvicorn tools.fixture_api:app")
