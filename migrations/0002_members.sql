-- 0002_members.sql
--
-- Anagrafica dei membri di un server Discord: quando sono entrati, quando
-- (se) sono usciti, per calcolare la retention e confrontarla con eventi di
-- onboarding (es. numero di connessioni fatte nei primi giorni). Vedi
-- CLAUDE.md, sezione "Gap noto: tabella members", per il razionale completo.
--
-- A differenza di raw_events, qui esiste una riga "corrente" per ogni
-- (guild_id, author_id): non e' un log immutabile, e' uno stato che viene
-- aggiornato in place quando il membro rientra dopo essere uscito (scelta
-- esplicita per l'MVP: nessuno storico dei rientri multipli, vedi CLAUDE.md).
--
-- Chiave composita (guild_id, author_id), non solo author_id: raw_events e'
-- gia' progettata multi-guild-ready (ogni query filtra per guild_id in vista
-- di piu' community), e members deve poter tracciare lo stesso utente
-- Discord come membro di piu' server Kindling senza che l'iscrizione a un
-- server sovrascriva quella a un altro. guild_id per primo nella chiave,
-- coerente con raw_events, dove tutte le query filtrano per guild_id prima.

CREATE TABLE IF NOT EXISTS members (
    guild_id     BIGINT NOT NULL,
    author_id    BIGINT NOT NULL,

    -- Da evento member_join in tempo reale, oppure dal backfill una tantum
    -- (comando !backfill_members, bot/cogs/admin.py) per i membri gia'
    -- presenti quando il bot viene aggiunto a un server.
    joined_at    TIMESTAMPTZ NOT NULL,

    -- NULL = membro tuttora presente. Non e' un dettaglio opzionale: senza
    -- uscite tracciate non si puo' mai verificare se l'onboarding rapido
    -- correla davvero con la retention (vedi CLAUDE.md).
    left_at      TIMESTAMPTZ,

    -- Diritto all'oblio, stessa semantica di raw_events.forgotten_at.
    forgotten_at TIMESTAMPTZ,

    PRIMARY KEY (guild_id, author_id)
);

-- Query attesa: membri tuttora presenti o usciti di recente, per community.
CREATE INDEX IF NOT EXISTS idx_members_guild_left_at
    ON members (guild_id, left_at);

-- Per il diritto all'oblio: trovare rapidamente le righe di un autore non
-- ancora anonimizzate, come in raw_events.
CREATE INDEX IF NOT EXISTS idx_members_author_pending_forget
    ON members (author_id)
    WHERE forgotten_at IS NULL;

COMMENT ON TABLE members IS
    'Stato corrente di ogni membro per guild (joined_at/left_at, per la retention). A differenza di raw_events non e'' append-only: viene aggiornata in place.';
