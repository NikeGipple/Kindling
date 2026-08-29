# Kindling
Community intelligence platform for Discord

Architettura e razionale delle scelte tecniche: `architettura/stack-tecnologico-mvp.md`.

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

Il servizio `postgres` non pubblica mai la porta sull'host (vedi
`CLAUDE.md`): per ispezionarlo da fuori usa `docker compose exec postgres
psql -U kindling -d kindling`.

### Connettersi al Postgres online (Fase 1: bot + DB sulla droplet)

In Fase 1 (vedi `architettura/stack-tecnologico-mvp.md`) il bot e Postgres
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
