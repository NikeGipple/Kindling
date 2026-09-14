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
