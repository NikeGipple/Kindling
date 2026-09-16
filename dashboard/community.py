"""Vista Community: dalle righe dell'API ai blocchi e ai grafici da rendere.

Tutto cio' che la vista DECIDE sta qui, in funzioni pure; il template decide solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4 "La
vista Community, in dettaglio" e §5.

- **Due domande, una riga.** *Il layer e' organizzato in gruppi distinguibili dal
  rumore?* (struttura) e *quei gruppi sono gli stessi di sette giorni fa?*
  (stabilita'). Stanno nella stessa riga, raggruppate ma distinte, con una sola
  etichetta di riga: ``is_significant`` e' un flag solo.
- **Quattro blocchi, uno per layer**, come Robustezza, ma con una riga sola per
  blocco: ogni ``(snapshot, layer)`` produce una riga di ``metric_communities``.
- **I tre campi del confronto viaggiano insieme** (regola 5 estesa):
  ``previous_gap_days``, ``node_overlap`` e ``stability_jaccard`` compaiono
  tutti e tre o nessuno — nella riga, nella tabella della serie, nel ``<title>``
  e nella legenda del grafico.
- **La distribuzione delle dimensioni** ha tante righe quante le classi che il
  job ha scritto: mai sei righe fisse, mai "il resto e' N".
- **La serie** sono due grafici per layer, ``modularity_z`` (riferimento a 2,0,
  la soglia vera) e ``stability_jaccard`` (asse [0, 1], nessun riferimento: nel
  job non esiste una soglia su quel numero).

``quality.details`` non si legge in questo modulo, per nessuna ragione (regola 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from api.models import CommunityRow, CommunitySizeBucket
from job.config import MetricParams

from .qualifica import formatta, precisione_colonna
from .robustezza import (
    ALTEZZA,
    LARGHEZZA,
    LAYERS,
    MARGINE_ALTO,
    MARGINE_BASSO,
    MARGINE_DX,
    MARGINE_SX,
    MIN_PUNTI_LINEA,
    NOMI_LAYER,
    Segmento,
    Snapshot,
    TickY,
)

_PARAMS = MetricParams()
# La soglia che entra davvero in is_significant: e' per questo che il grafico di
# modularity_z la disegna. stability_jaccard non ha un equivalente nel job, e il
# suo grafico non ha riferimenti (dashboard.md 4, correzione del 16/09/2026).
SOGLIA_Z = _PARAMS.min_modularity_z
# L'ordine delle classi di dimensione del job, dalla piu' piccola. L'API le serve
# in ordine alfabetico ("10-19" prima di "5-9"): si riordina, non si aggiunge.
ORDINE_CLASSI = tuple(label for label, _low, _high in _PARAMS.community_size_buckets())

MODULARITA = "modularity_z"
STABILITA = "stability_jaccard"


def giorni(valore: Optional[float], decimali: Optional[int] = None) -> str:
    return f"{formatta(valore, decimali)} giorni" if valore is not None else "—"


# --- struttura ------------------------------------------------------------


@dataclass
class Punto:
    """Un punto di una serie: uno snapshot, per un campo, in un layer.

    ``riga`` e' None quando in quello snapshot il layer non ha righe (grafo
    vuoto). Un punto non disegnabile interrompe la linea, qualunque ne sia la
    ragione — soppressione, layer assente, o il valore che manca su una riga
    pubblicata (il "terzo caso" di dashboard.md 4) — ma nel grafico nessuna delle
    tre porta un simbolo: il simbolo di soppressione e' un fatto della cella.
    """

    snapshot: Snapshot
    riga: Optional[CommunityRow]
    campo: str
    x: float = 0.0
    y: float = 0.0
    decimali: Optional[int] = None

    @property
    def valore(self) -> Optional[float]:
        if self.riga is None or self.riga.quality.suppressed:
            return None
        return getattr(self.riga.values, self.campo)

    @property
    def disegnabile(self) -> bool:
        return self.valore is not None

    @property
    def dequalificato(self) -> bool:
        return self.disegnabile and self.riga.quality.significant is False  # type: ignore[union-attr]

    @property
    def titolo(self) -> str:
        """Il ``<title>`` del punto. Sulla stabilita' porta gap e sovrapposizione
        della SUA riga: regola 5 estesa, anche a richiesta."""
        riga = self.riga
        assert riga is not None and self.valore is not None
        parti = [f"{self.snapshot.as_of:%d/%m/%Y}"]
        if self.campo == STABILITA:
            v = riga.values
            parti += [
                f"stabilità {formatta(self.valore, self.decimali)}",
                f"gap {giorni(v.previous_gap_days)}",
                f"sovrapposizione {formatta(v.node_overlap) if v.node_overlap is not None else '—'}",
            ]
        else:
            parti.append(f"z {formatta(self.valore, self.decimali)}")
        return " · ".join(parti)


@dataclass
class Grafico:
    campo: str
    punti: list[Punto]
    segmenti: list[Segmento]
    ticks_y: list[TickY]
    etichette_x: list[tuple[float, str]]
    # Tutti i punti disegnati sono non significativi: etichetta UNA volta sola.
    tutto_non_significativo: bool
    # Caso misto: la legenda spiega obbligatoriamente lo stile dequalificato.
    misto: bool
    # y della soglia di modularity_z; None sul grafico della stabilita'.
    y_riferimento: Optional[float]
    etichetta_riferimento: Optional[str]
    # Solo sul grafico della stabilita': gap e sovrapposizione dei punti disegnati.
    legenda_confronto: Optional[str]
    decimali: int
    larghezza: int = LARGHEZZA
    altezza: int = ALTEZZA

    @property
    def disegnati(self) -> list[Punto]:
        return [p for p in self.punti if p.disegnabile]

    @property
    def congiunta(self) -> bool:
        """Linea o punti: la regola 4 al livello della serie. Con una serie sola
        per grafico coincide con il livello del grafico, ed e' il ramo difensivo
        di dashboard.md 5."""
        return len(self.disegnati) >= MIN_PUNTI_LINEA


@dataclass
class Serie:
    """Un grafico, o la tabella che lo sostituisce sotto i tre punti disegnabili."""

    campo: str
    grafico: Optional[Grafico]
    # (snapshot, riga) dal piu' recente; riga None = layer assente in quello snapshot.
    tabella: list[tuple[Snapshot, Optional[CommunityRow]]]
    decimali: dict[str, int] = field(default_factory=dict)


@dataclass
class Blocco:
    layer: str
    nome: str
    # La riga dello snapshot piu' recente. None = layer assente in quello snapshot.
    riga: Optional[CommunityRow]
    ultimo: Snapshot
    modularita: Serie
    stabilita: Serie
    assente_ovunque: bool
    # Decimali per campo della tabella del blocco, calcolati sulla SUA riga.
    decimali: dict[str, int] = field(default_factory=dict)

    @property
    def assente(self) -> bool:
        return self.riga is None

    @property
    def classi(self) -> list[CommunitySizeBucket]:
        return ordina_classi(self.riga.sizes) if self.riga is not None else []


@dataclass
class Vista:
    snapshot: list[Snapshot]  # dal piu' vecchio al piu' recente
    blocchi: list[Blocco]

    @property
    def vuota(self) -> bool:
        """Guild osservata, nessuno snapshot: il calcolo non e' ancora girato."""
        return not self.snapshot


