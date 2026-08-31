# Architettura — Kindling

*Documento di riferimento unico per le decisioni architetturali. Aggiornato al 31 agosto 2026.*

## Assunzioni di partenza

- Team: 2-4 persone, competenze miste.
- Hosting: cloud, budget contenuto (pochi €/mese). Il team gestisce già infrastruttura su DigitalOcean per un altro progetto (HeroesAscent) e preferisce un solo provider invece di spezzare lo stack su tre servizi diversi.
- Scala: L'Arco del Leone, ~1.000-10.000 membri, decine di migliaia di messaggi/mese → il grafo sociale risultante è nell'ordine di migliaia di nodi e (nel tempo) centinaia di migliaia di archi. Una scala piccola per gli standard dei graph database.
- "Future-proof" = capace di crescere verso (a) altre community/server Discord, (b) altre fonti dati oltre Discord, (c) senza dipendere da tecnologie a rischio abbandono.
- Percorso di sviluppo: raccolta dati continua fin da subito; calcolo del grafo/metriche e ciclo di feedback con il community manager si sviluppano su un orizzonte di mesi, non di settimane.
- Obiettivo immediato: una **vertical slice completa su L'Arco del Leone** — ingestion → calcolo → API → dashboard — funzionante end-to-end sulla droplet già in uso, non un ambiente locale da promuovere in produzione più avanti. Il cambio di macchina è legato alla crescita reale (altre community, grafo live), non a una fase del piano (vedi sezione Hosting).

## Principio guida

A questa scala, il collo di bottiglia dell'MVP non è la potenza di calcolo del graph engine: è arrivare in fretta a metriche di salute sociale corrette e a un ciclo di feedback con il community manager. Per questo la scelta è **Postgres come backbone unico + calcolo del grafo in Python a batch**, non un graph database dedicato fin da subito. È una scelta deliberatamente in contro-tendenza rispetto all'istinto "SNA quindi graph DB" (vedi sezione dedicata più sotto).

## Principio di progettazione: nessun profilo individuale esposto agli amministratori delle community

La domanda guida del progetto (vedi descrizione del Project) è: *"Le condizioni che stiamo creando stanno favorendo la nascita, il rafforzamento e il mantenimento di relazioni spontanee tra i membri della community?"* — una domanda **di sistema**, sulle condizioni che la community crea, non un giudizio sul singolo membro. Kindling deve restare uno strumento per rispondere a quella domanda, non uno strumento di sorveglianza individuale. Scelta esplicita e non negoziabile:

- Gli amministratori **non vedono mai** un output che identifica un singolo membro per il suo ruolo relazionale: niente "questo utente è il bridge principale", niente punteggio di centralità nominale, niente lista di membri isolati/periferici con nome. Ogni metrica esposta resta a livello aggregato — community, sotto-gruppo, canale, coorte temporale (es. "i membri che fanno almeno N connessioni nei primi 5 giorni restano di più") — mai a livello di persona nominata.
- Le grandezze calcolate per singolo nodo (centralità, ruolo di bridge/broker, velocità di integrazione del singolo membro) restano un **passaggio di calcolo interno**, necessario per costruire le metriche aggregate — non sono mai, di per sé, un prodotto da mostrare a un amministratore.
- Questo vincolo vale per API, dashboard (Streamlit e, in futuro, Next.js/Sigma.js) e qualunque altro layer di presentazione: va rispettato in fase di design degli endpoint e delle viste, non solo verificato a posteriori.
- Motivazione duplice: (a) è coerente con l'obiettivo dichiarato del progetto — capire le condizioni sistemiche, non profilare le persone; (b) evita di dare agli amministratori uno strumento che possa essere usato per creare pregiudizio verso singoli membri sulla base di quanto sono socialmente centrali o periferici nella rete.
- Il catalogo concreto delle metriche aggregate derivate da questo principio è in `metriche-aggregate-admin.md`.

## Stack e componenti

### 1. Ingestion — bot Discord (discord.py)

