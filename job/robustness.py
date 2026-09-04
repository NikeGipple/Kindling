"""Robustezza strutturale: quanto la connettivita' dipende da pochi nodi.

Implementa modello-metriche.md 3. La domanda del catalogo e' se la community
dipende da un numero ristretto di connettori (fragile) o se la connettivita'
regge anche togliendo i nodi piu' centrali (distribuita).

Due cose sono facili da sbagliare qui, e sono entrambe nella specifica prima
che nel codice:

- **igraph interpreta i pesi come LUNGHEZZE, non come forze.** Il peso di un
  arco di Kindling e' una forza: piu' alto, piu' le due persone sono legate.
  Passarlo direttamente a ``betweenness(weights=...)`` significherebbe dire a
  igraph che i legami forti sono lontani, e il risultato sarebbe la betweenness
  di una rete rovesciata. La distanza e' ``1/weight``.
- **Senza baseline il numero non dice niente.** Qualunque grafo si frammenta se
  si toglie abbastanza: la domanda e' se si frammenta piu' di quanto farebbe
  togliendo nodi a caso.

Nessun output per-nodo lascia questo modulo: il vettore di betweenness e
l'elenco dei nodi rimossi restano dentro le funzioni che li producono.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

import igraph

from .config import MetricParams


@dataclass
class RobustnessResult:
    """Una riga di ``metric_robustness``, prima della soppressione."""

    layer: str
    removal_fraction: float
    n_effective: Optional[int] = None
    nodes_removed: Optional[int] = None
    giant_before: Optional[float] = None
    giant_after_targeted: Optional[float] = None
    components_after_targeted: Optional[int] = None
    giant_after_random_mean: Optional[float] = None
    giant_after_random_sd: Optional[float] = None
    components_after_random_mean: Optional[float] = None
    targeted_excess: Optional[float] = None
    targeted_z: Optional[float] = None
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None
    is_significant: Optional[bool] = None
    details: dict[str, Any] = field(default_factory=dict)

    # Chiave primaria della riga: e' cio' che sopravvive alla soppressione.
    KEY_FIELDS = ("layer", "removal_fraction")


def _giant_fraction(graph: igraph.Graph, *, total_nodes: int) -> float:
    """Frazione di nodi nella componente connessa piu' grande.

    Sempre rapportata a ``total_nodes``, cioe' ai nodi del grafo PRIMA della
    rimozione, mai ai residui: togliere il 20% dei nodi e trovare che "il 100%
    dei rimasti e' ancora connesso" e' vero e fuorviante.
    """
    if total_nodes == 0:
        return 0.0
    if graph.vcount() == 0:
        return 0.0
    return max(len(component) for component in graph.components()) / total_nodes


def _components(graph: igraph.Graph) -> int:
    return len(graph.components()) if graph.vcount() else 0


def _attack_order(graph: igraph.Graph) -> list[int]:
    """Nodi ordinati per criticita' decrescente.

    Betweenness con distanza ``1/weight`` (vedi il docstring del modulo), e
    tiebreak deterministico su ``author_id``: su grafi piccoli molti nodi hanno
    betweenness zero e l'ordinamento sarebbe altrimenti arbitrario, cioe'
    dipendente dall'ordine in cui igraph ha ricevuto gli archi.
    """
    distances = [1.0 / weight for weight in graph.es["weight"]] if graph.ecount() else None
    scores = graph.betweenness(weights=distances)
    author_ids = graph.vs["author_id"]
    return sorted(
        range(graph.vcount()), key=lambda v: (-scores[v], author_ids[v])
    )


def compute_robustness(
    graph: igraph.Graph,
    *,
    layer: str,
    removal_fraction: float,
    params: MetricParams,
    rng: random.Random,
) -> Optional[RobustnessResult]:
    """Robustezza di un layer a una data frazione di rimozione.

    ``None`` se il grafo e' vuoto: non c'e' niente da misurare e una riga di
    zeri direbbe "rete perfettamente frammentata", che e' un'altra cosa.
    """
    n = graph.vcount()
    if n == 0:
        return None

    nodes_removed = max(1, math.ceil(removal_fraction * n))
    repetitions, degraded = params.baseline_repetitions_for(n)

    giant_before = _giant_fraction(graph, total_nodes=n)

    order = _attack_order(graph)
    # induced_subgraph sui superstiti invece di copy() + delete_vertices: il
    # grafo residuo si costruisce una volta sola, senza copiare prima l'intero
    # grafo per poi smontarlo. Conta perche' questo giro si ripete R volte per
    # ogni valore di X.
    removed = set(order[:nodes_removed])
    targeted = graph.induced_subgraph([v for v in range(n) if v not in removed])
    giant_targeted = _giant_fraction(targeted, total_nodes=n)
    components_targeted = _components(targeted)

    # Stesso NUMERO di nodi rimossi, non la stessa percentuale ricalcolata.
    random_giants: list[float] = []
    random_components: list[int] = []
    for _ in range(repetitions):
        victims = set(rng.sample(range(n), nodes_removed))
        residual = graph.induced_subgraph([v for v in range(n) if v not in victims])
        random_giants.append(_giant_fraction(residual, total_nodes=n))
        random_components.append(_components(residual))

    giant_random_mean = statistics.fmean(random_giants)
    giant_random_sd = statistics.pstdev(random_giants) if len(random_giants) > 1 else 0.0
    components_random_mean = statistics.fmean(random_components)

    # Puo' essere negativo, e non va clampato: significa che i nodi piu'
    # centrali erano meno critici di nodi presi a caso, che su una rete a
    # connettivita' distribuita e' un risultato legittimo e informativo.
    targeted_excess = (giant_random_mean - giant_targeted) / giant_before

    targeted_z: Optional[float] = None
    if giant_random_sd > 0.0:
        targeted_z = (giant_random_mean - giant_targeted) / giant_random_sd

    reasons: list[str] = []
    if n < params.min_nodes_structural:
        reasons.append("too_few_nodes")
    if nodes_removed < 2:
        reasons.append("too_few_nodes_removed")
    if giant_random_sd == 0.0:
        reasons.append("degenerate_baseline")

    details: dict[str, Any] = {
        "baseline_repetitions_used": repetitions,
        "baseline_degraded": degraded,
    }
    if reasons:
        details["not_significant_because"] = reasons

    return RobustnessResult(
        layer=layer,
        removal_fraction=removal_fraction,
        n_effective=n,
        nodes_removed=nodes_removed,
        giant_before=giant_before,
        giant_after_targeted=giant_targeted,
        components_after_targeted=components_targeted,
        giant_after_random_mean=giant_random_mean,
        giant_after_random_sd=giant_random_sd,
        components_after_random_mean=components_random_mean,
        targeted_excess=targeted_excess,
        targeted_z=targeted_z,
        is_significant=not reasons,
        details=details,
    )


def sensitivity_details(
    baseline: Optional[RobustnessResult], *, identical: bool = False
) -> dict[str, Any]:
    """Il blocco di sensibilita' da aggiungere a ``details``.

    Gli archi ricostruiti sono INCLUSI nel calcolo principale: escluderli per
    default introdurrebbe un bias verso i periodi di downtime del bot, perche'
    quella co-presenza e' avvenuta davvero. Ma la riconciliazione inventa un
    estremo dell'intervallo, e la domanda da poter rifare non e' "quanto vale
    la metrica senza i ricostruiti" — e' "la lettura cambia se li tolgo".

    Tre stati distinti, e vanno tenuti distinti a valle: ``identical`` (nessun
    arco ricostruito, quindi la variante coincide e non e' stata ricalcolata),
    i valori della variante, oppure ``None`` (non calcolabile). "Nessuna
    differenza" e "non calcolata" non sono la stessa cosa.
    """
    if identical:
        return {"without_reconciled": {"identical": True}}
    if baseline is None:
        return {"without_reconciled": None}
    return {
        "without_reconciled": {
            "identical": False,
            "n_effective": baseline.n_effective,
            "targeted_excess": baseline.targeted_excess,
        }
    }

