"""Vista Robustezza: dalle righe dell'API ai blocchi e ai grafici da rendere.

Tutto cio' che la vista DECIDE sta qui, in funzioni pure; il template decide solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4 "La
vista Robustezza, in dettaglio" e §5 "La qualificazione di una serie".

- **Quattro blocchi, uno per layer**, mai una tabella con una colonna ``layer``:
  l'invariante 3 tradotto in layout. Nessun totale, media o confronto tra layer.
- **Il blocco mostra lo snapshot piu' recente**: tre righe, una per frazione, e
  ``n_effective`` una volta sola.
- **Un layer puo' mancare del tutto** (grafo vuoto: il job non scrive righe). Il
  blocco resta, con la sua frase; non e' una soppressione.
- **La serie** e' ``targeted_excess`` per snapshot, un grafico per layer con le
  tre frazioni dentro. La regola 4 conta i punti DISEGNABILI e si applica su due
  livelli: grafico o tabella per layer, linea o punti per serie.

``quality.details`` non si legge in questo modulo, per nessuna ragione (regola 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from api.models import RobustnessRow, RobustnessValues
from job.config import ALL_LAYERS, MetricParams

from .qualifica import DECIMALI_MINIMI, formatta, precisione_colonna

# I layer e le frazioni del job, importati e non ricopiati: la vista mostra un
# blocco per ogni layer che il job PUO' calcolare, anche quando l'API non ne porta
# righe — e' l'unico modo di distinguere "layer spento" da "layer mai calcolato".
LAYERS = ALL_LAYERS
REMOVAL_FRACTIONS = MetricParams().removal_fractions

NOMI_LAYER = {
    "voice": "Voce",
    "reply": "Risposte",
    "mention": "Menzioni",
    "reaction": "Reazioni",
}

# Punti disegnabili sotto i quali non si traccia una linea (regola 4): con due
# punti l'unica forma possibile e' la retta, e una retta afferma una direzione.
MIN_PUNTI_LINEA = 3

# Geometria del grafico, in unita' SVG (viewBox): la larghezza reale la decide il CSS.
LARGHEZZA = 640
ALTEZZA = 240
MARGINE_SX = 56
MARGINE_DX = 16
MARGINE_ALTO = 28
MARGINE_BASSO = 36


def percentuale(removal_fraction: float) -> str:
    """``0.05`` -> ``5%``. Da usare SOLO accanto ai nodi rimossi (regola 6)."""
    return f"{removal_fraction * 100:g}%"


def nodi_rimossi(n: int) -> str:
    return "1 nodo rimosso" if n == 1 else f"{n} nodi rimossi"


# --- struttura ------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    as_of: datetime


@dataclass
class Punto:
    """Un punto della serie: uno snapshot, per una frazione, in un layer.

    ``riga`` e' None quando in quello snapshot il layer non ha righe (grafo
    vuoto): come un punto soppresso, non si disegna e interrompe la linea, ma non
    porta nessun simbolo di soppressione.
    """

    snapshot: Snapshot
    riga: Optional[RobustnessRow]
    x: float = 0.0
    y: float = 0.0
    # La precisione del grafico a cui il punto appartiene, fissata da _grafico().
    decimali: Optional[int] = None

    @property
    def disegnabile(self) -> bool:
        # Ramo difensivo: su una riga pubblicata targeted_excess non e' mai None
        # (dashboard.md 5). Se lo fosse, non diventa ne' uno zero ne' un punto.
        return (
            self.riga is not None
            and not self.riga.quality.suppressed
            and self.riga.values.targeted_excess is not None
        )

    @property
    def dequalificato(self) -> bool:
        return self.disegnabile and self.riga.quality.significant is False  # type: ignore[union-attr]

    @property
    def titolo(self) -> str:
        """Il ``<title>`` del punto: percentuale e nodi rimossi della SUA riga.

        Senza "non significativo": nel grafico quell'etichetta compare una volta
        sola (dashboard.md 5), e dodici ``<title>`` che la ripetono sarebbero
        dodici etichette. La dequalificazione del punto e' nello stile.
        """
        riga = self.riga
        assert riga is not None and riga.values.nodes_removed is not None
        return " · ".join([
            f"{self.snapshot.as_of:%d/%m/%Y}",
            f"{percentuale(riga.removal_fraction)} · {nodi_rimossi(riga.values.nodes_removed)}",
            f"eccesso mirato {formatta(riga.values.targeted_excess, self.decimali)}",
        ])


@dataclass
class Segmento:
    x1: float
    y1: float
    x2: float
    y2: float
    # Eredita la qualificazione peggiore dei suoi estremi.
    dequalificato: bool


@dataclass
class Serie:
    removal_fraction: float
    punti: list[Punto]
    indice: int
    segmenti: list[Segmento] = field(default_factory=list)

    @property
    def disegnabili(self) -> list[Punto]:
        return [p for p in self.punti if p.disegnabile]

    @property
    def congiunta(self) -> bool:
        """Linea o punti non congiunti: la regola 4 al livello della serie."""
        return len(self.disegnabili) >= MIN_PUNTI_LINEA

    @property
    def legenda(self) -> Optional[str]:
        """``5% · da 1 a 3 nodi rimossi``: l'intervallo soddisfa la regola 6.

        None se la serie non ha nessuna riga pubblicata: senza nodi rimossi da
        mettergli accanto, la percentuale non si mostra.
        """
        rimossi = [
            p.riga.values.nodes_removed
            for p in self.punti
            if p.riga is not None and p.riga.values.nodes_removed is not None
        ]
        if not rimossi:
            return None
        lo, hi = min(rimossi), max(rimossi)
        pct = percentuale(self.removal_fraction)
        if lo == hi:
            return f"{pct} · {nodi_rimossi(lo)}"
        return f"{pct} · da {lo} a {hi} nodi rimossi"


@dataclass
class TickY:
    y: float
    etichetta: str


@dataclass
class Grafico:
    serie: list[Serie]
    y_min: float
    y_max: float
    y_zero: float
    ticks_y: list[TickY]
    etichette_x: list[tuple[float, str]]
    # Tutti i punti disegnati sono non significativi: etichetta UNA volta sola.
    tutto_non_significativo: bool
    # Caso misto: la legenda spiega obbligatoriamente lo stile dequalificato.
    misto: bool
    # La precisione della "colonna" del grafico: i suoi punti. Etichette dell'asse
    # e <title> dei punti la seguono.
    decimali: int = DECIMALI_MINIMI
    larghezza: int = LARGHEZZA
    altezza: int = ALTEZZA


@dataclass
class Blocco:
    layer: str
    nome: str
    # Righe dello snapshot piu' recente, in ordine di frazione. Vuota = layer
    # assente in quello snapshot.
    righe: list[RobustnessRow]
    ultimo: Snapshot
    grafico: Optional[Grafico]
    # Tabella della serie quando nessuna serie arriva a tre punti disegnabili.
    # Riga None = in quello snapshot il layer non ha righe.
    tabella_serie: list[tuple[Snapshot, Optional[RobustnessRow]]]
    # Il layer non ha righe in nessuno snapshot mostrato: solo la frase.
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
        costruzione, ma e' un'assunzione della vista: se non vale, non si sceglie
        una delle tre in silenzio (vedi ``n_incoerente``).
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
class Vista:
    snapshot: list[Snapshot]  # dal piu' vecchio al piu' recente
    blocchi: list[Blocco]

    @property
    def vuota(self) -> bool:
        """Guild osservata, nessuno snapshot: il calcolo non e' ancora girato."""
        return not self.snapshot


