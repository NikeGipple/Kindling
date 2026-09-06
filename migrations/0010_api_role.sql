-- 0010_api_role.sql
--
-- Ruolo Postgres di sola lettura per l'API. Vedi docs/architettura/api.md 1 e 4.
--
-- L'API non legge nessuna tabella che contenga dati riferibili a una persona.
-- Oggi quello e' un criterio che chi rilegge il codice deve applicare; con un
-- ruolo dedicato diventa una garanzia del database: un endpoint scritto per
-- errore contro graph_edges fallisce con un errore di permessi invece di
-- funzionare. E' lo stesso ragionamento del trigger di soppressione (0006) —
-- la garanzia non sta nel codice che la deve rispettare.
--
-- Tabelle leggibili, sette: le sei metric_* piu' guilds. guilds contiene
-- guild_id, first_seen_at, backfilled_at, left_at, rejoined_at, cioe' fatti sul
-- server Discord e sul deployment del bot, nessuno riferibile a una persona; e
-- first_seen_at e' l'ancora di osservabilita', cioe' la SPIEGAZIONE di
-- is_survivors_only. Servire i caveat senza la ragione dei caveat e' peggio che
-- non servirli.
--
-- Fuori restano raw_events, members, graph_edges, voice_sessions,
-- voice_session_participants, message_authors. members e' il caso non ovvio:
-- sembra una tabella di date, ma la chiave e' (guild_id, author_id), quindi
-- ogni riga dice quando UNA PERSONA e' entrata e quando se n'e' andata.
--
-- Additiva e idempotente, come le precedenti: rilanciabile senza effetti.

-- ---------------------------------------------------------------------------
-- Il ruolo
-- ---------------------------------------------------------------------------
--
-- CREATE ROLE IF NOT EXISTS non esiste in Postgres: serve un blocco DO che
-- controlli pg_roles prima di creare.
--
-- NESSUNA PASSWORD QUI DENTRO. Una migration sta in git, una password no. Il
-- ruolo si crea con LOGIN e la password si imposta a parte sulla droplet,
-- leggendola dal .env:
--
--     ALTER ROLE kindling_api PASSWORD '...';
--
-- Vedi il passo dedicato in runbook-droplet.md.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kindling_api') THEN
        CREATE ROLE kindling_api LOGIN;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Privilegi
-- ---------------------------------------------------------------------------
--
-- Si parte togliendo tutto: rieseguire la migration dopo aver ristretto
-- l'elenco delle tabelle leggibili deve REVOCARE davvero, non lasciare in
-- piedi un grant vecchio. Senza questa revoca la migration sarebbe idempotente
-- solo in aggiunta, che e' il tipo di idempotenza che non serve.

-- current_schema() e non "public" a lettera: tutte le migration di questo
-- progetto operano su qualunque schema punti il search_path, ed e' cosi' che i
-- test di schema le applicano dentro uno schema temporaneo usa e getta. Scrivere
-- "public" qui renderebbe questa migration l'unica che non si puo' provare
-- davvero.
DO $$
DECLARE
    target TEXT := current_schema();
BEGIN
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM kindling_api', target);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM kindling_api', target);
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM kindling_api', target);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO kindling_api', target);
END
$$;

-- I GRANT sono espliciti, tabella per tabella, e NON si usa
-- ALTER DEFAULT PRIVILEGES.
--
-- Conseguenza voluta: una tabella leggibile aggiunta in futuro non lo sara'
-- finche' non le si da' il GRANT, e il sintomo e' un errore di permessi chiaro.
-- L'alternativa (privilegi di default sullo schema) renderebbe leggibile in
-- automatico anche una tabella interna aggiunta domani — cioe' il contrario di
-- quello che serve. Meglio un GRANT dimenticato che fallisce rumorosamente che
-- una tabella con dati personali esposta in silenzio.

GRANT SELECT ON guilds                  TO kindling_api;
GRANT SELECT ON metric_runs             TO kindling_api;
GRANT SELECT ON metric_robustness       TO kindling_api;
GRANT SELECT ON metric_communities      TO kindling_api;
GRANT SELECT ON metric_community_sizes  TO kindling_api;
GRANT SELECT ON metric_cohorts          TO kindling_api;
GRANT SELECT ON metric_cohort_retention TO kindling_api;
