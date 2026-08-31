-- 0003_referenced_channel_id.sql
--
-- Aggiunge a raw_events il canale del messaggio a cui una reply risponde.
--
-- Fino a qui l'ingestion salvava solo referenced_message_id, e il codice a
-- valle assumeva implicitamente che il messaggio target vivesse nello stesso
-- canale della reply. E' vero nella stragrande maggioranza dei casi, ma
-- Discord permette reply cross-canale: in quei casi l'assunzione porta a
-- cercare l'id nel canale sbagliato. Serve al backfill lato bot che risolve
-- via API l'autore dei messaggi target (channel.fetch_message vuole il canale
-- giusto); il job di calcolo non ne ha bisogno, perche' risolve gli autori per
-- message_id, che in Discord e' globalmente univoco.
--
-- Vedi docs/architettura/modello-grafo.md 6 e CLAUDE.md.

-- ADD COLUMN IF NOT EXISTS invece di una CREATE TABLE riscritta: raw_events e'
-- il log immutabile gia' popolato in produzione, la migrazione non deve poter
-- toccare le righe esistenti.
ALTER TABLE raw_events
    ADD COLUMN IF NOT EXISTS referenced_channel_id BIGINT;

COMMENT ON COLUMN raw_events.referenced_channel_id IS
    'Canale del messaggio referenziato da una reply. NULL = sconosciuto, da trattare a valle come "stesso canale della reply" (comportamento storico: le reply ingerite prima di questa colonna ne sono prive e il dato non e'' sempre recuperabile).';

-- Nessun indice: la colonna non e' un criterio di ricerca, e' un dato di
-- corredo letto insieme alla riga che lo contiene.
