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
from typing import Any, Optional

# Codici dei quattro layer. Vivono qui perche' sono parte del modello, non un
# dettaglio del codice che li calcola.
LAYER_VOICE = "voice"
LAYER_REPLY = "reply"
LAYER_MENTION = "mention"
LAYER_REACTION = "reaction"

# I layer non diretti memorizzano la coppia una volta sola, con src < dst.
UNDIRECTED_LAYERS = frozenset({LAYER_VOICE})

ALL_LAYERS = (LAYER_VOICE, LAYER_REPLY, LAYER_MENTION, LAYER_REACTION)

# Ambito su cui si contano le connessioni distinte di un nuovo membro. "any" e'
# l'unione degli insiemi di partner su tutti i layer: e' un'unione di insiemi di
# PERSONE, non una somma di pesi, quindi non costruisce la sociomatrice fusa che
# modello-grafo.md 1 vieta.
SCOPE_ANY = "any"
# Etichetta del bucket che accorpa tutte le community sotto soglia: non si
# riporta mai la dimensione di una community sotto N come voce a se'.
BUCKET_SMALL = "small"


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


@dataclass(frozen=True)
class MetricParams:
    """Parametri del layer di metriche aggregate (modello-metriche.md 10).

    Separati da ``GraphParams`` e salvati in ``metric_runs.params`` invece che
    in ``graph_snapshots.params``: sono i parametri di un layer diverso e devono
    poter cambiare senza rendere incomparabili gli snapshot del grafo, che
    descrivono come e' stato costruito il grafo e non come lo si e' misurato.
    """

    # --- soppressione (6) ---------------------------------------------------

    # Cardinalita' minima sotto la quale una cella non viene mostrata. Valore
    # convenzionale della k-anonymity nelle tabelle di frequenza delle
    # statistiche ufficiali, non un risultato di letteratura sulle reti.
    min_cardinality: int = 5

    # Soglia di pubblicazione delle righe STRUTTURALI, separata da
    # min_cardinality perche' protegge da un rischio diverso: le grandezze
    # strutturali sono rapporti sull'intero grafo e non identificano nessuno,
    # quindi la loro soglia non e' quella dell'anonimato. Serve per il caso
    # residuo di de-anonimizzazione per contesto (un grafo di 3 nodi con una
    # componente gigante di 2 dice qualcosa su una coppia specifica). None =
    # uguale a min_cardinality.
    min_nodes_publish: Optional[int] = None

    # --- ammissione degli archi (2.3) ---------------------------------------

    # Un arco entra nel grafo se weight > questa soglia. A 0.0 e' la regola
    # "weight > 0 strettamente". Esiste per poter alzare la soglia quando i dati
    # diranno dove sta il confine tra un legame debole e un residuo numerico:
    # a 29,9 giorni il peso e' dell'ordine di 1e-5, e per le componenti
    # connesse quell'arco tiene insieme due parti esattamente come uno di peso
    # 1. Alzarlo e' un cambio di parametro, non una riprogettazione.
    min_edge_weight: float = 0.0

    # --- robustezza (3) -----------------------------------------------------

    # Non un X solo: il catalogo dichiara X da validare empiricamente, quindi
    # fissarne uno a tavolino sarebbe scegliere in silenzio proprio il
    # parametro segnalato come aperto.
    removal_fractions: tuple[float, ...] = (0.05, 0.10, 0.20)

    # --- baseline (3.3, 7.2) ------------------------------------------------

    baseline_repetitions: int = 100

    # Sopra questa dimensione il baseline si degrada: fino a ~400 esecuzioni di
    # Leiden e ~300 rimozioni casuali per snapshot su 1 vCPU condiviso con
    # l'heartbeat del gateway Discord non restano gratuite quando il grafo
    # cresce.
    baseline_downgrade_nodes: int = 500
    baseline_repetitions_reduced: int = 20

    # Il valore e' irrilevante, la sua stabilita' no: senza seed due esecuzioni
    # sullo stesso grafo danno partizioni diverse e la "stabilita' tra
    # snapshot" misura il rumore dell'algoritmo invece del cambiamento della
    # community. E' anche cio' che permette di RICOSTRUIRE la partizione
    # precedente invece di persisterla (8).
    seed: int = 20260903

    # --- community (4) ------------------------------------------------------

    leiden_objective: str = "modularity"
    # -1 = itera fino a convergenza, per ridurre la variabilita' residua a
    # parita' di seed.
    leiden_iterations: int = -1

    # Sotto questo Jaccard due community di snapshot successivi non sono la
    # stessa: nessuna base, da tarare sulle prime partizioni reali.
    jaccard_match_min: float = 0.30
    # Quota dei membri di una community precedente finiti in un'unica community
    # nuova perche' si parli di fusione e non di dissoluzione.
    merge_min_share: float = 0.50
    # Sotto meta' di nodi in comune il confronto non riguarda piu' abbastanza
    # la stessa popolazione, e la stabilita' non e' definita.
    min_node_overlap: float = 0.50

    # --- coorti (5) ---------------------------------------------------------

    # Connessioni distinte per considerare integrato un nuovo membro. Da tarare
    # sui dati reali, non da fissare a tavolino.
    k_connections: int = 5

    # Interazioni minime perche' un partner conti come connessione. A 1 un
    # membro che lascia cinque reazioni emoji nel primo giorno risulta
    # integrato come chi ha passato cinque serate in vocale: il modo in cui
    # questa metrica fallisce e' sembrando funzionare, quindi va ritarato
    # INSIEME a k_connections.
    partner_min_interactions: int = 1

    cohort_layer_scopes: tuple[str, ...] = (SCOPE_ANY, LAYER_VOICE)
    retention_horizons_days: tuple[int, ...] = (7, 14, 28)
    reach_horizons_days: tuple[int, ...] = (14, 28)

    # Sotto questa osservazione una coorte e' scritta ma non matura: la curva
    # di sopravvivenza esiste e quasi tutto e' NULL.
    min_observation_days: int = 14

    # Oltre questa eta' una coorte non viene piu' ricalcolata a ogni run: il
    # suo esito e' fermo, il costo di rileggerne gli snapshot no.
    cohort_max_age_days: int = 180

    # Un membro con attivita' antecedente al proprio joined_at o e' un rientro
    # o e' un dato incoerente: in entrambi i casi non e' un nuovo membro di cui
    # misurare l'onboarding (members sovrascrive joined_at/left_at sui rientri).
    exclude_suspected_rejoins: bool = True

    # --- calcolabilita' (7) -------------------------------------------------

    # Sotto poche decine di nodi la betweenness e' dominata da una manciata di
    # cammini e il top X% e' 1-2 nodi: la rimozione non e' una statistica.
    min_nodes_structural: int = 30
    min_modularity_z: float = 2.0

    @property
    def publish_threshold(self) -> int:
        return (
            self.min_nodes_publish
            if self.min_nodes_publish is not None
            else self.min_cardinality
        )

    def community_size_buckets(self) -> tuple[tuple[str, int, Optional[int]], ...]:
        """Classi di dimensione delle community: ``(etichetta, min, max)``.

        Il primo estremo e' ``min_cardinality``, quindi l'etichetta ``'5-9'``
        non e' un nome ma ``[N,10)`` con ``N=5``. Per questo gli estremi
        effettivi finiscono in ``metric_runs.params``: alzando N la stessa
        etichetta cambierebbe significato e una serie storica letta per
        etichetta mescolerebbe due vocabolari senza nessun segnale.
        """
        edges = [10, 20, 50, 100]
        buckets: list[tuple[str, int, Optional[int]]] = [
            (BUCKET_SMALL, 1, self.min_cardinality)
        ]
        low = self.min_cardinality
        for high in edges:
            if high <= low:
                # Con N gia' oltre questo estremo la classe non esiste.
                continue
            buckets.append((f"{low}-{high - 1}", low, high))
            low = high
        buckets.append((f"{low}+", low, None))
        return tuple(buckets)

    def baseline_repetitions_for(self, node_count: int) -> tuple[int, bool]:
        """Ripetizioni del baseline per un grafo di ``node_count`` nodi.

        Ritorna anche se la degradazione e' scattata: una deviazione standard
        da 20 ripetizioni e' piu' rumorosa di una da 100, quindi gli z-score di
        due snapshot con degradazione diversa non sono confrontabili alla pari
        e chi li legge deve poter vedere da quale situazione viene la riga.
        """
        if node_count > self.baseline_downgrade_nodes:
            return self.baseline_repetitions_reduced, True
        return self.baseline_repetitions, False

    def as_run_params(self) -> dict[str, Any]:
        params = asdict(self)
        params["min_nodes_publish"] = self.publish_threshold
        params["community_size_buckets"] = [
            {"bucket": label, "min": low, "max": high}
            for label, low, high in self.community_size_buckets()
        ]
        params["directed_layer_projection"] = "undirected_sum"
        params["partition_matching"] = "greedy_jaccard"
        params["centrality"] = "betweenness(distance=1/weight)"
        return params


DEFAULT_METRIC_PARAMS = MetricParams()
