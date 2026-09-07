"""I modelli di risposta, e la forma che impedisce ai flag di perdersi.

La decisione di docs/architettura/api.md 3, in codice: ogni riga di metrica e'
``{chiave..., quality, values}``. I valori stanno in un oggetto annidato, la
qualificazione in un altro accanto.

Perche' non un oggetto per OGNI valore (``targeted_excess: {value, significant}``):
sarebbe la forma piu' difficile da leggere male, ma applicata a tutti i campi
sarebbe falsa — quasi tutti i flag qualificano la RIGA, non il singolo campo, e
ripeterli implicherebbe una granularita' che i dati non hanno.

Ma la granularita' per valore esiste, in due punti (modello-metriche.md 7.3), ed
entrambi stanno nelle coorti: ``median_reached`` qualifica solo
``median_days_to_k``, e ``is_computable``/``not_computable_reason`` qualificano
solo ``retained_fraction``. Quei due stanno quindi in ``values``, accanto al
numero che qualificano: metterli in ``quality`` li farebbe sembrare giudizi
sull'intera riga.

Cio' che rende difficile perdere i flag:

- per arrivare a un numero bisogna passare da ``values``, e ``quality`` e' li'
  come suo fratello, non in coda a dieci campi;
- ``quality`` e' obbligatorio in ``MetricRow``, non Optional: una risposta nuova
  che se ne dimentica non compila lo schema invece di partire e perdere i flag
  in produzione;
- su una riga soppressa ``values`` c'e', con tutti i campi a None. Se sparisse,
  "soppressa" somiglierebbe ad "assente" a livello di JSON — la stessa
  confusione tra zero e NULL che il layer di calcolo evita in tabella.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

# Nessuna forward reference e nessuna API specifica di pydantic v2: i modelli
# sono definiti in ordine di dipendenza, cosi' non serve model_rebuild() (v2) ne'
# update_forward_refs() (v1). requirements.txt pinna la v2, ma un modello che
# gira su entrambe evita che una differenza di versione tra ambiente di sviluppo
# e droplet si manifesti come una risposta di forma diversa.


class Quality(BaseModel):
    """La qualificazione di una riga: quanto vale quello che c'e' in ``values``.

    ``details`` e' DIAGNOSTICA, NON CONTRATTO: il suo contenuto cambia insieme
    al codice del job, e nessun consumatore deve dipendere dalle sue chiavi. Ha
    lo stesso statuto di ``metric_runs.stats`` — la scatola nera
    dell'esecuzione, utile quando un numero sembra sbagliato, non una struttura
    su cui costruire un grafico.
    """

    n_effective: Optional[int] = Field(
        default=None,
        description="Numerosita' su cui la riga e' calcolata. None se soppressa.",
    )
    suppressed: bool = Field(
        description="Sotto la soglia di cardinalita': i valori sono tutti None."
    )
    suppression_reason: Optional[str] = None
    significant: Optional[bool] = Field(
        default=None,
        description=(
            "None = non valutata (riga soppressa). False = valutata e sotto i "
            "minimi di calcolabilita': il valore c'e' ma non e' distinguibile "
            "dal rumore."
        ),
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostica dell'esecuzione. Non e' un contratto: le chiavi cambiano.",
    )


class MetricRow(BaseModel):
    """Base di ogni riga di metrica. ``quality`` e' richiesto, non opzionale."""

    quality: Quality


# --- robustezza ------------------------------------------------------------


class RobustnessValues(BaseModel):
    nodes_removed: Optional[int] = None
    giant_before: Optional[float] = None
    giant_after_targeted: Optional[float] = None
    components_after_targeted: Optional[int] = None
    giant_after_random_mean: Optional[float] = None
    giant_after_random_sd: Optional[float] = None
    components_after_random_mean: Optional[float] = None
    targeted_excess: Optional[float] = Field(
        default=None,
        description=(
            "Quanta componente gigante in piu' si perde attaccando i connettori "
            "rispetto al caso. Puo' essere NEGATIVO e non e' clampato: significa "
            "che i nodi piu' centrali erano meno critici di nodi presi a caso."
        ),
    )
    targeted_z: Optional[float] = None


class RobustnessRow(MetricRow):
    snapshot_id: int
    as_of: datetime
    layer: str
    removal_fraction: float
    values: RobustnessValues


# --- community -------------------------------------------------------------


class CommunitySizeValues(BaseModel):
    community_count: Optional[int] = None
    member_count: Optional[int] = None


class CommunitySizeBucket(MetricRow):
    """Un bucket della distribuzione delle dimensioni.

    Porta il proprio ``quality`` perche' la soppressione qui e' per bucket, e
    puo' essere secondaria: una sola cella soppressa accanto a un totale
    pubblicato si ricava per differenza, quindi ne cade anche una seconda.
    """

    bucket: str
    values: CommunitySizeValues


class CommunityValues(BaseModel):
    community_count: Optional[int] = None
    modularity: Optional[float] = None
    modularity_random_mean: Optional[float] = None
    modularity_random_sd: Optional[float] = None
    modularity_z: Optional[float] = None
    node_overlap: Optional[float] = None
    stability_jaccard: Optional[float] = None
    previous_gap_days: Optional[float] = Field(
        default=None,
        description=(
            "Giorni tra questo as_of e quello dello snapshot con cui e' "
            "calcolata stability_jaccard. Qualifica il valore: con cadenza "
            "settimanale ci si aspetta 7. Un numero diverso non e' un errore, "
            "e' l'informazione che il confronto e' tra finestre a una "
            "distanza diversa da quella attesa — con finestre da 7 giorni, "
            "un gap di 1-2 giorni significa finestre sovrapposte all'85-95%, "
            "e stability_jaccard misura in gran parte quella sovrapposizione "
            "invece della ricomposizione delle community. None quando "
            "stability_jaccard e' None."
        ),
    )
    communities_born: Optional[int] = None
    communities_dissolved: Optional[int] = None
    communities_merged: Optional[int] = None
    communities_split: Optional[int] = None