Il bot cattura eventi grezzi (messaggi, reply, reazioni, thread, voice join/leave, eventi/RSVP) e li scrive, senza mai modificarli, in una tabella append-only (`raw_events`, payload JSONB + colonne indicizzate su tipo, autore, canale, guild, timestamp, id del messaggio referenziato). Questo log immutabile è la fonte di verità: si possono ricalcolare metriche nuove in futuro senza dover re-ingerire nulla. Il payload dei messaggi non contiene mai il testo: solo `content_length`, `has_attachments`, `mention_ids`, `channel_name`.

Oltre agli eventi di interazione, il bot raccoglie anche i **membri** della guild (member list, via `Server Members Intent`): dati anagrafici minimi del membro (id Discord, data di join, ruoli, eventuale stato di uscita/`left_at`) e gli aggiornamenti che li riguardano (join, leave, cambio ruolo). Questi dati alimentano la tabella dimensionale `members` (`author_id` PK, `guild_id`, `joined_at`, `left_at` nullable, `forgotten_at` nullable) e sono un prerequisito per metriche come la velocità di integrazione dei nuovi membri e il tasso di retention/abbandono — senza il dato di join/leave non è calcolabile "quanto tempo impiega un nuovo membro a costruire la prima connessione" né "quanti membri restano dopo N settimane". Come per gli eventi, anche gli snapshot/aggiornamenti sui membri vengono scritti in modo da preservare lo storico, non un semplice overwrite dello stato corrente. Per l'MVP, un rientro dopo un'uscita sovrascrive `joined_at`/`left_at` (nessuno storico multi-rientro) — scelta esplicita, da rivedere se in futuro serve tracciare rientri multipli.

Nota di compliance: i Developer Terms of Service di Discord impongono cifratura at-rest, uso dei dati coerente con una privacy policy dichiarata, e cancellazione tempestiva su richiesta dell'utente o quando il dato non serve più allo scopo dichiarato. Da qui la colonna/flag di "diritto all'oblio" per autore su `raw_events` e sulla tabella `members`.

### 2. Storage — Postgres self-hosted su DigitalOcean (Docker)

`raw_events` (grezzo) + tabelle dimensionali (membri, canali, guild) + tabelle di snapshot del grafo calcolato (nodi/archi/metriche per finestra temporale, versionate).

Scelta: Postgres self-hosted, non un servizio managed a canone gratuito — un log eventi che deve crescere in continuo per mesi eccede in fretta i limiti di storage di un free tier, e comunque un free tier copre tipicamente solo lo storage, non il compute sempre acceso richiesto dal bot. Dato che il team gestisce già DigitalOcean, ha più senso avere Postgres in un container Docker sulla stessa droplet del bot/API — un solo provider, un solo bill, meno pezzi mobili da tenere sincronizzati. Il costo di questa scelta è che i backup e gli upgrade di Postgres diventano responsabilità del team invece che del managed service: da mitigare con backup automatici della droplet + `pg_dump` periodico verso object storage (DO Spaces), impostati fin dal giorno 1 perché il log grezzo non è ricostruibile a posteriori.

