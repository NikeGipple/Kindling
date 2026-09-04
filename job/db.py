"""Tutto il SQL del job, e nient'altro.

Il job legge SOLO Postgres: non chiama mai l'API Discord (modello-grafo.md 6).
Il recupero via API degli autori mancanti e' un comando di backfill lato bot,
separato e opzionale — tenerlo fuori di qui e' cio' che rende il calcolo
riproducibile: rieseguire il job su dati fermi da' sempre lo stesso risultato.

Le letture escludono sempre le righe con ``forgotten_at`` valorizzato: una
richiesta di cancellazione non deve poter rientrare dalla finestra di un
derivato ricalcolato.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable, Optional

import asyncpg

from .cohorts import (
    CohortMember,
    CohortResult,
    PartnerEdge,
    PartnerTracker,
    RetentionResult,
)
from .communities import CommunityResult, CommunitySize
from .config import LAYER_MENTION, LAYER_REACTION, LAYER_REPLY, MetricParams
from .edges import DirectedInteraction, Edge
from .intervals import RestartMarker, VoiceEvent
from .metrics import SnapshotIdentity, snapshot_comparability
from .robustness import RobustnessResult
from .sessions import VoiceSession

logger = logging.getLogger(__name__)

# Tipi di evento, duplicati qui come stringhe invece che importati da bot/:
# il job e' un consumatore dello schema canonico degli eventi, non un pezzo
# dell'ingestion, e non deve dipendere da discord.py per girare.
EVENT_VOICE_JOIN = "voice_join"
EVENT_VOICE_LEAVE = "voice_leave"
EVENT_BOT_RESTART = "bot_restart"
EVENT_MESSAGE_CREATE = "message_create"
EVENT_MESSAGE_REPLY = "message_reply"
EVENT_REACTION_ADD = "reaction_add"


async def connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn=dsn)


# ---- letture -------------------------------------------------------------


async def list_guilds(
    conn: asyncpg.Connection, *, window_start: datetime, window_end: datetime
) -> list[int]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT guild_id
        FROM raw_events
        WHERE occurred_at >= $1 AND occurred_at < $2
        ORDER BY guild_id
        """,
        window_start,
        window_end,
    )
    return [r["guild_id"] for r in rows]


