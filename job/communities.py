"""Struttura e stabilita' delle community con Leiden (modello-metriche.md 4).

Tre trappole, tutte chiuse nella specifica prima che qui:

- **Leiden e' stocastico.** Senza seed fissato due esecuzioni sullo stesso
  grafo danno partizioni diverse, e la "stabilita' tra snapshot" misura il
  rumore dell'algoritmo invece del cambiamento della community. Il seed serve
  anche a una seconda cosa, strutturale: la partizione dello snapshot
  precedente non e' salvata da nessuna parte (e' un dato per-nodo), quindi per
  la stabilita' la si RICOSTRUISCE rieseguendo Leiden sugli archi vecchi — ed e'
  esatta solo se il calcolo e' deterministico.
- **Le community non hanno identita' tra due snapshot.** La community #3 di
  questa settimana non e' la #3 della scorsa: gli indici sono posizioni in una
  lista, non nomi. Un Jaccard sugli indici confronta due insiemi scelti a caso.
- **La modularita' di un grafo casuale non e' zero.** E' positiva e cresce al
  diminuire della dimensione, quindi anche qui serve un baseline.

L'assegnazione nodo -> community non lascia mai questo modulo: le funzioni
pubbliche restituiscono solo aggregati.
"""

from __future__ import annotations

import random
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional

import igraph
import leidenalg

from .config import MetricParams


@contextmanager
def _igraph_rng(rng: random.Random) -> Iterator[None]:
    """Lega il generatore di igraph a ``rng`` per la durata del blocco.

    ``Graph.rewire`` **non** usa il ``random.Random`` che gli passiamo noi: usa
    il generatore di igraph, che di default e' il modulo ``random`` globale.
    Senza questo, il baseline della modularita' non e' riproducibile —
    ``modularity_random_mean``, ``modularity_random_sd`` e ``modularity_z``
    cambiano tra due esecuzioni sullo stesso snapshot, e con loro
    ``is_significant``. E' esattamente il rumore che il seed di
    modello-metriche.md 4.2 esiste per eliminare, e sarebbe il tipo di
    non-determinismo che nessuno nota finche' non confronta due run.

    Il generatore di igraph e' uno **stato globale di processo**: va
    ripristinato e non impostato una volta e dimenticato, altrimenti il calcolo
    delle metriche cambierebbe il comportamento di qualunque altro uso di igraph
    nello stesso processo. python-igraph non espone un getter per leggere quello
    installato, quindi si ripristina il suo default documentato — il modulo
    ``random`` — invece di lasciare il nostro al suo posto.
    """
    igraph.set_random_number_generator(rng)
    try:
        yield
    finally:
        igraph.set_random_number_generator(random)


@dataclass
class CommunitySize:
    """Una riga di ``metric_community_sizes``."""

    layer: str
    bucket: str
    community_count: Optional[int] = None
    member_count: Optional[int] = None
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None

    KEY_FIELDS = ("layer", "bucket")


@dataclass
class CommunityResult:
    """Una riga di ``metric_communities``, prima della soppressione."""

    layer: str
    n_effective: Optional[int] = None
    community_count: Optional[int] = None
    modularity: Optional[float] = None
    modularity_random_mean: Optional[float] = None
    modularity_random_sd: Optional[float] = None
    modularity_z: Optional[float] = None
    previous_snapshot_id: Optional[int] = None
    node_overlap: Optional[float] = None
    stability_jaccard: Optional[float] = None
    # Giorni tra as_of e l'as_of del precedente: qualifica stability_jaccard,
    # quindi e' colonna tipizzata e non chiave di details — details e'
    # dichiarata diagnostica e non contratto (api/models.py), e questo e'
    # l'unico dei quattro dati del confronto che dice se gli altri tre valgono
    # qualcosa (modello-metriche.md 4.7).
    previous_gap_days: Optional[float] = None
    communities_born: Optional[int] = None
    communities_dissolved: Optional[int] = None
    communities_merged: Optional[int] = None
    communities_split: Optional[int] = None
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None
    is_significant: Optional[bool] = None
    details: dict[str, Any] = field(default_factory=dict)

    KEY_FIELDS = ("layer",)


def partition_of(graph: igraph.Graph, *, params: MetricParams) -> dict[int, int]:
    """Partizione Leiden come mappa ``author_id -> indice di community``.

    Dato per-nodo: e' un passaggio interno, e nessun chiamante fuori da questo
    modulo deve persisterlo o farlo uscire verso l'API (modello-metriche.md 8).

    ``ModularityVertexPartition`` e non CPM: la grandezza che il catalogo chiede
    di riportare e' la modularita' della partizione, e usarla anche come
    funzione obiettivo rende il numero riportato coerente con cio' che e' stato
    effettivamente ottimizzato. CPM richiederebbe un parametro di risoluzione da
    tarare sulla scala dei pesi, che dipende da H — cioe' tarare un parametro
    sopra un parametro provvisorio.
    """
    if graph.vcount() == 0:
        return {}
    return _membership_by_author(
        graph, _find_partition(graph, params=params, seed=params.seed)
    )


