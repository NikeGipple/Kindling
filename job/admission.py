"""Quali coppie entrano in una metrica: una regola sola, per tutti i percorsi.

La regola di ammissione di modello-metriche.md 2.3 viveva in due posti — nella
costruzione del grafo strutturale e nel conteggio dei partner delle coorti — e
le due versioni coincidevano solo finche' ``min_edge_weight`` valeva 0.0. Il
grafo somma i due orientamenti di un layer direzionale PRIMA di confrontare con
la soglia; il conteggio dei partner filtrava riga per riga. Alla prima volta che
la soglia si alza — cosa che 2.3 prevede esplicitamente — una coppia con due
orientamenti sotto soglia che sommano sopra sarebbe un partner per le metriche
strutturali e non per le coorti, senza che niente protesti.

Qui la regola e' una: proiezione non diretta (i due orientamenti diventano una
coppia sola, pesi e conteggi sommati), poi le soglie. Chi la usa sceglie solo
quali soglie applicare, non come proiettare.

Nessuna dipendenza da igraph: questo modulo lo importano sia ``graph.py``, che
igraph ce l'ha, sia ``cohorts.py``, che non deve averlo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

from .config import MetricParams


class EdgeLike(Protocol):
    """Il minimo che serve per decidere se una coppia e' ammessa.

    Lo soddisfano sia ``edges.Edge`` (letto da ``graph_edges``) sia
    ``cohorts.PartnerEdge`` (la vista ridotta usata dalla scansione delle
    coorti): il punto di avere un protocollo invece di un tipo e' che i due
    percorsi passino di qui senza doversi convertire l'uno nell'altro.
    """

    layer: str
    src_author_id: int
    dst_author_id: int
    weight: float
    interaction_count: int


@dataclass(frozen=True)
class Pair:
    """Una coppia canonica di un layer: ``low < high``, sempre."""

    layer: str
    low: int
    high: int


@dataclass
class Admitted:
    """Peso e conteggio della coppia, sommati sui due orientamenti."""

    weight: float = 0.0
    interaction_count: int = 0


def admitted_pairs(
    edges: Iterable[EdgeLike],
    *,
    params: MetricParams,
    layer: Optional[str] = None,
    include_reconciled: bool = True,
    min_interactions: int = 1,
) -> dict[Pair, Admitted]:
    """Le coppie ammesse, gia' proiettate a non dirette.

    - ``layer`` restringe a un solo layer; ``None`` li considera tutti, ognuno
      per conto suo. I pesi di layer diversi non si sommano MAI tra loro: sono
      relazioni di tipo diverso (modello-grafo.md 1), quindi una coppia che sta
      sotto soglia su due layer separati resta sotto soglia, non li mette
      insieme per superarla.
    - ``include_reconciled=False`` produce la variante per la verifica di
      sensibilita' (2.4).
    - ``min_interactions`` e' 1 per il grafo strutturale (nessun vincolo) e
      ``partner_min_interactions`` per il conteggio dei partner. Si applica alla
      somma degli orientamenti, cioe' alla RELAZIONE e non alla singola
      direzione.

    Self-loop esclusi ovunque: rispondersi da soli non e' una relazione.
    """
    combined: dict[Pair, Admitted] = {}
    for edge in edges:
        if layer is not None and edge.layer != layer:
            continue
        if not include_reconciled and getattr(edge, "is_reconciled", False):
            continue
        src, dst = edge.src_author_id, edge.dst_author_id
        if src == dst:
            continue
        low, high = (src, dst) if src < dst else (dst, src)
        key = Pair(layer=edge.layer, low=low, high=high)
        entry = combined.get(key)
        if entry is None:
            entry = Admitted()
            combined[key] = entry
        entry.weight += edge.weight
        entry.interaction_count += edge.interaction_count

    return {
        pair: entry
        for pair, entry in combined.items()
        if entry.weight > params.min_edge_weight
        and entry.interaction_count >= min_interactions
    }
