"""Robustezza: dalle righe dell'API alla vista, e alla tabella dei Dettagli tecnici.

Tutto cio' che si DECIDE sta qui, in funzioni pure; i template decidono solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4 "La
vista Robustezza, in dettaglio" e §5.

**Due forme, due lettori** (riscrittura del 30/09/2026, come Coorti il 26/09):

- ``pagina()`` costruisce la VISTA, per chi amministra il server: una tabella,
  una riga per tipo di interazione in uno di cinque stati, e sulle righe
  leggibili una barra e una tacca per frazione, sulle sette fasce di
  ``fasce.py``. Sotto ``min_nodes_structural`` nessun risultato della
  simulazione: la ragione e' la leggibilita', non la protezione.
- ``tecnica()`` costruisce la TABELLA dei Dettagli tecnici, che fino al 30/09
  era la vista: un blocco per layer, tre righe per frazione con ``cella()``, e la
  serie per snapshot in tabella.

**Uno stato di riga, una funzione.** ``_riga`` decide lo stato di un layer in uno
snapshot; la usano la vista, le settimane precedenti e la pillola di Stato
(``lettura``). Fino al 30/09 Stato decideva da ``quality.significant`` mentre la
vista decide da ``n_effective`` contro la soglia: sopra soglia una cella con
baseline degenere e' leggibile e non significativa, e le due pagine avrebbero
detto cose diverse senza nessun errore (CLAUDE.md 7).

**Le soglie vengono dai ``params`` della run che ha scritto QUELLO snapshot**, mai
da ``job/config.py`` e mai con un valore di ripiego: chiave mancante, frase che
non la nomina.

``quality.details`` non si legge in questo modulo, per nessuna ragione (regola 2).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from api.models import RobustnessRow, RunRow
from job.config import ALL_LAYERS, MetricParams

from . import stato as _stato
from .fasce import Fascia, fascia
from .qualifica import precisione_colonna

# I layer del job, importati e non ricopiati: la vista ha una riga per ogni layer
# che il job PUO' calcolare, anche quando l'API non ne porta righe — e' l'unico
# modo di distinguere "nessuna interazione" da "layer mai calcolato".
LAYERS = ALL_LAYERS
# Le frazioni del job, per la tabella tecnica: li' un blocco ha una riga per
# frazione presente. La vista invece prende le colonne dalle righe (``_frazioni``).
REMOVAL_FRACTIONS = MetricParams().removal_fractions

# I nomi dei tipi di interazione, per chi amministra: le cose che succedono su
# Discord, non i nomi dei layer (dashboard.md 4). Li usa anche Community.
NOMI_LAYER = {
    "voice": "In vocale",
    "reply": "Risposte",
    "mention": "Menzioni",
    "reaction": "Reazioni",
}

# Gli stati di una riga della vista, piu' il ramo difensivo delle righe discordi.
NESSUNA = "nessuna"
SOTTO_SOGLIA = "sotto_soglia"
IN_OSSERVAZIONE = "in_osservazione"
LEGGIBILE = "leggibile"
DISCORDE = "discorde"

# Quanto vicino a un intero deve stare 1 / removal_fraction per scriverlo "1 su
# N". 1 / 0.05 e' 20.0 esatto in Python, ma una frazione letta da un JSON puo'
# portare l'errore di rappresentazione: la tolleranza delle fasce.
_TOLLERANZA_INTERO = 1e-9


def percentuale(removal_fraction: float) -> str:
    """``0.05`` -> ``5%``. Da usare SOLO accanto ai nodi rimossi (regola 6)."""
    return f"{removal_fraction * 100:g}%"


def nodi_rimossi(n: int) -> str:
    return "1 nodo rimosso" if n == 1 else f"{n} nodi rimossi"


def intestazione_frazione(removal_fraction: float) -> str:
    """``0.05`` -> ``1 su 20``, se ``1 / removal_fraction`` e' intero.

    Altrimenti la percentuale: una frazione che non e' "una persona ogni N" non
    si arrotonda in silenzio a un "1 su 7" (dashboard.md 4). In entrambi i casi
    la cella porta accanto "senza N persone" (regola 6).
    """
    if removal_fraction > 0:
        inverso = 1 / removal_fraction
        if abs(inverso - round(inverso)) < _TOLLERANZA_INTERO:
            return f"1 su {round(inverso)}"
    return percentuale(removal_fraction)


def senza_persone(n: int) -> str:
    return "senza 1 persona" if n == 1 else f"senza {n} persone"


def _intero(params: Mapping[str, Any], chiave: str) -> Optional[int]:
    """Una soglia intera da ``params``, o ``None``: chiave mancante o di forma
    inattesa, e la frase che la citava non la cita."""
    valore = params.get(chiave)
    if isinstance(valore, bool) or not isinstance(valore, int):
        return None
    return valore


# --- struttura comune -------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    as_of: datetime


def _snapshot(righe: Sequence[RobustnessRow]) -> list[Snapshot]:
    """Gli snapshot delle righe, dal piu' vecchio al piu' recente.

    Per ``(as_of, snapshot_id)``: due snapshot possono pareggiare su ``as_of``
    (CLAUDE.md 7, il caso di ``api/db.py``), e senza tiebreaker "il piu' recente"
    dipenderebbe dall'ordine delle righe.
    """
    per_id: dict[int, Snapshot] = {}
    for r in righe:
        per_id.setdefault(r.snapshot_id, Snapshot(r.snapshot_id, r.as_of))
    return sorted(per_id.values(), key=lambda s: (s.as_of, s.snapshot_id))


def _per_layer(
    righe: Sequence[RobustnessRow],
) -> dict[tuple[int, str], list[RobustnessRow]]:
    per: dict[tuple[int, str], list[RobustnessRow]] = {}
    for r in righe:
        per.setdefault((r.snapshot_id, r.layer), []).append(r)
    for lista in per.values():
        lista.sort(key=lambda r: r.removal_fraction)
    return per


def _params(runs: Sequence[RunRow], snapshot: Snapshot) -> Mapping[str, Any]:
    """I ``params`` della run che ha scritto QUESTO snapshot, o niente.

    Non "l'ultima run": i params sono quelli con cui questi numeri sono stati
    calcolati, e una soglia cambiata dopo non li riqualifica.
    """
    candidate = [r for r in runs if r.snapshot_id == snapshot.snapshot_id]
    return max(candidate, key=lambda r: r.as_of).params if candidate else {}


# --- la vista ----------------------------------------------------------------


@dataclass(frozen=True)
class Cella:
    """Una cella di frazione su una riga leggibile.

    ``barra`` e ``tacca`` a ``None``: la riga non porta quella frazione. Succede
    solo se la griglia cambia fra due run; la cella lo dice invece di disegnare
    una barra vuota, che si leggerebbe "nessuno".
    """

    removal_fraction: float
    etichetta: str  # "1 su 20": data-etichetta, per le schede su telefono
    barra: Optional[Fascia] = None
    tacca: Optional[Fascia] = None
    rimossi: Optional[int] = None

    @property
    def quanti(self) -> str:
        return senza_persone(self.rimossi) if self.rimossi is not None else ""

    @property
    def descrizione(self) -> str:
        """L'``aria-label``: le due fasce, con il numero di persone tolte."""
        if self.barra is None or self.tacca is None or self.rimossi is None:
            return "non calcolata"
        k = self.rimossi
        centrali = "senza la più centrale" if k == 1 else f"senza le {k} più centrali"
        return (
            f"{centrali}: {self.barra.parola}; "
            f"{senza_persone(k)} qualunque: {self.tacca.parola}"
        )


