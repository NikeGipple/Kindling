# Runbook operativo — droplet `kindling-app-01`

*Checklist per il provisioning e il deploy della droplet `kindling-app-01`,
la macchina unica su cui gira l'intera vertical slice. Le scelte di
architettura e il criterio per cambiare taglia sono in `architettura.md`.
Versione formattata con comandi copiabili: artifact pubblicato "Kindling Fase 1".*

## Già pronto (verificato 28/08/2026, backup aggiunti il 02/09/2026)

- Droplet creata: `kindling-app-01`, progetto DO separato "Kindling", non condivisa con altri progetti del team.
- Chiave SSH dedicata a Kindling (distinta da quelle di altri progetti del team).
- fail2ban attivo su SSH.
- Cloud Firewall verificato: solo SSH/22 in ingresso da tutti gli IP, nient'altro — finché non ci sono servizi pubblici da esporre.
- **Automated Backups di DigitalOcean attivi** (verificato 02/09/2026): snapshot dell'intera droplet, **settimanali, la domenica tra le 4:00 e le 8:00 UTC**, retention ~4 settimane. Comprendono il volume `pgdata`, quindi `raw_events` e `members`. **Il tema backup è chiuso**: niente `pg_dump` verso DO Spaces da costruire. Unico limite noto, accettato: finestra di perdita massima di 7 giorni (razionale in `architettura.md`, sezione Storage).

## Da fare, in ordine

1. **Swapfile 2 GB** sul droplet (`fallocate` + `mkswap` + `swapon` + entry in `/etc/fstab`).
2. **Docker Engine + plugin Compose** dal repository ufficiale Docker (non `docker.io` di Ubuntu).
3. **Codice sulla droplet** via deploy key GitHub di sola lettura dedicata (mai la chiave personale), poi `git clone`.
4. **`.env` con credenziali reali** sulla droplet (mai committato): `DISCORD_TOKEN` reale, `POSTGRES_PASSWORD` generata con `openssl rand -base64 24`.
5. **Avvio**: `docker compose up -d --build` — oggi il compose definisce `postgres` e `bot`; `api`, `job` e dashboard si aggiungono a questo stesso file quando vengono sviluppati.
6. **Verifica**: dall'esterno `nc -zv <ip-droplet> 5432` deve fallire (porta non raggiungibile); dentro il container, controllare che `raw_events` riceva righe.

*(Il punto "backup dal giorno 1" che stava qui è stato completato il 02/09/2026 — vedi "Già pronto" sopra.)*

## Riferimento — esplorazione dei dati dal laptop

Nessun Postgres locale necessario per esplorare i dati: tunnel SSH verso la
droplet (`ssh -L 5432:localhost:5432 <utente>@<ip-droplet>`), poi `psql`/client
SQL puntato su `localhost:5432`. Dettagli in `README.md`.

## Aggiungere API, job e dashboard sulla stessa droplet

Restano sulla macchina attuale (6 $/mese): i consumi misurati lasciano ~630 MB
liberi, vedi `architettura.md`. Punti di attenzione al momento del deploy:

1. **Stesso `docker-compose.yml`**, non un file separato: si aggiungono i
   servizi `api`, `job` e dashboard a quello esistente.
2. **`mem_limit` per servizio**, così un picco del job non fa scegliere all'OOM
   killer il processo più grosso (Postgres) — deve morire il job.
3. **Job schedulato** in orario di bassa attività, con `nice`: su 1 vCPU non
   deve rubare tempo all'heartbeat del gateway Discord del bot.
4. **Reverse proxy Caddy** davanti ad API e dashboard: HTTPS automatico +
   autenticazione (basic auth come minimo) prima di renderli raggiungibili.
5. **Cloud Firewall**: aprire 443/80 solo in quel momento, mai la 5432.
6. **Riverificare i consumi** dopo il deploy: `free -h` e
   `docker stats --no-stream`.

## Quando cambiare macchina

Non è una scadenza né una fase: si resize quando i numeri lo impongono (job in
OOM, `available` stabilmente sotto ~150 MB, dashboard in swap), quando si
aggiungono altre community o quando serve un grafo live invece degli snapshot
settimanali. Usare l'opzione **"CPU and RAM only"** (reversibile, disco
invariato), non il resize del disco che è irreversibile. Criteri completi in
`architettura.md`.

## Deploy di una modifica che tocca schema e codice

Procedura generale per applicare a una droplet già in produzione, con dati
reali in `raw_events`, un cambiamento che comprende migration e codice nuovo.
Collaudata due volte: sulla tabella `members` (29/08/2026) e sul layer del
grafo (31/08/2026).

**L'ordine non è invertibile.** Il codice nuovo scrive colonne che la migration
deve aver già creato: riavviare il bot prima di migrare lo manda in crash-loop
su ogni insert, e gli eventi in arrivo si perdono finché qualcuno non se ne
accorge. Migration prima, sempre.

1. **Committare e pushare** sul branch che la droplet clona (deploy key di sola
   lettura). La droplet vede solo quello che è su GitHub, mai la working copy
   locale — è l'errore più facile da fare, perché in locale è tutto pronto e
   sembra fatto. Controllare con `git status` che non finiscano nel commit
   `.env`, `__pycache__/`, `.pytest_cache/` o la cartella `exports/`.
2. **Backup prima di toccare la produzione**, anche se la migration è puramente
   additiva:
   ```bash
   docker compose exec postgres pg_dump -U kindling -d kindling -F c -f /tmp/pre_migration_backup.dump
   docker cp $(docker compose ps -q postgres):/tmp/pre_migration_backup.dump ./pre_migration_backup.dump
   ```
