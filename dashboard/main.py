"""Applicazione della dashboard: server-rendered, parla solo con l'API.

Il browser non chiama mai l'API: le chiamate partono da questo processo, sulla
rete interna del compose (dashboard.md 1). Nessuna connessione a Postgres,
nessuna variabile di database nell'ambiente — il processo rifiuta di partire se
ne trova una.

Fase 2 (dashboard.md 8, dashboard-fase2.md): davanti c'e' Caddy, e ogni vista
sta dietro il login Discord di ``auth.py``. Fuori dalla guardia restano
``/health`` (lo chiama l'healthcheck del container, dall'interno), le rotte del
login stesso e ``/static/`` — il foglio di stile e la favicon, che servono
alla pagina d'accesso e non dicono niente di nessun server. Il binding sul
loopback per il tunnel SSH resta.

Uso (``--factory``: la configurazione OAuth si legge all'avvio, e importare il
modulo — i test lo fanno — non deve pretenderla):
    uvicorn dashboard.main:crea_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
from . import community, coorti, domande, regole, robustezza, stato
from .qualifica import cella

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Il prefisso che l'applicazione serve da STATIC_DIR, e l'unico che sfugge al
# "private, no-store". Scritto una volta sola: il mount e il middleware devono
# parlare dello stesso insieme di URL, e due stringhe uguali per caso sono due
# stringhe che un giorno divergono senza nessun errore (CLAUDE.md 7).
PREFISSO_STATICI = "/static/"

# Le pagine: mai in cache, da nessuna parte. Un computer condiviso non deve
# conservare la pagina di un amministratore (dashboard-fase2.md 3-bis).
CACHE_PAGINE = "private, no-store"
# I file statici: un anno, e "immutable" perche' il browser non li rivalidi
# nemmeno con un ricarica. E' sicuro SOLO perche' l'URL porta l'impronta del
# contenuto (impronta_statico): un foglio nuovo e' un URL nuovo, e quello vecchio
# non viene piu' chiesto da nessuno. Senza quella impronta, questa riga
# congelerebbe per un anno lo stile di chi ha gia' visitato la dashboard.
CACHE_STATICI = "public, max-age=31536000, immutable"

# La CSP delle risposte della dashboard (dashboard.md 3). "default-src 'none'"
# e poi solo cio' che serve davvero, verificato sul codice e non copiato da un
# esempio:
#
# - style-src 'self': il solo /static/dashboard.css. NIENTE 'unsafe-inline',
#   che e' ottenibile oggi perche' nessun template ha un <style> o uno
#   style="..." e nessuna stringa generata in Python ne produce — i grafici
#   sono SVG con attributi di presentazione, che la CSP non guarda. Il test
#   che lo tiene vero sulle quindici pagine sta in tests/test_dashboard_statici.py:
#   senza, il primo style="" aggiunto per comodita' non si vedrebbe qui ma
#   nella console di chi legge, con l'elemento senza stile.
# - script-src 'self': oggi non c'e' nessuno script, e la direttiva dice che
#   se un giorno ce ne sara' uno dovra' essere un file di questa origine, non
#   una riga dentro la pagina.
# - img-src 'self': nessuna immagine oggi (il marchio e' un <svg> in linea, non
#   un <img>). "data:" stava nella bozza ed e' stato tolto perche' niente lo
#   usa; rimetterlo e' una riga, e la sua mancanza si vede subito — immagine
#   rotta e violazione in console — non in silenzio.
# - form-action 'self' https://discord.com: i due form della dashboard postano
#   su /login e /logout, ma POST /login risponde 303 verso
#   discord.com/oauth2/authorize, e form-action vale anche sulla DESTINAZIONE
#   del redirect, non solo sull'action del form. Provato su Chromium il
#   21/09/2026 con la CSP accesa: con il solo 'self' la POST parte, torna il
#   303 e la navigazione viene abortita (net::ERR_ABORTED), e il messaggio in
#   console e' fuorviante — nomina l'action del form ("Sending form data to
#   'http://localhost:8001/login' violates ... form-action 'self'"), cioe'
#   proprio l'URL che 'self' consente, e non dice mai discord.com. Con
#   discord.com elencato, lo stesso giro arriva alla pagina di Discord.
#   Sbagliare questa riga vuol dire un bottone "Accedi con Discord" che non
#   porta da nessuna parte, senza nessun errore fuori dalla console, sulla
#   prima pagina che un amministratore vede: il difetto di CLAUDE.md 7.
# - base-uri 'none' e frame-ancestors 'none': un <base> iniettato
#   riscriverebbe ogni URL relativo della pagina, e nessuno deve poter
#   incorniciare la dashboard.
CSP = "; ".join(
    (
        "default-src 'none'",
        "style-src 'self'",
        "script-src 'self'",
        "img-src 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self' https://discord.com",
    )
)

# I documenti pubblici di Kindling. URL ASSOLUTI, e non e' una scelta di stile:
# li serve kindling.nexus, non questo host — un percorso relativo cadrebbe su
# dashboard.kindling.nexus, dove non esiste, e darebbe una 404 al posto della
# privacy policy.
#
# Qui e non nei template perche' compaiono in DUE posti: il piede di tutte e
# undici le pagine e la testata delle due pubbliche. Due copie sono due copie che
# divergono al primo indirizzo che cambia (CLAUDE.md 7), e la divergenza di un
# URL non si vede finche' qualcuno non ci clicca sopra.
#
# Tre voci e non quattro: la radice del sito non c'e' perche' nessuna pagina la
# nomina — il marchio in testata punta a "/", che qui e' l'elenco dei server di
# chi guarda. Una voce che nessuno usa e' una voce di cui nessuno si accorge se
# sbaglia.
SITO_PUBBLICO = {
    "privacy": "https://kindling.nexus/informativa-privacy.html",
    "termini": "https://kindling.nexus/termini-di-servizio.html",
    "codice": "https://github.com/NikeGipple/Kindling",
}

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


def _impronta(dati: bytes) -> str:
    """Le prime dodici cifre esadecimali dello sha256. Valore opaco: serve solo
    che sia lo stesso per lo stesso contenuto e diverso per un contenuto diverso."""
    return hashlib.sha256(dati).hexdigest()[:12]


@lru_cache(maxsize=None)
def impronta_statico(nome: str) -> str:
    """L'impronta del contenuto di un file di ``/static/``, per la query del suo URL.

    L'impronta del file e non ``KINDLING_CODE_VERSION``, che pure e' incisa
    nell'immagine e cambia a ogni deploy. Due ragioni, e la seconda e' quella
    che decide:

    1. cambia ESATTAMENTE quando cambia quel file: un deploy che non tocca il
       CSS non butta via la copia in cache di nessuno;
    2. in sviluppo ``KINDLING_CODE_VERSION`` e' vuota — nel Dockerfile e' un
       ARG con default vuoto, e in locale non la esporta nessuno. Sarebbe una
       versione COSTANTE accanto a "immutable, un anno", cioe' un foglio che
       non si aggiorna mai proprio dove cambia ogni minuto, e per accorgersene
       bisogna sospettare della cache invece che del proprio CSS.

    Prende un NOME e non e' una variabile sola (com'era ``versione_css`` fino al
    21/09/2026, quando il foglio era l'unico file statico): con la favicon i file
    sono due, e una sola impronta per tutti e due sarebbe il difetto di
    CLAUDE.md 7 — sembra che la cache si invalidi, e per il file che non ha
    mosso l'impronta non si invalida mai.

    Letta una volta per nome (``lru_cache``): i file stanno nell'immagine e non
    cambiano sotto un processo vivo. ``cache_clear()`` esiste per i test.
    """
    return _impronta((STATIC_DIR / nome).read_bytes())


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
    # Globali della cornice, non variabili di contesto: le vogliono tutte e nove
    # le pagine e nessuna rotta ha un'opinione diversa. Con StrictUndefined, una
    # cornice passata dalla rotta sarebbe una riga da ricordarsi in ogni rotta
    # nuova; una globale non si puo' dimenticare.
    env.globals["impronta_statico"] = impronta_statico
    env.globals["sito"] = SITO_PUBBLICO
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

    # Il foglio di stile lo serve QUESTA applicazione, non Caddy, e non e' una
    # svista di configurazione: CSS e template stanno nella stessa immagine e
    # cambiano con lo stesso `docker compose build`. Caddy legge invece dalla
    # copia del repo sulla droplet, quindi fra il `git pull` e il rebuild
    # servirebbe il CSS nuovo alle pagine vecchie — una finestra breve, e muta.
    #
    # Fuori dalla guardia, come /login: senza foglio di stile la pagina d'accesso
    # sarebbe illeggibile proprio a chi non e' ancora entrato. Ne' il foglio ne'
    # la favicon dicono niente di nessun server.
    app.mount(PREFISSO_STATICI.rstrip("/"), StaticFiles(directory=STATIC_DIR), name="statici")

    # Ogni risposta, non solo quelle autenticate: e' il sovrainsieme che non si
    # sbaglia. Senza, una cache intermedia o il disco del browser su un computer
    # condiviso conservano la pagina di un amministratore (dashboard-fase2.md
    # 3-bis) — e la difesa sta qui, non in una configurazione fatta altrove.
    #
    # L'unica eccezione e' /static/, ed e' scritta STRETTA: il prefisso E lo stato
    # 200/304. Una 404 sotto /static/ (un file rinominato, un URL sbagliato)
    # ricade nel ramo delle pagine e resta no-store, invece di farsi ricordare
    # come "assente" per un anno da ogni browser che l'ha chiesta.
    #
    # La CSP sta qui e non in un decoratore per rotta per la stessa ragione della
    # cache: una rotta nuova non puo' dimenticarsela. Sulle risposte statiche non
    # si mette, perche' un foglio di stile non ha sottorisorse da governare.
    @app.middleware("http")
    async def _intestazioni(request: Request, call_next):
        risposta = await call_next(request)
        statica = (
            request.url.path.startswith(PREFISSO_STATICI)
            and risposta.status_code in (200, 304)
        )
        if statica:
            risposta.headers["Cache-Control"] = CACHE_STATICI
        else:
            risposta.headers["Cache-Control"] = CACHE_PAGINE
            risposta.headers["Content-Security-Policy"] = CSP
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
    async def vista_stato(request: Request, guild_id: int):
        """Vista Stato (dashboard.md 4): la vista iniziale, quella che spiega le altre.

        Cinque chiamate e non due, ed e' il prezzo dichiarato di "Cosa puoi
        leggere oggi": non c'e' modo di sapere se una vista e' leggibile senza
        guardare il ``quality`` delle sue righe, e un campo riassuntivo
        sull'API sarebbe un secondo posto da tenere allineato con le righe che
        riassume. ``cohorts()`` chiede un solo snapshot: il default e' nella
        firma del client, e qui non si ha un'opinione diversa da quella.
        """
        api: ApiClient = request.app.state.api
        guild = await api.guild(guild_id)
        runs = await api.runs(guild_id)
        return pagina(
            request,
            "stato.html",
            guild_id=guild_id,
            vista=stato.costruisci(
                guild,
                runs,
                await api.robustness(guild_id),
                await api.communities(guild_id),
                await api.cohorts(guild_id),
            ),
            vista_corrente="stato",
        )

    @app.get("/guilds/{guild_id}/domande", response_class=HTMLResponse, dependencies=protetta)
    async def pagina_domande(request: Request, guild_id: int):
        """Le Domande (dashboard.md 4): risposte generiche, con un indirizzo ciascuna.

        Legge le run per una ragione sola: le soglie citate nelle risposte
        vengono da ``params``, come in Stato. Senza run non ci sono soglie da
        citare, e le frasi che le nominavano semplicemente non compaiono.
        """
        api: ApiClient = request.app.state.api
        runs = await api.runs(guild_id)
        ultima = stato.parametri_ultima_run(runs)
        return pagina(
            request,
            "domande.html",
            guild_id=guild_id,
            domande=domande.costruisci(
                guild_id,
                ultima.params if ultima is not None else None,
                privacy_url=SITO_PUBBLICO["privacy"],
                cadenza=stato.cadenza_osservata(runs),
            ),
            vista_corrente="domande",
        )

    @app.get(
        "/guilds/{guild_id}/dettagli-tecnici",
        response_class=HTMLResponse,
        dependencies=protetta,
    )
    async def pagina_dettagli_tecnici(request: Request, guild_id: int):
        """I Dettagli tecnici (dashboard.md 4): cio' che e' uscito da Stato.

        ``vista_corrente`` c'e' — quindi menu e riga di contesto ci sono — ma
        nessuna voce del menu porta qui: ci si arriva dalla domanda che la
        nomina. Il menu serve a tornare indietro, non ad arrivarci.
        """
        api: ApiClient = request.app.state.api
        runs = await api.runs(guild_id)
        ultima = stato.parametri_ultima_run(runs)
        return pagina(
            request,
            "dettagli_tecnici.html",
            guild_id=guild_id,
            ultima=ultima,
            storico=stato.storico(runs),
            aree=regole.per_area(ultima.params if ultima is not None else None),
            vista_corrente="dettagli",
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
