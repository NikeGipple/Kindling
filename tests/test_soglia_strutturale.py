"""`min_nodes_structural` segue la griglia di rimozione (modello-metriche.md 7.1).

La soglia e' il piu' piccolo ``n`` per cui la frazione piu' piccola di
``removal_fractions`` toglie almeno 2 nodi: sotto, una rimozione di un nodo solo
e' un aneddoto (``too_few_nodes_removed``). Non e' un numero scelto sui dati di
un server, quindi non deve poter restare fermo se la griglia cambia.

Il numero di nodi rimossi non si ricalcola qui con una formula ricopiata: lo si
legge da ``compute_robustness``, cioe' da cio' che il job fa davvero. Una copia
della formula resterebbe d'accordo con se stessa anche se il job cambiasse
arrotondamento.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime, timezone

import pytest

from job.config import LAYER_REPLY, MetricParams
from job.edges import Edge
from job.graph import build_metric_graph
from job.robustness import compute_robustness

T0 = datetime(2026, 9, 28, tzinfo=timezone.utc)

# I default, tranne le ripetizioni del baseline: qui conta quali motivi scattano,
# non la precisione di targeted_z.
PARAMS = dataclasses.replace(MetricParams(), baseline_repetitions=20)


def ring(n: int) -> list[Edge]:
    """Anello di ``n`` nodi con una corda: connesso, e non cosi' regolare da
    rendere degenere il baseline casuale."""
    pairs = [(i, i % n + 1) for i in range(1, n + 1)] + [(1, n // 2 + 1)]
    return [
        Edge(
            layer=LAYER_REPLY,
            src_author_id=min(a, b),
            dst_author_id=max(a, b),
            weight=1.0,
            weight_undecayed=1.0,
            raw_units=1.0,
            interaction_count=1,
            last_interaction_at=T0,
        )
        for a, b in pairs
    ]


def robustness(n: int, fraction: float):
    graph = build_metric_graph(ring(n), layer=LAYER_REPLY, params=PARAMS)
    assert graph.vcount() == n
    return compute_robustness(
        graph,
        layer=LAYER_REPLY,
        removal_fraction=fraction,
        params=PARAMS,
        rng=random.Random(f"soglia-{n}-{fraction}"),
    )


def reasons(row) -> set[str]:
    return set(row.details.get("not_significant_because", []))


def test_la_soglia_e_il_piu_piccolo_n_che_la_griglia_rende_significativo():
    # Fallisce nei due versi: una soglia piu' bassa lascerebbe passare righe da
    # un nodo rimosso, una piu' alta scarterebbe grafi che la griglia gia'
    # misura con due nodi o piu'.
    smallest = min(PARAMS.removal_fractions)
    derived = next(n for n in range(3, 200) if robustness(n, smallest).nodes_removed >= 2)
    assert PARAMS.min_nodes_structural == derived


@pytest.mark.parametrize("fraction", MetricParams().removal_fractions)
def test_sotto_soglia_il_motivo_e_too_few_nodes(fraction):
    n = PARAMS.min_nodes_structural - 1
    row = robustness(n, fraction)
    assert row.is_significant is False
    assert "too_few_nodes" in reasons(row)


def test_un_nodo_sotto_soglia_la_frazione_minima_toglie_un_nodo_solo():
    row = robustness(PARAMS.min_nodes_structural - 1, min(PARAMS.removal_fractions))
    assert row.nodes_removed == 1
    assert reasons(row) >= {"too_few_nodes", "too_few_nodes_removed"}


@pytest.mark.parametrize(
    "n", range(MetricParams().min_nodes_structural, MetricParams().min_nodes_structural + 20)
)
@pytest.mark.parametrize("fraction", MetricParams().removal_fractions)
def test_da_soglia_in_su_resta_possibile_solo_degenerate_baseline(n, fraction):
    # modello-metriche.md 7.1: sopra soglia too_few_nodes_removed non puo'
    # scattare, e l'unico motivo rimasto e' il baseline degenere.
    row = robustness(n, fraction)
    assert row.nodes_removed >= 2
    assert reasons(row) <= {"degenerate_baseline"}