async def fetch_voice_events(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> list[VoiceEvent]:
    """Eventi vocali della guild, con il margine oltre i bordi della finestra.

    Il margine non e' un'ottimizzazione: senza di esso una sessione a cavallo
    del confine dello snapshot verrebbe ricostruita a meta', ed e' proprio
    quello che modello-grafo.md 4.1 vieta ("mai spezzare una sessione a meta'
    per far quadrare la finestra").
    """
    rows = await conn.fetch(
        """
        SELECT id, author_id, channel_id, event_type, occurred_at,
               payload ->> 'reconstructed' AS reconstructed
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($4, $5)
          AND author_id IS NOT NULL
          AND channel_id IS NOT NULL
          AND forgotten_at IS NULL
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY occurred_at, id
        """,
        guild_id,
        since,
        until,
        EVENT_VOICE_JOIN,
        EVENT_VOICE_LEAVE,
    )
    return [
        VoiceEvent(
            author_id=r["author_id"],
            channel_id=r["channel_id"],
            event_type=r["event_type"],
            occurred_at=r["occurred_at"],
            is_reconstructed=r["reconstructed"] == "true",
            event_id=r["id"],
        )
        for r in rows
    ]


async def fetch_restart_markers(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> list[RestartMarker]:
    rows = await conn.fetch(
        """
        SELECT occurred_at,
               payload ->> 'downtime_start' AS downtime_start,
               payload ->> 'voice_confirmed' AS voice_confirmed
        FROM raw_events
        WHERE guild_id = $1
          AND event_type = $4
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY occurred_at
        """,
        guild_id,
        since,
        until,
        EVENT_BOT_RESTART,
    )
    markers = []
    for row in rows:
        downtime_start = None
        if row["downtime_start"]:
            downtime_start = datetime.fromisoformat(row["downtime_start"])
        markers.append(
            RestartMarker(
                restarted_at=row["occurred_at"],
                downtime_start=downtime_start,
                voice_confirmed=_parse_voice_confirmed(row["voice_confirmed"]),
            )
        )
    return markers


def _parse_voice_confirmed(raw: Optional[str]) -> frozenset[tuple[int, int]]:
    """Le presenze che quel riavvio ha visto ancora in corso.

    Chiave assente = marcatore vecchio, scritto prima che l'ingestion la
    registrasse: nessuna conferma, cioe' esattamente il comportamento di
    allora. Nessuna migrazione dei marcatori gia' in produzione.
    """
    if not raw:
        return frozenset()
    try:
        entries = json.loads(raw) or []
    except (TypeError, ValueError):
        logger.warning("voice_confirmed non decodificabile, marcatore trattato senza conferme")
        return frozenset()
    confirmed = set()
    for entry in entries:
        try:
            confirmed.add((int(entry["author_id"]), int(entry["channel_id"])))
        except (KeyError, TypeError, ValueError):
            logger.warning("voce di voice_confirmed malformata, ignorata")
    return frozenset(confirmed)


async def fetch_member_activity(
    conn: asyncpg.Connection, *, guild_id: int, since: datetime, until: datetime
) -> dict[int, list[datetime]]:
    """Timestamp di tutti gli eventi per autore, per chiudere le sessioni orfane.

    "Primo evento successivo dello stesso membro" (modello-grafo.md 4.3) e'
    volutamente qualunque evento, non solo quelli vocali: e' un confine
    superiore su quando quella presenza era ancora osservabile, non una prova
    che la persona fosse uscita dal canale.
    """
    rows = await conn.fetch(
        """
        SELECT author_id, occurred_at
        FROM raw_events
        WHERE guild_id = $1
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
          AND occurred_at >= $2
          AND occurred_at <= $3
        ORDER BY author_id, occurred_at
        """,
        guild_id,
        since,
        until,
    )
    activity: dict[int, list[datetime]] = {}
    for row in rows:
        activity.setdefault(row["author_id"], []).append(row["occurred_at"])
    return activity


async def refresh_message_authors(conn: asyncpg.Connection, *, guild_id: int) -> int:
    """Ricostruisce ``message_authors`` da raw_events.

    Su tutto lo storico e non solo sulla finestra: una reply o una reazione di
    questa settimana puo' benissimo puntare a un messaggio di mesi fa, e senza
    il suo autore quell'interazione non genera un arco.
    """
    result = await conn.execute(
        """
        INSERT INTO message_authors (guild_id, message_id, author_id)
        SELECT DISTINCT guild_id, message_id, author_id
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($2, $3)
          AND message_id IS NOT NULL
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
        ON CONFLICT (guild_id, message_id) DO NOTHING
        """,
        guild_id,
        EVENT_MESSAGE_CREATE,
        EVENT_MESSAGE_REPLY,
    )
    return int(result.rsplit(" ", 1)[-1]) if result.startswith("INSERT") else 0


async def fetch_directed_interactions(
    conn: asyncpg.Connection, *, guild_id: int, window_start: datetime, window_end: datetime
) -> list[DirectedInteraction]:
    """Reply, menzioni e reazioni della finestra, con il bersaglio gia' risolto.

    La risoluzione e' un LEFT JOIN su message_authors per ``message_id``: gli
    id dei messaggi Discord sono globalmente univoci, quindi il canale non
    serve a risolvere l'autore. ``referenced_channel_id`` serve al backfill via
    API lato bot (``channel.fetch_message`` vuole il canale giusto), non qui.
    Un target non risolto resta ``None`` e viene contato nella copertura.
    """
    interactions: list[DirectedInteraction] = []

    reply_rows = await conn.fetch(
        """
        SELECT e.author_id, e.occurred_at, ma.author_id AS target_author_id
        FROM raw_events e
        LEFT JOIN message_authors ma
               ON ma.guild_id = e.guild_id
              AND ma.message_id = e.referenced_message_id
        WHERE e.guild_id = $1
          AND e.event_type = $4
          AND e.author_id IS NOT NULL
          AND e.referenced_message_id IS NOT NULL
          AND e.forgotten_at IS NULL
          AND e.occurred_at >= $2
          AND e.occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_MESSAGE_REPLY,
    )
    interactions.extend(
        DirectedInteraction(
            layer=LAYER_REPLY,
            src_author_id=r["author_id"],
            dst_author_id=r["target_author_id"],
            occurred_at=r["occurred_at"],
        )
        for r in reply_rows
    )

    reaction_rows = await conn.fetch(
        """
        SELECT e.author_id, e.occurred_at, ma.author_id AS target_author_id
        FROM raw_events e
        LEFT JOIN message_authors ma
               ON ma.guild_id = e.guild_id
              AND ma.message_id = e.message_id
        WHERE e.guild_id = $1
          AND e.event_type = $4
          AND e.author_id IS NOT NULL
          AND e.message_id IS NOT NULL
          AND e.forgotten_at IS NULL
          AND e.occurred_at >= $2
          AND e.occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_REACTION_ADD,
    )
    interactions.extend(
        DirectedInteraction(
            layer=LAYER_REACTION,
            src_author_id=r["author_id"],
            dst_author_id=r["target_author_id"],
            occurred_at=r["occurred_at"],
        )
        for r in reaction_rows
    )

    # Le menzioni non hanno il problema della risoluzione: payload.mention_ids
    # contiene gia' gli id dei destinatari.
    mention_rows = await conn.fetch(
        """
        SELECT author_id, occurred_at, payload ->> 'mention_ids' AS mention_ids
        FROM raw_events
        WHERE guild_id = $1
          AND event_type IN ($4, $5)
          AND author_id IS NOT NULL
          AND forgotten_at IS NULL
          AND payload ->> 'mention_ids' IS NOT NULL
          AND occurred_at >= $2
          AND occurred_at < $3
        """,
        guild_id,
        window_start,
        window_end,
        EVENT_MESSAGE_CREATE,
        EVENT_MESSAGE_REPLY,
    )
    for row in mention_rows:
        try:
            mention_ids = json.loads(row["mention_ids"]) or []
        except (TypeError, ValueError):
            logger.warning("mention_ids non decodificabile, riga ignorata")
            continue
        for target in mention_ids:
            interactions.append(
                DirectedInteraction(
                    layer=LAYER_MENTION,
                    src_author_id=row["author_id"],
                    dst_author_id=int(target),
                    occurred_at=row["occurred_at"],
                )
            )

    return interactions


# ---- scritture -----------------------------------------------------------


async def write_snapshot(
    conn: asyncpg.Connection,
    *,
    guild_id: int,
    as_of: datetime,
    window_start: datetime,
    window_end: datetime,
    params: dict[str, Any],
    stats: dict[str, Any],
    code_version: Optional[str],
    edges: Iterable[Edge],
    sessions: Iterable[VoiceSession],
) -> int:
    """Scrive snapshot, archi e sessioni in un'unica transazione.

    Idempotente: sullo stesso ``(guild_id, as_of, finestra)`` riscrive lo
    snapshot esistente invece di affiancargliene un altro. Un job rilanciato
    dopo un fallimento a meta' non deve lasciare mezzo grafo in tabella.
    """
    async with conn.transaction():
        snapshot_id = await conn.fetchval(
            """
            INSERT INTO graph_snapshots
                (guild_id, as_of, window_start, window_end, params, stats, code_version)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
            ON CONFLICT (guild_id, as_of, window_start, window_end) DO UPDATE
                SET params = EXCLUDED.params,
                    stats = EXCLUDED.stats,
                    code_version = EXCLUDED.code_version,
                    created_at = now()
            RETURNING id
            """,
            guild_id,
            as_of,
            window_start,
            window_end,
            json.dumps(params, default=str),
            json.dumps(stats, default=str),
            code_version,
        )

        # Gli archi vengono riscritti per intero: un ricalcolo con parametri
        # diversi puo' far sparire un arco (sotto soglia), e un UPSERT
        # lascerebbe in tabella il fantasma di quello vecchio.
        await conn.execute("DELETE FROM graph_edges WHERE snapshot_id = $1", snapshot_id)
        await conn.executemany(
            """
            INSERT INTO graph_edges (
                snapshot_id, layer, src_author_id, dst_author_id,
                weight, weight_undecayed, raw_units,
                interaction_count, last_interaction_at, is_reconciled
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            [
                (
                    snapshot_id,
                    e.layer,
                    e.src_author_id,
                    e.dst_author_id,
                    e.weight,
                    e.weight_undecayed,
                    e.raw_units,
                    e.interaction_count,
                    e.last_interaction_at,
                    e.is_reconciled,
                )
                for e in edges
            ],
        )

        for session in sessions:
            session_id = await conn.fetchval(
                """
                INSERT INTO voice_sessions (
                    guild_id, channel_id, started_at, ended_at,
                    participant_count, is_complete, reconciliation_note
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (guild_id, channel_id, started_at) DO UPDATE
                    SET ended_at = EXCLUDED.ended_at,
                        participant_count = EXCLUDED.participant_count,
                        is_complete = EXCLUDED.is_complete,
                        reconciliation_note = EXCLUDED.reconciliation_note
                RETURNING id
                """,
                session.guild_id,
                session.channel_id,
                session.started_at,
                session.ended_at,
                session.participant_count,
                session.is_complete,
                session.reconciliation_note,
            )
            await conn.execute(
                "DELETE FROM voice_session_participants WHERE session_id = $1", session_id
            )
            await conn.executemany(
                """
                INSERT INTO voice_session_participants
                    (session_id, author_id, joined_at, left_at, is_reconciled)
                VALUES ($1, $2, $3, $4, $5)
                """,
                [
                    (session_id, i.author_id, i.start, i.end, i.is_reconciled)
                    for i in session.intervals
                ],
            )

    return snapshot_id


# ---- letture per l'export -------------------------------------------------


async def fetch_snapshot(
    conn: asyncpg.Connection, *, snapshot_id: Optional[int] = None, guild_id: Optional[int] = None
) -> Optional[asyncpg.Record]:
    if snapshot_id is not None:
        return await conn.fetchrow(
            "SELECT * FROM graph_snapshots WHERE id = $1", snapshot_id
        )
    return await conn.fetchrow(
        """
        SELECT * FROM graph_snapshots
        WHERE ($1::bigint IS NULL OR guild_id = $1)
        ORDER BY as_of DESC, id DESC
        LIMIT 1
        """,
        guild_id,
    )


async def fetch_snapshot_edges(
    conn: asyncpg.Connection, *, snapshot_id: int
) -> list[Edge]:
    rows = await conn.fetch(
        """
        SELECT layer, src_author_id, dst_author_id, weight, weight_undecayed,
               raw_units, interaction_count, last_interaction_at, is_reconciled
        FROM graph_edges
        WHERE snapshot_id = $1
        ORDER BY layer, src_author_id, dst_author_id
        """,
        snapshot_id,
    )
    return [
        Edge(
            layer=r["layer"],
            src_author_id=r["src_author_id"],
            dst_author_id=r["dst_author_id"],
            weight=r["weight"],
            weight_undecayed=r["weight_undecayed"],
            raw_units=r["raw_units"],
            interaction_count=r["interaction_count"],
            last_interaction_at=r["last_interaction_at"],
            is_reconciled=r["is_reconciled"],
        )
        for r in rows
    ]


# ---- letture per il layer di metriche -------------------------------------


async def list_snapshot_guilds(conn: asyncpg.Connection) -> list[int]:
    """Le guild che hanno almeno uno snapshot del grafo.

    Serve al sottocomando ``metrics`` per lavorare su tutte le community, come
    fa ``snapshot``: non e' list_guilds, che guarda raw_events in una finestra —
    qui la domanda e' "di chi esiste un grafo gia' calcolato".
    """
    rows = await conn.fetch(
        "SELECT DISTINCT guild_id FROM graph_snapshots ORDER BY guild_id"
    )
    return [r["guild_id"] for r in rows]


async def fetch_previous_snapshot(
    conn: asyncpg.Connection, *, snapshot: asyncpg.Record
) -> tuple[Optional[asyncpg.Record], Optional[str]]:
    """Lo snapshot immediatamente precedente della stessa guild, se confrontabile.

    Ritorna anche il motivo per cui non lo e', da salvare in ``details``: uno
    snapshot precedente "vicino ma non identico" nei parametri non viene
    adattato ne' riscalato, perche' la differenza tra le due partizioni
    conterrebbe il cambio di parametri e attribuirla alla community sarebbe
    falso (modello-metriche.md 4.6).
    """
    row = await conn.fetchrow(
        """
        SELECT * FROM graph_snapshots
        WHERE guild_id = $1
          AND as_of < $2
        ORDER BY as_of DESC, id DESC
        LIMIT 1
        """,
        snapshot["guild_id"],
        snapshot["as_of"],
    )
    if row is None:
        return None, "no_previous_snapshot"
    reason = snapshot_comparability(identity_of(snapshot), identity_of(row))
    if reason is not None:
        return None, f"previous_snapshot_{reason}"
    return row, None


def identity_of(snapshot: asyncpg.Record) -> SnapshotIdentity:
    """Cio' che rende due snapshot confrontabili: parametri E ampiezza finestra.

    L'ampiezza non e' un parametro e non sta in ``params``, ma due finestre
    diverse danno grafi di densita' diversa — e non e' un caso teorico: il primo
    snapshot copre ~3 giorni e i successivi 7.
    """
    return SnapshotIdentity(
        params=json.loads(snapshot["params"]),
        window=snapshot["window_end"] - snapshot["window_start"],
    )


async def fetch_observability_anchor(
    conn: asyncpg.Connection, *, guild_id: int
) -> Optional[datetime]:
    """L'istante da cui le uscite dei membri sono osservabili.

    ``members`` non e' un log: il backfill la popola con chi e' presente al
    momento in cui Kindling arriva sul server, quindi chi era entrato e gia'
    uscito prima non ha lasciato traccia. Prima di questo istante ogni coorte e'
    fatta per costruzione dai soli sopravvissuti (modello-metriche.md 5.5).

    L'ancora e' ``guilds.first_seen_at``: un dato **dichiarato dal bot**, che
    quel momento lo conosce, invece di un ``MIN(occurred_at)`` dedotto da
    ``raw_events`` — una tabella che puo' contenere altro, e il cui minimo si
    sposta per ragioni che non hanno niente a che vedere con l'osservabilita'
    (un evento cancellato per diritto all'oblio, una riga importata).

    ``None`` se la guild non e' registrata: a valle significa "coorte trattata
    come anteriore all'osservazione", che e' il verso prudente. Una guild senza
    riga in ``guilds`` e' una guild su cui il bot non ha ancora girato dopo
    l'introduzione della tabella — vedi il passo di migrazione dati nel runbook.
    """
    return await conn.fetchval(
        "SELECT first_seen_at FROM guilds WHERE guild_id = $1",
        guild_id,
    )


async def fetch_cohort_members(
    conn: asyncpg.Connection, *, guild_id: int, as_of: datetime, since: datetime
) -> list[CohortMember]:
    """Membri entrati da ``since`` in poi, con il flag di rientro sospetto.

    ``has_prior_activity`` viene da un EXISTS su raw_events: se una persona ha
    attivita' in questa guild anteriore al proprio ``joined_at``, o e' un
    rientro o e' un dato incoerente — e ``members`` sovrascrive
    ``joined_at``/``left_at`` sui rientri, quindi senza questo controllo un
    rientrante sarebbe indistinguibile da un nuovo arrivato che raggiunge k
    connessioni in un giorno grazie a relazioni che aveva gia'.

    Nessun falso positivo dai membri caricati con ``!backfill_members``: quel
    comando scrive la data di join reale, che precede la loro attivita'.
    """
    rows = await conn.fetch(
        """
        SELECT m.author_id,
               m.joined_at,
               m.left_at,
               EXISTS (
                   SELECT 1
                   FROM raw_events e
                   WHERE e.guild_id = m.guild_id
                     AND e.author_id = m.author_id
                     AND e.forgotten_at IS NULL
                     AND e.occurred_at < m.joined_at
               ) AS has_prior_activity
        FROM members m
        WHERE m.guild_id = $1
          AND m.forgotten_at IS NULL
          AND m.joined_at >= $2
          AND m.joined_at <= $3
        ORDER BY m.joined_at, m.author_id
        """,
        guild_id,
        since,
        as_of,
    )
    return [
        CohortMember(
            author_id=r["author_id"],
            joined_at=r["joined_at"],
            left_at=r["left_at"],
            has_prior_activity=r["has_prior_activity"],
        )
        for r in rows
    ]


async def fetch_comparable_snapshots(
    conn: asyncpg.Connection,
    *,
    guild_id: int,
    reference: SnapshotIdentity,
    since: datetime,
    until: datetime,
) -> tuple[list[asyncpg.Record], int]:
    """Serie di snapshot confrontabili con quello di riferimento, per as_of.

    Stessa regola di ``fetch_previous_snapshot``, e deve esserlo: parametri
    identici e stessa ampiezza di finestra. Gli altri non vengono mescolati —
    sono un buco nella serie, cioe' censura intervallare, e il loro numero va in
    diagnostica invece di sparire: due coorti sono confrontabili solo se la
    serie di snapshot sotto di loro ha la stessa cadenza.
    """
    rows = await conn.fetch(
        """
        SELECT id, as_of, params, window_start, window_end
        FROM graph_snapshots
        WHERE guild_id = $1
          AND as_of >= $2
          AND as_of <= $3
        ORDER BY as_of, id
        """,
        guild_id,
        since,
        until,
    )
    comparable = [
        row for row in rows if snapshot_comparability(reference, identity_of(row)) is None
    ]
    return comparable, len(rows) - len(comparable)


async def scan_partner_growth(
    conn: asyncpg.Connection,
    *,
    snapshots: Iterable[asyncpg.Record],
    members: Iterable[CohortMember],
    params: MetricParams,
) -> tuple[dict[str, dict[int, datetime]], dict[str, Any]]:
    """Istante in cui ogni membro raggiunge k partner distinti, per ambito.

    Il vincolo di memoria di modello-metriche.md 11.1 e' implementato qui, ed e'
    vincolante e non un suggerimento: si legge **uno snapshot alla volta**, si
    filtrano gli archi a quelli incidenti ai membri delle coorti ancora aperte,
    e si accumulano insiemi di partner — mai archi. Con 180 giorni di storico
    sono ~26 snapshot, e caricarne gli archi tutti insieme e' il modo di far
    esplodere il picco di memoria del job sulla droplet.

    Questa funzione fa solo il SQL: cosa conta come partner e quando l'evento e'
    avvenuto lo decide ``PartnerTracker``, che e' logica e sta in cohorts.py.
    """
    tracker = PartnerTracker(members, params=params)

    for snapshot in snapshots:
        pending = tracker.pending(snapshot["as_of"])
        if not pending:
            continue
        # Nessun filtro sul peso qui: l'ammissione somma i due orientamenti
        # PRIMA di applicare la soglia, quindi filtrare riga per riga
        # escluderebbe coppie che la regola condivisa ammette. Il SQL restringe
        # solo ai membri ancora aperti — il vincolo di memoria — e la regola la
        # applica PartnerTracker.
        rows = await conn.fetch(
            """
            SELECT layer, src_author_id, dst_author_id, weight, interaction_count
            FROM graph_edges
            WHERE snapshot_id = $1
              AND (src_author_id = ANY($2::bigint[]) OR dst_author_id = ANY($2::bigint[]))
            """,
            snapshot["id"],
            pending,
        )
        tracker.observe(
            snapshot["as_of"],
            (
                PartnerEdge(
                    layer=r["layer"],
                    src_author_id=r["src_author_id"],
                    dst_author_id=r["dst_author_id"],
                    weight=r["weight"],
                    interaction_count=r["interaction_count"],
                )
                for r in rows
            ),
        )

    return tracker.reached, {"snapshots_used": tracker.snapshots_used}


async def fetch_snapshot_by_id(
    conn: asyncpg.Connection, *, snapshot_id: int
) -> Optional[asyncpg.Record]:
    return await conn.fetchrow("SELECT * FROM graph_snapshots WHERE id = $1", snapshot_id)


# ---- scritture del layer di metriche --------------------------------------


async def write_metrics(
    conn: asyncpg.Connection,
    *,
    snapshot_id: int,
    guild_id: int,
    as_of: datetime,
    params: dict[str, Any],
    stats: dict[str, Any],
    code_version: Optional[str],
    robustness: Iterable[RobustnessResult],
    communities: Iterable[CommunityResult],
    community_sizes: Iterable[CommunitySize],
    cohorts: Iterable[CohortResult],
    cohort_retention: Iterable[RetentionResult],
) -> None:
    """Scrive tutte le tabelle di metrica in un'unica transazione.

    DELETE + INSERT e non UPSERT riga per riga, come per ``graph_edges`` e per
    la stessa ragione: un ricalcolo con ``k`` o ``X`` diversi puo' far SPARIRE
    righe, e un UPSERT lascerebbe in tabella il fantasma di quelle vecchie —
    con l'aggravante che sarebbero indistinguibili dalle nuove.
    """
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO metric_runs
                (snapshot_id, guild_id, as_of, params, stats, code_version)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            ON CONFLICT (snapshot_id) DO UPDATE
                SET params = EXCLUDED.params,
                    stats = EXCLUDED.stats,
                    code_version = EXCLUDED.code_version,
                    created_at = now()
            """,
            snapshot_id,
            guild_id,
            as_of,
            json.dumps(params, default=str),
            json.dumps(stats, default=str),
            code_version,
        )

        for table in (
            "metric_robustness",
            "metric_communities",
            "metric_community_sizes",
            "metric_cohorts",
            "metric_cohort_retention",
        ):
            await conn.execute(
                "DELETE FROM " + table + " WHERE snapshot_id = $1", snapshot_id
            )

        await conn.executemany(
            """
            INSERT INTO metric_robustness (
                snapshot_id, layer, removal_fraction,
                n_effective, nodes_removed, giant_before, giant_after_targeted,
                components_after_targeted, giant_after_random_mean,
                giant_after_random_sd, components_after_random_mean,
                targeted_excess, targeted_z,
                is_suppressed, suppression_reason, is_significant, details
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17::jsonb)
            """,
            [
                (
                    snapshot_id,
                    r.layer,
                    r.removal_fraction,
                    r.n_effective,
                    r.nodes_removed,
                    r.giant_before,
                    r.giant_after_targeted,
                    r.components_after_targeted,
                    r.giant_after_random_mean,
                    r.giant_after_random_sd,
                    r.components_after_random_mean,
                    r.targeted_excess,
                    r.targeted_z,
                    r.is_suppressed,
                    r.suppression_reason,
                    r.is_significant,
                    json.dumps(r.details, default=str),
                )
                for r in robustness
            ],
        )

        await conn.executemany(
            """
            INSERT INTO metric_communities (
                snapshot_id, layer,
                n_effective, community_count, modularity,
                modularity_random_mean, modularity_random_sd, modularity_z,
                previous_snapshot_id, node_overlap, stability_jaccard,
                communities_born, communities_dissolved,
                communities_merged, communities_split,
                is_suppressed, suppression_reason, is_significant, details
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17, $18, $19::jsonb)
            """,
            [
                (
                    snapshot_id,
                    c.layer,
                    c.n_effective,
                    c.community_count,
                    c.modularity,
                    c.modularity_random_mean,
                    c.modularity_random_sd,
                    c.modularity_z,
                    c.previous_snapshot_id,
                    c.node_overlap,
                    c.stability_jaccard,
                    c.communities_born,
                    c.communities_dissolved,
                    c.communities_merged,
                    c.communities_split,
                    c.is_suppressed,
                    c.suppression_reason,
                    c.is_significant,
                    json.dumps(c.details, default=str),
                )
                for c in communities
            ],
        )

        await conn.executemany(
            """
            INSERT INTO metric_community_sizes (
                snapshot_id, layer, bucket,
                community_count, member_count,
                is_suppressed, suppression_reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [
                (
                    snapshot_id,
                    s.layer,
                    s.bucket,
                    s.community_count,
                    s.member_count,
                    s.is_suppressed,
                    s.suppression_reason,
                )
                for s in community_sizes
            ],
        )

        await conn.executemany(
            """
            INSERT INTO metric_cohorts (
                snapshot_id, cohort_start, layer_scope, k,
                n_effective, observation_days, is_mature,
                event_count, censored_count, censored_by_leave,
                median_days_to_k, median_reached, p25_days_to_k, p75_days_to_k,
                reached_by_14d, reached_by_28d, excluded_rejoins,
                is_survivors_only, has_snapshot_coverage,
                is_suppressed, suppression_reason, is_significant, details
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17, $18, $19, $20, $21, $22, $23::jsonb)
            """,
            [
                (
                    snapshot_id,
                    c.cohort_start,
                    c.layer_scope,
                    c.k,
                    c.n_effective,
                    c.observation_days,
                    c.is_mature,
                    c.event_count,
                    c.censored_count,
                    c.censored_by_leave,
                    c.median_days_to_k,
                    c.median_reached,
                    c.p25_days_to_k,
                    c.p75_days_to_k,
                    c.reached_by_14d,
                    c.reached_by_28d,
                    c.excluded_rejoins,
                    c.is_survivors_only,
                    c.has_snapshot_coverage,
                    c.is_suppressed,
                    c.suppression_reason,
                    c.is_significant,
                    json.dumps(c.details, default=str),
                )
                for c in cohorts
            ],
        )

        await conn.executemany(
            """
            INSERT INTO metric_cohort_retention (
                snapshot_id, cohort_start, horizon_days,
                n_effective, excluded_rejoins, is_survivors_only,
                retained_fraction, is_computable, not_computable_reason,
                is_suppressed, suppression_reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [
                (
                    snapshot_id,
                    r.cohort_start,
                    r.horizon_days,
                    r.n_effective,
                    r.excluded_rejoins,
                    r.is_survivors_only,
                    r.retained_fraction,
                    r.is_computable,
                    r.not_computable_reason,
                    r.is_suppressed,
                    r.suppression_reason,
                )
                for r in cohort_retention
            ],
        )
