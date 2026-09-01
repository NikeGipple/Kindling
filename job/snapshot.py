"""Orchestrazione del calcolo di uno snapshot, senza toccare il database.

Mette in fila i pezzi nell'ordine in cui il modello li definisce —
ricostruzione degli intervalli, sessioni, archi voce, archi direzionali,
decadimento — e decide cosa entra nella finestra e cosa no. E' una funzione
pura perche' e' qui che vivono le regole di attribuzione temporale
(modello-grafo.md 4.1 e 4.2), che sono la parte piu' facile da sbagliare in
silenzio e la piu' importante da poter testare senza un Postgres a portata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from .config import GraphParams
from .edges import (
    DirectedInteraction,
    Edge,
    LayerCoverage,
    build_directed_edges,
    build_voice_edges,
)
from .intervals import (
    IntervalStats,
    NextActivity,
    RestartMarker,
    VoiceEvent,
    build_intervals,
)
from .sessions import VoiceSession, build_sessions


@dataclass
class SnapshotResult:
    guild_id: int
    as_of: datetime
    window_start: datetime
    window_end: datetime
    edges: list[Edge] = field(default_factory=list)
    # Solo le sessioni attribuite a questo snapshot: sono quelle che vengono
    # persistite e che hanno generato archi.
    sessions: list[VoiceSession] = field(default_factory=list)
    # Ricostruite ma fuori finestra: non entrano nello snapshot, servono solo
    # alla diagnostica ("quante ne ho viste e quante ne ho usate").
    sessions_out_of_window: int = 0
    interval_stats: IntervalStats = field(default_factory=IntervalStats)
    coverage: dict[str, LayerCoverage] = field(default_factory=dict)

    def as_snapshot_stats(self) -> dict[str, Any]:
        """I contatori da salvare in ``graph_snapshots.stats``.

        Stessa logica di ``GraphParams.as_snapshot_params``: cio' che serve a
        interpretare uno snapshot viaggia dentro lo snapshot. Qui sono gli
        scarti — intervalli ancora aperti, join duplicati, target non risolti
        — che altrimenti resterebbero in una riga di log, invisibili al
        confronto tra un'esecuzione e la successiva.
        """
        return {
            "intervals": asdict(self.interval_stats),
            "sessions": {
                "in_window": len(self.sessions),
                "out_of_window": self.sessions_out_of_window,
            },
            "coverage": {
                layer: asdict(cov) for layer, cov in sorted(self.coverage.items())
            },
        }


def build_snapshot(
    *,
    guild_id: int,
    as_of: datetime,
    window_start: datetime,
    window_end: datetime,
    params: GraphParams,
    voice_events: Iterable[VoiceEvent] = (),
    restarts: Iterable[RestartMarker] = (),
    next_activity: Optional[NextActivity] = None,
    interactions: Iterable[DirectedInteraction] = (),
) -> SnapshotResult:
    """Calcola archi e sessioni di uno snapshot.

    ``voice_events`` deve gia' includere il margine oltre i bordi della
    finestra: e' qui che una sessione a cavallo del confine viene ricostruita
    intera e poi attribuita, per intero, allo snapshot in cui cade la sua
    fine. ``interactions``, al contrario, riguardano istanti singoli e vanno
    passate gia' filtrate sulla finestra.
    """
    intervals, interval_stats = build_intervals(
        voice_events, params=params, restarts=restarts, next_activity=next_activity
    )
    all_sessions = build_sessions(intervals, guild_id=guild_id, params=params)

    # Una sessione appartiene allo snapshot in cui cade la sua FINE, mai
    # spezzata a meta' per far quadrare la finestra. La conseguenza voluta e'
    # che una sessione ancora in corso (fine oltre la finestra, o mai
    # osservata) semplicemente non e' qui: verra' contata per intero al
    # prossimo snapshot, senza doppio conteggio, perche' ogni snapshot
    # ricalcola tutto da raw_events invece di sommarsi al precedente.
    in_window = [s for s in all_sessions if window_start <= s.ended_at < window_end]

    voice_edges = build_voice_edges(in_window, as_of=as_of, params=params)
    directed_edges, coverage = build_directed_edges(
        interactions, as_of=as_of, params=params
    )

    return SnapshotResult(
        guild_id=guild_id,
        as_of=as_of,
        window_start=window_start,
        window_end=window_end,
        # Concatenati, non fusi: ogni arco porta il proprio layer e i pesi non
        # vengono mai sommati tra layer diversi.
        edges=[*voice_edges, *directed_edges],
        sessions=in_window,
        sessions_out_of_window=len(all_sessions) - len(in_window),
        interval_stats=interval_stats,
        coverage=coverage,
    )
