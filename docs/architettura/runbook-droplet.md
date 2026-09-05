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
7. **Passi applicativi specifici**, se previsti — migrazioni di *dati* (non di
   schema) e simili. Il backfill di `members` **non è più di questi**: è
   interno al bot e gira da solo all'avvio e quando il bot entra in un server,
   quindi non c'è niente da lanciare a mano. Se dopo il riavvio i log del bot
   non mostrano `Backfill members completato` né `già backfillata`, quello è un
   problema da guardare, non un passo da eseguire.
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

## Lanciare il calcolo delle metriche aggregate

**Prima serve la migration `0006_metrics.sql`**, applicata come tutte le altre
(passo 5 della procedura di deploy) e **in ordine, dopo `0005`**. Senza, il
sottocomando fallisce alla prima scrittura: le tabelle `metric_*` non esistono.

```bash
docker compose exec postgres psql -U kindling -d kindling \
  -f /docker-entrypoint-initdb.d/0006_metrics.sql
```

```bash
docker compose exec postgres psql -U kindling -d kindling \
  -f /docker-entrypoint-initdb.d/0007_metrics_observability.sql
```

```bash
docker compose exec postgres psql -U kindling -d kindling \
  -f /docker-entrypoint-initdb.d/0008_guilds.sql
```

```bash
docker compose exec postgres psql -U kindling -d kindling \
  -f /docker-entrypoint-initdb.d/0009_guild_rejoined_at.sql
```

`0009` aggiunge `guilds.rejoined_at`, che con `left_at` delimita il buco di
osservazione quando il bot viene rimosso e riaggiunto a un server.

### Migrazione dati una tantum: `guilds` per L'Arco del Leone

`0008` crea la tabella `guilds` vuota. Per un server nuovo il bot la popola da
solo al primo avvio; **L'Arco no**, perché è già stato backfillato a mano il
29/08/2026 e il bot non deve rifarlo. Senza questa riga il job tratterebbe ogni
coorte come anteriore all'osservazione, e il backfill automatico ripartirebbe su
un server già osservato.

> **L'ordine non è una preferenza: invertirlo costa un dato permanente.** Questa
> `INSERT` va eseguita **prima** di ricostruire il container `bot`. Appena il
> codice nuovo parte, `on_ready` chiama `register_guild` con
> `first_seen_at = now()` — e quel valore, per progetto, **non viene mai
> riscritto**: spostare l'ancora dopo averla fissata cancellerebbe osservazioni
> valide o ne inventerebbe di mai fatte. Se il bot parte per primo, la data del
> riavvio resta lì per sempre al posto di quella reale, ogni coorte precedente
> risulta anteriore all'osservazione, e l'unico rimedio è un `UPDATE` a mano
> sulla produzione — cioè esattamente la cosa che questo runbook esiste per
> evitare. Nell'ordine dei passi di deploy: questa `INSERT` sta con le
> migration (passo 5), non con i passi applicativi (passo 7).

Da eseguire **una volta sola**, dopo `0008` e prima di ricostruire il bot:

```sql
INSERT INTO guilds (guild_id, first_seen_at, backfilled_at)
SELECT guild_id, MIN(occurred_at), TIMESTAMPTZ '2026-08-29 00:00:00+00'
FROM raw_events
GROUP BY guild_id
ON CONFLICT (guild_id) DO NOTHING;
```

I due valori, e da dove vengono:

