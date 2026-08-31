# Kindling
Community intelligence platform for Discord

Architettura e razionale delle scelte tecniche: `docs/architettura/stack-tecnologico-mvp.md`.

## Bot di ingestion (`bot/`)

Bot discord.py che cattura eventi grezzi (messaggi, reply, reazioni, thread,
voice join/leave, RSVP a eventi) e li scrive, senza mai modificarli, nella
tabella append-only `raw_events`. Nessuna logica di metriche/grafo qui: quel
calcolo legge `raw_events` a valle, a batch.

```
bot/
  client.py        # classe del bot: intents, pool DB, caricamento cog
  config.py        # configurazione da variabili d'ambiente
  db.py             # pool asyncpg + insert_raw_event() + stato members
  event_types.py    # tipi di evento canonici
  main.py           # entrypoint (python -m bot.main)
  cogs/
    ingestion.py    # listener discord.py -> raw_events (+ stato members)
    admin.py        # comandi operativi (es. !backfill_members)
```

### Setup locale

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # poi valorizzare DISCORD_TOKEN e DATABASE_URL
```

Nel Discord Developer Portal, sul bot vanno abilitati i Privileged Gateway
Intent **Message Content** e **Server Members**, altrimenti `on_message` e
gli eventi legati ai membri non arrivano.

### Database

Lo schema si applica con psql (nessun framework di migrazione per l'MVP,
coerente con la scelta di restare semplici a questa scala):

```bash
psql "$DATABASE_URL" -f migrations/0001_raw_events.sql
psql "$DATABASE_URL" -f migrations/0002_members.sql
psql "$DATABASE_URL" -f migrations/0003_referenced_channel_id.sql
psql "$DATABASE_URL" -f migrations/0004_graph_snapshots.sql
```

`migrations/0001_raw_events.sql` crea `raw_events`: vedi i commenti nel file
per il razionale di ogni colonna e indice (incluso il flag `forgotten_at` per
il diritto all'oblio richiesto dai Discord Developer ToS).

`migrations/0002_members.sql` crea `members` (stato corrente joined_at/left_at
per calcolare la retention, non append-only come raw_events). Quando Kindling
viene aggiunto a un nuovo server, lanciare una tantum, in quel server, il
comando `!backfill_members` (richiede permessi di amministratore): senza
questo passaggio il `joined_at` dei membri già presenti non viene mai
osservato via evento, e va perso per sempre se qualcuno esce prima del
backfill.

`migrations/0003_referenced_channel_id.sql` aggiunge a `raw_events` il canale
del messaggio a cui una reply risponde (Discord permette reply cross-canale).

`migrations/0004_graph_snapshots.sql` crea le tabelle del grafo calcolato
(`graph_snapshots`, `graph_edges`, `voice_sessions`,
`voice_session_participants`, `message_authors`). Sono tutte derivate e
ricostruibili da `raw_events`, e tutte **interne**: non vengono mai esposte da
un endpoint API.

Le migrazioni vengono applicate automaticamente solo al **primo** avvio di un
volume Postgres vuoto (`docker-entrypoint-initdb.d`). Su un database già
esistente — la droplet — vanno applicate a mano con `psql`, in ordine.

### Avvio del bot

```bash
python -m bot.main
```

### In alternativa: Docker Compose (locale)

`docker-compose.yml` fa girare il bot contro un Postgres locale, utile per
sviluppo isolato senza dipendere dalla droplet online. Al primo avvio,
Postgres applica automaticamente `migrations/0001_raw_events.sql` (monta la
cartella `migrations/` come `docker-entrypoint-initdb.d`).

```bash
cp .env.example .env  # valorizzare almeno DISCORD_TOKEN e POSTGRES_PASSWORD;
                       # DATABASE_URL viene sovrascritto dal compose per
                       # puntare al postgres locale
