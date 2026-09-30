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
    # Le cinque domande della vista Robustezza (30/09/2026). Tutte con il
    # prefisso "q-robustezza-", per la stessa ragione di "q-coorte-leggibile".
    # L'ordine di questa mappa e' quello della pagina, cioe' dei GRUPPI sotto.
    "q-robustezza-come": "Come fa Kindling a capire se il server dipende da poche persone?",
    "q-robustezza-barra": "Come si leggono la barra e la tacca?",
    "q-robustezza-chi": "Posso sapere chi sono le persone più centrali?",
    "q-robustezza-leggibile": "Quando sarà leggibile Robustezza?",
    "q-robustezza-tipi": "Perché quattro righe separate e non un risultato unico?",
    # L'id resta "q-segnate" anche se dal 26/09/2026 il titolo e' un altro: un
    # indirizzo che cambia e' un link che atterra in cima alla pagina senza
    # nessun errore, e Stato ci rimanda da prima.
    "q-segnate": "Perché le coorti partono dall'arrivo del bot?",
    # Le tre domande della vista Coorti. "q-coorte-leggibile" e non
    # "q-leggibile", che e' gia' la domanda su Community: due id che differiscono
    # di una parola sono due indirizzi facili da scambiare scrivendo un rimando.
    "q-coorte-leggibile": "Quando una coorte diventa leggibile?",
    "q-vocale": "Qual è la differenza fra le due barre di «si integrano»?",
    "q-barre": "Perché barre e non numeri?",
    "q-leggibile": "Quando sarà leggibile Community?",
    "q-aggiorna": "Ogni quanto si aggiornano i dati?",
    # L'unico posto fuori dai Dettagli tecnici in cui si scrive "UTC" a video
    # (dashboard.md 4, "Quale fuso per cosa"): il confine della settimana si
    # spiega qui una volta, non su ogni vista. tests/test_orari.py lo ammette
    # dentro questo id e da nessun'altra parte.
    "q-date": "Che cosa indicano le date?",
    "q-raccoglie": "Cosa raccoglie il bot, esattamente?",
    "q-tecnico": "Dove trovo i dettagli tecnici di un calcolo?",
}


# I gruppi della pagina (30/09/2026, dashboard.md 4 "La pagina Domande"), con le
# assegnazioni del mockup di Robustezza. Un gruppo e' un titolo sopra le sue
# domande; l'ordine dentro un gruppo e' quello di prima, e gli id non cambiano.
# Ogni domanda sta in un gruppo e in uno solo: una domanda nuova senza gruppo
# sparirebbe dalla pagina senza nessun errore, e un test lo verifica.
GRUPPI = (
    ("In generale", ("q-persone", "q-mancanti", "q-voto", "q-passato")),
    ("Robustezza", (
        "q-robustezza-come", "q-robustezza-barra", "q-robustezza-chi",
        "q-robustezza-leggibile", "q-robustezza-tipi",
    )),
    # q-barre vale anche per Robustezza, ma il mockup la lascia qui come domanda
    # aperta: spostarla e' una decisione, non una correzione.
    ("Coorti", ("q-segnate", "q-coorte-leggibile", "q-vocale", "q-barre")),
    ("Community", ("q-leggibile",)),
    ("Dati e aggiornamenti", ("q-aggiorna", "q-date", "q-raccoglie", "q-tecnico")),
)

_NUMERI = {1: "una", 2: "due", 3: "tre", 4: "quattro", 5: "cinque", 6: "sei"}


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