# --- lo stato del confronto --------------------------------------------------


def senza_confronto(riga: CommunityRow) -> bool:
    """Prima osservazione confrontabile: nessuno dei tre campi del confronto.

    La tabella di dashboard.md 4 riconosce lo stato da ``previous_gap_days is
    None``; qui si chiede che manchino TUTTI E TRE. Nel job coincidono (si
    scrivono insieme), e ``--check`` del fixture lo impone. Se un giorno non
    coincidessero, la vista mostra i tre campi uno per uno invece di coprire con
    una frase un valore che c'e': fallisce mostrando di piu', non di meno.
    """
    v = riga.values
    return v.previous_gap_days is None and v.node_overlap is None and v.stability_jaccard is None


def ordina_classi(classi: list[CommunitySizeBucket]) -> list[CommunitySizeBucket]:
    """Le classi nell'ordine del job. Solo quelle arrivate: nessuna riga inventata.

    Una classe che il job non conosce resta, in coda, nell'ordine ricevuto: un
    bucket sconosciuto e' comunque un dato.
    """
    posizione = {c: i for i, c in enumerate(ORDINE_CLASSI)}
    return sorted(classi, key=lambda b: posizione.get(b.bucket, len(ORDINE_CLASSI)))


# --- costruzione -----------------------------------------------------------


# Le colonne numeriche che ciascuna tabella MOSTRA con cella(). Il test confronta
# questi elenchi con il template.
COLONNE_BLOCCO = (
    "community_count",
    "modularity",
    "modularity_random_mean",
    "modularity_z",
    "previous_gap_days",
    "node_overlap",
    "stability_jaccard",
    "communities_born",
    "communities_dissolved",
    "communities_merged",
    "communities_split",
)
COLONNE_SERIE_MODULARITA = ("modularity_z",)
COLONNE_SERIE_STABILITA = ("previous_gap_days", "node_overlap", "stability_jaccard")

