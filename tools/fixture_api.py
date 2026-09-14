"""Server di fixture per lo sviluppo della dashboard.

Espone le stesse sette rotte dell'API reale, con risposte sintetiche. Serve a
sviluppare la dashboard senza toccare la droplet e — soprattutto — a esercitare
gli stati che in produzione oggi NON esistono.

**Perche' non si sviluppa contro i dati veri.** Oggi la produzione ha un solo
snapshot, tutto sotto la soglia dei 30 nodi e le coorti non mature: esercita due
stati su otto. Gli altri sei — riga soppressa, valore significativo,
``stability_jaccard`` popolata, ``previous_gap_days`` anomalo, retention non
calcolabile, coorte di soli sopravvissuti — comparirebbero per la prima volta in
produzione, da soli, quando nessuno sta guardando. Un percorso di rendering mai
eseguito non e' codice che funziona: e' codice di cui non si sa niente. E' la
classe di difetto di CLAUDE.md 7 applicata alla UI.

**Perche' le fixture si costruiscono con i modelli veri.** Ogni riga qui sotto e'
un'istanza di ``api.models``, non un dizionario scritto a mano. Se un campo
cambia nome, sparisce o cambia tipo, questo file smette di importare o di
validare — rumorosamente, adesso — invece di servire per mesi una forma che
l'API non produce piu'. Una fixture scritta a mano diverge dal contratto in
silenzio, ed e' esattamente il difetto che pretende di aiutare a evitare.

**Nessun dato reale, mai.** Gli id sono sintetici e i numeri inventati. Questo
file puo' stare in git; un dump o un export no.

Uso::

    python -m tools.fixture_api --check      # valida e basta, exit 1 se rotto
    uvicorn tools.fixture_api:app --port 8899

Tre guild, tre scenari — si cambia scenario scegliendo la guild, che esercita
anche il selettore multi-guild del flusso di autorizzazione:

===================  =========================================================
``...001`` oggi      lo stato reale del 12/09: un punto, niente di significativo
``...002`` maturo    dodici settimane di serie, valori significativi, stabilita'
``...003`` limite    gli stati che mordono: soppressione, gap anomalo, non
                     calcolabile, soli sopravvissuti, buco di osservazione
===================  =========================================================
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
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


# Costanti prese da job/config.py. Se divergono, le fixture descrivono una
# configurazione che il job non produce.
LAYERS = ("voice", "reply", "mention", "reaction")
REMOVAL_FRACTIONS = (0.05, 0.10, 0.20)
COHORT_SCOPES = ("any", "voice")
RETENTION_HORIZONS = (7, 14, 28)
SIZE_BUCKETS = ("small", "5-9", "10-19", "20-49", "50-99", "100+")
K = 5

GUILD_TODAY = 900000000000000001
GUILD_MATURE = 900000000000000002
GUILD_EDGE = 900000000000000003

MONDAY = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _as_of(weeks_ago: int) -> datetime:
    return MONDAY - timedelta(weeks=weeks_ago)


# --- robustezza ------------------------------------------------------------


def _robustness(
    snapshot_id: int,
    as_of: datetime,
    layer: str,
    removal_fraction: float,
    *,
    n: int,
    significant: Optional[bool],
    suppressed: bool = False,
    excess: Optional[float] = None,
    reasons: Optional[list[str]] = None,
    nodes_removed: Optional[int] = None,
    degenerate: bool = False,
) -> RobustnessRow:
    """Una riga di robustezza.

    ``nodes_removed`` e' esplicito e non derivato dalla frazione: e' il punto
    in cui la percentuale mente. A 20 nodi, 0.05 rimuove UN nodo, e
    ``targeted_excess`` vicino a zero si legge come "togliere il 5% non rompe
    niente" mentre il fatto e' che non e' stato tolto quasi nessuno.
    """
    if suppressed:
        return RobustnessRow(
            snapshot_id=snapshot_id,
            as_of=as_of,
            layer=layer,
            removal_fraction=removal_fraction,
            quality=Quality(
                n_effective=None,
                suppressed=True,
                suppression_reason="below_threshold",
                significant=None,
                details={},
            ),
            values=RobustnessValues(),
        )

    removed = nodes_removed if nodes_removed is not None else max(0, int(n * removal_fraction))
    giant_before = 1.0
    giant_targeted = round(max(0.0, giant_before - (excess or 0.0) - 0.12), 3)
    sd = 0.0 if degenerate else 0.041

    details: dict[str, Any] = {"baseline_repetitions_used": 100, "baseline_degraded": False}
    if reasons:
        details["not_significant_because"] = reasons

    return RobustnessRow(
        snapshot_id=snapshot_id,
        as_of=as_of,
        layer=layer,
        removal_fraction=removal_fraction,
        quality=Quality(
            n_effective=n,
            suppressed=False,
            suppression_reason=None,
            significant=significant,
            details=details,
        ),
        values=RobustnessValues(
            nodes_removed=removed,
            giant_before=giant_before,
            giant_after_targeted=giant_targeted,
            components_after_targeted=max(1, removed * 2),
            giant_after_random_mean=round(giant_targeted + (excess or 0.0), 3),
            giant_after_random_sd=sd,
            components_after_random_mean=round(max(1.0, removed * 1.3), 2),
            targeted_excess=excess,
            # Senza dispersione lo z non esiste: e' un None che significa
            # "non calcolabile", non "zero".
            targeted_z=None if degenerate or excess is None else round(excess / sd, 2),
        ),
    )


# --- community -------------------------------------------------------------


def _sizes(*, suppressed_bucket: Optional[str] = None, populated: bool = True) -> list[CommunitySizeBucket]:
    out: list[CommunitySizeBucket] = []
    counts = {"small": 3, "5-9": 2, "10-19": 1, "20-49": 1, "50-99": 0, "100+": 0}
    members = {"small": 7, "5-9": 13, "10-19": 14, "20-49": 22, "50-99": 0, "100+": 0}
    for bucket in SIZE_BUCKETS:
        if suppressed_bucket is not None and bucket == suppressed_bucket:
            out.append(
                CommunitySizeBucket(
                    bucket=bucket,
                    quality=Quality(
                        n_effective=None,
                        suppressed=True,
                        # Soppressione SECONDARIA: da sola questa cella sarebbe
                        # sopra soglia, ma accanto al totale della riga madre si
                        # ricaverebbe per differenza la cella gia' soppressa.
                        suppression_reason="secondary",
                        significant=None,
                        details={},
                    ),
                    values=CommunitySizeValues(),
                )
            )
            continue
        out.append(
            CommunitySizeBucket(
                bucket=bucket,
                quality=Quality(
                    n_effective=members[bucket] if populated else None,
                    suppressed=False,
                    suppression_reason=None,
                    significant=None,
                    details={},
                ),
                values=CommunitySizeValues(
                    community_count=counts[bucket],
                    member_count=members[bucket],
                ),
            )
        )
    return out


def _community(
    snapshot_id: int,
    as_of: datetime,
    layer: str,
    *,
    n: int,
    significant: Optional[bool],
    stability: Optional[float] = None,
    gap_days: Optional[float] = None,
    previous_id: Optional[int] = None,
    overlap: Optional[float] = None,
    reasons: Optional[list[str]] = None,
    extra_details: Optional[dict[str, Any]] = None,
    suppressed_bucket: Optional[str] = None,
) -> CommunityRow:
    details: dict[str, Any] = {
        "baseline_repetitions_used": 100,
        "baseline_degraded": False,
        "leiden_objective": "modularity",
        "seed": 20260907,
    }
    if extra_details:
        details.update(extra_details)
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
            significant=significant,
            details=details,
        ),
        values=CommunityValues(
            community_count=4 if n > 20 else 3,
            modularity=0.412,
            modularity_random_mean=0.208,
            modularity_random_sd=0.037,
            modularity_z=5.51 if significant else 1.22,
            node_overlap=overlap,
            stability_jaccard=stability,
            previous_gap_days=gap_days,
            communities_born=1 if stability is not None else None,
            communities_dissolved=0 if stability is not None else None,
            communities_merged=1 if stability is not None else None,
            communities_split=0 if stability is not None else None,
        ),
        sizes=_sizes(suppressed_bucket=suppressed_bucket),
    )


# --- coorti ----------------------------------------------------------------


def _onboarding(
    scope: str,
    *,
    n: Optional[int],
    suppressed: bool = False,
    significant: Optional[bool] = None,
    mature: Optional[bool] = None,
    coverage: Optional[bool] = None,
    survivors: Optional[bool] = None,
    observation_days: Optional[int] = None,
    median: Optional[float] = None,
    median_reached: Optional[bool] = None,
    reasons: Optional[list[str]] = None,
) -> OnboardingRow:
    if suppressed:
        return OnboardingRow(
            layer_scope=scope,
            k=K,
            quality=OnboardingQuality(
                n_effective=None,
                suppressed=True,
                suppression_reason="below_threshold",
                significant=None,
                details={},
            ),
            values=OnboardingValues(),
        )
    details: dict[str, Any] = {}
    if reasons:
        details["not_significant_because"] = reasons
    return OnboardingRow(
        layer_scope=scope,
        k=K,
        quality=OnboardingQuality(
            n_effective=n,
            suppressed=False,
            suppression_reason=None,
            significant=significant,
            is_survivors_only=survivors,
            excluded_rejoins=0,
            is_mature=mature,
            has_snapshot_coverage=coverage,
            observation_days=observation_days,
            details=details,
        ),
        values=OnboardingValues(
            event_count=n,
            censored_count=max(0, (n or 0) - 4),
            censored_by_leave=1,
            median_days_to_k=median,
            # Flag PUNTUALE: qualifica solo median_days_to_k. Con meta' coorte
            # che non arriva a k, la mediana non esiste e non e' un errore.
            median_reached=median_reached,
            p25_days_to_k=None if median is None else round(median * 0.6, 1),
            p75_days_to_k=None if median is None else round(median * 1.8, 1),
            reached_by_14d=None if median is None else 0.38,
            reached_by_28d=None if median is None else 0.61,
        ),
    )


def _retention(
    horizon: int,
    *,
    n: Optional[int],
    suppressed: bool = False,
    fraction: Optional[float] = None,
    computable: Optional[bool] = None,
    reason: Optional[str] = None,
    survivors: Optional[bool] = None,
) -> RetentionRow:
    if suppressed:
        return RetentionRow(
            horizon_days=horizon,
            quality=CohortQuality(
                n_effective=None,
                suppressed=True,
                suppression_reason="below_threshold",
                significant=None,
                details={},
            ),
            values=RetentionValues(),
        )
    return RetentionRow(
        horizon_days=horizon,
        quality=CohortQuality(
            n_effective=n,
            suppressed=False,
            suppression_reason=None,
            significant=None,
            is_survivors_only=survivors,
            excluded_rejoins=0,
            details={},
        ),
        values=RetentionValues(
            retained_fraction=fraction,
            is_computable=computable,
            not_computable_reason=reason,
        ),
    )


# --- gli scenari -----------------------------------------------------------


def _scenario_today() -> dict[str, Any]:
    """Lo stato reale del 12/09/2026: un solo snapshot, niente di significativo."""
    as_of = MONDAY
    sid = 11
    n_by_layer = {"voice": 14, "reply": 24, "mention": 26, "reaction": 22}

    robustness = []
    for layer in LAYERS:
        n = n_by_layer[layer]
        for rf in REMOVAL_FRACTIONS:
            removed = int(n * rf)
            reasons = ["too_few_nodes"]
            if removed < 2:
                # Il caso 0.05: la percentuale dice 5%, il fatto e' "un nodo".
                reasons.append("too_few_nodes_removed")
            robustness.append(
                _robustness(
                    sid, as_of, layer, rf,
                    n=n, significant=False, excess=0.0 if removed == 0 else 0.13,
                    reasons=reasons, nodes_removed=removed,
                )
            )

    communities = [
        _community(
            sid, as_of, layer,
            n=n_by_layer[layer], significant=False,
            stability=None, gap_days=None, previous_id=None, overlap=None,
            reasons=["too_few_nodes", "modularity_indistinguishable_from_random"],
            extra_details={"stability_unavailable": "no_previous_snapshot"},
        )
        for layer in LAYERS
    ]

    cohorts = [
        CohortGroup(
            snapshot_id=sid, as_of=as_of, cohort_start=date(2026, 8, 31),
            onboarding=[
                _onboarding(
                    s, n=6, significant=False, mature=False, coverage=True,
                    survivors=False, observation_days=1,
                    median=None, median_reached=False,
                    reasons=["cohort_not_mature"],
                )
                for s in COHORT_SCOPES
            ],
            retention=[
                _retention(h, n=6, fraction=None, computable=False,
                           reason="horizon_not_reached", survivors=False)
                for h in RETENTION_HORIZONS
            ],
        ),
        CohortGroup(
            snapshot_id=sid, as_of=as_of, cohort_start=date(2026, 8, 17),
            onboarding=[
                _onboarding(
                    s, n=5, significant=False, mature=True, coverage=False,
                    survivors=True, observation_days=21,
                    median=None, median_reached=False,
                    reasons=["no_snapshot_coverage", "survivors_only_cohort"],
                )
                for s in COHORT_SCOPES
            ],
            retention=[
                _retention(h, n=5, fraction=None, computable=False,
                           reason="before_observability_anchor", survivors=True)
                for h in RETENTION_HORIZONS
            ],
        ),
    ]

    return {
        "guild": GuildRow(
            guild_id=GUILD_TODAY,
            first_seen_at=datetime(2026, 8, 28, 15, 28, tzinfo=timezone.utc),
            backfilled_at=datetime(2026, 8, 28, 15, 31, tzinfo=timezone.utc),
            left_at=None, rejoined_at=None, latest_metrics_as_of=as_of,
        ),
        "runs": [
            RunRow(
                snapshot_id=sid, as_of=as_of,
                params={"min_cardinality": 5, "k_connections": 5, "min_nodes_structural": 30},
                stats={"durations_ms": {"robustness": 4180, "communities": 2610, "cohorts": 890},
                       "snapshots_used": 1},
                code_version="dfc1696",
                created_at=as_of + timedelta(hours=4, minutes=15),
            )
        ],
        "robustness": robustness,
        "communities": communities,
        "cohorts": cohorts,
    }


def _scenario_mature() -> dict[str, Any]:
    """Dodici settimane di serie: significativo, stabile, retention calcolabile."""
    runs, robustness, communities, cohorts = [], [], [], []
    n_by_layer = {"voice": 52, "reply": 88, "mention": 91, "reaction": 79}

    for i in range(12):
        sid = 40 - i
        as_of = _as_of(i)
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
        for layer in LAYERS:
            n = n_by_layer[layer] - i
            for rf in REMOVAL_FRACTIONS:
                robustness.append(
                    _robustness(
                        sid, as_of, layer, rf, n=n, significant=True,
                        excess=round(0.18 + rf + i * 0.004, 3),
                    )
                )
            communities.append(
                _community(
                    sid, as_of, layer, n=n, significant=True,
                    stability=round(0.74 - i * 0.008, 3),
                    gap_days=7.0, previous_id=sid - 1, overlap=0.88,
                )
            )

    for w in range(4):
        start = date(2026, 8, 3) + timedelta(weeks=w)
        cohorts.append(
            CohortGroup(
                snapshot_id=40, as_of=MONDAY, cohort_start=start,
                onboarding=[
                    _onboarding(
                        s, n=18 + w, significant=True, mature=True, coverage=True,
                        survivors=False, observation_days=40 - w * 7,
                        median=9.5 + w, median_reached=True,
                    )
                    for s in COHORT_SCOPES
                ],
                retention=[
                    _retention(h, n=18 + w, fraction=round(0.81 - h * 0.006, 3),
                               computable=True, survivors=False)
                    for h in RETENTION_HORIZONS
                ],
            )
        )

    return {
        "guild": GuildRow(
            guild_id=GUILD_MATURE,
            first_seen_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
            backfilled_at=datetime(2026, 3, 2, 9, 4, tzinfo=timezone.utc),
            left_at=None, rejoined_at=None, latest_metrics_as_of=MONDAY,
        ),
        "runs": runs,
        "robustness": robustness,
        "communities": communities,
        "cohorts": cohorts,
    }


def _scenario_edge() -> dict[str, Any]:
    """Gli stati che mordono. Ogni riga qui esiste per rompere una vista.

    Se la dashboard rende questo scenario senza far sembrare un errore niente
    che errore non e', e senza far sembrare un valore niente che valore non e',
    allora regge anche i dati veri quando arriveranno.
    """
    as_of = MONDAY
    sid = 77

    robustness = [
        # Riga soppressa: values c'e', tutti i campi a None. Non si nasconde.
        _robustness(sid, as_of, "voice", 0.05, n=0, significant=None, suppressed=True),
        # targeted_excess NEGATIVO e non clampato: i nodi centrali erano MENO
        # critici di nodi presi a caso. Un asse y ancorato a zero lo nasconde.
        _robustness(sid, as_of, "voice", 0.10, n=61, significant=True, excess=-0.07),
        # Baseline degenere: sd = 0, quindi targeted_z e' None. Non e' uno zero.
        _robustness(sid, as_of, "voice", 0.20, n=61, significant=False,
                    excess=0.21, degenerate=True, reasons=["degenerate_baseline"]),
        # Zero nodi rimossi: excess 0.0 che NON significa "robusto".
        _robustness(sid, as_of, "reply", 0.05, n=31, significant=False,
                    excess=0.0, nodes_removed=1, reasons=["too_few_nodes_removed"]),
        _robustness(sid, as_of, "reply", 0.10, n=31, significant=True, excess=0.33),
        _robustness(sid, as_of, "reply", 0.20, n=31, significant=True, excess=0.44),
    ]

    communities = [
        # Gap anomalo: 1.2 giorni significa finestre sovrapposte all'85-95%, e
        # stability_jaccard misura la sovrapposizione, non la ricomposizione.
        _community(sid, as_of, "voice", n=61, significant=True,
                   stability=0.97, gap_days=1.2, previous_id=76, overlap=0.94),
        # Sovrapposizione di nodi sotto il minimo: la stabilita' non si calcola
        # affatto, ma born/dissolved/merged/split ci sono lo stesso.
        _community(sid, as_of, "reply", n=31, significant=False,
                   stability=None, gap_days=7.0, previous_id=76, overlap=0.21,
                   reasons=["node_overlap_below_minimum"],
                   extra_details={"stability_unavailable": "node_overlap_below_minimum"}),
        # Modularita' indistinguibile dal caso, con nodi a sufficienza: passare
        # i 30 nodi non basta a rendere una riga significativa.
        _community(sid, as_of, "mention", n=44, significant=False,
                   stability=0.66, gap_days=7.0, previous_id=76, overlap=0.81,
                   reasons=["modularity_indistinguishable_from_random"]),
        # Soppressione secondaria su un bucket della distribuzione.
        _community(sid, as_of, "reaction", n=38, significant=True,
                   stability=0.71, gap_days=7.0, previous_id=76, overlap=0.86,
                   suppressed_bucket="20-49"),
    ]

    cohorts = [
        # Coorte interamente soppressa: n < 5. Tutte le righe, entrambe le liste.
        CohortGroup(
            snapshot_id=sid, as_of=as_of, cohort_start=date(2026, 9, 7),
            onboarding=[_onboarding(s, n=None, suppressed=True) for s in COHORT_SCOPES],
            retention=[_retention(h, n=None, suppressed=True) for h in RETENTION_HORIZONS],
        ),
        # Onboarding pubblicabile ma mediana non raggiunta: median_days_to_k e'
        # None mentre il resto della riga e' valido. Flag puntuale, non di riga.
        CohortGroup(
            snapshot_id=sid, as_of=as_of, cohort_start=date(2026, 8, 24),
            onboarding=[
                _onboarding(s, n=11, significant=True, mature=True, coverage=True,
                            survivors=False, observation_days=28,
                            median=None, median_reached=False)
                for s in COHORT_SCOPES
            ],
            retention=[
                _retention(7, n=11, fraction=0.73, computable=True, survivors=False),
                _retention(14, n=11, fraction=0.64, computable=True, survivors=False),
                _retention(28, n=11, fraction=None, computable=False,
                           reason="horizon_not_reached", survivors=False),
            ],
        ),
        # Coorte di soli sopravvissuti: i numeri esistono, ma contano le persone
        # sbagliate. Mostrabile, mai senza il badge.
        CohortGroup(
            snapshot_id=sid, as_of=as_of, cohort_start=date(2026, 2, 9),
            onboarding=[
                _onboarding(s, n=9, significant=False, mature=True, coverage=True,
                            survivors=True, observation_days=180,
                            median=6.0, median_reached=True,
                            reasons=["survivors_only_cohort"])
                for s in COHORT_SCOPES
            ],
            retention=[
                # NON calcolabile, e non e' una scelta di comodo: job/cohorts.py
                # rifiuta sempre la retention su una coorte di soli
                # sopravvissuti, perche' varrebbe 1.0 per costruzione — "una
                # tautologia, non un risultato, ed e' esattamente il tipo di
                # numero che sembra buono". Una fixture con 1.0 qui avrebbe
                # insegnato alla dashboard a rendere una riga che l'API non puo'
                # produrre, e a non rendere quella che produce davvero.
                _retention(h, n=9, fraction=None, computable=False,
                           reason="before_observability_anchor", survivors=True)
                for h in RETENTION_HORIZONS
            ],
        ),
    ]

    return {
        # left_at E rejoined_at entrambi valorizzati: l'ancora di osservabilita'
        # non e' piu' un istante solo, c'e' un buco in mezzo e le uscite
        # avvenute li' dentro sono invisibili. Cambia la lettura di OGNI coorte.
        "guild": GuildRow(
            guild_id=GUILD_EDGE,
            first_seen_at=datetime(2026, 1, 12, 11, 0, tzinfo=timezone.utc),
            backfilled_at=datetime(2026, 1, 12, 11, 6, tzinfo=timezone.utc),
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
                       "snapshot_gaps": {"cadence_days_median": 7.0, "max_gap_days": 47.4}},
                # code_version assente: una run scritta da un'immagine che non
                # lo incideva. E' successo davvero, per mesi.
                code_version=None,
                created_at=as_of + timedelta(hours=4, minutes=15),
            )
        ],
        "robustness": robustness,
        "communities": communities,
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
    return _guild(guild_id)["runs"][:limit]


@app.get("/guilds/{guild_id}/robustness", response_model=list[RobustnessRow])
def robustness(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[RobustnessRow]:
    return _guild(guild_id)["robustness"][: limit * len(LAYERS) * len(REMOVAL_FRACTIONS)]


@app.get("/guilds/{guild_id}/communities", response_model=list[CommunityRow])
def communities(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[CommunityRow]:
    return _guild(guild_id)["communities"][: limit * len(LAYERS)]


@app.get("/guilds/{guild_id}/cohorts", response_model=list[CohortGroup])
def cohorts(guild_id: int, limit: int = Query(12, ge=1, le=200)) -> list[CohortGroup]:
    return _guild(guild_id)["cohorts"][:limit]


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
    for nome in ("job/suppression.py", "job/cohorts.py", "job/communities.py",
                 "job/robustness.py"):
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


def check() -> int:
    """Round-trip: si serializza e si rivalida con i modelli veri.

    Non e' una formalita'. Le fixture sono costruite istanziando i modelli,
    quindi un campo sbagliato fallisce gia' all'import; questo controllo prende
    il caso diverso e piu' insidioso — una fixture che l'API non potrebbe
    produrre, per esempio valori non nulli su una riga soppressa, che in
    produzione un trigger Postgres rifiuta e qui passerebbe.
    """
    problems: list[str] = _codici_estranei_al_job()
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
