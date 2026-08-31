"""Entrypoint del job di calcolo del grafo.

Uso:
    python -m job.main snapshot [--guild-id N] [--window-days 7] [--as-of ISO]
    python -m job.main export --snapshot-id N [--out-dir exports] [--identified]

Il job non e' un servizio: parte, calcola, scrive e muore. Nessun processo
sempre acceso in piu' sulla droplet oltre a bot e Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from . import db
from .config import ALL_LAYERS, DEFAULT_PARAMS, GraphParams
from .graph import build_layer_graph, write_graphml
from .intervals import activity_lookup
from .pseudonyms import load_salt, pseudonymize
from .snapshot import build_snapshot

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
    as_of = _parse_instant(args.as_of) if args.as_of else datetime.now(timezone.utc)
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
    # as_of, perche' oltre non c'e' nulla da sapere.
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
            logger.info(
                "guild_id=%s: %d sessioni in finestra (%d fuori finestra), "
                "intervalli: %d osservati, %d ricostruiti, %d ancora aperti, "
                "%d scartati oltre il tetto",
                guild_id,
                len(result.sessions),
                result.sessions_out_of_window,
                stats.closed_observed,
                stats.closed_reconciled,
                stats.still_open,
                stats.discarded_over_cap,
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
        help="istante rispetto a cui decadere i pesi (ISO 8601, default: adesso)",
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
    else:
        asyncio.run(run_export(args))


if __name__ == "__main__":
    main()
