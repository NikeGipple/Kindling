"""Applicazione della dashboard: server-rendered, parla solo con l'API.

Il browser non chiama mai l'API: le chiamate partono da questo processo, sulla
rete interna del compose (dashboard.md 1). Nessuna connessione a Postgres,
nessuna variabile di database nell'ambiente — il processo rifiuta di partire se
ne trova una.

Fase 1 (dashboard.md 8): porta pubblicata solo sul loopback dell'host,
raggiungibile via tunnel SSH, e nessuna autenticazione. Non e' "autenticazione
posticcia": e' "non esposto", che e' un'altra cosa.

Uso:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from . import config
from .client import (
    ApiClient,
    ApiNonRaggiungibile,
    GuildNonOsservata,
    RispostaNonConforme,
    crea_http,
)
from . import community, robustezza
from .qualifica import cella

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
_MESI = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)


def data_ora(valore: Optional[datetime]) -> str:
    """Data leggibile, sempre in UTC e sempre dichiarata tale.

    Nomi di giorni e mesi scritti qui e non presi dal locale del sistema: il
    container gira con il locale C, e un rendering che cambia con la macchina e'
    una differenza tra sviluppo e produzione che nessuno cerca.
    """
    if valore is None:
        return "—"
    if valore.tzinfo is None:
        valore = valore.replace(tzinfo=timezone.utc)
    v = valore.astimezone(timezone.utc)
    return f"{_GIORNI[v.weekday()]} {v.day} {_MESI[v.month - 1]} {v.year}, {v:%H:%M} UTC"


def crea_templates() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        # Autoescape esplicito: i valori arrivano dall'API, e un campo testuale
        # come suppression_reason non deve poter iniettare markup.
        autoescape=select_autoescape(["html"]),
        # Una variabile scritta male nel template fallisce, invece di diventare
        # una stringa vuota — che in questa UI sembrerebbe un dato assente.
        undefined=StrictUndefined,
    )
    env.filters["data_ora"] = data_ora
    env.globals["cella"] = cella
    env.globals["percentuale"] = robustezza.percentuale
    env.globals["nodi_rimossi"] = robustezza.nodi_rimossi
    env.globals["senza_confronto"] = community.senza_confronto
    return env


def crea_app(*, api_http: Optional[httpx.AsyncClient] = None) -> FastAPI:
    """Costruisce l'app. ``api_http`` e' per i test, che montano il fixture senza rete.

    Senza ``api_http`` l'indirizzo dell'API si legge dall'ambiente all'avvio: una
    configurazione mancante ferma il processo prima che risponda a qualunque
    richiesta, invece di produrre pagine d'errore a ogni visita.
    """
    templates = crea_templates()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.assert_no_database_variables()
        if api_http is not None:
            app.state.api = ApiClient(api_http)
            yield
            return
        base_url = config.api_base_url()
        async with crea_http(base_url) as http:
            app.state.api = ApiClient(http)
            logger.info("Dashboard avviata, API su %s", base_url)
            yield

    app = FastAPI(
        title="Kindling — dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def pagina(nome: str, *, status_code: int = 200, **contesto) -> HTMLResponse:
        html = templates.get_template(nome).render(**contesto)
        return HTMLResponse(html, status_code=status_code)

    # --- errori: un aspetto diverso da ogni stato di qualificazione --------
    #
    # dashboard.md 5, regola 1: "soppresso", "non significativo" e gli altri non
    # sono errori. L'errore e' l'API che non risponde, e ha la sua pagina.

    @app.exception_handler(ApiNonRaggiungibile)
    async def _api_non_raggiungibile(request: Request, exc: ApiNonRaggiungibile):
        return pagina(
            "errore.html",
            status_code=503,
            titolo="L'API di Kindling non risponde",
            messaggio=(
                "La dashboard non riesce a leggere i dati. Non è uno stato dei dati: "
                "è un guasto di comunicazione, e nessun numero di questa pagina è stato letto."
            ),
            dettaglio=str(exc),
        )

    @app.exception_handler(RispostaNonConforme)
    async def _risposta_non_conforme(request: Request, exc: RispostaNonConforme):
        return pagina(
            "errore.html",
            status_code=502,
            titolo="L'API ha risposto in una forma inattesa",
            messaggio=(
                "La risposta non corrisponde al contratto di api/models.py. "
                "Dashboard e API sono probabilmente a versioni diverse del codice."
            ),
            dettaglio=str(exc),
        )

    @app.exception_handler(GuildNonOsservata)
    async def _guild_non_osservata(request: Request, exc: GuildNonOsservata):
        return pagina(
            "non_osservata.html",
            status_code=404,
            guild_id=exc.guild_id,
        )

    # --- rotte ---------------------------------------------------------------

    @app.get("/health")
    async def health(request: Request):
        """Viva e capace di raggiungere l'API: e' cio' che il healthcheck del compose chiede."""
        try:
            await request.app.state.api.health()
        except (ApiNonRaggiungibile, RispostaNonConforme) as exc:
            return JSONResponse({"status": "degradato", "api": str(exc)}, status_code=503)
        return {"status": "ok", "api": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def elenco_guild(request: Request):
        guilds = await request.app.state.api.guilds()
        return pagina("guilds.html", guilds=guilds)

    @app.get("/guilds/{guild_id}", response_class=HTMLResponse)
    async def stato(request: Request, guild_id: int):
        """Vista Stato (dashboard.md 4): la vista iniziale, quella che spiega le altre."""
        api: ApiClient = request.app.state.api
        guild = await api.guild(guild_id)
        runs = await api.runs(guild_id)
        return pagina(
            "stato.html",
            guild_id=guild_id,
            guild=guild,
            runs=runs,
            ultima=runs[0] if runs else None,
            buco_di_osservazione=guild.left_at is not None and guild.rejoined_at is not None,
            vista_corrente="stato",
        )

    @app.get("/guilds/{guild_id}/robustezza", response_class=HTMLResponse)
    async def vista_robustezza(request: Request, guild_id: int):
        """Vista Robustezza (dashboard.md 4): la connettivita' dipende da pochi connettori?"""
        api: ApiClient = request.app.state.api
        righe = await api.robustness(guild_id)
        return pagina(
            "robustezza.html",
            guild_id=guild_id,
            vista=robustezza.costruisci(righe),
            vista_corrente="robustezza",
        )

    @app.get("/guilds/{guild_id}/community", response_class=HTMLResponse)
    async def vista_community(request: Request, guild_id: int):
        """Vista Community (dashboard.md 4): gruppi distinguibili dal rumore, e stabili?"""
        api: ApiClient = request.app.state.api
        righe = await api.communities(guild_id)
        return pagina(
            "community.html",
            guild_id=guild_id,
            vista=community.costruisci(righe),
            vista_corrente="community",
        )

    return app


app = crea_app()
