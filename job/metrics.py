"""Orchestrazione del calcolo delle metriche aggregate, senza toccare il database.

Stessa forma di ``snapshot.py``: una funzione pura che mette in fila i pezzi
nell'ordine in cui la specifica li definisce, cosi' che le regole piu' facili da
sbagliare in silenzio — la soppressione sotto soglia, la calcolabilita', la
censura a destra — siano testabili senza un Postgres a portata.

Il confine di modello-metriche.md 8 passa di qui: le grandezze per-nodo
(betweenness, appartenenza alla community, partner del singolo membro) restano
dentro i moduli che le calcolano, e quello che esce da questa funzione sono solo
righe di tabella gia' soppresse.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from .cohorts import (
    CohortMember,
    CohortResult,
    RetentionResult,
    compute_cohort,
    compute_retention,
    split_cohorts,
)
from .communities import (
    CommunityResult,
    CommunitySize,
    compute_communities,
    sensitivity_details as community_sensitivity,
)
from .config import ALL_LAYERS, MetricParams
from .edges import Edge
from .graph import build_metric_graph
from .robustness import (
    RobustnessResult,
    compute_robustness,
    sensitivity_details as robustness_sensitivity,
)
from .suppression import apply_secondary_suppression, apply_threshold


@dataclass
class MetricsResult:
    snapshot_id: int
    guild_id: int
    as_of: datetime
    robustness: list[RobustnessResult] = field(default_factory=list)
    communities: list[CommunityResult] = field(default_factory=list)
    community_sizes: list[CommunitySize] = field(default_factory=list)
    cohorts: list[CohortResult] = field(default_factory=list)
    cohort_retention: list[RetentionResult] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviousPartition:
    """La partizione dello snapshot precedente, gia' ricostruita.

    Ricostruita e non letta da una tabella: l'appartenenza alla community e' un
    dato per-nodo e non viene persistita da nessuna parte (modello-metriche.md
    8). E' il seed fissato a rendere esatta la ricostruzione — il determinismo
    e' cio' che permette di non salvarla, non solo una comodita'.
    """

    snapshot_id: int
    # L'as_of dello snapshot precedente, non solo il suo id: la stabilita' deve
    # poter dichiarare a che distanza e' stata calcolata (modello-metriche.md
    # 4.7), e senza questo campo l'informazione non arriverebbe fin qui.
    as_of: datetime
    membership_by_layer: dict[str, dict[int, int]]



@dataclass(frozen=True)
class SnapshotIdentity:
    """Cio' che decide se due snapshot del grafo sono confrontabili.

    Non solo i parametri: anche l'**ampiezza della finestra**, che parametro non
    e' e non compare in ``graph_snapshots.params``. Due finestre di ampiezza
    diversa producono grafi di densita' diversa, e non e' un caso teorico — il
    primo snapshot copre ~3 giorni e i successivi ne coprono 7, quindi la prima
    stabilita' mai calcolata sarebbe tra quei due.
    """

    params: dict[str, Any]
    window: timedelta


def snapshot_comparability(
    reference: SnapshotIdentity, candidate: SnapshotIdentity
) -> Optional[str]:
    """``None`` se i due snapshot sono confrontabili, altrimenti il perche'.

    Tre modi di non esserlo, tutti da dichiarare invece che da aggirare
    (modello-metriche.md 4.6):

    - ``params_unknown`` — i parametri sono vuoti da almeno una delle due parti.
      ``{}`` e' il default di uno snapshot scritto prima che la colonna
      esistesse, e significa "non so con cosa e' stato calcolato", non "stessi
      parametri". Due ``{}`` non sono confrontabili tra loro piu' di quanto lo
      siano con qualunque altra cosa.
    - ``params_differ`` — parametri diversi: la differenza tra le due partizioni
      conterrebbe il cambio di parametri, e attribuirla alla community sarebbe
      falso.
    - ``window_differs`` — finestre di ampiezza diversa.

    Il confronto sull'ampiezza e' esatto: due esecuzioni con la stessa
    ``--window-days`` danno la stessa durata al microsecondo, quindi una
    differenza non e' rumore numerico ma una finestra davvero diversa.
    """
    if not reference.params or not candidate.params:
        return "params_unknown"
    if reference.params != candidate.params:
        return "params_differ"
    if reference.window != candidate.window:
        return "window_differs"
    return None


def _seed_for(params: MetricParams, *parts: object) -> str:
    """Seme deterministico per un singolo calcolo.

    Stringa e non ``hash()`` di una tupla: l'hash delle stringhe in Python e'
    randomizzato a ogni processo (PYTHONHASHSEED), quindi due esecuzioni dello
    stesso job darebbero baseline diversi — cioe' esattamente il rumore che il
    seed esiste per eliminare. ``random.Random`` con una stringa passa da
    SHA-512 ed e' stabile tra processi e tra macchine.
    """
    return "|".join([str(params.seed), *(str(part) for part in parts)])


def build_metrics(
    *,
    snapshot_id: int,
    guild_id: int,
    as_of: datetime,
    params: MetricParams,
    edges: Iterable[Edge],
    members: Iterable[CohortMember] = (),
    reached_at: Optional[dict[str, dict[int, datetime]]] = None,
    observability_anchor: Optional[datetime] = None,
    snapshot_windows: Iterable[tuple[datetime, datetime]] = (),
    previous: Optional[PreviousPartition] = None,
    stability_unavailable_reason: Optional[str] = None,
    cohort_stats: Optional[dict[str, Any]] = None,
) -> MetricsResult:
    """Calcola tutte le righe di metrica di uno snapshot.

    ``reached_at`` mappa ambito -> {author_id: istante in cui ha raggiunto k}, ed
    e' gia' il risultato della scansione snapshot per snapshot fatta dal layer
    di lettura: qui non si rileggono archi.
    """
    edges = list(edges)
    result = MetricsResult(snapshot_id=snapshot_id, guild_id=guild_id, as_of=as_of)
    durations: dict[str, float] = {}

    started = time.monotonic()
    _structural_metrics(
        result,
        as_of=as_of,
        edges=edges,
        params=params,
        previous=previous,
        stability_unavailable_reason=stability_unavailable_reason,
        durations=durations,
    )
    durations["structural_total_ms"] = (time.monotonic() - started) * 1000.0

    started = time.monotonic()
    _cohort_metrics(
        result,
        params=params,
        members=list(members),
        reached_at=reached_at or {},
        as_of=as_of,
        observability_anchor=observability_anchor,
        snapshot_windows=list(snapshot_windows),
        cohort_stats=cohort_stats or {},
    )
    durations["cohorts_ms"] = (time.monotonic() - started) * 1000.0

    result.stats = {
        "durations_ms": {name: round(value, 1) for name, value in durations.items()},
        "params_thresholds": {
            "min_cardinality": params.min_cardinality,
            "min_nodes_publish": params.publish_threshold,
        },
        **(cohort_stats or {}),
    }
    return result


def _structural_metrics(
    result: MetricsResult,
    *,
    as_of: datetime,
    edges: list[Edge],
    params: MetricParams,
    previous: Optional[PreviousPartition],
    stability_unavailable_reason: Optional[str],
    durations: dict[str, float],
) -> None:
    robustness_ms = 0.0
    communities_ms = 0.0

    for layer in ALL_LAYERS:
        graph = build_metric_graph(edges, layer=layer, params=params)
        if graph.vcount() == 0:
            # Nessun arco in questo layer: nessuna riga. Una riga di zeri
            # direbbe "rete perfettamente frammentata", che e' un'altra cosa da
            # "questo layer non esiste ancora".
            continue

        # Verifica di sensibilita' (2.4): gli archi ricostruiti sono inclusi nel
        # calcolo principale, ma la domanda "la lettura cambia se li tolgo?"
        # deve poter essere fatta senza rieseguire il job.
        clean = build_metric_graph(
            edges, layer=layer, params=params, include_reconciled=False
        )
        # Il grafo senza archi ricostruiti e' un sottografo di quello principale
        # per costruzione: a parita' di nodi e archi i due coincidono. Quando
        # coincidono — il caso normale, perche' nessun arco e' ricostruito — la
        # variante non si ricalcola: sarebbero R ripetizioni di baseline per
        # ogni X piu' R rewiring con Leiden, cioe' il 100% di CPU in piu' sulla
        # voce che 11.1 identifica come dominante, per un risultato identico.
        # Il fatto che coincidano viene comunque registrato: "nessuna
        # differenza" e "non calcolata" sono due cose diverse a valle.
        clean_identical = (
            clean.vcount() == graph.vcount() and clean.ecount() == graph.ecount()
        )

        started = time.monotonic()
        for fraction in params.removal_fractions:
            row = compute_robustness(
                graph,
                layer=layer,
                removal_fraction=fraction,
                params=params,
                # Un generatore per riga, seminato in modo deterministico: due
                # esecuzioni sullo stesso snapshot devono dare lo stesso
                # baseline, altrimenti la variazione tra snapshot misura in
                # parte il rumore del baseline.
                rng=random.Random(_seed_for(params, layer, "robustness", fraction)),
            )
            if row is None:
                continue
            clean_row = None
            if not clean_identical and clean.vcount():
                clean_row = compute_robustness(
                    clean,
                    layer=layer,
                    removal_fraction=fraction,
                    params=params,
                    rng=random.Random(
                        _seed_for(params, layer, "robustness_clean", fraction)
                    ),
                )
            row.details.update(
                robustness_sensitivity(clean_row, identical=clean_identical)
            )
            apply_threshold(
                row, count=row.n_effective, threshold=params.publish_threshold
            )
            result.robustness.append(row)
        robustness_ms += (time.monotonic() - started) * 1000.0

        started = time.monotonic()
        previous_membership = (
            previous.membership_by_layer.get(layer) if previous is not None else None
        )
        computed = compute_communities(
            graph,
            layer=layer,
            params=params,
            rng=random.Random(_seed_for(params, layer, "leiden")),
            as_of=as_of,
            previous=previous_membership,
            previous_snapshot_id=previous.snapshot_id if previous else None,
            previous_as_of=previous.as_of if previous else None,
            unavailable_reason=stability_unavailable_reason,
        )
        communities_ms += (time.monotonic() - started) * 1000.0
        if computed is None:
            continue
        community_row, sizes = computed

        clean_community = None
        if not clean_identical and clean.vcount():
            clean_computed = compute_communities(
                clean,
                layer=layer,
                params=params,
                rng=random.Random(_seed_for(params, layer, "leiden_clean")),
            )
            if clean_computed is not None:
                clean_community = clean_computed[0]
        community_row.details.update(
            community_sensitivity(clean_community, identical=clean_identical)
        )

        apply_threshold(
            community_row,
            count=community_row.n_effective,
            threshold=params.publish_threshold,
        )
        result.communities.append(community_row)

        if community_row.is_suppressed:
            # Con la riga madre soppressa la distribuzione non va pubblicata a
            # nessuna condizione: il totale che i bucket ricostruiscono e'
            # esattamente la numerosita' appena nascosta.
            for size in sizes:
                apply_threshold(size, count=None, threshold=params.min_cardinality)
            result.community_sizes.extend(sizes)
        else:
            result.community_sizes.extend(
                apply_secondary_suppression(sizes, threshold=params.min_cardinality)
            )

    durations["robustness_ms"] = robustness_ms
    durations["communities_ms"] = communities_ms


def _cohort_metrics(
    result: MetricsResult,
    *,
    params: MetricParams,
    members: list[CohortMember],
    reached_at: dict[str, dict[int, datetime]],
    as_of: datetime,
    observability_anchor: Optional[datetime],
    snapshot_windows: list[tuple[datetime, datetime]],
    cohort_stats: dict[str, Any],
) -> None:
    cohorts, excluded = split_cohorts(members, params=params)

    for cohort_start in sorted(cohorts):
        cohort_members = cohorts[cohort_start]
        excluded_count = excluded.get(cohort_start, 0)

        for scope in params.cohort_layer_scopes:
            row = compute_cohort(
                cohort_start=cohort_start,
                layer_scope=scope,
                members=cohort_members,
                reached_at=reached_at.get(scope, {}),
                excluded_rejoins=excluded_count,
                as_of=as_of,
                params=params,
                observability_anchor=observability_anchor,
                snapshot_windows=snapshot_windows,
                details=_cohort_details(cohort_stats),
            )
            apply_threshold(
                row, count=row.n_effective, threshold=params.min_cardinality
            )
            result.cohorts.append(row)

        for horizon in params.retention_horizons_days:
            retention = compute_retention(
                cohort_start=cohort_start,
                horizon_days=horizon,
                members=cohort_members,
                excluded_rejoins=excluded_count,
                as_of=as_of,
                observability_anchor=observability_anchor,
            )
            # Stessa soglia e stessa popolazione di metric_cohorts: due
            # denominatori diversi per la stessa coorte in due tabelle
            # affiancate sono una divergenza che non si nota mai.
            apply_threshold(
                retention, count=retention.n_effective, threshold=params.min_cardinality
            )
            result.cohort_retention.append(retention)


# Diagnostica di lettura comune a tutte le coorti. Il numero di snapshot usati e
# la cadenza della serie non sono un dettaglio: il tempo all'evento e' misurato
# in snapshot, quindi una settimana senza job e' censura intervallare e due
# coorti sono confrontabili solo se la serie sotto di loro ha la stessa cadenza.
COHORT_DIAGNOSTIC_KEYS = (
    "snapshots_used",
    "snapshots_skipped_params",
    "snapshot_gaps",
)


def _cohort_details(cohort_stats: dict[str, Any]) -> dict[str, Any]:
    """Le chiavi di diagnostica, **sempre tutte**, anche quando valgono ``None``.

    Emetterle solo quando ci sono le farebbe sparire senza traccia proprio nel
    caso in cui il codice non sa produrle — ed e' cosi' che ``snapshot_gaps`` e'
    rimasto specificato in 5.3 e non calcolato da nessuno, senza che niente lo
    segnalasse. Una chiave assente e una che vale zero devono restare
    distinguibili: e' la stessa distinzione tra "non calcolato" e "zero" che la
    specifica impone dappertutto altrove.
    """
    return {key: cohort_stats.get(key) for key in COHORT_DIAGNOSTIC_KEYS}


def cohort_window_start(as_of: datetime, *, params: MetricParams) -> date:
    """Coorte piu' vecchia ancora ricalcolata a questa run."""
    return (as_of - timedelta(days=params.cohort_max_age_days)).date()