# --- costruzione -----------------------------------------------------------


def costruisci(righe: list[RobustnessRow]) -> Vista:
    """I quattro blocchi, dalle righe di ``/guilds/{id}/robustness``."""
    per_snapshot: dict[int, Snapshot] = {}
    per_chiave: dict[tuple[int, str, float], RobustnessRow] = {}
    for r in righe:
        per_snapshot.setdefault(r.snapshot_id, Snapshot(r.snapshot_id, r.as_of))
        per_chiave[(r.snapshot_id, r.layer, r.removal_fraction)] = r

    snapshot = sorted(per_snapshot.values(), key=lambda s: (s.as_of, s.snapshot_id))
    if not snapshot:
        return Vista(snapshot=[], blocchi=[])
    ultimo = snapshot[-1]

    blocchi = []
    for layer in LAYERS:
        righe_ultimo = [
            per_chiave[(ultimo.snapshot_id, layer, rf)]
            for rf in REMOVAL_FRACTIONS
            if (ultimo.snapshot_id, layer, rf) in per_chiave
        ]
        serie = [
            Serie(
                removal_fraction=rf,
                indice=i,
                punti=[Punto(s, per_chiave.get((s.snapshot_id, layer, rf))) for s in snapshot],
            )
            for i, rf in enumerate(REMOVAL_FRACTIONS)
        ]
        assente_ovunque = all(p.riga is None for s in serie for p in s.punti)

        grafico: Optional[Grafico] = None
        tabella: list[tuple[Snapshot, Optional[RobustnessRow]]] = []
        if not assente_ovunque:
            # Regola 4, livello del grafico: si disegna se ALMENO UNA serie ha tre
            # punti disegnabili. Una serie corta non cancella le altre due.
            if any(s.congiunta for s in serie):
                grafico = _grafico(serie, snapshot)
            else:
                # Dal piu' recente; dentro lo snapshot, per frazione. Uno snapshot
                # in cui il layer non ha righe resta in tabella con None: se
                # sparisse, la serie sembrerebbe piu' corta invece che interrotta.
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
                grafico=grafico,
                tabella_serie=tabella,
                assente_ovunque=assente_ovunque,
                decimali=decimali_per_campo(righe_ultimo),
                decimali_serie=decimali_per_campo([r for _s, r in tabella if r is not None]),
            )
        )
    return Vista(snapshot=snapshot, blocchi=blocchi)


