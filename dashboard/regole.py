"""Le regole del calcolo: da ``metric_runs.params`` a valore, etichetta, spiegazione.

La vista Stato mostra i valori con cui Kindling misura *questo* server. I valori
vengono da ``params`` della run — cioe' da cio' con cui quei numeri sono stati
calcolati davvero — e **mai** da ``job/config.py``, che e' cio' con cui
verrebbero calcolati oggi. Sono la stessa cosa solo finche' nessuno cambia un
parametro, e il giorno in cui qualcuno lo cambia la differenza e' esattamente
quella che chi legge deve poter vedere (dashboard.md 4, "Le regole del calcolo").

Qui ci sono le due cose che dai dati non si ricavano: **l'etichetta** e **la
spiegazione**. Le frasi si COMPONGONO dal valore (``f"... {valore} ..."``), non
si scrivono a mano con il numero dentro: una frase con "5" battuto dentro resta
"5" il giorno in cui ``params`` dice 8, e nessuno riceve un segnale — e' il
difetto di CLAUDE.md 7 nella forma in cui il fixture l'ha gia' avuto due volte.

**Se una chiave richiesta manca da ``params``, la regola non compare.** Nessun
valore di ripiego: un numero inventato con l'aria di essere misurato e' peggio di
una regola assente, che almeno si nota. Le chiavi sono richieste tutte, anche
quelle che entrano solo in una frase secondaria — una regola che si mostra a
meta' direbbe una soglia sola dove ce ne sono due.

``AREE`` invece non e' un filtro ma una mappa di ETICHETTE, e serve ai Dettagli
tecnici: una chiave che non conosce finisce sotto ``ALTRI`` e resta visibile.
Un raggruppamento che scarta cio' che non riconosce nasconderebbe in silenzio
proprio la chiave nuova, cioe' l'unica interessante.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .qualifica import formatta


@dataclass(frozen=True)
class Regola:
    """Una regola pronta da rendere. ``codice`` e' l'identita' stabile, per i test."""

    codice: str
    valore: str
    etichetta: str
    spiegazione: str


# --- formattazione dei valori ------------------------------------------------
#
# I valori arrivano da JSON: le tuple del job sono liste, e un intero puo'
# essere arrivato come float. Nessuna di queste funzioni sceglie un numero:
# scelgono solo come si scrive quello che ricevono.


def numero(valore: Any) -> str:
    """Un numero come lo scriverebbe una persona: virgola decimale, niente zeri inutili.

    Gli zeri in coda si tolgono davvero, e non e' cosmesi: ``formatta`` tiene tre
    decimali perche' serve ad allineare una COLONNA di numeri (dashboard.md §5,
    "La precisione di una colonna numerica"), mentre qui i numeri stanno dentro
    una frase. "circa ogni 6,800 giorni" fa sembrare misurata al millesimo una
    cadenza che e' la mediana di due date.
    """
    if isinstance(valore, bool):
        return formatta(valore)
    if isinstance(valore, float) and valore.is_integer():
        valore = int(valore)
    reso = formatta(valore)
    if "," in reso:
        reso = reso.rstrip("0").rstrip(",")
    return reso


def plurale(quantita: Any, singolare: str, molti: str) -> str:
    """``1 serata`` / ``2 serate``. Il confronto e' sul valore, non sul testo."""
    uno = isinstance(quantita, (int, float)) and not isinstance(quantita, bool) and quantita == 1
    return f"{numero(quantita)} {singolare if uno else molti}"


def _elenco(valori: Sequence[Any], unita: str) -> str:
    """``7 · 14 · 28 giorni``. Il separatore e' un punto medio, non una virgola:
    questi non sono un elenco di cose, sono le tacche della stessa misura."""
    return " · ".join(numero(v) for v in valori) + (f" {unita}" if unita else "")


def _percentuali(frazioni: Sequence[Any]) -> str:
    """``5 · 10 · 20%`` da ``[0.05, 0.1, 0.2]``.

    Il ``round`` non e' cosmetico: ``0.05 * 100`` vale ``5.000000000000001`` in
    virgola mobile, e senza arrotondamento la pagina direbbe "5,000".
    """
    return _elenco([round(float(f) * 100, 6) for f in frazioni], "") + "%"


