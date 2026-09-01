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


def test_riavvio_che_conferma_la_presenza_lascia_la_sessione_aperta():
    # Il caso osservato in produzione il 31/08/2026: Ada e' entrata alle 20:00
    # ed e' ancora in canale al riavvio delle 23:00. La riconciliazione l'ha
    # vista e ha lasciato la sessione aperta apposta — il marcatore lo dice.
    # Senza quella conferma il job chiuderebbe qui una sessione in corso, e la
    # co-presenza con chiunque altro fosse in canale risulterebbe troncata.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(
            restarted_at=at(hours=3),
            downtime_start=at(hours=2, minutes=50),
            voice_confirmed=frozenset({(1, CANALE)}),
        )
    ]

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts
    )

    assert intervals == []
    assert stats.still_open == 1


def test_riavvio_che_non_conferma_chiude_al_confine_piu_stretto():
    # Stesso riavvio, ma la conferma riguarda un'altra persona: per Ada il
    # leave e' andato perso davvero, e la sessione si chiude all'inizio del
    # downtime. E' il comportamento di prima del fix, e non deve regredire.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(
            restarted_at=at(hours=3),
            downtime_start=at(hours=2, minutes=50),
            voice_confirmed=frozenset({(2, CANALE)}),
        )
    ]

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts
    )

    assert stats.still_open == 0
    assert len(intervals) == 1
    assert intervals[0].end == at(hours=2, minutes=50)
    assert intervals[0].is_reconciled is True


def test_conferma_nello_stesso_canale_non_vale_per_un_altro_canale():
    # La conferma e' per la coppia (membro, canale), non per il solo membro:
    # essere in #musica adesso non dice nulla sulla sessione rimasta aperta in
    # #generale, che anzi e' proprio il leave che si e' perso.
    events = [join(1, 900, at())]
    restarts = [
        RestartMarker(
            restarted_at=at(hours=3),
            downtime_start=at(hours=2, minutes=50),
            voice_confirmed=frozenset({(1, 901)}),
        )
    ]

    intervals, _ = build_intervals(events, params=DEFAULT_PARAMS, restarts=restarts)

    assert len(intervals) == 1
    assert intervals[0].end == at(hours=2, minutes=50)


def test_due_riavvii_il_primo_conferma_il_secondo_no():
    # Il bot si riconnette due volte mentre Ada e' in vocale. Al primo riavvio
    # e' ancora li' (confermata, si salta), al secondo non piu': e' quello il
    # riavvio che chiude la sessione.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(
            restarted_at=at(hours=1),
            downtime_start=at(minutes=55),
            voice_confirmed=frozenset({(1, CANALE)}),
        ),
        RestartMarker(
            restarted_at=at(hours=3),
            downtime_start=at(hours=2, minutes=50),
        ),
    ]

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts
    )

    assert stats.still_open == 0
    assert len(intervals) == 1
    assert intervals[0].end == at(hours=2, minutes=50)


def test_una_conferma_e_un_pavimento_non_solo_un_riavvio_da_saltare():
    # Ada scrive un messaggio alle 20:30, poi resta in vocale: al riavvio
    # delle 21:00 e' ancora in canale. Il "primo evento successivo" (20:30) e'
    # anteriore alla conferma, e chiuderla li' taglierebbe via mezz'ora che il
    # bot ha visto con i propri occhi.
    events = [join(1, CANALE, at())]
    restarts = [
        RestartMarker(
            restarted_at=at(hours=1),
            downtime_start=at(minutes=55),
            voice_confirmed=frozenset({(1, CANALE)}),
        ),
        RestartMarker(restarted_at=at(hours=3), downtime_start=at(hours=2)),
    ]
    activity = activity_lookup({1: [at(), at(minutes=30)]})

    intervals, _ = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=restarts, next_activity=activity
    )

    assert len(intervals) == 1
    assert intervals[0].end == at(hours=2)


def test_marcatore_senza_conferme_si_comporta_come_prima():
    # Formato vecchio, gia' in produzione: nessuna chiave voice_confirmed nel
    # payload, quindi nessuna conferma. Non e' un caso da migrare, e' il
    # comportamento precedente che resta valido.
    events = [join(1, CANALE, at())]
    vecchio = RestartMarker(restarted_at=at(hours=3), downtime_start=at(hours=1))

    assert vecchio.voice_confirmed == frozenset()

    intervals, stats = build_intervals(
        events, params=DEFAULT_PARAMS, restarts=[vecchio]
    )

    assert stats.still_open == 0
    assert len(intervals) == 1
    assert intervals[0].end == at(hours=1)
    assert intervals[0].is_reconciled is True


def test_due_join_di_fila_non_gonfiano_un_unico_intervallo():
    # Il leave delle 10 e' andato perso (dati anteriori al fix di voice_move).
    # Il primo intervallo si chiude al secondo join e viene dichiarato
    # ricostruito; il buco tra i due non e' coperto da nulla. Lasciar
    # sopravvivere il primo open_start darebbe un solo intervallo di cinque
    # ore spacciato per osservato.
    events = [
        join(1, CANALE, at()),
        join(1, CANALE, at(hours=4)),
        leave(1, CANALE, at(hours=5)),
    ]

    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS)

    assert stats.duplicate_joins == 1
    assert len(intervals) == 2
    assert (intervals[0].start, intervals[0].end) == (at(), at(hours=4))
    assert intervals[0].is_reconciled is True
    assert (intervals[1].start, intervals[1].end) == (at(hours=4), at(hours=5))
    assert intervals[1].is_reconciled is False
    # Nessun intervallo copre il buco tra le 24:00 e le 00:00 del giorno dopo.
    assert not any(i.start < at(hours=4) < i.end for i in intervals)


def test_join_duplicato_oltre_il_tetto_e_scartato_non_troncato():
    # Tra i due join passano tredici ore: l'intervallo ricostruito supera il
    # tetto di dodici. Vale la stessa regola della sessione orfana — scartato,
    # non troncato a una durata verosimile.
    events = [
        join(1, CANALE, at()),
        join(1, CANALE, at(hours=13)),
        leave(1, CANALE, at(hours=14)),
    ]

    intervals, stats = build_intervals(events, params=DEFAULT_PARAMS)

    assert stats.duplicate_joins == 1
    assert stats.discarded_over_cap == 1
    assert len(intervals) == 1
    assert (intervals[0].start, intervals[0].end) == (at(hours=13), at(hours=14))


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
