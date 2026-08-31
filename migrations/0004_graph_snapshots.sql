-- 0004_graph_snapshots.sql
--
-- Tabelle del grafo sociale calcolato: snapshot, archi per layer, e la
-- ricostruzione delle sessioni vocali su cui poggia il layer di co-presenza.
-- Vedi docs/architettura/modello-grafo.md 7 per il modello completo.
--
-- Tutte queste tabelle sono DERIVATE: si ricostruiscono in qualunque momento
-- da raw_events, che resta la sola fonte di verita'. Perderle e' un fastidio
-- (un ricalcolo), non una perdita di dati.
--
-- Sono anche tutte INTERNE: graph_edges e voice_sessions non vengono mai
-- esposte da un endpoint API. L'API legge solo tabelle di aggregati sopra la
-- soglia di cardinalita' N (vedi metriche-aggregate-admin.md) — un arco e' una
-- relazione tra due persone nominate, esattamente cio' che il principio di
-- "nessun profilo individuale esposto agli amministratori" esclude.

-- ---------------------------------------------------------------------------
-- graph_snapshots: una riga per esecuzione del job su una guild
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS graph_snapshots (
    id            BIGSERIAL PRIMARY KEY,

    guild_id      BIGINT NOT NULL,

    -- Istante rispetto al quale i pesi sono stati decaduti. Un peso non e' un
    -- valore assoluto ma un valore *rispetto a un istante*: senza as_of gli
    -- archi di questo snapshot non sono interpretabili ne' confrontabili con
    -- quelli di un altro (modello-grafo.md 5).
    as_of         TIMESTAMPTZ NOT NULL,

    -- Finestra di osservazione degli eventi, semiaperta [start, end).
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,

    -- Emivita, cutoff, soglie e finestra di sessione effettivamente usate.
    -- Salvati DENTRO lo snapshot perche' due snapshot calcolati con parametri
    -- diversi non sono confrontabili e devono poterlo dichiarare da soli: i
    -- default di job/config.py cambieranno appena ci saranno mesi di dati su
    -- cui ritararli, e gli snapshot vecchi devono restare leggibili.
    params        JSONB NOT NULL DEFAULT '{}'::jsonb,

    code_version  TEXT,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotenza del job: rieseguirlo sullo stesso as_of e sulla stessa finestra
-- deve riscrivere lo snapshot esistente, non affiancargliene un duplicato.
CREATE UNIQUE INDEX IF NOT EXISTS graph_snapshots_identity
    ON graph_snapshots (guild_id, as_of, window_start, window_end);

-- Query attesa: "l'ultimo snapshot di questa community", e la serie storica
-- per confrontare snapshot successivi.
CREATE INDEX IF NOT EXISTS idx_graph_snapshots_guild_as_of
    ON graph_snapshots (guild_id, as_of DESC);

COMMENT ON TABLE graph_snapshots IS
    'Una riga per esecuzione del job di calcolo del grafo. Derivata e ricostruibile da raw_events; i parametri usati sono salvati nella riga perche'' snapshot con parametri diversi non sono confrontabili.';

-- ---------------------------------------------------------------------------
-- graph_edges: gli archi, un layer per volta
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS graph_edges (
    snapshot_id         BIGINT NOT NULL
                            REFERENCES graph_snapshots(id) ON DELETE CASCADE,

    -- 'voice' | 'reply' | 'mention' | 'reaction'. I layer vivono in righe
    -- separate della stessa tabella e non vengono MAI sommati tra loro: sono
    -- relazioni di tipo diverso, e la letteratura SNA sconsiglia esplicitamente
    -- di collassarle in un'unica sociomatrice (modello-grafo.md 1 e 2).
    -- Testo libero come event_type in raw_events, per lo stesso motivo:
    -- aggiungere un layer non deve richiedere una migrazione.
    layer               TEXT NOT NULL,

    src_author_id       BIGINT NOT NULL,
    dst_author_id       BIGINT NOT NULL,

    -- Peso normalizzato per dimensione della sessione E decaduto rispetto a
    -- as_of: e' il numero da usare per le metriche.
    weight              DOUBLE PRECISION NOT NULL,

    -- Stesso peso senza decadimento. Serve a isolare l'effetto del
    -- decadimento e a ritarare l'emivita H senza rifare la ricostruzione
    -- delle sessioni, che e' la parte costosa e delicata del calcolo.
    weight_undecayed    DOUBLE PRECISION NOT NULL,

    -- Unita' grezze, non normalizzate e non decadute: minuti di
    -- sovrapposizione per 'voice', conteggio di interazioni per gli altri
    -- layer. Permette di riapplicare in futuro una normalizzazione diversa
    -- (es. l'odds ratio di Wasserman & Faust) senza ricalcolare da capo.
    raw_units           DOUBLE PRECISION NOT NULL,

    -- Frequenza, non intensita': sessioni condivise per 'voice', numero di
    -- interazioni per gli altri. Distinguere "molto tempo insieme una volta"
    -- da "poco tempo insieme molte volte" richiede entrambe le grandezze.
    interaction_count   INTEGER NOT NULL,

    last_interaction_at TIMESTAMPTZ NOT NULL,

    -- Almeno un contributo di questo arco viene da un intervallo ricostruito
    -- (leave perso durante un downtime del bot) invece che osservato. Esiste
    -- per poter rifare le analisi escludendo gli archi ricostruiti e vedere
    -- se una conclusione dipende da dati inventati dalla riconciliazione.
    is_reconciled       BOOLEAN NOT NULL DEFAULT FALSE,

    -- Per i layer non diretti la coppia e' memorizzata una volta sola, con
    -- src < dst: senza questo vincolo lo stesso legame potrebbe entrare due
    -- volte, in due orientamenti, e contare doppio a valle.
    PRIMARY KEY (snapshot_id, layer, src_author_id, dst_author_id),

    CONSTRAINT graph_edges_no_self_loop
        CHECK (src_author_id <> dst_author_id),

    CONSTRAINT graph_edges_undirected_canonical
        CHECK (layer <> 'voice' OR src_author_id < dst_author_id)
);

-- Nessun indice aggiuntivo su (snapshot_id, layer): e' gia' il prefisso della
-- chiave primaria, che copre il filtro tipico "gli archi di un layer di uno
-- snapshot".

COMMENT ON TABLE graph_edges IS
    'Archi pesati per snapshot e layer. Tabella interna: mai esposta da un endpoint API (un arco identifica due membri per nome). I layer non si sommano mai tra loro.';

-- ---------------------------------------------------------------------------
-- voice_sessions / voice_session_participants: la ricostruzione persistita
-- ---------------------------------------------------------------------------
--
-- Persistite e non solo tenute in memoria durante il calcolo: servono alla
-- riconciliazione tra snapshot successivi, al debug di un peso che sembra
-- sbagliato (senza di queste si puo' solo rieseguire il job e sperare), e piu'
-- avanti alle metriche di autosostenibilita' del falo'.

CREATE TABLE IF NOT EXISTS voice_sessions (
    id                  BIGSERIAL PRIMARY KEY,

    guild_id            BIGINT NOT NULL,
    channel_id          BIGINT NOT NULL,

    started_at          TIMESTAMPTZ NOT NULL,

    -- NOT NULL: una sessione ancora aperta al momento del calcolo viene
    -- esclusa dallo snapshot e recuperata al successivo (modello-grafo.md
    -- 4.2), quindi non arriva mai fin qui. Una riga in questa tabella e' per
    -- costruzione una sessione conclusa.
    ended_at            TIMESTAMPTZ NOT NULL,

    -- 2 = brace, >=3 = falo'. Entrambe generano archi: la brace e' un'unita'
    -- di bonding valida di per se'.
    participant_count   INTEGER NOT NULL,

    -- FALSE se almeno un intervallo e' stato ricostruito invece che osservato.
    is_complete         BOOLEAN NOT NULL,

    reconciliation_note TEXT
);

-- Chiave naturale della sessione: la ricostruzione e' deterministica, quindi
-- rieseguire il job riscrive le stesse sessioni invece di duplicarle.
CREATE UNIQUE INDEX IF NOT EXISTS voice_sessions_identity
    ON voice_sessions (guild_id, channel_id, started_at);

-- Query attesa: le sessioni concluse in una finestra, per community. La fine
-- e non l'inizio, perche' e' la fine che decide a quale snapshot la sessione
-- viene attribuita (modello-grafo.md 4.1).
CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_ended_at
    ON voice_sessions (guild_id, ended_at);

CREATE TABLE IF NOT EXISTS voice_session_participants (
    session_id    BIGINT NOT NULL
                      REFERENCES voice_sessions(id) ON DELETE CASCADE,

    author_id     BIGINT NOT NULL,

    joined_at     TIMESTAMPTZ NOT NULL,
    left_at       TIMESTAMPTZ NOT NULL,

    -- Questo specifico intervallo e' stato chiuso dalla riconciliazione, non
    -- da un voice_leave osservato.
    is_reconciled BOOLEAN NOT NULL DEFAULT FALSE,

    -- joined_at fa parte della chiave: dentro una stessa sessione una persona
    -- puo' comparire piu' volte, se esce e rientra entro la finestra di
    -- sessione. Sono episodi distinti e la sovrapposizione va calcolata su
    -- ciascuno, non su un intervallo unico da primo ingresso a ultima uscita.
    PRIMARY KEY (session_id, author_id, joined_at)
);

CREATE INDEX IF NOT EXISTS idx_voice_session_participants_author
    ON voice_session_participants (author_id);

-- ---------------------------------------------------------------------------
-- message_authors: risoluzione message_id -> autore
-- ---------------------------------------------------------------------------
--
-- Reply e reazioni identificano il messaggio bersaglio, non il suo autore, ma
-- un arco va da persona a persona. La risoluzione vive qui, in una tabella
-- derivata, e non in una colonna denormalizzata su raw_events: quel log resta
-- immutabile e privo di comodita' aggiunte a posteriori (modello-grafo.md 6).
--
-- Popolata dal job a partire da raw_events, e arricchibile in futuro da un
-- comando di backfill lato bot per i messaggi anteriori all'arrivo di
-- Kindling. Il job non chiama mai l'API Discord: legge solo Postgres.

CREATE TABLE IF NOT EXISTS message_authors (
    guild_id   BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    author_id  BIGINT NOT NULL,

    -- guild_id per primo, coerente con tutto il resto dello schema: ogni
    -- query filtra per community prima di ogni altra cosa.
    PRIMARY KEY (guild_id, message_id)
);

COMMENT ON TABLE message_authors IS
    'Derivata: message_id -> author_id, per risolvere il bersaglio di reply e reazioni. Un target non risolvibile non genera un arco (un arco verso un autore ignoto non esiste), viene solo contato nelle statistiche di copertura.';