@dataclass(frozen=True)
class Riga:
    layer: str
    nome: str
    stato: str
    persone: Optional[int] = None
    # "sett. prima 15": solo quando c'e' un numero (dashboard.md 4).
    prima: Optional[str] = None
    frase: Optional[str] = None
    collegati: Optional[Fascia] = None
    celle: tuple[Cella, ...] = ()


@dataclass(frozen=True)
class Storia:
    """Le settimane di un tipo di interazione, dalla piu' recente."""

    layer: str
    nome: str
    righe: tuple[tuple[str, Riga], ...]  # (settimana, riga)


@dataclass(frozen=True)
class Pagina:
    vuota: bool
    settimana: str = ""
    frazioni: tuple[tuple[float, str], ...] = ()
    righe: tuple[Riga, ...] = ()
    soglia: Optional[int] = None
    avviso: Optional[str] = None
    storia: tuple[Storia, ...] = ()

    @property
    def per_tipo(self) -> tuple[tuple[str, Riga], ...]:
        """Le righe della settimana chiusa come le vuole la tabella: (nome, riga)."""
        return tuple((r.nome, r) for r in self.righe)


def _frazioni(righe: Sequence[RobustnessRow]) -> tuple[float, ...]:
    """Le frazioni presenti nelle righe, ordinate.

    Dalle righe e non da ``job/config.py``: una riga con una frazione che la
    vista non conosce sparirebbe dalla pagina senza nessun errore (CLAUDE.md 7).
    """
    return tuple(sorted({r.removal_fraction for r in righe}))


