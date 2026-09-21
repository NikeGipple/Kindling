"""Una sessione autenticata per i test delle viste, costruita come la costruisce Starlette.

Le viste stanno dietro la guardia di ``dashboard/auth.py``: i test che guardano
cosa mostra una vista entrano con un cookie firmato con la stessa chiave e lo
stesso formato di ``SessionMiddleware`` (JSON -> base64 -> ``TimestampSigner``),
non con una guardia spenta. Una guardia disattivabile "per i test" sarebbe una
guardia disattivabile.

Nessun segreto scritto qui, nemmeno finto: client secret e chiave di firma si
generano a ogni esecuzione.
"""

from __future__ import annotations

import json
import secrets
import time
from base64 import b64encode
from typing import Iterable, Mapping, Optional

import httpx
import itsdangerous
from fastapi.testclient import TestClient

from dashboard import auth, config
from dashboard.config import OAuthConfig
from dashboard.main import crea_app
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_SCALE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

OAUTH_DI_TEST = OAuthConfig(
    client_id="100000000000000001",
    client_secret=secrets.token_hex(16),
    session_secret=secrets.token_hex(32),
    redirect_uri="http://localhost:8000/oauth/callback",
)

# Le quattro guild del fixture, piu' gli id che i test delle viste usano con un
# transport finto (1, 5) o per provare il 404 dell'API (123, 123456789): per
# arrivare fino all'API quegli id devono essere DENTRO l'insieme autorizzato,
# altrimenti la guardia risponde prima e il test proverebbe la guardia invece
# della vista.
GUILD_DI_TEST = (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE, GUILD_SCALE, 1, 5, 123, 123456789)


class _SignerAllIstante(itsdangerous.TimestampSigner):
    def __init__(self, *args, istante: int, **kwargs):
        super().__init__(*args, **kwargs)
        self._istante = istante

    def get_timestamp(self) -> int:
        return self._istante


def cookie_firmato(
    dati: dict,
    *,
    segreto: str = OAUTH_DI_TEST.session_secret,
    firmato_a: Optional[float] = None,
) -> str:
    """Il valore del cookie di sessione, firmato ORA o all'istante ``firmato_a``."""
    if firmato_a is None:
        signer = itsdangerous.TimestampSigner(segreto)
    else:
        signer = _SignerAllIstante(segreto, istante=int(firmato_a))
    return signer.sign(b64encode(json.dumps(dati).encode("utf-8"))).decode("utf-8")


def dati_di_sessione(
    guilds: Iterable[int] = GUILD_DI_TEST,
    *,
    login_at: Optional[float] = None,
    checked_at: Optional[float] = None,
    nomi: Optional[Mapping[int, str]] = None,
) -> dict:
    """Il contenuto di una sessione firmata.

    **Senza ``nomi`` il risultato ha la forma PRECEDENTE al commit dei nomi dei
    server**, cioe' senza la chiave: e' voluto, e non e' una svista da sistemare.
    Cosi' quasi tutta la suite gira sul cookie che le persone gia' collegate
    avevano in mano nel momento del deploy, che e' l'unico caso che in produzione
    non si puo' riprovare.
    """
    ora = time.time()
    dati = {
        "guilds": sorted(guilds),
        "login_at": ora if login_at is None else login_at,
        "checked_at": ora if checked_at is None else checked_at,
    }
    if nomi is not None:
        # Le chiavi di un oggetto JSON sono stringhe, come le scrive auth.py.
        dati["nomi"] = {str(gid): nome for gid, nome in sorted(nomi.items())}
    return dati


def entra(client: TestClient, dati: Optional[dict] = None, **firma) -> TestClient:
    # Lo stesso dominio con cui httpx registra i cookie che il server imposta a
    # TestClient (``testserver`` senza punto diventa ``testserver.local``): con
    # un dominio diverso la risposta successiva aggiungerebbe un secondo cookie
    # accanto a questo, invece di sostituirlo.
    client.cookies.set(
        auth.COOKIE_SESSIONE,
        cookie_firmato(dati or dati_di_sessione(), **firma),
        domain="testserver.local",
    )
    return client


def client_autenticato(app, **kwargs) -> TestClient:
    return entra(TestClient(app, **kwargs))


# --- le tredici pagine, prese dalle rotte che le producono --------------------
#
# Sta qui e non dentro un file di test perche' due file la guardano: la cornice
# (menu, piede, legenda, coda del marchio) e le intestazioni delle risposte
# (cache, CSP, niente stile in linea). Una seconda copia sarebbe una copia
# destinata a divergere, e la prima cosa che perderebbe sarebbe proprio il conto
# delle pagine — cioe' la ragione per cui la raccolta esiste.
#
# Le pagine si raggiungono DALLE ROTTE, non rendendo i template a mano: un
# contesto costruito qui sarebbe una copia di quello che passa la rotta.

