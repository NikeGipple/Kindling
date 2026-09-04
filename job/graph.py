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

from .admission import admitted_pairs
from .config import MetricParams, UNDIRECTED_LAYERS
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


def build_metric_graph(
    edges: Iterable[Edge],
    *,
    layer: str,
    params: MetricParams,
    include_reconciled: bool = True,
) -> igraph.Graph:
    """Grafo di un layer per le metriche strutturali (modello-metriche.md 2).

    Tre differenze rispetto a ``build_layer_graph``, tutte volute:

    - **Sempre non diretto.** I layer direzionali entrano come proiezione: i due
      orientamenti della stessa coppia diventano un arco solo, con peso pari
      alla somma dei due. Robustezza e Leiden sono domande sulla connettivita',
      che si legge sul grafo non orientato. La somma avviene DENTRO un layer,
      tra due orientamenti della stessa relazione, non tra relazioni di tipo
      diverso: non e' la fusione che modello-grafo.md 1 vieta.
    - **Ammissione condivisa.** Proiezione e soglia vengono da
      ``admission.admitted_pairs``, la stessa funzione che usa il conteggio dei
      partner delle coorti: due regole separate coinciderebbero solo finche'
      ``min_edge_weight`` vale 0.0. Un arco di peso nullo — o quasi — tiene
      comunque insieme due componenti, perche' le componenti connesse i pesi
      non li guardano: lasciarlo dentro falserebbe proprio le grandezze della
      robustezza.
    - **Ordinamento deterministico dei nodi**, per author_id crescente. Il
      risultato di Leiden dipende dall'ordine in cui i nodi sono visitati: se
      quell'ordine dipendesse dall'ordine di lettura degli archi da Postgres,
      il seed da solo non basterebbe a rendere il calcolo riproducibile — e la
      riproducibilita' e' cio' che permette di ricostruire la partizione dello
      snapshot precedente invece di persisterla.

    Il grafo e' indotto dai suoi archi: un membro senza archi in questo layer
    non e' un nodo. ``author_id`` resta come attributo di vertice ed e' un dato
    interno al calcolo, che non esce mai da questo modulo (modello-metriche.md
    8).
    """
    combined = {
        (pair.low, pair.high): entry.weight
        for pair, entry in admitted_pairs(
            edges,
            params=params,
            layer=layer,
            include_reconciled=include_reconciled,
        ).items()
    }

    author_ids = sorted({author for pair in combined for author in pair})
    index = {author_id: position for position, author_id in enumerate(author_ids)}

    graph = igraph.Graph(directed=False)
    graph.add_vertices(len(author_ids))
    graph.vs["author_id"] = author_ids

    pairs = sorted(combined)
    graph.add_edges([(index[src], index[dst]) for src, dst in pairs])
    graph.es["weight"] = [combined[pair] for pair in pairs]

    graph["layer"] = layer
    return graph
