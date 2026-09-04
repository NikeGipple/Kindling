-- 0006_metrics.sql
--
-- Tabelle delle metriche aggregate lette dall'API: robustezza strutturale,
-- struttura e stabilita' delle community (Leiden), onboarding e retention per
-- coorte. Vedi docs/architettura/modello-metriche.md per il modello completo.
--
-- Additiva e idempotente come le migration precedenti: si applica su un
-- database gia' popolato senza toccare le righe esistenti.
--
-- A differenza di graph_edges e voice_sessions, che sono INTERNE, queste sono
-- le uniche tabelle pensate per essere lette dall'API. E' la ragione per cui
-- ogni riga porta la propria soppressione: la soglia di cardinalita' N vive
-- qui, nel layer di calcolo, cosi' nessun layer di presentazione futuro puo'
-- bucarla per errore (metriche-aggregate-admin.md, principio operativo).
--
-- Nessuna di queste tabelle contiene un output per-nodo: centralita',
-- appartenenza alla community e conteggio connessioni del singolo membro sono
-- passaggi di calcolo interni e non arrivano mai fin qui
-- (modello-metriche.md 8).

-- ---------------------------------------------------------------------------
-- Il vincolo di soppressione, condiviso da tutte le tabelle di metrica
-- ---------------------------------------------------------------------------
--
-- Regola (modello-metriche.md 6.2): su una riga soppressa sopravvivono solo le
-- colonne di chiave primaria, is_suppressed, suppression_reason, e details che
-- deve valere '{}'. Tutto il resto e' NULL — non solo cio' che sembra "il
-- valore": nodes_removed e' ceil(X*n) con X in chiave e restituisce n;
-- giant_before e' una frazione con n al denominatore; event_count +
-- censored_count e' la dimensione della coorte. Lasciarne uno solo pubblica la
-- numerosita' che la soppressione doveva nascondere.
--
-- Perche' un trigger e non un CHECK dichiarativo: un CHECK di tabella puo' solo
-- ENUMERARE le colonne da annullare (num_nonnulls(a, b, c, ...) = 0), cioe'
-- esattamente l'elenco che la specifica rifiuta. Un elenco che ci si dimentica
-- di aggiornare il giorno in cui si aggiunge una colonna, e il cui fallimento
-- e' silenzioso: la colonna nuova continua a essere pubblicata su righe
-- soppresse senza che niente protesti.
--
-- Questo trigger invece legge la riga come JSONB e ricava le colonne di chiave
-- primaria dal catalogo di Postgres: aggiungere una colonna la mette sotto
-- vincolo senza toccare niente, e aggiungerne una alla chiave primaria pure.
-- E' schema e non codice del job: vale per qualunque client, compresa una
-- INSERT scritta a mano in psql.

CREATE OR REPLACE FUNCTION metric_suppressed_row_is_empty()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    row_json  JSONB;
    key_cols  TEXT[];
    leaked    TEXT[];
BEGIN
    IF NOT COALESCE(NEW.is_suppressed, FALSE) THEN
        RETURN NEW;
    END IF;

    row_json := to_jsonb(NEW);

    -- details e' l'unica eccezione al vincolo: resta NOT NULL DEFAULT '{}' —
    -- un JSONB vuoto non pubblica niente, e renderla nullable per l'unico caso
    -- in cui deve essere vuota peggiorerebbe la colonna per tutte le altre
    -- righe, che ci scrivono senza doverla inizializzare.
    IF row_json ? 'details' AND row_json -> 'details' <> '{}'::jsonb THEN
        RAISE EXCEPTION
            'riga soppressa di %: details deve essere ''{}'', trovato %',
            TG_TABLE_NAME, row_json -> 'details'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT array_agg(a.attname)
      INTO key_cols
      FROM pg_index i
      JOIN pg_attribute a
        ON a.attrelid = i.indrelid
       AND a.attnum = ANY (i.indkey)
     WHERE i.indrelid = TG_RELID
       AND i.indisprimary;

    IF key_cols IS NULL THEN
        -- Senza chiave primaria non si sa cosa deve sopravvivere: meglio
        -- rifiutare la scrittura che lasciar passare una riga non verificata.
        RAISE EXCEPTION
            'metric_suppressed_row_is_empty: % non ha una chiave primaria',
            TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT array_agg(entry.key ORDER BY entry.key)
      INTO leaked
      FROM jsonb_each(
               row_json
               - key_cols
               - 'is_suppressed'
               - 'suppression_reason'
               - 'details'
           ) AS entry
     WHERE jsonb_typeof(entry.value) <> 'null';

    IF leaked IS NOT NULL THEN
        RAISE EXCEPTION
            'riga soppressa di % con valori non NULL: %',
            TG_TABLE_NAME, array_to_string(leaked, ', ')
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION metric_suppressed_row_is_empty() IS
    'Vincolo di soppressione delle metriche aggregate: su una riga con is_suppressed sopravvivono solo chiave primaria, is_suppressed, suppression_reason e details = ''{}''. Trigger e non CHECK perche'' un CHECK dovrebbe enumerare le colonne, e un elenco del genere si dimentica di aggiornare.';

