# Kindling

Community intelligence platform per Discord.

Misura la **salute sociale** di una community — come nascono, si rafforzano e
si mantengono le relazioni tra i membri — a partire dal grafo delle
interazioni, non dalle vanity metrics di attività.

---

## Come funziona, in breve

```
Discord ──▶ bot/ ──▶ raw_events ──▶ job/ ──▶ graph_snapshots   ──▶ api/ ──▶ dashboard
          (sempre     (append-only,  (batch,   + metric_*                  (non ancora
           acceso)     mai modificata) settimanale)                          esistente)
```

| Componente | Cosa fa | Quando gira |
|---|---|---|
| `bot/` | cattura eventi grezzi da Discord e li scrive in `raw_events` | sempre acceso |
| `job/` | costruisce il grafo, salva uno snapshot, calcola le metriche aggregate | a batch, cron settimanale |
| `api/` | serve gli aggregati in sola lettura | sempre acceso |

Tre confini che spiegano quasi tutte le scelte del codice:

1. **`raw_events` non si modifica mai.** Nessuna logica di metriche nel bot.
2. **Il job legge solo Postgres**, mai l'API Discord. Parte, calcola, scrive, muore.
3. **L'API non calcola niente** e non legge nessuna tabella con dati riferibili
   a una persona — è una garanzia del database (ruolo di sola lettura,
   migration `0010`), non una convenzione del codice.

---

## Dove sta la fonte di verità

Questo README dice **come si usa** il progetto. Il **perché** delle scelte sta
altrove, ed è quella la documentazione autorevole quando c'è un dubbio:

| Documento | Argomento |
|---|---|
| `docs/architettura/architettura.md` | architettura e razionale delle scelte tecniche |
| `docs/architettura/modello-grafo.md` | costruzione degli archi (spec del grafo) |
| `docs/architettura/modello-metriche.md` | definizione delle metriche |
| `docs/architettura/api.md` | perimetro e contratto dell'API |
| `docs/architettura/runbook-droplet.md` | operazioni sulla droplet, deploy, verifica del cron |
| `CLAUDE.md` | regole non negoziabili ed errori già commessi |
| commenti dentro `migrations/*.sql` | razionale di ogni colonna, indice e vincolo |

---

## Struttura del repository

```
bot/                  ingestion Discord -> raw_events
  client.py           bot: intents, pool DB, caricamento cog
  config.py           configurazione da variabili d'ambiente
  db.py               pool asyncpg, insert_raw_event(), stato members
  event_types.py      tipi di evento canonici
  backfill.py         backfill interno di members (automatico, nessun comando)
  cogs/ingestion.py   listener discord.py -> raw_events
  main.py             entrypoint (python -m bot.main)

job/                  grafo e metriche
  config.py           tutti i parametri del modello, in un posto solo
  intervals.py        eventi vocali -> intervalli di presenza
  sessions.py         intervalli -> sessioni per canale
  edges.py            sessioni e interazioni -> archi pesati per layer
  decay.py            decadimento del legame rispetto all'as_of
  graph.py            grafo igraph + export GraphML
  snapshot.py         orchestrazione dello snapshot (funzione pura)
  admission.py        quali coppie entrano in una metrica
  robustness.py       robustezza strutturale + baseline casuale
  communities.py      Leiden + matching tra snapshot successivi
  cohorts.py          coorti di ingresso, Kaplan-Meier, retention
  suppression.py      soglia N: dove una cella diventa NULL
  metrics.py          orchestrazione delle metriche (funzione pura)
  pseudonyms.py       pseudonimi stabili per gli export
  db.py               tutto il SQL, e nient'altro
  main.py             CLI: snapshot | metrics | export

api/                  FastAPI di sola lettura sugli aggregati
  config.py           parametri e perimetro delle tabelle leggibili
  models.py           modelli di risposta: quality accanto a values
  assemble.py         da riga a risposta (funzioni pure, nessun DB)
  db.py               tutto il SQL, e nient'altro
  main.py             applicazione FastAPI

migrations/           schema SQL, numerato e additivo
ops/                  script di deploy ed esecuzione settimanale
tests/                fixture sintetiche, nessun database (salvo 2 file)
```

---

## Setup

### 1. Ambiente Python

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Valorizzare in `.env` almeno `DISCORD_TOKEN` e `DATABASE_URL`. Ogni variabile
è commentata in `.env.example`; le trappole note (in particolare
`KINDLING_CODE_VERSION`, che **non va aggiunta**) sono spiegate lì.

### 2. Permessi del bot

Nel Discord Developer Portal vanno abilitati i Privileged Gateway Intent
**Message Content** e **Server Members**: senza, `on_message` e gli eventi
legati ai membri non arrivano.