def _riga(
    layer: str,
    righe: Sequence[RobustnessRow],
    params: Mapping[str, Any],
    frazioni: Sequence[float],
    *,
    nella_storia: bool = False,
) -> Riga:
    """Lo stato di un layer in uno snapshot, e cosa ne mostra la riga.

    L'ordine delle condizioni e' quello della tabella di dashboard.md 4: prima
    "non c'e' niente", poi "c'e' e non si mostra", poi le righe che non
    concordano, poi la soglia di lettura.
    """
    nome = NOMI_LAYER.get(layer, layer)
    if not righe:
        # Grafo vuoto: compute_robustness restituisce None e il job non scrive
        # righe. Non e' una soppressione, e non e' un layer mai calcolato.
        frase = (
            "nessuna interazione di questo tipo"
            if nella_storia
            else "nessuna interazione di questo tipo in questa settimana"
        )
        return Riga(layer, nome, NESSUNA, frase=frase)

    soppresse = [r.quality.suppressed for r in righe]
    if all(soppresse):
        pubblicazione = _intero(params, "min_nodes_publish")
        frase = (
            f"meno di {pubblicazione} persone attive: non mostrata"
            if pubblicazione is not None
            else "troppo poche persone attive: non mostrata"
        )
        return Riga(layer, nome, SOTTO_SOGLIA, frase=frase)

    valori_n = {r.quality.n_effective for r in righe}
    if any(soppresse) or len(valori_n) != 1:
        # Ramo difensivo (dashboard.md 5, "I rami difensivi"): soppressione e
        # n_effective sono per layer, uguali sulle tre frazioni. Se non lo sono,
        # non si sceglie una riga in silenzio.
        return Riga(
            layer, nome, DISCORDE,
            frase="i dati di questa settimana non concordano fra loro: "
                  "si leggono solo nei dettagli tecnici",
        )

    persone = valori_n.pop()
    soglia = _intero(params, "min_nodes_structural")
    if soglia is None or persone is None or persone < soglia:
        # Sotto la soglia nessun risultato della simulazione, per leggibilita':
        # con dodici persone "togliendone una si stacca meta' della rete" fa
        # pensare a qualcuno. Senza la chiave la riga resta in osservazione, e
        # la frase non inventa un numero.
        if soglia is None:
            frase = "in osservazione"
        elif nella_storia:
            frase = f"in osservazione · meno di {soglia} persone attive"
        else:
            frase = f"in osservazione · si legge da {soglia} persone attive in una settimana"
        return Riga(layer, nome, IN_OSSERVAZIONE, persone=persone, frase=frase)

    per_frazione = {r.removal_fraction: r for r in righe}
    celle = []
    for rf in frazioni:
        r = per_frazione.get(rf)
        if r is None:
            celle.append(Cella(rf, intestazione_frazione(rf)))
            continue
        v = r.values
        celle.append(Cella(
            rf, intestazione_frazione(rf),
            barra=fascia(v.giant_after_targeted),
            tacca=fascia(v.giant_after_random_mean),
            rimossi=v.nodes_removed,
        ))
    # giant_before e' lo stesso sulle tre frazioni: e' il grafo prima della
    # rimozione. Si legge dalla prima riga.
    return Riga(
        layer, nome, LEGGIBILE, persone=persone,
        collegati=fascia(righe[0].values.giant_before), celle=tuple(celle),
    )


