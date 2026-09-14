"""Il motore di rendering della qualificazione: dashboard.md 5, in codice.

**La firma e' ``cella(row, campo)``, mai ``cella(valore)``.** Una funzione che
riceve un float non puo' sapere se quel float va mostrato: per arrivare al numero
bisogna passare da ``values``, e ``quality`` e' li' come suo fratello (api.md 3).
Qui la funzione riceve la riga intera, e il numero lo estrae lei.

**I nomi dei campi sono quelli di ``api/models.py``**, non quelli del database ne'
della prosa: ``quality.suppressed`` e ``quality.significant`` senza prefisso;
``is_survivors_only`` con il prefisso, e solo nelle coorti; ``is_computable`` e
``not_computable_reason`` in ``values``, non in ``quality``. Gli accessi sono ad
attributo e i flag non universali si leggono solo dopo un ``isinstance`` sul
modello: nessun ``getattr(q, "is_suppressed", False)``, che su un nome sbagliato
restituirebbe il default senza nessun errore.

**Gli stati si compongono in un ordine** (dashboard.md 5):

1. *Il numero esiste?* ``suppressed`` e' terminale per la riga — nessun'altra
   etichetta, perche' non c'e' nient'altro da mostrare. ``is_computable is
   False`` e' terminale per ``retained_fraction``, non per la riga.
2. *Se esiste: quanto vale, e chi sta contando?* ``significant`` e
   ``is_survivors_only`` rispondono a domande diverse e si mostrano INSIEME,
   come due etichette distinte. Su una coorte anteriore all'ancora la coppia
   "non significativo" + "solo sopravvissuti" e' la forma normale, non un caso
   limite (``job/cohorts.py``: ``is_significant = not reasons``).

``quality.significant`` ha TRE stati, e solo uno produce un'etichetta: ``False``
"non significativo". ``True`` non ne porta, e ``None`` ("non valutato") nemmeno,
MAI — dashboard.md 5, "Perche' 'non valutato' non ha rendering": sulle righe
soppresse la soppressione e' terminale, e sulle tabelle senza colonna
``is_significant`` la significativita' non e' un concetto. Cinque stati, quattro
rendering. Il confronto resta ``is False`` e non ``not``: collassare ``None`` in
``False`` metterebbe l'etichetta "non significativo" su ogni numero di retention.

**``quality.details`` non si legge qui** (dashboard.md 5, regola 2): e'
diagnostica, non contratto, e non puo' essere sorgente di un'etichetta ne' di una
condizione. Per questo l'etichetta "non significativo" non porta il perche': il
perche' sta in ``details``, che la vista potra' mostrare solo come testo
letterale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel

from api.models import CohortQuality, OnboardingValues, Quality, RetentionValues

# --- esiti: cosa c'e' al posto del numero -----------------------------------

VALORE = "valore"
SOPPRESSO = "soppresso"
NON_CALCOLABILE = "non_calcolabile"
# Il sesto stato: median_reached=False. Non e' un'assenza, e' un risultato.
MEDIANA_NON_RAGGIUNTA = "mediana_non_raggiunta"
# Un None su una riga non soppressa, senza un flag tipizzato che lo spieghi
# (es. targeted_z con baseline degenere, stability_jaccard senza snapshot
# precedente). Non e' nessuno dei cinque stati, e non deve somigliare a nessuno:
# in particolare non al simbolo di soppressione.
ASSENTE = "assente"

# --- etichette: cosa accompagna un numero che esiste ------------------------

NON_SIGNIFICATIVO = "non_significativo"
# Uno stato senza rendering: nessuna cella lo porta, e un test lo verifica su ogni
# scenario del fixture. Resta nominato perche' un'assenza verificata vale piu' di
# un'assenza presunta (dashboard.md 5): se servira' mostrarlo, si decide in spec
# e si cambia quel test, non si reintroduce un'etichetta di passaggio.
NON_VALUTATO = "non_valutato"
SOLO_SOPRAVVISSUTI = "solo_sopravvissuti"

# Flag che qualificano un altro campo di values: non sono valori da rendere.
# Chiedere cella(row, "is_computable") e' quasi certamente un errore di chi
# chiama, e restituirne il booleano come numero lo nasconderebbe.
_QUALIFICATORI_PUNTUALI = frozenset({"median_reached", "is_computable", "not_computable_reason"})

# Traduzioni dei codici che il job scrive davvero (job/suppression.py,
# job/cohorts.py). Un codice che non e' qui si mostra letterale, mai nascosto:
# un motivo sconosciuto e' comunque un motivo.
MOTIVI_SOPPRESSIONE = {
    "below_threshold": "troppo poche persone per mostrare il dato",
    "secondary": "soppressione secondaria: il dato si ricaverebbe per differenza da una cella gia' soppressa",
}
MOTIVI_NON_CALCOLABILE = {
    "before_observability_anchor": "coorte anteriore all'inizio dell'osservazione",
    "empty_cohort": "coorte vuota",
    "horizon_not_reached": "l'orizzonte non e' ancora trascorso per tutta la coorte",
}


@dataclass(frozen=True)
class Etichetta:
    tipo: str
    testo: str


@dataclass(frozen=True)
class Cella:
    """Cosa mostrare per un campo di una riga. Il template decide solo l'aspetto.

    ``testo`` non e' mai vuoto: una cella vuota e' il rendering che dashboard.md 5
    vieta per la soppressione, e che qui non si produce per nessuno stato.
    ``motivo`` e' il codice grezzo, ``spiegazione`` la sua traduzione.
    """

    esito: str
    testo: str
    etichette: tuple[Etichetta, ...] = ()
    motivo: Optional[str] = None
    spiegazione: Optional[str] = None

    @property
    def stati(self) -> frozenset[str]:
        """Esito ed etichette insieme: tutti gli stati in cui sta la cella."""
        return frozenset({self.esito, *(e.tipo for e in self.etichette)})


def cella(row: BaseModel, campo: str) -> Cella:
    """La cella di ``campo`` nella riga ``row`` (una riga con ``quality`` e ``values``)."""
    quality = getattr(row, "quality", None)
    values = getattr(row, "values", None)
    if not isinstance(quality, Quality) or not isinstance(values, BaseModel):
        raise TypeError(
            f"cella() vuole una riga di metrica con quality e values, non {type(row).__name__}"
        )
    if campo not in type(values).model_fields:
        # Un nome sbagliato deve fallire qui, rumorosamente: e' il punto in cui
        # un accesso scritto dalla prosa diventerebbe un None silenzioso.
        raise KeyError(f"{type(values).__name__} non ha il campo {campo!r}")
    if campo in _QUALIFICATORI_PUNTUALI:
        raise ValueError(f"{campo!r} qualifica un altro valore, non e' un valore da mostrare")

    # 1a. Terminale per la riga: nessun'altra etichetta.
    if quality.suppressed:
        return Cella(
            esito=SOPPRESSO,
            testo="soppresso",
            motivo=quality.suppression_reason,
            spiegazione=_traduci(quality.suppression_reason, MOTIVI_SOPPRESSIONE),
        )

    # 1b. Terminale per QUESTO valore, non per la riga.
    if (
        isinstance(values, RetentionValues)
        and campo == "retained_fraction"
        and values.is_computable is False
    ):
        return Cella(
            esito=NON_CALCOLABILE,
            testo="non calcolabile",
            motivo=values.not_computable_reason,
            spiegazione=_traduci(values.not_computable_reason, MOTIVI_NON_CALCOLABILE),
        )

    # 2. Il numero esiste (o e' un risultato): quanto vale e chi sta contando.
    etichette = _etichette_di_riga(quality)

    if (
        isinstance(values, OnboardingValues)
        and campo == "median_days_to_k"
        and values.median_reached is False
    ):
        k = getattr(row, "k", None)
        soglia = f"{k} connessioni" if k is not None else "le k connessioni"
        return Cella(
            esito=MEDIANA_NON_RAGGIUNTA,
            testo=f"meno di metà della coorte ha raggiunto {soglia}",
            etichette=etichette,
        )

    valore = getattr(values, campo)
    if valore is None:
        return Cella(esito=ASSENTE, testo="non disponibile", etichette=etichette)
    return Cella(esito=VALORE, testo=formatta(valore), etichette=etichette)


def _etichette_di_riga(quality: Quality) -> tuple[Etichetta, ...]:
    etichette: list[Etichetta] = []

    # `is False`, non `not`: un `if not quality.significant` metterebbe None nello
    # stesso ramo di False. None non produce nessuna etichetta (vedi docstring).
    if quality.significant is False:
        etichette.append(Etichetta(NON_SIGNIFICATIVO, "non significativo"))

    # Solo le coorti hanno il flag: isinstance, non getattr con default.
    if isinstance(quality, CohortQuality) and quality.is_survivors_only is True:
        etichette.append(
            Etichetta(
                SOLO_SOPRAVVISSUTI,
                "solo sopravvissuti: conta chi era ancora presente all'inizio "
                "dell'osservazione, non chi è entrato",
            )
        )
    return tuple(etichette)


def _traduci(codice: Optional[str], tabella: dict[str, str]) -> str:
    if codice is None:
        return "motivo non registrato"
    return tabella.get(codice, codice)


def formatta(valore: Any) -> str:
    """Un valore numerico in forma leggibile, con la virgola decimale.

    Il segno si conserva sempre: ``targeted_excess`` puo' essere negativo e non e'
    clampato (dashboard.md 5).
    """
    if isinstance(valore, bool):
        # bool e' sottoclasse di int: senza questo ramo True diventerebbe "1".
        return "sì" if valore else "no"
    if isinstance(valore, int):
        return str(valore)
    if isinstance(valore, float):
        testo = f"{valore:.3f}".rstrip("0")
        if testo.endswith("."):
            testo += "0"
        return testo.replace(".", ",")
    return str(valore)
