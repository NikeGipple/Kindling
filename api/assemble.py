"""Da righe di database a modelli di risposta. Funzioni pure, nessun database.

Sta in un modulo a se' per la stessa ragione per cui ``job/snapshot.py`` e'
separato da ``job/db.py``: la parte che si puo' sbagliare in silenzio — un flag
che finisce nel posto sbagliato, un raggruppamento che collassa due
qualificazioni in una — deve essere provabile senza un Postgres a portata.

Le righe arrivano come mappature (``asyncpg.Record`` o dizionari): il tipo non
conta, contano le chiavi — e questo modulo **non importa asyncpg**, altrimenti
"funzioni pure, nessun database" sarebbe vero solo a parole.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .models import (
    CohortGroup,
    CohortQuality,
    CommunityRow,
    CommunitySizeBucket,
    CommunitySizeValues,
    CommunityValues,
    GuildRow,
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


def as_dict(raw: Any) -> dict[str, Any]:
    """Una colonna JSONB come dizionario.

    asyncpg restituisce jsonb come stringa se non e' registrato un codec: qui si
    decodifica una volta sola, e un contenuto illeggibile diventa un dizionario
    vuoto invece di far fallire l'intera risposta. ``details`` e ``stats`` sono
    diagnostica, e una diagnostica malformata non deve impedire di leggere la
    metrica a cui e' attaccata.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _quality(row: Mapping[str, Any], model=Quality, **extra: Any):
    """La qualificazione comune a ogni riga di metrica.

    ``suppressed`` e' l'unico campo non nullable: e' sempre una risposta, mentre
    ``significant`` a None significa "non valutata" ed e' uno stato diverso da
    False.
    """
    return model(
        n_effective=row.get("n_effective"),
        suppressed=bool(row.get("is_suppressed")),
        suppression_reason=row.get("suppression_reason"),
        significant=row.get("is_significant"),
        details=as_dict(row.get("details")),
        **extra,
    )


def guild(row: Mapping[str, Any]) -> GuildRow:
    return GuildRow(
        guild_id=row["guild_id"],
        first_seen_at=row["first_seen_at"],
        backfilled_at=row.get("backfilled_at"),
        left_at=row.get("left_at"),
        rejoined_at=row.get("rejoined_at"),
        latest_metrics_as_of=row.get("latest_metrics_as_of"),
    )


def run(row: Mapping[str, Any]) -> RunRow:
    return RunRow(
        snapshot_id=row["snapshot_id"],
        as_of=row["as_of"],
        params=as_dict(row.get("params")),
        stats=as_dict(row.get("stats")),
        code_version=row.get("code_version"),
        created_at=row.get("created_at"),
    )


def robustness(row: Mapping[str, Any]) -> RobustnessRow:
    return RobustnessRow(
        snapshot_id=row["snapshot_id"],
        as_of=row["as_of"],
        layer=row["layer"],
        removal_fraction=row["removal_fraction"],
        quality=_quality(row),
        values=RobustnessValues(
            nodes_removed=row.get("nodes_removed"),
            giant_before=row.get("giant_before"),
            giant_after_targeted=row.get("giant_after_targeted"),
            components_after_targeted=row.get("components_after_targeted"),
            giant_after_random_mean=row.get("giant_after_random_mean"),
            giant_after_random_sd=row.get("giant_after_random_sd"),
            components_after_random_mean=row.get("components_after_random_mean"),
            targeted_excess=row.get("targeted_excess"),
            targeted_z=row.get("targeted_z"),
        ),
    )