def _prima(
    precedente: Snapshot, attuale: Snapshot, righe: Sequence[RobustnessRow]
) -> Optional[str]:
    """"sett. prima N", solo quando c'e' un numero (decisione del 30/09/2026).

    Niente se nel precedente il layer non c'e' o e' soppresso, o se il precedente
    non cade sette giorni prima: "settimana prima" su un buco di due settimane
    sarebbe falso. Le settimane si confrontano come etichette (``settimana``),
    cosi' una run anteriore all'ancoraggio al lunedi' (as_of alle 04:15) conta
    come la settimana che e'.
    """
    if not _stato.settimana_precedente(attuale.as_of, precedente.as_of) or not righe:
        return None
    if any(r.quality.suppressed for r in righe):
        return None
    valori = {r.quality.n_effective for r in righe}
    if len(valori) != 1:
        return None
    n = valori.pop()
    return f"sett. prima {n}" if n is not None else None


def _righe_dello_snapshot(
    per: Mapping[tuple[int, str], list[RobustnessRow]],
    snapshot: Snapshot,
    params: Mapping[str, Any],
    frazioni: Sequence[float],
    *,
    nella_storia: bool = False,
) -> list[Riga]:
    return [
        _riga(layer, per.get((snapshot.snapshot_id, layer), []), params, frazioni,
              nella_storia=nella_storia)
        for layer in LAYERS
    ]


def pagina(righe: Sequence[RobustnessRow], runs: Sequence[RunRow]) -> Pagina:
    """La vista Robustezza per chi amministra il server (dashboard.md 4)."""
    snapshot = _snapshot(righe)
    if not snapshot:
        return Pagina(vuota=True)
    ultimo = snapshot[-1]
    precedente = snapshot[-2] if len(snapshot) > 1 else None
    per = _per_layer(righe)
    frazioni = _frazioni(righe)
    params = _params(runs, ultimo)
    # L'anno delle date della pagina si confronta con questo as_of (stato.data_breve).
    riferimento = _stato.settimana(ultimo.as_of)

    principali = []
    for r in _righe_dello_snapshot(per, ultimo, params, frazioni):
        if precedente is not None and r.persone is not None:
            r = dataclasses.replace(
                r, prima=_prima(precedente, ultimo, per.get((precedente.snapshot_id, r.layer), []))
            )
        principali.append(r)

    storia: list[Storia] = []
    if len(snapshot) > 1:
        dal_piu_recente = list(reversed(snapshot))
        per_snapshot = {
            s.snapshot_id: _righe_dello_snapshot(
                per, s, _params(runs, s), frazioni, nella_storia=True
            )
            for s in dal_piu_recente
        }
        # La settimana coperta, non il lunedi' in cui si chiude (30/09/2026): per
        # ogni snapshot, il precedente nella pagina decide se l'intervallo si sa.
        etichette = {
            s.snapshot_id: _stato.etichetta_settimana(
                s.as_of, precedente.as_of if precedente else None, riferimento=riferimento
            )
            for precedente, s in zip([None, *snapshot[:-1]], snapshot)
        }
        for i, layer in enumerate(LAYERS):
            storia.append(Storia(
                layer, NOMI_LAYER.get(layer, layer),
                tuple((etichette[s.snapshot_id], per_snapshot[s.snapshot_id][i])
                      for s in dal_piu_recente),
            ))

    soglia = _intero(params, "min_nodes_structural")
    return Pagina(
        vuota=False,
        settimana=_stato.data_estesa(riferimento, riferimento=riferimento, con_giorno=True),
        frazioni=tuple((rf, intestazione_frazione(rf)) for rf in frazioni),
        righe=tuple(principali),
        soglia=soglia,
        avviso=_avviso(principali, storia, soglia),
        storia=tuple(storia),
    )


