"""Una sola definizione di "arco ammesso", usata da tutti i percorsi.

Il grafo delle metriche strutturali e il conteggio dei partner delle coorti
devono ammettere le stesse coppie. Finche' ``min_edge_weight`` vale 0.0 la cosa
non si vede: due regole diverse danno lo stesso risultato, e un test a soglia
zero non proverebbe niente. Questi test girano con la soglia alzata, che e' lo
scenario che modello-metriche.md 2.3 prevede esplicitamente.

Confrontabilita' degli snapshot: stessa idea, altro punto. Due snapshot con
finestre di ampiezza diversa sono grafi di densita' diversa, e la stabilita'
calcolata tra loro misura il cambio di finestra chiamandolo ricomposizione
della community.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

from job.admission import admitted_pairs
from job.cohorts import CohortMember, PartnerEdge, PartnerTracker
from job.config import LAYER_REPLY, LAYER_VOICE, SCOPE_ANY, MetricParams
from job.edges import Edge
from job.graph import build_metric_graph
from job.metrics import SnapshotIdentity, snapshot_comparability

T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)

# Soglia alzata: con 0.0 i due percorsi coinciderebbero comunque e il test non
# distinguerebbe la regola unificata da due regole per caso d'accordo.
#
# k alto di proposito: il tracker butta l'insieme dei partner appena il membro
# raggiunge k (l'evento e' gia' avvenuto, l'insieme non serve piu'), e qui
# l'insieme e' proprio quello che si vuole ispezionare.
STRICT = MetricParams(min_edge_weight=0.5, k_connections=99)


def edge(src, dst, *, weight, layer=LAYER_VOICE, interactions=1, reconciled=False):
    return Edge(
        layer=layer,
        src_author_id=src,
        dst_author_id=dst,
        weight=weight,
        weight_undecayed=weight,
        raw_units=weight,
        interaction_count=interactions,
        last_interaction_at=T0,
        is_reconciled=reconciled,
    )


def _partner_edges(edges):
    return [
        PartnerEdge(
            layer=e.layer,
            src_author_id=e.src_author_id,
            dst_author_id=e.dst_author_id,
            weight=e.weight,
            interaction_count=e.interaction_count,
        )
        for e in edges
    ]


def _partners_seen(edges, *, params, member_id):
    tracker = PartnerTracker(
        [CohortMember(author_id=member_id, joined_at=T0)], params=params
    )
    tracker.observe(T0 + timedelta(days=1), _partner_edges(edges))
    return tracker.partners_of(member_id, scope=SCOPE_ANY)


def test_orientamenti_sotto_soglia_che_sommano_sopra_sono_ammessi_da_entrambi():
    # Due reply, una per verso, ciascuna sotto la soglia; la somma la supera.
    # La proiezione non diretta somma i due orientamenti PRIMA di confrontare,
    # quindi la coppia e' ammessa — e deve esserlo per entrambi i percorsi.
    edges = [
        edge(1, 2, weight=0.3, layer=LAYER_REPLY),
        edge(2, 1, weight=0.3, layer=LAYER_REPLY),
    ]

    graph = build_metric_graph(edges, layer=LAYER_REPLY, params=STRICT)
    assert graph.ecount() == 1, "il grafo strutturale ammette la coppia"

    assert _partners_seen(edges, params=STRICT, member_id=1) == {2}


def test_i_due_percorsi_ammettono_lo_stesso_insieme_di_coppie():
    # Un campione che tocca tutti i casi: sotto soglia da entrambi i lati,
    # sopra soglia solo sommando, sopra soglia da solo.
    edges = [
        edge(1, 2, weight=0.2, layer=LAYER_REPLY),   # 1-2: 0.2 -> escluso
        edge(1, 3, weight=0.3, layer=LAYER_REPLY),   # 1-3: 0.3+0.3 = 0.6 -> ammesso
        edge(3, 1, weight=0.3, layer=LAYER_REPLY),
        edge(1, 4, weight=0.9, layer=LAYER_REPLY),   # 1-4: 0.9 -> ammesso
        edge(1, 5, weight=0.5, layer=LAYER_REPLY),   # 1-5: 0.5 -> escluso (soglia stretta)
    ]

    graph = build_metric_graph(edges, layer=LAYER_REPLY, params=STRICT)
    dal_grafo = {
        frozenset((graph.vs[a]["author_id"], graph.vs[b]["author_id"]))
        for a, b in graph.get_edgelist()
    }
    dalle_coorti = {
        frozenset((1, partner))
        for partner in _partners_seen(edges, params=STRICT, member_id=1)
    }

    assert dal_grafo == dalle_coorti == {frozenset((1, 3)), frozenset((1, 4))}


def test_partner_min_interactions_si_applica_alla_somma_degli_orientamenti():
    # Una reply per verso: due interazioni sulla stessa relazione. Con la soglia
    # a 2 la coppia passa, perche' e' la RELAZIONE ad avere due interazioni.
    edges = [
        edge(1, 2, weight=0.6, layer=LAYER_REPLY),
        edge(2, 1, weight=0.6, layer=LAYER_REPLY),
    ]
    params = dataclasses.replace(STRICT, partner_min_interactions=2)

    assert _partners_seen(edges, params=params, member_id=1) == {2}

    # Con una sola direzione, l'unica interazione non basta.
    solo_andata = [edge(1, 2, weight=0.6, layer=LAYER_REPLY)]
    assert _partners_seen(solo_andata, params=params, member_id=1) == set()


def test_soglia_su_un_layer_non_si_somma_tra_layer():
    # 'any' e' un'unione di insiemi di persone, non una somma di pesi: un
    # partner qualifica se qualifica in ALMENO UN layer, e i pesi di layer
    # diversi non si sommano mai per superare la soglia insieme.
    edges = [
        edge(1, 2, weight=0.3, layer=LAYER_REPLY),
        edge(1, 2, weight=0.3, layer=LAYER_VOICE),
    ]

    assert _partners_seen(edges, params=STRICT, member_id=1) == set()
    assert admitted_pairs(edges, params=STRICT) == {}


# --- confrontabilita' degli snapshot ---------------------------------------


def identity(*, params=None, window_days=7.0):
    return SnapshotIdentity(
        params=params if params is not None else {"decay_half_life_days": 7.0},
        window=timedelta(days=window_days),
    )


def test_finestre_di_ampiezza_diversa_non_sono_confrontabili():
    # Il caso reale: il primo snapshot copre ~3 giorni, i successivi 7. La prima
    # stabilita' mai calcolata sarebbe tra due grafi di densita' diversa.
    assert (
        snapshot_comparability(identity(window_days=7.0), identity(window_days=3.0))
        == "window_differs"
    )
    assert snapshot_comparability(identity(), identity()) is None


def test_params_vuoti_non_sono_stessi_params_ma_non_so():
    # Uno snapshot scritto prima che i params esistessero ha '{}' per default, e
    # sono proprio quelli vecchi che finiranno per essere usati come precedente.
    vuoti = identity(params={})
    assert snapshot_comparability(identity(), vuoti) == "params_unknown"
    assert snapshot_comparability(vuoti, identity()) == "params_unknown"
    assert snapshot_comparability(vuoti, vuoti) == "params_unknown"


def test_params_diversi_restano_non_confrontabili():
    altro = identity(params={"decay_half_life_days": 14.0})
    assert snapshot_comparability(identity(), altro) == "params_differ"
