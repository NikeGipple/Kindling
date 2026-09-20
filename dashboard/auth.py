"""Login con Discord e autorizzazione: dashboard.md 3, in un file solo.

Il modello non si decide qui, si applica: scope ``identify guilds``, permesso
ADMINISTRATOR sul server osservato, ricontrollo ogni 15 minuti, sessione di 8
ore, ``state`` monouso, token Discord scartato appena usato. Le due aggiunte di
``dashboard-fase2.md`` 3-quinquies sono la ragione della forma di questo file:

- **Il ricontrollo e' un giro silenzioso attraverso Discord** (``prompt=none``),
  non una chiamata in background: il token non c'e' piu'. E' il flow completo
  senza schermata, e la decisione si prende ricalcolando l'insieme autorizzato,
  non leggendo un errore — chi perde ADMINISTRATOR riceve comunque un ``code``
  valido. Ogni esito diverso da ``code`` valido, ``state`` valido, scambio
  riuscito e insieme non vuoto nega l'accesso.
- **La scadenza delle 8 ore e' assoluta, e la impone la guardia** con
  ``login_at``. ``SessionMiddleware`` di Starlette rifirma il cookie a ogni
  risposta, quindi il ``max_age`` del signer conta dall'ULTIMA richiesta, non dal
  login: da solo e' un timeout di inattivita', che un cookie rubato e usato di
  continuo — o il ricontrollo stesso, ogni 15 minuti — terrebbe vivo per sempre.
  Il signer resta come seconda difesa, non come la difesa.

Nella sessione stanno le guild autorizzate, i loro nomi, ``checked_at`` e
``login_at`` — piu' ``state`` e destinazione, solo fino al callback. Nessun token,
nessuno username, nessun id Discord di chi si collega (dashboard.md 3): il cookie
e' firmato, non cifrato, e quello che ci finisce dentro e' leggibile da chi ce
l'ha. Il nome di un server non e' un'identita' di chi si collega: e' il nome di
una cosa che quella persona amministra, e lo sa gia'.

I nomi vengono da ``GET /users/@me/guilds``, non dal database
(stato-progetto.md 7-R): ``guilds`` non ha una colonna ``name``, e Discord il nome
lo manda gia' a ogni login e a ogni ricontrollo. **Il nome non decide mai niente**
— l'autorizzazione si gioca sugli id, e se i nomi sparissero del tutto chi entra e
chi no non cambierebbe di una riga.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Mapping, MutableMapping, Optional
from urllib.parse import urlencode

import httpx
from starlette.requests import Request

from .config import OAuthConfig

logger = logging.getLogger(__name__)

# Il bit ADMINISTRATOR del bitfield dei permessi Discord.
ADMINISTRATOR = 0x8

# Le due scadenze di dashboard.md 3, che proteggono da cose diverse.
TTL_RICONTROLLO_SECONDI = 15 * 60  # dalla revoca che non fa effetto
DURATA_SESSIONE_SECONDI = 8 * 3600  # da un cookie rubato o un PC lasciato aperto

SCOPE = "identify guilds"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_API_BASE_URL = "https://discord.com/api/v10"
# Stesso ragionamento del timeout verso l'API: un login che aspetta Discord per
# trenta secondi non e' piu' paziente, e' bloccato.
DISCORD_TIMEOUT_SECONDI = 5.0

COOKIE_SESSIONE = "kindling_sessione"

# Discord dichiara il nome di una guild fra 2 e 100 caratteri (campo ``name`` di
# https://discord.com/developers/docs/resources/guild, letto il 20/09/2026). Il
# troncamento e' una rete contro una risposta che violi il proprio limite, non
# una regola nostra: in pratica non deve mai scattare.
LUNGHEZZA_MASSIMA_NOME = 100

# Il budget del JSON di sessione, in byte, PRIMA di base64 e firma. Un cookie sta
# sotto i 4096 byte contando nome, valore e attributi, e da quel tetto si torna
# indietro: il valore e' ``base64(json)`` firmato, cioe' 4*ceil(n/3) byte piu' 35
# di firma (il punto separatore, il timestamp e l'HMAC-SHA1 in base64 di
# itsdangerous), piu' 73 fra il nome del cookie e gli attributi che
# SessionMiddleware scrive sempre (``path``, ``Max-Age``, ``httponly``,
# ``samesite``, ``secure``). Con n = 2800: 4*934 + 35 + 73 = 3844, e restano ~250
# byte di margine. La misura si fa con lo stesso ``json.dumps`` di Starlette, che
# scrive i separatori larghi e sfugge i non-ASCII: e' il caso peggiore, quindi si
# sbaglia dalla parte prudente.
BUDGET_JSON_SESSIONE_BYTE = 2800

_K_GUILDS = "guilds"
_K_NOMI = "nomi"
_K_CHECKED_AT = "checked_at"
_K_LOGIN_AT = "login_at"
_K_FLUSSO = "oauth_flusso"
_K_DOPO = "dopo"


class Caso(str, Enum):
    """Perche' l'accesso e' negato. Frasi diverse, perche' sono cose diverse."""

    NESSUN_SERVER = "nessun_server"
    VERIFICA_FALLITA = "verifica_fallita"
    SCADUTA = "scaduta"
    SERVER_NON_AUTORIZZATO = "server_non_autorizzato"


class AccessoNegato(Exception):
    def __init__(self, caso: Caso):
        super().__init__(caso.value)
        self.caso = caso


class AccessoRichiesto(Exception):
    """Serve un giro altrove prima di rispondere: alla pagina di accesso o a Discord."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