**Hardening di rete — non opzionale, fin dal primo commit del compose file.** Su HeroesAscent (progetto separato, stesso team) si è verificato un incidente su MySQL in Docker: porta del database pubblicata direttamente su internet (`3306:3306`) e credenziali in chiaro in un repository GitHub pubblico — la firma tipica di attacchi automatizzati di massa contro database esposti (scanner che trovano la porta aperta, si autenticano con la password trapelata, cancellano i dati e lasciano una cartella di riscatto). La lezione riguarda la configurazione di rete e la gestione dei segreti, non la scelta del container runtime — con Docker configurato correttamente si ottiene anzi più isolamento di rete di default (un container non è raggiungibile dall'esterno a meno di pubblicarne esplicitamente la porta):

- **La regola è "mai su tutte le interfacce" (mai `0.0.0.0`), non "mai una sezione `ports:`".** In produzione, dove Postgres serve solo agli altri container (bot, API) sulla rete Docker interna, nessuna sezione `ports:` è la scelta giusta. Ma se serve anche l'accesso da tunnel SSH per l'esplorazione dei dati dal laptop (psql, notebook, Gephi), va pubblicata solo sul loopback dell'host: `ports: - "127.0.0.1:5432:5432"`. Non raggiungibile dall'esterno in nessun caso, ma raggiungibile da un tunnel SSH aperto sulla stessa macchina. Omettere `ports:` del tutto in quel caso rompe il tunnel: senza nessuna porta pubblicata, nemmeno `localhost:5432` sulla droplet stessa risponde.
- **Credenziali mai nel repository**: file `.env` escluso da `.gitignore` fin dal primo commit, prima ancora di scrivere il compose file.
- **DigitalOcean Cloud Firewall** attivo fin dal provisioning della droplet: in ingresso solo SSH e le porte strettamente necessarie per l'API/dashboard pubblici (443/80 dietro reverse proxy, da aprire quando quei servizi vengono esposti — vedi sezione Hosting), nient'altro raggiungibile dall'esterno.
- **fail2ban** su SSH, dato che quella porta resta comunque esposta.

### 3. Calcolo del grafo — Python, batch/incrementale

Un job schedulato (cron, non serve un orchestratore vero e proprio a questa scala) ricostruisce il grafo in memoria dagli eventi grezzi e calcola le metriche SNA: centralità (degree, eigenvector, betweenness), reciprocità, clustering coefficient, community detection con **Leiden** (via `leidenalg`/`python-igraph` — successore di Louvain, garantisce community ben connesse), identificazione di bridge/broker, velocità di integrazione dei nuovi membri, decadimento del legame nel tempo. Come da principio di progettazione sopra, queste grandezze per-nodo restano interne al calcolo: le tabelle di snapshot pensate per essere lette dall'API/dashboard espongono aggregati, non il valore per membro nominato.

Per il grafo in evoluzione nel tempo, l'approccio più pragmatico a questa scala è a **snapshot periodici** (es. settimanali) confrontati tra loro, non un motore di temporal graph streaming — è anche l'approccio più comune in letteratura SNA.

Libreria: **NetworkX** per l'esplorazione e la fase di ricerca (più leggibile, più algoritmi, community enorme, ideale quando si sta ancora definendo quali metriche contano). Per il **job schedulato che gira sulla droplet**, invece, **python-igraph** fin da subito: tiene nodi e archi in array compatti e consuma circa un ordine di grandezza in meno di memoria rispetto alle strutture dict-of-dicts di NetworkX — differenza irrilevante su un laptop, non su una macchina da 1 GB condivisa con bot e database. `leidenalg` gira comunque su igraph, quindi la dipendenza è già in casa. `graph-tool` è più veloce di entrambi ma ha dipendenze C++ pesanti da installare e mantenere — poco adatto a un team di 2-4 persone che deve restare agile.

I risultati calcolati vengono riscritti in Postgres: l'API non carica mai il graph engine nel path di serving, legge solo tabelle già pronte.

### 4. API — FastAPI

Stesso linguaggio del layer di calcolo (Python), riduce la superficie tecnologica per un team piccolo. Espone gli endpoint che il frontend consuma.

### 5. Frontend/dashboard

Con un team piccolo e ancora in fase di scoperta delle metriche giuste, conviene partire leggeri: un dashboard interno in **Streamlit** (o simile) per iterare rapidamente con il community manager su quali visualizzazioni/metriche sono davvero utili, prima di investire in un frontend web vero. Quando le metriche sono validate, si passa a **Next.js** con **Sigma.js** per la visualizzazione del grafo (WebGL, regge bene migliaia di nodi, mantenuto attivamente) — Cytoscape.js resta un'opzione migliore per esplorazioni ravvicinate su sotto-grafi piccoli (es. il vicinato di un singolo membro) grazie alle interazioni più ricche. Anche qui vale il principio di non esposizione di profili individuali: la visualizzazione del grafo va pensata in forma anonimizzata/aggregata (es. nodi non etichettati con l'username, focus su pattern strutturali) piuttosto che come strumento per navigare la rete membro-per-membro.

