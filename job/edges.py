"""Dagli eventi ricostruiti agli archi pesati (modello-grafo.md 3.3, 5, 6).

I quattro layer sono calcolati separatamente e restano separati: nessun peso
combinato viene mai prodotto qui, nemmeno come comodita' intermedia. Sono
relazioni di tipo diverso e la letteratura SNA sconsiglia esplicitamente di
collassarle in un'unica sociomatrice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from .config import UNDIRECTED_LAYERS, GraphParams, LAYER_VOICE
from .decay import decay_factor
from .intervals import PresenceInterval
from .sessions import VoiceSession

_SECONDS_PER_MINUTE = 60.0


@dataclass
class Edge:
    layer: str
    src_author_id: int
    dst_author_id: int
    # Normalizzato per dimensione della sessione e decaduto rispetto ad as_of.
    weight: float = 0.0
    # Stesso peso senza decadimento: isola l'effetto di H.
    weight_undecayed: float = 0.0
    # Minuti grezzi (voice) o conteggio (layer direzionali): ne' normalizzati
    # ne' decaduti, per poter riapplicare in futuro un'altra normalizzazione.
    raw_units: float = 0.0
    interaction_count: int = 0
    last_interaction_at: Optional[datetime] = None
    is_reconciled: bool = False

    def add(
        self,
        *,
        contribution: float,
        raw: float,
        at: datetime,
        decay: float,
        reconciled: bool = False,
    ) -> None:
        self.weight += contribution * decay
        self.weight_undecayed += contribution
        self.raw_units += raw
        self.interaction_count += 1
        if self.last_interaction_at is None or at > self.last_interaction_at:
            self.last_interaction_at = at
        self.is_reconciled = self.is_reconciled or reconciled


@dataclass
class LayerCoverage:
    """Copertura della risoluzione del bersaglio, per layer.

    Un'interazione il cui target non e' risolvibile non genera un arco — un
    arco verso un autore ignoto non esiste — ma sparire in silenzio la
    renderebbe indistinguibile da un'interazione mai avvenuta. Qui si conta.
    """

    total: int = 0
    resolved: int = 0
    unresolved: int = 0
    self_loops: int = 0

    @property
    def resolution_rate(self) -> Optional[float]:
        return (self.resolved / self.total) if self.total else None


@dataclass(frozen=True)
class DirectedInteraction:
    """Una singola interazione direzionale A -> B.

    ``dst_author_id`` None significa bersaglio non risolvibile (messaggio
    anteriore all'arrivo del bot, o cancellato).
    """

    layer: str
    src_author_id: int
    dst_author_id: Optional[int]
    occurred_at: datetime


class EdgeSet:
    """Accumulatore di archi, un layer per volta."""

    def __init__(self) -> None:
        self._edges: dict[tuple[str, int, int], Edge] = {}

    def get(self, layer: str, src: int, dst: int) -> Edge:
        if layer in UNDIRECTED_LAYERS and src > dst:
            # Per i layer non diretti la coppia e' memorizzata una volta sola,
            # con src < dst: senza canonicalizzazione lo stesso legame
            # entrerebbe due volte e conterebbe doppio a valle.
            src, dst = dst, src
        key = (layer, src, dst)
        edge = self._edges.get(key)
        if edge is None:
            edge = Edge(layer=layer, src_author_id=src, dst_author_id=dst)
            self._edges[key] = edge
        return edge

    def all(self) -> list[Edge]:
        return sorted(
            self._edges.values(),
            key=lambda e: (e.layer, e.src_author_id, e.dst_author_id),
        )


def overlap_minutes(
    first: Iterable[PresenceInterval], second: Iterable[PresenceInterval]
) -> float:
    """Minuti di sovrapposizione effettiva tra due insiemi di intervalli.

    E' il cuore del vincolo "co-presenza = sovrapposizione reale": due persone
    nello stesso canale a quindici minuti di distanza danno zero, per quanto
    vicini siano i loro ingressi.
    """
    total = 0.0
    for a in first:
        for b in second:
            start = max(a.start, b.start)
            end = min(a.end, b.end)
            if end > start:
                total += (end - start).total_seconds() / _SECONDS_PER_MINUTE
    return total


def build_voice_edges(
    sessions: Iterable[VoiceSession], *, as_of: datetime, params: GraphParams
) -> list[Edge]:
    """Archi del layer ``voice`` dalle sessioni gia' filtrate per la finestra."""
    edges = EdgeSet()

    for session in sessions:
        participants = sorted(session.participants)
        n = len(participants)
        if n < 2:
            # Una persona da sola in un canale non e' una relazione.
            continue

        # Correzione per dimensione: stare 60 minuti in due pesa piu' che
        # stare 60 minuti in venti. Senza questo fattore i canali affollati
        # producono archi forti per puro effetto numerico.
        size_factor = 1.0 / (n - 1)

        # L'eta' di un contributo vocale si misura dalla FINE della sessione.
        decay = decay_factor(as_of - session.ended_at, params)

        for i, a in enumerate(participants):
            intervals_a = session.intervals_of(a)
            for b in participants[i + 1 :]:
                intervals_b = session.intervals_of(b)
                minutes = overlap_minutes(intervals_a, intervals_b)
                if minutes < params.min_overlap_minutes:
                    # Soglia applicata per sessione, non sulla somma del
                    # periodo: dieci incroci da un minuto in dieci serate
                    # diverse non fanno una relazione da dieci minuti.
                    continue
                reconciled = any(
                    interval.is_reconciled for interval in (*intervals_a, *intervals_b)
                )
                edges.get(LAYER_VOICE, a, b).add(
                    contribution=minutes * size_factor,
                    raw=minutes,
                    at=session.ended_at,
                    decay=decay,
                    reconciled=reconciled,
                )

    return edges.all()


def build_directed_edges(
    interactions: Iterable[DirectedInteraction], *, as_of: datetime, params: GraphParams
) -> tuple[list[Edge], dict[str, LayerCoverage]]:
    """Archi dei layer direzionali, piu' le statistiche di copertura."""
    edges = EdgeSet()
    coverage: dict[str, LayerCoverage] = {}

    for interaction in interactions:
        stats = coverage.setdefault(interaction.layer, LayerCoverage())
        stats.total += 1

        if interaction.dst_author_id is None:
            stats.unresolved += 1
            continue

        stats.resolved += 1
        if interaction.dst_author_id == interaction.src_author_id:
            # Rispondersi o reagire a se' stessi non e' una relazione.
            stats.self_loops += 1
            continue

        decay = decay_factor(as_of - interaction.occurred_at, params)
        edges.get(
            interaction.layer, interaction.src_author_id, interaction.dst_author_id
        ).add(
            contribution=1.0,
            raw=1.0,
            at=interaction.occurred_at,
            decay=decay,
        )

    return edges.all(), coverage
