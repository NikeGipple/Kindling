"""Dagli intervalli agli archi: soglie, normalizzazione, copertura, self-loop.

Casi limite di modello-grafo.md 3.3 e 6.
"""

from __future__ import annotations

from conftest import at, presence

from job.config import (
    DEFAULT_PARAMS,
    LAYER_MENTION,
    LAYER_REACTION,
    LAYER_REPLY,
    LAYER_VOICE,
)
from job.edges import DirectedInteraction, build_directed_edges, build_voice_edges
from job.intervals import build_intervals
from job.sessions import build_sessions

GUILD = 111
CANALE = 900


def voice_edges(events, *, as_of=None):
    intervals, _ = build_intervals(events, params=DEFAULT_PARAMS)
    sessions = build_sessions(intervals, guild_id=GUILD, params=DEFAULT_PARAMS)
    # as_of alla fine dell'ultima sessione: cosi' il decadimento vale 1 e i
    # test sui pesi non misurano due cose insieme.
    as_of = as_of or max(s.ended_at for s in sessions)
    return build_voice_edges(sessions, as_of=as_of, params=DEFAULT_PARAMS)


def test_vicini_nel_tempo_ma_mai_sovrapposti_non_fanno_un_arco():
    # Il falso positivo che il modello esiste apposta per evitare: due persone
    # nello stesso canale a cinque minuti di distanza sono nella stessa
    # sessione, ma non si sono mai incrociate.
    events = [
        *presence(1, CANALE, at(), at(minutes=10)),
        *presence(2, CANALE, at(minutes=15), at(minutes=40)),
    ]
    assert voice_edges(events) == []


def test_sovrapposizione_sotto_la_soglia_non_fa_un_arco():
    # Tre minuti in comune: si sono incrociati mentre uno usciva e l'altro
    # entrava, non hanno passato del tempo insieme.
    events = [
        *presence(1, CANALE, at(), at(minutes=20)),
        *presence(2, CANALE, at(minutes=17), at(minutes=40)),
    ]
    assert voice_edges(events) == []


def test_sovrapposizione_sopra_la_soglia_fa_un_arco():
    events = [
        *presence(1, CANALE, at(), at(minutes=30)),
        *presence(2, CANALE, at(minutes=20), at(minutes=50)),
    ]
    edges = voice_edges(events)

    assert len(edges) == 1
    assert edges[0].layer == LAYER_VOICE
    assert edges[0].raw_units == 10.0
    assert edges[0].interaction_count == 1


def test_soglia_applicata_per_sessione_non_sulla_somma():
    # Quattro minuti in una serata piu' quattro in un'altra non fanno otto
    # minuti sopra soglia: nessuna delle due sessioni supera i cinque minuti.
    events = [
        *presence(1, CANALE, at(), at(minutes=20)),
        *presence(2, CANALE, at(minutes=16), at(minutes=40)),
        *presence(1, CANALE, at(days=1), at(days=1, minutes=20)),
        *presence(2, CANALE, at(days=1, minutes=16), at(days=1, minutes=40)),
    ]
    assert voice_edges(events) == []


def test_sessioni_multiple_si_sommano_e_si_contano():
    events = [
        *presence(1, CANALE, at(), at(minutes=30)),
        *presence(2, CANALE, at(), at(minutes=30)),
        *presence(1, CANALE, at(days=1), at(days=1, minutes=30)),
        *presence(2, CANALE, at(days=1), at(days=1, minutes=30)),
    ]
    edges = voice_edges(events, as_of=at(days=1, minutes=30))

    assert len(edges) == 1
    assert edges[0].raw_units == 60.0
    # Frequenza e intensita' sono grandezze diverse e vanno tenute distinte.
    assert edges[0].interaction_count == 2


def test_sessantaminuti_in_due_pesano_piu_di_sessanta_in_venti():
    in_due = [
        *presence(1, CANALE, at(), at(hours=1)),
        *presence(2, CANALE, at(), at(hours=1)),
    ]
    in_venti = []
    for author_id in range(1, 21):
        in_venti.extend(presence(author_id, 901, at(), at(hours=1)))

    coppia = voice_edges(in_due)[0]
    folla = [e for e in voice_edges(in_venti) if {e.src_author_id, e.dst_author_id} == {1, 2}][0]

    # Stessi minuti grezzi: la co-presenza misurata e' identica.
    assert coppia.raw_units == folla.raw_units == 60.0
    # Ma il legame no: 1/(n-1) corregge la crescita meccanica della
    # co-appartenenza con la dimensione del gruppo.
    assert coppia.weight_undecayed == 60.0
    assert folla.weight_undecayed == 60.0 / 19
    assert coppia.weight_undecayed > folla.weight_undecayed


