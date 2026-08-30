# Lezioni da errori di implementazione — docker-compose / hardening di rete

*Nota di post-mortem interna, riferita alla sessione di sviluppo del bot di
ingestion (28 agosto 2026). Estende `stack-tecnologico-mvp.md`, non lo
sostituisce.*

## 1. Porta Postgres pubblicata su tutte le interfacce

Durante la prima implementazione del bot di ingestion, Claude Code ha
prodotto un `docker-compose.yml` che pubblicava la porta di Postgres
sull'host (`ports: - "5432:5432"`, equivalente a `0.0.0.0:5432:5432`) con
`POSTGRES_PASSWORD` hardcoded in chiaro — esattamente la combinazione di
errori che, sul progetto HeroesAscent dello stesso team, aveva già causato
un incidente ransomware su MySQL (porta esposta + credenziali trapelate).

## 2. Correzione iniziale troppo aggressiva: porta rimossa del tutto

Il fix applicato ha rimosso la sezione `ports:` per intero, non solo il
binding su tutte le interfacce. Conseguenza scoperta solo più tardi, durante
il setup del tunnel SSH per l'analisi locale (workflow già documentato in
README, parte del piano Fase 1): senza **nessuna** porta pubblicata, nemmeno
sul loopback, `localhost:5432` sulla droplet stessa non risponde a nulla —
quindi il tunnel SSH (che si appoggia su `localhost` della droplet per
raggiungere Postgres) falliva con `connection refused`, pur essendo il
tunnel SSH in sé perfettamente funzionante. Ore di debug (fail2ban, formato
della chiave SSH, `AllowTcpForwarding`, tunnel nativo vs. tunnel del client
GUI) prima di arrivare alla causa reale.

**Fix corretto**: `ports: - "127.0.0.1:5432:5432"` — pubblica la porta solo
sul loopback dell'host. Non raggiungibile dall'esterno in nessun caso
(diverso da `0.0.0.0`, la causa dell'incidente HeroesAscent), ma raggiungibile
da un tunnel SSH aperto sulla stessa macchina. La regola giusta è "mai su
tutte le interfacce", non "mai una sezione `ports:`".

## 3. Line ending riscritti senza motivo

`LICENSE` e `.gitignore` sono stati riscritti convertendo i line ending da
LF a CRLF senza alcun cambiamento di contenuto, producendo diff Git enormi e
fuorvianti.

## Cosa resta fatto

Un file `CLAUDE.md` con le regole esplicite (incluso il distinguo
0.0.0.0 vs 127.0.0.1), una checklist pre-commit, e il resoconto di questo
errore specifico è nel repository, in modo che le sessioni future di Claude
Code su questo progetto lo rispettino di default. Vedi anche
`stack-tecnologico-mvp.md` per il razionale completo delle scelte di
hardening di rete e per il resoconto dell'incidente HeroesAscent che le ha
motivate.
