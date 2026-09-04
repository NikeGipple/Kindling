-- 0007_metrics_observability.sql
--
-- Due limiti scoperti alla prima esecuzione in produzione del layer metriche,
-- entrambi della categoria peggiore: numeri falsi che sembrano risultati.
-- Vedi docs/architettura/modello-metriche.md 5.5, 5.6 e 7.3.
--
-- 1. SURVIVORSHIP BIAS. members non e' un log: viene popolata da
--    !backfill_members e contiene chi era presente QUEL giorno. Chi e' entrato
--    a marzo e uscito ad aprile non ha un left_at — non e' mai stato scritto.
--    Ogni coorte anteriore all'inizio dell'osservazione e' quindi composta per
--    costruzione dai soli sopravvissuti, e la sua retention risultava 1.00 a 7,
--    14 e 28 giorni con is_computable = true. Non e' un risultato, e' una
--    tautologia: sbagliato il numeratore E il denominatore, perche' anche
--    n_effective significa "quanti erano ancora presenti", non "quanti sono
--    entrati".
--
-- 2. COPERTURA DI SNAPSHOT. is_mature contava giorni di calendario. Una coorte
--    di marzo con un solo snapshot ad agosto risultava matura e significativa
--    con reached_by_14d = 0.0 — che un admin legge come "a marzo nessuno
--    costruiva connessioni", mentre per marzo non esiste dato di grafo e
--    nessuno POTEVA raggiungere k. Quello zero misurava l'assenza di
--    osservazione.
--
-- Additiva e idempotente come le precedenti: ADD COLUMN IF NOT EXISTS su
-- tabelle gia' popolate, nessuna riga toccata. Le colonne nuove nascono NULL
-- sulle righe scritte da 0006, che e' il valore giusto — quelle righe sono
-- state calcolate senza conoscere ne' l'ancora ne' la copertura, e "non
-- valutato" e' esattamente cio' che NULL dice.
--
-- Il trigger di soppressione di 0006 non va toccato: legge la riga come JSONB e
-- ricava la chiave primaria dal catalogo, quindi copre le colonne nuove senza
-- nessuna modifica. E' la ragione per cui e' un trigger e non un CHECK che
-- enumera le colonne.

-- ---------------------------------------------------------------------------
-- metric_cohorts
-- ---------------------------------------------------------------------------

ALTER TABLE metric_cohorts
    ADD COLUMN IF NOT EXISTS is_survivors_only BOOLEAN;

COMMENT ON COLUMN metric_cohorts.is_survivors_only IS
    'La coorte precede l''istante da cui le uscite sono osservabili: e'' composta per costruzione dai soli sopravvissuti, quindi n_effective non e'' "quanti sono entrati" ma "quanti erano ancora presenti al backfill". NULL = non valutato.';

ALTER TABLE metric_cohorts
    ADD COLUMN IF NOT EXISTS has_snapshot_coverage BOOLEAN;

COMMENT ON COLUMN metric_cohorts.has_snapshot_coverage IS
    'Esiste almeno uno snapshot confrontabile che copre i primi min_observation_days della coorte. Senza, nessuno POTEVA raggiungere k e uno zero misura l''assenza di osservazione, non l''integrazione. NULL = non valutato.';

-- ---------------------------------------------------------------------------
-- metric_cohort_retention
-- ---------------------------------------------------------------------------

-- Ripetuta qui come n_effective ed excluded_rejoins, e per la stessa ragione:
-- le due tabelle si leggono affiancate, e una divergenza tra i loro
-- denominatori deve essere verificabile con una query invece di restare un
-- numero plausibile.
ALTER TABLE metric_cohort_retention
    ADD COLUMN IF NOT EXISTS is_survivors_only BOOLEAN;

-- La ragione in chiaro accanto al flag: 'before_observability_anchor',
-- 'horizon_not_reached', 'empty_cohort'. Colonna tipizzata e non JSONB perche'
-- e' un dato di dominio che si leggera' in un WHERE, non diagnostica di
-- esecuzione.
ALTER TABLE metric_cohort_retention
    ADD COLUMN IF NOT EXISTS not_computable_reason TEXT;

COMMENT ON COLUMN metric_cohort_retention.not_computable_reason IS
    'Perche'' retained_fraction e'' NULL: before_observability_anchor (coorte di soli sopravvissuti), horizon_not_reached (non tutti i membri hanno avuto horizon_days di osservazione), empty_cohort.';

-- Un motivo esiste esattamente quando il valore non e' calcolabile: senza
-- questo vincolo le due colonne possono divergere in silenzio, ed e'' il tipo
-- di incoerenza che si nota solo quando qualcuno legge il motivo sbagliato.
ALTER TABLE metric_cohort_retention
    DROP CONSTRAINT IF EXISTS metric_cohort_retention_reason_matches_computable;

ALTER TABLE metric_cohort_retention
    ADD CONSTRAINT metric_cohort_retention_reason_matches_computable
    CHECK (
        is_computable IS NULL
        OR (is_computable AND not_computable_reason IS NULL)
        OR (NOT is_computable AND not_computable_reason IS NOT NULL)
    );
