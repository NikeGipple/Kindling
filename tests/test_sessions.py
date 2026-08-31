"""Ricostruzione degli intervalli e raggruppamento in sessioni.

Casi limite di modello-grafo.md 3.1, 3.2 e 4.3.
"""

from __future__ import annotations

from conftest import at, join, leave, presence

from job.config import DEFAULT_PARAMS
from job.intervals import RestartMarker, activity_lookup, build_intervals
from job.sessions import KIND_BONFIRE, KIND_EMBER, build_sessions

GUILD = 111
CANALE = 900


def sessions_from(events, **kwargs):
    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS, **kwargs)
    return build_sessions(intervals, guild_id=GUILD, params=DEFAULT_PARAMS), stats


def test_due_episodi_vicini_sono_la_stessa_sessione():
    # Ada esce alle 20:10, Bo entra alle 20:15: cinque minuti di distanza,
    # meno della finestra di sessione, quindi stessa sessione. Non si sono
    # pero' mai incrociati: e' edges.py a doverlo notare, non questo stadio.
    events = [
        *presence(1, CANALE, at(), at(minutes=10)),
        *presence(2, CANALE, at(minutes=15), at(minutes=40)),
    ]
    sessions, _ = sessions_from(events)

    assert len(sessions) == 1
    assert sessions[0].participant_count == 2
    assert sessions[0].kind == KIND_EMBER


def test_episodi_lontani_sono_sessioni_diverse():
    events = [
        *presence(1, CANALE, at(), at(minutes=10)),
        *presence(2, CANALE, at(minutes=45), at(minutes=70)),
    ]
    sessions, _ = sessions_from(events)

    assert len(sessions) == 2


def test_chi_resta_a_lungo_tiene_aperta_la_sessione():
    # Il confronto per il raggruppamento e' con la fine piu' tarda vista
    # finora, non con quella dell'ultimo intervallo per ordine di inizio:
    # altrimenti chi entra ed esce in fretta "chiuderebbe" una sessione che
    # qualcun altro sta ancora tenendo in piedi.
    events = [
        *presence(1, CANALE, at(), at(hours=3)),
        *presence(2, CANALE, at(minutes=5), at(minutes=10)),
        *presence(3, CANALE, at(hours=2), at(hours=2, minutes=30)),
    ]
    sessions, _ = sessions_from(events)

    assert len(sessions) == 1
    assert sessions[0].participant_count == 3
    assert sessions[0].kind == KIND_BONFIRE


def test_canali_diversi_non_si_mescolano_mai():
    events = [
        *presence(1, 900, at(), at(hours=1)),
        *presence(2, 901, at(), at(hours=1)),
    ]
    sessions, _ = sessions_from(events)

    assert len(sessions) == 2
    assert all(s.participant_count == 1 for s in sessions)


def test_sessione_ancora_aperta_e_esclusa():
    # Join senza leave e nessun riavvio dopo: il bot e' rimasto acceso, quindi
    # il leave non e' andato perso, semplicemente non e' ancora avvenuto.
    events = [
        *presence(1, CANALE, at(), at(hours=1)),
        join(2, CANALE, at()),
    ]
    sessions, stats = sessions_from(events)

    assert stats.still_open == 1
    assert sessions[0].participants == {1}


def test_sessione_orfana_chiusa_al_primo_evento_successivo():
    # Join senza leave, ma dopo c'e' un riavvio: il leave e' andato perso.
    # Ada ha scritto un messaggio alle 20:45, quindi la presenza si chiude li'
    # e non all'inizio del downtime (21:30), che e' piu' tardi.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(restarted_at=at(hours=4), downtime_start=at(hours=1, minutes=30))
    ]
    activity = activity_lookup({1: [at(), at(minutes=45)]})

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts, next_activity=activity
    )

    assert stats.still_open == 0
    assert len(intervals) == 1
    assert intervals[0].end == at(minutes=45)
    assert intervals[0].is_reconciled is True


def test_sessione_orfana_troppo_lunga_e_scartata():
    # Nessuna attivita' successiva nota e un downtime iniziato tredici ore
    # dopo il join: l'intervallo che ne uscirebbe supera il tetto di dodici
    # ore. Va scartato, non troncato a dodici.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(restarted_at=at(hours=20), downtime_start=at(hours=13))
    ]

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts
    )

    assert intervals == []
    assert stats.discarded_over_cap == 1


def test_leave_ricostruito_marca_lintervallo():
    # Il leave sintetico scritto dalla riconciliazione all'avvio del bot: e'
    # una riga vera in raw_events, ma il dato che porta e' ricostruito.
    events = [
        join(1, CANALE, at()),
        leave(1, CANALE, at(minutes=30), reconstructed=True),
    ]
    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS)

    assert intervals[0].is_reconciled is True
    assert stats.closed_reconciled == 1
    assert stats.closed_observed == 0


def test_leave_ricostruito_oltre_il_tetto_e_scartato():
    events = [
        join(1, CANALE, at()),
        leave(1, CANALE, at(hours=13), reconstructed=True),
    ]
    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS)

    assert intervals == []
    assert stats.discarded_over_cap == 1


def test_sessione_osservata_lunga_non_viene_scartata():
    # Il tetto vale per gli intervalli ricostruiti, non per quelli osservati:
    # una maratona di tredici ore con join e leave entrambi visti e' un dato,
    # non un'implausibilita'.
    events = [*presence(1, CANALE, at(), at(hours=13))]
    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS)

    assert len(intervals) == 1
    assert stats.discarded_over_cap == 0
