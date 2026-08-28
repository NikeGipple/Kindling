# Kindling — istruzioni per Claude Code

Community Intelligence Platform per la community italiana di Guild Wars 2
"L'Arco del Leone". Prima di toccare qualunque cosa relativa a hosting,
Docker, rete o storage, leggi `architettura/stack-tecnologico-mvp.md`: è la
decisione di architettura corrente, motivata, e ha priorità su qualunque
default "standard" che useresti altrimenti (es. pubblicare una porta Postgres
per comodità di debug locale).

## Regole non negoziabili (violarle ha già causato un incidente reale)

Queste regole vengono dalla sezione "Requisiti di hardening di rete" di
`architettura/stack-tecnologico-mvp.md` e non sono discrezionali:

- **Mai `ports: - "5432:5432"` (o qualunque forma equivalente a
  `0.0.0.0:5432:5432`) su Postgres in nessun `docker-compose.yml`.** Quella
  forma pubblica la porta su tutte le interfacce di rete, raggiungibile da
  internet — è la causa dell'incidente HeroesAscent, vedi sotto. **Non è
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

Su un altro progetto dello stesso team (HeroesAscent) un container MySQL è
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

## Checklist prima di chiudere un task che tocca Docker/rete/segreti

1. `git diff` sui file toccati: il diff riflette solo l'intento del task?
2. Postgres (o altri servizi stateful) non è raggiungibile da IP esterni —
   niente `ports:` senza IP esplicito, o con `0.0.0.0`. Un `ports:` limitato
   a `127.0.0.1:...` va bene, anzi serve per il tunnel SSH.
3. Nessuna password/token/secret hardcoded in un file che verrà committato.
4. Il contenuto coincide con quanto descritto in
   `architettura/stack-tecnologico-mvp.md` per quella parte di stack? In
   caso di dubbio o di conflitto, segnalarlo esplicitamente invece di
   procedere silenziosamente con un'assunzione diversa.
5. Se la modifica riguarda una porta o un binding di rete già usato da un
   workflow documentato altrove (es. il tunnel SSH in README), verificare
   che quel workflow continui a funzionare con la nuova configurazione,
   non solo che la regola di sicurezza sia rispettata sulla carta.