- **`first_seen_at` = `MIN(occurred_at)` dei `raw_events` di quella guild.** È
  esattamente il valore che il job calcolava prima come ancora di
  osservabilità, quindi per L'Arco non cambia nessun numero già prodotto — la
  differenza è che da adesso l'ancora è un dato scritto e non un minimo
  ricalcolato ogni volta su una tabella che può cambiare sotto (una riga
  cancellata per diritto all'oblio sposterebbe il minimo).
- **`backfilled_at` = la data in cui il backfill manuale è stato eseguito**,
  cioè il 29/08/2026. È un **marcatore di "già fatto", non una misura**:
  nessun calcolo lo legge come numero, serve solo perché il bot non ripeta il
  backfill. Se la data esatta fosse incerta, qualunque valore non nullo
  precedente a oggi ha lo stesso effetto — ma va scritto quello vero, perché
  resta l'unica traccia di quando è successo.

Verifica:

```sql
SELECT guild_id, first_seen_at, backfilled_at, left_at, rejoined_at FROM guilds;
```

Una riga per server, `backfilled_at` valorizzato, `left_at` e `rejoined_at`
nulli (nessun buco di osservazione). Se
`backfilled_at` risultasse `NULL`, al riavvio il bot rifarebbe il backfill:
non corromperebbe niente (il percorso inserisce e non aggiorna), ma è una
chiamata inutile all'API Discord su tutta la member list.

`0007` aggiunge le colonne che dichiarano i due limiti scoperti alla prima
esecuzione in produzione: `is_survivors_only` (coorti anteriori all'inizio
dell'osservazione, la cui retention sarebbe 1.00 per costruzione) e
`has_snapshot_coverage` (coorti senza dato di grafo sulla propria finestra). È
additiva e non tocca le righe già scritte: su quelle le colonne nuove restano
`NULL`, che è il valore giusto — sono state calcolate senza conoscere né
l'ancora né la copertura.

`0006` crea anche una funzione di trigger condivisa dalle cinque tabelle di
metrica, che rifiuta una riga soppressa contenente un valore. È il vincolo su
cui poggia la regola "le tabelle lette dall'API contengono già solo aggregati
sopra soglia": se `psql` segnala un errore su quella funzione, la migration non
è andata a fondo e le metriche non vanno scritte finché non si capisce perché.

Poi, come il resto del job (profilo `tools`, non parte con `docker compose up`):

```bash
docker compose run --rm job python -m job.main metrics --dry-run   # verifica
docker compose run --rm job python -m job.main metrics             # scrive
```

Il sottocomando **legge uno snapshot già scritto** invece di ricalcolare il
grafo: va lanciato dopo `snapshot`. Senza argomenti processa **tutte** le guild
con almeno uno snapshot, prendendo l'ultimo di ciascuna — lo stesso contratto di
`snapshot`; `--guild-id` e `--snapshot-id` restringono, e sono mutuamente
esclusivi. Ricalcolare sullo stesso snapshot **riscrive** le righe invece di
duplicarle.

Cosa aspettarsi oggi, con circa tre giorni di dati e un grafo di nove nodi:

- **righe strutturali scritte ma `is_significant = false`.** Su nove nodi
  betweenness e modularità producono numeri formalmente validi e
  sostanzialmente casuali: la riga esiste, i valori ci sono, e dichiara di non
  essere affidabile. Non è un errore da correggere, ed è uno stato diverso da
  "soppressa";
- **quasi tutte le righe di coorte soppresse** (`is_suppressed = true`, valori
  a `NULL`): con pochi ingressi a settimana le coorti stanno sotto la soglia
  N = 5. Anche questo è corretto — una tabella quasi tutta soppressa in questa
  fase è il segno che la regola funziona, non che la soglia è sbagliata;
- **nessuna coorte significativa**, e le coorti anteriori all'arrivo del bot
  marcate `is_survivors_only = true` con retention non calcolabile
  (`not_computable_reason = 'before_observability_anchor'`). Con un solo
  snapshot nessuna coorte ha copertura di grafo sulla propria finestra, e
  `members` non contiene chi era già uscito al momento del backfill. Se invece
  compare una retention del 100% con `is_computable = true` su coorti vecchie,
  la migration `0007` non è stata applicata o il codice è vecchio: fermarsi;
- `stability_jaccard` a `NULL` finché non esistono **due** snapshot consecutivi
  confrontabili — stessi parametri del grafo *e* stessa ampiezza di finestra. Il
  primo snapshot copre ~3 giorni e i successivi 7, quindi il confronto tra
  quei due non è disponibile per costruzione: comparirà dal terzo snapshot in
  poi, quando ci saranno due finestre settimanali di fila. Il motivo è in
  `details.stability_unavailable`;
- righe solo per il layer `voice`: sugli altri il traffico non basta a produrre
  archi.

Un valore che **non** deve mai comparire: uno zero al posto di una cella
soppressa. Se una riga con `is_suppressed = true` porta numeri invece di
`NULL`, il trigger della migration non è attivo.

Verifica dopo la prima esecuzione:

```sql
SELECT layer, removal_fraction, n_effective, is_significant, is_suppressed
FROM metric_robustness ORDER BY layer, removal_fraction;

SELECT stats -> 'durations_ms' FROM metric_runs ORDER BY created_at DESC LIMIT 1;
```

Le durate per metrica vanno confrontate **di esecuzione in esecuzione**: sono
l'unico modo per vedere arrivare il problema di budget descritto in
`modello-metriche.md` §11.1 — il baseline è fino a ~400 esecuzioni di Leiden per
snapshot su 1 vCPU condiviso con l'heartbeat del gateway Discord — invece di
scoprirlo da un OOM. Sopra 500 nodi le ripetizioni si riducono da sole, e la
riga lo dichiara in `details.baseline_degraded`.

## Cadenza settimanale (cron)

Perché settimanale e perché lunedì: con `--window-days 7` e un'esecuzione ogni
sette giorni le finestre si affiancano **senza sovrapporsi né lasciare buchi**,
ed è la condizione in cui `stability_jaccard` misura la ricomposizione delle
community invece della sovrapposizione delle finestre. Con gli snapshot a
quattro giorni di distanza la stabilità risulta gonfiata — e lo dice da sé, in
`snapshot_gaps.cadence_days_median`. Il lunedì allinea le finestre alle settimane
ISO usate dalle coorti; le 04:15 UTC sono l'ora più tranquilla per una community
serale come L'Arco.

Tre file, tutti in `ops/` nel repository — niente da scrivere a mano sulla
droplet:

| File | Destinazione |
|---|---|
| `ops/kindling-weekly.sh` | resta nel repo, eseguito da lì |
| `ops/kindling.cron` | `/etc/cron.d/kindling` |
| `ops/kindling.logrotate` | `/etc/logrotate.d/kindling` |

### Installazione

```bash
install -m 644 /root/kindling/ops/kindling.cron /etc/cron.d/kindling && install -m 644 /root/kindling/ops/kindling.logrotate /etc/logrotate.d/kindling
```

Il bit di esecuzione dello script è salvato in git (`100755`), quindi arriva già
eseguibile con il `git pull`. Se un checkout l'avesse perso:
`chmod +x /root/kindling/ops/kindling-weekly.sh`.

**Lo script non ha percorsi da aggiornare**: ricava da sé la directory del
progetto risalendo da dove il file si trova, quindi funziona ovunque il repo sia
clonato. `KINDLING_PROJECT_DIR` resta come override, ed è quello che permette di
provarlo senza modificarlo.

#### Due modi in cui `/etc/cron.d` sbaglia in silenzio

Questo file, a differenza di un `crontab -e`, non protesta: se è malformato cron
scarta la riga e non lo dice a nessuno. Due trappole, entrambe già viste:

1. **Il campo utente è obbligatorio.** In `/etc/cron.d` le colonne sono **sei**:
   `m h dom mon dow utente comando`. Un file copiato da un crontab personale ha
   cinque colonne, cron interpreta il primo pezzo del comando come nome utente,
   la riga viene scartata, e l'unica traccia è una riga in `/var/log/syslog` che
   nessuno sta guardando.
2. **Il percorso del comando dipende da dove è clonato il repo.** Lì l'assoluto
   serve per forza — cron non ha una directory di lavoro utile — e su questa
   droplet è `/root/kindling`. È l'unica riga da tenere allineata se il repo si
   sposta, perché lo script la propria directory se la ricava da solo.

Quindi, **dopo ogni copia**, si guarda cosa è finito nel file:

```bash
cat /etc/cron.d/kindling
```

Sei colonne sulla riga eseguita, e il percorso deve esistere:

```bash
ls -l "$(awk '$1 !~ /^#/ && NF >= 7 {print $NF}' /etc/cron.d/kindling)"
```

### Verifica: eseguire a mano una volta, non aspettare lunedì

```bash
/root/kindling/ops/kindling-weekly.sh; echo "exit=$?"
```

**È sicuro**: rieseguire il job sullo stesso `as_of` riscrive lo snapshot invece
di duplicarlo, e lo stesso vale per le metriche. Serve a verificare che lo
script giri nell'ambiente giusto — che `docker` sia dove lo cerca, che il `.env`
venga letto, che il log sia scrivibile — prima che lo faccia cron, dove un
errore non lo vede nessuno.

Poi il lunedì successivo:

```bash
tail -40 /var/log/kindling/job.log
```

```sql
SELECT id, as_of FROM graph_snapshots ORDER BY id DESC LIMIT 3;
```

Gli `as_of` devono essere a sette giorni esatti di distanza.

### Come accorgersi che il cron ha smesso di funzionare

È la domanda che i runbook non pongono mai: **un job schedulato che non parte
più non produce nessun errore, produce solo assenza**, e l'assenza non arriva in
nessuna casella di posta. Il segnale però c'è già, ed è nei dati.

`details.snapshot_gaps` di ogni riga di coorte riporta `cadence_days_median` e
`max_gap_days`, calcolati sulla spaziatura degli `as_of`:

```sql
SELECT cohort_start, details -> 'snapshot_gaps'
FROM metric_cohorts
WHERE snapshot_id = (SELECT max(snapshot_id) FROM metric_runs)
LIMIT 1;
```

Se `max_gap_days` è il doppio della mediana, **una settimana è saltata**. È una
lettura diretta e non richiede nessuna soglia configurata: la mediana dice qual
è la cadenza vera, il massimo dice se qualcosa l'ha interrotta. Con cadenza
settimanale ci si aspetta `cadence_days_median` ≈ 7 e `max_gap_days` ≈ 7; un 14
è un'esecuzione persa.

Controllo più diretto, se si ha una connessione al database a portata:

```sql
SELECT max(as_of), now() - max(as_of) AS eta FROM graph_snapshots;
```

Oltre gli otto giorni, l'ultima esecuzione è saltata.

### Vedere arrivare il problema di CPU invece di scoprirlo dall'OOM

`metric_runs.stats` porta le **durate per metrica**. Con il cron diventano una
serie storica, ed è l'unico modo per accorgersi che il costo cresce mentre
cresce il grafo — il baseline è fino a ~400 esecuzioni di Leiden per snapshot su
1 vCPU condiviso con l'heartbeat del bot (`modello-metriche.md` §11.1):

```sql
SELECT as_of, stats -> 'durations_ms' FROM metric_runs ORDER BY as_of DESC LIMIT 8;
```

Va letta come tendenza, non come valore assoluto. Se `communities_ms` raddoppia
di settimana in settimana, il momento di alzare `baseline_downgrade_nodes` (o di
guardare i segnali di resize) è quello — non quando l'OOM killer decide da solo
quale processo chiudere.

### Note sullo script

- **`flock`**: se un'esecuzione è ancora in corso la successiva non parte e
  scrive `SKIP` nel log. Su 1 vCPU due job sovrapposti sono il modo più diretto
  di rubare l'heartbeat del gateway al bot.
- **`docker compose run -T`**: senza `-T` il comando alloca un terminale e prova
  a leggere da stdin; lanciato in background il kernel ferma il processo con
  SIGTTIN (`[1]+ Stopped`). Un processo fermo **continua a tenere il lock**,
  quindi l'esecuzione successiva salta con una riga `SKIP` che non spiega
  niente. È la stessa forma di `docker compose exec -T` usata sopra per le
  migration.
- **Codici di uscita**, che è quello che un monitoraggio legge:

  | Codice | Significato |
  |---|---|
  | `0` | snapshot e metriche calcolati |
  | `99` | **saltato**: un'altra esecuzione era in corso, non è stato calcolato niente |
  | altro | snapshot o metriche falliti (il log dice quale) |

  `99` non viene tradotto in `0` di proposito: una settimana saltata non deve
  somigliare dall'esterno a una andata bene, altrimenti il `MAILTO` di cron o
  un controllo sull'ultimo codice di uscita leggerebbero "tutto bene" su una
  settimana in cui il job non ha prodotto nulla. Vale qui lo stesso principio
  della sezione precedente — un job che non parte produce assenza e non errore
  — con la differenza che questa assenza un modo di farsi notare ce l'ha.
- **`nice -n 10`**: come previsto da `architettura.md`, sezione Hosting. Il
  limite di memoria è già nel compose (`mem_limit: 400m`), `nice` copre la CPU.
- **`snapshot` prima, `metrics` solo se il primo è riuscito**: metriche calcolate
  su uno snapshot fallito a metà sono peggio di metriche assenti — sono numeri
  pubblicabili ricavati da un grafo incompleto, e a valle nessuno li distingue
  da quelli buoni. Nel log compare `ABORT metrics non eseguito`.
- Nessuna credenziale nello script né nel crontab: `DATABASE_URL` vive nel
  `.env` della droplet, che `docker compose` legge da sé.

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