def test_coppia_non_diretta_memorizzata_una_volta_sola():
    events = [
        *presence(7, CANALE, at(), at(hours=1)),
        *presence(3, CANALE, at(), at(hours=1)),
    ]
    edges = voice_edges(events)

    assert len(edges) == 1
    assert edges[0].src_author_id < edges[0].dst_author_id


def test_arco_ricostruito_e_marcato():
    from conftest import join, leave

    events = [
        *presence(1, CANALE, at(), at(hours=1)),
        join(2, CANALE, at()),
        leave(2, CANALE, at(hours=1), reconstructed=True),
    ]
    edges = voice_edges(events)

    assert len(edges) == 1
    assert edges[0].is_reconciled is True


# ---- layer direzionali ----------------------------------------------------


def test_bersaglio_non_risolvibile_non_fa_un_arco_ma_si_conta():
    # Reply a un messaggio anteriore all'arrivo del bot, o cancellato: un arco
    # verso un autore ignoto non esiste, ma l'interazione e' avvenuta e la
    # copertura deve dirlo.
    interactions = [
        DirectedInteraction(LAYER_REPLY, 1, None, at()),
        DirectedInteraction(LAYER_REPLY, 1, 2, at()),
    ]
    edges, coverage = build_directed_edges(
        interactions, as_of=at(), params=DEFAULT_PARAMS
    )

    assert len(edges) == 1
    assert coverage[LAYER_REPLY].total == 2
    assert coverage[LAYER_REPLY].resolved == 1
    assert coverage[LAYER_REPLY].unresolved == 1
    assert coverage[LAYER_REPLY].resolution_rate == 0.5


def test_self_loop_scartati_in_tutti_i_layer_direzionali():
    interactions = [
        DirectedInteraction(LAYER_REPLY, 1, 1, at()),
        DirectedInteraction(LAYER_MENTION, 1, 1, at()),
        DirectedInteraction(LAYER_REACTION, 1, 1, at()),
    ]
    edges, coverage = build_directed_edges(
        interactions, as_of=at(), params=DEFAULT_PARAMS
    )

    assert edges == []
    assert all(c.self_loops == 1 for c in coverage.values())


def test_nessun_self_loop_possibile_nel_layer_voce():
    # Anche entrando e uscendo piu' volte nella stessa sessione, le coppie si
    # formano solo tra partecipanti distinti.
    events = [
        *presence(1, CANALE, at(), at(minutes=20)),
        *presence(1, CANALE, at(minutes=25), at(minutes=60)),
    ]
    assert all(e.src_author_id != e.dst_author_id for e in voice_edges(events))


def test_layer_direzionali_restano_distinti():
    # Stessa coppia, tre tipi di interazione: tre archi, non uno sommato.
    interactions = [
        DirectedInteraction(LAYER_REPLY, 1, 2, at()),
        DirectedInteraction(LAYER_MENTION, 1, 2, at()),
        DirectedInteraction(LAYER_REACTION, 1, 2, at()),
    ]
    edges, _ = build_directed_edges(interactions, as_of=at(), params=DEFAULT_PARAMS)

    assert {e.layer for e in edges} == {LAYER_REPLY, LAYER_MENTION, LAYER_REACTION}
    assert all(e.weight_undecayed == 1.0 for e in edges)


def test_direzione_conservata_nei_layer_diretti():
    interactions = [
        DirectedInteraction(LAYER_REPLY, 5, 2, at()),
        DirectedInteraction(LAYER_REPLY, 2, 5, at()),
    ]
    edges, _ = build_directed_edges(interactions, as_of=at(), params=DEFAULT_PARAMS)

    # Due archi opposti, non una coppia canonicalizzata: A che risponde a B non
    # e' la stessa cosa di B che risponde ad A.
    assert len(edges) == 2


def test_interazione_vecchia_pesa_meno_di_una_recente():
    recente, _ = build_directed_edges(
        [DirectedInteraction(LAYER_REPLY, 1, 2, at(days=14))],
        as_of=at(days=14),
        params=DEFAULT_PARAMS,
    )
    vecchia, _ = build_directed_edges(
        [DirectedInteraction(LAYER_REPLY, 1, 2, at())],
        as_of=at(days=14),
        params=DEFAULT_PARAMS,
    )

    assert recente[0].weight_undecayed == vecchia[0].weight_undecayed == 1.0
    assert vecchia[0].weight < recente[0].weight