class RispostaDiscordNonValida(Exception):
    """Discord non ha risposto, o non nella forma attesa. Si nega, sempre."""


def adesso() -> float:
    """L'orologio della sessione, in un punto solo: i test lo spostano da qui."""
    return time.time()


# --- il controllo di autorizzazione: UNA funzione -----------------------------


def e_amministratore(guild: Mapping[str, Any]) -> bool:
    """Il controllo di autorizzazione, e vive solo qui (dashboard.md 3).

    Il giorno in cui servira' un ruolo dedicato si cambia questa funzione, non si
    fa un refactor.

    ``permissions`` e' una STRINGA: i bitfield Discord superano 2^53, e una
    conversione che passi da un float (o un confronto su una stringa) risponde
    sbagliato in silenzio. Un valore che non e' una stringa di cifre non e' un
    "no": e' una risposta che non si capisce, e alza — il chiamante nega tutto.
    ``owner is True`` e non ``owner``: una stringa ``"false"`` e' vera.
    """
    if guild.get("owner") is True:
        return True
    permessi = guild.get("permissions")
    if not isinstance(permessi, str) or not (permessi.isascii() and permessi.isdigit()):
        raise RispostaDiscordNonValida(f"permissions non e' una stringa di cifre: {permessi!r}")
    return int(permessi) & ADMINISTRATOR == ADMINISTRATOR


@dataclass(frozen=True)
class Autorizzazione:
    """Chi entra, e come si chiamano i server. Due campi, una sola decisione.

    ``guilds`` e' l'autorizzazione. ``nomi`` la accompagna: contiene solo guild
    che stanno gia' in ``guilds``, e solo quelle che un nome ce l'hanno.
    """

    guilds: frozenset[int]
    nomi: Mapping[int, str]

    def __bool__(self) -> bool:
        # Prima di questo commit la funzione restituiva un insieme, e
        # ``if not autorizzate`` era una riga vera di completa_callback. Su un
        # oggetto quella riga e' sempre falsa: farla alzare e' l'unico modo
        # perche' un insieme autorizzato vuoto non passi in silenzio.
        raise TypeError("Autorizzazione non si valuta come booleana: guarda .guilds")


