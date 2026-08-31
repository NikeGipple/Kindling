"""Pseudonimizzazione degli id per gli export destinati all'ispezione visiva.

Un export identificato del grafo e' una mappa sociale nominativa della
community: chi parla con chi, quanto, e chi sta ai margini. E' esattamente il
tipo di artefatto che non deve poter finire per sbaglio in un repository o in
una cartella condivisa. Di default quindi gli export portano id pseudonimi, e
quelli reali richiedono un flag esplicito.

Lo pseudonimo e' un hash con chiave (BLAKE2b keyed), non un hash semplice: gli
id Discord sono numeri in uno spazio piccolo e prevedibile, quindi un hash
senza salt e' invertibile per forza bruta in pochi secondi da chiunque abbia
una lista di id. Il salt vive in una variabile d'ambiente e non nel codice.

Lo stesso id produce lo stesso pseudonimo a parita' di salt, cosi' due export
successivi restano confrontabili nodo per nodo.
"""

from __future__ import annotations

import hashlib
import os

SALT_ENV_VAR = "KINDLING_PSEUDONYM_SALT"

# 8 byte = 16 caratteri esadecimali: collisioni trascurabili su qualche
# migliaio di nodi, e un'etichetta ancora leggibile in Gephi.
_DIGEST_BYTES = 8


class MissingSaltError(RuntimeError):
    """Il salt non e' configurato: meglio fallire che pseudonimizzare male."""


def load_salt() -> bytes:
    salt = os.environ.get(SALT_ENV_VAR, "").strip()
    if not salt:
        raise MissingSaltError(
            f"{SALT_ENV_VAR} non impostata. Senza salt l'hash di un id Discord e' "
            "invertibile per forza bruta, quindi l'export pseudonimo non sarebbe "
            "pseudonimo. Impostala nel .env (vedi .env.example) oppure, se l'export "
            "identificato e' voluto, passa --identified."
        )
    # BLAKE2b accetta una chiave fino a 64 byte: un salt piu' lungo viene
    # ridotto invece di far fallire l'export.
    return hashlib.blake2b(salt.encode("utf-8"), digest_size=64).digest()


def pseudonymize(author_id: int, key: bytes) -> str:
    return hashlib.blake2b(
        str(author_id).encode("ascii"), key=key, digest_size=_DIGEST_BYTES
    ).hexdigest()
