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

# Marcatore di riavvio del bot: scritto a ogni ``on_ready``, uno per guild
# collegata. Non e' un'interazione tra membri e non genera archi; serve al job
# di calcolo per distinguere "e' ancora in canale" da "abbiamo perso il
# voice_leave mentre il bot era spento" (modello-grafo.md 4.3/4.4). Senza
# questo marcatore quella distinzione e' impossibile a posteriori.
BOT_RESTART = "bot_restart"

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
        BOT_RESTART,
    }
)

# Chiavi di payload che marcano un evento come *ricostruito* invece che
# osservato in tempo reale (join/leave sintetici emessi dalla riconciliazione
# all'avvio, modello-grafo.md 4.4). Vivono qui e non nel cog perche' il job di
# calcolo deve leggerle per marcare l'arco risultante come ``is_reconciled``:
# sono parte del contratto tra ingestion e calcolo, non un dettaglio del bot.
RECONSTRUCTED_KEY = "reconstructed"
RECONSTRUCTION_REASON_KEY = "reconstruction_reason"

# Valore di RECONSTRUCTION_REASON_KEY per gli eventi emessi dalla
# riconciliazione all'avvio.
REASON_BOT_RESTART = "bot_restart"