def _nome_di_guild(guild: Mapping[str, Any]) -> Optional[str]:
    """Il nome della guild, se c'e' ed e' una stringa. Mai un motivo per negare.

    E' qui la differenza con ``id`` e ``permissions``, e vale dirla: quelli fanno
    alzare quando sono fuori forma, perche' sono i dati su cui si decide. Un
    ``name`` assente, non stringa o vuoto non fa niente — la guild resta
    autorizzata e semplicemente non ha nome. Il nome non vota, ne' per concedere
    ne' per negare.

    Si tronca in SCRITTURA, non in rendering: una sessione con dentro un nome
    lungo quanto pare sarebbe un problema del cookie, non della pagina.
    """
    nome = guild.get("name")
    if not isinstance(nome, str):
        return None
    # Prima il taglio, poi lo strip: cosi' un troncamento non lascia uno spazio
    # in coda, e uno spazio in testa (che Discord dichiara di escludere) sparisce
    # comunque.
    nome = nome[:LUNGHEZZA_MASSIMA_NOME].strip()
    return nome or None


def guild_autorizzate(risposta_discord: Any, osservate: Iterable[int]) -> Autorizzazione:
    """Le guild osservate da Kindling su cui l'utente e' amministratore, e i loro nomi.

    ``risposta_discord`` e' il JSON di ``GET /users/@me/guilds``, non ancora
    fidato: qualunque voce fuori forma fa alzare, e un'eccezione qui e' un
    accesso negato. Mai un "salto la voce strana e tengo le altre".

    "Fuori forma" riguarda ``id`` e ``permissions``, non ``name``: vedi
    ``_nome_di_guild``. I nomi si raccolgono per le guild gia' autorizzate, dopo
    che la decisione e' presa.

    Discord restituisce al massimo 200 guild per pagina e qui se ne legge una:
    chi e' in piu' di 200 server puo' non vedere quelli oltre la prima pagina.
    E' un difetto in direzione di NEGARE, quindi accettabile.
    """
    if not isinstance(risposta_discord, list):
        raise RispostaDiscordNonValida("la risposta delle guild non e' una lista")
    osservate = frozenset(osservate)
    autorizzate = set()
    nomi: dict[int, str] = {}
    for guild in risposta_discord:
        if not isinstance(guild, dict):
            raise RispostaDiscordNonValida("una guild non e' un oggetto")
        gid = guild.get("id")
        if not isinstance(gid, str) or not (gid.isascii() and gid.isdigit()):
            raise RispostaDiscordNonValida(f"id di guild non valido: {gid!r}")
        if e_amministratore(guild) and int(gid) in osservate:
            autorizzate.add(int(gid))
            nome = _nome_di_guild(guild)
            if nome is not None:
                nomi[int(gid)] = nome
    return Autorizzazione(guilds=frozenset(autorizzate), nomi=nomi)


# --- sessione ----------------------------------------------------------------


@dataclass(frozen=True)
class Sessione:
    guilds: frozenset[int]
    login_at: float
    checked_at: float
    # I nomi dei server autorizzati che ne hanno uno in sessione. Le chiavi
    # stanno sempre dentro ``guilds``: lo impone ``_nomi_di_sessione``.
    nomi: Mapping[int, str]


def _nomi_di_sessione(dati: Mapping[str, Any], guilds: frozenset[int]) -> Optional[dict[int, str]]:
    """I nomi scritti in sessione, o ``None`` se la chiave c'e' e non va bene.

    Tre casi, tenuti distinti di proposito.

    1. **Chiave assente**: mappa vuota, sessione valida. E' il cookie firmato
       prima del deploy di questo codice, e un cambio di formato che sloggia
       tutti sarebbe rumore per niente: la testata mostra l'ID finche' il
       ricontrollo dei 15 minuti non riscrive la sessione con i nomi.
    2. **Chiave presente e fuori forma** — non una mappa, una chiave che non e'
       un intero, un valore che non e' una stringa: ``None``, cioe' nessuna
       sessione. O tutta o niente, mai "salto la voce strana e tengo le altre".
    3. **Chiave presente e valida, ma con una guild fuori dall'insieme
       autorizzato**: ``None`` anche qui, ed e' la decisione meno ovvia delle
       tre. Questi dati li scriviamo noi, e ``completa_callback`` scrive
       ``guilds`` e ``nomi`` insieme subito dopo un ``clear()``: non esiste un
       cammino normale che li faccia divergere. Se divergono, o la firma l'ha
       prodotta qualcun altro o il difetto e' nostro, e in nessuno dei due casi
       "tengo il resto" e' automaticamente giusto — vorrebbe dire continuare su
       dati che abbiamo appena dimostrato di non capire. Non costa niente alle
       sessioni gia' vive, che ricadono nel caso 1, e rende rumoroso un futuro
       scrittore che sbagli l'invariante invece di lasciarlo passare.
    """
    if _K_NOMI not in dati:
        return {}
    grezzi = dati[_K_NOMI]
    if not isinstance(grezzi, dict):
        return None
    nomi: dict[int, str] = {}
    for chiave, valore in grezzi.items():
        # Le chiavi di un oggetto JSON sono stringhe: l'id torna intero qui.
        if not isinstance(chiave, str) or not (chiave.isascii() and chiave.isdigit()):
            return None
        if not isinstance(valore, str):
            return None
        gid = int(chiave)
        if gid not in guilds:
            return None
        nomi[gid] = valore
    return nomi


