# Kindling — istruzioni per Claude Code

Community Intelligence Platform. Prima di toccare qualunque cosa relativa a hosting,
Docker, rete o storage, leggi `docs/architettura/architettura.md`: è la
decisione di architettura corrente, motivata, e ha priorità su qualunque
default "standard" che useresti altrimenti (es. pubblicare una porta Postgres
per comodità di debug locale).

Prima di toccare qualunque cosa relativa al **grafo sociale** — ricostruzione
delle sessioni vocali, archi, pesi, decadimento, snapshot — leggi
`docs/architettura/modello-grafo.md`: è la specifica del modello, ed è il
risultato di decisioni di ricerca prese esplicitamente. Vale la stessa regola
del doc di architettura, più una: se il codice diverge dalla specifica, è il
codice a essere sbagliato, oppure è una cosa da discutere **prima**. Non si
modifica `modello-grafo.md` per farlo combaciare con l'implementazione. Se un
punto risulta ambiguo o impossibile da implementare come scritto, chiedere
invece di scegliere: una deviazione silenziosa vanifica la decisione che c'è
dietro.

## Regole non negoziabili (violarle ha già causato un incidente reale)

Queste regole vengono dalla sezione "Requisiti di hardening di rete" di
`docs/architettura/architettura.md` e non sono discrezionali:

- **Mai `ports: - "5432:5432"` (o qualunque forma equivalente a
  `0.0.0.0:5432:5432`) su Postgres in nessun `docker-compose.yml`.** Quella
  forma pubblica la porta su tutte le interfacce di rete, raggiungibile da
  internet — è la causa dell'incidente descritto sotto. **Non è
  invece vietato** `ports: - "127.0.0.1:5432:5432"` (porta pubblicata solo
  sul loopback dell'host): non è raggiungibile dall'esterno in nessun caso,
  ma è quello che rende possibile collegarsi da un tunnel SSH aperto sulla
  stessa macchina (vedi errore #3 sotto — senza questo binding il tunnel SSH
  documentato in README non ha nulla a cui collegarsi). La regola è "mai su
  tutte le interfacce", non "mai una sezione `ports:`".
- **Nessuna credenziale reale o riutilizzabile hardcoded in un file
  committato** (docker-compose.yml, Dockerfile, ecc.), nemmeno per l'ambiente
  di sviluppo locale. Le credenziali vivono in `.env` (mai in git) o vengono
  generate/lette da variabile d'ambiente anche nel compose di sviluppo.
- Se stai scrivendo o modificando `docker-compose.yml`, `Dockerfile`, o
  qualunque file di configurazione di rete/deploy, prima di considerare il
  lavoro finito rileggi la sezione "Requisiti di hardening di rete" del doc
  di architettura e verifica riga per riga che il file non la contraddica.

### Perché queste regole esistono (non toglierle "perché sembrano eccessive per il dev locale")

Su un altro progetto dello stesso team un container MySQL è
stato compromesso e i dati cancellati da un attacco ransomware automatizzato.
La causa diagnosticata non è stata Docker in sé, ma la combinazione di due
scelte enterprise-costose-da-ignorare-mai:

1. la porta del database pubblicata direttamente sull'host (`3306:3306`);
2. credenziali in chiaro in un repository GitHub pubblico.

Kindling ha scritto queste regole in architettura proprio per non ripetere
quell'incidente. Un `docker-compose.yml` che pubblica `5432:5432` con
`POSTGRES_PASSWORD` hardcoded ricrea, riga per riga, la stessa combinazione —
anche se il compose è "solo per sviluppo locale": una volta committato, il
file (e la password) sono comunque pubblici se il repo lo è, e la porta
resta aperta su qualunque macchina esegua quel compose fuori da una rete
fidata.

## Errori già commessi in questo progetto (non ripeterli)

### 1. Porta Postgres pubblicata nel docker-compose.yml

Una prima versione di `docker-compose.yml` conteneva:

```yaml
services:
  postgres:
    ...
    environment:
      POSTGRES_PASSWORD: kindling
    ports:
      - "5432:5432"
```

Questo viola direttamente la regola sopra, scritta esplicitamente nel doc di
architettura il giorno prima con tanto di esempio letterale da evitare
("nessun `ports: - "5432:5432"` nel `docker-compose.yml`"). Il fix corretto:
niente sezione `ports` sul servizio `postgres` — resta raggiungibile solo
dal servizio `bot` (e in futuro `api`/`job`) sulla rete Docker interna del
compose. Se serve ispezionare il DB da fuori durante lo sviluppo, la via è
un tunnel SSH verso la droplet (o, in locale, `docker exec` / connessione
diretta al container), non un port mapping permanente nel file committato.

### 2. Line ending riscritti senza motivo (CRLF introdotto su file invariati)

Nella stessa sessione, `LICENSE` e `.gitignore` — file già presenti nel
repository con line ending LF — sono stati riscritti con line ending CRLF
pur avendo contenuto testuale identico. Risultato: `git diff` mostra ogni
riga di entrambi i file come rimossa e riaggiunta, un diff enorme e
fuorviante per un cambiamento che, nei contenuti, non esiste.

Regola pratica per evitarlo: quando modifichi un file esistente, non
riscriverne il contenuto intero se non è necessario, e preserva il suo line
ending originale invece di lasciare che lo strumento/editor lo normalizzi
implicitamente. Prima di considerare un task finito, esegui `git diff` sui
file toccati e verifica che il diff rifletta solo il cambiamento intenzionale
— un diff "tutte le righe cambiate" su un file che dovevi lasciare quasi
intatto è un segnale che qualcosa è andato storto nel modo in cui è stato
scritto, non nel contenuto.

### 3. Correzione del punto 1 troppo aggressiva: rimossa la sezione `ports`
per intero invece di limitarla al loopback

Il fix del punto 1 (fatto da Claude, non da Claude Code) ha rimosso la
sezione `ports:` del tutto, invece di limitarla a
`"127.0.0.1:5432:5432"`. Conseguenza scoperta solo più tardi, durante il
setup del tunnel SSH per l'analisi locale (vedi README): senza **nessuna**
porta pubblicata, nemmeno sul loopback, `localhost:5432` sulla droplet
stessa non risponde — quindi anche un tunnel SSH (`ssh -L
5432:localhost:5432 ...`), che si appoggia proprio su `localhost` della
droplet per raggiungere Postgres, fallisce con `connection refused`, pur
essendo il tunnel SSH in sé perfettamente funzionante. Ore di debug (fail2ban,
formato della chiave, `AllowTcpForwarding`, tunnel nativo vs. tunnel di un
client GUI) prima di arrivare alla causa reale, che non aveva nulla a che
fare con nessuna di quelle piste.

Lezione: quando si applica una regola di hardening, verificare cosa il resto
del sistema si aspetta che quella porta faccia (qui: il workflow di tunnel
SSH già documentato in README) prima di chiuderla del tutto — "più
restrittivo possibile" non è la stessa cosa di "restrittivo quanto serve
davvero". La versione corretta pubblica la porta solo sul loopback
dell'host, non la rimuove.

### 4. Riferimento a hosting sbagliato nel Dockerfile (Fly.io/Railway invece di DigitalOcean)

Il commento in testa a `Dockerfile` citava "l'hosting di riferimento
(Fly.io/Railway)", in conflitto con `docs/architettura/architettura.md`,
che descrive una droplet DigitalOcean dedicata (`kindling-app-01`) come
scelta di hosting — non un PaaS come Fly.io o Railway. Corretto il 28 agosto
2026: da ora in poi l'unico hosting di riferimento per Kindling è
**DigitalOcean** (droplet dedicata, piano di deploy a due fasi). Qualunque
riferimento futuro a Fly.io, Railway o altri PaaS in file di questo repo è
un refuso da correggere, non un'opzione valida — vedi
`docs/architettura/architettura.md` per il razionale completo.

### 5. `Dockerfile` non aggiornato quando è stata aggiunta una nuova cartella di primo livello (`api/`)

Il `Dockerfile` copiava esplicitamente `bot/` e `job/`, ma nessuno lo ha
aggiornato quando `api/` è diventata una terza componente del progetto.
`requirements.txt` conteneva già `fastapi`/`uvicorn`/`pydantic` (le
dipendenze si installano da lì), il che ha mascherato il problema fino al
primo `docker compose up -d --build api` in produzione (06/09/2026):
`ModuleNotFoundError: No module named 'api'`, container in crash-loop.

Lezione: avere le dipendenze giuste in `requirements.txt` non dice niente
sul fatto che il **codice sorgente** della componente sia nell'immagine — sono
due liste indipendenti (`RUN pip install` da un lato, `COPY <cartella>/
./<cartella>/` dall'altro) e vanno tenute allineate a mano. Quando si aggiunge
una cartella di primo livello con codice Python destinato a girare in un
container, il `Dockerfile` va aggiornato nello stesso commit, non quando si
arriva finalmente a fare il primo deploy di quella componente — altrimenti il
gap resta invisibile per settimane, come qui.

### 6. Password generata con `openssl rand -base64` incollata dentro una connection string URL

La password del ruolo `kindling_api` (`docs/architettura/runbook-droplet.md`,
sezione "Avviare l'API") è stata generata con `openssl rand -base64 24` e
messa direttamente in `API_DATABASE_URL=postgresql://kindling_api:<password>@postgres:5432/kindling`.
L'alfabeto base64 include `/`, `+` e `=` — caratteri che dentro una URL hanno
un significato proprio (`/` in particolare separa il path). Una password che
ne contiene uno rompe il parsing di `asyncpg` **dentro** l'host/porta, con un
errore fuorviante (`ValueError: invalid literal for int()`) che non menziona
mai la password — molto più lento da diagnosticare di un banale "password
errata". Scoperto e risolto il 06/09/2026, rigenerando la password con
`openssl rand -hex 24` (alfabeto `0-9a-f`, nessun carattere riservato di URL).

Regola pratica: **qualunque credenziale destinata a finire dentro una URL
`postgresql://...` (o schema simile) si genera con `openssl rand -hex`, mai
con `-base64`.** `-base64` resta adatto a segreti che non vengono mai
interpolati in una URL (es. `KINDLING_PSEUDONYM_SALT`, che il codice legge
come stringa e basta). Nota collaterale non ancora risolta: `POSTGRES_PASSWORD`
(usata nello stesso modo dentro `DATABASE_URL` per `bot`/`job`) è stata
generata con `-base64` il 28/08/2026 e funziona solo perché non contiene per
caso nessuno di quei caratteri — è un rischio latente, non un bug attivo; da
sistemare (rigenerare in hex) alla prossima occasione in cui si tocca comunque
quella credenziale, non con un cambio dedicato su una credenziale che oggi
funziona.

### 7. Classe di difetto: un meccanismo che sembra funzionare e non dice che non sta funzionando

Non è un errore singolo, è un **genere** da cercare attivamente. Un controllo
che continua a passare mentre ha smesso di controllare qualcosa — o un
meccanismo che sembra applicarsi a tutto e in realtà esclude qualcosa in
silenzio — è peggio di un'assenza dichiarata: quella si nota, questo dà
conferma. Quattro casi già visti in questo repo, diversi nella forma e
identici nella sostanza:

- **`api/db.py`, ordinamento senza tiebreaker.** `ORDER BY as_of DESC` senza
  `snapshot_id DESC` lascia l'ordine indefinito quando due snapshot pareggiano
  su `as_of` (caso reale: stessa `--as-of`, ampiezze di finestra diverse), e sei
  endpoint possono rispondere su snapshot diversi senza nessun errore. Un test
  scritto sul *testo* della query sarebbe passato lo stesso, purché sbagliato
  in modo uniforme in tutti e sei i punti; il test che lo prende
  (`tests/test_api_db.py`) riproduce il pareggio e guarda cosa esce.
- **`runbook-droplet.md`, controllo del file di cron.** Il comando
  `ls -l "$(awk '... {print $NF}' /etc/cron.d/kindling)"` serviva a verificare
  che il percorso dello script esistesse. Aggiungendo una pipe alla riga di
  cron, `$NF` è diventato `kindling-cron` (l'argomento di `logger`) invece del
  percorso: il comando avrebbe continuato a girare, verificando una cosa che a
  nessuno interessa. Corretto in `$7`, cioè il campo che il formato di
  `/etc/cron.d` definisce come "comando" (06/09/2026).
- **`docker compose build` che salta `job` senza dirlo.** Il servizio `job` ha
  `profiles: ["tools"]`, e Compose esclude i servizi fuori dal profilo attivo
  sia da `build` sia da `up`, con lo stesso silenzio: nessun errore, nessuna
  riga di log, l'immagine vecchia resta. Capitato davvero il 07/09/2026:
  `docker compose build` seguito da un `docker compose run --rm job ...` ha
  eseguito codice di tre giorni prima, scritto metriche, uscendo `0`. Il
  sintomo (`previous_gap_days` rimasto `NULL`) sembrava un bug del codice
  nuovo. Fix: `docker compose --profile tools build job` esplicito, sempre
  accanto a `docker compose build`, con la verifica
  `docker images --format '{{.Repository}}\t{{.CreatedAt}}' | grep kindling`
  (le tre date devono essere ravvicinate) — vedi `runbook-droplet.md`.
- **`KINDLING_CODE_VERSION` mai scritta sulla droplet.** La riga è in
  `.env.example` da settimane, con un commento che dice "sulla droplet la
  imposta il deploy" — ma nessun passo della procedura di deploy la impostava
  davvero, perché a differenza di ogni altra variabile del file il suo valore
  giusto *cambia* a ogni deploy, e non esiste un comando "una tantum" a cui
  appoggiarsi come per le altre. Risultato: `code_version` è stato `NULL` su
  ogni riga di `graph_snapshots` e `metric_runs` da sempre, scoperto solo
  cercando "quale codice ha scritto questa riga" — la stessa domanda che la
  regola del ricalcolo di `modello-grafo.md` §5.1 chiede di fare prima di
  ricalcolare con codice diverso, e che quella regola presuppone risolvibile.
  Fix: passo esplicito nella procedura di deploy (`runbook-droplet.md`, passo
  4) che la riscrive da `git rev-parse --short HEAD` a ogni esecuzione, non
  una tantum.

Come si cercano: ogni volta che si cambia la **forma** di qualcosa che un
controllo ispeziona — l'ordine di una query, il numero di campi di una riga, il
formato di un log, il nome di una colonna — si rilegge il controllo e ci si
chiede *cosa fallirebbe adesso*. Se la risposta è "niente", il controllo è da
riscrivere insieme alla modifica, non dopo. Vale in modo particolare per le
verifiche scritte nei runbook: nessuno le esegue abbastanza spesso da vederle
degradare, e quando le si esegue è di solito nel momento peggiore per
accorgersene.

Vale anche al contrario, ed è la parte nuova con `profiles` e con
`KINDLING_CODE_VERSION`: non solo un controllo che smette di controllare, ma un
**meccanismo che sembra coprire tutto** (un `build` senza argomenti, un
commento che promette "lo imposta il deploy") **e in realtà esclude un caso
particolare senza segnalarlo**. Il test per riconoscerlo è lo stesso: quando si
introduce un servizio con un comportamento diverso dagli altri (un `profiles`,
un valore che cambia invece di restare fisso), ci si chiede se ogni comando
"per tutti" scritto prima di quel momento lo include ancora davvero — e se un
commento promette che "qualcosa lo fa", si verifica che quel qualcosa esista
per davvero come passo eseguibile, non come intenzione.

## Checklist prima di chiudere un task che tocca Docker/rete/segreti

1. `git diff` sui file toccati: il diff riflette solo l'intento del task?
2. Postgres (o altri servizi stateful) non è raggiungibile da IP esterni —
   niente `ports:` senza IP esplicito, o con `0.0.0.0`. Un `ports:` limitato
   a `127.0.0.1:...` va bene, anzi serve per il tunnel SSH.
3. Nessuna password/token/secret hardcoded in un file che verrà committato.
4. Il contenuto coincide con quanto descritto in
   `docs/architettura/architettura.md` per quella parte di stack? In
   caso di dubbio o di conflitto, segnalarlo esplicitamente invece di
   procedere silenziosamente con un'assunzione diversa.
5. Se la modifica riguarda una porta o un binding di rete già usato da un
   workflow documentato altrove (es. il tunnel SSH in README), verificare
   che quel workflow continui a funzionare con la nuova configurazione,
   non solo che la regola di sicurezza sia rispettata sulla carta.

## Tabella `members`

Implementata (`migrations/0002_members.sql`, `bot/db.py`,
`bot/cogs/admin.py`) — non è più un gap. Anagrafica dello stato corrente di
ogni membro per server, per calcolare la retention e confrontarla con eventi
di onboarding (es. numero di connessioni fatte nei primi giorni):

- Chiave primaria composita `(guild_id, author_id)` — non solo `author_id`
  come nella bozza iniziale: coerente con `raw_events`, e permette allo
  stesso utente Discord di essere membro di più server Kindling senza che
  un'iscrizione sovrascriva l'altra.
- `joined_at` (NOT NULL), `left_at` (nullable — NULL = membro tuttora
  presente), `forgotten_at` (nullable, stessa semantica GDPR di
  `raw_events`).
- `left_at` non è un dettaglio opzionale: senza uscite tracciate non si può
  mai verificare se l'onboarding rapido (es. Millington, 5 connessioni
  distinte) correla davvero con la retention — è il termine di paragone
  altrimenti mancante.
- Due nuovi tipi di evento coerenti con la filosofia raw-event-come-fonte-
  di-verità: `member_join` / `member_remove` (`bot/event_types.py`, da
  `on_member_join` / `on_member_remove` di discord.py).
- Edge case rientro dopo uscita: per l'MVP si sovrascrivono `joined_at`/
  `left_at` (nessuno storico multi-rientro). Scelta esplicita, da rivedere
  se in futuro serve tracciare rientri multipli.
- **Backfill automatico e interno** (`bot/backfill.py`): popola `members` con
  i membri già presenti su un server al momento dell'arrivo del bot — senza,
  il loro `joined_at` non viene mai osservato via evento. Gira da solo
  all'`on_guild_join` **e all'avvio** per ogni guild non ancora backfillata
  (`guilds.backfilled_at`): `on_guild_join` da solo non basta, perché il
  flusso OAuth che aggiunge l'applicazione funziona anche a bot spento e
  Discord non ritrasmette gli eventi del gateway.
- **Non è più un comando**, e non deve tornare a esserlo. `!backfill_members`
  è stato rimosso insieme al cog `admin.py`. Il motivo non è l'igiene:
  `is_survivors_only` (metriche di coorte) distingue i membri backfillati
  dagli osservati confrontando il loro `joined_at` con `guilds.first_seen_at`,
  e un backfill rilanciato su una guild già osservata riscriverebbe il
  `joined_at` di membri osservati, rompendo quella distinzione **senza nessun
  errore**. Una regola che dipende da "nessuno lo rilancia" è più debole di un
  comando che non esiste.
- **Il backfill INSERISCE, non aggiorna** (`db.insert_member_join_if_absent`,
  `ON CONFLICT DO NOTHING`). È il secondo livello, indipendente dal primo:
  anche se il backfill venisse eseguito per qualche via, non potrebbe toccare
  una riga esistente. `db.upsert_member_join` resta al servizio di
  `on_member_join`, dove sovrascrivere è corretto (rientro dopo uscita): sono
  due chiamanti con esigenze opposte e non devono condividere la funzione solo
  perché toccano la stessa tabella.

## Colonna `referenced_channel_id` su `raw_events`

Implementata (`migrations/0003_referenced_channel_id.sql`, `bot/db.py`,
`bot/cogs/ingestion.py`) — non è più un gap. `on_message` valorizza
`referenced_channel_id` da `message.reference.channel_id` quando il messaggio
è una reply: Discord permette reply cross-canale, e prima il codice assumeva
implicitamente che il messaggio target fosse nello stesso canale della reply.

Due precisazioni emerse implementandola:

- **Il job di calcolo non ne ha bisogno.** Risolve l'autore di un messaggio
  target per `message_id` (tabella `message_authors`), e gli id dei messaggi
  Discord sono globalmente univoci: il canale non serve al join. La colonna
  serve al **backfill lato bot** che recupera via API gli autori mancanti, dove
  `channel.fetch_message` vuole il canale giusto. Quel backfill è ancora da
  scrivere ed è dichiarato opzionale in `modello-grafo.md` §6.
- **Le reply già ingerite restano a `NULL`**, e `NULL` va trattato a valle come
  "stesso canale della reply" — cioè il comportamento precedente. Nessuna
  regressione, solo un dato più preciso da qui in avanti. Il riempimento una
  tantum delle righe vecchie (rifacendo `channel.fetch_message` sulla reply
  stessa, non sul target) resta possibile ma non è stato fatto.

## Terminologia: `guild_id`

`guild_id` indica **sempre e solo il server Discord**, mai la gilda in-game
di Guild Wars 2. Sono due entità diverse, con fonti dati diverse:

- `guild_id` → server Discord (fonte: eventi Discord, già presente su
  `raw_events` e nello schema).
- Gilda in-game GW2 → fonte dati separata, non ancora integrata. Se in futuro
  verrà integrata, andrà chiamata esplicitamente `gw2_guild_id` per evitare
  ambiguità con `guild_id`.

Non usare mai "guild" da solo per riferirsi alla gilda GW2 nel codice o nello
schema: nel contesto Discord/discord.py "guild" è già un termine riservato
con un significato preciso (= server).

## Migrazioni: `schema_migrations` e autoregistrazione

`schema_migrations (filename, applied_at)` (migration `0012`) è il ledger di
quali migration sono applicate su un dato database. Prima non esisteva:
"quali migration sono applicate sulla droplet" era un fatto custodito FUORI
dal database — scritto a mano altrove — e poteva divergere dalla realtà senza
che niente lo segnalasse. È la stessa forma dei difetti in §7: un meccanismo
che sembra tenere traccia e non lo fa in modo verificabile.

**Regola non negoziabile da qui in avanti: ogni nuova migration finisce
registrando il proprio nome file**, ultima riga del file:

```sql
INSERT INTO schema_migrations (filename) VALUES ('0013_qualcosa.sql')
    ON CONFLICT (filename) DO NOTHING;
```

Non un passo separato per l'operatore, e non facoltativo. Un ledger che
qualcuno deve ricordarsi di aggiornare a mano è peggio di nessun ledger: non
diverge mai in modo rumoroso, diverge zitto e continua a sembrare affidabile.
La riga sta nel file della migration stessa, quindi non può mancare se il
file è stato applicato — **una migration senza questa riga è incompleta**,
allo stesso titolo di una migration senza `IF NOT EXISTS`.

`ops/kindling-deploy.sh` legge questa tabella per fermarsi prima del deploy se
qualcosa non è stato applicato (vedi lo script e `runbook-droplet.md`). Un
falso "tutto applicato" per una migration dimenticata di registrarsi
vanificherebbe esattamente il controllo che il ledger esiste per fare.

## Follow-up aperti (trovati, non risolti di proposito)

Gap reali, verificati, ma volutamente non risolti nel branch in cui sono stati
trovati perché il fix cambia un default o un comportamento e merita la propria
voce di spec — non un innesto silenzioso su un cambiamento che parlava d'altro.

### `run_metrics` sceglie lo snapshot per `as_of` massimo, non per "appena scritto"

Trovato e verificato su Postgres reale durante il deploy del fix di
`modello-grafo.md` §5.1 (ancoraggio di `as_of` alla settimana ISO, 07/09/2026).

`ops/kindling-weekly.sh` lancia `snapshot` e poi `metrics` **senza
`--snapshot-id`**: `metrics` sceglie con `db.fetch_snapshot`
(`ORDER BY as_of DESC, id DESC`). Finché `as_of` veniva da `now()`, "il più
recente per `as_of`" e "quello appena scritto" erano sempre la stessa riga per
costruzione. Con `as_of` ancorato al lunedì non lo sono più: uno snapshot con
`as_of` maggiore, scritto prima (codice vecchio, o un `--as-of` esplicito nel
passato), resta il più recente per `as_of` anche dopo che un lancio successivo
ne scrive uno logicamente più nuovo ma con `as_of` minore. `metrics` calcola
allora sullo snapshot sbagliato, `exit=0`, nessun errore nel log.

Riprodotto: `scritto id=1 as_of=2026-09-07 04:15` (cron, codice pre-fix) poi
`scritto id=2 as_of=2026-09-07 00:00` (lancio a mano, codice col fix) →
`fetch_snapshot()` torna `id=1`.

**Non è un bug introdotto oggi in astratto**: prima del fix "appena scritto" e
"più recente per `as_of`" coincidevano sempre, quindi la distinzione non
esisteva. È il fix di `as_of` a separare le due nozioni, e a scoprire che
`fetch_snapshot` dipendeva dalla loro coincidenza senza dichiararlo.

**Fix NON fatto qui**: disaccoppiare le due nozioni — per esempio scegliendo
per `created_at` (che l'`ON CONFLICT` di `write_snapshot` aggiorna a ogni
riscrittura) invece che per `as_of`, o accettando esplicitamente che `metrics`
senza `--snapshot-id` richieda una garanzia diversa. Cambia il comportamento di
default di un comando usato in produzione: va deciso e scritto in spec, non
scelto a margine di un altro task.

**Mitigazione temporanea, documentata in `runbook-droplet.md`** (sezione
"Verifica: eseguire a mano una volta"): dal deploy del fix di `as_of` fino al
prossimo cron regolare (lunedì 14/09/2026 04:15 UTC, che scrive un `as_of`
sicuramente più alto di qualunque snapshot di transizione) non lanciare
`ops/kindling-weekly.sh` a mano; se serve verificare, lanciare `snapshot` e poi
`metrics --snapshot-id <id>` separatamente. La finestra si chiude da sola.
