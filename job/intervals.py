"""Da eventi vocali a intervalli di presenza (modello-grafo.md 3.1 e 4).

Primo stadio del calcolo del layer ``voice``: ``voice_join``/``voice_leave``
diventano, per ogni membro e canale, una serie di intervalli ``[inizio, fine)``.

E' il punto in cui vive quasi tutta la casistica silenziosa del modello — join
senza leave, leave senza join, bot spento nel mezzo — quindi e' deliberatamente
una funzione pura: prende eventi e restituisce intervalli, senza toccare il
database. Tutto cio' che serve dal DB (l'attivita' successiva di un membro, i
marcatori di riavvio) entra come argomento.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from .config import GraphParams

logger = logging.getLogger(__name__)

# Un membro non ha attivita' successiva nota: nessun modo di chiudere qui.
NextActivity = Callable[[int, datetime], Optional[datetime]]


@dataclass(frozen=True)
class VoiceEvent:
    """Un ``voice_join`` o ``voice_leave`` letto da ``raw_events``."""

    author_id: int
    channel_id: int
    event_type: str
    occurred_at: datetime
    # L'evento e' stato emesso dalla riconciliazione all'avvio del bot invece
    # che osservato in tempo reale (payload ``reconstructed``).
    is_reconstructed: bool = False
    # Solo per ordinare in modo stabile eventi con lo stesso timestamp: uno
    # spostamento tra canali produce leave e join nello stesso istante.
    event_id: int = 0


@dataclass(frozen=True)
class RestartMarker:
    """Marcatore ``bot_restart`` scritto dall'ingestion all'avvio."""

    restarted_at: datetime
    # Ultimo istante in cui il bot risulta essere stato vivo prima del
    # riavvio. None se ignoto (marcatore scritto senza il campo).
    downtime_start: Optional[datetime] = None


@dataclass(frozen=True)
class PresenceInterval:
    """Presenza continua di un membro in un canale vocale."""

    author_id: int
    channel_id: int
    start: datetime
    end: datetime
    # Almeno un estremo e' stato ricostruito, non osservato.
    is_reconciled: bool = False

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass
class IntervalStats:
    """Contabilita' della ricostruzione, per il log del job.

    Non finisce nel database: e' diagnostica di esecuzione. Se
    ``still_open`` o ``discarded_over_cap`` crescono di snapshot in snapshot,
    il problema e' nell'ingestion, non nel calcolo.
    """

    closed_observed: int = 0
    closed_reconciled: int = 0
    still_open: int = 0
    discarded_over_cap: int = 0
    discarded_non_positive: int = 0
    duplicate_joins: int = 0
    unmatched_leaves: int = 0


def build_intervals(
    events: Iterable[VoiceEvent],
    *,
    params: GraphParams,
    restarts: Iterable[RestartMarker] = (),
    next_activity: Optional[NextActivity] = None,
    join_type: str = "voice_join",
) -> tuple[list[PresenceInterval], IntervalStats]:
    """Ricostruisce gli intervalli di presenza da una sequenza di eventi vocali.

    ``next_activity(author_id, after)`` deve restituire il timestamp del primo
    evento di quel membro successivo a ``after``, o None: e' cio' che consente
    di chiudere una sessione orfana (modello-grafo.md 4.3).
    """
    stats = IntervalStats()
    intervals: list[PresenceInterval] = []

    restart_times = sorted(restarts, key=lambda r: r.restarted_at)
    restart_keys = [r.restarted_at for r in restart_times]

    def emit(
        author_id: int, channel_id: int, start: datetime, end: datetime, reconciled: bool
    ) -> None:
        if end <= start:
            # Puo' succedere quando l'inizio del downtime coincide con il join
            # stesso: la persona era in canale, ma di quel tempo non sappiamo
            # nulla. Zero minuti osservati, non minuti da indovinare.
            stats.discarded_non_positive += 1
            return
        if reconciled and (end - start) > params.orphan_max_duration:
            # Implausibile: scartato, non troncato. Troncare significherebbe
            # produrre una durata verosimile a partire da un dato che non c'e',
            # e quel numero finirebbe indistinguibile da uno misurato.
            stats.discarded_over_cap += 1
            return
        intervals.append(
            PresenceInterval(
                author_id=author_id,
                channel_id=channel_id,
                start=start,
                end=end,
                is_reconciled=reconciled,
            )
        )

    # Un membro puo' essere in un solo canale vocale per volta, ma tenere gli
    # stream separati per canale rende lo spostamento tra canali (leave + join
    # nello stesso istante) un caso senza ambiguita' di ordinamento.
    streams: dict[tuple[int, int], list[VoiceEvent]] = {}
    for event in events:
        streams.setdefault((event.author_id, event.channel_id), []).append(event)

    for (author_id, channel_id), stream in streams.items():
        stream.sort(key=lambda e: (e.occurred_at, e.event_id))

        open_start: Optional[datetime] = None
        open_reconstructed = False

        for event in stream:
            if event.event_type == join_type:
                if open_start is not None:
                    # Due join di fila senza leave: il primo resta valido, il
                    # secondo non aggiunge informazione (non puo' essere
                    # entrato due volte senza uscire).
                    stats.duplicate_joins += 1
                    continue
                open_start = event.occurred_at
                open_reconstructed = event.is_reconstructed
            else:
                if open_start is None:
                    # Leave senza join: tipicamente il join e' anteriore alla
                    # finestra letta. Nessun intervallo da aprire.
                    stats.unmatched_leaves += 1
                    continue
                emit(
                    author_id,
                    channel_id,
                    open_start,
                    event.occurred_at,
                    open_reconstructed or event.is_reconstructed,
                )
                stats.closed_reconciled += (
                    1 if (open_reconstructed or event.is_reconstructed) else 0
                )
                stats.closed_observed += (
                    0 if (open_reconstructed or event.is_reconstructed) else 1
                )
                open_start = None
                open_reconstructed = False

        if open_start is None:
            continue

        # Join rimasto aperto. Due casi diversissimi tra loro, e distinguerli e'
        # esattamente il motivo per cui l'ingestion scrive un marcatore di
        # riavvio (modello-grafo.md 4.4).
        idx = bisect_right(restart_keys, open_start)
        if idx >= len(restart_keys):
            # Nessun riavvio dopo il join: il bot e' rimasto acceso, quindi il
            # leave non e' andato perso — semplicemente non e' ancora
            # avvenuto. La sessione e' ancora in corso: esclusa da questo
            # snapshot, verra' contata in quello successivo. Nessun dato
            # perso e nessun doppio conteggio, perche' ogni snapshot
            # ricalcola tutto da raw_events.
            stats.still_open += 1
            continue

        marker = restart_times[idx]
        candidates = [c for c in (marker.downtime_start, marker.restarted_at) if c is not None]
        if next_activity is not None:
            following = next_activity(author_id, open_start)
            if following is not None:
                candidates.append(following)
        # Il piu' stretto dei confini noti: sia l'inizio del downtime sia il
        # primo evento successivo del membro sono momenti in cui quella
        # sessione o era gia' finita o non e' piu' osservabile. Prendere il
        # piu' tardi gonfierebbe la presenza con tempo mai visto.
        later = [c for c in candidates if c > open_start]
        close_at = min(later) if later else open_start

        emit(author_id, channel_id, open_start, close_at, True)
        stats.closed_reconciled += 1

    intervals.sort(key=lambda i: (i.channel_id, i.start, i.author_id))
    return intervals, stats


def activity_lookup(activity: dict[int, list[datetime]]) -> NextActivity:
    """Costruisce un ``next_activity`` da timestamp per autore gia' ordinati."""

    def lookup(author_id: int, after: datetime) -> Optional[datetime]:
        timestamps = activity.get(author_id)
        if not timestamps:
            return None
        idx = bisect_right(timestamps, after)
        return timestamps[idx] if idx < len(timestamps) else None

    return lookup
