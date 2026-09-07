-- 0011_previous_gap_days.sql
--
-- Additiva e idempotente come le precedenti.
--
-- Promuove previous_gap_days da chiave di metric_communities.details a colonna
-- tipizzata, accanto a previous_snapshot_id, node_overlap, stability_jaccard.
--
-- Il motivo non e' preferenza di stile: api/models.py dichiara esplicitamente
-- che details e' DIAGNOSTICA, NON CONTRATTO, e che nessun consumatore deve
-- dipendere dalle sue chiavi. Ma previous_gap_days e' l'unico dei quattro dati
-- che descrivono il confronto di stabilita' a dire se il confronto vale
-- qualcosa: senza, un consumatore ha stability_jaccard e node_overlap ma non
-- puo' sapere se sono stati calcolati tra snapshot consecutivi o tra due
-- lanciati a un giorno di distanza (finestre da 7 giorni sovrapposte
-- all'85-95%, modello-metriche.md 4.7). Lasciarlo in details significava che
-- l'unico dato che qualifica gli altri tre era anche l'unico che il contratto
-- invita a ignorare.
--
-- Nessun GRANT aggiuntivo: 0010 concede SELECT sull'intera tabella
-- (GRANT SELECT ON metric_communities TO kindling_api), non colonna per
-- colonna — in Postgres un GRANT senza elenco di colonne copre anche quelle
-- aggiunte dopo, senza bisogno di un nuovo GRANT. Verificato su Postgres
-- 16.13 e non solo dedotto dal meccanismo: dopo questa migration,
-- kindling_api legge previous_gap_days (letto 0.318 su una riga scritta dal
-- proprietario) senza nessun GRANT aggiuntivo, e resta cieco su graph_edges
-- come prima — il perimetro del ruolo non si e' allargato per un effetto
-- collaterale.

ALTER TABLE metric_communities
    ADD COLUMN IF NOT EXISTS previous_gap_days DOUBLE PRECISION;

COMMENT ON COLUMN metric_communities.previous_gap_days IS
    'Giorni tra as_of e l''as_of dello snapshot con cui e'' calcolata la stabilita''. Nessuna soglia: un valore diverso da quello atteso (7 con cadenza settimanale) non e'' un errore, e'' l''informazione che stability_jaccard sta confrontando finestre a una distanza diversa. NULL quando la stabilita'' non e'' calcolabile, come stability_jaccard stesso.';

-- Rientra sotto il vincolo generico di suppressione (metric_suppressed_row_is_empty,
-- 0006_metrics.sql): il trigger enumera le colonne della riga con to_jsonb(NEW)
-- e non una lista scritta a mano, quindi una riga soppressa che porta un
-- previous_gap_days non NULL viene gia' rifiutata senza toccare il trigger.
-- Verificato su Postgres 16.13 su questa colonna, non solo dedotto dal
-- meccanismo generico: una riga soppressa con previous_gap_days = 0.318 e'
-- stata rifiutata con
--   "riga soppressa di metric_communities con valori non NULL: previous_gap_days"
-- e la stessa riga con previous_gap_days = NULL e' stata accettata.