# Nessun gruppo di precisione aritmetica in questa vista, e non per dimenticanza
# (dashboard.md 5, "Le colonne legate da un'operazione"): un gruppo esiste dove un
# numero mostrato si ottiene da altri numeri mostrati.
#
# - modularity_z = (modularity - modularity_random_mean) / modularity_random_sd, e
#   modularity_random_sd non si mostra: z non si ricava dalle colonne visibili;
# - node_overlap e stability_jaccard sono due uscite indipendenti di
#   compare_partitions, non un numero e la sua differenza.


def decimali_per_campo(righe: list[CommunityRow], colonne: tuple[str, ...]) -> dict[str, int]:
    """La precisione di ogni colonna mostrata, calcolata sulle righe di QUESTA tabella."""
    pubblicate = [r for r in righe if not r.quality.suppressed]
    return {
        campo: precisione_colonna(getattr(r.values, campo) for r in pubblicate)
        for campo in colonne
    }


def costruisci(righe: list[CommunityRow]) -> Vista:
    """I quattro blocchi, dalle righe di ``/guilds/{id}/communities``."""
    per_snapshot: dict[int, Snapshot] = {}
    per_chiave: dict[tuple[int, str], CommunityRow] = {}
    for r in righe:
        per_snapshot.setdefault(r.snapshot_id, Snapshot(r.snapshot_id, r.as_of))
        per_chiave[(r.snapshot_id, r.layer)] = r

    snapshot = sorted(per_snapshot.values(), key=lambda s: (s.as_of, s.snapshot_id))
    if not snapshot:
        return Vista(snapshot=[], blocchi=[])
    ultimo = snapshot[-1]

    blocchi = []
    for layer in LAYERS:
        riga = per_chiave.get((ultimo.snapshot_id, layer))
        storico = [(s, per_chiave.get((s.snapshot_id, layer))) for s in snapshot]
        assente_ovunque = all(r is None for _s, r in storico)
        blocchi.append(
            Blocco(
                layer=layer,
                nome=NOMI_LAYER.get(layer, layer),
                riga=riga,
                ultimo=ultimo,
                modularita=_serie(MODULARITA, storico, snapshot, assente_ovunque),
                stabilita=_serie(STABILITA, storico, snapshot, assente_ovunque),
                assente_ovunque=assente_ovunque,
                decimali=decimali_per_campo([riga] if riga else [], COLONNE_BLOCCO),
            )
        )
    return Vista(snapshot=snapshot, blocchi=blocchi)


def _serie(
    campo: str,
    storico: list[tuple[Snapshot, Optional[CommunityRow]]],
    snapshot: list[Snapshot],
    assente_ovunque: bool,
) -> Serie:
    colonne = COLONNE_SERIE_STABILITA if campo == STABILITA else COLONNE_SERIE_MODULARITA
    if assente_ovunque:
        return Serie(campo=campo, grafico=None, tabella=[])
    punti = [Punto(s, r, campo) for s, r in storico]
    # Regola 4: punti DISEGNABILI, non snapshot. Un valore assente su una riga
    # pubblicata non conta, esattamente come un punto soppresso.
    if sum(p.disegnabile for p in punti) >= MIN_PUNTI_LINEA:
        return Serie(campo=campo, grafico=_grafico(campo, punti, snapshot), tabella=[])
    # Dal piu' recente. Uno snapshot con il layer assente resta in tabella: se
    # sparisse, la serie sembrerebbe piu' corta invece che interrotta.
    tabella = list(reversed(storico))
    return Serie(
        campo=campo,
        grafico=None,
        tabella=tabella,
        decimali=decimali_per_campo([r for _s, r in tabella if r is not None], colonne),
    )


def dominio_modularita(valori: list[float]) -> tuple[float, float]:
    """``[min(valori, 0), max(valori, soglia)]``: lo zero e la soglia sempre dentro.

    ``modularity_z`` puo' essere negativo (``mention``@11: -1,009): come per
    ``targeted_excess``, un asse che parte da zero nasconderebbe proprio il caso
    in cui la modularita' e' sotto quella di un grafo casuale. La soglia sta nel
    dominio perche' un riferimento fuori dal riquadro non si vede.
    """
    return min([*valori, 0.0]), max([*valori, 0.0, SOGLIA_Z])


# [0, 1]: una frazione. L'ancoraggio a zero e' leggibilita' dell'asse, non una
# soglia dichiarata — nessuna linea, nessuna etichetta la suggerisce.
DOMINIO_STABILITA = (0.0, 1.0)