### 3. Schema del database

Le migration si applicano **a mano, in ordine**, e sono additive e
idempotenti:

```bash
for f in migrations/[0-9]*.sql; do psql "$DATABASE_URL" -f "$f"; done
```

- Su un **volume Postgres vuoto** avviato con Docker Compose vengono applicate
  da sole al primo avvio (`docker-entrypoint-initdb.d`). Su un database già
  esistente — la droplet — mai: sempre a mano.
- `schema_migrations` (dalla `0012`) tiene traccia di cosa è applicato su un
  dato database; ogni migration registra da sé il proprio nome file.
  `ops/kindling-deploy.sh` legge quella tabella e si ferma se manca qualcosa.
- Prerequisiti per componente: `metrics` richiede `0006`, l'API richiede `0010`
  più la password del ruolo `kindling_api` impostata a mano (vedi
  `runbook-droplet.md`).

I membri già presenti quando Kindling arriva su un server non hanno mai
prodotto un evento di ingresso: il loro `joined_at` viene recuperato dal
backfill interno (`bot/backfill.py`), che parte da solo. **Non esiste un
comando di backfill, ed è voluto** — rieseguirlo su un server già osservato
sovrascriverebbe `joined_at` osservati con quelli storici e romperebbe in
silenzio le metriche di coorte.

---

## Collegarsi al database

C'è un solo modo di lavorare in locale sui dati veri, e vale per psql, per un
client SQL grafico, per il job e per l'API: **tunnel SSH verso la droplet**.
Postgres non pubblica mai la porta se non sul loopback.

In un terminale, da lasciare aperto:

```bash
ssh -L 5432:localhost:5432 utente@ip-droplet
```

In un secondo terminale, finché il tunnel è attivo:

```bash
export DATABASE_URL="postgresql://kindling:<password>@localhost:5432/kindling"
psql "$DATABASE_URL"
```

In alternativa, per sviluppo isolato senza toccare i dati veri:

```bash
docker compose up --build     # bot + postgres + api locali
docker compose exec postgres psql -U kindling -d kindling
```

---

## Uso

### Bot

```bash
python -m bot.main                      # in locale
docker compose up -d bot                # sulla droplet
```

### Job — snapshot del grafo

```bash
python -m job.main snapshot --dry-run   # calcola e stampa, senza scrivere
python -m job.main snapshot
docker compose run --rm job python -m job.main snapshot   # sulla droplet
```

`--dry-run` è il modo di guardare cosa produrrebbe uno snapshot prima di
produrlo davvero, soprattutto contro il database di produzione. Rieseguire con
lo stesso `as_of` e la stessa finestra riscrive lo snapshot esistente invece di
duplicarlo.

| Opzione | Default |
|---|---|
| `--guild-id` | tutte le guild con eventi nella finestra |
| `--as-of` | adesso — istante rispetto a cui i pesi decadono |
| `--window-days` | 7 |
| `--window-start` / `--window-end` | derivate da `as_of` e `--window-days` |
| `--dry-run` | disattivo |

Il grafo ha quattro layer, calcolati separatamente e **mai sommati tra loro**:

| Layer | Direzione | Unità di peso |
|---|---|---|
| `voice` | non diretto | minuti di sovrapposizione reale |
| `reply` | A→B | numero di reply |
| `mention` | A→B | numero di menzioni |
| `reaction` | A→B | numero di reazioni |

### Job — metriche aggregate

```bash
python -m job.main metrics --dry-run
python -m job.main metrics
```

Legge uno snapshot già scritto e calcola le tre metriche di
`modello-metriche.md` (robustezza strutturale, community Leiden, coorti e
retention). Non ricalcola il grafo: si possono ritarare i parametri senza
rifare la ricostruzione delle sessioni, che è la parte costosa.

Senza argomenti lavora su tutte le guild con almeno uno snapshot, prendendo
l'ultimo di ciascuna. `--guild-id` restringe a una community, `--snapshot-id` a
uno snapshot preciso; sono mutuamente esclusivi.

### Job — export per Gephi

```bash
python -m job.main export --snapshot-id 42 --out-dir exports
```

Un file GraphML **per layer**, con i pesi come attributi d'arco (`weight`,
`weight_undecayed`, `raw_units`, `interaction_count`, `last_interaction_at`,
`is_reconciled`).

I nodi portano **id pseudonimi** per default e serve `KINDLING_PSEUDONYM_SALT`
nel `.env`: senza salt il comando si rifiuta di partire, perché l'hash di un id
Discord senza salt è invertibile per forza bruta. Gli id reali richiedono il
flag esplicito `--identified` — un export identificato è una mappa sociale
nominativa della community e non deve poter finire per sbaglio in un repository
o in una cartella condivisa. `exports/` è in `.gitignore`.

