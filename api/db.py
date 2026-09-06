"""Tutto il SQL dell'API, e nient'altro.

Stessa disciplina di ``job/db.py``. Con una differenza che qui e' il punto:
**nessuna query di scrittura, e nessuna query su una tabella che contenga dati
riferibili a una persona** (docs/architettura/api.md 1). Il criterio non e'
affidato a questo file — il ruolo ``kindling_api`` non ha i permessi (migration
0010) — ma un lettore deve poterlo verificare guardando qui.

Nessuna aggregazione calcolata al volo: soglie e soppressione vivono nel layer
di calcolo, e un aggregato costruito a runtime le aggirerebbe. Le query
selezionano colonne e le ordinano, e basta.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    """Pool piccolo: le query sono letture puntuali su tabelle minuscole.

    ``max_size=2`` su una droplet da 1 GB con 1 vCPU: un pool grande sarebbe
    solo memoria ferma (architettura.md, sezione Hosting).
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool Postgres non inizializzato.")
    return _pool


# ---- salute ---------------------------------------------------------------


async def ping() -> bool:
    pool = get_pool()
    return await pool.fetchval("SELECT 1") == 1


# ---- guild ----------------------------------------------------------------


async def fetch_guilds() -> list[asyncpg.Record]:
    """Le community osservate, con l'as_of dell'ultima run di metriche.

    LEFT JOIN e non INNER: una guild osservata ma senza metriche esiste ed e'
    una risposta legittima — ``latest_metrics_as_of`` a NULL dice esattamente
    quello, mentre farla sparire dall'elenco la renderebbe indistinguibile da
    una community mai vista.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT g.guild_id, g.first_seen_at, g.backfilled_at,
               g.left_at, g.rejoined_at,
               (SELECT max(r.as_of) FROM metric_runs r
                 WHERE r.guild_id = g.guild_id) AS latest_metrics_as_of
        FROM guilds g
        ORDER BY g.guild_id
        """
    )


async def fetch_guild(guild_id: int) -> Optional[asyncpg.Record]:
    """Una guild, o ``None`` se il bot non l'ha mai vista.

    E' cio' che permette di distinguere 404 ("questa community non esiste") da
    serie vuota ("esiste, non ha ancora metriche"): due cose diverse che senza
    ``guilds`` leggibile collasserebbero in una sola.
    """
    pool = get_pool()
    return await pool.fetchrow(
        """
        SELECT g.guild_id, g.first_seen_at, g.backfilled_at,
               g.left_at, g.rejoined_at,
               (SELECT max(r.as_of) FROM metric_runs r
                 WHERE r.guild_id = g.guild_id) AS latest_metrics_as_of
        FROM guilds g
        WHERE g.guild_id = $1
        """,
        guild_id,
    )


# ---- run ------------------------------------------------------------------


async def fetch_runs(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT snapshot_id, as_of, params, stats, code_version, created_at
        FROM metric_runs
        WHERE guild_id = $1
        ORDER BY as_of DESC, snapshot_id DESC
        LIMIT $2
        """,
        guild_id,
        limit,
    )


# ---- metriche -------------------------------------------------------------


async def fetch_robustness(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    """Le righe di robustezza degli ultimi ``limit`` snapshot.

    Il limite e' sugli SNAPSHOT, non sulle righe: ogni snapshot produce una riga
    per layer e per valore di X, e troncare a meta' snapshot darebbe una serie
    con l'ultimo punto incompleto — che a un grafico sembra un calo.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT r.snapshot_id, run.as_of, r.layer, r.removal_fraction,
               r.n_effective, r.nodes_removed, r.giant_before,
               r.giant_after_targeted, r.components_after_targeted,
               r.giant_after_random_mean, r.giant_after_random_sd,
               r.components_after_random_mean, r.targeted_excess, r.targeted_z,
               r.is_suppressed, r.suppression_reason, r.is_significant, r.details
        FROM metric_robustness r
        JOIN metric_runs run ON run.snapshot_id = r.snapshot_id
        WHERE run.guild_id = $1
          AND run.snapshot_id IN (
              SELECT snapshot_id FROM metric_runs
              WHERE guild_id = $1 ORDER BY as_of DESC LIMIT $2
          )
        ORDER BY run.as_of DESC, r.layer, r.removal_fraction
        """,
        guild_id,
        limit,
    )


async def fetch_communities(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT c.snapshot_id, run.as_of, c.layer,
               c.n_effective, c.community_count, c.modularity,
               c.modularity_random_mean, c.modularity_random_sd, c.modularity_z,
               c.previous_snapshot_id, c.node_overlap, c.stability_jaccard,
               c.communities_born, c.communities_dissolved,
               c.communities_merged, c.communities_split,
               c.is_suppressed, c.suppression_reason, c.is_significant, c.details
        FROM metric_communities c
        JOIN metric_runs run ON run.snapshot_id = c.snapshot_id
        WHERE run.guild_id = $1
          AND run.snapshot_id IN (
              SELECT snapshot_id FROM metric_runs
              WHERE guild_id = $1 ORDER BY as_of DESC LIMIT $2
          )
        ORDER BY run.as_of DESC, c.layer
        """,
        guild_id,
        limit,
    )


async def fetch_community_sizes(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT s.snapshot_id, s.layer, s.bucket,
               s.community_count, s.member_count,
               s.is_suppressed, s.suppression_reason
        FROM metric_community_sizes s
        JOIN metric_runs run ON run.snapshot_id = s.snapshot_id
        WHERE run.guild_id = $1
          AND run.snapshot_id IN (
              SELECT snapshot_id FROM metric_runs
              WHERE guild_id = $1 ORDER BY as_of DESC LIMIT $2
          )
        ORDER BY s.snapshot_id DESC, s.layer, s.bucket
        """,
        guild_id,
        limit,
    )


async def fetch_cohorts(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT c.snapshot_id, run.as_of, c.cohort_start, c.layer_scope, c.k,
               c.n_effective, c.observation_days, c.is_mature,
               c.event_count, c.censored_count, c.censored_by_leave,
               c.median_days_to_k, c.median_reached,
               c.p25_days_to_k, c.p75_days_to_k,
               c.reached_by_14d, c.reached_by_28d,
               c.excluded_rejoins, c.is_survivors_only, c.has_snapshot_coverage,
               c.is_suppressed, c.suppression_reason, c.is_significant, c.details
        FROM metric_cohorts c
        JOIN metric_runs run ON run.snapshot_id = c.snapshot_id
        WHERE run.guild_id = $1
          AND run.snapshot_id IN (
              SELECT snapshot_id FROM metric_runs
              WHERE guild_id = $1 ORDER BY as_of DESC LIMIT $2
          )
        ORDER BY run.as_of DESC, c.cohort_start DESC, c.layer_scope, c.k
        """,
        guild_id,
        limit,
    )


async def fetch_cohort_retention(guild_id: int, *, limit: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT t.snapshot_id, run.as_of, t.cohort_start, t.horizon_days,
               t.n_effective, t.excluded_rejoins, t.is_survivors_only,
               t.retained_fraction, t.is_computable, t.not_computable_reason,
               t.is_suppressed, t.suppression_reason
        FROM metric_cohort_retention t
        JOIN metric_runs run ON run.snapshot_id = t.snapshot_id
        WHERE run.guild_id = $1
          AND run.snapshot_id IN (
              SELECT snapshot_id FROM metric_runs
              WHERE guild_id = $1 ORDER BY as_of DESC LIMIT $2
          )
        ORDER BY t.snapshot_id DESC, t.cohort_start DESC, t.horizon_days
        """,
        guild_id,
        limit,
    )