def _soglie_di_rimozione(params: Mapping[str, Any]) -> Optional[str]:
    """``una persona su 20, una su 10, una su 5``, da ``removal_fractions``.

    Con la stessa regola delle intestazioni della vista
    (``robustezza.intestazione_frazione``): "1 su N" solo se N e' intero,
    altrimenti la percentuale. ``None`` se la chiave manca o non e' una lista di
    numeri: la frase che le nominava non compare.
    """
    from . import robustezza as _robustezza  # robustezza importa stato, che importa questo modulo

    valore = params.get("removal_fractions")
    if not isinstance(valore, (list, tuple)) or not valore:
        return None
    if any(isinstance(f, bool) or not isinstance(f, (int, float)) for f in valore):
        return None
    parti = []
    for i, f in enumerate(sorted(valore)):
        intestazione = _robustezza.intestazione_frazione(f)
        if intestazione.startswith("1 su "):
            n = intestazione.removeprefix("1 su ")
            parti.append(f"una persona su {n}" if i == 0 else f"una su {n}")
        else:
            parti.append(f"il {intestazione} delle persone")
    return ", ".join(parti)


def per_gruppo(domande: tuple[Domanda, ...]) -> tuple[tuple[str, tuple[Domanda, ...]], ...]:
    """Le domande nei loro gruppi, nell'ordine di ``GRUPPI``."""
    per_codice = {d.codice: d for d in domande}
    return tuple(
        (titolo, tuple(per_codice[c] for c in codici if c in per_codice))
        for titolo, codici in GRUPPI
    )