def communities(
    rows: Iterable[Mapping[str, Any]], sizes: Iterable[Mapping[str, Any]]
) -> list[CommunityRow]:
    """Righe di community, ognuna con la distribuzione della propria partizione.

    L'accoppiamento e' su ``(snapshot_id, layer)``, che e' la chiave primaria di
    ``metric_communities``: un bucket appartiene a quella partizione e a nessuna
    altra. Ogni bucket porta il proprio ``quality``, perche' la soppressione qui
    e' per bucket e puo' essere secondaria.
    """
    by_partition: dict[tuple[int, str], list[CommunitySizeBucket]] = {}
    for size in sizes:
        key = (size["snapshot_id"], size["layer"])
        by_partition.setdefault(key, []).append(
            CommunitySizeBucket(
                bucket=size["bucket"],
                quality=_quality(size),
                values=CommunitySizeValues(
                    community_count=size.get("community_count"),
                    member_count=size.get("member_count"),
                ),
            )
        )

    result = []
    for row in rows:
        result.append(
            CommunityRow(
                snapshot_id=row["snapshot_id"],
                as_of=row["as_of"],
                layer=row["layer"],
                previous_snapshot_id=row.get("previous_snapshot_id"),
                quality=_quality(row),
                values=CommunityValues(
                    community_count=row.get("community_count"),
                    modularity=row.get("modularity"),
                    modularity_random_mean=row.get("modularity_random_mean"),
                    modularity_random_sd=row.get("modularity_random_sd"),
                    modularity_z=row.get("modularity_z"),
                    node_overlap=row.get("node_overlap"),
                    stability_jaccard=row.get("stability_jaccard"),
                    communities_born=row.get("communities_born"),
                    communities_dissolved=row.get("communities_dissolved"),
                    communities_merged=row.get("communities_merged"),
                    communities_split=row.get("communities_split"),
                ),
                sizes=by_partition.get((row["snapshot_id"], row["layer"]), []),
            )
        )
    return result


def cohorts(
    rows: Iterable[Mapping[str, Any]], retention: Iterable[Mapping[str, Any]]
) -> list[CohortGroup]:
    """Coorti con onboarding e retention insieme, raggruppate per coorte.

    Le due tabelle condividono la popolazione per progetto
    (modello-metriche.md 5.5): servirle separate inviterebbe a leggerle
    separate, che e' il modo di fallire contro cui la duplicazione di
    ``n_effective``/``excluded_rejoins``/``is_survivors_only`` esiste.

    ``cohort_start`` e' solo la chiave che le tiene insieme: **ogni riga
    annidata porta il proprio quality**, perche' onboarding e retention della
    stessa coorte possono essere soppressi o non calcolabili per ragioni
    diverse, e collassarli in una qualificazione sola cancellerebbe proprio la
    differenza.
    """
    groups: dict[tuple[int, Any], CohortGroup] = {}

    def group_for(snapshot_id: int, cohort_start: Any, as_of: Any) -> CohortGroup:
        key = (snapshot_id, cohort_start)
        if key not in groups:
            groups[key] = CohortGroup(
                snapshot_id=snapshot_id, as_of=as_of, cohort_start=cohort_start
            )
        return groups[key]

    for row in rows:
        group = group_for(row["snapshot_id"], row["cohort_start"], row["as_of"])
        group.onboarding.append(
            OnboardingRow(
                layer_scope=row["layer_scope"],
                k=row["k"],
                quality=_quality(
                    row,
                    model=OnboardingQuality,
                    is_survivors_only=row.get("is_survivors_only"),
                    excluded_rejoins=row.get("excluded_rejoins"),
                    is_mature=row.get("is_mature"),
                    has_snapshot_coverage=row.get("has_snapshot_coverage"),
                    observation_days=row.get("observation_days"),
                ),
                values=OnboardingValues(
                    event_count=row.get("event_count"),
                    censored_count=row.get("censored_count"),
                    censored_by_leave=row.get("censored_by_leave"),
                    median_days_to_k=row.get("median_days_to_k"),
                    median_reached=row.get("median_reached"),
                    p25_days_to_k=row.get("p25_days_to_k"),
                    p75_days_to_k=row.get("p75_days_to_k"),
                    reached_by_14d=row.get("reached_by_14d"),
                    reached_by_28d=row.get("reached_by_28d"),
                ),
            )
        )

    for row in retention:
        # as_of arriva dalla query, non da una mappa costruita sulle righe di
        # onboarding: una coorte puo' avere righe di retention e nessuna riga di
        # onboarding (k diverso, ambito non calcolato), e dedurre l'as_of da
        # quelle altre significherebbe non averlo proprio in quel caso.
        group = group_for(row["snapshot_id"], row["cohort_start"], row["as_of"])
        group.retention.append(
            RetentionRow(
                horizon_days=row["horizon_days"],
                quality=_quality(
                    row,
                    model=CohortQuality,
                    is_survivors_only=row.get("is_survivors_only"),
                    excluded_rejoins=row.get("excluded_rejoins"),
                ),
                values=RetentionValues(
                    retained_fraction=row.get("retained_fraction"),
                    is_computable=row.get("is_computable"),
                    not_computable_reason=row.get("not_computable_reason"),
                ),
            )
        )

    return sorted(
        groups.values(),
        key=lambda g: (g.snapshot_id, g.cohort_start),
        reverse=True,
    )
