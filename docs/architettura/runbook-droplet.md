# Runbook operativo — droplet `kindling-app-01`

*Checklist per il provisioning e il deploy della droplet `kindling-app-01`
(FRA1), la macchina unica su cui gira l'intera vertical slice. Le scelte di
architettura e il criterio per cambiare taglia sono in `architettura.md`.
Versione formattata con comandi copiabili: artifact pubblicato "Kindling Fase 1".*

## Già pronto (verificato 28/08/2026)

- Droplet creata: `kindling-app-01`, progetto DO separato "Kindling", non condivisa con HeroesAscent.
- Chiave SSH dedicata a Kindling (distinta da HeroesAscent).
- fail2ban attivo su SSH.
- Cloud Firewall verificato: solo SSH/22 in ingresso da tutti gli IP, nient'altro — finché non ci sono servizi pubblici da esporre.

## Da fare, in ordine

1. **Swapfile 2 GB** sul droplet (`fallocate` + `mkswap` + `swapon` + entry in `/etc/fstab`).
2. **Docker Engine + plugin Compose** dal repository ufficiale Docker (non `docker.io` di Ubuntu).
3. **Codice sulla droplet** via deploy key GitHub di sola lettura dedicata (mai la chiave personale), poi `git clone`.
4. **`.env` con credenziali reali** sulla droplet (mai committato): `DISCORD_TOKEN` reale, `POSTGRES_PASSWORD` generata con `openssl rand -base64 24`.
5. **Avvio**: `docker compose up -d --build` — oggi il compose definisce `postgres` e `bot`; `api`, `job` e dashboard si aggiungono a questo stesso file quando vengono sviluppati.
6. **Verifica**: dall'esterno `nc -zv <ip-droplet> 5432` deve fallire (porta non raggiungibile); dentro il container, controllare che `raw_events` riceva righe.
7. **Backup dal giorno 1**: snapshot droplet settimanali dal pannello DO (Backups & Snapshots) + `pg_dump` giornaliero via cron verso un DO Space (con `rclone`, chiavi mai in git).

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

## Applicare una nuova migration in produzione senza perdere `raw_events` (29/08/2026)

Caso concreto: aggiunta della tabella `members` (`migrations/0002_members.sql`)
dopo che la droplet è già in produzione con dati reali in `raw_events`.
Procedura generale, valida per ogni migration futura applicata a un volume
Postgres non vuoto:

1. **Verificare che il codice sia committato e pushato** sul branch che la
   droplet clona (deploy key di sola lettura). La droplet vede solo quello
   che è su GitHub, non la working copy locale.
2. **Backup di sicurezza prima di toccare la produzione**, anche se la
   migration è puramente additiva:
   ```bash
   docker compose exec postgres pg_dump -U kindling -d kindling -F c -f /tmp/pre_migration_backup.dump
   docker cp <container_postgres>:/tmp/pre_migration_backup.dump ./pre_migration_backup.dump
   ```
3. **Perché non basta `docker compose up -d --build`**: i file in
   `migrations/` vengono eseguiti da Postgres automaticamente solo al primo
   avvio, a volume dati (`pgdata`) vuoto (`docker-entrypoint-initdb.d`, vedi
   commento in `docker-compose.yml`). Su un volume già popolato (come sulla
   droplet dopo il go-live) le nuove migration **non partono da sole**: vanno
   applicate a mano, come già fa il README in locale.
4. **Applicare la migration sulla droplet.** Il file è già montato dentro il
   container Postgres (`./migrations:/docker-entrypoint-initdb.d:ro`), quindi
   non serve copiarlo:
   ```bash
   ssh utente@ip-droplet
   cd kindling
   git pull
   docker compose exec postgres psql -U kindling -d kindling -f /docker-entrypoint-initdb.d/000N_nome.sql
   ```
   Tutte le migration del progetto usano `CREATE TABLE IF NOT EXISTS` /
   `CREATE INDEX IF NOT EXISTS`: idempotenti, rilanciabili in sicurezza senza
   toccare le tabelle esistenti.
5. **Aggiornare il bot col nuovo codice** (non il DB):
   ```bash
   docker compose up -d --build bot
   ```
   Ricrea solo il container `bot`; il container/volume `postgres` resta in
   esecuzione, invariato.
6. **Da non fare mai**: `docker compose down -v` (o `docker volume rm`) —
   cancella il volume `pgdata`, quindi anche `raw_events`. Per riavviare i
   servizi basta sempre `docker compose up -d --build`, mai `down -v`.
7. **Passi applicativi specifici della migration**, se previsti — es. per
   `members`, lanciare `!backfill_members` (admin only) nel server Discord
   subito dopo il passo 5, per popolare `joined_at` dei membri già presenti
   prima che qualcuno di loro esca (altrimenti quel dato è perso per sempre).
8. **Verifica finale**:
   ```sql
   SELECT count(*) FROM raw_events;  -- deve solo essere cresciuto rispetto a prima
   SELECT count(*) FROM members;     -- popolato dopo il backfill
   ```
