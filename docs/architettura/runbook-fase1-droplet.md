# Runbook Fase 1 — bot + Postgres sulla droplet

*Checklist operativa per il deploy della droplet `kindling-app-01` (FRA1),
secondo il piano a due fasi di `stack-tecnologico-mvp.md`. Versione
formattata con comandi copiabili: artifact pubblicato "Kindling Fase 1".*

## Già pronto (verificato 28/08/2026)

- Droplet creata: `kindling-app-01`, progetto DO separato "Kindling", non condivisa con HeroesAscent.
- Chiave SSH dedicata a Kindling (distinta da HeroesAscent).
- fail2ban attivo su SSH.
- Cloud Firewall verificato: solo SSH/22 in ingresso da tutti gli IP, nient'altro — coerente con la regola non negoziabile per la Fase 1.

## Da fare, in ordine

1. **Swapfile 2 GB** sul droplet (`fallocate` + `mkswap` + `swapon` + entry in `/etc/fstab`).
2. **Docker Engine + plugin Compose** dal repository ufficiale Docker (non `docker.io` di Ubuntu).
3. **Codice sulla droplet** via deploy key GitHub di sola lettura dedicata (mai la chiave personale), poi `git clone`.
4. **`.env` con credenziali reali** sulla droplet (mai committato): `DISCORD_TOKEN` reale, `POSTGRES_PASSWORD` generata con `openssl rand -base64 24`.
5. **Avvio**: `docker compose up -d --build` — solo `postgres` e `bot` partono in questa fase.
6. **Verifica**: dall'esterno `nc -zv <ip-droplet> 5432` deve fallire (porta non raggiungibile); dentro il container, controllare che `raw_events` riceva righe.
7. **Backup dal giorno 1**: snapshot droplet settimanali dal pannello DO (Backups & Snapshots) + `pg_dump` giornaliero via cron verso un DO Space (con `rclone`, chiavi mai in git).

## Riferimento — analisi locale

Nessun Postgres locale necessario per esplorare i dati: tunnel SSH verso la
droplet (`ssh -L 5432:localhost:5432 <utente>@<ip-droplet>`), poi `psql`/client
SQL puntato su `localhost:5432`. Dettagli in `README.md`.

## Trigger Fase 2

Non è una scadenza a calendario: scatta quando API/dashboard deve essere
raggiungibile da altri (in primis il community manager) senza dipendere dal
laptop di chi sviluppa, o quando il job di calcolo deve girare su schedule
automatico. Vedi `stack-tecnologico-mvp.md`.

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
