# Kindling
Community intelligence platform for Discord

Architettura e razionale delle scelte tecniche: `docs/architettura/architettura.md`.

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
  backfill.py       # backfill interno di members (nessun comando)
  cogs/
    ingestion.py    # listener discord.py -> raw_events (+ stato members)
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
psql "$DATABASE_URL" -f migrations/0005_snapshot_stats.sql
```

`migrations/0001_raw_events.sql` crea `raw_events`: vedi i commenti nel file
per il razionale di ogni colonna e indice (incluso il flag `forgotten_at` per
il diritto all'oblio richiesto dai Discord Developer ToS).

`migrations/0002_members.sql` crea `members` (stato corrente joined_at/left_at
per calcolare la retention, non append-only come raw_events). I membri già
presenti quando Kindling arriva su un server non hanno mai prodotto un evento
di ingresso: il loro `joined_at` viene recuperato dal **backfill interno**
(`bot/backfill.py`), che gira da solo quando il bot entra in un server e
all'avvio per ogni server non ancora backfillato. Non c'è nessun comando da
lanciare, ed è voluto: un backfill rieseguito su un server già osservato
riscriverebbe `joined_at` osservati con quelli storici, rompendo senza errori
la distinzione tra membri osservati e backfillati su cui poggiano le metriche
di coorte. Il percorso di backfill inserisce soltanto, non aggiorna mai.

`migrations/0003_referenced_channel_id.sql` aggiunge a `raw_events` il canale
del messaggio a cui una reply risponde (Discord permette reply cross-canale).

`migrations/0004_graph_snapshots.sql` crea le tabelle del grafo calcolato
(`graph_snapshots`, `graph_edges`, `voice_sessions`,
`voice_session_participants`, `message_authors`). Sono tutte derivate e
ricostruibili da `raw_events`, e tutte **interne**: non vengono mai esposte da
un endpoint API.

`migrations/0005_snapshot_stats.sql` aggiunge a `graph_snapshots` la colonna
`stats` (JSONB) con i contatori diagnostici dell'esecuzione che ha prodotto lo
snapshot: ricostruzione degli intervalli e copertura della risoluzione dei
layer direzionali. Servono confrontati tra snapshot successivi — è la loro
variazione a dire se un problema è nell'ingestion o nel calcolo — e in una
riga di log quella serie storica non esiste.

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

In Fase 1 (vedi `docs/architettura/architettura.md`) il bot e Postgres
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

Costruisce il grafo sociale dagli eventi grezzi, ne salva uno snapshot, e da
quello calcola le metriche aggregate destinate all'API. Due specifiche, ed è
quella la fonte di verità, non questo codice: `modello-grafo.md` per la
costruzione degli archi, `modello-metriche.md` per le metriche.

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
  admission.py   # quali coppie entrano in una metrica: una regola, tutti i percorsi
  robustness.py  # robustezza strutturale, con il baseline della rimozione casuale
  communities.py # Leiden, e il matching tra partizioni di snapshot successivi
  cohorts.py     # coorti di ingresso, Kaplan-Meier, retention
  suppression.py # soglia N: dove una cella diventa NULL invece di un numero
  metrics.py     # orchestrazione delle metriche (funzione pura)
  pseudonyms.py  # pseudonimi stabili per gli export
  db.py          # tutto il SQL, e nient'altro
  main.py        # CLI (snapshot | metrics | export)
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

### Metriche aggregate

```bash
python -m job.main metrics --dry-run
python -m job.main metrics
```

Senza argomenti lavora su **tutte** le guild con almeno uno snapshot, prendendo
l'ultimo di ciascuna — stesso contratto di `snapshot`. `--guild-id` restringe a
una community e `--snapshot-id` a uno snapshot preciso; sono mutuamente
esclusivi.

Legge uno snapshot già scritto e ne
calcola le tre metriche di `modello-metriche.md`: robustezza strutturale,
struttura e stabilità delle community (Leiden), onboarding e retention per
coorte. Non ricalcola il grafo: si possono ritarare i parametri delle metriche
senza rifare la ricostruzione delle sessioni, che è la parte costosa.

Richiede la migration `0006_metrics.sql` applicata.

Le tabelle `metric_*` sono le **uniche** pensate per essere lette dall'API, ed è
il motivo per cui la soglia di cardinalità vive nel calcolo e non nella
dashboard: una cella sotto soglia arriva in tabella già a `NULL`, con un flag,
**mai a zero** — zero è un valore legittimo e diverso da "non mostrabile". Il
vincolo è imposto da un trigger nella migration, non dal codice che scrive.

Tre stati diversi da non confondere leggendo una riga:

| Stato | Come si presenta |
|---|---|
| Buona | valori presenti, `is_significant = true` |
| Pubblicata ma inaffidabile | valori presenti, `is_significant = false`, motivo in `details` |
| Soppressa | tutto `NULL` tranne la chiave, `is_suppressed = true` |

Con i dati di oggi (tre giorni, nove nodi) ci si aspetta il secondo stato per le
righe strutturali e il terzo per quasi tutte le coorti. È il comportamento
corretto, non un difetto da correggere.

Nessun output per-nodo sopravvive al calcolo: centralità, appartenenza alle
community e conteggio connessioni del singolo membro sono passaggi interni, non
finiscono in nessuna tabella e non compaiono in nessun log, a nessun livello.

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
tra canali) e le regole su normalizzazione, decadimento e self-loop. Per le
metriche, grafi piccoli di cui si conosce la risposta giusta: una stella che si
frammenta togliendo il centro, due cricche unite da un solo arco, una coorte
sotto soglia soppressa e **non** azzerata.

Un solo file fa eccezione, `tests/test_metrics_schema.py`: verifica che sia il
**database** a rifiutare una riga soppressa con un valore dentro, che è una
garanzia dello schema e non del codice, e quindi non è verificabile su fixture.
Si salta da solo senza Postgres. Per eseguirlo serve un database di prova — mai
quello di produzione, crea e distrugge uno schema:

```bash
KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test pytest
```
