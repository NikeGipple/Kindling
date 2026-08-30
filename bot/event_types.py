"""Tipi di evento canonici scritti in ``raw_events``.

Questo e' il confine netto tra ingestion source-specific e schema canonico
degli eventi (docs/architettura/stack-tecnologico-mvp.md, punto 2 di
"Cosa rende questa architettura davvero future-proof"): un nuovo ingestor,
anche per una fonte diversa da Discord, deve produrre uno di questi tipi (o
estendere questa lista) senza toccare il motore di calcolo del grafo.

``event_type`` in Postgres e' TEXT libero, non un enum a livello di schema:
questa lista e' la fonte di verita' applicativa, non un vincolo DB.
"""

from __future__ import annotations

MESSAGE_CREATE = "message_create"
MESSAGE_REPLY = "message_reply"
REACTION_ADD = "reaction_add"
REACTION_REMOVE = "reaction_remove"
THREAD_CREATE = "thread_create"
VOICE_JOIN = "voice_join"
VOICE_LEAVE = "voice_leave"
EVENT_RSVP_ADD = "event_rsvp_add"
EVENT_RSVP_REMOVE = "event_rsvp_remove"
MEMBER_JOIN = "member_join"
MEMBER_REMOVE = "member_remove"

ALL = frozenset(
    {
        MESSAGE_CREATE,
        MESSAGE_REPLY,
        REACTION_ADD,
        REACTION_REMOVE,
        THREAD_CREATE,
        VOICE_JOIN,
        VOICE_LEAVE,
        EVENT_RSVP_ADD,
        EVENT_RSVP_REMOVE,
        MEMBER_JOIN,
        MEMBER_REMOVE,
    }
)
