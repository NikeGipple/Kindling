"""Vista Coorti: dai gruppi dell'API alle righe da rendere.

Tutto cio' che la vista DECIDE sta qui, in funzioni pure; il template decide solo
l'aspetto. Le decisioni vengono da ``docs/architettura/dashboard.md`` §4 "La
vista Coorti, in dettaglio" e §5.

- **Due domande, una popolazione condivisa.** *Con quanta rapidita' un nuovo
  membro si integra?* (l'onboarding, due ambiti) e *quanti restano?* (la
  retention, tre orizzonti) sono domande diverse, e l'incrocio fra le due e' il
  senso della metrica (``modello-metriche.md`` §5.5). Stanno nella stessa vista e
  nella stessa riga di gruppo, mai in due tabelle.
- **Un gruppo per coorte, non cinque righe indipendenti.** ``n_effective``,
  ``excluded_rejoins``, ``observation_days``, ``is_mature``,
  ``has_snapshot_coverage``, ``is_survivors_only`` e ``significant`` NON
  dipendono dal ``layer_scope`` ne' dall'orizzonte: ``job/metrics.py`` calcola
  ``cohort_members`` una volta sola e lo passa identico a tutte e cinque le
  chiamate. Sono quindi **colonne condivise**, scritte una volta per gruppo;
  divergono solo le uscite della curva di sopravvivenza e, per la retention,
  ``is_computable``/``not_computable_reason``.
- **Le etichette di riga si compongono** (regola 8): zero, una o due
  ("non significativo", "solo sopravvissuti"), mai scelte fra loro. Una riga
  soppressa non ne porta nessuna — ``suppress()`` azzera anche
  ``is_survivors_only``, quindi l'informazione a livello di dato non esiste.
- **Nessun grafico di serie**, e non per dimenticanza: ``limit=1`` porta un solo
  snapshot, e venticinque coorti indipendenti non sono poche grandezze
  comparabili su molti snapshot (§4, "Rotta e dati"). La tabella E' la vista.
- **``censored_by_leave`` non e' una colonna**: e' un sotto-conteggio di
  ``censored_count``, e si mostra come nota della cella.

``quality.details`` non si legge in questo modulo, per nessuna ragione (regola 2):
qui non servirebbe comunque, perche' i tre motivi di non significativita' sono
tutti colonne tipizzate (``is_mature``, ``has_snapshot_coverage``,
``is_survivors_only``) — l'unica vista in cui il follow-up di §5 e' gia' fatto,
dalla migration ``0007``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from api.models import CohortGroup, OnboardingRow, RetentionRow
from job.config import MetricParams

from .qualifica import precisione_colonna
from .robustezza import Snapshot

_PARAMS = MetricParams()

# Gli ambiti e gli orizzonti del job, nell'ordine del job: due gruppi di colonne
# di integrazione e tre di retention. Importati, non riscritti — un ambito
# aggiunto al job compare qui senza che nessuno se ne ricordi.
AMBITI = tuple(_PARAMS.cohort_layer_scopes)
ORIZZONTI = tuple(_PARAMS.retention_horizons_days)
GIORNI_MATURITA = _PARAMS.min_observation_days
K_CONNESSIONI = _PARAMS.k_connections

NOMI_AMBITO = {"any": "qualunque interazione", "voice": "solo voce"}

# Le colonne numeriche della curva, per ambito. Nessuna sta in un gruppo di
# precisione aritmetica (dashboard.md 5, "Le colonne legate da un'operazione"):
# mediana, quartili e frazioni raggiunte sono uscite indipendenti della stessa
# curva di Kaplan-Meier, non l'una calcolata dall'altra con una formula che si
# legge in tabella. Stesso criterio gia' applicato a node_overlap e
# stability_jaccard in Community, stesso esito (nessun gruppo).
COLONNE_INTEGRAZIONE = (
    "event_count",
    "censored_count",
    "median_days_to_k",
    "p25_days_to_k",
    "p75_days_to_k",
    "reached_by_14d",
    "reached_by_28d",
)
COLONNA_RETENTION = "retained_fraction"

# I campi che le cinque righe di un gruppo condividono per costruzione. Sono
# anche l'elenco su cui si cercano le divergenze: vedi Gruppo.divergenze.
CONDIVISI_ONBOARDING = (
    "n_effective",
    "excluded_rejoins",
    "observation_days",
    "is_mature",
    "has_snapshot_coverage",
    "is_survivors_only",
    "significant",
)
CONDIVISI_RETENTION = ("n_effective", "excluded_rejoins", "is_survivors_only")


def maturita(riga: OnboardingRow) -> str:
    """``is_mature`` con ``observation_days`` accanto, mai da solo.

    "Non matura" senza il numero di giorni non dice quanto manca, e la domanda
    che un admin si fa davanti a una coorte immatura e' esattamente quella
    (dashboard.md 4: per una coorte immatura la domanda e' "quando matura").
    Il numero e' in ``quality``, non in ``values``: ``cella()`` non lo rende, e
    non deve — non e' un valore di metrica.
    """
    q = riga.quality
    if q.observation_days is None:
        return "—"
    giorni = "1 giorno" if q.observation_days == 1 else f"{q.observation_days} giorni"
    if q.is_mature:
        return f"sì · {giorni}"
    return f"no · {giorni} su {GIORNI_MATURITA}"


def esclusi(riga: OnboardingRow) -> str:
    """``excluded_rejoins``: quanti rientri sospetti sono usciti dal conteggio.

    Sta accanto a ``n`` perche' i due si leggono insieme: ``n`` e' chi e' stato
    contato, questo e' chi e' stato tolto. Un trattino dove il job non lo ha
    scritto, mai uno zero: "nessuno escluso" e "non lo sappiamo" sono cose
    diverse, ed e' la stessa distinzione fra zero e NULL che il layer di calcolo
    tiene ferma in tabella.
    """
    quanti = riga.quality.excluded_rejoins
    return "—" if quanti is None else str(quanti)


def copertura(riga: OnboardingRow) -> str:
    """``has_snapshot_coverage``: c'era un grafo sui primi giorni della coorte?

    Non ha un'etichetta di riga propria, e non deve averne una (§4): si legge da
    questa colonna, come ``maturita``. Un "no" qui e' la ragione per cui un
    ``raggiunta a 14g`` pari a zero sulla stessa riga misura l'assenza di
    osservazione e non l'assenza di integrazione.
    """
    if riga.quality.has_snapshot_coverage is None:
        return "—"
    return "sì" if riga.quality.has_snapshot_coverage else "no"


def censura(riga: OnboardingRow) -> Optional[str]:
    """La nota della cella ``censurati``: quanti dei censurati sono usciti.

    ``censored_by_leave`` NON e' una colonna a se' (§4): non e' un terzo esito
    accanto a eventi e censure, e' un dettaglio del secondo — in
    ``SurvivalCurve`` e' un sottoinsieme filtrato dello stesso insieme, non un
    conteggio indipendente. Come colonna propria suggerirebbe il contrario.
    ``None`` quando non c'e' niente da dire: zero uscite, o riga soppressa.
    """
    quante = riga.values.censored_by_leave
    if not quante:
        return None
    return f"di cui {quante} per uscita dal server"


@dataclass
class Gruppo:
    """Una coorte: una riga di qualificazione condivisa, due curve, tre orizzonti."""

    cohort_start: date
    onboarding: dict[str, OnboardingRow]
    retention: dict[int, RetentionRow]
    # La riga da cui si leggono le colonne condivise. E' una riga di onboarding
    # perche' is_mature/has_snapshot_coverage vivono solo li' (OnboardingQuality).
    guida: OnboardingRow

    @property
    def soppressa(self) -> bool:
        """La soppressione e' per coorte, non per riga: stessa soglia sullo stesso
        ``n_effective`` per tutte e cinque. Se una sola riga risultasse soppressa,
        ``divergenze`` lo direbbe; qui basta la guida."""
        return self.guida.quality.suppressed

    @property
    def divergenze(self) -> tuple[str, ...]:
        """I campi condivisi che NON coincidono fra le righe del gruppo.

        Il job non puo' produrne (stessa chiamata sugli stessi ``members``), e un
        test lo verifica su ogni coorte del fixture. Ma il contratto dell'API non
        lo vieta, e il template non deve *assumerlo*: se un giorno divergessero,
        questa vista lo dichiara invece di mostrare il valore di una riga sola
        spacciandolo per quello del gruppo. Ramo difensivo, nel codice e non nella
        fixture (dashboard.md 5, "I rami difensivi").
        """
        fuori: list[str] = []
        righe = list(self.onboarding.values())
        for campo in CONDIVISI_ONBOARDING:
            if len({getattr(r.quality, campo) for r in righe}) > 1:
                fuori.append(campo)
        tutte = [*righe, *self.retention.values()]
        if len({r.quality.suppressed for r in tutte}) > 1:
            fuori.append("suppressed")
        for campo in CONDIVISI_RETENTION:
            if len({getattr(r.quality, campo) for r in tutte}) > 1:
                fuori.append(campo)
        return tuple(dict.fromkeys(fuori))


@dataclass
class Vista:
    """La tabella di uno snapshot: un gruppo per coorte, dalla piu' recente."""

    snapshot: Optional[Snapshot]
    gruppi: list[Gruppo]
    # Decimali per (ambito, campo) e per orizzonte: la precisione si sceglie per
    # COLONNA, e le colonne di 'any' e di 'voice' sono colonne diverse della
    # stessa tabella — come i blocchi di Robustezza, uniformarle creerebbe una
    # colonna sola che attraversa i due ambiti (dashboard.md 5).
    decimali: dict[tuple[str, str], int] = field(default_factory=dict)
    decimali_retention: dict[int, int] = field(default_factory=dict)

    @property
    def vuota(self) -> bool:
        """Guild osservata, nessuno snapshot: il calcolo non e' ancora girato."""
        return self.snapshot is None

    @property
    def pubblicate(self) -> list[Gruppo]:
        return [g for g in self.gruppi if not g.soppressa]

    @property
    def soppresse(self) -> int:
        return sum(1 for g in self.gruppi if g.soppressa)

    @property
    def sopravvissuti(self) -> int:
        """Quante coorti pubblicate contano solo chi era gia' presente.

        E' un conteggio di righe in vista, non un aggregato di metriche: nessuna
        somma fra ambiti o fra orizzonti (regola 3 e §4). Serve alla frase che §6
        chiede a questa vista — in produzione sono 17 su 25, e senza il numero la
        pagina sembra una tabella grigia invece di dirlo.
        """
        return sum(1 for g in self.pubblicate if g.guida.quality.is_survivors_only is True)

    @property
    def significative(self) -> int:
        return sum(1 for g in self.pubblicate if g.guida.quality.significant is True)


def costruisci(gruppi: list[CohortGroup]) -> Vista:
    """La vista dallo snapshot piu' recente fra quelli ricevuti.

    Con ``limit=1`` lo snapshot e' uno solo. La selezione c'e' lo stesso, e non e'
    difensiva per abitudine: rende la funzione totale su qualunque risposta
    dell'endpoint (che accetta ``limit`` maggiori) invece di dipendere da cosa ha
    chiesto il chiamante — e permette di rendere uno snapshot piu' vecchio
    passandogli le sole righe di quello, come i test fanno per gli stati che
    l'ultimo snapshot non mostra.
    """
    if not gruppi:
        return Vista(snapshot=None, gruppi=[])

    ultimo = max(gruppi, key=lambda g: (g.as_of, g.snapshot_id))
    snapshot = Snapshot(ultimo.snapshot_id, ultimo.as_of)
    scelti = [g for g in gruppi if g.snapshot_id == snapshot.snapshot_id]

    costruiti: list[Gruppo] = []
    # cohort_start DESC: le coorti recenti — le uniche che possono ancora
    # cambiare, e le uniche che possono diventare significative — in cima, e non
    # sepolte sotto la sequenza delle coorti anteriori all'ancora (§4).
    for g in sorted(scelti, key=lambda g: g.cohort_start, reverse=True):
        onboarding = {r.layer_scope: r for r in g.onboarding}
        if not onboarding:
            # Nessuna riga di onboarding: senza non c'e' niente da qualificare, e
            # inventare una riga vuota mostrerebbe una coorte che l'API non ha
            # servito. Il job scrive sempre un ambito per coorte.
            continue
        guida = onboarding.get(AMBITI[0]) or next(iter(onboarding.values()))
        costruiti.append(
            Gruppo(
                cohort_start=g.cohort_start,
                onboarding=onboarding,
                retention={r.horizon_days: r for r in g.retention},
                guida=guida,
            )
        )

    return Vista(
        snapshot=snapshot,
        gruppi=costruiti,
        decimali=decimali_integrazione(costruiti),
        decimali_retention=decimali_retention(costruiti),
    )


def decimali_integrazione(gruppi: list[Gruppo]) -> dict[tuple[str, str], int]:
    """La precisione di ogni colonna di curva, per ambito.

    Si calcola sulle righe PUBBLICATE di questa tabella: una riga soppressa non ha
    valori, e contarla come colonna di zeri abbasserebbe la risoluzione dichiarata
    dalla colonna.
    """
    fuori: dict[tuple[str, str], int] = {}
    for ambito in AMBITI:
        righe = [
            g.onboarding[ambito]
            for g in gruppi
            if ambito in g.onboarding and not g.onboarding[ambito].quality.suppressed
        ]
        for campo in COLONNE_INTEGRAZIONE:
            fuori[(ambito, campo)] = precisione_colonna(getattr(r.values, campo) for r in righe)
    return fuori


def decimali_retention(gruppi: list[Gruppo]) -> dict[int, int]:
    """La precisione della colonna ``trattenuti``, un orizzonte alla volta.

    Tre colonne, tre precisioni: 7, 14 e 28 giorni sono letture diverse della
    stessa coorte, e non si sommano ne' si confrontano come fossero una serie.
    """
    fuori: dict[int, int] = {}
    for orizzonte in ORIZZONTI:
        righe = [
            g.retention[orizzonte]
            for g in gruppi
            if orizzonte in g.retention and not g.retention[orizzonte].quality.suppressed
        ]
        fuori[orizzonte] = precisione_colonna(r.values.retained_fraction for r in righe)
    return fuori


def frase_di_stato(vista: Vista) -> str:
    """Cosa dice questa pagina, in una riga, prima della tabella.

    §6 chiede a questa vista una frase precisa — "il 68% delle coorti conta solo
    chi e' rimasto da prima che Kindling iniziasse a osservare" — e la chiede
    perche' la tabella da sola somiglia a "non c'e' niente", che e' un'altra cosa
    (regola 1). I numeri sono conteggi di righe in vista, non metriche.
    """
    if not vista.gruppi:
        return ""
    pubblicate = len(vista.pubblicate)
    parti = [f"{len(vista.gruppi)} coorti su questo snapshot"]
    if vista.soppresse:
        parti.append(f"{vista.soppresse} sotto la soglia di pubblicazione")
    if vista.sopravvissuti:
        # Il denominatore sono le coorti PUBBLICATE, non tutte: su una riga
        # soppressa is_survivors_only e' None — non "false" — perche' suppress()
        # lo azzera, quindi di quelle coorti questa vista non sa se contino solo
        # chi era gia' presente. Metterle al denominatore darebbe una percentuale
        # piu' bassa del vero, che e' il tipo di numero che sembra migliore.
        quota = round(vista.sopravvissuti / pubblicate * 100)
        parti.append(
            f"{vista.sopravvissuti} delle {pubblicate} pubblicate contano solo chi "
            f"era già presente quando l'osservazione è iniziata ({quota}%)"
        )
    parti.append(
        f"{vista.significative} distinguibili dal rumore"
        if vista.significative
        else "nessuna ancora distinguibile dal rumore"
    )
    return ", ".join(parti) + "."
