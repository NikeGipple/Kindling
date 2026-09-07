"""Entrypoint del job di calcolo del grafo.

Uso:
    python -m job.main snapshot [--guild-id N] [--window-days 7] [--as-of ISO]
    python -m job.main export --snapshot-id N [--out-dir exports] [--identified]
    python -m job.main metrics [--snapshot-id N | --guild-id N] [--dry-run]

Il job non e' un servizio: parte, calcola, scrive e muore. Nessun processo
sempre acceso in piu' sulla droplet oltre a bot e Postgres.

Senza --as-of, lo snapshot e' quello della settimana ISO corrente e non
dell'istante di esecuzione: as_of e' il lunedi' 00:00 UTC (modello-grafo.md
5.1), quindi due esecuzioni nella stessa settimana riscrivono la stessa riga
invece di affiancarne una nuova.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from . import db
from .cohorts import cohort_start_of, series_spacing
from .communities import partition_of
from .config import (
    ALL_LAYERS,
    DEFAULT_METRIC_PARAMS,
    DEFAULT_PARAMS,
    GraphParams,
    MetricParams,
)
from .graph import build_layer_graph, build_metric_graph, write_graphml
from .intervals import activity_lookup
from .metrics import PreviousPartition, build_metrics, cohort_window_start
from .pseudonyms import load_salt, pseudonymize
from .snapshot import build_snapshot
from .suppression import as_public_dict

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = Path("exports")


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # Un istante senza fuso e' ambiguo, e tutto lo schema e' TIMESTAMPTZ:
        # meglio assumere UTC in modo esplicito che lasciarlo decidere alla
        # macchina su cui il job gira.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _week_aligned_now() -> datetime:
    """L'as_of di default: il lunedi' 00:00 UTC della settimana ISO corrente.

    Non l'istante di esecuzione (modello-grafo.md 5.1). Preso da now(), as_of
    non si ripete mai al microsecondo, quindi la chiave
    (guild_id, as_of, window_start, window_end) e' sempre nuova e l'ON CONFLICT
    di write_snapshot non viene MAI raggiunto: l'idempotenza promessa dal
    runbook e dall'intestazione di ops/kindling-weekly.sh era falsa, e ogni
    lancio a mano lasciava in tabella una riga in piu'.

    L'ancora e' cohort_start_of, la STESSA funzione che definisce le coorti, e
    non un calcolo del lunedi' riscritto qui: e' questo che fa coincidere i
    confini delle finestre con quelli delle coorti invece di sfalsarli, e due
    implementazioni separate della stessa nozione divergono al primo che ne
    tocca una.

    Nessun flag per disattivare l'allineamento: --as-of e' gia' la via di fuga,
    e ha il pregio di costringere a dichiarare l'istante invece di ereditare
    quello dell'orologio.
    """
    return datetime.combine(
        cohort_start_of(datetime.now(timezone.utc)), time.min, tzinfo=timezone.utc
    )


def _code_version() -> Optional[str]:
    """Versione del codice che ha prodotto lo snapshot.

    Nel container il repository git non c'e' (vedi .dockerignore), quindi la
    variabile d'ambiente e' la via normale e git il ripiego per chi lancia il
    job dal proprio checkout.
    """
    from_env = os.environ.get("KINDLING_CODE_VERSION")
    if from_env:
        return from_env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL non impostata. Vedi .env.example.")
    return url


async def run_snapshot(args: argparse.Namespace, params: GraphParams) -> None:
    as_of = _parse_instant(args.as_of) if args.as_of else _week_aligned_now()
    window_end = _parse_instant(args.window_end) if args.window_end else as_of
    window_start = (
        _parse_instant(args.window_start)
        if args.window_start
        else window_end - timedelta(days=args.window_days)
    )
    if window_start >= window_end:
        raise RuntimeError("Finestra vuota: window_start deve precedere window_end.")

    # Il margine di lettura oltre l'inizio della finestra serve a ricostruire
    # intera una sessione a cavallo del confine; verso il futuro il confine e'
    # as_of, che con l'ancoraggio alla settimana e' nel passato (lunedi' 00:00
    # UTC, mentre il cron gira alle 04:15). Non e' un buco: una sessione che
    # finisce in quelle ore resta "ancora aperta" per questo snapshot ed entra
    # nel prossimo — regola 4.2 di modello-grafo.md — dove viene riletta intera
    # grazie al lookback margin, che dal lunedi' seguente arriva alla domenica.
    read_since = window_start - params.lookback_margin
    read_until = max(as_of, window_end)

    conn = await db.connect(_database_url())
    try:
        guild_ids = (
            [args.guild_id]
            if args.guild_id
            else await db.list_guilds(
                conn, window_start=window_start, window_end=window_end
            )
        )
        if not guild_ids:
            logger.warning("Nessuna guild con eventi nella finestra: niente da calcolare.")
            return

        for guild_id in guild_ids:
            inserted = await db.refresh_message_authors(conn, guild_id=guild_id)
            logger.info(
                "guild_id=%s: message_authors aggiornata (%d nuove righe)", guild_id, inserted
            )

            voice_events = await db.fetch_voice_events(
                conn, guild_id=guild_id, since=read_since, until=read_until
            )
            restarts = await db.fetch_restart_markers(
                conn, guild_id=guild_id, since=read_since, until=read_until
            )
            activity = await db.fetch_member_activity(
                conn, guild_id=guild_id, since=read_since, until=read_until
            )
            interactions = await db.fetch_directed_interactions(
                conn, guild_id=guild_id, window_start=window_start, window_end=window_end
            )

            result = build_snapshot(
                guild_id=guild_id,
                as_of=as_of,
                window_start=window_start,
                window_end=window_end,
                params=params,
                voice_events=voice_events,
                restarts=restarts,
                next_activity=activity_lookup(activity),
                interactions=interactions,
            )

            stats = result.interval_stats
            # Tutti e sette i contatori, non solo i quattro "buoni": gli
            # scarti silenziosi (join duplicati, leave spaiati, durate non
            # positive) sono proprio quelli che nessuno andrebbe a cercare, e
            # che segnalano un problema di ingestion prima che diventi un peso
            # sbagliato in tabella.
            logger.info(
                "guild_id=%s: %d sessioni in finestra (%d fuori finestra), "
                "intervalli: %d osservati, %d ricostruiti, %d ancora aperti, "
                "%d scartati oltre il tetto, %d scartati di durata non positiva, "
                "%d join duplicati, %d leave senza join",
                guild_id,
                len(result.sessions),
                result.sessions_out_of_window,
                stats.closed_observed,
                stats.closed_reconciled,
                stats.still_open,
                stats.discarded_over_cap,
                stats.discarded_non_positive,
                stats.duplicate_joins,
                stats.unmatched_leaves,
            )
            for layer, coverage in sorted(result.coverage.items()):
                logger.info(
                    "guild_id=%s layer=%s: %d interazioni, %d risolte, %d senza autore "
                    "noto (nessun arco), %d self-loop scartati",
                    guild_id,
                    layer,
                    coverage.total,
                    coverage.resolved,
                    coverage.unresolved,
                    coverage.self_loops,
                )
            for layer in ALL_LAYERS:
                count = sum(1 for e in result.edges if e.layer == layer)
                logger.info("guild_id=%s layer=%s: %d archi", guild_id, layer, count)

            if args.dry_run:
                logger.info("--dry-run: nessuna scrittura su Postgres.")
                continue

            snapshot_id = await db.write_snapshot(
                conn,
                guild_id=guild_id,
                as_of=as_of,
                window_start=window_start,
                window_end=window_end,
                params=params.as_snapshot_params(),
                stats=result.as_snapshot_stats(),
                code_version=_code_version(),
                edges=result.edges,
                sessions=result.sessions,
            )
            logger.info("guild_id=%s: snapshot %d scritto.", guild_id, snapshot_id)
    finally:
        await conn.close()


async def run_export(args: argparse.Namespace) -> None:
    """Export GraphML di uno snapshot, un file per layer.

    Pseudonimo di default: un export identificato e' una mappa sociale
    nominativa della community, e non deve poter uscire da una macchina per
    distrazione. --identified lo produce comunque, ma va chiesto.
    """
    if args.identified:
        logger.warning(
            "Export IDENTIFICATO: i file conterranno id Discord reali. Non "
            "committarli e non copiarli in cartelle condivise."
        )

        def label(author_id: int) -> str:
            return str(author_id)
    else:
        key = load_salt()

        def label(author_id: int) -> str:
            return pseudonymize(author_id, key)

    conn = await db.connect(_database_url())
    try:
        snapshot = await db.fetch_snapshot(
            conn, snapshot_id=args.snapshot_id, guild_id=args.guild_id
        )
        if snapshot is None:
            raise RuntimeError("Nessuno snapshot trovato con questi criteri.")

        edges = await db.fetch_snapshot_edges(conn, snapshot_id=snapshot["id"])
        out_dir = Path(args.out_dir)
        written = []
        for layer in ALL_LAYERS:
            graph = build_layer_graph(edges, layer=layer, label=label)
            if graph.vcount() == 0:
                continue
            # Un file per layer, mai un file unico: i layer non si fondono,
            # nemmeno per comodita' di ispezione in Gephi.
            path = out_dir / f"snapshot_{snapshot['id']}_{layer}.graphml"
            write_graphml(graph, path)
            written.append((path, graph.vcount(), graph.ecount()))

        if not written:
            logger.warning("Snapshot %s: nessun arco da esportare.", snapshot["id"])
        for path, vcount, ecount in written:
            logger.info("Scritto %s (%d nodi, %d archi)", path, vcount, ecount)
    finally:
        await conn.close()


async def run_metrics(args: argparse.Namespace, params: MetricParams) -> None:
    """Calcola le metriche aggregate di snapshot gia' scritti.

    Legge uno snapshot invece di ricalcolare il grafo: permette di ricalcolare
    le metriche con parametri nuovi senza rifare la ricostruzione delle
    sessioni, che e' la parte costosa e delicata, e rende la riesecuzione del
    layer metriche un'operazione senza conseguenze sul grafo.

    Senza argomenti lavora su **tutte** le guild, una per una, come fa
    ``snapshot``: due sottocomandi dello stesso job non possono avere contratti
    diversi su cosa significa "senza argomenti", perche' e' il tipo di
    asimmetria che nessuno si ricorda e che si scopre quando arriva la seconda
    community — lo scenario di crescita dichiarato in architettura.md.
    """
    conn = await db.connect(_database_url())
    try:
        if args.snapshot_id is not None:
            snapshot = await db.fetch_snapshot_by_id(conn, snapshot_id=args.snapshot_id)
            if snapshot is None:
                raise RuntimeError(f"Nessuno snapshot con id {args.snapshot_id}.")
            snapshots = [snapshot]
        else:
            guild_ids = (
                [args.guild_id]
                if args.guild_id is not None
                else await db.list_snapshot_guilds(conn)
            )
            snapshots = []
            for guild_id in guild_ids:
                snapshot = await db.fetch_snapshot(conn, guild_id=guild_id)
                if snapshot is None:
                    logger.warning("guild_id=%s: nessuno snapshot, saltata.", guild_id)
                    continue
                snapshots.append(snapshot)

        if not snapshots:
            logger.warning("Nessuno snapshot su cui calcolare le metriche.")
            return

        for snapshot in snapshots:
            await _metrics_for_snapshot(conn, snapshot, params=params, args=args)
    finally:
        await conn.close()


async def _previous_partition(
    conn, snapshot, *, params: MetricParams
) -> tuple[Optional[PreviousPartition], Optional[str]]:
    """Ricostruisce la partizione dello snapshot precedente, se confrontabile.

    Ricostruita e non letta: l'appartenenza alla community e' un dato per-nodo e
    non e' salvata da nessuna parte, ed e' il seed fissato a rendere esatta la
    ricostruzione (modello-metriche.md 4.2, 8).

    Sta in una funzione a se' anche per una ragione di memoria: gli archi dello
    snapshot precedente servono solo qui, e cosi' escono di scope appena la
    partizione e' pronta invece di restare referenziati per tutto il calcolo.
    Su una droplet da 1 GB e' margine che non costa niente recuperare.
    """
    previous_row, unavailable = await db.fetch_previous_snapshot(conn, snapshot=snapshot)
    if previous_row is None:
        return None, unavailable

    previous_edges = await db.fetch_snapshot_edges(conn, snapshot_id=previous_row["id"])
    membership = {
        layer: partition_of(
            build_metric_graph(previous_edges, layer=layer, params=params),
            params=params,
        )
        for layer in ALL_LAYERS
    }
    # Anche l'as_of, non solo l'id: e' quello che permette alla riga di
    # community di dichiarare a che distanza la stabilita' e' stata calcolata
    # (modello-metriche.md 4.7). L'id da solo non basta, perche' a valle
    # nessuno rilegge graph_snapshots per risalire all'istante.
    return PreviousPartition(
        snapshot_id=previous_row["id"],
        as_of=previous_row["as_of"],
        membership_by_layer=membership,
    ), None


async def _metrics_for_snapshot(
    conn, snapshot, *, params: MetricParams, args: argparse.Namespace
) -> None:
    snapshot_id = snapshot["id"]
    guild_id = snapshot["guild_id"]
    as_of = snapshot["as_of"]

    edges = await db.fetch_snapshot_edges(conn, snapshot_id=snapshot_id)
    previous, unavailable = await _previous_partition(conn, snapshot, params=params)

    since = datetime.combine(
        cohort_window_start(as_of, params=params), time.min, tzinfo=timezone.utc
    )
    members = await db.fetch_cohort_members(
        conn, guild_id=guild_id, as_of=as_of, since=since
    )
    anchor = await db.fetch_observability_anchor(conn, guild_id=guild_id)
    comparable, skipped = await db.fetch_comparable_snapshots(
        conn,
        guild_id=guild_id,
        reference=db.identity_of(snapshot),
        since=since,
        until=as_of,
    )
    reached_at, scan_stats = await db.scan_partner_growth(
        conn, snapshots=comparable, members=members, params=params
    )
    # La cadenza della serie di snapshot: una settimana senza job e' censura
    # intervallare, e non e' la stessa cosa di uno snapshot che esiste ma non e'
    # confrontabile (quello e' snapshots_skipped_params).
    gaps = series_spacing(row["as_of"] for row in comparable)

    result = build_metrics(
        snapshot_id=snapshot_id,
        guild_id=guild_id,
        as_of=as_of,
        params=params,
        edges=edges,
        members=members,
        reached_at=reached_at,
        observability_anchor=anchor,
        snapshot_windows=[
            (row["window_start"], row["window_end"]) for row in comparable
        ],
        previous=previous,
        stability_unavailable_reason=unavailable,
        cohort_stats={
            **scan_stats,
            "snapshots_skipped_params": skipped,
            "snapshot_gaps": gaps,
        },
    )

    _log_metrics(result, snapshot_id=snapshot_id, guild_id=guild_id)

    if args.dry_run:
        logger.info("--dry-run: nessuna scrittura su Postgres.")
        return

    await db.write_metrics(
        conn,
        snapshot_id=snapshot_id,
        guild_id=guild_id,
        as_of=as_of,
        params=params.as_run_params(),
        stats=result.stats,
        code_version=_code_version(),
        robustness=result.robustness,
        communities=result.communities,
        community_sizes=result.community_sizes,
        cohorts=result.cohorts,
        cohort_retention=result.cohort_retention,
    )
    logger.info("snapshot %d: metriche scritte.", snapshot_id)


def _log_metrics(result, *, snapshot_id: int, guild_id: int) -> None:
    """Riepilogo dell'esecuzione, con gli stessi aggregati che andranno in tabella.

    Nessun ``author_id``, nessun elenco di nodi rimossi, nessuna composizione
    delle community, a nessun livello di log: i log del job finiscono su stdout
    del container, quindi in journald, quindi persistiti — e un livello di log
    non e' un confine di sicurezza, perche' e' una variabile d'ambiente
    (modello-metriche.md 8).
    """
    logger.info(
        "snapshot=%d guild_id=%s: %d righe robustezza, %d community, %d bucket, "
        "%d coorti, %d righe di retention",
        snapshot_id,
        guild_id,
        len(result.robustness),
        len(result.communities),
        len(result.community_sizes),
        len(result.cohorts),
        len(result.cohort_retention),
    )
    for row in result.robustness:
        logger.info(
            "  robustezza layer=%s X=%.2f: %s",
            row.layer,
            row.removal_fraction,
            _describe(row),
        )
    for row in result.communities:
        logger.info("  community layer=%s: %s", row.layer, _describe(row))
    for row in result.community_sizes:
        logger.info(
            "  dimensioni layer=%s bucket=%s: %s", row.layer, row.bucket, _describe(row)
        )
    for row in result.cohorts:
        logger.info(
            "  coorte %s scope=%s k=%s: %s",
            row.cohort_start,
            row.layer_scope,
            row.k,
            _describe(row),
        )
    for row in result.cohort_retention:
        logger.info(
            "  retention %s a %d giorni: %s",
            row.cohort_start,
            row.horizon_days,
            _describe(row),
        )


def _describe(row) -> str:
    """Una riga come finira' in tabella, soppressione inclusa.

    ``--dry-run`` non e' una scorciatoia per guardare i dati per-nodo senza
    scriverli: stampa esattamente gli stessi aggregati che scriverebbe, con le
    celle soppresse gia' a NULL.
    """
    if row.is_suppressed:
        return f"SOPPRESSA ({row.suppression_reason})"
    values = as_public_dict(row)
    for key in (*row.KEY_FIELDS, "is_suppressed", "suppression_reason"):
        values.pop(key, None)
    # details fa parte della riga, e le sue diagnostiche — copertura di
    # snapshot, cadenza della serie, motivo per cui la stabilita' manca — sono
    # proprio quelle che rendono leggibile un numero che altrimenti sembra un
    # risultato. Ometterle dal --dry-run significava doverle dedurre.
    details = values.pop("details", None)
    rendered = ", ".join(
        f"{key}={value!r}" for key, value in values.items() if value is not None
    )
    if details:
        rendered += f", details={json.dumps(details, default=str, sort_keys=True)}"
    return rendered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job", description=__doc__)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="calcola e scrive uno snapshot del grafo")
    snapshot.add_argument(
        "--guild-id", type=int, default=None, help="solo questa guild (default: tutte)"
    )
    snapshot.add_argument(
        "--as-of",
        default=None,
        help=(
            "istante rispetto a cui decadere i pesi (ISO 8601; default: lunedi' "
            "00:00 UTC della settimana corrente)"
        ),
    )
    snapshot.add_argument("--window-start", default=None, help="inizio finestra (ISO 8601)")
    snapshot.add_argument("--window-end", default=None, help="fine finestra (ISO 8601)")
    snapshot.add_argument(
        "--window-days",
        type=float,
        default=DEFAULT_PARAMS.default_window_days,
        help="ampiezza della finestra in giorni, se window-start non e' dato",
    )
    snapshot.add_argument(
        "--dry-run", action="store_true", help="calcola e logga senza scrivere nulla"
    )

    metrics = sub.add_parser(
        "metrics", help="calcola le metriche aggregate di uno snapshot gia' scritto"
    )
    # Mutuamente esclusivi: uno snapshot preciso E una guild insieme sono una
    # richiesta contraddittoria, e l'errore deve arrivare dall'interfaccia
    # invece che da un comportamento silenzioso.
    target = metrics.add_mutually_exclusive_group()
    target.add_argument("--snapshot-id", type=int, default=None)
    target.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="ultimo snapshot di questa guild (default: tutte le guild)",
    )
    metrics.add_argument(
        "--dry-run",
        action="store_true",
        help="calcola e stampa gli aggregati (soppressione inclusa) senza scrivere nulla",
    )

    export = sub.add_parser("export", help="esporta uno snapshot in GraphML, un file per layer")
    export.add_argument("--snapshot-id", type=int, default=None)
    export.add_argument(
        "--guild-id", type=int, default=None, help="ultimo snapshot di questa guild"
    )
    export.add_argument("--out-dir", default=str(DEFAULT_EXPORT_DIR))
    export.add_argument(
        "--identified",
        action="store_true",
        help="esporta gli id Discord reali invece degli pseudonimi (da usare con cautela)",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.command == "snapshot":
        asyncio.run(run_snapshot(args, DEFAULT_PARAMS))
    elif args.command == "metrics":
        asyncio.run(run_metrics(args, DEFAULT_METRIC_PARAMS))
    else:
        asyncio.run(run_export(args))


if __name__ == "__main__":
    main()