def leggi_sessione(dati: Mapping[str, Any]) -> Optional[Sessione]:
    """La sessione autenticata, o None. Un campo fuori forma vale "nessuna sessione".

    **Questa funzione non alza mai.** ``guild_autorizzate`` legge Discord e, su
    una risposta fuori forma, alza: li' l'eccezione diventa un accesso negato.
    Qui si legge un cookie nostro, e l'esito e' ``None``. Stesso principio — o
    tutta la sessione o niente — meccanismo diverso, e chiamarlo con il nome
    dell'altro e' un modo di aspettarsi un'eccezione che non arrivera'.
    """
    guilds = dati.get(_K_GUILDS)
    login_at = dati.get(_K_LOGIN_AT)
    checked_at = dati.get(_K_CHECKED_AT)
    if not isinstance(guilds, list) or not all(
        isinstance(g, int) and not isinstance(g, bool) for g in guilds
    ):
        return None
    if not all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in (login_at, checked_at)):
        return None
    nomi = _nomi_di_sessione(dati, frozenset(guilds))
    if nomi is None:
        return None
    return Sessione(
        guilds=frozenset(guilds),
        login_at=float(login_at),
        checked_at=float(checked_at),
        nomi=nomi,
    )


def destinazione_sicura(valore: Any) -> str:
    """Un percorso locale, o ``/``. Niente open redirect (dashboard.md 3).

    ``//evil.example`` e ``/\\evil.example`` sono URL assoluti per un browser, pur
    iniziando con ``/``: un percorso come ``//evil.example`` arriva anche da una
    richiesta del tutto normale, quindi il controllo vale anche per cio' che la
    sessione ha preso da ``request.url.path``.
    """
    if (
        not isinstance(valore, str)
        or not valore.startswith("/")
        or valore.startswith("//")
        or "\\" in valore
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in valore)
    ):
        return "/"
    return valore


def ricorda_destinazione(request: Request) -> None:
    request.session[_K_DOPO] = destinazione_sicura(request.url.path)


def avvia_flusso(request: Request, oauth: OAuthConfig, *, silenzioso: bool) -> str:
    """Prepara il giro su Discord e restituisce l'URL a cui mandare il browser.

    ``state`` monouso in sessione, insieme a COSA e' questo giro: un ricontrollo
    (``prompt=none``, conserva ``login_at``) o un login interattivo (schermata di
    consenso, ``login_at`` nuovo). Il tipo sta nella sessione e non nell'URL: se
    lo decidesse il client, un giro silenzioso potrebbe presentarsi come login e
    rinnovare le 8 ore senza nessuna interazione.
    """
    state = secrets.token_urlsafe(32)
    if silenzioso:
        dopo = destinazione_sicura(request.url.path)
    else:
        dopo = destinazione_sicura(request.session.get(_K_DOPO))
    request.session.pop(_K_DOPO, None)
    request.session[_K_FLUSSO] = {"state": state, "ricontrollo": silenzioso, "dopo": dopo}
    parametri = {
        "response_type": "code",
        "client_id": oauth.client_id,
        "scope": SCOPE,
        "state": state,
        "redirect_uri": oauth.redirect_uri,
        "prompt": "none" if silenzioso else "consent",
    }
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(parametri)}"


# --- la guardia --------------------------------------------------------------


