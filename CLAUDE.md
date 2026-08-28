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

- **Mai `ports: - "5432:5432"` (o equivalente) su Postgres in nessun
  `docker-compose.yml`.** Postgres deve essere raggiungibile solo dagli altri
  container sulla rete Docker interna, o dagli sviluppatori via tunnel SSH.
  Se un task richiede di far girare Postgres in Docker, per sviluppo locale
  compreso, questa regola vale comunque: niente port mapping verso l'host.
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

## Checklist prima di chiudere un task che tocca Docker/rete/segreti

1. `git diff` sui file toccati: il diff riflette solo l'intento del task?
2. Nessun `ports:` che esponga Postgres (o altri servizi stateful) all'host.
3. Nessuna password/token/secret hardcoded in un file che verrà committato.
4. Il contenuto coincide con quanto descritto in
   `architettura/stack-tecnologico-mvp.md` per quella parte di stack? In
   caso di dubbio o di conflitto, segnalarlo esplicitamente invece di
   procedere silenziosamente con un'assunzione diversa.
