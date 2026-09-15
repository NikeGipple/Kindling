-- 0013_previous_gap_days_comment.sql
--
-- Corregge solo il commento di metric_communities.previous_gap_days scritto da
-- 0011, che diceva "NULL quando la stabilita' non e' calcolabile, come
-- stability_jaccard stesso". E' falso, e lo smentiscono i dati di produzione:
-- allo snapshot 12 (15/09/2026, dopo il rerun delle metriche) mention,
-- reaction e reply hanno previous_gap_days = 6.823 e stability_jaccard NULL,
-- perche' node_overlap e' sotto la soglia minima. Le due colonne dipendono da
-- condizioni diverse: il gap solo dall'esistenza di uno snapshot precedente
-- (come previous_snapshot_id), la stabilita' anche dalla sovrapposizione dei
-- nodi.
--
-- 0011 non si modifica: e' gia' applicata sulla droplet, e riscriverne il file
-- non cambierebbe il commento nel database. Stesso testo, nel significato, della
-- descrizione del campo in api/models.py (CommunityValues.previous_gap_days),
-- corretta lo stesso giorno.
--
-- Nessuno schema cambia: COMMENT ON e' idempotente per costruzione, riapplicare
-- questo file riscrive lo stesso testo.

COMMENT ON COLUMN metric_communities.previous_gap_days IS
    'Giorni tra as_of e l''as_of dello snapshot precedente (previous_snapshot_id), con cui si confronta la partizione. Nessuna soglia: un valore diverso da quello atteso (7 con cadenza settimanale) non e'' un errore, e'' l''informazione che stability_jaccard e node_overlap confrontano finestre a una distanza diversa. NULL quando non esiste uno snapshot precedente, per lo stesso motivo per cui previous_snapshot_id e'' NULL. Puo'' essere valorizzato anche quando stability_jaccard e'' NULL per un motivo diverso, per esempio node_overlap sotto la soglia minima.';

INSERT INTO schema_migrations (filename) VALUES ('0013_previous_gap_days_comment.sql')
    ON CONFLICT (filename) DO NOTHING;