-- ---------------------------------------------------------------------------
-- metric_runs: una riga per esecuzione del layer metriche su uno snapshot
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS metric_runs (
    -- Chiave primaria e non un id surrogato: c'e' AL PIU' una run di metriche
    -- per snapshot, e ricalcolare sullo stesso snapshot deve riscrivere invece
    -- di affiancare una seconda run.
    snapshot_id  BIGINT PRIMARY KEY
                     REFERENCES graph_snapshots(id) ON DELETE CASCADE,

    -- Denormalizzati dallo snapshot: ogni query filtra per community prima di
    -- ogni altra cosa, e as_of e' cio' che rende interpretabili i pesi.
    guild_id     BIGINT NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,

    -- N, min_nodes_publish, min_edge_weight, griglia X, k, seed, R, soglie,
    -- estremi dei bucket, funzione obiettivo di Leiden. Salvati QUI e non in
    -- graph_snapshots.params: quello descrive come e' stato costruito il
    -- grafo, e riscriverlo per aggiungerci i parametri di un layer successivo
    -- significherebbe modificare uno snapshot gia' calcolato.
    params       JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Scatola nera dell'esecuzione, come graph_snapshots.stats (0005):
    -- durations_ms per metrica, snapshot riletti per le coorti, degradazioni
    -- dei baseline scattate. E' l'unico modo per vedere arrivare il problema
    -- di budget descritto in modello-metriche.md 11.1 invece di scoprirlo
    -- dall'OOM killer — un tempo di esecuzione ha senso solo come serie.
    stats        JSONB NOT NULL DEFAULT '{}'::jsonb,

    code_version TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metric_runs_guild_as_of
    ON metric_runs (guild_id, as_of DESC);

COMMENT ON TABLE metric_runs IS
    'Una riga per esecuzione del layer metriche su uno snapshot del grafo. I parametri usati vivono qui e non in graph_snapshots.params: due run con parametri diversi non sono confrontabili e devono poterlo dichiarare da sole.';

-- ---------------------------------------------------------------------------
-- metric_robustness: robustezza strutturale (catalogo 1)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS metric_robustness (
    snapshot_id BIGINT NOT NULL
                    REFERENCES metric_runs(snapshot_id) ON DELETE CASCADE,

    -- Una riga per layer: robustezza e Leiden si calcolano SEPARATAMENTE su
    -- ciascun layer, mai su un grafo fuso (modello-grafo.md 1).
    layer       TEXT NOT NULL,

    -- 0.05 | 0.10 | 0.20 — non un X solo. Il catalogo dichiara X da validare
    -- empiricamente: salvarne tre rende il parametro ritarabile guardando i
    -- dati invece che ricalcolandoli.
    removal_fraction DOUBLE PRECISION NOT NULL,

    PRIMARY KEY (snapshot_id, layer, removal_fraction),

    -- Nodi del grafo del layer. NULL su riga soppressa: e' la numerosita' che
    -- la soppressione nasconde.
    n_effective                  INTEGER,
    nodes_removed                INTEGER,

    -- Frazioni SEMPRE rapportate al numero di nodi originale, non ai residui:
    -- rapportarle ai residui nasconderebbe meta' dell'effetto.
    giant_before                 DOUBLE PRECISION,
    giant_after_targeted         DOUBLE PRECISION,
    components_after_targeted    INTEGER,

    -- Il termine di paragone: la stessa rimozione fatta a caso. Senza, il
    -- numero non dice niente — qualunque grafo si frammenta se si toglie
    -- abbastanza. E' la differenza tra "fragile in assoluto" e "fragile
    -- rispetto a un grafo delle sue dimensioni".
    giant_after_random_mean      DOUBLE PRECISION,
    giant_after_random_sd        DOUBLE PRECISION,
    components_after_random_mean DOUBLE PRECISION,

    -- (giant_random_mean - giant_targeted) / giant_before. Puo' essere
    -- NEGATIVO, e non va clampato: significa che i nodi piu' centrali erano
    -- meno critici di nodi presi a caso, che e' un risultato legittimo su una
    -- rete con connettivita' distribuita.
    targeted_excess              DOUBLE PRECISION,
    -- NULL quando la deviazione standard del baseline e' zero.
    targeted_z                   DOUBLE PRECISION,

    is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,

    -- NULL = non valutata (riga soppressa). false = valutata e sotto i minimi.
    -- Sono due stati diversi: collassarli su false rimetterebbe in tabella la
    -- stessa confusione che la soppressione evita per lo zero, cioe' un
    -- giudizio negativo al posto di un'assenza.
    is_significant     BOOLEAN,

    details            JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT metric_robustness_reason_only_if_suppressed
        CHECK (is_suppressed OR suppression_reason IS NULL)
);

DROP TRIGGER IF EXISTS metric_robustness_suppression ON metric_robustness;
CREATE TRIGGER metric_robustness_suppression
    BEFORE INSERT OR UPDATE ON metric_robustness
    FOR EACH ROW EXECUTE FUNCTION metric_suppressed_row_is_empty();

COMMENT ON TABLE metric_robustness IS
    'Robustezza strutturale per snapshot, layer e frazione di nodi rimossa, con il baseline della rimozione casuale. targeted_excess puo'' essere negativo e non va clampato.';

-- ---------------------------------------------------------------------------
-- metric_communities: struttura e stabilita' delle community (catalogo 3)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS metric_communities (
    snapshot_id BIGINT NOT NULL
                    REFERENCES metric_runs(snapshot_id) ON DELETE CASCADE,
    layer       TEXT NOT NULL,

    PRIMARY KEY (snapshot_id, layer),

    n_effective            INTEGER,
    community_count        INTEGER,
    modularity             DOUBLE PRECISION,

    -- La modularita' di un grafo casuale NON e' zero: e' positiva e cresce al
    -- diminuire della dimensione. Senza questo baseline (rewiring che preserva
    -- la sequenza dei gradi) una modularita' di 0.4 su venti nodi e'
    -- indistinguibile da una partizione di rumore.
    modularity_random_mean DOUBLE PRECISION,
    modularity_random_sd   DOUBLE PRECISION,
    modularity_z           DOUBLE PRECISION,

    -- Stabilita' rispetto allo snapshot precedente. NULL quando non e'
    -- definita: nessuno snapshot precedente, parametri del grafo diversi
    -- (la differenza tra le partizioni conterrebbe il cambio di parametri), o
    -- troppi pochi nodi in comune.
    previous_snapshot_id   BIGINT REFERENCES graph_snapshots(id) ON DELETE SET NULL,
    node_overlap           DOUBLE PRECISION,
    stability_jaccard      DOUBLE PRECISION,

    -- Colonne e non un JSONB: una community che si dissolve e una che si fonde
    -- sono segnali opposti sulla domanda guida, e vanno letti come serie senza
    -- aprire un JSON.
    communities_born       INTEGER,
    communities_dissolved  INTEGER,
    communities_merged     INTEGER,
    communities_split      INTEGER,

    is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,
    is_significant     BOOLEAN,
    details            JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT metric_communities_reason_only_if_suppressed
        CHECK (is_suppressed OR suppression_reason IS NULL)
);

