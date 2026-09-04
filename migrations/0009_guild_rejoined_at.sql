-- 0009_guild_rejoined_at.sql
--
-- Chiude il buco di osservazione dal lato della fine, nel caso a una sola
-- interruzione. Additiva e idempotente come le precedenti.
--
-- `guilds.left_at` (0008) registra quando il bot e' stato rimosso da un server,
-- cioe' l'inizio di un periodo in cui le uscite dei membri non sono osservabili.
-- Da solo pero' dice "c'e' stato un buco, e' cominciato allora" e nient'altro:
-- chi legge non puo' delimitarlo. Con `rejoined_at` il buco e'
-- [left_at, rejoined_at], che e' tutto cio' che serve nel caso realistico —
-- un'unica interruzione.
--
-- NON e' la lista di intervalli di osservazione rimandata a modello-metriche.md
-- 12, ed e' bene non confonderli: con piu' di un'interruzione la coppia
-- descrive solo la piu' recente, perche' `mark_guild_left` riazzera
-- `rejoined_at` a ogni nuova uscita. Le interruzioni precedenti non vengono
-- conservate. E' un limite dichiarato, non un difetto nascosto: due colonne non
-- possono descrivere N buchi, e fingere di si' sarebbe peggio che dire dove si
-- fermano.

ALTER TABLE guilds
    ADD COLUMN IF NOT EXISTS rejoined_at TIMESTAMPTZ;

COMMENT ON COLUMN guilds.rejoined_at IS
    'Quando il bot e'' tornato su questa guild dopo una rimozione. Con left_at delimita il buco di osservazione [left_at, rejoined_at]. Riazzerato a ogni nuova uscita: la coppia descrive sempre e solo l''interruzione piu'' recente.';

-- Coerenza della coppia: non si puo' essere tornati senza essere andati via.
-- Non impedisce il caso multi-interruzione (quello resta un limite dichiarato),
-- impedisce lo stato incoerente.
ALTER TABLE guilds
    DROP CONSTRAINT IF EXISTS guilds_rejoined_requires_left;

ALTER TABLE guilds
    ADD CONSTRAINT guilds_rejoined_requires_left
    CHECK (rejoined_at IS NULL OR left_at IS NOT NULL);
