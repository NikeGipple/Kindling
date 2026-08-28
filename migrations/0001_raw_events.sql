-- 0001_raw_events.sql
--
-- Log grezzo, immutabile e append-only di tutti gli eventi catturati dagli
-- ingestor (bot Discord, e in futuro altre fonti). E' la fonte di verita':
-- qualunque metrica si ricalcola da qui, senza mai dover re-ingerire nulla.
--
-- Vedi architettura/stack-tecnologico-mvp.md.

CREATE TABLE IF NOT EXISTS raw_events (
    id                      BIGSERIAL PRIMARY KEY,

    -- Chiave di partizione logica fin dal giorno 1: ogni query filtra per
    -- guild_id, anche con una sola community attiva, per rendere il
    -- multi-community un dettaglio di query e non una riscrittura.
    guild_id                BIGINT NOT NULL,

    -- Tipo canonico dell'evento (vedi bot/event_types.py). Testo libero, non
    -- un enum: aggiungere un nuovo tipo di evento o una nuova fonte dati non
    -- deve richiedere una migrazione dello schema.
    event_type              TEXT NOT NULL,

    -- Attore che ha generato l'evento (autore del messaggio, chi reagisce,
    -- chi entra in voice, ecc.). Nullable perche' non tutti gli eventi hanno
    -- un attore chiaro.
    author_id               BIGINT,

    channel_id              BIGINT,

    -- Id dell'entita' Discord a cui si riferisce l'evento (messaggio, thread,
    -- scheduled event...), a seconda di event_type.
    message_id              BIGINT,

    -- Per le reply: id del messaggio a cui si risponde.
    referenced_message_id   BIGINT,

    -- Chiave opzionale di idempotenza costruita dall'ingestor (es. per le
    -- reazioni: "reaction_add:<message_id>:<user_id>:<emoji>"), per evitare
    -- duplicati in caso di reconnect/backfill. NULL quando non applicabile.
    dedup_key               TEXT,

    -- Corpo grezzo dell'evento cosi' come arrivato dalla fonte, mai
    -- modificato dopo l'inserimento.
    payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Quando l'evento e' avvenuto secondo la fonte (es. message.created_at).
    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Quando la riga e' stata scritta in raw_events.
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Diritto all'oblio (Discord Developer ToS): quando valorizzato, un job
    -- di cancellazione deve aver gia' rimosso/anonimizzato i dati personali
    -- di author_id da questa riga. NULL = nessuna richiesta di cancellazione.
    forgotten_at            TIMESTAMPTZ
);

-- Idempotenza: un dedup_key ripetuto viene scartato silenziosamente
-- dall'ingestor (ON CONFLICT ... DO NOTHING), vedi bot/db.py.
CREATE UNIQUE INDEX IF NOT EXISTS raw_events_dedup_key_key
    ON raw_events (dedup_key)
    WHERE dedup_key IS NOT NULL;

-- Query principale attesa: metriche/finestra temporale per una community.
CREATE INDEX IF NOT EXISTS idx_raw_events_guild_occurred_at
    ON raw_events (guild_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_raw_events_event_type
    ON raw_events (event_type);

CREATE INDEX IF NOT EXISTS idx_raw_events_author_id
    ON raw_events (author_id)
    WHERE author_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_events_channel_id
    ON raw_events (channel_id)
    WHERE channel_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_events_message_id
    ON raw_events (message_id)
    WHERE message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_events_referenced_message_id
    ON raw_events (referenced_message_id)
    WHERE referenced_message_id IS NOT NULL;

-- Query ad-hoc sul payload senza dover anticipare ogni colonna futura.
CREATE INDEX IF NOT EXISTS idx_raw_events_payload_gin
    ON raw_events USING GIN (payload);

-- Per il diritto all'oblio: trovare rapidamente le righe di un autore non
-- ancora anonimizzate.
CREATE INDEX IF NOT EXISTS idx_raw_events_author_pending_forget
    ON raw_events (author_id)
    WHERE author_id IS NOT NULL AND forgotten_at IS NULL;

COMMENT ON TABLE raw_events IS
    'Log grezzo append-only di tutti gli eventi ingested. Fonte di verita'': mai modificato se non per il diritto all''oblio (forgotten_at).';