def _membership_by_author(
    graph: igraph.Graph, partition: leidenalg.VertexPartition
) -> dict[int, int]:
    return {
        graph.vs[vertex]["author_id"]: community
        for vertex, community in enumerate(partition.membership)
    }


def _find_partition(
    graph: igraph.Graph, *, params: MetricParams, seed: int
) -> leidenalg.VertexPartition:
    weights = graph.es["weight"] if graph.ecount() else None
    return leidenalg.find_partition(
        graph,
        leidenalg.ModularityVertexPartition,
        weights=weights,
        seed=seed,
        n_iterations=params.leiden_iterations,
    )


def _modularity_baseline(
    graph: igraph.Graph, *, params: MetricParams, rng: random.Random
) -> tuple[Optional[float], Optional[float], int, bool]:
    """Modularita' attesa su grafi casuali con la stessa sequenza di gradi.

    Il rewiring preserva i gradi ma non i pesi, quindi il multiset dei pesi
    viene riassegnato a caso agli archi: e' un'approssimazione, dichiarata nella
    specifica e non nascosta qui.
    """
    repetitions, degraded = params.baseline_repetitions_for(graph.vcount())
    if graph.ecount() < 2:
        # Con meno di due archi il rewiring non ha nulla da scambiare.
        return None, None, repetitions, degraded

    weights = list(graph.es["weight"])
    values: list[float] = []
    with _igraph_rng(rng):
        for _ in range(repetitions):
            shuffled = graph.copy()
            shuffled.rewire(n=10 * graph.ecount(), mode="simple")
            rng.shuffle(weights)
            shuffled.es["weight"] = list(weights)
            values.append(
                _find_partition(shuffled, params=params, seed=params.seed).modularity
            )

    mean = sum(values) / len(values)
    if len(values) > 1:
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        sd = variance**0.5
    else:
        sd = 0.0
    return mean, sd, repetitions, degraded


def size_buckets(
    membership: dict[int, int], *, layer: str, params: MetricParams
) -> list[CommunitySize]:
    """Distribuzione delle dimensioni, gia' accorpata sotto soglia.

    Una community sotto N non viene mai riportata come voce a se': finisce nel
    bucket ``small`` insieme a tutte le altre. La soppressione vera e propria
    (primaria e secondaria) la applica ``suppression.py``: qui si costruisce
    solo la distribuzione.
    """
    sizes: dict[int, int] = {}
    for community in membership.values():
        sizes[community] = sizes.get(community, 0) + 1

    rows: list[CommunitySize] = []
    for label, low, high in params.community_size_buckets():
        members = [
            size for size in sizes.values() if size >= low and (high is None or size < high)
        ]
        if not members:
            continue
        rows.append(
            CommunitySize(
                layer=layer,
                bucket=label,
                community_count=len(members),
                member_count=sum(members),
            )
        )
    return rows


@dataclass(frozen=True)
class PartitionComparison:
    node_overlap: float
    stability_jaccard: Optional[float]
    born: int
    dissolved: int
    merged: int
    split: int