def verifica_accesso(request: Request, oauth: OAuthConfig) -> Sessione:
    """La guardia di ogni vista. Alza ``AccessoNegato`` o ``AccessoRichiesto``.

    Nell'ordine: sessione presente, 8 ore non superate, ricontrollo non scaduto,
    guild della rotta dentro l'insieme autorizzato.
    """
    dati = request.session
    sessione = leggi_sessione(dati)
    if sessione is None:
        # Cookie presente e sessione vuota: il signer l'ha rifiutato (scaduto o
        # manomesso). Senza cookie non c'e' mai stato un accesso.
        firma_rifiutata = COOKIE_SESSIONE in request.cookies and not dati
        dati.clear()
        ricorda_destinazione(request)
        if firma_rifiutata:
            raise AccessoNegato(Caso.SCADUTA)
        raise AccessoRichiesto("/login")

    ora = adesso()
    if ora - sessione.login_at >= DURATA_SESSIONE_SECONDI:
        dati.clear()
        ricorda_destinazione(request)
        raise AccessoNegato(Caso.SCADUTA)

    if not sessione.guilds:
        dati.clear()
        raise AccessoNegato(Caso.NESSUN_SERVER)

    if ora - sessione.checked_at >= TTL_RICONTROLLO_SECONDI:
        if request.method != "GET":
            # Un giro su Discord non si fa nel mezzo di un POST: si nega, e la
            # sessione non si prolunga per inerzia.
            dati.clear()
            raise AccessoNegato(Caso.VERIFICA_FALLITA)
        raise AccessoRichiesto(avvia_flusso(request, oauth, silenzioso=True))

    guild_id = request.path_params.get("guild_id")
    if guild_id is not None:
        try:
            autorizzata = int(guild_id) in sessione.guilds
        except (TypeError, ValueError):
            autorizzata = False
        if not autorizzata:
            raise AccessoNegato(Caso.SERVER_NON_AUTORIZZATO)

    request.state.sessione = sessione
    return sessione


# --- il callback -------------------------------------------------------------


async def _guild_da_discord(
    discord_http: httpx.AsyncClient, oauth: OAuthConfig, code: str
) -> Any:
    """Scambia il ``code`` e legge le guild. Il token muore con questa funzione.

    Non si scrive da nessuna parte, non si logga, e non si revoca: la revoca di
    un token puo' ritirare l'autorizzazione dell'applicazione, e il ricontrollo
    silenzioso successivo troverebbe allora un utente mai autorizzato.
    """
    try:
        risposta = await discord_http.post(
            "/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": oauth.redirect_uri,
            },
            auth=(oauth.client_id, oauth.client_secret),
        )
        if risposta.status_code != 200:
            raise RispostaDiscordNonValida(f"scambio del code: HTTP {risposta.status_code}")
        token = risposta.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise RispostaDiscordNonValida("scambio del code: nessun access_token")
        risposta = await discord_http.get(
            "/users/@me/guilds", headers={"Authorization": f"Bearer {token}"}
        )
        if risposta.status_code != 200:
            raise RispostaDiscordNonValida(f"guild dell'utente: HTTP {risposta.status_code}")
        return risposta.json()
    except httpx.HTTPError as exc:
        raise RispostaDiscordNonValida(f"Discord non raggiungibile ({type(exc).__name__})") from exc
    except (ValueError, AttributeError) as exc:
        # JSON malformato, o JSON che non e' un oggetto.
        raise RispostaDiscordNonValida("risposta di Discord non leggibile") from exc