3. **Prendere il codice sulla droplet**: `git pull`.
4. **Aggiungere al `.env` le nuove variabili d'ambiente**, prima del comando che
   le userà. Se ne manca una, il fallimento arriva a metà procedura, quando è
   più scomodo. Le variabili che vanno generate una volta sola e mai più
   cambiate (es. `KINDLING_PSEUDONYM_SALT`, con
   `openssl rand -base64 32`) vanno anche annotate come tali: cambiarle
   invalida silenziosamente gli output precedenti.
5. **Applicare le migration a mano.** I file in `migrations/` vengono eseguiti
   da Postgres automaticamente **solo al primo avvio**, a volume dati (`pgdata`)
   vuoto (`docker-entrypoint-initdb.d`). Su un volume già popolato le nuove
   migration **non partono da sole**. Sono già montate nel container, quindi non
   serve copiarle:
   ```bash
   docker compose exec postgres psql -U kindling -d kindling \
     -f /docker-entrypoint-initdb.d/000N_nome.sql
   ```
   Tutte le migration del progetto usano `ADD COLUMN IF NOT EXISTS` /
   `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`: idempotenti,
   rilanciabili in sicurezza senza toccare le righe esistenti.
6. **Solo adesso il codice nuovo**:
   ```bash
   docker compose up -d --build bot
   ```
   Ricrea solo il container `bot`; il container e il volume `postgres` restano
   in esecuzione, invariati.
7. **Passi applicativi specifici**, se previsti — es. per `members`, lanciare
   `!backfill_members` (admin only) nel server Discord subito dopo il passo 6,
   per popolare `joined_at` dei membri già presenti prima che qualcuno di loro
   esca (altrimenti quel dato è perso per sempre).
8. **Verifica**:
   ```bash
   docker compose logs --tail=50 bot
   ```
   ```sql
   SELECT count(*) FROM raw_events;  -- deve solo essere cresciuto
   SELECT count(*) FROM members;
   \d raw_events                     -- le colonne nuove ci sono
   \dt                               -- le tabelle nuove ci sono
   ```

**Da non fare mai**: `docker compose down -v` (o `docker volume rm`) — cancella
il volume `pgdata`, quindi anche `raw_events`, che non è ricostruibile. Per
riavviare i servizi basta sempre `docker compose up -d --build`, mai `down -v`.

## Lanciare il job di calcolo del grafo

Il job è sotto `profiles: ["tools"]`: non parte con `docker compose up -d` e non
occupa memoria quando non serve. Si lancia a mano o da cron:

```bash
docker compose run --rm job python -m job.main snapshot --dry-run   # verifica
docker compose run --rm job python -m job.main snapshot             # scrive
docker compose run --rm job python -m job.main export --guild-id <id>
```

Il `--dry-run` calcola tutto e logga i numeri senza scrivere niente su
Postgres: è il passo da fare sempre prima della prima esecuzione dopo un
cambiamento al calcolo, e i suoi numeri vanno confrontati con quelli attesi
prima di lasciarlo scrivere.

Il job è **idempotente**: rilanciarlo sullo stesso `as_of` e sulla stessa
finestra riscrive lo snapshot esistente invece di affiancargliene un duplicato
(chiave `graph_snapshots (guild_id, as_of, window_start, window_end)`). È
quello che permette, dopo una correzione al calcolo, di rigenerare uno
snapshot già scritto:

```bash
docker compose run --rm job python -m job.main snapshot --as-of '<as_of dello snapshot>'
```

Gli export GraphML restano in `exports/` sull'host e si portano sul proprio PC
con `scp`. Sono file per Gephi, non dati del servizio, e la cartella è in
`.gitignore`. Di default i nodi sono pseudonimi: un export identificato è una
mappa sociale nominativa della community e richiede un flag esplicito.

## Esito del primo deploy completo (31/08/2026)

Riferimento di cosa aspettarsi, non un obiettivo da riprodurre: serve a
riconoscere quando qualcosa non torna.

- Migration `0003` (colonna su `raw_events`) e `0004` (tabelle del grafo)
  applicate senza toccare le righe esistenti.
- Build dell'immagine ~20 s; `python-igraph` installato da wheel precompilato,
  nessuna compilazione da sorgente (se il log mostra `Building wheel for
  python-igraph` invece di `Downloading ...whl`, fermarsi: su 1 GB non
  finisce bene).
- Bot riavviato: riconciliazione vocale con 0 sessioni chiuse, 0 join
  sintetici, 1 già allineata — cioè una persona era in vocale durante il
  deploy e la sua sessione non è stata interrotta.
- Primo snapshot su ~3 giorni di dati (152 eventi grezzi): 6 sessioni vocali,
  25 intervalli, **14 archi voice, 7 reply, 8 mention, 17 reaction**, su 9
  nodi nel layer vocale. Copertura della risoluzione dei target: 100% sulle
  menzioni, 85% sulle reazioni, 83% sulle reply — i target non risolti sono
  messaggi anteriori all'arrivo del bot, ed è normale che il numero scenda col
  tempo.
- I numeri del job sono stati verificati ricostruendo sessioni e archi
  indipendentemente dai `raw_events` esportati: coincidono tutti. Vale la pena
  rifare questa verifica indipendente ogni volta che il calcolo cambia in modo
  sostanziale — è l'unico controllo che non si fida del codice che sta
  testando.
