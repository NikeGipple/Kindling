"""La pagina Domande: le risposte che le viste non possono ripetere ogni volta.

Tre vincoli, tutti e tre con una ragione che non e' di stile (dashboard.md §4,
"La pagina Domande"):

- **Ogni domanda ha un ``id`` stabile.** Le viste ci rimandano con un'ancora
  invece di ripetere la spiegazione, e un ``id`` che cambia e' un link che
  atterra in cima alla pagina senza dare nessun errore. Gli ``id`` stanno qui,
  in un posto solo, e ``stato.py`` costruisce i suoi rimandi dagli stessi nomi.
- **Le risposte sono generiche.** Valgono per ogni server osservato: nessuna
  data fissa, nessun nome, nessun numero di *questo* server. Dove servirebbe una
  data si rimanda ("la data è in cima a Stato"), perche' una data scritta qui
  sarebbe giusta per un server e falsa per il successivo.
- **Le soglie si leggono da ``params``**, come in Stato, e con la stessa regola:
  **chiave mancante, frase che non compare**. Ogni domanda ha almeno un
  paragrafo che non dipende da nessuna chiave, quindi nessuna domanda puo'
  ridursi a un ``<details>`` vuoto.

La prima domanda nomina il destinatario — "in questa dashboard gli
amministratori vedono..." — e non dice "Kindling non sa chi sei", che sarebbe
falso: il bot registra ``author_id`` pseudonimizzati, e l'invariante di
``stato-progetto.md`` §3 riguarda cio' che ESCE da qui, non cio' che esiste.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional

from .regole import plurale

# Il testo di ogni domanda, in un posto solo. Lo legge anche ``stato.py``, che
# lo usa come etichetta dei rimandi di "Cosa puoi leggere oggi": un rimando che
# dice una cosa e atterra su una domanda che ne dice un'altra e' una promessa
# rotta che nessun errore segnala (CLAUDE.md 7).
TITOLI = {
    "q-persone": "Posso vedere un singolo membro, o chi è isolato?",
    "q-mancanti": "Perché alcuni numeri mancano o sono in grigio?",
    "q-voto": "Perché non c'è un punteggio complessivo della community?",
    "q-passato": "Kindling sa cosa è successo prima del suo arrivo nel server?",
    "q-segnate": "Perché le coorti più vecchie sono segnate?",
    "q-leggibile": "Quando sarà leggibile Community?",
    "q-aggiorna": "Ogni quanto si aggiornano i dati?",
    "q-raccoglie": "Cosa raccoglie il bot, esattamente?",
    "q-tecnico": "Dove trovo i dettagli tecnici di un calcolo?",
}


@dataclass(frozen=True)
class Link:
    url: str
    testo: str


@dataclass(frozen=True)
class Domanda:
    codice: str
    domanda: str
    paragrafi: tuple[str, ...]
    link: Optional[Link] = None


def _soglia(params: Mapping[str, Any], chiave: str) -> Optional[Any]:
    valore = params.get(chiave)
    if isinstance(valore, bool) or not isinstance(valore, (int, float)):
        return None
    return valore


def costruisci(
    guild_id: int,
    params: Optional[Mapping[str, Any]],
    *,
    privacy_url: str,
    cadenza: Optional[timedelta] = None,
) -> tuple[Domanda, ...]:
    """Le nove domande, con le soglie che i dati della run consentono di citare.

    ``cadenza`` e' quella OSSERVATA fra le run (``stato.cadenza_osservata``), non
    un valore preso da ``job/config.py``: la risposta "ogni quanto si aggiornano
    i dati" descrive cio' che e' successo su questo server, e con una run sola —
    quando la cadenza non esiste — la frase che la nominava semplicemente non
    compare.
    """
    p = params or {}
    cardinalita = _soglia(p, "min_cardinality")
    nodi = _soglia(p, "min_nodes_structural")
    giorni_cadenza = (
        round(cadenza.total_seconds() / 86400, 1) if cadenza is not None else None
    )

    def con(*paragrafi: Optional[str]) -> tuple[str, ...]:
        return tuple(testo for testo in paragrafi if testo)

    return (
        Domanda(
            "q-persone",
            TITOLI["q-persone"],
            con(
                "No. In questa dashboard gli amministratori vedono soltanto gruppi: "
                "nessun nome, nessun profilo, nessuna riga che riguardi una persona "
                "sola. Kindling guarda la community nel suo insieme.",
                f"La soglia è {plurale(cardinalita, 'membro', 'membri')}: un numero che "
                "ne riguarderebbe di meno non viene mostrato affatto."
                if cardinalita is not None
                else None,
            ),
        ),
        Domanda(
            "q-mancanti",
            TITOLI["q-mancanti"],
            con(
                "Mancano quando riguarderebbero troppe poche persone: al loro posto "
                "c'è un simbolo e il motivo per cui la cella è vuota."
                + (
                    f" La soglia è {plurale(cardinalita, 'persona', 'persone')}."
                    if cardinalita is not None
                    else ""
                ),
                "Sono in grigio quando il numero c'è ma non è distinguibile da quello "
                "che si otterrebbe per caso. Si mostra lo stesso, dequalificato: "
                "nasconderlo darebbe l'idea che non esista, che è una cosa diversa.",
            ),
        ),
        Domanda(
            "q-voto",
            TITOLI["q-voto"],
            con(
                "Perché un numero solo nasconderebbe proprio quello che le viste "
                "servono a mostrare. Robustezza, Community e Coorti rispondono a tre "
                "domande diverse, e una community può stare bene su una e male "
                "sull'altra: un punteggio unico darebbe una risposta dove ci sono tre "
                "domande.",
            ),
        ),
        Domanda(
            "q-passato",
            TITOLI["q-passato"],
            con(
                "No, e non lo recupererà mai. Registra solo quello che accade "
                "dall'arrivo del bot in avanti (la data è in cima a Stato). Le "
                "interazioni di prima non sono da nessuna parte, e chi se n'era già "
                "andato non ha lasciato traccia.",
            ),
        ),
        Domanda(
            "q-segnate",
            TITOLI["q-segnate"],
            con(
                "Di chi è entrato prima dell'arrivo del bot (la data è in cima a "
                "Stato) Kindling vede solo chi è ancora nel server. Quei gruppi "
                "portano l'etichetta «solo sopravvissuti»: i loro numeri contano le "
                "persone rimaste, non quelle entrate, e la permanenza non si può "
                "calcolare.",
            ),
        ),
        Domanda(
            "q-leggibile",
            TITOLI["q-leggibile"],
            con(
                f"Quando la rete di quel tipo di interazione ha almeno "
                f"{plurale(nodi, 'persona attiva', 'persone attive')} nella settimana "
                "calcolata."
                if nodi is not None
                else "Quando la rete di quel tipo di interazione è abbastanza grande.",
                "E quando i gruppi che emergono sono più netti di quanto lo sarebbero "
                "in una rete formata a caso. Kindling lo verifica da solo a ogni "
                "calcolo: non c'è niente da impostare, e le due condizioni non si "
                "accendono necessariamente insieme.",
            ),
        ),
        Domanda(
            "q-aggiorna",
            TITOLI["q-aggiorna"],
            con(
                f"Finora i calcoli si sono succeduti circa ogni "
                f"{plurale(giorni_cadenza, 'giorno', 'giorni')}. È la cadenza che "
                "Kindling misura sulle date dei calcoli già fatti, non una promessa "
                "sul futuro: se cambiasse, cambierebbe anche questa risposta."
                if giorni_cadenza is not None
                else None,
                "La data dell'ultimo aggiornamento, e quella del prossimo previsto "
                "quando c'è abbastanza storia per stimarla, sono in cima a Stato "
                "insieme al calendario di tutti i calcoli fatti.",
            ),
        ),
        Domanda(
            "q-raccoglie",
            TITOLI["q-raccoglie"],
            con(
                "È descritto nell'informativa sulla privacy, che è il documento che "
                "fa fede: qui un elenco parallelo finirebbe per divergere da quello.",
            ),
            Link(privacy_url, "Informativa sulla Privacy"),
        ),
        Domanda(
            "q-tecnico",
            TITOLI["q-tecnico"],
            con(
                "In una pagina a parte: versione del codice, parametri e diagnostica "
                "di ogni esecuzione. Serve a chi gestisce Kindling, quando un numero "
                "sembra sbagliato.",
            ),
            Link(f"/guilds/{guild_id}/dettagli-tecnici", "Dettagli tecnici"),
        ),
    )
