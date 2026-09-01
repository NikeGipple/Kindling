-- 0005_snapshot_stats.sql
--
-- Diagnostica dell'esecuzione del job, salvata insieme allo snapshot che ha
-- prodotto. Additiva e idempotente come le migration precedenti: si applica
-- su un database gia' popolato senza toccare le righe esistenti.
--
-- Perche' in tabella e non solo a log: i contatori della ricostruzione
-- (intervalli ancora aperti, join duplicati, scarti oltre il tetto) e quelli
-- di copertura dei layer direzionali vanno letti "di snapshot in snapshot" —
-- e' la loro *variazione* a dire se il problema e' nell'ingestion o nel
-- calcolo. Finche' vivevano in una riga di log quella serie storica non
-- esisteva e il confronto era impossibile.
--
-- JSONB e non colonne dedicate per la stessa ragione di params: l'insieme dei
-- contatori cambiera' insieme al codice del job, e aggiungerne uno non deve
-- richiedere una migrazione. Non e' un dato di dominio, e' la scatola nera
-- dell'esecuzione.

ALTER TABLE graph_snapshots
    ADD COLUMN IF NOT EXISTS stats JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN graph_snapshots.stats IS
    'Contatori diagnostici dell''esecuzione che ha prodotto lo snapshot: ricostruzione degli intervalli e copertura della risoluzione dei layer direzionali. Vanno confrontati tra snapshot successivi, non letti in assoluto.';