def decimali_per_campo(righe: list[RobustnessRow]) -> dict[str, int]:
    """La precisione di ogni colonna di una tabella, calcolata sulle SUE righe.

    Una chiave per ogni campo di ``RobustnessValues``: i campi interi non la usano
    (``formatta`` li rende senza decimali), ma averla sempre evita che il template
    chieda una chiave che non c'e'. Le righe soppresse non contano: i loro valori
    sono tutti None.
    """
    pubblicate = [r for r in righe if not r.quality.suppressed]
    return {
        campo: precisione_colonna(getattr(r.values, campo) for r in pubblicate)
        for campo in RobustnessValues.model_fields
    }


def dominio_y(valori: list[float]) -> tuple[float, float]:
    """``[min(valori, 0), max(valori, 0)]``: lo zero sempre dentro, mai l'ancora.

    ``targeted_excess`` puo' essere negativo e non e' clampato: un asse che parte
    da zero nasconderebbe proprio il caso in cui i nodi centrali erano meno
    critici di nodi presi a caso.
    """
    lo = min([*valori, 0.0])
    hi = max([*valori, 0.0])
    if lo == hi:
        # Solo zeri: un intervallo nullo non ha una scala. Si apre simmetrico.
        return -0.1, 0.1
    return lo, hi


def _grafico(serie: list[Serie], snapshot: list[Snapshot]) -> Grafico:
    disegnabili = [p for s in serie for p in s.disegnabili]
    valori = [p.riga.values.targeted_excess for p in disegnabili]  # type: ignore[union-attr]
    lo, hi = dominio_y(valori)

    area_w = LARGHEZZA - MARGINE_SX - MARGINE_DX
    area_h = ALTEZZA - MARGINE_ALTO - MARGINE_BASSO

    # x proporzionale al tempo, non all'indice: la distanza tra snapshot non e'
    # sempre la stessa (6,82 giorni contro 7), e un asse a passo fisso la
    # nasconderebbe.
    t0 = snapshot[0].as_of.timestamp()
    t1 = snapshot[-1].as_of.timestamp()

    def x_di(s: Snapshot) -> float:
        if t1 == t0:
            return MARGINE_SX + area_w / 2
        return round(MARGINE_SX + (s.as_of.timestamp() - t0) / (t1 - t0) * area_w, 2)

    def y_di(v: float) -> float:
        return round(MARGINE_ALTO + (hi - v) / (hi - lo) * area_h, 2)

    for s in serie:
        for p in s.punti:
            p.x = x_di(p.snapshot)
            if p.disegnabile:
                p.y = y_di(p.riga.values.targeted_excess)  # type: ignore[union-attr]
        if s.congiunta:
            # Segmenti solo tra punti CONSECUTIVI disegnabili: un punto soppresso
            # o un layer assente in mezzo interrompe la linea, non la salta.
            for a, b in zip(s.punti, s.punti[1:]):
                if a.disegnabile and b.disegnabile:
                    s.segmenti.append(
                        Segmento(a.x, a.y, b.x, b.y, dequalificato=a.dequalificato or b.dequalificato)
                    )

    tutti_dequalificati = all(p.dequalificato for p in disegnabili)
    nessuno_dequalificato = not any(p.dequalificato for p in disegnabili)

    # Nel grafico i valori sono coordinate, non testo: la precisione riguarda solo
    # le etichette dell'asse e i <title>, e si calcola sui punti di QUESTO grafico.
    decimali = precisione_colonna(valori)
    for p in disegnabili:
        p.decimali = decimali

    ticks = [TickY(y_di(hi), formatta(hi, decimali)), TickY(y_di(lo), formatta(lo, decimali))]
    y_zero = y_di(0.0)
    # L'etichetta dello zero solo se non si sovrappone a quelle degli estremi; la
    # linea di riferimento dello zero si disegna comunque.
    if lo < 0 < hi and all(abs(y_zero - t.y) >= 14 for t in ticks):
        ticks.append(TickY(y_zero, formatta(0.0, decimali)))

    return Grafico(
        decimali=decimali,
        serie=serie,
        y_min=lo,
        y_max=hi,
        y_zero=y_di(0.0),
        ticks_y=ticks,
        etichette_x=[(x_di(snapshot[0]), f"{snapshot[0].as_of:%d/%m}"),
                     (x_di(snapshot[-1]), f"{snapshot[-1].as_of:%d/%m}")],
        tutto_non_significativo=tutti_dequalificati,
        misto=not tutti_dequalificati and not nessuno_dequalificato,
    )