def _grafico(campo: str, punti: list[Punto], snapshot: list[Snapshot]) -> Grafico:
    disegnati = [p for p in punti if p.disegnabile]
    valori = [p.valore for p in disegnati]  # type: ignore[misc]
    lo, hi = DOMINIO_STABILITA if campo == STABILITA else dominio_modularita(valori)  # type: ignore[arg-type]

    area_w = LARGHEZZA - MARGINE_SX - MARGINE_DX
    area_h = ALTEZZA - MARGINE_ALTO - MARGINE_BASSO
    t0 = snapshot[0].as_of.timestamp()
    t1 = snapshot[-1].as_of.timestamp()

    # x proporzionale al tempo, non all'indice, come in Robustezza.
    def x_di(s: Snapshot) -> float:
        if t1 == t0:
            return MARGINE_SX + area_w / 2
        return round(MARGINE_SX + (s.as_of.timestamp() - t0) / (t1 - t0) * area_w, 2)

    def y_di(v: float) -> float:
        return round(MARGINE_ALTO + (hi - v) / (hi - lo) * area_h, 2)

    decimali = precisione_colonna(valori)
    for p in punti:
        p.x = x_di(p.snapshot)
        if p.disegnabile:
            p.y = y_di(p.valore)  # type: ignore[arg-type]
            p.decimali = decimali

    segmenti: list[Segmento] = []
    if len(disegnati) >= MIN_PUNTI_LINEA:
        # Solo tra punti CONSECUTIVI disegnabili: qualunque punto mancante in mezzo
        # interrompe la linea, non la salta.
        for a, b in zip(punti, punti[1:]):
            if a.disegnabile and b.disegnabile:
                segmenti.append(
                    Segmento(a.x, a.y, b.x, b.y, dequalificato=a.dequalificato or b.dequalificato)
                )

    ticks = [TickY(y_di(hi), formatta(hi, decimali)), TickY(y_di(lo), formatta(lo, decimali))]
    y_riferimento = etichetta_riferimento = None
    legenda_confronto = None
    if campo == MODULARITA:
        y_riferimento = y_di(SOGLIA_Z)
        etichetta_riferimento = formatta(SOGLIA_Z, decimali)
        if all(abs(y_riferimento - t.y) >= 14 for t in ticks):
            ticks.append(TickY(y_riferimento, etichetta_riferimento))
    else:
        legenda_confronto = _legenda_confronto(disegnati)

    tutti = all(p.dequalificato for p in disegnati)
    nessuno = not any(p.dequalificato for p in disegnati)
    return Grafico(
        campo=campo,
        punti=punti,
        segmenti=segmenti,
        ticks_y=ticks,
        etichette_x=[(x_di(snapshot[0]), f"{snapshot[0].as_of:%d/%m}"),
                     (x_di(snapshot[-1]), f"{snapshot[-1].as_of:%d/%m}")],
        tutto_non_significativo=tutti,
        misto=not tutti and not nessuno,
        y_riferimento=y_riferimento,
        etichetta_riferimento=etichetta_riferimento,
        legenda_confronto=legenda_confronto,
        decimali=decimali,
    )


def _intervallo(valori: list[float], formato: Callable[[float, int], str]) -> str:
    d = precisione_colonna(valori)
    lo, hi = min(valori), max(valori)
    return formato(lo, d) if lo == hi else f"da {formato(lo, d)} a {formato(hi, d)}"


def _legenda_confronto(disegnati: list[Punto]) -> str:
    """Gap e sovrapposizione dei punti disegnati, in vista accanto al grafico.

    Regola 5 estesa nel grafico: come l'intervallo dei nodi rimossi nella legenda
    di Robustezza (regola 6), e' questo a tenere i tre campi insieme — il
    ``<title>`` del punto si vede solo passandoci sopra. Un punto disegnato ha
    sempre gap e sovrapposizione: stability_jaccard si scrive solo dopo di loro.
    """
    def parte(nome: str, valori: list[Optional[float]], unita: str = "") -> str:
        presenti = [v for v in valori if v is not None]
        # Ramo difensivo: il job non lo produce. Un campo che manca lo dice, non
        # sparisce dalla legenda lasciando la stabilita' da sola.
        if len(presenti) != len(valori):
            return f"{nome} non disponibile su alcuni punti"
        return f"{nome} {_intervallo(presenti, formatta)}{unita}"

    return " · ".join([
        parte("gap", [p.riga.values.previous_gap_days for p in disegnati], " giorni"),  # type: ignore[union-attr]
        parte("sovrapposizione", [p.riga.values.node_overlap for p in disegnati]),  # type: ignore[union-attr]
    ])
