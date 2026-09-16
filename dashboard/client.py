"""Client HTTP verso l'API. L'unico canale da cui la dashboard riceve dati.

Le risposte si validano con i modelli di ``api/models.py``, gli stessi che l'API
usa per produrle: un campo rinominato lato API fa fallire la dashboard subito e
in modo riconoscibile, invece di far leggere ``None`` a un accesso scritto con il
nome vecchio — che per un flag come ``suppressed`` significherebbe mostrare una
riga soppressa come se non lo fosse.

Tre modi di non ottenere una risposta, e restano tre eccezioni distinte perche'
la pagina che ne risulta e' diversa (dashboard.md 5, regola 1: l'errore deve
avere un aspetto diverso da tutti gli stati di qualificazione):

- ``ApiNonRaggiungibile``: rete, timeout, 5xx. "Il sistema non risponde."
- ``RispostaNonConforme``: l'API risponde, ma non con il contratto atteso.
- ``GuildNonOsservata``: 404. Non e' un guasto: il bot quella guild non l'ha
  mai vista, ed e' diverso da una guild osservata e ancora senza metriche, che
  risponde con una serie vuota (api.md 2).
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from api.models import CommunityRow, GuildRow, Health, RobustnessRow, RunRow

from .config import API_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Lo stesso default dell'API (api/config.py): la vista Stato mostra lo storico
# recente, non tutto.
DEFAULT_RUNS_LIMIT = 12
# Il limite delle serie conta SNAPSHOT, non righe (api/db.py): dodici snapshot
# sono dodici settimane, ognuna con fino a 12 righe di robustezza o 4 di community.
DEFAULT_SERIES_LIMIT = 12


class ErroreApi(Exception):
    """Base degli errori di comunicazione con l'API."""


class ApiNonRaggiungibile(ErroreApi):
    pass


class RispostaNonConforme(ErroreApi):
    pass


class GuildNonOsservata(ErroreApi):
    def __init__(self, guild_id: int):
        super().__init__(f"guild_id {guild_id} non risulta osservata da Kindling")
        self.guild_id = guild_id


def crea_http(
    base_url: str, *, transport: Optional[httpx.AsyncBaseTransport] = None
) -> httpx.AsyncClient:
    """Il client httpx condiviso dall'applicazione.

    ``follow_redirects=False``: l'API non redirige mai, e un redirect seguito in
    silenzio porterebbe le richieste altrove senza che nessuno lo veda.
    ``transport`` esiste per i test, che montano il fixture senza rete.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=API_TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=transport,
    )


class ApiClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def _get(
        self,
        path: str,
        adapter: TypeAdapter[T],
        *,
        params: Optional[dict[str, Any]] = None,
        guild_id: Optional[int] = None,
    ) -> T:
        try:
            risposta = await self._http.get(path, params=params)
        except httpx.TransportError as exc:
            # TransportError copre connessione rifiutata, DNS, timeout: tutti
            # "l'API non c'e'", nessuno "l'API ha risposto male".
            logger.warning("API non raggiungibile su %s: %s", path, exc)
            raise ApiNonRaggiungibile(f"API non raggiungibile ({type(exc).__name__})") from exc

        if risposta.status_code == 404 and guild_id is not None:
            raise GuildNonOsservata(guild_id)
        if risposta.status_code >= 500:
            logger.warning("API in errore su %s: HTTP %s", path, risposta.status_code)
            raise ApiNonRaggiungibile(f"l'API ha risposto HTTP {risposta.status_code}")
        if risposta.status_code != 200:
            raise RispostaNonConforme(
                f"risposta inattesa da {path}: HTTP {risposta.status_code}"
            )

        try:
            return adapter.validate_json(risposta.content)
        except ValidationError as exc:
            logger.error("Risposta di %s fuori contratto: %s", path, exc)
            raise RispostaNonConforme(
                f"la risposta di {path} non rispetta il contratto di api/models.py"
            ) from exc

    async def health(self) -> Health:
        return await self._get("/health", TypeAdapter(Health))

    async def guilds(self) -> list[GuildRow]:
        return await self._get("/guilds", TypeAdapter(list[GuildRow]))

    async def guild(self, guild_id: int) -> GuildRow:
        return await self._get(
            f"/guilds/{guild_id}", TypeAdapter(GuildRow), guild_id=guild_id
        )

    async def runs(self, guild_id: int, *, limit: int = DEFAULT_RUNS_LIMIT) -> list[RunRow]:
        return await self._get(
            f"/guilds/{guild_id}/runs",
            TypeAdapter(list[RunRow]),
            params={"limit": limit},
            guild_id=guild_id,
        )

    async def robustness(
        self, guild_id: int, *, limit: int = DEFAULT_SERIES_LIMIT
    ) -> list[RobustnessRow]:
        return await self._get(
            f"/guilds/{guild_id}/robustness",
            TypeAdapter(list[RobustnessRow]),
            params={"limit": limit},
            guild_id=guild_id,
        )

    async def communities(
        self, guild_id: int, *, limit: int = DEFAULT_SERIES_LIMIT
    ) -> list[CommunityRow]:
        """Una chiamata sola: la stessa risposta alimenta i blocchi e la serie."""
        return await self._get(
            f"/guilds/{guild_id}/communities",
            TypeAdapter(list[CommunityRow]),
            params={"limit": limit},
            guild_id=guild_id,
        )