# Fuori dall'insieme autorizzato della sessione di test: la guardia nega prima
# ancora di chiedere all'API se quel server esista.
FUORI_DALL_INSIEME = 424242
assert FUORI_DALL_INSIEME not in GUILD_DI_TEST

# I nove template che estendono base.html. Scritti qui perche' i test possano
# dire "nove", non "quelli che mi sono ricordato di raggiungere".
TEMPLATE_CON_CORNICE = {
    "accesso.html",
    "accesso_negato.html",
    "community.html",
    "coorti.html",
    "errore.html",
    "guilds.html",
    "non_osservata.html",
    "robustezza.html",
    "stato.html",
}

# Le pagine pubbliche: si vedono senza essere entrati. Sono le due che NON
# portano la coda del marchio.
PAGINE_PUBBLICHE = frozenset(
    {"accesso", "negato_server", "negato_verifica", "negato_scaduta", "negato_nessun_server"}
)


def senza_variabili_di_database(monkeypatch) -> None:
    """Il corpo della fixture autouse che ogni file di test della dashboard usa.

    L'elenco lo conosce ``dashboard.config``: ricopiarlo in ogni file lo farebbe
    divergere dal divieto vero al primo nome aggiunto (CLAUDE.md 7).
    """
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


def app_di_test(transport: Optional[httpx.AsyncBaseTransport] = None):
    """L'app della dashboard davanti al fixture dell'API (o a un transport finto)."""
    return crea_app(
        api_http=httpx.AsyncClient(
            transport=transport or httpx.ASGITransport(app=fixture_app),
            base_url="http://api.test",
        ),
        oauth=OAUTH_DI_TEST,
    )


def _api_giu(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("nessuna rete", request=request)


def _api_fuori_contratto(request: httpx.Request) -> httpx.Response:
    # I nomi del database invece di quelli del modello: RispostaNonConforme.
    return httpx.Response(200, json={"guild_id": GUILD_TODAY, "is_suppressed": False})


def raccogli_pagine() -> dict:
    """Ogni pagina presa dalla rotta che la produce, con il contesto che le passa.

    Se ``StrictUndefined`` alza dentro un template, il TestClient rialza qui: la
    raccolta stessa fallisce, e si vede quale pagina.
    """
    raccolta: dict[str, tuple[str, httpx.Response]] = {}

    with client_autenticato(app_di_test(), follow_redirects=False) as c:
        raccolta["elenco"] = ("guilds.html", c.get("/"))
        raccolta["stato"] = ("stato.html", c.get(f"/guilds/{GUILD_TODAY}"))
        raccolta["robustezza"] = ("robustezza.html", c.get(f"/guilds/{GUILD_TODAY}/robustezza"))
        raccolta["community"] = ("community.html", c.get(f"/guilds/{GUILD_TODAY}/community"))
        raccolta["coorti"] = ("coorti.html", c.get(f"/guilds/{GUILD_TODAY}/coorti"))
        # 123 e' nell'insieme autorizzato ma il fixture non lo osserva: la
        # guardia passa e l'API risponde 404. E' la pagina del difetto 1c.
        raccolta["non_osservata"] = ("non_osservata.html", c.get("/guilds/123"))
        raccolta["negato_server"] = (
            "accesso_negato.html", c.get(f"/guilds/{FUORI_DALL_INSIEME}")
        )
        # verifica_fallita senza passare da Discord: uno ``state`` che non e'
        # quello del flusso nega prima di qualunque chiamata.
        raccolta["negato_verifica"] = (
            "accesso_negato.html",
            c.get("/oauth/callback", params={"code": "x", "state": "non-mio"}),
        )

    # Senza cookie: la pagina d'accesso, che per definizione non ha sessione.
    with TestClient(app_di_test(), follow_redirects=False) as c:
        raccolta["accesso"] = ("accesso.html", c.get("/login"))

    with TestClient(app_di_test(), follow_redirects=False) as c:
        entra(c, dati_di_sessione(login_at=time.time() - auth.DURATA_SESSIONE_SECONDI - 1))
        raccolta["negato_scaduta"] = ("accesso_negato.html", c.get("/"))

    with TestClient(app_di_test(), follow_redirects=False) as c:
        entra(c, dati_di_sessione(guilds=[]))
        raccolta["negato_nessun_server"] = ("accesso_negato.html", c.get("/"))

    with client_autenticato(
        app_di_test(httpx.MockTransport(_api_giu)), follow_redirects=False
    ) as c:
        raccolta["errore_api_giu"] = ("errore.html", c.get(f"/guilds/{GUILD_TODAY}"))

    transport = httpx.MockTransport(_api_fuori_contratto)
    with client_autenticato(app_di_test(transport), follow_redirects=False) as c:
        raccolta["errore_contratto"] = ("errore.html", c.get(f"/guilds/{GUILD_TODAY}"))

    return raccolta