async def completa_callback(
    request: Request,
    oauth: OAuthConfig,
    discord_http: httpx.AsyncClient,
    guild_osservate: Callable[[], Awaitable[Iterable[int]]],
) -> str:
    """Chiude il giro su Discord. Restituisce la destinazione, o alza ``AccessoNegato``.

    Un ``state`` assente, diverso o gia' usato nega senza toccare la sessione:
    non e' il giro di questa sessione, e non deve poterla nemmeno cancellare. Da
    ``state`` valido in poi, ogni fallimento cancella l'autorizzazione: un
    ricontrollo fallito non lascia al suo posto la sessione di prima.
    """
    dati = request.session
    flusso = dati.pop(_K_FLUSSO, None)  # monouso: tolto PRIMA di qualunque confronto
    ricevuto = request.query_params.get("state")
    atteso = flusso.get("state") if isinstance(flusso, dict) else None
    if (
        not isinstance(atteso, str)
        or not ricevuto
        or not secrets.compare_digest(ricevuto.encode("utf-8"), atteso.encode("utf-8"))
    ):
        raise AccessoNegato(Caso.VERIFICA_FALLITA)

    def nega(caso: Caso) -> AccessoNegato:
        dati.clear()
        return AccessoNegato(caso)

    precedente = leggi_sessione(dati)
    if flusso.get("ricontrollo") is True:
        if precedente is None:
            raise nega(Caso.VERIFICA_FALLITA)
        # Il ricontrollo NON rinnova le 8 ore: login_at resta quello del login
        # interattivo. E' il punto di dashboard-fase2.md 3-quinquies.
        login_at = precedente.login_at
        if adesso() - login_at >= DURATA_SESSIONE_SECONDI:
            raise nega(Caso.SCADUTA)
    else:
        login_at = adesso()

    code = request.query_params.get("code")
    if "error" in request.query_params or not code:
        # Il comportamento di prompt=none su un'autorizzazione revocata non e'
        # documentato da Discord: non ci si appoggia. Un errore qualunque nega.
        logger.info("Callback OAuth senza code (error=%s)", request.query_params.get("error"))
        raise nega(Caso.VERIFICA_FALLITA)

    try:
        risposta = await _guild_da_discord(discord_http, oauth, code)
        autorizzate = guild_autorizzate(risposta, await guild_osservate())
    except RispostaDiscordNonValida as exc:
        logger.warning("Verifica del permesso non riuscita: %s", exc)
        raise nega(Caso.VERIFICA_FALLITA) from exc
    except Exception as exc:
        # Anche l'API di Kindling che non risponde (l'insieme delle osservate) e'
        # una verifica non riuscita: si nega, non si passa.
        logger.warning("Verifica del permesso non riuscita: %s", type(exc).__name__)
        raise nega(Caso.VERIFICA_FALLITA) from exc

    if not autorizzate.guilds:
        raise nega(Caso.NESSUN_SERVER)

    dati.clear()
    dati[_K_GUILDS] = sorted(autorizzate.guilds)
    dati[_K_LOGIN_AT] = login_at
    dati[_K_CHECKED_AT] = adesso()
    # I nomi passano di qui a ogni login E a ogni ricontrollo silenzioso, cioe'
    # almeno ogni 15 minuti: un server rinominato prende il nome nuovo da solo,
    # e l'invalidazione della cache che qualcuno cerchera' altrove e' questa riga.
    _scrivi_nomi(dati, autorizzate.nomi)
    return destinazione_sicura(flusso.get("dopo"))


def _scrivi_nomi(dati: MutableMapping[str, Any], nomi: Mapping[int, str]) -> None:
    """Scrive in sessione tutti i nomi, o nessuno.

    Mai qualcuno: un elenco in cui tre server hanno il nome e due il numero
    sembra un difetto, mentre un ripiego totale sugli ID e' leggibile e si spiega
    da se'. Con un server osservato non scattera' mai — esiste perche' il giorno
    in cui scattera' nessuno stara' guardando.
    """
    if not nomi:
        return
    candidata = dict(dati)
    # Le chiavi di un oggetto JSON sono stringhe: gli id ci vanno come tali.
    candidata[_K_NOMI] = {str(gid): nome for gid, nome in sorted(nomi.items())}
    if len(json.dumps(candidata).encode("utf-8")) <= BUDGET_JSON_SESSIONE_BYTE:
        dati[_K_NOMI] = candidata[_K_NOMI]
    else:
        logger.info(
            "Sessione oltre il budget del cookie (%d guild): nessun nome in sessione",
            len(nomi),
        )


def crea_discord_http(*, transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=DISCORD_API_BASE_URL,
        timeout=DISCORD_TIMEOUT_SECONDI,
        follow_redirects=False,
        transport=transport,
    )
