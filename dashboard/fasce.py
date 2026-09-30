"""Le sette fasce delle barre: una definizione sola per Coorti e Robustezza.

Le barre della dashboard mostrano la FASCIA di una frazione, non il valore: i
numeri esatti stanno nei Dettagli tecnici (dashboard.md 4, "La vista Coorti" e
"La vista Robustezza"). Fino al 30/09/2026 questa funzione viveva in
``coorti.py``; ora la usano due viste, e una copia nella seconda sarebbe una
soglia che diverge dalla prima al primo ritocco (CLAUDE.md 7). ``coorti`` e
``robustezza`` la importano da qui.

L'indice e' la classe CSS (``fascia-0`` … ``fascia-6``, e per la tacca di
Robustezza ``tacca-0`` … ``tacca-6``): la CSP vieta ``style=""``, quindi una
larghezza e' una classe. La parola va nell'``aria-label``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FASCE = (
    "nessuno",
    "pochissimi",
    "pochi",
    "circa metà",
    "la maggior parte",
    "quasi tutti",
    "tutti",
)

# Le cifre con cui una frazione si arrotonda prima di confrontarla con i bordi.
# Il job non arrotonda, e un bordo interno arriva spesso APPENA SOTTO: 1 - 0.8 in
# Python e' 0.19999999999999996, e il fixture ha davvero 0.050000000000000044.
# Nove cifre assorbono l'errore di rappresentazione senza spostare una frazione
# vera: 0.99999 resta "quasi tutti".
_CIFRE_DI_CONFRONTO = 9


@dataclass(frozen=True)
class Fascia:
    """Una delle sette fasce: l'indice e' la classe CSS, la parola l'aria-label."""

    indice: int
    parola: str


def fascia(frazione: Optional[float]) -> Optional[Fascia]:
    """La fascia di una frazione, o ``None`` se la frazione non c'e'.

    Bordi: 0 e 1 sono fasce a se'; 0,2 e 0,8 aprono la fascia successiva;
    "circa meta'" comprende entrambi i suoi estremi, 0,4 e 0,6.
    """
    if frazione is None:
        return None
    f = round(frazione, _CIFRE_DI_CONFRONTO)
    if f <= 0:
        indice = 0
    elif f >= 1:
        indice = 6
    elif f < 0.2:
        indice = 1
    elif f < 0.4:
        indice = 2
    elif f <= 0.6:
        indice = 3
    elif f < 0.8:
        indice = 4
    else:
        indice = 5
    return Fascia(indice, FASCE[indice])