class CommunityRow(MetricRow):
    snapshot_id: int
    as_of: datetime
    layer: str
    previous_snapshot_id: Optional[int] = Field(
        default=None,
        description=(
            "Id opaco dello snapshot con cui e' calcolata la stabilita'. Il "
            "consumatore non puo' risolverlo: graph_snapshots non e' leggibile."
        ),
    )
    values: CommunityValues
    sizes: list[CommunitySizeBucket] = Field(
        default_factory=list,
        description=(
            "Distribuzione delle dimensioni di QUESTA partizione. Sta qui e non "
            "in un endpoint proprio perche' la sua soppressione secondaria e' "
            "calcolata rispetto al totale della riga madre."
        ),
    )


# --- coorti ----------------------------------------------------------------


class CohortQuality(Quality):
    """Qualificazione di una riga di coorte, con i due flag di popolazione.

    ``is_survivors_only`` non e' ne' soppressione ne' significativita': dice
    CHI sono le persone contate. Una coorte anteriore all'ancora di
    osservabilita' e' fatta per costruzione dai soli sopravvissuti, quindi
    ``n_effective`` significa "quanti erano ancora presenti", non "quanti sono
    entrati" (modello-metriche.md 5.5).
    """

    is_survivors_only: Optional[bool] = None
    excluded_rejoins: Optional[int] = None


class OnboardingQuality(CohortQuality):
    is_mature: Optional[bool] = None
    has_snapshot_coverage: Optional[bool] = None
    observation_days: Optional[int] = None


class OnboardingValues(BaseModel):
    event_count: Optional[int] = None
    censored_count: Optional[int] = None
    censored_by_leave: Optional[int] = None
    median_days_to_k: Optional[float] = None
    # Flag PUNTUALE: qualifica solo median_days_to_k. Sta qui e non in quality
    # perche' li' sembrerebbe un giudizio sull'intera riga.
    median_reached: Optional[bool] = None
    p25_days_to_k: Optional[float] = None
    p75_days_to_k: Optional[float] = None
    reached_by_14d: Optional[float] = None
    reached_by_28d: Optional[float] = None


class OnboardingRow(BaseModel):
    layer_scope: str
    k: int
    quality: OnboardingQuality
    values: OnboardingValues


class RetentionValues(BaseModel):
    retained_fraction: Optional[float] = None
    # Flag PUNTUALI: qualificano solo retained_fraction. In quality direbbero
    # "questa riga non e' calcolabile", mentre dicono se QUEL numero esiste.
    is_computable: Optional[bool] = None
    not_computable_reason: Optional[str] = None


class RetentionRow(BaseModel):
    horizon_days: int
    quality: CohortQuality
    values: RetentionValues


class CohortGroup(BaseModel):
    """Una coorte di ingresso, con onboarding e retention insieme.

    Le due tabelle condividono la popolazione per progetto
    (modello-metriche.md 5.5), e servirle da due endpoint inviterebbe a
    leggerle separate — che e' il modo di fallire contro cui la duplicazione di
    ``n_effective``/``excluded_rejoins``/``is_survivors_only`` esiste.

    ``cohort_start`` e' solo la chiave che le tiene insieme: ogni riga annidata
    porta il PROPRIO ``quality``, perche' onboarding e retention della stessa
    coorte possono essere soppresse per ragioni diverse.
    """

    snapshot_id: int
    as_of: datetime
    cohort_start: date
    onboarding: list[OnboardingRow] = Field(default_factory=list)
    retention: list[RetentionRow] = Field(default_factory=list)


# --- run e guild -----------------------------------------------------------


class RunRow(BaseModel):
    """Un'esecuzione del layer metriche. Diagnostica, non una metrica."""

    snapshot_id: int
    as_of: datetime
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Parametri usati. Due run con parametri diversi non sono confrontabili.",
    )
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Durate per metrica e contatori di lettura. Come details: "
            "diagnostica, non contratto — le chiavi cambiano col codice del job."
        ),
    )
    code_version: Optional[str] = None
    created_at: Optional[datetime] = None


class GuildRow(BaseModel):
    """L'osservabilita' di una community.

    ``first_seen_at`` e' l'ancora di osservabilita' (modello-metriche.md 5.5):
    la SPIEGAZIONE di ``is_survivors_only`` e di
    ``before_observability_anchor``. Si espone di proposito — servire i caveat
    senza la ragione dei caveat e' peggio che non servirli.

    ``left_at`` e ``rejoined_at`` entrambi valorizzati significano che l'ancora
    di questa guild NON e' piu' un istante solo: c'e' un buco di osservazione in
    mezzo, e le uscite avvenute li' dentro sono invisibili come quelle
    precedenti all'ancora (5.6).
    """

    guild_id: int
    first_seen_at: datetime
    backfilled_at: Optional[datetime] = None
    left_at: Optional[datetime] = None
    rejoined_at: Optional[datetime] = None
    latest_metrics_as_of: Optional[datetime] = Field(
        default=None,
        description="as_of dell'ultima run di metriche. None se non ne esistono ancora.",
    )


class Health(BaseModel):
    status: str
    database: str