DROP TRIGGER IF EXISTS metric_communities_suppression ON metric_communities;
CREATE TRIGGER metric_communities_suppression
    BEFORE INSERT OR UPDATE ON metric_communities
    FOR EACH ROW EXECUTE FUNCTION metric_suppressed_row_is_empty();

COMMENT ON TABLE metric_communities IS
    'Partizione Leiden per snapshot e layer: numero di community, modularita'' con il suo baseline, e stabilita'' rispetto allo snapshot precedente. Nessuna appartenenza per-nodo e'' salvata: la partizione precedente si RICOSTRUISCE rieseguendo Leiden, ed e'' per questo che il seed e'' obbligatorio.';

-- ---------------------------------------------------------------------------
-- metric_community_sizes: distribuzione delle dimensioni, gia' soppressa
-- ---------------------------------------------------------------------------
--
-- Tabella figlia e non un JSONB dentro metric_communities: la distribuzione
-- delle dimensioni e' proprio la parte con i numeri piccoli, quella su cui la
-- soppressione (primaria e secondaria) deve essere imposta dal database.
--
-- Gli estremi dei bucket dipendono da N e sono salvati in metric_runs.params:
-- l'etichetta '5-9' non e' un nome, e' [N,10) con N=5. Un cambio di N
-- interrompe la comparabilita' di questa tabella, e le righe vecchie vanno
-- lette con gli estremi della loro run, non rimappate.