def compare_partitions(
    previous: dict[int, int], current: dict[int, int], *, params: MetricParams
) -> PartitionComparison:
    """Confronto tra due partizioni, con le community accoppiate esplicitamente.

    **Due popolazioni diverse, per due domande diverse.**

    - Il **Jaccard e la stabilita'** si calcolano sul *nucleo comune*, cioe' sui
      soli nodi presenti in entrambi i grafi. Calcolarli sull'unione
      mescolerebbe la ricomposizione delle community con il ricambio dei membri,
      e il ricambio si riporta a parte come ``node_overlap``.
    - I **quattro conteggi** (nate, dissolte, fuse, scisse) si calcolano invece
      sulle partizioni *intere*. Una community composta interamente da nodi
      assenti la settimana prima e' una community nata — anzi, su una community
      in crescita e' proprio il fenomeno che si vuole vedere — e restringerla al
      nucleo comune la renderebbe invisibile. La restrizione al nucleo esiste per
      proteggere una misura di *somiglianza* dal ricambio; un conteggio di gruppi
      non e' una misura di somiglianza, e tenerlo sulla stessa popolazione degli
      altri tre e' cio' che li rende confrontabili tra loro.

    Limite da conoscere: ``dissolved`` conta insieme "queste persone ci sono
    ancora ma non stanno piu' insieme" e "queste persone non sono piu' nel
    grafo" — due cause diverse con lo stesso effetto sul conteggio.
    ``node_overlap`` e' il numero che le separa, e va letto accanto.
    """
    previous_nodes = set(previous)
    current_nodes = set(current)
    union = previous_nodes | current_nodes
    core = previous_nodes & current_nodes
    node_overlap = (len(core) / len(union)) if union else 0.0

    # Partizioni intere: la popolazione dei conteggi.
    prev_all = _groups(previous, previous_nodes)
    curr_all = _groups(current, current_nodes)
    # Ristrette al nucleo comune: la popolazione del Jaccard.
    prev_groups = _groups(previous, core)
    curr_groups = _groups(current, core)

    if not prev_groups or not curr_groups:
        return PartitionComparison(
            node_overlap=node_overlap,
            stability_jaccard=None,
            born=len(curr_all),
            dissolved=len(prev_all),
            merged=0,
            split=0,
        )

    # Accoppiamento greedy sul Jaccard decrescente. Un accoppiamento bipartito
    # ottimo (Hungarian) sarebbe piu' corretto ma richiederebbe scipy, che non
    # e' tra le dipendenze e che non si giustifica su una droplet da 1 GB per
    # questo solo uso.
    candidates = []
    for prev_id, prev_members in prev_groups.items():
        for curr_id, curr_members in curr_groups.items():
            score = _jaccard(prev_members, curr_members)
            if score >= params.jaccard_match_min:
                candidates.append((score, prev_id, curr_id))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    matched_prev: dict[int, float] = {}
    matched_curr: set[int] = set()
    for score, prev_id, curr_id in candidates:
        if prev_id in matched_prev or curr_id in matched_curr:
            continue
        matched_prev[prev_id] = score
        matched_curr.add(curr_id)

    # Media dei Jaccard PESATA per la dimensione della community precedente:
    # senza il peso, una community di 3 persone conterebbe quanto una da 200 e
    # il numero racconterebbe le fluttuazioni delle micro-community. Le
    # community precedenti non accoppiate entrano con J = 0, altrimenti la
    # stabilita' ignorerebbe proprio quelle sparite.
    total_weight = sum(len(members) for members in prev_groups.values())
    weighted = sum(
        matched_prev.get(prev_id, 0.0) * len(members)
        for prev_id, members in prev_groups.items()
    )
    stability = (weighted / total_weight) if total_weight else None

    merged = 0
    split = 0
    dissolved = 0
    for prev_id, prev_members in prev_all.items():
        if prev_id in matched_prev:
            continue
        # Dove sono finiti i membri SOPRAVVISSUTI: chi non e' piu' nel grafo non
        # e' finito da nessuna parte, e una community senza sopravvissuti non
        # puo' essersi ne' fusa ne' scissa.
        survivors = prev_members & core
        if not survivors:
            dissolved += 1
            continue
        shares = [
            len(survivors & curr_members) / len(survivors)
            for curr_members in curr_all.values()
            if survivors & curr_members
        ]
        if any(share >= params.merge_min_share for share in shares):
            # Non e' sparita: e' stata assorbita da una sola community nuova.
            merged += 1
        elif len(shares) >= 2:
            split += 1
        else:
            dissolved += 1

    born = len(curr_all) - len(matched_curr)

    return PartitionComparison(
        node_overlap=node_overlap,
        stability_jaccard=stability,
        born=born,
        dissolved=dissolved,
        merged=merged,
        split=split,
    )


def _groups(membership: dict[int, int], core: set[int]) -> dict[int, set[int]]:
    groups: dict[int, set[int]] = {}
    for author_id, community in membership.items():
        if author_id not in core:
            continue
        groups.setdefault(community, set()).add(author_id)
    return groups


def _jaccard(first: set[int], second: set[int]) -> float:
    union = first | second
    return (len(first & second) / len(union)) if union else 0.0


