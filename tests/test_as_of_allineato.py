"""L'as_of dello snapshot e la distanza che la stabilita' deve dichiarare.

Due difetti, uno la causa dell'altro:

- ``as_of`` veniva preso da ``now()`` al microsecondo, quindi la chiave
  ``(guild_id, as_of, window_start, window_end)`` non si ripeteva mai e l'
  ``ON CONFLICT`` di ``write_snapshot`` era irraggiungibile. L'idempotenza
  promessa dal runbook e dall'intestazione di ``ops/kindling-weekly.sh`` era
  falsa: ogni lancio a mano creava una riga nuova invece di riscrivere;
- di conseguenza ``fetch_previous_snapshot`` poteva scegliere come precedente
  uno snapshot di un giorno prima, con finestre sovrapposte all'85-95%, e la
  ``stability_jaccard`` che ne usciva veniva salvata senza nessun campo che
  dicesse a che distanza fosse stata calcolata.

Qui si verifica l'ancoraggio (sempre lunedi' 00:00 UTC, qualunque sia il giorno
in cui il job parte) e che la riga di community porti
``previous_gap_days``: colonna tipizzata, non chiave di ``details``.
Che l'UPSERT venga davvero raggiunto e' un'altra cosa e richiede Postgres: sta
in ``tests/test_write_snapshot_upsert.py``.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from job import main
from job.communities import compute_communities, partition_of
from job.config import LAYER_VOICE, MetricParams
from job.edges import Edge
from job.graph import build_metric_graph
from job.metrics import PreviousPartition, build_metrics

# Lunedi' 31/08/2026: la settimana ISO che finisce con la domenica 06/09, cioe'
# quella dei lanci manuali che hanno prodotto gli snapshot 8, 9 e 10.
LUNEDI = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)

SMALL = MetricParams(
    min_cardinality=3,
    min_nodes_structural=4,
    baseline_repetitions=25,
    min_modularity_z=1.0,
)


def _orologio_fermo(monkeypatch, momento: datetime) -> None:
    """Congela ``datetime.now`` dentro job.main, e solo li'.

    Una sottoclasse invece di un mock: ``_week_aligned_now`` non chiama solo
    ``now()``, chiama anche ``datetime.combine``, e sostituire l'intera classe
    con un finto romperebbe la seconda mentre si prova la prima.
    """

    class Orologio(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento if tz is None else momento.astimezone(tz)

    monkeypatch.setattr(main, "datetime", Orologio)


# --- l'ancora --------------------------------------------------------------


@pytest.mark.parametrize("giorno", range(7))
@pytest.mark.parametrize("ora,minuto", [(0, 0), (4, 15), (13, 37), (23, 59)])
def test_qualunque_giorno_della_settimana_da_lo_stesso_lunedi(
    monkeypatch, giorno, ora, minuto
):
    # Il punto del fix: sette esecuzioni in giorni diversi della stessa
    # settimana ISO devono produrre lo STESSO as_of, altrimenti la chiave dello
    # snapshot non si ripete e l'UPSERT non viene mai raggiunto.
    adesso = LUNEDI + timedelta(days=giorno, hours=ora, minutes=minuto)
    _orologio_fermo(monkeypatch, adesso)

    assert main._week_aligned_now() == LUNEDI


@pytest.mark.parametrize("giorno", range(7))
def test_l_ancora_e_sempre_mezzanotte_utc_e_tz_aware(monkeypatch, giorno):
    # tz-aware non e' un dettaglio: tutto lo schema e' TIMESTAMPTZ, e un istante
    # naive verrebbe interpretato dal driver con il fuso della macchina.
    _orologio_fermo(monkeypatch, LUNEDI + timedelta(days=giorno, hours=11))

    ancorato = main._week_aligned_now()

    assert ancorato.tzinfo == timezone.utc
    assert (ancorato.hour, ancorato.minute, ancorato.second) == (0, 0, 0)
    assert ancorato.microsecond == 0
    assert ancorato.weekday() == 0


def test_l_ancora_usa_la_stessa_funzione_delle_coorti(monkeypatch):
    # Non un calcolo del lunedi' riscritto in main.py: se le due nozioni si
    # separassero, i confini delle finestre e quelli delle coorti comincerebbero
    # a sfalsarsi senza che niente lo segnali.
    from job.cohorts import cohort_start_of

    adesso = LUNEDI + timedelta(days=3, hours=4, minutes=15)
    _orologio_fermo(monkeypatch, adesso)

    assert main._week_aligned_now().date() == cohort_start_of(adesso)


def test_as_of_esplicito_non_viene_allineato():
    # --as-of e' la via di fuga, e deve restare esatto: e' quello che permette
    # di ricalcolare uno snapshot gia' scritto o di confrontare due ampiezze di
    # finestra sullo stesso istante.
    istante = "2026-08-29T14:03:07+00:00"

    assert main._parse_instant(istante) == datetime(
        2026, 8, 29, 14, 3, 7, tzinfo=timezone.utc
    )


# --- previous_gap_days -----------------------------------------------------


def _edge(src: int, dst: int, *, weight: float) -> Edge:
    low, high = (src, dst) if src < dst else (dst, src)
    return Edge(
        layer=LAYER_VOICE,
        src_author_id=low,
        dst_author_id=high,
        weight=weight,
        weight_undecayed=weight,
        raw_units=weight,
        interaction_count=1,
        last_interaction_at=LUNEDI,
    )


def _due_cricche(size: int = 5) -> list[Edge]:
    primo = list(range(1, size + 1))
    secondo = list(range(100, 100 + size))
    edges = []
    for gruppo in (primo, secondo):
        for i, a in enumerate(gruppo):
            for b in gruppo[i + 1 :]:
                edges.append(_edge(a, b, weight=10.0))
    edges.append(_edge(primo[-1], secondo[0], weight=0.5))
    return edges


def _riga_di_community(*, gap: timedelta):
    graph = build_metric_graph(_due_cricche(), layer=LAYER_VOICE, params=SMALL)
    precedente = partition_of(graph, params=SMALL)
    as_of = LUNEDI + timedelta(days=7)
    row, _ = compute_communities(
        graph,
        layer=LAYER_VOICE,
        params=SMALL,
        rng=random.Random("gap"),
        as_of=as_of,
        previous=precedente,
        previous_snapshot_id=41,
        previous_as_of=as_of - gap,
    )
    return row


def test_la_riga_di_stabilita_dichiara_la_distanza_dal_precedente():
    row = _riga_di_community(gap=timedelta(days=7))

    assert row.stability_jaccard is not None, "senza stabilita' il test non prova nulla"
    # Colonna tipizzata e non chiave di details: details e' dichiarata
    # diagnostica e non contratto (api/models.py), e questo e' l'unico dei
    # quattro dati del confronto che dice se gli altri tre valgono qualcosa.
    assert row.previous_gap_days == 7.0
    assert "previous_gap_days" not in row.details


def test_la_distanza_e_quella_vera_anche_fuori_cadenza():
    # E' il caso che ha reso necessario il campo: due snapshot a un giorno di
    # distanza confrontano finestre da sette giorni sovrapposte all'85-95%, e la
    # stabilita' che ne esce e' gonfiata. Il numero lo dice; la riga resta
    # significativa, perche' decidere sta a chi legge.
    row = _riga_di_community(gap=timedelta(days=1, hours=6))

    assert row.previous_gap_days == 1.25
    assert row.is_significant is True
    assert "not_significant_because" not in row.details
    assert "stability_unavailable" not in row.details


def test_senza_precedente_non_c_e_nessuna_distanza_da_dichiarare():
    graph = build_metric_graph(_due_cricche(), layer=LAYER_VOICE, params=SMALL)

    row, _ = compute_communities(
        graph,
        layer=LAYER_VOICE,
        params=SMALL,
        rng=random.Random("gap"),
        as_of=LUNEDI,
    )

    assert row.previous_gap_days is None
    assert row.details["stability_unavailable"] == "no_previous_snapshot"


def test_una_partizione_precedente_senza_istanti_e_un_errore():
    # Il campo non deve poter sparire in silenzio: se un chiamante futuro
    # passasse la partizione precedente dimenticando gli as_of, la stabilita'
    # tornerebbe a viaggiare da sola come prima del fix, e nessuno se ne
    # accorgerebbe leggendo una riga a cui manca una chiave di details.
    graph = build_metric_graph(_due_cricche(), layer=LAYER_VOICE, params=SMALL)
    precedente = partition_of(graph, params=SMALL)

    with pytest.raises(ValueError, match="previous_as_of"):
        compute_communities(
            graph,
            layer=LAYER_VOICE,
            params=SMALL,
            rng=random.Random("gap"),
            previous=precedente,
            previous_snapshot_id=41,
        )


def test_la_distanza_arriva_fino_alla_riga_scritta_da_build_metrics():
    # Il pezzo che i test su compute_communities da soli non coprono: che
    # PreviousPartition porti davvero l'as_of e che questo attraversi
    # build_metrics fino ai details della riga. E' li' che una svista si
    # nasconderebbe, perche' il risultato resterebbe una riga valida a cui manca
    # solo una chiave.
    as_of = LUNEDI + timedelta(days=7)
    edges = _due_cricche()
    precedente = partition_of(
        build_metric_graph(edges, layer=LAYER_VOICE, params=SMALL), params=SMALL
    )

    result = build_metrics(
        snapshot_id=7,
        guild_id=515151,
        as_of=as_of,
        params=SMALL,
        edges=edges,
        previous=PreviousPartition(
            snapshot_id=6,
            as_of=as_of - timedelta(days=7),
            membership_by_layer={LAYER_VOICE: precedente},
        ),
    )

    righe = [row for row in result.communities if row.layer == LAYER_VOICE]
    assert righe, "nessuna riga di community: il test non prova nulla"
    row = righe[0]
    assert not row.is_suppressed, "riga soppressa: i details sarebbero vuoti comunque"
    assert row.previous_snapshot_id == 6
    assert row.previous_gap_days == 7.0
