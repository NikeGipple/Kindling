"""Applicazione della dashboard: server-rendered, parla solo con l'API.

Il browser non chiama mai l'API: le chiamate partono da questo processo, sulla
rete interna del compose (dashboard.md 1). Nessuna connessione a Postgres,
nessuna variabile di database nell'ambiente — il processo rifiuta di partire se
ne trova una.

Fase 2 (dashboard.md 8, dashboard-fase2.md): davanti c'e' Caddy, e ogni vista
sta dietro il login Discord di ``auth.py``. Fuori dalla guardia restano solo
``/health`` (lo chiama l'healthcheck del container, dall'interno) e le rotte del
login stesso. Il binding sul loopback per il tunnel SSH resta.

Uso (``--factory``: la configurazione OAuth si legge all'avvio, e importare il
modulo — i test lo fanno — non deve pretenderla):
    uvicorn dashboard.main:crea_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config
from .client import (
    ApiClient,
    ApiNonRaggiungibile,
    GuildNonOsservata,
    RispostaNonConforme,
    crea_http,
)
from . import community, coorti, robustezza
from .qualifica import cella

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
_MESI = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)

# Le viste dove i simboli di qualificazione POSSONO comparire, e quindi dove il
# piede porta la legenda. La regola guarda la vista, non i dati del giorno: una
# legenda che va e viene con quello che c'e' oggi in tabella diventerebbe essa
# stessa un segnale, e non l'ha progettata nessuno. Stato non c'e' perche' mostra
# il contesto, non metriche: nessuna sua cella passa da cella().
_VISTE_CON_SIMBOLI = frozenset({"robustezza", "community", "coorti"})


def cornice(**contesto) -> dict:
    """Le variabili che ``base.html`` si aspetta, aggiunte se chi rende non le passa.

    Sta fuori da ``pagina()``, che in produzione e' l'unico chiamante, perche'
    base.html si rende anche senza una Request: i test delle viste chiedono il
    template direttamente per guardare cosa produce il modulo di vista. Con
    ``StrictUndefined`` una variabile di cornice che vivesse dentro ``pagina()``
    li farebbe fallire, e la riparazione ovvia — ricopiarla nel test — sarebbe una
    copia destinata a divergere (CLAUDE.md 7) proprio al commit successivo, quando
    ``nome_server`` smettera' di essere l'ID.
    """
    # La riga di contesto in testata mostra l'ID finche' il nome non arriva dalla
    # sessione (stato-progetto.md 7-R, commit successivo). La variabile esiste da
    # subito perche' quel commit non debba riaprire base.html.
    if "guild_id" in contesto:
        contesto.setdefault("nome_server", str(contesto["guild_id"]))
    contesto.setdefault("mostra_legenda", contesto.get("vista_corrente") in _VISTE_CON_SIMBOLI)
    return contesto


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
    # Vista Coorti: le colonne che NON passano da cella() perche' stanno in
    # quality e non in values (maturita', copertura, esclusi), la nota di cella di
    # censored_by_leave e la frase di stato. Sono decisioni di coorti.py, non del
    # template: qui si registrano soltanto.
    env.globals["maturita"] = coorti.maturita
    env.globals["copertura"] = coorti.copertura
    env.globals["esclusi"] = coorti.esclusi
    env.globals["censura"] = coorti.censura
    env.globals["frase_di_stato"] = coorti.frase_di_stato
    # Ambiti, orizzonti e soglie arrivano da job/config.py passando per
    # coorti.py: ricopiarli nel template li farebbe divergere dal job in
    # silenzio, che e' il difetto di CLAUDE.md 7 applicato a un'intestazione.
    env.globals["ambiti"] = coorti.AMBITI
    env.globals["orizzonti"] = coorti.ORIZZONTI
    env.globals["nomi_ambito"] = coorti.NOMI_AMBITO
    env.globals["k_connessioni"] = coorti.K_CONNESSIONI
    env.globals["giorni_maturita"] = coorti.GIORNI_MATURITA
    return env


def crea_app(
    *,
    api_http: Optional[httpx.AsyncClient] = None,
    oauth: Optional[config.OAuthConfig] = None,
    discord_http: Optional[httpx.AsyncClient] = None,
) -> FastAPI:
    """Costruisce l'app. ``api_http``, ``oauth`` e ``discord_http`` sono per i test.

    Senza di essi l'indirizzo dell'API e la configurazione OAuth si leggono
    dall'ambiente all'avvio: una configurazione mancante ferma il processo prima
    che risponda a qualunque richiesta, invece di produrre pagine d'errore a ogni
    visita.
    """
    templates = crea_templates()
    if oauth is None:
        oauth = config.oauth_da_ambiente()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.assert_no_database_variables()
        async with contextlib.AsyncExitStack() as stack:
            if discord_http is not None:
                app.state.discord = discord_http
            else:
                app.state.discord = await stack.enter_async_context(auth.crea_discord_http())
            if api_http is not None:
                app.state.api = ApiClient(api_http)
            else:
                base_url = config.api_base_url()
                http = await stack.enter_async_context(crea_http(base_url))
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

    # Ogni risposta, non solo quelle autenticate: e' il sovrainsieme che non si
    # sbaglia. Senza, una cache intermedia o il disco del browser su un computer
    # condiviso conservano la pagina di un amministratore (dashboard-fase2.md
    # 3-bis) — e la difesa sta qui, non in una configurazione fatta altrove.
    @app.middleware("http")
    async def _nessuna_cache(request: Request, call_next):
        risposta = await call_next(request)
        risposta.headers["Cache-Control"] = "private, no-store"
        return risposta

    # max_age e' la SECONDA difesa: Starlette rifirma il cookie a ogni risposta,
    # quindi da solo e' un timeout di inattivita'. La scadenza assoluta delle 8
    # ore la impone la guardia con login_at (auth.py, dashboard-fase2.md
    # 3-quinquies). HttpOnly lo mette Starlette sempre; Secure lo decide la
    # redirect URI (config.OAuthConfig.cookie_secure).
    app.add_middleware(
        SessionMiddleware,
        secret_key=oauth.session_secret,
        session_cookie=auth.COOKIE_SESSIONE,
        max_age=auth.DURATA_SESSIONE_SECONDI,
        same_site="lax",
        https_only=oauth.cookie_secure,
    )

    def pagina(
        request: Request, nome: str, *, status_code: int = 200, **contesto
    ) -> HTMLResponse:
        # Per la testata solo il numero di server: in sessione non c'e' nessuna
        # identita', quindi nessuna da mostrare (dashboard.md 3).
        sessione = getattr(request.state, "sessione", None)
        if sessione is not None:
            contesto.setdefault("server_autorizzati", len(sessione.guilds))
            # Il nome del server viene dalla sessione, non dal database
            # (stato-progetto.md 7-R). Quando non c'e' — cookie firmato prima del
            # deploy, guild senza nome su Discord, ripiego per budget del cookie —
            # qui non si imposta niente, e il setdefault di cornice() lascia
            # l'ID. Il ripiego e' gia' scritto li': non serve una condizione nuova.
            nome_server = sessione.nomi.get(contesto.get("guild_id"))
            if nome_server:
                contesto.setdefault("nome_server", nome_server)
        html = templates.get_template(nome).render(**cornice(**contesto))
        return HTMLResponse(html, status_code=status_code)

    async def richiede_accesso(request: Request) -> auth.Sessione:
        return auth.verifica_accesso(request, oauth)

    # La guardia di ogni vista. Una rotta nuova che la dimentica fallisce in
    # tests/test_dashboard_auth.py, che le enumera tutte.
    protetta = [Depends(richiede_accesso)]

    # --- errori: un aspetto diverso da ogni stato di qualificazione --------
    #
    # dashboard.md 5, regola 1: "soppresso", "non significativo" e gli altri non
    # sono errori. L'errore e' l'API che non risponde, e ha la sua pagina.

    @app.exception_handler(ApiNonRaggiungibile)
    async def _api_non_raggiungibile(request: Request, exc: ApiNonRaggiungibile):
        return pagina(
            request,
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
            request,
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
            request,
            "non_osservata.html",
            status_code=404,
            guild_id=exc.guild_id,
        )

    @app.exception_handler(auth.AccessoRichiesto)
    async def _accesso_richiesto(request: Request, exc: auth.AccessoRichiesto):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(auth.AccessoNegato)
    async def _accesso_negato(request: Request, exc: auth.AccessoNegato):
        # 401 quando basta rientrare; 404 per un server fuori dall'insieme, che
        # non conferma nemmeno che Kindling lo osservi; 403 per il resto.
        status = {
            auth.Caso.SCADUTA: 401,
            auth.Caso.SERVER_NON_AUTORIZZATO: 404,
        }.get(exc.caso, 403)
        return pagina(request, "accesso_negato.html", status_code=status, caso=exc.caso.value)

    # --- login ---------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    async def login(request: Request):
        return pagina(request, "accesso.html")

    @app.post("/login")
    async def avvia_login(request: Request):
        """Il bottone "Accedi con Discord": POST, perche' scrive lo ``state`` in sessione."""
        return RedirectResponse(auth.avvia_flusso(request, oauth, silenzioso=False), status_code=303)

    @app.get("/oauth/callback")
    async def oauth_callback(request: Request):
        async def osservate() -> list[int]:
            return [g.guild_id for g in await request.app.state.api.guilds()]

        destinazione = await auth.completa_callback(
            request, oauth, request.app.state.discord, osservate
        )
        return RedirectResponse(destinazione, status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        """POST e non GET: un ``<img src="/logout">`` non deve poter sloggare nessuno."""
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # --- rotte ---------------------------------------------------------------

    @app.get("/health")
    async def health(request: Request):
        """Viva e capace di raggiungere l'API: e' cio' che il healthcheck del compose chiede."""
        try:
            await request.app.state.api.health()
        except (ApiNonRaggiungibile, RispostaNonConforme) as exc:
            return JSONResponse({"status": "degradato", "api": str(exc)}, status_code=503)
        return {"status": "ok", "api": "ok"}

    @app.get("/", response_class=HTMLResponse, dependencies=protetta)
    async def elenco_guild(request: Request):
        # Solo i server dell'insieme autorizzato: l'elenco completo di quelli
        # osservati non e' affare di chi ne amministra uno.
        sessione = request.state.sessione
        guilds = [g for g in await request.app.state.api.guilds() if g.guild_id in sessione.guilds]
        # I nomi viaggiano con l'elenco come ci viaggiano le righe: la rotta ha
        # la sessione, il template non ce l'ha.
        return pagina(request, "guilds.html", guilds=guilds, nomi=sessione.nomi)

    @app.get("/guilds/{guild_id}", response_class=HTMLResponse, dependencies=protetta)
    async def stato(request: Request, guild_id: int):
        """Vista Stato (dashboard.md 4): la vista iniziale, quella che spiega le altre."""
        api: ApiClient = request.app.state.api
        guild = await api.guild(guild_id)
        runs = await api.runs(guild_id)
        return pagina(
            request,
            "stato.html",
            guild_id=guild_id,
            guild=guild,
            runs=runs,
            ultima=runs[0] if runs else None,
            buco_di_osservazione=guild.left_at is not None and guild.rejoined_at is not None,
            vista_corrente="stato",
        )

    @app.get("/guilds/{guild_id}/robustezza", response_class=HTMLResponse, dependencies=protetta)
    async def vista_robustezza(request: Request, guild_id: int):
        """Vista Robustezza (dashboard.md 4): la connettivita' dipende da pochi connettori?"""
        api: ApiClient = request.app.state.api
        righe = await api.robustness(guild_id)
        return pagina(
            request,
            "robustezza.html",
            guild_id=guild_id,
            vista=robustezza.costruisci(righe),
            vista_corrente="robustezza",
        )

    @app.get("/guilds/{guild_id}/community", response_class=HTMLResponse, dependencies=protetta)
    async def vista_community(request: Request, guild_id: int):
        """Vista Community (dashboard.md 4): gruppi distinguibili dal rumore, e stabili?"""
        api: ApiClient = request.app.state.api
        righe = await api.communities(guild_id)
        return pagina(
            request,
            "community.html",
            guild_id=guild_id,
            vista=community.costruisci(righe),
            vista_corrente="community",
        )

    @app.get("/guilds/{guild_id}/coorti", response_class=HTMLResponse, dependencies=protetta)
    async def vista_coorti(request: Request, guild_id: int):
        """Vista Coorti (dashboard.md 4): chi entra si integra, e chi resta?

        ``cohorts()`` chiede uno snapshot solo, e il default e' nella firma del
        client: qui non si passa ``limit``, perche' la vista non ha un'opinione
        diversa da quella gia' dichiarata li'.
        """
        api: ApiClient = request.app.state.api
        gruppi = await api.cohorts(guild_id)
        return pagina(
            request,
            "coorti.html",
            guild_id=guild_id,
            vista=coorti.costruisci(gruppi),
            vista_corrente="coorti",
        )

    return app