def costruisci(
    guild_id: int,
    params: Optional[Mapping[str, Any]],
    *,
    privacy_url: str,
    cadenza_giorni: Optional[int] = None,
) -> tuple[Domanda, ...]:
    """Le domande, con le soglie che i dati della run consentono di citare.

    ``cadenza_giorni`` e' quella OSSERVATA fra le run, non un valore preso da
    ``job/config.py``: la risposta "ogni quanto si aggiornano i dati" descrive
    cio' che e' successo su questo server, e con una run sola — quando la
    cadenza non esiste — la frase che la nominava semplicemente non compare.

    In giorni interi, da ``stato.cadenza_in_giorni``, la stessa della previsione
    di Stato: fino al 28/09/2026 qui c'era la mediana grezza con un decimale, e
    in produzione la pagina diceva "circa ogni 6,9 giorni" mentre Stato
    prevedeva il lunedi' successivo, sette giorni dopo.
    """
    p = params or {}
    cardinalita = _soglia(p, "min_cardinality")
    nodi = _soglia(p, "min_nodes_structural")
    maturita = _soglia(p, "min_observation_days")
    k = _soglia(p, "k_connections")
    ripetizioni = _soglia(p, "baseline_repetitions")
    rimozioni = _soglie_di_rimozione(p)
    quante_soglie = (
        len(p["removal_fractions"]) if rimozioni is not None else None
    )
    giorni_cadenza = cadenza_giorni

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
        # Le cinque domande di Robustezza: testi del mockup approvato, con le
        # soglie da params (dashboard.md 4, "La pagina Domande"). Nessuna spiega
        # solo il caso "concentrato su pochi": una cella leggibile puo' dire
        # "distribuiti", e una risposta a senso unico la farebbe leggere come un
        # allarme.
        Domanda(
            "q-robustezza-come",
            TITOLI["q-robustezza-come"],
            con(
                "Per ogni tipo di interazione costruisce la rete della settimana: chi "
                "ha risposto a chi, chi ha menzionato chi, chi ha reagito ai messaggi "
                "di chi, chi è stato in vocale con chi. Poi simula l'assenza delle "
                "persone che più spesso fanno da ponte fra le altre, e conta quanti "
                "restano collegati fra loro.",
                "Da sola la simulazione non direbbe molto: qualunque rete si sfalda se "
                "si toglie abbastanza gente. Per questo Kindling rifà la prova togliendo "
                "lo stesso numero di persone scelte a caso"
                + (f", {ripetizioni} volte," if ripetizioni is not None else "")
                + " e confronta. Se senza le persone centrali si stacca molta più gente "
                "che senza persone qualunque, i contatti passano da poche persone; se "
                "se ne stacca quanta a caso, sono distribuiti.",
                f"La prova si fa a {_NUMERI.get(quante_soglie, str(quante_soglie))} "
                f"soglie: {rimozioni}."
                if rimozioni is not None and quante_soglie and quante_soglie > 1
                else (f"La prova toglie {rimozioni}." if rimozioni is not None else None),
            ),
        ),
        Domanda(
            "q-robustezza-barra",
            TITOLI["q-robustezza-barra"],
            con(
                "In ogni cella ci sono due prove sulla stessa rete, sulla stessa scala "
                "da «nessuno» a «tutti». La barra dice quanti restano collegati "
                "togliendo le persone più centrali. La tacca dice quanti ne "
                "resterebbero togliendo lo stesso numero di persone qualunque.",
                "Conta la distanza fra le due. Se la barra arriva alla tacca, togliere "
                "le persone centrali fa lo stesso danno che togliere gente a caso: i "
                "contatti sono distribuiti. Se la barra si ferma molto prima della "
                "tacca, senza quelle poche persone si stacca molta più gente del "
                "normale: i contatti passano da loro. Se la barra supera la tacca, le "
                "persone centrali contano meno di persone prese a caso; succede, e non "
                "è un errore.",
                "Barra e tacca riportano la proporzione a una di sette fasce, come le "
                "barre di Coorti: una differenza piccola può non vedersi. I numeri "
                "esatti sono nei dettagli tecnici.",
            ),
            Link(f"/guilds/{guild_id}/dettagli-tecnici#robustezza",
                 "Dettagli tecnici: la robustezza"),
        ),
        Domanda(
            "q-robustezza-chi",
            TITOLI["q-robustezza-chi"],
            con(
                "No. Kindling le individua solo dentro il calcolo, e lì le dimentica: "
                "nel database e in questa dashboard arrivano soltanto quanti restano "
                "collegati, mai chi è stato tolto. Nessun profilo individuale esce da "
                "Kindling verso gli amministratori.",
                (
                    f"Sotto le {nodi} persone attive"
                    if nodi is not None
                    else "Sotto una certa soglia di persone attive"
                )
                + " la vista non mostra i risultati della prova: dipendono da una o due "
                "persone, e in un gruppo piccolo «togliendo una persona si stacca metà "
                "del gruppo» fa pensare subito a qualcuno. È una scelta di leggibilità, "
                "non una protezione: i numeri esatti restano nei dettagli tecnici.",
            ),
        ),
        Domanda(
            "q-robustezza-leggibile",
            TITOLI["q-robustezza-leggibile"],
            con(
                (
                    f"Per ciascun tipo di interazione, quando almeno "
                    f"{plurale(nodi, 'persona interagisce', 'persone interagiscono')} "
                    "in quel modo nella stessa settimana."
                    if nodi is not None
                    else "Per ciascun tipo di interazione, quando abbastanza persone "
                    "interagiscono in quel modo nella stessa settimana."
                )
                + " Sotto quella soglia la prova più piccola toglierebbe una persona "
                "sola, e il risultato racconterebbe un caso più che la struttura del "
                "server.",
                "I tipi si accendono separatamente: le reazioni possono diventare "
                "leggibili molto prima del vocale.",
                "Leggibile non vuol dire che il server dipenda da poche persone: vuol "
                "dire che ci sono abbastanza persone perché la prova dica com'è.",
            ),
        ),
        Domanda(
            "q-robustezza-tipi",
            TITOLI["q-robustezza-tipi"],
            con(
                "Perché sono quattro reti diverse: chi ti risponde non è per forza chi "
                "sta con te in vocale. Un server può reggere bene nelle risposte e "
                "dipendere da poche persone in vocale, e una media nasconderebbe "
                "proprio questo. È la stessa ragione per cui non c'è un punteggio "
                "complessivo.",
            ),
        ),
        Domanda(
            "q-segnate",
            TITOLI["q-segnate"],
            con(
                "Perché di chi è entrato prima dell'arrivo del bot (la data è in cima "
                "a Stato) Kindling vede solo chi è ancora nel server: chi se n'era già "
                "andato non ha lasciato traccia. Un gruppo così conta le persone "
                "rimaste, non quelle entrate, e quanti sono rimasti non si può "
                "calcolare.",
                "Per questo la vista Coorti parte dall'arrivo del bot. I gruppi "
                "precedenti non sono spariti: restano nei dettagli tecnici, marcati "
                "come tali.",
            ),
        ),
        Domanda(
            "q-coorte-leggibile",
            TITOLI["q-coorte-leggibile"],
            con(
                (
                    f"Quando sono passati almeno "
                    f"{plurale(maturita, 'giorno', 'giorni')} dall'ingresso "
                    "dell'ultimo arrivato"
                    if maturita is not None
                    else "Quando è passato abbastanza tempo dall'ingresso dell'ultimo "
                    "arrivato"
                )
                + ", e Kindling osservava il server per tutto quel periodo. Prima, i "
                "numeri dipendono da pochissime persone e cambiano molto da un "
                "calcolo all'altro.",
                "Finché non lo è, la sua riga in Coorti dice da quale calcolo lo "
                "diventerà.",
            ),
        ),
        Domanda(
            "q-vocale",
            TITOLI["q-vocale"],
            con(
                "La barra arancio conta ogni modo di interagire: risposte, menzioni, "
                "reazioni e tempo passato insieme in vocale. La barra blu conta solo "
                "il tempo passato insieme in vocale. La prima comprende anche il "
                "vocale: non è «scritto contro vocale».",
                f"La soglia è la stessa per tutte e due: almeno "
                f"{plurale(k, 'persona diversa', 'persone diverse')}."
                if k is not None
                else None,
                "Sono due stime separate della stessa settimana, non due parti di un "
                "totale: non si sommano e non si sottraggono.",
            ),
        ),
        Domanda(
            "q-barre",
            TITOLI["q-barre"],
            con(
                "Per leggibilità. In un gruppo di poche persone un numero esatto "
                "(«1 su 14») fa pensare subito a qualcuno in particolare, e non dice "
                "più di una barra quasi vuota. Le barre sono divise in quinti e "
                "riportano la proporzione a una di sette fasce, da «nessuno» a «tutti».",
                "I numeri esatti non sono nascosti: stanno nei dettagli tecnici, per "
                "chi deve verificare un calcolo.",
            ),
            Link(f"/guilds/{guild_id}/dettagli-tecnici#coorti", "Dettagli tecnici: le coorti"),
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
            "q-date",
            TITOLI["q-date"],
            # "Comprende i dati fino a", e non "copre una settimana": la finestra
            # del grafo e' di sette giorni (--window-days in
            # ops/kindling-weekly.sh), ma le coorti risalgono fino a
            # cohort_max_age_days di job/config.py, mesi e non giorni: per Coorti
            # "una settimana" sarebbe falso. Il confine e' invece lo stesso per
            # tutto: as_of (modello-grafo.md 5.1).
            con(
                "Indicano giorni, non orari: il giorno in cui il bot è arrivato o è "
                "uscito dal server, e quello di ogni aggiornamento. I giorni seguono "
                "l'ora italiana.",
                "Ogni aggiornamento comprende i dati fino alle 00:00 di lunedì in "
                "tempo universale (UTC), cioè le 2 di notte in Italia d'estate e l'una "
                "d'inverno: quello che succede il lunedì prima di quell'ora entra "
                "nell'aggiornamento di quel lunedì, quello che succede dopo nel "
                "successivo. Vale anche per le coorti: chi entra in quelle ore fa "
                "parte del gruppo della settimana prima.",
                "Gli orari esatti di ogni calcolo sono nei Dettagli tecnici.",
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