Streamlit sta comodamente sulla macchina attuale (vedi i consumi misurati nella sezione Hosting): non è un componente da rimandare per motivi di memoria. Se in futuro il margine si stringe, la leva più economica prima di cambiare macchina è generare il dashboard come pagine statiche rigenerate dal job — le metriche sono snapshot settimanali, quindi un server Python sempre vivo non è indispensabile per mostrarle.

### 6. Hosting — una sola droplet, la taglia attuale

Droplet dedicata `kindling-app-01` (FRA1), in un progetto DO separato "Kindling", non condivisa con HeroesAscent, per isolare risorse e falle di sicurezza tra i due progetti.

**Taglia: 1 GB RAM / 1 vCPU / 25 GB SSD — 6 $/mese. Ci gira l'intera vertical slice.** Ingestion, job di calcolo, API e dashboard vivono tutti su questa macchina: non c'è una fase di sviluppo locale obbligatoria da attraversare, e non c'è un resize pianificato. Il community manager può usare il dashboard senza dipendere dal laptop di chi sviluppa fin dal primo giorno in cui il dashboard esiste.

**Consumi reali misurati sulla droplet in esercizio (31/08/2026, bot e Postgres attivi):**

```
              total   used   free  buff/cache  available
Mem:          956Mi  322Mi   77Mi       607Mi      634Mi
Swap:         2.0Gi   69Mi  1.9Gi

kindling-bot-1        25 MiB
kindling-postgres-1   36 MiB
```

I 322 MB usati includono sistema operativo e Docker daemon; i due container insieme pesano ~61 MB. Restano ~630 MB realmente disponibili (i 607 MB di `buff/cache` sono page cache riutilizzabile — ed è anche il modo principale in cui Postgres tiene caldi i dati a questa scala). Le stime iniziali del progetto (Postgres 300-600 MB, bot 150 MB) erano sovrastimate di un ordine di grandezza rispetto al carico reale: **è questa misurazione, non una previsione, la base della scelta di restare su 6 $/mese.**

Su quei ~630 MB entrano senza forzature i pezzi mancanti: FastAPI/uvicorn con 1 worker (~80-120 MB), reverse proxy Caddy per HTTPS e autenticazione (~30 MB), dashboard Streamlit (~200-300 MB), e il job di calcolo, che non è always-on — gira qualche minuto a settimana con un picco di qualche centinaio di MB.

**Accorgimenti per stare tranquilli su una macchina piccola:**

- `mem_limit` per servizio nel `docker-compose.yml`: se qualcosa deve morire per esaurimento memoria deve morire il job, non il bot o Postgres. Senza limiti espliciti l'OOM killer del kernel sceglie il processo con più RSS, che è probabilmente il database.
- Job schedulato in orario di bassa attività e con priorità CPU ridotta (`nice`): su 1 vCPU il calcolo non deve rubare tempo all'heartbeat del gateway Discord del bot.
- `python-igraph` nel job (vedi sezione 3), non NetworkX.
- Swapfile 2 GB già attivo: rete di sicurezza per i picchi del job, non memoria di lavoro — se il dashboard finisce stabilmente in swap è un segnale di resize, non una configurazione accettabile.
- `restart: unless-stopped` su bot e Postgres.

**Esposizione pubblica.** Quando API e dashboard diventano raggiungibili da fuori, il Cloud Firewall apre 443/80 verso un reverse proxy (Caddy, certificati TLS automatici), mai la porta del database. Il dashboard mostra dati di community e va protetto da autenticazione dal primo giorno in cui è online — basic auth come minimo, OAuth Discord se serve distinguere più persone. **Questo, non il costo della macchina, è il vero lavoro aggiuntivo rispetto alla configurazione di oggi**, ed è anche il punto in cui vale la lezione HeroesAscent: si apre una porta nuova su internet, quindi la si apre con TLS e autenticazione già configurate, non "per provare".

**Quando cambiare macchina.** Non è una fase pianificata né una scadenza: si cambia quando i numeri lo impongono. Segnali concreti:

- il job va in OOM, o il suo tempo di esecuzione cresce fuori scala su 1 vCPU;
- `available` scende stabilmente sotto ~150 MB con tutti i servizi attivi, o il dashboard risponde lento perché sta in swap;
- **si aggiungono altre community** (più bot, più eventi, più grafi da calcolare) — è lo scenario di crescita esplicito del progetto;
- **serve un grafo live/interattivo** invece degli snapshot settimanali: cambia il profilo di carico, il serving diventa sensibile alla latenza e il calcolo non è più confinabile a una finestra notturna.

Quando succede, il resize è questione di pochi minuti di downtime dal pannello DO e — punto importante — **è reversibile se si usa l'opzione "CPU and RAM only"**, che lascia il disco a 25 GB: si sale a 2 GB (12 $/mese) e si può tornare giù. È il resize del **disco** a essere irreversibile (DigitalOcean non rimpicciolisce un disco), ed è anche quello che non serve: a decine di migliaia di eventi al mese il log grezzo cresce nell'ordine di qualche centinaio di MB l'anno, 25 GB bastano per anni. Con più community o un dashboard più pesante la taglia naturale successiva è 4 GB / 2 vCPU (24 $/mese), o lo split su più droplet.

**Sviluppo e deploy.** L'ambiente di riferimento è la droplet: deploy con `git pull` + `docker compose up -d --build` (passi operativi in `runbook-droplet.md`). Docker in locale resta utile per iterare senza toccare la produzione, ma non è un passaggio obbligato e non esiste una "fase locale" da promuovere: `api` e `job` si aggiungono allo stesso `docker-compose.yml` che oggi definisce `postgres` e `bot`, non a un compose separato. Per l'esplorazione dei dati dal laptop (psql, notebook, Gephi) resta il tunnel SSH verso Postgres (`ssh -L 5432:localhost:5432 utente@ip-droplet`).

## Terminologia e modello dati

- **`guild_id`** indica sempre e solo il server Discord, mai la gilda in-game di Guild Wars 2. Sono due entità diverse, con fonti dati diverse: `guild_id` viene dagli eventi Discord (già presente su `raw_events` e nello schema); la gilda in-game GW2 è una fonte dati separata, non ancora integrata — se in futuro verrà integrata, andrà chiamata esplicitamente `gw2_guild_id` per evitare ambiguità. Non usare mai "guild" da solo per riferirsi alla gilda GW2 nel codice o nello schema: nel contesto Discord/discord.py "guild" è già un termine riservato con un significato preciso (= server).
- **`author_id`** è l'ID Discord grezzo (BIGINT), usato direttamente per collegare eventi e membri — non è né anonimizzato né pseudonimizzato in senso tecnico. L'informativa privacy (`legal/informativa-privacy.html`) riflette questo: è accurata nel dire che il contenuto testuale del messaggio non viene mai raccolto, ma non deve promettere l'anonimizzazione dell'ID, che il sistema non implementa. L'hosting è su server nell'Unione Europea (informativa punto 5), senza nominare il fornitore.

## Perché non un graph database dedicato (per ora)

Le opzioni valutate hanno tutte un "ma" che le rende premature come cuore del sistema a questa scala:

- **Neo4j AuraDB Free**: 200k nodi / 400k archi, un solo database, e viene cancellato dopo 30 giorni di inattività — rischioso come fonte di verità, oltre a introdurre Cypher come secondo linguaggio di query per un team piccolo.
- **Memgraph / ArangoDB / FalkorDB**: licenze source-available (BSL/SSPL) con "uso in produzione gratuito" limitato a scenari interni — da rileggere con attenzione se in futuro si offre la piattaforma a terzi; inoltre richiedono di riscrivere le query in AQL o varianti di Cypher.
- **Apache AGE** (estensione graph su Postgres): interessante concettualmente perché resterebbe dentro Postgres, ma è un progetto Apache Incubator mantenuto da volontari con release poco frequenti — non la base su cui scommettere la parte "future-proof" del progetto oggi, meglio tenerlo d'occhio come opzione di migrazione.