CREATE TABLE IF NOT EXISTS metric_community_sizes (
    snapshot_id BIGINT NOT NULL
                    REFERENCES metric_runs(snapshot_id) ON DELETE CASCADE,
    layer       TEXT NOT NULL,

    -- 'small' (< N, tutte accorpate) | '5-9' | '10-19' | '20-49' | '50-99' | '100+'
    bucket      TEXT NOT NULL,

    PRIMARY KEY (snapshot_id, layer, bucket),

    community_count    INTEGER,
    -- E' l'n_effective di questa riga: i membri complessivi del bucket.
    member_count       INTEGER,

    is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE,
    -- 'below_threshold' | 'secondary'. La soppressione secondaria esiste
    -- perche' una sola cella soppressa accanto a un totale pubblicato si
    -- ricava per differenza, e non avrebbe protetto niente.
    suppression_reason TEXT,

    CONSTRAINT metric_community_sizes_reason_only_if_suppressed
        CHECK (is_suppressed OR suppression_reason IS NULL)
);

DROP TRIGGER IF EXISTS metric_community_sizes_suppression ON metric_community_sizes;
CREATE TRIGGER metric_community_sizes_suppression
    BEFORE INSERT OR UPDATE ON metric_community_sizes
    FOR EACH ROW EXECUTE FUNCTION metric_suppressed_row_is_empty();

