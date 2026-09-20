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

import itsdangerous
from fastapi.testclient import TestClient

from dashboard import auth
from dashboard.config import OAuthConfig
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_SCALE, GUILD_TODAY

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