def _leggibile_prima(storia: Sequence[Storia]) -> bool:
    """Una settimana PRECEDENTE all'ultima ha almeno un tipo leggibile?

    La prima riga di ogni storia e' l'ultimo snapshot: le precedenti sono le altre.
    """
    return any(r.stato == LEGGIBILE for s in storia for _settimana, r in s.righe[1:])


def _avviso(
    principali: Sequence[Riga], storia: Sequence[Storia], soglia: Optional[int]
) -> Optional[str]:
    """Solo con zero tipi leggibili nell'ultimo snapshot, come Coorti dal 28/09.

    "Nell'ultima settimana" e non "per ora": dopo il ricalcolo del 30/09 in
    produzione le settimane precedenti hanno righe leggibili e l'ultima no, e
    "per ora nessun tipo e' leggibile" sopra quella storia sarebbe falso.
    """
    if any(r.stato == LEGGIBILE for r in principali):
        return None
    testo = "Nell'ultima settimana nessun tipo di interazione è leggibile."
    if soglia is not None:
        testo += (
            f" Serve che almeno {soglia} persone interagiscano in quel modo nella "
            "stessa settimana: sotto, il risultato dipende da una o due persone."
        )
    if _leggibile_prima(storia):
        testo += " Le settimane precedenti ne hanno: sono qui sotto."
    return testo


# --- la pillola di Stato -----------------------------------------------------


@dataclass(frozen=True)
class Lettura:
    """Lo stato della vista per "Cosa puoi leggere oggi", e la sua frase."""

    stato: str
    frase: str


def lettura(righe: Sequence[RobustnessRow], runs: Sequence[RunRow]) -> Lettura:
    """La pillola di Robustezza in Stato, dagli stessi stati di riga della vista.

    Guarda l'ultimo snapshot, come le altre pillole: nessuna riga → in raccolta;
    ogni layer presente soppresso → sotto la soglia; almeno un layer leggibile →
    leggibile; altrimenti con cautela. Le frasi sono della vista: "distinguibile
    dal caso", la frase generica di Stato, qui sarebbe falsa — sopra soglia una
    cella leggibile puo' essere non significativa, e la significativita' della
    robustezza non guarda targeted_z (dashboard.md 4).
    """
    vista = pagina(righe, runs)
    if vista.vuota:
        return Lettura(_stato.IN_RACCOLTA, _stato.FRASI_STATO[_stato.IN_RACCOLTA])
    stati = [r.stato for r in vista.righe]
    presenti = [s for s in stati if s != NESSUNA]
    if presenti and all(s == SOTTO_SOGLIA for s in presenti):
        return Lettura(_stato.SOTTO_SOGLIA, _stato.FRASI_STATO[_stato.SOTTO_SOGLIA])
    if LEGGIBILE in stati:
        return Lettura(
            _stato.LEGGIBILE,
            "Nell'ultima settimana almeno un tipo di interazione ha abbastanza "
            "persone attive per leggere la prova.",
        )
    frase = (
        "Nell'ultima settimana nessun tipo di interazione ha abbastanza persone "
        "attive per leggere la prova: la vista li mostra in osservazione."
    )
    if _leggibile_prima(vista.storia):
        frase += " Le settimane precedenti si leggono."
    return Lettura(_stato.CON_CAUTELA, frase)


