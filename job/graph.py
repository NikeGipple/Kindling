"""Costruzione del grafo in memoria e export GraphML, con python-igraph.

python-igraph e non NetworkX: il job gira su una droplet da 1 GB condivisa con
il bot e Postgres, e igraph tiene nodi e archi in array compatti invece che in
dict di dict (vedi docs/architettura/architettura.md, sezioni 3 e Hosting).

Il grafo qui e' transiente: si costruisce, si esporta o si misura, e si butta.
Non e' uno stato da mantenere — la fonte di verita' resta ``raw_events``.

Un layer per grafo, sempre: i quattro layer non vengono mai fusi, nemmeno in un
export "solo per comodita' di ispezione".
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

import igraph

from .config import UNDIRECTED_LAYERS
from .edges import Edge

NodeLabeller = Callable[[int], str]


def build_layer_graph(
    edges: Iterable[Edge], *, layer: str, label: Optional[NodeLabeller] = None
) -> igraph.Graph:
    """Grafo igraph di un singolo layer.

    ``label`` mappa un author_id nell'etichetta del nodo: e' il punto in cui si
    innesta la pseudonimizzazione, cosi' che gli id reali non entrino mai
    nell'oggetto esportato invece di essere rimossi dopo.
    """
    label = label or (lambda author_id: str(author_id))
    layer_edges = [e for e in edges if e.layer == layer]

    ids: list[int] = []
    index: dict[int, int] = {}
    for edge in layer_edges:
        for author_id in (edge.src_author_id, edge.dst_author_id):
            if author_id not in index:
                index[author_id] = len(ids)
                ids.append(author_id)

    graph = igraph.Graph(directed=layer not in UNDIRECTED_LAYERS)
    graph.add_vertices(len(ids))
    graph.vs["name"] = [label(author_id) for author_id in ids]

    graph.add_edges(
        [(index[e.src_author_id], index[e.dst_author_id]) for e in layer_edges]
    )
    graph.es["weight"] = [e.weight for e in layer_edges]
    graph.es["weight_undecayed"] = [e.weight_undecayed for e in layer_edges]
    graph.es["raw_units"] = [e.raw_units for e in layer_edges]
    graph.es["interaction_count"] = [e.interaction_count for e in layer_edges]
    graph.es["last_interaction_at"] = [
        e.last_interaction_at.isoformat() if e.last_interaction_at else ""
        for e in layer_edges
    ]
    graph.es["is_reconciled"] = [bool(e.is_reconciled) for e in layer_edges]

    graph["layer"] = layer
    return graph


def write_graphml(graph: igraph.Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    graph.write_graphml(str(path))
