-- 0014_graph_snapshot_params.sql
--
-- Una vista stretta su graph_snapshots, e il SELECT su di essa (non sulla
-- tabella) per kindling_api. Serve alla vista Stato della dashboard, che deve
-- poter mostrare tre parametri del GRAFO accanto ai sei delle metriche:
-- l'emivita e il cutoff del decadimento ("memoria delle interazioni") e la
-- sovrapposizione minima in vocale (docs/architettura/dashboard.md 4, "Le
-- regole del calcolo").
--
-- Perche' una vista e non un GRANT su graph_snapshots. La tabella non contiene
-- dati riferibili a una persona — guild_id, as_of, la finestra, i parametri,
-- code_version — quindi il criterio di api.md 1 da solo non la terrebbe fuori.
-- Il motivo e' un altro, ed e' lo stesso per cui 0010 scrive i GRANT tabella
-- per tabella invece di usare ALTER DEFAULT PRIVILEGES: cio' che e' leggibile
-- deve essere un elenco DECISO, non un elenco che cresce da solo. Con il GRANT
-- sulla tabella, una colonna aggiunta domani a graph_snapshots diventerebbe
-- leggibile dall'API senza che nessuno l'abbia deciso e senza nessun segnale.
-- Con la vista, aggiungere un parametro e' una migration — cioe' una decisione
-- che si vede.
--
-- Tre colonne e non "params". Il blob intero sarebbe un SECONDO blob di
-- parametri accanto a metric_runs.params, indistinguibile da quello nella
-- risposta dell'API e con lo stesso statuto di diagnostica che api.md 3 nega
-- alle cose su cui si costruisce. Qui invece i tre valori diventano campi
-- tipizzati con un nome proprio (api/models.py, GraphParamsRow).
--
-- Il CASE su jsonb_typeof non e' pedanteria: params e' JSONB senza schema, e
-- una chiave scritta come stringa da un codice futuro farebbe fallire il cast
-- con un errore in fondo alla catena — dentro una query dell'API, su una
-- pagina della dashboard. Cosi' invece il valore esce NULL, e NULL a valle
-- significa gia' "questa regola non si mostra" (dashboard.md 4).
--
-- ORDINE: questa migration deve restare DOPO 0010, e non e' un dettaglio.
-- 0010 comincia con REVOKE ALL ON ALL TABLES IN SCHEMA, che in Postgres
-- comprende anche le viste: riapplicare 0010 da solo, dopo questa, toglierebbe
-- il permesso sulla vista e l'API risponderebbe 500 su /runs. Le migration si
-- applicano sempre tutte e in ordine (runbook-droplet.md), e
-- tests/test_api_role_schema.py le applica DUE volte di fila proprio per
-- provare che la sequenza regge.
--
-- Additiva e idempotente: CREATE OR REPLACE VIEW riscrive la definizione,
-- GRANT si puo' ripetere.

CREATE OR REPLACE VIEW graph_snapshot_params AS
SELECT
    s.id AS snapshot_id,
    CASE WHEN jsonb_typeof(s.params -> 'decay_half_life_days') = 'number'
         THEN (s.params ->> 'decay_half_life_days')::double precision END
        AS decay_half_life_days,
    CASE WHEN jsonb_typeof(s.params -> 'decay_cutoff_days') = 'number'
         THEN (s.params ->> 'decay_cutoff_days')::double precision END
        AS decay_cutoff_days,
    CASE WHEN jsonb_typeof(s.params -> 'min_overlap_minutes') = 'number'
         THEN (s.params ->> 'min_overlap_minutes')::double precision END
        AS min_overlap_minutes
FROM graph_snapshots s;

COMMENT ON VIEW graph_snapshot_params IS
    'I soli parametri di graph_snapshots.params che l''API espone, uno per colonna. Superficie DECISA: aggiungerne uno richiede una migration, come 0010 richiede un GRANT esplicito per ogni tabella leggibile. graph_snapshots resta non leggibile da kindling_api.';

-- Il GRANT e' sulla VISTA soltanto. graph_snapshots non compare in nessun
-- GRANT di nessuna migration, e tests/test_parametri_grafo.py fallisce se ci
-- finisce.
GRANT SELECT ON graph_snapshot_params TO kindling_api;

INSERT INTO schema_migrations (filename) VALUES ('0014_graph_snapshot_params.sql')
    ON CONFLICT (filename) DO NOTHING;