# --- la tabella dei Dettagli tecnici ----------------------------------------


@dataclass
class Blocco:
    layer: str
    nome: str
    # Righe dello snapshot piu' recente, in ordine di frazione. Vuota = layer
    # assente in quello snapshot.
    righe: list[RobustnessRow]
    ultimo: Snapshot
    # La serie per snapshot, dal piu' recente; dentro lo snapshot, per frazione.
    # Riga None = in quello snapshot il layer non ha righe.
    tabella_serie: list[tuple[Snapshot, Optional[RobustnessRow]]]
    # Il layer non ha righe in nessuno snapshot mostrato.
    assente_ovunque: bool
    # Decimali per campo, calcolati su QUESTA tabella (dashboard.md 5, "La
    # precisione di una colonna numerica"): la tabella del blocco sulle sue righe,
    # la tabella della serie sulle sue. Mai sulla pagina: una precisione comune ai
    # quattro blocchi sarebbe una colonna che attraversa i layer.
    decimali: dict[str, int] = field(default_factory=dict)
    decimali_serie: dict[str, int] = field(default_factory=dict)

    @property
    def assente(self) -> bool:
        return not self.righe

    @property
    def tutto_soppresso(self) -> bool:
        return bool(self.righe) and all(r.quality.suppressed for r in self.righe)

    @property
    def n_effective(self) -> Optional[int]:
        """La dimensione del grafo del layer, una volta sola.

        Che le tre righe di un (snapshot, layer) la condividano e' vero per
        costruzione, ma e' un'assunzione: se non vale, non si sceglie una delle
        tre in silenzio (vedi ``n_incoerente``).
        """
        valori = {r.quality.n_effective for r in self.righe if not r.quality.suppressed}
        return valori.pop() if len(valori) == 1 else None

    @property
    def n_incoerente(self) -> bool:
        valori = {r.quality.n_effective for r in self.righe if not r.quality.suppressed}
        return len(valori) > 1

    @property
    def soppressione(self) -> Optional[RobustnessRow]:
        """Una riga soppressa rappresentativa, per il motivo del blocco interamente soppresso."""
        return self.righe[0] if self.tutto_soppresso else None


@dataclass
class Tecnica:
    snapshot: list[Snapshot]  # dal piu' vecchio al piu' recente
    blocchi: list[Blocco]

    @property
    def vuota(self) -> bool:
        """Guild osservata, nessuno snapshot: il calcolo non e' ancora girato."""
        return not self.snapshot