def _sequenza(valore: Any) -> Optional[Sequence[Any]]:
    """Una sequenza di numeri, o None se il dato non lo e'.

    Ramo difensivo con un motivo: ``params`` e' un ``dict[str, Any]`` nel
    contratto (``api/models.py``), quindi la sua forma non la garantisce nessuno
    schema. Una chiave presente ma di forma sbagliata fa sparire la regola come
    se mancasse, invece di far fallire la pagina intera.
    """
    if isinstance(valore, (list, tuple)) and valore:
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valore):
            return list(valore)
    return None


def _intero(valore: Any) -> Optional[float]:
    """Il valore se e' un numero, altrimenti None. Stessa ragione di ``_sequenza``."""
    if isinstance(valore, bool):
        return None
    if isinstance(valore, (int, float)):
        return valore
    return None


# --- le sei regole -----------------------------------------------------------


def _soglia_pubblicazione(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    minimo, pubblicazione = _intero(p["min_cardinality"]), _intero(p["min_nodes_publish"])
    if minimo is None or pubblicazione is None:
        return None
    frase = (
        f"Nessun numero riguarda meno di {numero(minimo)} membri: sotto questa "
        "soglia la cella resta vuota, così nessuno è riconoscibile."
    )
    if pubblicazione != minimo:
        # Le due soglie proteggono da rischi diversi (job/config.py): quando
        # coincidono dirlo due volte sarebbe rumore, quando divergono tacerlo
        # sarebbe una soglia nascosta.
        frase += (
            f" Per le misure sulla forma della rete la soglia è "
            f"{numero(pubblicazione)}."
        )
    return plurale(minimo, "persona", "persone"), frase


def _integrato(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    k, partner = _intero(p["k_connections"]), _intero(p["partner_min_interactions"])
    if k is None or partner is None:
        return None
    return (
        f"{numero(k)} persone diverse",
        f"Un nuovo membro conta come integrato quando ha interagito con almeno "
        f"{numero(k)} persone diverse. Una persona conta come partner dopo "
        f"{plurale(partner, 'interazione', 'interazioni')}. Valori provvisori, "
        "da tarare sui dati di questo server.",
    )


def _permanenza(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    orizzonti = _sequenza(p["retention_horizons_days"])
    maturita = _intero(p["min_observation_days"])
    if orizzonti is None or maturita is None:
        return None
    return (
        _elenco(orizzonti, "giorni"),
        f"Per ogni gruppo di nuovi arrivati Kindling guarda chi c'è ancora dopo "
        f"{_elenco(orizzonti, 'giorni')} dal loro ingresso. Un gruppo diventa "
        f"leggibile dopo almeno {plurale(maturita, 'giorno', 'giorni')} di "
        "osservazione.",
    )


def _rete_minima(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    nodi = _intero(p["min_nodes_structural"])
    if nodi is None:
        return None
    return (
        plurale(nodi, "persona", "persone"),
        f"Con meno di {numero(nodi)} persone attive le misure sulla forma della "
        "rete dipendono da pochissimi individui: i numeri si calcolano lo stesso, "
        "ma non dicono niente sulla community.",
    )


def _uscite_simulate(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    frazioni = _sequenza(p["removal_fractions"])
    if frazioni is None:
        return None
    return (
        _percentuali(frazioni),
        f"Robustezza simula l'uscita del {_percentuali(frazioni)} delle persone "
        "più centrali e confronta quello che resta con quello che resterebbe "
        "togliendo altrettante persone a caso.",
    )


def _serate_vocale(p: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    serate = _intero(p["voice_structural_min_sessions"])
    if serate is None:
        return None
    return (
        plurale(serate, "serata", "serate"),
        f"Due persone entrano nella rete del vocale solo dopo aver condiviso "
        f"almeno {plurale(serate, 'serata diversa', 'serate diverse')}: una sola "
        "serata affollata metterebbe in relazione tutti con tutti.",
    )


# --- le due regole che vengono dal GRAFO, non dalle metriche -----------------
#
# Leggono ``RunRow.graph_params`` — campi tipizzati che l'API ricava dalla vista
# ``graph_snapshot_params`` (migration 0014) — e non il dizionario ``params``.
# Sono due sorgenti diverse apposta: ``MetricParams`` e ``GraphParams`` stanno
# separati in ``job/config.py`` perche' i parametri delle metriche possano
# cambiare senza rendere incomparabili gli snapshot del grafo, e fonderli qui
# rimetterebbe insieme cio' che quella divisione tiene separato.
#
# ``None`` su un campo vuol dire che il valore non c'e' — snapshot cancellato,
# chiave assente, valore di forma inattesa — e la regola non compare, esattamente
# come per una chiave mancante in ``params``.


def _memoria(g: Any) -> Optional[tuple[str, str]]:
    emivita = _intero(getattr(g, "decay_half_life_days", None))
    cutoff = _intero(getattr(g, "decay_cutoff_days", None))
    if emivita is None or cutoff is None:
        return None
    return (
        f"{numero(emivita)} · {numero(cutoff)} giorni",
        f"Un'interazione pesa la metà dopo {plurale(emivita, 'giorno', 'giorni')} "
        f"e non pesa più niente dopo {plurale(cutoff, 'giorno', 'giorni')}: la "
        "rete mostra i legami di adesso, non quelli di mesi fa.",
    )


def _sovrapposizione_vocale(g: Any) -> Optional[tuple[str, str]]:
    minuti = _intero(getattr(g, "min_overlap_minutes", None))
    if minuti is None:
        return None
    return (
        plurale(minuti, "minuto", "minuti"),
        f"Due persone nello stesso canale vocale contano come legame solo se ci "
        f"restano insieme almeno {plurale(minuti, 'minuto', 'minuti')}, e il "
        "conto si fa su ogni singola serata: essersi incrociati mentre uno "
        "entrava e l'altro usciva non è una relazione.",
    )


# codice → (chiavi richieste, etichetta breve, costruttore di (valore, spiegazione))
_DEFINIZIONI: tuple[tuple[str, tuple[str, ...], str, Callable[..., Any]], ...] = (
    (
        "soglia_pubblicazione",
        ("min_cardinality", "min_nodes_publish"),
        "gruppo più piccolo mostrato",
        _soglia_pubblicazione,
    ),
    (
        "integrato",
        ("k_connections", "partner_min_interactions"),
        "per dire «integrato»",
        _integrato,
    ),
    (
        "permanenza",
        ("retention_horizons_days", "min_observation_days"),
        "quando si misura chi resta",
        _permanenza,
    ),
    (
        "rete_minima",
        ("min_nodes_structural",),
        "rete minima per Robustezza e Community",
        _rete_minima,
    ),
    (
        "uscite_simulate",
        ("removal_fractions",),
        "uscite simulate in Robustezza",
        _uscite_simulate,
    ),
    (
        "serate_vocale",
        ("voice_structural_min_sessions",),
        "serate in vocale per la struttura",
        _serate_vocale,
    ),
)

# Le regole che leggono i campi tipizzati del grafo invece del dizionario
# ``params``. In fondo alla griglia perche' riguardano come il grafo e' COSTRUITO,
# mentre le sei sopra riguardano come lo si misura.
#
# Fino al 22/09/2026 qui c'era ``REGOLE_SENZA_SORGENTE``, l'elenco delle regole
# che la vista voleva e non poteva avere perche' ``graph_snapshots`` non era
# leggibile. La vista ``graph_snapshot_params`` (migration 0014) le ha rese
# possibili, e l'elenco e' stato tolto invece di restare vuoto: una struttura
# vuota in piedi sembra un posto dove qualcosa mancava e non dice piu' che cosa.
_DEFINIZIONI_GRAFO: tuple[tuple[str, str, Callable[..., Any]], ...] = (
    ("memoria", "memoria delle interazioni", _memoria),
    ("sovrapposizione_vocale", "tempo minimo insieme in vocale", _sovrapposizione_vocale),
)


def costruisci(
    params: Optional[Mapping[str, Any]], grafo: Any = None
) -> tuple[Regola, ...]:
    """Le regole che i dati della run consentono di scrivere, nel loro ordine.

    ``params`` e' ``metric_runs.params`` (un dizionario, chiavi che possono
    mancare); ``grafo`` e' ``RunRow.graph_params`` (campi tipizzati, valori che
    possono essere ``None``). Due argomenti e non uno fuso: sono due sorgenti con
    due statuti diversi, e unirli qui produrrebbe proprio il blob unico che
    ``api/models.py`` evita di servire.
    """
    regole: list[Regola] = []
    for codice, chiavi, etichetta, costruttore in _DEFINIZIONI:
        if not params or any(chiave not in params for chiave in chiavi):
            continue
        reso = costruttore(params)
        if reso is None:
            continue
        valore, spiegazione = reso
        regole.append(Regola(codice, valore, etichetta, spiegazione))
    for codice, etichetta, costruttore in _DEFINIZIONI_GRAFO:
        if grafo is None:
            continue
        reso = costruttore(grafo)
        if reso is None:
            continue
        valore, spiegazione = reso
        regole.append(Regola(codice, valore, etichetta, spiegazione))
    return tuple(regole)


# --- i parametri per area, per i Dettagli tecnici ----------------------------
#
# Le aree sono quelle dei commenti di sezione di ``job/config.py``
# (``MetricParams``): non un raggruppamento inventato qui, la stessa divisione
# che il job usa per sé. Cio' che non e' elencato finisce sotto ALTRI — vedi la
# docstring del modulo.

ALTRI = "Altri parametri"

AREE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Soppressione", ("min_cardinality", "min_nodes_publish")),
    ("Ammissione degli archi", ("min_edge_weight", "voice_structural_min_sessions")),
    ("Robustezza", ("removal_fractions",)),
    (
        "Baseline casuale",
        (
            "baseline_repetitions",
            "baseline_downgrade_nodes",
            "baseline_repetitions_reduced",
            "seed",
        ),
    ),
    (
        "Community",
        (
            "leiden_objective",
            "leiden_iterations",
            "jaccard_match_min",
            "merge_min_share",
            "min_node_overlap",
            "community_size_buckets",
            "partition_matching",
        ),
    ),
    (
        "Coorti",
        (
            "k_connections",
            "partner_min_interactions",
            "cohort_layer_scopes",
            "retention_horizons_days",
            "reach_horizons_days",
            "min_observation_days",
            "cohort_max_age_days",
            "exclude_suspected_rejoins",
        ),
    ),
    ("Calcolabilità", ("min_nodes_structural", "min_modularity_z")),
    ("Costruzione del grafo delle metriche", ("directed_layer_projection", "centrality")),
)


@dataclass(frozen=True)
class Area:
    nome: str
    voci: tuple[tuple[str, Any], ...]


def per_area(params: Optional[Mapping[str, Any]]) -> tuple[Area, ...]:
    """I parametri raggruppati, senza perderne nemmeno uno.

    Le aree vuote non compaiono; le chiavi che nessuna area nomina compaiono
    tutte insieme in fondo, ordinate.
    """
    if not params:
        return ()
    aree: list[Area] = []
    nominate: set[str] = set()
    for nome, chiavi in AREE:
        voci = tuple((c, params[c]) for c in chiavi if c in params)
        nominate.update(chiavi)
        if voci:
            aree.append(Area(nome, voci))
    resto = tuple(sorted((c, v) for c, v in params.items() if c not in nominate))
    if resto:
        aree.append(Area(ALTRI, resto))
    return tuple(aree)


def chiavi_note() -> frozenset[str]:
    """Ogni chiave che ``AREE`` nomina. La usa il test che impedisce a un
    parametro nuovo del job di finire sotto ALTRI senza che nessuno lo decida."""
    return frozenset(c for _nome, chiavi in AREE for c in chiavi)


def chiavi_delle_regole() -> frozenset[str]:
    """Ogni chiave di ``params`` che una regola legge. La usa il test che vieta
    a una chiave grezza di ricomparire nel template di Stato."""
    return frozenset(c for _codice, chiavi, _etichetta, _f in _DEFINIZIONI for c in chiavi)