def compute_communities(
    graph: igraph.Graph,
    *,
    layer: str,
    params: MetricParams,
    rng: random.Random,
    as_of: Optional[datetime] = None,
    previous: Optional[dict[int, int]] = None,
    previous_snapshot_id: Optional[int] = None,
    previous_as_of: Optional[datetime] = None,
    unavailable_reason: Optional[str] = None,
) -> Optional[tuple[CommunityResult, list[CommunitySize]]]:
    """Partizione, modularita' con baseline, e stabilita' se confrontabile.

    ``as_of`` e ``previous_as_of`` sono opzionali solo perche' le varianti che
    non confrontano niente (la verifica di sensibilita' senza archi ricostruiti)
    non hanno un precedente da dichiarare. Con ``previous`` servono entrambi, e
    la mancanza e' un errore invece che un dato che manca in silenzio: vedi
    sotto.
    """
    n = graph.vcount()
    if n == 0:
        return None

    if previous is not None and (as_of is None or previous_as_of is None):
        # Non un guard difensivo: e' la regola di 4.7. La stabilita' non deve
        # poter essere scritta senza la distanza a cui e' stata calcolata, e
        # una colonna che resta silenziosamente NULL invece di sollevare e'
        # precisamente il modo in cui l'informazione sparirebbe senza che
        # nessuno se ne accorga.
        raise ValueError(
            "compute_communities: con una partizione precedente servono as_of e "
            "previous_as_of"
        )

    partition = _find_partition(graph, params=params, seed=params.seed)
    membership = _membership_by_author(graph, partition)
    modularity = partition.modularity
    community_count = len(set(membership.values()))

    random_mean, random_sd, repetitions, degraded = _modularity_baseline(
        graph, params=params, rng=rng
    )
    modularity_z: Optional[float] = None
    if random_mean is not None and random_sd:
        modularity_z = (modularity - random_mean) / random_sd

    reasons: list[str] = []
    if n < params.min_nodes_structural:
        reasons.append("too_few_nodes")
    if modularity_z is None:
        reasons.append("degenerate_baseline")
    elif modularity_z < params.min_modularity_z:
        reasons.append("modularity_indistinguishable_from_random")

    details: dict[str, Any] = {
        "baseline_repetitions_used": repetitions,
        "baseline_degraded": degraded,
        "leiden_objective": params.leiden_objective,
        "seed": params.seed,
    }

    result = CommunityResult(
        layer=layer,
        n_effective=n,
        community_count=community_count,
        modularity=modularity,
        modularity_random_mean=random_mean,
        modularity_random_sd=random_sd,
        modularity_z=modularity_z,
    )

    if previous is None:
        # La stabilita' semplicemente non e' calcolabile: uno snapshot
        # precedente "vicino ma non identico" nei parametri non viene adattato
        # ne' riscalato, perche' la differenza tra le due partizioni
        # conterrebbe il cambio di parametri e attribuirla alla community
        # sarebbe falso.
        details["stability_unavailable"] = unavailable_reason or "no_previous_snapshot"
    else:
        # Sempre, e senza soglia: la cadenza con cui la stabilita' e' stata
        # calcolata viaggia insieme al valore. fetch_previous_snapshot prende lo
        # snapshot immediatamente precedente per as_of e non chiede nessuna
        # distanza minima, quindi due snapshot a un giorno l'uno dall'altro
        # confrontano finestre da 7 giorni sovrapposte all'85-95% e il Jaccard
        # che ne esce misura in gran parte quella sovrapposizione — uscendo
        # comunque con is_significant a true.
        #
        # Nessun reason, nessuna soglia, is_significant non si tocca: e' lo
        # stesso argomento del docstring di series_spacing, e vale identico qui.
        # Una soglia inventata su poche settimane di dati sarebbe un parametro
        # senza base messo davanti a un numero che si legge benissimo da solo.
        result.previous_gap_days = round(
            (as_of - previous_as_of).total_seconds() / 86400.0, 3
        )
        comparison = compare_partitions(previous, membership, params=params)
        result.previous_snapshot_id = previous_snapshot_id
        result.node_overlap = comparison.node_overlap
        result.communities_born = comparison.born
        result.communities_dissolved = comparison.dissolved
        result.communities_merged = comparison.merged
        result.communities_split = comparison.split
        if comparison.node_overlap < params.min_node_overlap:
            details["stability_unavailable"] = "node_overlap_below_minimum"
            reasons.append("node_overlap_below_minimum")
        else:
            result.stability_jaccard = comparison.stability_jaccard

    if reasons:
        details["not_significant_because"] = reasons
    result.is_significant = not reasons
    result.details = details

    return result, size_buckets(membership, layer=layer, params=params)


def sensitivity_details(
    baseline: Optional[CommunityResult], *, identical: bool = False
) -> dict[str, Any]:
    """Verifica di sensibilita' senza gli archi ricostruiti (2.4).

    ``identical`` quando nessun arco del layer e' ricostruito: la variante
    coincide con il calcolo principale e non viene rieseguita. Va comunque
    registrato — "nessuna differenza" e "non calcolata" sono due cose diverse a
    valle, e la seconda non autorizza a concludere niente.
    """
    if identical:
        return {"without_reconciled": {"identical": True}}
    if baseline is None:
        return {"without_reconciled": None}
    return {
        "without_reconciled": {
            "identical": False,
            "n_effective": baseline.n_effective,
            "community_count": baseline.community_count,
            "modularity": baseline.modularity,
        }
    }