### API

Sulla droplet parte con gli altri servizi (`docker compose up -d`) e **non
pubblica porte**: è raggiungibile dagli altri container e, per lo sviluppo, via
tunnel SSH. Niente TLS né autenticazione, perché non c'è ancora niente di
esposto — sono il prerequisito del momento in cui esisterà il dashboard.

In locale, con il tunnel aperto:

```bash
API_DATABASE_URL="postgresql://kindling_api:<password>@localhost:5432/kindling" \
  uvicorn api.main:app --reload
```

| Endpoint | Risponde a |
|---|---|
| `GET /health` | l'API è viva e Postgres risponde |
| `GET /guilds` | quali community sono osservate, e a che data arrivano le metriche |
| `GET /guilds/{id}` | ancora di osservabilità, backfill, buchi di osservazione |
| `GET /guilds/{id}/runs` | stato dell'ultimo snapshot (`?limit=1`) e storico |
| `GET /guilds/{id}/robustness` | serie storica della robustezza strutturale |
| `GET /guilds/{id}/communities` | Leiden, con la distribuzione delle dimensioni annidata |
| `GET /guilds/{id}/cohorts` | coorti: onboarding **e** retention insieme |

Documentazione interattiva su `/docs` (generata da FastAPI).

### Esecuzione settimanale e deploy

Sulla droplet il job gira da cron il lunedì alle **04:15 UTC**
(`ops/kindling-weekly.sh`: `flock`, `nice`, e `metrics` solo se `snapshot` è
riuscito). Il deploy di una modifica già pushata si fa con
`ops/kindling-deploy.sh`, che **non applica migration**: le elenca e si ferma
se ne manca qualcuna.

Installazione, verifica e — soprattutto — come accorgersi che il cron ha smesso
di funzionare sono in `docs/architettura/runbook-droplet.md`.

La cadenza settimanale non è un dettaglio operativo: con `--window-days 7` le
finestre si affiancano senza sovrapporsi, ed è la condizione in cui
`stability_jaccard` misura la ricomposizione delle community e non la
sovrapposizione delle finestre.

---

## Come leggere i risultati

Ogni riga di metrica può trovarsi in **tre stati diversi**, da non confondere:

| Stato | Come si presenta |
|---|---|
| Buona | valori presenti, `is_significant = true` |
| Pubblicata ma inaffidabile | valori presenti, `is_significant = false`, motivo in `details` |
| Soppressa | tutto `NULL` tranne la chiave, `is_suppressed = true` |

Una cella sotto soglia di cardinalità arriva in tabella già a `NULL`, con un
flag, **mai a zero**: zero è un valore legittimo e diverso da "non mostrabile".
Il vincolo è imposto da un trigger nella migration, non dal codice che scrive.

> Con i dati attuali (pochi giorni, pochi nodi) ci si aspetta il secondo stato
> per le righe strutturali e il terzo per quasi tutte le coorti. È il
> comportamento corretto, non un difetto da correggere.

**Nessun output per-nodo sopravvive al calcolo**: centralità, appartenenza alle
community e conteggio connessioni del singolo membro sono passaggi interni, non
finiscono in nessuna tabella e non compaiono in nessun log, a nessun livello.

Sul lato API, un valore non viaggia mai senza i flag che dicono quanto vale:
ogni riga è `{chiave…, quality, values}`, e su una riga soppressa `values`
resta con tutti i campi a `null` — se sparisse, "soppressa" somiglierebbe ad
"assente".

---

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

Fixture sintetiche, nessun database: coprono i casi limite della ricostruzione
delle sessioni (intervalli vicini ma mai sovrapposti, sessioni a cavallo del
confine di uno snapshot, sessioni ancora aperte, sessioni orfane, spostamenti
tra canali), le regole su normalizzazione, decadimento e self-loop, e — per le
metriche — grafi piccoli di cui si conosce la risposta giusta.

Due file fanno eccezione e richiedono un Postgres di prova, perché verificano
garanzie dello **schema** e non del codice (si saltano da soli se il database
non c'è):

```bash
KINDLING_TEST_DATABASE_URL=postgresql://kindling:...@localhost:5432/kindling_test pytest
```

- `tests/test_metrics_schema.py` — che sia il database a rifiutare una riga
  soppressa con un valore dentro.
- `tests/test_api_role_schema.py` — che il ruolo di sola lettura **non possa**
  leggere `graph_edges` e `members`, non possa scrivere, e possa leggere
  `guilds` e le `metric_*`.

Mai contro il database di produzione: creano e distruggono uno schema.
