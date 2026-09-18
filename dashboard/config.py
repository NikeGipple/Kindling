"""Configurazione della dashboard, tutta in un posto solo.

**L'indirizzo dell'API e' configurazione, non codice** (dashboard.md 1).
``KINDLING_API_BASE_URL`` si legge dall'ambiente all'avvio, e non esiste un
default: nemmeno ``http://api:8000`` "tanto in produzione e' sempre quello".
Puntata al fixture si sviluppa, puntata all'API si va in produzione, e non cambia
nient'altro. Un indirizzo scritto qui renderebbe il fixture inutilizzabile.

**Nessun ``load_dotenv()``, a differenza dell'API, e non e' una svista.** Il
``.env`` del progetto contiene ``DATABASE_URL`` e ``API_DATABASE_URL``: caricarlo
metterebbe nel processo della dashboard proprio le variabili che dashboard.md 1
vuole che il processo *non abbia*. In locale la variabile si esporta nella shell;
nel container la passa il compose, e solo quella.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Le chiamate all'API partono dalla stessa macchina, verso tabelle minuscole: un
# timeout lungo non aspetta una risposta lenta, trattiene una pagina bloccata.
API_TIMEOUT_SECONDS = 5.0

# Le variabili che il processo della dashboard non deve AVERE, non solo non
# usare (dashboard.md 1). Il controllo all'avvio e' la versione in-processo della
# verifica che dashboard.md 1 affida a ops/kindling-deploy.sh: una variabile
# aggiunta per comodita' durante un debug e mai tolta e' esattamente il genere di
# cosa che nessuno rilegge.
DATABASE_VARIABLES = ("DATABASE_URL", "API_DATABASE_URL")


def api_base_url() -> str:
    """L'indirizzo base dell'API, senza ``/`` finale. Nessun default."""
    url = os.environ.get("KINDLING_API_BASE_URL", "").strip()
    if not url:
        # Vuota e assente sono lo stesso errore: il compose passa
        # ${KINDLING_API_BASE_URL:-}, quindi una variabile dimenticata nel .env
        # arriva qui come stringa vuota, non come assenza.
        raise RuntimeError(
            "KINDLING_API_BASE_URL non impostata. E' l'indirizzo dell'API "
            "(in sviluppo il fixture, es. uvicorn tools.fixture_api:app --port 8899). "
            "Nessun default, di proposito: vedi docs/architettura/dashboard.md 1."
        )
    parti = urlsplit(url)
    if parti.scheme not in ("http", "https") or not parti.netloc:
        raise RuntimeError(
            f"KINDLING_API_BASE_URL non e' un indirizzo http(s) valido: {url!r}"
        )
    return url.rstrip("/")


def assert_no_database_variables() -> None:
    """Rifiuta di partire se il processo ha una variabile di database.

    Anche vuota: la regola e' che il container non le riceva, e una riga
    ``DATABASE_URL=`` nell'environment e' gia' una variabile ricevuta — il primo
    passo perche' qualcuno ci metta un valore "solo per provare".
    """
    presenti = [nome for nome in DATABASE_VARIABLES if nome in os.environ]
    if presenti:
        raise RuntimeError(
            "La dashboard ha ricevuto variabili di database: "
            + ", ".join(presenti)
            + ". Parla solo con l'API, mai con Postgres (docs/architettura/dashboard.md 1): "
            "toglile dall'environment del servizio."
        )


# --- OAuth2 Discord e sessione (dashboard.md 3, dashboard-fase2.md) -----------
#
# Quattro variabili, tutte obbligatorie e tutte senza default, nello stesso stile
# di KINDLING_API_BASE_URL: una configurazione mancante ferma il processo
# all'avvio con un messaggio che la nomina, invece di produrre un login rotto
# alla prima visita.

# Sotto questa lunghezza la chiave di firma della sessione si indovina: la regola
# e' `openssl rand -hex 32` (64 caratteri), e 32 e' il pavimento, non l'obiettivo.
SESSION_SECRET_MIN_LENGTH = 32

# Gli unici host su cui la redirect URI puo' essere http: la macchina di chi
# sviluppa. Ovunque altrove http vorrebbe dire cookie di sessione in chiaro.
_HOST_LOCALI = ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    session_secret: str = field(repr=False)
    redirect_uri: str

    @property
    def cookie_secure(self) -> bool:
        """Cookie ``Secure`` se e solo se la redirect URI e' https.

        Derivato, non una variabile a parte: una variabile in piu' e' una
        variabile che qualcuno dimentica impostata male in produzione. La
        combinazione pericolosa (http su un host pubblico) non arriva fin qui:
        la rifiuta ``oauth_redirect_uri``.
        """
        return urlsplit(self.redirect_uri).scheme == "https"


def _obbligatoria(nome: str) -> str:
    valore = os.environ.get(nome, "").strip()
    if not valore:
        # Vuota e assente sono lo stesso errore, come per KINDLING_API_BASE_URL:
        # il compose passa ${NOME:-}.
        raise RuntimeError(
            f"{nome} non impostata. Nessun default, di proposito: vedi .env.example "
            "e docs/architettura/dashboard-fase2.md."
        )
    return valore


def oauth_redirect_uri() -> str:
    """La redirect URI registrata su Discord. https, oppure http solo in locale."""
    uri = _obbligatoria("KINDLING_OAUTH_REDIRECT_URI")
    parti = urlsplit(uri)
    if parti.scheme == "https" and parti.hostname:
        return uri
    if parti.scheme == "http" and parti.hostname in _HOST_LOCALI:
        return uri
    raise RuntimeError(
        f"KINDLING_OAUTH_REDIRECT_URI deve essere https (o http su localhost): {uri!r}. "
        "Una redirect URI http su un host pubblico manderebbe il cookie di sessione "
        "in chiaro, e non deve essere possibile per distrazione."
    )


def session_secret() -> str:
    segreto = _obbligatoria("KINDLING_SESSION_SECRET")
    if len(segreto) < SESSION_SECRET_MIN_LENGTH:
        raise RuntimeError(
            f"KINDLING_SESSION_SECRET e' troppo corta ({len(segreto)} caratteri, "
            f"minimo {SESSION_SECRET_MIN_LENGTH}). Si genera con `openssl rand -hex 32`."
        )
    return segreto


def oauth_da_ambiente() -> OAuthConfig:
    return OAuthConfig(
        client_id=_obbligatoria("KINDLING_DISCORD_CLIENT_ID"),
        client_secret=_obbligatoria("KINDLING_DISCORD_CLIENT_SECRET"),
        session_secret=session_secret(),
        redirect_uri=oauth_redirect_uri(),
    )
