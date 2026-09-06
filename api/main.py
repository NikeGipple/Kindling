"""Applicazione FastAPI: legge aggregati, non calcola niente.

Sette endpoint, tutti in sola lettura (docs/architettura/api.md 2). Nessun
endpoint di scrittura, nessuno che esponga il grafo o qualunque cosa per-nodo, e
nessuna aggregazione calcolata al volo: soglie e soppressione vivono nel layer di
calcolo, e un aggregato costruito a runtime le aggirerebbe.

**Nessuna autenticazione, e non e' una dimenticanza.** Il servizio non pubblica
porte: e' raggiungibile dagli altri container del compose e, per lo sviluppo, via
tunnel SSH. TLS e autenticazione sono un prerequisito del momento in cui l'API
diventa raggiungibile da fuori — cioe' quando esistera' il dashboard — non un
affinamento successivo, e un'autenticazione posticcia "per intanto" sarebbe
peggio di nessuna: darebbe l'impressione che il problema sia risolto.

Uso:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Query

from . import assemble, db
from .config import DEFAULT_LIMIT, MAX_LIMIT, database_url
from .models import (
    CohortGroup,
    CommunityRow,
    GuildRow,
    Health,
    RobustnessRow,
    RunRow,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    await db.init_pool(database_url())
    logger.info("API avviata: pool Postgres pronto (ruolo di sola lettura)")
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title="Kindling — metriche aggregate",
    description=(
        "Aggregati di salute della community. Ogni riga porta la propria "
        "qualificazione in `quality`: un valore non viaggia mai senza i flag "
        "che dicono quanto vale. Nessun dato per-nodo e' esposto."
    ),
    version="0",
    lifespan=lifespan,
)


def limit_param(
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=(
            "Quanti snapshot restituire, dal piu' recente. Lo stato dell'ultimo "
            "snapshot e' limit=1."
        ),
    ),
) -> int:
    return limit


async def existing_guild(
    guild_id: int = Path(description="Id del server Discord."),
) -> int:
    """404 se la community non e' mai stata vista dal bot.

    E' una distinzione che si puo' fare solo perche' ``guilds`` e' leggibile
    (api.md 1): senza, "questa community non esiste" e "esiste ma non ha ancora
    metriche" collasserebbero entrambe in una serie vuota, e la prima
    sembrerebbe la seconda.
    """
    if await db.fetch_guild(guild_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"guild_id {guild_id} non risulta osservata da Kindling.",
        )
    return guild_id


@app.get("/health", response_model=Health, tags=["servizio"])
async def health() -> Health:
    """L'API e' viva e Postgres risponde."""
    try:
        alive = await db.ping()
    except Exception:
        logger.exception("Health check fallito: Postgres non raggiungibile")
        raise HTTPException(status_code=503, detail="database non raggiungibile")
    return Health(status="ok", database="ok" if alive else "degradato")


@app.get("/guilds", response_model=list[GuildRow], tags=["community"])
async def list_guilds() -> list[GuildRow]:
    """Le community osservate, da quando, e a che data arrivano le metriche."""
    return [assemble.guild(row) for row in await db.fetch_guilds()]


@app.get("/guilds/{guild_id}", response_model=GuildRow, tags=["community"])
async def get_guild(guild_id: int = Depends(existing_guild)) -> GuildRow:
    """L'osservabilita' di una community: ancora, backfill, buchi.

    ``first_seen_at`` e' l'ancora di osservabilita', cioe' la spiegazione di
    ``is_survivors_only`` e di ``before_observability_anchor``. Si espone di
    proposito: servire i caveat senza la ragione dei caveat e' peggio che non
    servirli.
    """
    row = await db.fetch_guild(guild_id)
    assert row is not None  # garantito da existing_guild
    return assemble.guild(row)


@app.get("/guilds/{guild_id}/runs", response_model=list[RunRow], tags=["metriche"])
async def list_runs(
    guild_id: int = Depends(existing_guild), limit: int = Depends(limit_param)
) -> list[RunRow]:
    """Stato dell'ultimo snapshot (``limit=1``) e storico delle esecuzioni."""
    return [assemble.run(row) for row in await db.fetch_runs(guild_id, limit=limit)]


@app.get(
    "/guilds/{guild_id}/robustness",
    response_model=list[RobustnessRow],
    tags=["metriche"],
)
async def list_robustness(
    guild_id: int = Depends(existing_guild), limit: int = Depends(limit_param)
) -> list[RobustnessRow]:
    """Robustezza strutturale, una riga per layer e frazione di rimozione."""
    rows = await db.fetch_robustness(guild_id, limit=limit)
    return [assemble.robustness(row) for row in rows]


@app.get(
    "/guilds/{guild_id}/communities",
    response_model=list[CommunityRow],
    tags=["metriche"],
)
async def list_communities(
    guild_id: int = Depends(existing_guild), limit: int = Depends(limit_param)
) -> list[CommunityRow]:
    """Partizione Leiden, con la distribuzione delle dimensioni annidata."""
    rows = await db.fetch_communities(guild_id, limit=limit)
    sizes = await db.fetch_community_sizes(guild_id, limit=limit)
    return assemble.communities(rows, sizes)


@app.get(
    "/guilds/{guild_id}/cohorts", response_model=list[CohortGroup], tags=["metriche"]
)
async def list_cohorts(
    guild_id: int = Depends(existing_guild), limit: int = Depends(limit_param)
) -> list[CohortGroup]:
    """Coorti di ingresso: onboarding e retention insieme.

    Le due tabelle condividono la popolazione per progetto, e servirle da due
    endpoint inviterebbe a leggerle separate — che e' il modo di fallire contro
    cui la duplicazione dei denominatori esiste.
    """
    rows = await db.fetch_cohorts(guild_id, limit=limit)
    retention = await db.fetch_cohort_retention(guild_id, limit=limit)
    return assemble.cohorts(rows, retention)