def tecnica(righe: Sequence[RobustnessRow]) -> Tecnica:
    """I quattro blocchi dei Dettagli tecnici, dalle righe di ``/robustness``.

    La serie e' sempre una tabella, da due snapshot in su: con uno solo
    duplicherebbe il blocco. Il grafico SVG della prima stesura non c'e' piu'
    (dashboard.md 4).
    """
    per_chiave: dict[tuple[int, str, float], RobustnessRow] = {
        (r.snapshot_id, r.layer, r.removal_fraction): r for r in righe
    }
    snapshot = _snapshot(righe)
    if not snapshot:
        return Tecnica(snapshot=[], blocchi=[])
    ultimo = snapshot[-1]

    blocchi = []
    for layer in LAYERS:
        righe_ultimo = [
            per_chiave[(ultimo.snapshot_id, layer, rf)]
            for rf in REMOVAL_FRACTIONS
            if (ultimo.snapshot_id, layer, rf) in per_chiave
        ]
        assente_ovunque = not any(
            (s.snapshot_id, layer, rf) in per_chiave for s in snapshot for rf in REMOVAL_FRACTIONS
        )
        tabella: list[tuple[Snapshot, Optional[RobustnessRow]]] = []
        if not assente_ovunque and len(snapshot) > 1:
            # Dal piu' recente; dentro lo snapshot, per frazione. Uno snapshot in
            # cui il layer non ha righe resta in tabella con None: se sparisse,
            # la serie sembrerebbe piu' corta invece che interrotta.
            for s in reversed(snapshot):
                presenti = [
                    per_chiave[(s.snapshot_id, layer, rf)]
                    for rf in REMOVAL_FRACTIONS
                    if (s.snapshot_id, layer, rf) in per_chiave
                ]
                tabella += [(s, r) for r in presenti] if presenti else [(s, None)]

        blocchi.append(
            Blocco(
                layer=layer,
                nome=NOMI_LAYER.get(layer, layer),
                righe=righe_ultimo,
                ultimo=ultimo,
                tabella_serie=tabella,
                assente_ovunque=assente_ovunque,
                decimali=decimali_per_campo(righe_ultimo, COLONNE_BLOCCO),
                decimali_serie=decimali_per_campo(
                    [r for _s, r in tabella if r is not None], COLONNE_SERIE
                ),
            )
        )
    return Tecnica(snapshot=snapshot, blocchi=blocchi)


# Le colonne numeriche che ciascuna tabella tecnica MOSTRA. Un gruppo di
# precisione vale solo tra colonne mostrate: tests/test_dashboard_robustezza.py
# confronta questi elenchi con le cella() del template, perche' un elenco che
# diverge dalla tabella vera applicherebbe il gruppo a colonne che non ci sono.
COLONNE_BLOCCO = (
    "nodes_removed",
    "giant_before",
    "giant_after_targeted",
    "giant_after_random_mean",
    "components_after_targeted",
    "targeted_excess",
    "targeted_z",
)
COLONNE_SERIE = ("nodes_removed", "targeted_excess")

# Colonne legate da un'operazione (dashboard.md 5): condividono la precisione piu'
# alta del gruppo, altrimenti la riga si contraddice da sola — due giganti resi
# entrambi 0,880 accanto a un eccesso di -0,0004.
#
#   targeted_excess = (giant_after_random_mean - giant_after_targeted) / giant_before
#
# targeted_z NON ne fa parte, e non per dimenticanza: il suo denominatore e'
# giant_after_random_sd, che nessuna tabella mostra, quindi z non si ricava dalle
# colonne visibili. Il criterio e' questo, non l'elenco: un gruppo esiste dove un
# numero mostrato si ottiene da altri numeri mostrati.
GRUPPI_CALCOLATI = (
    frozenset({"giant_before", "giant_after_targeted", "giant_after_random_mean", "targeted_excess"}),
)


def decimali_per_campo(righe: list[RobustnessRow], colonne: tuple[str, ...]) -> dict[str, int]:
    """La precisione di ogni colonna mostrata di una tabella, calcolata sulle SUE righe.

    Prima colonna per colonna (``precisione_colonna``), poi i gruppi calcolati:
    ogni gruppo, ristretto alle colonne che la tabella mostra, prende la
    precisione piu' alta tra le sue. Nella tabella della serie i giganti non ci
    sono, quindi il gruppo si riduce al solo eccesso e non cambia niente.

    Le righe soppresse non contano: i loro valori sono tutti None.
    """
    pubblicate = [r for r in righe if not r.quality.suppressed]
    decimali = {
        campo: precisione_colonna(getattr(r.values, campo) for r in pubblicate)
        for campo in colonne
    }
    for gruppo in GRUPPI_CALCOLATI:
        mostrate = gruppo & set(colonne)
        if len(mostrate) > 1:
            comune = max(decimali[c] for c in mostrate)
            for c in mostrate:
                decimali[c] = comune
    return decimali
