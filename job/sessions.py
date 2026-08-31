"""Da intervalli di presenza a sessioni vocali (modello-grafo.md 3.2).

Una sessione e', per un dato canale, un gruppo massimale di intervalli di
presenza che si sovrappongono, con episodi separati da meno della finestra di
sessione considerati la stessa sessione.

Attenzione a cosa questo raggruppamento NON dice: appartenere alla stessa
sessione non significa essersi incrociati. Due persone entrate nello stesso
canale a un quarto d'ora di distanza finiscono nella stessa sessione ma possono
non essersi mai viste — il vincolo "co-presenza = sovrapposizione reale, mai
prossimita' temporale di ingresso" e' rispettato in edges.py, dove il peso si
calcola sulla sovrapposizione effettiva coppia per coppia. La sessione serve a
delimitare il contesto e a contare quante persone c'erano, non a dedurre chi ha
parlato con chi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from .config import GraphParams
from .intervals import PresenceInterval

# Classificazione di fondamenta-teoriche.md 7.1. Entrambe generano archi: la
# brace e' un'unita' di bonding valida di per se'.
KIND_EMBER = "brace"
KIND_BONFIRE = "falo"


@dataclass
class VoiceSession:
    guild_id: int
    channel_id: int
    started_at: datetime
    ended_at: datetime
    intervals: list[PresenceInterval] = field(default_factory=list)

    @property
    def participants(self) -> set[int]:
        return {i.author_id for i in self.intervals}

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    @property
    def is_complete(self) -> bool:
        """Nessun intervallo e' stato ricostruito: tutto osservato."""
        return not any(i.is_reconciled for i in self.intervals)

    @property
    def kind(self) -> Optional[str]:
        if self.participant_count >= 3:
            return KIND_BONFIRE
        if self.participant_count == 2:
            return KIND_EMBER
        return None

    @property
    def reconciliation_note(self) -> Optional[str]:
        reconciled = sum(1 for i in self.intervals if i.is_reconciled)
        if reconciled == 0:
            return None
        return (
            f"{reconciled} di {len(self.intervals)} intervalli ricostruiti "
            f"(leave non osservato)"
        )

    def intervals_of(self, author_id: int) -> list[PresenceInterval]:
        return [i for i in self.intervals if i.author_id == author_id]


def build_sessions(
    intervals: Iterable[PresenceInterval], *, guild_id: int, params: GraphParams
) -> list[VoiceSession]:
    """Raggruppa gli intervalli in sessioni, un canale per volta."""
    by_channel: dict[int, list[PresenceInterval]] = {}
    for interval in intervals:
        by_channel.setdefault(interval.channel_id, []).append(interval)

    sessions: list[VoiceSession] = []
    for channel_id, channel_intervals in by_channel.items():
        channel_intervals.sort(key=lambda i: (i.start, i.end, i.author_id))

        current: Optional[VoiceSession] = None
        for interval in channel_intervals:
            if current is not None and (interval.start - current.ended_at) < params.session_window:
                # Meno della finestra di sessione dalla fine dell'ultimo
                # episodio (o sovrapposto, se la differenza e' negativa):
                # stessa sessione. Il confronto e' con la fine PIU' TARDA vista
                # finora, non con quella dell'ultimo intervallo in ordine di
                # inizio: chi e' rimasto a lungo tiene aperta la sessione anche
                # se dopo di lui sono entrati e usciti altri.
                current.intervals.append(interval)
                current.ended_at = max(current.ended_at, interval.end)
                current.started_at = min(current.started_at, interval.start)
                continue

            if current is not None:
                sessions.append(current)
            current = VoiceSession(
                guild_id=guild_id,
                channel_id=channel_id,
                started_at=interval.start,
                ended_at=interval.end,
                intervals=[interval],
            )

        if current is not None:
            sessions.append(current)

    sessions.sort(key=lambda s: (s.channel_id, s.started_at))
    return sessions
