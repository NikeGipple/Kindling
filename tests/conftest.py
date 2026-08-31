"""Fixture sintetiche per i test del job.

Nessun database: la ricostruzione delle sessioni e il calcolo degli archi sono
funzioni pure proprio perche' i loro casi limite (intervalli vicini ma non
sovrapposti, sessioni a cavallo del confine, join senza leave) sono facili da
costruire a mano e impossibili da provocare a comando su dati reali.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

from job.intervals import VoiceEvent

# Un lunedi' sera qualunque, con fuso esplicito: tutto lo schema e' TIMESTAMPTZ.
T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)

VOICE_JOIN = "voice_join"
VOICE_LEAVE = "voice_leave"

_ids = count(1)


def at(*, minutes: float = 0, hours: float = 0, days: float = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, hours=hours, days=days)


def join(author_id: int, channel_id: int, when: datetime, *, reconstructed: bool = False):
    return VoiceEvent(
        author_id=author_id,
        channel_id=channel_id,
        event_type=VOICE_JOIN,
        occurred_at=when,
        is_reconstructed=reconstructed,
        event_id=next(_ids),
    )


def leave(author_id: int, channel_id: int, when: datetime, *, reconstructed: bool = False):
    return VoiceEvent(
        author_id=author_id,
        channel_id=channel_id,
        event_type=VOICE_LEAVE,
        occurred_at=when,
        is_reconstructed=reconstructed,
        event_id=next(_ids),
    )


def presence(author_id: int, channel_id: int, start: datetime, end: datetime):
    """Coppia join/leave per un membro che entra ed esce."""
    return [join(author_id, channel_id, start), leave(author_id, channel_id, end)]
