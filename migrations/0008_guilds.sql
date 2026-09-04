-- 0008_guilds.sql
--
-- Tabella dimensionale delle guild (server Discord). architettura.md prevede
-- gia' le tabelle dimensionali per membri, canali e guild: questa e' quella
-- mancante, non una struttura nuova.
--
-- Additiva e idempotente come le precedenti.
--
-- Due usi, e il secondo e' il piu' importante:
--
-- 1. Il backfill di `members` legge `backfilled_at` per sapere se e' gia' stato
--    fatto su questa guild. Il backfill non e' piu' un comando (`!backfill_
--    members` non esiste piu'): e' una funzione interna, eseguita
--    automaticamente quando il bot entra in una guild e all'avvio per le guild
--    in cui si trova senza esserne stato testimone. `on_guild_join` da solo non
--    basta — il flusso OAuth che aggiunge l'applicazione funziona anche a bot
--    spento, e Discord non ritrasmette gli eventi del gateway.
--
-- 2. `first_seen_at` e' l'ANCORA DI OSSERVABILITA' usata dal job
--    (modello-metriche.md 5.5): l'istante da cui le uscite dei membri sono
--    osservabili. Prima veniva dedotta come MIN(occurred_at) su raw_events;
--    qui e' un dato dichiarato da chi lo sa, invece di un minimo estratto da
--    una tabella che potrebbe contenere altro.
--
--    Serve perche' `members` non e' un log: contiene chi era presente quando il
--    backfill e' stato fatto, quindi chi era entrato e gia' uscito prima non ha
--    lasciato traccia. Ogni coorte anteriore a `first_seen_at` e' composta per
--    costruzione dai soli sopravvissuti, e la sua retention varrebbe 1.00 per
--    tautologia.

CREATE TABLE IF NOT EXISTS guilds (
    guild_id      BIGINT PRIMARY KEY,

    -- Da quando Kindling osserva questa community. NON viene mai riscritto
    -- dopo il primo inserimento: e' l'ancora, e spostarla in avanti
    -- cancellerebbe osservazioni valide, spostarla indietro inventerebbe
    -- osservazioni mai fatte.
    first_seen_at TIMESTAMPTZ NOT NULL,

    -- NULL = backfill di `members` mai eseguito su questa guild. E' un
    -- marcatore di "gia' fatto", non una misura: il valore esatto documenta
    -- quando, ma nessun calcolo lo legge come numero.
    backfilled_at TIMESTAMPTZ,

    -- Il bot e' stato rimosso dalla guild. Registrato per non perdere il fatto:
    -- una rimozione seguita da un riaggiunta lascia un buco di osservazione che
    -- una singola `first_seen_at` non sa esprimere (limite dichiarato in
    -- modello-metriche.md 5.6). Questa colonna e' cio' che permettera', se
    -- servira', di trasformare l'ancora in una lista di intervalli di
    -- osservazione senza dover indovinare niente a posteriori.
    left_at       TIMESTAMPTZ
);

-- Query attesa dal job: l'ancora di una community, per guild_id — coperta dalla
-- chiave primaria. Nessun indice aggiuntivo.

COMMENT ON TABLE guilds IS
    'Anagrafica dei server Discord osservati. first_seen_at e'' l''ancora di osservabilita'' usata dalle metriche di coorte; backfilled_at marca che il backfill interno di members e'' gia'' stato eseguito su questa guild.';

COMMENT ON COLUMN guilds.first_seen_at IS
    'Da quando le uscite dei membri sono osservabili. Mai riscritto dopo il primo inserimento.';

COMMENT ON COLUMN guilds.backfilled_at IS
    'NULL = backfill di members mai eseguito su questa guild. Marcatore, non misura.';