-- ---------------------------------------------------------------------------
-- metric_cohorts: onboarding per coorte di ingresso (catalogo 5)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS metric_cohorts (
    snapshot_id  BIGINT NOT NULL
                     REFERENCES metric_runs(snapshot_id) ON DELETE CASCADE,

    -- Lunedi' della settimana ISO di join.
    cohort_start DATE NOT NULL,

    -- 'any' (unione degli insiemi di partner su tutti i layer) | 'voice' | ...
    -- 'any' e' un'unione di insiemi di PERSONE, non una somma di pesi: non
    -- richiede un tasso di cambio tra un minuto di voce e una reply e non
    -- produce nessun peso combinato.
    layer_scope  TEXT NOT NULL,

    -- Connessioni distinte per considerare integrato un nuovo membro.
    k            INTEGER NOT NULL,

    PRIMARY KEY (snapshot_id, cohort_start, layer_scope, k),

    -- Membri della coorte al netto dei rientri sospetti esclusi.
    n_effective        INTEGER,
    observation_days   INTEGER,

    -- NULL = non valutata (riga soppressa). false = coorte troppo giovane
    -- perche' i suoi numeri vogliano dire qualcosa: e' scritta comunque,
    -- perche' sopprimerla nasconderebbe che esiste, e riportarla senza dire
    -- che e' giovane e' il modo in cui si legge un numero incompleto come se
    -- fosse un risultato.
    is_mature          BOOLEAN,

    event_count        INTEGER,
    censored_count     INTEGER,
    -- Censure dovute a un'uscita dal server. Sono propriamente un RISCHIO
    -- COMPETITIVO trattato come censura in v0: Kaplan-Meier sovrastima quindi
    -- la probabilita' di integrazione. Il contatore e' qui apposta, per
    -- rendere visibile la distorsione invece di lasciarla implicita.
    censored_by_leave  INTEGER,

    -- NULL quando la curva non scende sotto 0.5 nell'osservazione disponibile:
    -- la mediana NON e' raggiunta, e non va estrapolata.
    median_days_to_k   DOUBLE PRECISION,
    median_reached     BOOLEAN,
    p25_days_to_k      DOUBLE PRECISION,
    p75_days_to_k      DOUBLE PRECISION,

    -- 1 - S(t) a orizzonte fisso: piu' leggibili di una mediana spesso non
    -- raggiunta, e per un admin la forma azionabile della metrica.
    reached_by_14d     DOUBLE PRECISION,
    reached_by_28d     DOUBLE PRECISION,

    excluded_rejoins   INTEGER,

    is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,
    is_significant     BOOLEAN,
    details            JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT metric_cohorts_reason_only_if_suppressed
        CHECK (is_suppressed OR suppression_reason IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_metric_cohorts_cohort_start
    ON metric_cohorts (cohort_start);

DROP TRIGGER IF EXISTS metric_cohorts_suppression ON metric_cohorts;
CREATE TRIGGER metric_cohorts_suppression
    BEFORE INSERT OR UPDATE ON metric_cohorts
    FOR EACH ROW EXECUTE FUNCTION metric_suppressed_row_is_empty();

COMMENT ON TABLE metric_cohorts IS
    'Tempo al raggiungimento di k connessioni distinte per coorte di ingresso, stimato con Kaplan-Meier. La censura a destra e'' il punto: contare come "non integrata" una coorte entrata la settimana scorsa e'' un errore, non un dato.';

-- ---------------------------------------------------------------------------
-- metric_cohort_retention: retention della stessa coorte
-- ---------------------------------------------------------------------------
--
-- Tabella separata perche' la retention non dipende da layer_scope ne' da k:
-- e' solo membership. Metterla nella stessa riga la duplicherebbe per ogni
-- combinazione, ed e' il modo in cui due numeri identici cominciano a
-- divergere.

CREATE TABLE IF NOT EXISTS metric_cohort_retention (
    snapshot_id  BIGINT NOT NULL
                     REFERENCES metric_runs(snapshot_id) ON DELETE CASCADE,
    cohort_start DATE NOT NULL,
    horizon_days INTEGER NOT NULL,

    PRIMARY KEY (snapshot_id, cohort_start, horizon_days),

    -- STESSA popolazione di metric_cohorts, rientri sospetti esclusi.
    -- n_effective ed excluded_rejoins sono duplicati qui apposta, contro la
    -- regola generale di non ripetere un dato: sono l'unico modo per
    -- accorgersi che i due denominatori hanno smesso di coincidere, e il costo
    -- di una divergenza silenziosa e' un'intera conclusione sbagliata
    -- sull'incrocio integrazione/retention, che e' la ragione per cui la
    -- metrica esiste.
    n_effective        INTEGER,
    excluded_rejoins   INTEGER,

    retained_fraction  DOUBLE PRECISION,

    -- Calcolabile solo se TUTTI i membri della coorte hanno avuto
    -- horizon_days di osservazione. Senza questa regola una coorte entrata
    -- ieri risulterebbe con "100% di retention a 28 giorni", che e' il modo
    -- piu' diretto di trasformare l'assenza di dati in un ottimo risultato.
    is_computable      BOOLEAN,

    is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,

    CONSTRAINT metric_cohort_retention_reason_only_if_suppressed
        CHECK (is_suppressed OR suppression_reason IS NULL)
);

DROP TRIGGER IF EXISTS metric_cohort_retention_suppression ON metric_cohort_retention;
CREATE TRIGGER metric_cohort_retention_suppression
    BEFORE INSERT OR UPDATE ON metric_cohort_retention
    FOR EACH ROW EXECUTE FUNCTION metric_suppressed_row_is_empty();