docker compose up --build
```

Il servizio `postgres` pubblica la porta **solo sul loopback dell'host**
(`127.0.0.1:5432:5432`), mai su tutte le interfacce: non è raggiungibile
dall'esterno in nessun caso, ma è raggiungibile da un tunnel SSH aperto sulla
stessa macchina — vedi `CLAUDE.md`. Dall'interno del compose resta comodo
`docker compose exec postgres psql -U kindling -d kindling`.

### Connettersi al Postgres online (Fase 1: bot + DB sulla droplet)

In Fase 1 (vedi `docs/architettura/stack-tecnologico-mvp.md`) il bot e Postgres
girano sempre accesi sulla droplet DigitalOcean, senza porte pubblicate: per
analizzare i dati in locale ci si collega via tunnel SSH invece di far
girare un Postgres locale:

```bash
ssh -L 5432:localhost:5432 utente@ip-droplet
# in un altro terminale, con il tunnel aperto:
psql "postgresql://kindling:<password>@localhost:5432/kindling"
```

Qualunque tool locale (psql, un client SQL grafico, uno script Python) può
allo stesso modo puntare a `localhost:5432` finché il tunnel resta aperto.

## Job di calcolo del grafo (`job/`)

Costruisce il grafo sociale dagli eventi grezzi e ne salva uno snapshot. Si
ferma agli archi: nessuna metrica SNA, nessun endpoint. La specifica del
modello è `docs/architettura/modello-grafo.md` — è quella la fonte di verità,
non questo codice.

Quattro layer, calcolati separatamente e **mai sommati tra loro**:

| Layer | Direzione | Unità di peso |
|---|---|---|
| `voice` | non diretto | minuti di sovrapposizione reale |
| `reply` | A→B | numero di reply |
| `mention` | A→B | numero di menzioni |
| `reaction` | A→B | numero di reazioni |

```
job/
  config.py      # tutti i parametri del modello, in un posto solo
  decay.py       # decadimento del legame rispetto all'as_of dello snapshot
  intervals.py   # eventi vocali -> intervalli di presenza (+ riconciliazione)
  sessions.py    # intervalli -> sessioni per canale
  edges.py       # sessioni e interazioni -> archi pesati per layer
  snapshot.py    # orchestrazione e attribuzione temporale (funzione pura)
  graph.py       # grafo igraph + export GraphML
  pseudonyms.py  # pseudonimi stabili per gli export
  db.py          # tutto il SQL, e nient'altro
  main.py        # CLI (snapshot | export)
```

Il job **legge solo Postgres**: non chiama mai l'API Discord. Non è un servizio
sempre acceso — parte, calcola, scrive e muore.

### Lanciarlo sulla droplet

```bash
docker compose run --rm job python -m job.main snapshot
```

Il servizio `job` è nello stesso `docker-compose.yml` di `postgres` e `bot`, ma
sotto il profilo `tools`: `docker compose up` non lo avvia, `docker compose
run` sì.

### Lanciarlo in locale contro il Postgres della droplet (tunnel SSH)

Il modo normale di lavorarci durante lo sviluppo: il codice gira sul laptop, i
dati restano sulla droplet. In un terminale, apri il tunnel e lascialo aperto:

```bash
ssh -L 5432:localhost:5432 utente@ip-droplet
```

In un secondo terminale, con il tunnel attivo:

```bash
export DATABASE_URL="postgresql://kindling:<password>@localhost:5432/kindling"
python -m job.main snapshot --dry-run
```

`--dry-run` calcola e stampa tutto (sessioni ricostruite, archi per layer,
copertura della risoluzione degli autori) senza scrivere niente: è il modo di
guardare cosa produrrebbe uno snapshot prima di produrlo davvero, soprattutto
contro il database di produzione.

Opzioni principali di `snapshot`:

| Opzione | Default |
|---|---|
| `--guild-id` | tutte le guild con eventi nella finestra |
| `--as-of` | adesso — istante rispetto a cui i pesi decadono |
| `--window-days` | 7 |
| `--window-start` / `--window-end` | derivate da `as_of` e `--window-days` |
| `--dry-run` | disattivo |

Rieseguire il job con lo stesso `as_of` e la stessa finestra riscrive lo
snapshot esistente invece di duplicarlo.

### Export per Gephi

```bash
python -m job.main export --snapshot-id 42 --out-dir exports
```

Un file GraphML **per layer** (i layer non si fondono, nemmeno per comodità di
ispezione), con i pesi come attributi d'arco: `weight` (normalizzato e
decaduto), `weight_undecayed`, `raw_units`, `interaction_count`,
`last_interaction_at`, `is_reconciled`.

I nodi portano **id pseudonimi** per default: un export identificato è una
mappa sociale nominativa della community e non deve poter finire per sbaglio in
un repository o in una cartella condivisa. Serve `KINDLING_PSEUDONYM_SALT` nel
`.env` (vedi `.env.example`) — senza salt il comando si rifiuta di partire,
perché l'hash di un id Discord senza salt è invertibile per forza bruta. Per
gli id reali serve il flag esplicito `--identified`.

`exports/` è in `.gitignore`.

### Test

```bash
pip install -r requirements-dev.txt
pytest
```

Fixture sintetiche, nessun database: coprono i casi limite della ricostruzione
delle sessioni (intervalli vicini ma mai sovrapposti, sessioni a cavallo del
confine di uno snapshot, sessioni ancora aperte, sessioni orfane, spostamenti
tra canali) e le regole su normalizzazione, decadimento e self-loop.
