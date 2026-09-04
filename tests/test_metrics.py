"""Metriche aggregate: robustezza, community, coorti, soppressione.

Fixture sintetiche e nessun database, come per il resto del job. Il criterio e'
grafi piccoli costruiti a mano di cui si conosce la risposta giusta: una rete a
stella si frammenta togliendo il centro, due cricche collegate da un solo arco
danno due community, una coorte sotto soglia viene soppressa e non azzerata.

Con i dati reali di oggi (tre giorni, nove nodi) non e' possibile validare
niente, ed e' proprio per questo che i casi qui sono costruiti.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime, timedelta, timezone

import pytest

from job.cohorts import (
    CohortMember,
    Observation,
    SurvivalCurve,
    PartnerEdge,
    PartnerTracker,
    compute_cohort,
    compute_retention,
    series_spacing,
)
from job.communities import compare_partitions, compute_communities, partition_of
from job.config import LAYER_VOICE, SCOPE_ANY, MetricParams
from job.edges import Edge
from job.graph import build_metric_graph
from job.metrics import COHORT_DIAGNOSTIC_KEYS, build_metrics
from job.robustness import compute_robustness
from job.suppression import apply_secondary_suppression, suppress

T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)

# Soglie abbassate: i grafi di prova sono piccoli per costruzione, e con i
# default (30 nodi, 100 ripetizioni) ogni caso finirebbe indistinguibile dagli
# altri sotto lo stesso "non significativa".
SMALL = MetricParams(
    min_cardinality=3,
    min_nodes_structural=4,
    baseline_repetitions=25,
    min_modularity_z=1.0,
)


def edge(src: int, dst: int, *, weight: float = 1.0, layer: str = LAYER_VOICE) -> Edge:
    low, high = (src, dst) if src < dst else (dst, src)
    return Edge(
        layer=layer,
        src_author_id=low,
        dst_author_id=high,
        weight=weight,
        weight_undecayed=weight,
        raw_units=weight,
        interaction_count=1,
        last_interaction_at=T0,
    )


def star(points: int) -> list[Edge]:
    """Rete a stella: un centro (nodo 1) e ``points`` foglie."""
    return [edge(1, leaf) for leaf in range(2, points + 2)]


def two_cliques(size: int = 5) -> list[Edge]:
    """Due cricche collegate da un solo arco: il caso da manuale."""
    first = list(range(1, size + 1))
    second = list(range(100, 100 + size))
    edges = []
    for group in (first, second):
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                edges.append(edge(a, b, weight=10.0))
    # Il ponte e' debole apposta: e' l'unico arco che tiene insieme il grafo.
    edges.append(edge(first[-1], second[0], weight=0.5))
    return edges


# --- robustezza ------------------------------------------------------------


def test_stella_si_frammenta_togliendo_il_centro():
    graph = build_metric_graph(star(8), layer=LAYER_VOICE, params=SMALL)
    row = compute_robustness(
        graph,
        layer=LAYER_VOICE,
        removal_fraction=0.10,
        params=SMALL,
        rng=random.Random("test"),
    )

    # Il 10% di 9 nodi e' un nodo, e la betweenness lo trova: e' il centro.
    assert row.nodes_removed == 1
    assert row.giant_before == 1.0
    # Tolto il centro restano otto foglie isolate: nessuna componente maggiore
    # di un nodo, cioe' 1/9 del grafo originale.
    assert row.giant_after_targeted == pytest.approx(1 / 9)
    assert row.components_after_targeted == 8
    # Togliere una foglia a caso non fa quasi niente, quindi l'attacco mirato
    # e' molto peggio del caso: e' esattamente cio' che l'indice deve dire.
    assert row.targeted_excess > 0.5


def test_targeted_excess_negativo_ammesso_e_non_clampato():
    # Anello: tutti i nodi sono equivalenti, la betweenness e' pari ovunque e
    # il tiebreak sceglie l'author_id piu' basso. Su una struttura cosi'
    # regolare l'attacco mirato non e' migliore del caso, e su alcune
    # realizzazioni e' peggio: il valore negativo e' un risultato legittimo.
    ring = [edge(i, i + 1) for i in range(1, 12)] + [edge(1, 12)]
    graph = build_metric_graph(ring, layer=LAYER_VOICE, params=SMALL)
    row = compute_robustness(
        graph,
        layer=LAYER_VOICE,
        removal_fraction=0.20,
        params=SMALL,
        rng=random.Random("ring"),
    )

    assert row.targeted_excess < 0.0, "un anello non ha connettori privilegiati"
    # Nessun clamp a zero: il numero arriva in tabella com'e'.
    assert row.targeted_excess == pytest.approx(
        (row.giant_after_random_mean - row.giant_after_targeted) / row.giant_before
    )


def test_riga_strutturale_pubblicata_ma_non_significativa():
    # Il caso reale di oggi: nove nodi. Sopra la soglia di pubblicazione, sotto
    # il minimo di calcolabilita'. La riga esiste, i valori ci sono, e dice di
    # non essere affidabile — che e' un terzo stato, distinto sia da "soppressa"
    # sia da "buona".
    params = dataclasses.replace(SMALL, min_nodes_publish=5, min_nodes_structural=30)
    result = build_metrics(
        snapshot_id=1,
        guild_id=99,
        as_of=T0,
        params=params,
        edges=star(8),
    )

    rows = result.robustness
    assert rows, "nove nodi superano la soglia di pubblicazione"
    for row in rows:
        assert row.is_suppressed is False
        assert row.is_significant is False
        assert row.n_effective == 9
        assert row.giant_before is not None
        assert "too_few_nodes" in row.details["not_significant_because"]


def test_riga_soppressa_azzera_anche_le_colonne_da_cui_si_ricava_la_numerosita():
    # Tre nodi, soglia di pubblicazione a cinque.
    params = dataclasses.replace(SMALL, min_cardinality=5, min_nodes_publish=5)
    result = build_metrics(
        snapshot_id=1, guild_id=99, as_of=T0, params=params, edges=star(2)
    )

    row = result.robustness[0]
    assert row.is_suppressed is True
    assert row.suppression_reason == "below_threshold"
    # Non solo cio' che sembra "il valore": nodes_removed e' ceil(X*n) con X in
    # chiave, e giant_before e' una frazione con n al denominatore.
    assert row.n_effective is None
    assert row.nodes_removed is None
    assert row.giant_before is None
    assert row.targeted_excess is None
    # NULL e non false: su una riga soppressa la metrica non e' stata valutata,
    # e "valutata e non significativa" e' un'altra cosa.
    assert row.is_significant is None
    assert row.details == {}
    # La chiave sopravvive: e' cio' che rende la riga una riga.
    assert row.layer == LAYER_VOICE
    assert row.removal_fraction is not None


# --- community -------------------------------------------------------------


def test_due_cricche_unite_da_un_solo_arco_danno_due_community():
    graph = build_metric_graph(two_cliques(), layer=LAYER_VOICE, params=SMALL)
    row, sizes = compute_communities(
        graph, layer=LAYER_VOICE, params=SMALL, rng=random.Random("cliques")
    )

    assert row.community_count == 2
    assert row.modularity > 0.3
    assert sum(size.member_count for size in sizes) == 10


def test_leiden_rieseguito_da_la_stessa_partizione():
    # Senza seed fissato due esecuzioni possono differire, e la "stabilita' tra
    # snapshot" misurerebbe il rumore dell'algoritmo. E' anche cio' che rende
    # esatta la ricostruzione della partizione precedente, che non viene mai
    # salvata.
    edges = two_cliques(6)
    first = partition_of(
        build_metric_graph(edges, layer=LAYER_VOICE, params=SMALL), params=SMALL
    )
    second = partition_of(
        build_metric_graph(edges, layer=LAYER_VOICE, params=SMALL), params=SMALL
    )
    assert first == second


def test_baseline_della_modularita_e_deterministico_nello_stesso_processo():
    # Il rewiring del baseline passa dal generatore di igraph, che e' uno stato
    # GLOBALE di processo e non il random.Random che gli passiamo. Senza legarlo,
    # modularity_random_mean e modularity_z cambiano tra due run sullo stesso
    # snapshot, e con loro is_significant — cioe' esattamente il rumore che il
    # seed esiste per eliminare.
    graph = build_metric_graph(two_cliques(7), layer=LAYER_VOICE, params=SMALL)

    first, _ = compute_communities(
        graph, layer=LAYER_VOICE, params=SMALL, rng=random.Random("stesso-seme")
    )
    second, _ = compute_communities(
        graph, layer=LAYER_VOICE, params=SMALL, rng=random.Random("stesso-seme")
    )

    assert first.modularity_random_mean == second.modularity_random_mean
    assert first.modularity_random_sd == second.modularity_random_sd
    assert first.modularity_z == second.modularity_z
    assert first.is_significant == second.is_significant


def test_il_baseline_non_lascia_sporco_il_generatore_globale():
    # Lo stato e' globale di processo: va ripristinato, non impostato e
    # dimenticato, altrimenti il calcolo delle metriche cambierebbe il
    # comportamento di qualunque altro uso di igraph nello stesso processo.
    graph = build_metric_graph(two_cliques(6), layer=LAYER_VOICE, params=SMALL)
    before = random.getstate()

    compute_communities(
        graph, layer=LAYER_VOICE, params=SMALL, rng=random.Random("isolato")
    )

    # Il generatore globale non e' stato consumato dal baseline.
    assert random.getstate() == before


def test_community_accoppiate_per_sovrapposizione_non_per_indice():
    # Stessi due gruppi, indici scambiati tra i due snapshot: un Jaccard fatto
    # sugli indici direbbe "tutto cambiato", che e' falso.
    previous = {1: 0, 2: 0, 3: 0, 10: 1, 11: 1, 12: 1}
    current = {1: 1, 2: 1, 3: 1, 10: 0, 11: 0, 12: 0}

    comparison = compare_partitions(previous, current, params=SMALL)

    assert comparison.stability_jaccard == pytest.approx(1.0)
    assert comparison.born == 0
    assert comparison.dissolved == 0


def test_community_assorbita_conta_come_fusione_non_come_dissoluzione():
    previous = {1: 0, 2: 0, 3: 0, 10: 1, 11: 1}
    current = {1: 0, 2: 0, 3: 0, 10: 0, 11: 0}

    comparison = compare_partitions(previous, current, params=SMALL)

    assert comparison.merged == 1
    assert comparison.dissolved == 0


def test_community_di_soli_nuovi_arrivati_conta_come_nata():
    # I conteggi si calcolano sulle partizioni intere, non sul nucleo comune:
    # una community fatta interamente di nodi assenti la settimana prima e' una
    # community nata, ed e' proprio il fenomeno che si vuole vedere su una
    # community in crescita.
    previous = {1: 0, 2: 0, 3: 0}
    current = {1: 0, 2: 0, 3: 0, 50: 1, 51: 1, 52: 1}

    comparison = compare_partitions(previous, current, params=SMALL)

    assert comparison.born == 1
    # Il gruppo storico e' rimasto sé stesso: la stabilita' resta alta, perche'
    # si calcola sul nucleo comune e non e' diluita dai nuovi arrivati.
    assert comparison.stability_jaccard == pytest.approx(1.0)
    assert comparison.dissolved == 0


def test_community_uscita_per_intero_conta_come_dissolta():
    # Il caso simmetrico, e il limite dichiarato: dissolved non distingue "non
    # stanno piu' insieme" da "non sono piu' nel grafo". node_overlap e' il
    # numero che le separa.
    previous = {1: 0, 2: 0, 3: 0, 90: 1, 91: 1, 92: 1}
    current = {1: 0, 2: 0, 3: 0}

    comparison = compare_partitions(previous, current, params=SMALL)

    assert comparison.dissolved == 1
    assert comparison.born == 0
    assert comparison.node_overlap == pytest.approx(0.5)


def test_cadenza_della_serie_rende_visibile_una_settimana_mancante():
    # Cadenza settimanale con un buco: la settimana del 14 non c'e'. Non serve
    # un flag ne' una soglia — il massimo e' il doppio della mediana e si legge.
    settimanale = [T0 + timedelta(days=7 * i) for i in range(5)]
    assert series_spacing(settimanale) == {
        "cadence_days_median": 7.0,
        "max_gap_days": 7.0,
    }

    con_buco = [m for m in settimanale if m != T0 + timedelta(days=14)]
    misura = series_spacing(con_buco)
    assert misura["cadence_days_median"] == 7.0
    assert misura["max_gap_days"] == 14.0


def test_spaziatura_non_definita_sotto_due_snapshot():
    # Una spaziatura tra meno di due istanti non esiste, e non e' zero.
    assert series_spacing([]) is None
    assert series_spacing([T0]) is None


def test_diagnostica_delle_coorti_sempre_presente_anche_quando_nulla():
    # E' cosi' che snapshot_gaps e' rimasto specificato e mai calcolato: la
    # chiave spariva invece di comparire a None. Una chiave assente e una che
    # vale zero devono restare distinguibili.
    as_of = T0 + timedelta(days=60)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    result = build_metrics(
        snapshot_id=1,
        guild_id=99,
        as_of=as_of,
        params=dataclasses.replace(SMALL, min_cardinality=5),
        edges=[],
        members=members,
        cohort_stats={"snapshots_used": 0},
    )

    row = next(r for r in result.cohorts if r.layer_scope == SCOPE_ANY)
    for key in COHORT_DIAGNOSTIC_KEYS:
        assert key in row.details, key
    assert row.details["snapshots_used"] == 0
    # Non prodotte da questa run: presenti ed esplicitamente nulle.
    assert row.details["snapshot_gaps"] is None
    assert row.details["snapshots_skipped_params"] is None


# --- soppressione ----------------------------------------------------------


def test_soppressione_secondaria_quando_una_sola_cella_e_soppressa():
    from job.communities import CommunitySize

    rows = [
        CommunitySize(layer=LAYER_VOICE, bucket="small", community_count=1, member_count=2),
        CommunitySize(layer=LAYER_VOICE, bucket="3-9", community_count=2, member_count=12),
        CommunitySize(layer=LAYER_VOICE, bucket="10-19", community_count=1, member_count=15),
    ]

    apply_secondary_suppression(rows, threshold=5)

    # La prima e' sotto soglia; da sola si ricaverebbe per differenza dal
    # totale pubblicato, quindi ne cade anche una seconda.
    assert [row.is_suppressed for row in rows] == [True, True, False]
    assert rows[0].suppression_reason == "below_threshold"
    assert rows[1].suppression_reason == "secondary"
    assert rows[1].member_count is None


def test_suppress_non_azzera_la_chiave():
    from job.communities import CommunitySize

    row = suppress(
        CommunitySize(layer=LAYER_VOICE, bucket="small", community_count=1, member_count=2)
    )
    assert (row.layer, row.bucket) == (LAYER_VOICE, "small")
    assert row.community_count is None and row.member_count is None


def test_variante_senza_ricostruiti_saltata_ma_dichiarata_quando_coincide():
    # Nessun arco ricostruito: la variante coincide con il calcolo principale e
    # non va rieseguita — sarebbe il 100% di CPU in piu' sulla voce dominante,
    # per un risultato identico. Ma il fatto che coincida va detto: "nessuna
    # differenza" e "non calcolata" sono due cose diverse a valle.
    result = build_metrics(
        snapshot_id=1, guild_id=99, as_of=T0, params=SMALL, edges=two_cliques()
    )

    for row in (*result.robustness, *result.communities):
        assert row.details["without_reconciled"] == {"identical": True}


def test_variante_senza_ricostruiti_calcolata_quando_ci_sono_archi_ricostruiti():
    edges = two_cliques()
    # Il ponte tra le due cricche viene da un intervallo ricostruito: senza,
    # il grafo si spezza in due, ed e' esattamente la conclusione che dipende
    # da un dato inventato dalla riconciliazione.
    edges[-1].is_reconciled = True

    result = build_metrics(
        snapshot_id=1, guild_id=99, as_of=T0, params=SMALL, edges=edges
    )

    for row in result.robustness:
        sensitivity = row.details["without_reconciled"]
        assert sensitivity["identical"] is False
        assert sensitivity["targeted_excess"] is not None
    for row in result.communities:
        assert row.details["without_reconciled"]["identical"] is False


# --- ammissione degli archi ------------------------------------------------


def test_min_edge_weight_separa_due_componenti_tenute_insieme_da_un_arco_spento():
    # Un arco a 1e-5 tiene insieme due componenti esattamente come uno di peso
    # 1: le componenti connesse i pesi non li guardano.
    edges = [edge(1, 2), edge(2, 3), edge(10, 11), edge(11, 12), edge(3, 10, weight=1e-5)]

    connected = build_metric_graph(edges, layer=LAYER_VOICE, params=SMALL)
    assert len(connected.components()) == 1

    strict = dataclasses.replace(SMALL, min_edge_weight=1e-3)
    fragmented = build_metric_graph(edges, layer=LAYER_VOICE, params=strict)
    assert len(fragmented.components()) == 2
    assert fragmented.vcount() == 6, "nessun nodo perso: solo l'arco cade"


# --- coorti ----------------------------------------------------------------


def member(author_id: int, *, joined_days: float, left_days: float = None, prior=False):
    return CohortMember(
        author_id=author_id,
        joined_at=T0 + timedelta(days=joined_days),
        left_at=None if left_days is None else T0 + timedelta(days=left_days),
        has_prior_activity=prior,
    )


def test_coorte_sotto_soglia_soppressa_e_non_azzerata():
    as_of = T0 + timedelta(days=60)
    members = [member(i, joined_days=0) for i in (1, 2)]
    result = build_metrics(
        snapshot_id=1,
        guild_id=99,
        as_of=as_of,
        params=dataclasses.replace(SMALL, min_cardinality=5),
        edges=[],
        members=members,
    )

    for row in result.cohorts:
        assert row.is_suppressed is True
        assert row.n_effective is None
        assert row.event_count is None
        assert row.censored_count is None
        # NON zero: uno zero e' un valore legittimo e diverso da "non
        # mostrabile", ed e' proprio la confusione che la regola evita.
        assert row.n_effective != 0
        assert row.is_mature is None


def test_coorte_a_zero_eventi_e_distinguibile_da_una_soppressa():
    as_of = T0 + timedelta(days=60)
    members = [member(i, joined_days=0) for i in range(1, 7)]
    result = build_metrics(
        snapshot_id=1,
        guild_id=99,
        as_of=as_of,
        params=dataclasses.replace(SMALL, min_cardinality=5),
        edges=[],
        members=members,
        # Nessuno ha raggiunto k: legittimamente zero eventi.
        reached_at={SCOPE_ANY: {}, LAYER_VOICE: {}},
    )

    row = next(r for r in result.cohorts if r.layer_scope == SCOPE_ANY)
    assert row.is_suppressed is False
    assert row.event_count == 0
    assert row.n_effective == 6
    assert row.censored_count == 6
    # Zero eventi non e' NULL, e la mediana non raggiunta non e' zero.
    assert row.median_days_to_k is None
    assert row.median_reached is False


def test_censura_a_destra_non_conta_come_non_integrato():
    # Coorte entrata due giorni fa: nessuno ha ancora avuto il tempo di
    # raggiungere k. La curva non deve dichiarare una mediana.
    as_of = T0 + timedelta(days=2)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    row = compute_cohort(
        cohort_start=T0.date(),
        layer_scope=SCOPE_ANY,
        members=members,
        reached_at={},
        excluded_rejoins=0,
        as_of=as_of,
        params=SMALL,
    )

    assert row.observation_days == 2
    assert row.is_mature is False
    assert row.is_significant is False
    assert row.median_days_to_k is None
    assert row.censored_count == 6


def test_reached_by_non_estrapola_oltre_losservazione():
    # Coorte osservata tre giorni, con un evento al giorno 2. reached_by_28d
    # ricavato da due giorni di dati sarebbe un numero inventato — e sono
    # proprio reached_by_14d/28d la forma azionabile della metrica, cioe' quella
    # che l'admin legge davvero.
    as_of = T0 + timedelta(days=3)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    row = compute_cohort(
        cohort_start=T0.date(),
        layer_scope=SCOPE_ANY,
        members=members,
        reached_at={1: T0 + timedelta(days=2)},
        excluded_rejoins=0,
        as_of=as_of,
        params=SMALL,
    )

    assert row.event_count == 1, "l'evento c'e' stato"
    assert row.reached_by_14d is None, "14 giorni oltre i 3 osservati"
    assert row.reached_by_28d is None


def test_reached_by_e_esatto_quando_tutti_hanno_avuto_levento():
    # Coorte osservata 30 giorni, tutti arrivati a k entro il giorno 3, nessun
    # censurato. La curva e' a zero da li' in poi PER COSTRUZIONE, quindi
    # 1 - S(28) = 1.0 e' esatto e non e' un'estrapolazione. E' anche il caso che
    # l'admin piu' vorrebbe vedere: farlo sparire lo renderebbe indistinguibile
    # dalla coorte giovane su cui il numero davvero non si puo' dire.
    as_of = T0 + timedelta(days=30)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    row = compute_cohort(
        cohort_start=T0.date(),
        layer_scope=SCOPE_ANY,
        members=members,
        reached_at={i: T0 + timedelta(days=3) for i in range(1, 7)},
        excluded_rejoins=0,
        as_of=as_of,
        params=SMALL,
    )

    assert row.censored_count == 0
    assert row.reached_by_14d == pytest.approx(1.0)
    assert row.reached_by_28d == pytest.approx(1.0)


def test_reached_by_resta_nullo_se_qualcuno_e_ancora_censurato():
    # Un evento al giorno 2, gli altri censurati al giorno 3: la curva NON
    # arriva a zero, restano persone di cui non sappiamo, e a 28 giorni non c'e'
    # niente da dire. E' il confine esatto con il test sopra.
    curve = SurvivalCurve(
        [
            Observation(days=2.0, event=True),
            *[Observation(days=3.0, event=False) for _ in range(5)],
        ]
    )

    assert curve.reached_by(2.0) == pytest.approx(1 / 6)
    assert curve.reached_by(28.0) is None


def test_reached_by_entro_losservazione_e_calcolato():
    # Il contrappeso: dentro l'osservazione il numero c'e' e non e' NULL, cosi'
    # la regola non e' "non pubblicare mai niente".
    as_of = T0 + timedelta(days=40)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    row = compute_cohort(
        cohort_start=T0.date(),
        layer_scope=SCOPE_ANY,
        members=members,
        reached_at={i: T0 + timedelta(days=5) for i in (1, 2, 3)},
        excluded_rejoins=0,
        as_of=as_of,
        params=SMALL,
    )

    assert row.reached_by_14d == pytest.approx(0.5)
    assert row.reached_by_28d == pytest.approx(0.5)


def test_retention_non_calcolabile_non_e_retention_perfetta():
    as_of = T0 + timedelta(days=3)
    members = [member(i, joined_days=0) for i in range(1, 7)]

    row = compute_retention(
        cohort_start=T0.date(),
        horizon_days=28,
        members=members,
        excluded_rejoins=0,
        as_of=as_of,
    )

    assert row.is_computable is False
    assert row.retained_fraction is None, "mai 100% per assenza di osservazione"


def test_denominatore_unico_tra_coorti_e_retention_con_rientri_esclusi():
    as_of = T0 + timedelta(days=60)
    members = [member(i, joined_days=0) for i in range(1, 7)]
    members.append(member(99, joined_days=0, prior=True))

    result = build_metrics(
        snapshot_id=1,
        guild_id=99,
        as_of=as_of,
        params=dataclasses.replace(SMALL, min_cardinality=5),
        edges=[],
        members=members,
    )

    cohort_rows = {(r.cohort_start, r.layer_scope): r for r in result.cohorts}
    for retention in result.cohort_retention:
        peer = cohort_rows[(retention.cohort_start, SCOPE_ANY)]
        assert retention.n_effective == peer.n_effective == 6
        assert retention.excluded_rejoins == peer.excluded_rejoins == 1


def test_partner_min_interactions_cambia_chi_e_integrato():
    # Cinque partner da UNA sola interazione ciascuno, tutti nello stesso
    # giorno: integrato con soglia 1, non integrato con soglia 2. E' la
    # differenza tra cinque reazioni emoji e cinque serate in vocale.
    newcomer = member(1, joined_days=0)
    edges = [
        PartnerEdge(
            layer=LAYER_VOICE,
            src_author_id=1,
            dst_author_id=partner,
            weight=1.0,
            interaction_count=1,
        )
        for partner in range(2, 7)
    ]

    def reached(threshold: int):
        params = dataclasses.replace(SMALL, partner_min_interactions=threshold)
        tracker = PartnerTracker([newcomer], params=params)
        tracker.observe(T0 + timedelta(days=1), edges)
        return tracker.reached[SCOPE_ANY]

    assert reached(1) == {1: T0 + timedelta(days=1)}
    assert reached(2) == {}, "cinque interazioni singole non sono cinque relazioni"


def test_partner_distinti_si_accumulano_tra_snapshot():
    # L'evento e' "aver fatto k connessioni", non mantenerle: i partner si
    # accumulano attraverso gli snapshot anche se ogni arco compare una volta
    # sola, e l'istante registrato e' quello del primo snapshot in cui si
    # arriva a k.
    newcomer = member(1, joined_days=0)
    tracker = PartnerTracker([newcomer], params=SMALL)

    for day, partners in ((1, (2, 3)), (8, (4, 5)), (15, (6,))):
        tracker.observe(
            T0 + timedelta(days=day),
            [
                PartnerEdge(
                    layer=LAYER_VOICE,
                    src_author_id=1,
                    dst_author_id=partner,
                    weight=1.0,
                    interaction_count=3,
                )
                for partner in partners
            ],
        )

    assert tracker.reached[SCOPE_ANY] == {1: T0 + timedelta(days=15)}
    assert tracker.snapshots_used == 3


def test_membro_entrato_dopo_lo_snapshot_non_e_osservato():
    # Un membro non puo' aver fatto connessioni prima di esistere: gli snapshot
    # anteriori al suo joined_at non lo riguardano.
    latecomer = member(1, joined_days=10)
    tracker = PartnerTracker([latecomer], params=SMALL)

    assert tracker.pending(T0 + timedelta(days=5)) == []
    assert tracker.pending(T0 + timedelta(days=11)) == [1]
