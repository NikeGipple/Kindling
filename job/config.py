"""Parametri del modello del grafo, tutti in un posto solo.

I valori qui sono quelli della tabella in docs/architettura/modello-grafo.md 8.
Stanno in un unico modulo e non sparsi nel codice per due motivi espliciti
della specifica: cambiarli deve essere la modifica di un file solo, e ogni
snapshot deve registrare i valori con cui e' stato calcolato (``params`` in
``graph_snapshots``), perche' due snapshot calcolati con parametri diversi non
sono confrontabili.

Sono quasi tutti provvisori e da ritarare sui dati reali: il primo della lista
e' l'emivita ``H``, che e' ingegneria nostra e non letteratura.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

# Codici dei quattro layer. Vivono qui perche' sono parte del modello, non un
# dettaglio del codice che li calcola.
LAYER_VOICE = "voice"
LAYER_REPLY = "reply"
LAYER_MENTION = "mention"
LAYER_REACTION = "reaction"

# I layer non diretti memorizzano la coppia una volta sola, con src < dst.
UNDIRECTED_LAYERS = frozenset({LAYER_VOICE})

ALL_LAYERS = (LAYER_VOICE, LAYER_REPLY, LAYER_MENTION, LAYER_REACTION)


@dataclass(frozen=True)
class GraphParams:
    """Parametri di costruzione del grafo (modello-grafo.md 8)."""

    # Due episodi di presenza nello stesso canale separati da MENO di questa
    # finestra sono la stessa sessione. Provvisoria, da rifittare sui dati.
    session_window_minutes: float = 30.0

    # Sotto questa soglia di sovrapposizione, in una singola sessione, la
    # coppia non genera contributo: essersi incrociati per due minuti mentre
    # uno entrava e l'altro usciva non e' una relazione. Applicata per
    # sessione, non sulla somma del periodo.
    min_overlap_minutes: float = 5.0

    # Emivita del decadimento: dopo H il contributo di un'interazione vale
    # circa la meta'. Ingegneria nostra, primo parametro da rimettere in
    # discussione quando ci saranno mesi di dati.
    decay_half_life_days: float = 7.0

    # Cutoff: oltre C un'interazione non pesa piu' nulla. Viene dalla finestra
    # di relazione sostenuta di Millington ("minimo una discussione al mese").
    decay_cutoff_days: float = 30.0

    # Un intervallo di presenza ricostruito (leave perso) piu' lungo di questo
    # e' implausibile: viene scartato, non troncato. Troncarlo significherebbe
    # inventare una durata plausibile a partire da un dato che non c'e'.
    orphan_max_hours: float = 12.0

    # Quanto indietro leggere gli eventi oltre l'inizio della finestra, perche'
    # una sessione a cavallo del confine venga ricostruita intera invece che
    # spezzata a meta' (modello-grafo.md 4.1). Deve coprire la sessione
    # ricostruibile piu' lunga: il tetto della sessione orfana piu' la finestra
    # di sessione, con margine. Non e' un parametro del modello ma della
    # lettura, e cambiarlo non cambia il significato dei pesi.
    lookback_margin_hours: float = 24.0

    # Cadenza di snapshot decisa: settimanale. E' anche l'ampiezza di finestra
    # di default del job, sovrascrivibile da riga di comando.
    default_window_days: float = 7.0

    @property
    def session_window(self) -> timedelta:
        return timedelta(minutes=self.session_window_minutes)

    @property
    def decay_half_life(self) -> timedelta:
        return timedelta(days=self.decay_half_life_days)

    @property
    def decay_cutoff(self) -> timedelta:
        return timedelta(days=self.decay_cutoff_days)

    @property
    def orphan_max_duration(self) -> timedelta:
        return timedelta(hours=self.orphan_max_hours)

    @property
    def lookback_margin(self) -> timedelta:
        return timedelta(hours=self.lookback_margin_hours)

    def as_snapshot_params(self) -> dict[str, Any]:
        """I valori da salvare in ``graph_snapshots.params``.

        Include anche la formula di normalizzazione: non e' configurabile oggi,
        ma uno snapshot deve poter dichiarare con quale correzione per
        dimensione della sessione e' stato costruito, dato che l'alternativa
        (odds ratio) e' gia' prevista come sviluppo futuro.
        """
        params = asdict(self)
        params["size_normalization"] = "1/(n-1)"
        params["decay_formula"] = "(2^(-d/H) - 2^(-C/H)) / (1 - 2^(-C/H))"
        return params


DEFAULT_PARAMS = GraphParams()
