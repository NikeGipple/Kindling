"""Attribuzione temporale: quali sessioni entrano in quale snapshot.

Casi limite di modello-grafo.md 4.1 e 4.2. Sono i piu' insidiosi perche'
sbagliarli non produce un errore, produce un numero plausibile e sbagliato.
"""

from __future__ import annotations

from conftest import at, join, leave, presence

from job.config import DEFAULT_PARAMS, LAYER_VOICE
from job.snapshot import build_snapshot

GUILD = 111
CANALE = 900


def snapshot(events, *, window_start, window_end, as_of=None, **kwargs):
    return build_snapshot(
        guild_id=GUILD,
        as_of=as_of or window_end,
        window_start=window_start,
        window_end=window_end,
        params=DEFAULT_PARAMS,
        voice_events=events,
        **kwargs,
    )


def test_sessione_a_cavallo_del_confine_e_attribuita_a_dove_finisce():
    # La sessione va dalle 23:40 di domenica alle 00:20 di lunedi'. Il confine
    # tra i due snapshot cade a mezzanotte.
    inizio = at(hours=3, minutes=40)  # 23:40
    fine = at(hours=4, minutes=20)  # 00:20
    mezzanotte = at(hours=4)
    events = [
        *presence(1, CANALE, inizio, fine),
        *presence(2, CANALE, inizio, fine),
    ]

    primo = snapshot(events, window_start=at(days=-7), window_end=mezzanotte)
    secondo = snapshot(events, window_start=mezzanotte, window_end=at(days=7))

    # Mai spezzata a meta' per far quadrare la finestra: o e' tutta di qua o
    # e' tutta di la'.
    assert primo.edges == []
    assert len(secondo.edges) == 1
    assert secondo.edges[0].raw_units == 40.0
    assert secondo.edges[0].layer == LAYER_VOICE


def test_sessione_aperta_esclusa_ora_e_contata_dopo_senza_doppio_conteggio():
    # Al primo calcolo Bo e' ancora in canale: il suo leave non e' ancora
    # arrivato. Al secondo, con il leave in raw_events, la sessione entra per
    # intero — e il primo snapshot non ne aveva contato nessun pezzo.
    aperti = [
        *presence(1, CANALE, at(), at(hours=1)),
        join(2, CANALE, at()),
    ]
    chiusi = [*aperti, leave(2, CANALE, at(hours=1))]

    primo = snapshot(aperti, window_start=at(days=-1), window_end=at(hours=2))
    secondo = snapshot(chiusi, window_start=at(days=-1), window_end=at(hours=2))

    assert primo.edges == []
    assert primo.interval_stats.still_open == 1
    assert len(secondo.edges) == 1
    assert secondo.edges[0].raw_units == 60.0


def test_rieseguire_lo_stesso_calcolo_da_lo_stesso_risultato():
    # Idempotenza: nessuno stato accumulato tra un'esecuzione e l'altra, il
    # grafo si ricostruisce sempre da zero dagli eventi.
    events = [
        *presence(1, CANALE, at(), at(hours=1)),
        *presence(2, CANALE, at(), at(hours=1)),
    ]
    kwargs = dict(window_start=at(days=-1), window_end=at(hours=2), as_of=at(hours=2))

    primo = snapshot(events, **kwargs)
    secondo = snapshot(events, **kwargs)

    assert [vars(e) for e in primo.edges] == [vars(e) for e in secondo.edges]


def test_sessione_fuori_finestra_non_entra_ma_viene_contata():
    events = [
        *presence(1, CANALE, at(days=-10), at(days=-10, hours=1)),
        *presence(2, CANALE, at(days=-10), at(days=-10, hours=1)),
    ]
    result = snapshot(events, window_start=at(days=-1), window_end=at(hours=2))

    assert result.edges == []
    assert result.sessions_out_of_window == 1


def test_spostamento_tra_canali_conta_la_copresenza_in_entrambi():
    # Ada passa mezz'ora in generale con Bo, poi si sposta in musica dove c'e'
    # Cy. Due archi, uno per canale, trenta minuti ciascuno: lo spostamento e'
    # un leave piu' un join, non un buco.
    generale, musica = 900, 901
    events = [
        # Ada: leave da generale e join in musica nello stesso istante.
        join(1, generale, at()),
        leave(1, generale, at(minutes=30)),
        join(1, musica, at(minutes=30)),
        leave(1, musica, at(hours=1)),
        *presence(2, generale, at(), at(hours=1)),
        *presence(3, musica, at(), at(hours=1)),
    ]
    result = snapshot(events, window_start=at(days=-1), window_end=at(hours=2))

    per_coppia = {
        (e.src_author_id, e.dst_author_id): e for e in result.edges
    }
    assert set(per_coppia) == {(1, 2), (1, 3)}
    assert per_coppia[(1, 2)].raw_units == 30.0
    assert per_coppia[(1, 3)].raw_units == 30.0
    # Bo e Cy non si sono mai incrociati: erano in canali diversi.
    assert (2, 3) not in per_coppia
