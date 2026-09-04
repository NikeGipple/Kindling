"""Soglia minima di cardinalita' N, applicata qui e non nella dashboard.

`metriche-aggregate-admin.md` e' esplicito: le tabelle lette dall'API devono
gia' contenere solo aggregati sopra soglia, cosi' nessun layer di presentazione
futuro puo' bucare la regola per errore. Questo modulo e' quel punto.

Due regole che il codice deve rispettare alla lettera:

- **Mai uno zero.** Zero e' un valore legittimo e diverso da "non mostrabile",
  e una tabella che li confonde e' una tabella in cui il valore soppresso e'
  indistinguibile da un risultato reale. Una cella soppressa e' NULL piu' un
  flag.
- **"Colonna di valore" = ogni colonna fuori dalla chiave primaria.** Non solo
  quelle che sembrano un risultato: la numerosita' si ricava per differenza o
  per divisione da quasi tutte — ``nodes_removed`` e' ``ceil(X*n)`` con X in
  chiave, ``event_count + censored_count`` e' la dimensione della coorte,
  ``giant_before`` e' una frazione con n al denominatore.

Per questo l'azzeramento e' generico: si costruisce dai campi della dataclass
tenendo la chiave, non da un elenco di colonne da ricordare. E' la stessa forma
del trigger che impone il vincolo nel database (migrations/0006_metrics.sql):
enumerare le colonne pericolose e' un elenco che ci si dimentica di aggiornare
il giorno in cui se ne aggiunge una.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional, TypeVar

from .communities import CommunitySize

REASON_BELOW_THRESHOLD = "below_threshold"
REASON_SECONDARY = "secondary"

# Colonne che sopravvivono alla soppressione insieme alla chiave primaria.
# details resta '{}' e non NULL: un JSONB vuoto non pubblica niente, e la
# colonna ha un default che serve a tutte le altre righe.
_ALWAYS_KEPT = ("is_suppressed", "suppression_reason", "details")

Row = TypeVar("Row")


def suppress(row: Row, *, reason: str = REASON_BELOW_THRESHOLD) -> Row:
    """Azzera la riga lasciando solo chiave, flag e ragione."""
    kept = set(getattr(row, "KEY_FIELDS")) | set(_ALWAYS_KEPT)
    for field in dataclasses.fields(row):
        if field.name in kept:
            continue
        setattr(row, field.name, None)
    row.is_suppressed = True
    row.suppression_reason = reason
    if hasattr(row, "details"):
        row.details = {}
    return row


def apply_threshold(row: Row, *, count: Optional[int], threshold: int) -> Row:
    """Sopprime la riga se ``count`` e' sotto soglia.

    ``count`` e' la numerosita' della cella piu' fine effettivamente scritta —
    la combinazione completa della chiave primaria — non il totale della
    dimensione superiore.
    """
    if count is None or count < threshold:
        return suppress(row)
    return row


def apply_secondary_suppression(
    rows: list[CommunitySize], *, threshold: int
) -> list[CommunitySize]:
    """Soppressione primaria e secondaria della distribuzione delle community.

    Quando in un gruppo di righe che condividono un totale pubblicato **una
    sola** cella e' soppressa, quella cella si ricava per differenza dal totale
    e la soppressione non ha protetto niente: se ne sopprime allora anche una
    seconda, la piu' piccola tra le restanti.

    E' l'unico posto in v0 in cui la regola si applica, perche' e' l'unico in
    cui un totale e' pubblicato accanto alle sue parti
    (``metric_communities.n_effective`` e' la somma dei membri dei bucket).
    """
    for row in rows:
        apply_threshold(row, count=row.member_count, threshold=threshold)

    suppressed = [row for row in rows if row.is_suppressed]
    remaining = [row for row in rows if not row.is_suppressed]
    if len(suppressed) == 1 and remaining:
        victim = min(remaining, key=lambda row: (row.member_count, row.bucket))
        suppress(victim, reason=REASON_SECONDARY)
    return rows


def as_public_dict(row: Any) -> dict[str, Any]:
    """La riga come finisce in tabella, per il log di ``--dry-run``.

    Nessun id, nessuno pseudonimo, nessuna etichetta di nodo: ``--dry-run``
    stampa esattamente gli stessi aggregati che scriverebbe, non e' una
    scorciatoia per guardare i dati per-nodo senza scriverli.
    """
    return {
        field.name: getattr(row, field.name) for field in dataclasses.fields(row)
    }