Nessuna di queste è "sbagliata" in assoluto: se in futuro il grafo cresce di ordini di grandezza o servono query di traversal interattive in tempo reale (non solo metriche calcolate a batch), una di queste — o un export verso Neo4j/Memgraph per esplorazione interattiva — diventa sensata. Ma introdurla ora significherebbe aggiungere un secondo sistema di storage, un secondo linguaggio di query e un secondo bill, senza un beneficio misurabile alla scala attuale.

## Strumenti open source da riusare, non reinventare

- **CHAOSS / Augur / GrimoireLab**: framework maturo di metriche di salute delle community open source (dimensioni Diversity & Inclusion, Value, Growth-Maturity-Decline, Risk) — utile soprattutto come riferimento concettuale/tassonomico per definire le metriche di Kindling evitando le vanity metrics. La parte infrastrutturale (Perceval, GrimoireELK) non supporta Discord nativamente e va comunque costruito un ingestor ad hoc, ma il fatto che supportino già Slack/Discourse conferma che il pattern "ingestor per fonte → schema canonico → metriche" è quello giusto da replicare.
- **Leiden (`leidenalg`)**: algoritmo di community detection, da usare così com'è.
- **Gephi**: non per la produzione, ma utile come strumento manuale di esplorazione visiva degli snapshot del grafo durante la fase di ricerca.
- Non risulta esistere un tool open source pronto all'uso per "social graph su Discord orientato alla salute delle relazioni" — i bot Discord esistenti (Statbot, Sesh e simili) fanno essenzialmente activity analytics/leaderboard, cioè esattamente le vanity metrics che il progetto vuole evitare. Questo pezzo va costruito.

## Cosa rende questa architettura future-proof

1. **`guild_id` come chiave di partizione fin dal giorno 1**, anche con una sola community attiva — rende l'estensione multi-community un dettaglio di query, non una riscrittura.
2. **Confine netto tra ingestion source-specific e schema canonico degli eventi** (attore, target, tipo di interazione, peso, timestamp, contesto). Aggiungere una fonte dati diversa da Discord in futuro significa scrivere un nuovo ingestor che produce lo stesso schema canonico, senza toccare il motore di calcolo del grafo.
3. **Postgres come unico sistema stateful**: è probabilmente la tecnologia con il miglior track record di longevità dell'intero stack, ed è comunque il punto di partenza naturale se in futuro si aggiunge Apache AGE, pgvector per embedding testuali, o un export verso un graph database dedicato.
4. **Log grezzo immutabile**: qualunque errore o evoluzione nelle metriche si corregge ricalcolando dagli eventi, non serve mai re-ingerire.
5. **Servizi containerizzati fin dal giorno 1** (Docker Compose: `postgres` e `bot` sono già definiti nel file; `api` e `job` si aggiungono allo stesso file quando vengono sviluppati, non a un compose separato): spostarsi su una macchina più grande, o in futuro su più macchine/servizi managed, è un cambio di quali servizi sono avviati dove, non una riscrittura.

## Riepilogo

