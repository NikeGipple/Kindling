"""voice e' una proiezione di affiliazione: una sessione sola non ammette la coppia.

modello-metriche.md 2.5. Una sessione da n persone genera tutte le n(n-1)/2
coppie in un colpo, e per le componenti connesse un arco diluito da
``size_factor`` vale quanto uno forte: un solo falo' affollato basta a mettere
tutti nella stessa componente. Il grafo strutturale di voice chiede quindi
``voice_structural_min_sessions`` sessioni condivise distinte.

Le sessioni qui sono costruite dagli eventi, con le stesse funzioni del job
(intervalli, sessioni, archi), non scrivendo archi a mano con un
``interaction_count`` scelto: il numero che la soglia guarda deve essere quello
che ``build_voice_edges`` produce davvero per una sessione.
"""

from __future__ import annotations

import dataclasses

from conftest import at, presence

from job.cohorts import CohortMember, PartnerEdge, PartnerTracker
from job.config import DEFAULT_PARAMS, LAYER_REPLY, LAYER_VOICE, MetricParams
from job.edges import Edge, build_voice_edges
from job.graph import build_metric_graph
from job.intervals import build_intervals
from job.sessions import build_sessions

GUILD = 111
CANALE = 900

# Default del job, tranne k: il tracker butta l'insieme dei partner appena il
# membro arriva a k, e qui l'insieme e' proprio cio' che si vuole ispezionare.
PARAMS = MetricParams(k_connections=99)

FALO = (1, 2, 3, 4, 5)


def _voice_edges(events) -> list[Edge]:
    intervals, _ = build_intervals(events, params=DEFAULT_PARAMS)
    sessions = build_sessions(intervals, guild_id=GUILD, params=DEFAULT_PARAMS)
    as_of = max(s.ended_at for s in sessions)
    return build_voice_edges(sessions, as_of=as_of, params=DEFAULT_PARAMS)


def _un_falo():
    # Cinque persone nello stesso canale per un'ora, tutte sovrapposte ben oltre
    # min_overlap_minutes.
    return [
        event
        for author_id in FALO
        for event in presence(author_id, CANALE, at(), at(minutes=60))
    ]


def _seconda_sessione(*partecipanti):
    # Il giorno dopo: ben oltre session_window, quindi una sessione distinta.
    return [
        event
        for author_id in partecipanti
        for event in presence(author_id, CANALE, at(days=1), at(days=1, minutes=45))
    ]


def _coppie(graph) -> set[frozenset[int]]:
    return {
        frozenset((graph.vs[a]["author_id"], graph.vs[b]["author_id"]))
        for a, b in graph.get_edgelist()
    }


def _partner_di(edges, author_id, *, scope):
    tracker = PartnerTracker(
        [CohortMember(author_id=author_id, joined_at=at(days=-1))], params=PARAMS
    )
    tracker.observe(
        at(days=2),
        [
            PartnerEdge(
                layer=e.layer,
                src_author_id=e.src_author_id,
                dst_author_id=e.dst_author_id,
                weight=e.weight,
                interaction_count=e.interaction_count,
            )
            for e in edges
        ],
    )
    return tracker.partners_of(author_id, scope=scope)


def test_un_solo_falo_non_ammette_nessuna_coppia_nel_grafo_strutturale():
    edges = _voice_edges(_un_falo())

    # Gli archi ci sono tutti, con peso positivo: e' l'ammissione a fermarli,
    # non la costruzione.
    assert len(edges) == len(FALO) * (len(FALO) - 1) // 2
    assert all(e.weight > 0 and e.interaction_count == 1 for e in edges)

    graph = build_metric_graph(edges, layer=LAYER_VOICE, params=PARAMS)
    assert graph.ecount() == 0
    assert graph.vcount() == 0


def test_due_sessioni_condivise_ammettono_solo_la_coppia_che_si_e_ritrovata():
    # 1 e 2 si ritrovano il giorno dopo con una persona nuova (6): la coppia 1-2
    # ha due sessioni, 1-6 e 2-6 una sola, le altre del falo' una sola.
    edges = _voice_edges([*_un_falo(), *_seconda_sessione(1, 2, 6)])

    graph = build_metric_graph(edges, layer=LAYER_VOICE, params=PARAMS)
    assert _coppie(graph) == {frozenset((1, 2))}
    assert sorted(graph.vs["author_id"]) == [1, 2]


def test_il_conteggio_dei_partner_delle_coorti_non_cambia():
    # Stessi scenari: per le coorti la domanda e' "ha stabilito un contatto", e
    # un solo falo' e' una risposta legittima (partner_min_interactions = 1).
    un_falo = _voice_edges(_un_falo())
    assert _partner_di(un_falo, 1, scope=LAYER_VOICE) == {2, 3, 4, 5}

    con_ritorno = _voice_edges([*_un_falo(), *_seconda_sessione(1, 2, 6)])
    assert _partner_di(con_ritorno, 1, scope=LAYER_VOICE) == {2, 3, 4, 5, 6}


def test_la_soglia_riguarda_solo_voice():
    # Una sola reply resta un arco del grafo strutturale di reply: i layer
    # direzionali sono diadici, nessuna proiezione da un evento a n persone.
    reply = Edge(
        layer=LAYER_REPLY,
        src_author_id=1,
        dst_author_id=2,
        weight=1.0,
        weight_undecayed=1.0,
        raw_units=1.0,
        interaction_count=1,
    )
    graph = build_metric_graph([reply], layer=LAYER_REPLY, params=PARAMS)
    assert _coppie(graph) == {frozenset((1, 2))}


def test_la_soglia_viene_dal_parametro_ed_e_registrata_nel_run():
    # A 1 torna la regola precedente: la soglia e' un parametro, non un 2 fisso.
    edges = _voice_edges(_un_falo())
    permissivo = dataclasses.replace(PARAMS, voice_structural_min_sessions=1)
    assert build_metric_graph(edges, layer=LAYER_VOICE, params=permissivo).ecount() == 10

    # E finisce in metric_runs.params: due run con soglie diverse devono potersi
    # riconoscere come tali.
    assert MetricParams().as_run_params()["voice_structural_min_sessions"] == 2