| Layer       | Scelta MVP                                                                                                                                                                     | Perché                                                                                                                                      | Evoluzione futura                                                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ingestion   | discord.py (bot), always-on sulla droplet — cattura eventi di interazione **e** membri (member list, join/leave, ruoli)                                                        | Libreria matura, community grande; i dati sui membri sono necessari per metriche di retention/integrazione                                  | Nuovi ingestor per altre fonti sullo stesso schema canonico                                                                                           |
| Storage     | Postgres self-hosted (Docker su droplet, porta mai pubblicata su internet, accesso dev via tunnel SSH)                                                                         | Un solo provider, backup sotto controllo del team                                                                                           | Managed DO Database o Apache AGE/pgvector se il carico operativo lo giustifica                                                                        |
| Calcolo SNA | Python: igraph/leidenalg nel job schedulato, NetworkX per l'esplorazione — sulla droplet                                                                                       | Memoria compatta dove conta (1 GB condiviso con bot e DB), leggibilità dove serve (ricerca)                                                 | graph-tool o motore dedicato se la scala lo richiede                                                                                                  |
| API         | FastAPI, sulla droplet dietro reverse proxy                                                                                                                                    | Stesso linguaggio del calcolo, team piccolo                                                                                                 | —                                                                                                                                                     |
| Dashboard   | Streamlit → Next.js + Sigma.js, sulla droplet con TLS e autenticazione                                                                                                         | Iterare veloce sulle metriche prima di investire in UI                                                                                      | Cytoscape.js per esplorazioni su sotto-grafi; pagine statiche rigenerate dal job se serve alleggerire                                                 |
| Hosting     | Droplet DigitalOcean dedicata (progetto DO separato "Kindling"), 1 GB / 1 vCPU / 25 GB SSD — 6 $/mese, con l'intera vertical slice a bordo: Postgres, bot, job, API, dashboard | Consumi misurati (~61 MB per bot+Postgres, ~630 MB disponibili): la taglia attuale basta per L'Arco e non serve un ambiente locale separato | Resize "CPU and RAM only" a 2 GB (12 $/mese, reversibile, disco invariato) con più community o grafo live; 4 GB / 2 vCPU o split su più droplet oltre |

## Rischi aperti da monitorare

- Discord App Review e privacy policy pubblica se il bot cresce/richiede permessi privilegiati sui contenuti dei messaggi o sull'elenco membri (`Server Members Intent`).
- **Discord Developer Policy, regola 16 ("Handle Data with Care")**: vieta di usare i dati ottenuti dalle API per "profile Discord users, their identities, or their relationships with other users", oltre che per discriminare in base a caratteristiche protette o per finalità di eleggibilità (impiego, casa, assicurazioni). Verificato leggendo il testo ufficiale della policy (support-dev.discord.com) e contattando il supporto sviluppatori Discord: la lettura ricevuta è che il divieto riguarda l'uso di dati profilati per creare pregiudizio/discriminazione, non l'analisi relazionale aggregata in sé svolta per finalità di community health. Mitigato anche a monte dal principio di progettazione "nessun profilo individuale esposto agli amministratori" (vedi sopra), scelto dal team indipendentemente dalla regola 16. Resta buona norma tenere per iscritto lo scambio col supporto Discord (email/screenshot) prima di un'eventuale sottomissione ad App Review con permessi privilegiati (es. Message Content, Server Members Intent).
- Se in futuro si vuole offrire la piattaforma a terzi (non solo uso interno), rivedere le licenze source-available di qualsiasi componente non-Postgres introdotto in seguito (Memgraph/ArangoDB/FalkorDB).
- Apache AGE resta un'opzione di migrazione da rivalutare periodicamente (release cadence, PG18 support), non una dipendenza da introdurre oggi.
- **Postgres self-hosted**: backup e restore sono responsabilità del team (nessun managed service dietro le quinte) — backup automatici della droplet e/o `pg_dump` schedulato verso DO Spaces fin dal giorno 1, dato che il log eventi grezzo (e i dati sui membri) non è recuperabile se perso.
- **Esposizione di rete e segreti**: rischio di primo livello, non secondario, alla luce dell'incidente HeroesAscent — checklist di provisioning (Cloud Firewall, `.env` mai in git, nessuna porta DB pubblicata su tutte le interfacce) da rispettare fin dalla prima droplet, non da aggiungere dopo un incidente. Con API e dashboard esposti pubblicamente sulla stessa macchina la superficie cresce: TLS e autenticazione sul dashboard fanno parte della checklist, non sono un affinamento successivo.
- **Margine di memoria su 1 GB**: la scelta di restare sulla taglia da 6 $/mese poggia su una misurazione fatta con i soli bot e Postgres attivi (~630 MB disponibili). Va riverificata con `free -h` e `docker stats` dopo l'aggiunta di API, dashboard e job, e i segnali di resize elencati nella sezione Hosting vanno controllati periodicamente, non una volta sola.
