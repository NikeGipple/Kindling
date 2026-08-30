# Stack tecnologico MVP — Kindling

*Decisione di architettura — 27 agosto 2026 (aggiornata più volte lo stesso giorno: hosting/storage self-hosted su DigitalOcean; taglia droplet iniziale; diagnosi corretta dell'incidente HeroesAscent e requisiti di hardening di rete; piano di deploy a due fasi — dev locale via tunnel, poi tutto su server; aggiornata il 30/08/2026 per includere la raccolta dei membri nell'ingestion)*

## Assunzioni di partenza

- Team: 2-4 persone, competenze miste.
- Hosting: cloud, budget contenuto (pochi €/mese). Il team gestisce già infrastruttura su DigitalOcean per un altro progetto (HeroesAscent) e preferisce un solo provider invece di spezzare lo stack su tre servizi diversi.
- Scala: L'Arco del Leone, ~1.000-10.000 membri, decine di migliaia di messaggi/mese → il grafo sociale risultante è nell'ordine di migliaia di nodi e (nel tempo) centinaia di migliaia di archi. Una scala piccola per gli standard dei graph database.
- "Future-proof" = capace di crescere verso (a) altre community/server Discord, (b) altre fonti dati oltre Discord, (c) senza dipendere da tecnologie a rischio abbandono.
- Percorso di sviluppo: raccolta dati continua fin da subito, ma calcolo del grafo/metriche e ciclo di feedback con il community manager si sviluppano su un orizzonte di mesi, non di settimane — questo condiziona quando è necessario avere API e job di calcolo sempre attivi su un server (vedi sezione Hosting).

## Principio guida

A questa scala, il collo di bottiglia dell'MVP non è la potenza di calcolo del graph engine: è arrivare in fretta a metriche di salute sociale corrette e a un ciclo di feedback con il community manager. Per questo la proposta è **Postgres come backbone unico + calcolo del grafo in Python a batch**, non un graph database dedicato fin da subito. È una scelta deliberatamente in contro-tendenza rispetto all'istinto "SNA quindi graph DB", e vale la pena spiegare perché regge meglio anche sul fronte future-proof.

## Principio di progettazione: nessun profilo individuale esposto agli amministratori

*Aggiunto il 30/08/2026, a valle della verifica sulla regola 16 della Discord Developer Policy (vedi "Rischi aperti") — ma è una scelta di design a monte, non solo una mitigazione legale.*

La domanda guida del progetto (vedi descrizione del Project) è: *"Le condizioni che stiamo creando stanno favorendo la nascita, il rafforzamento e il mantenimento di relazioni spontanee tra i membri della community?"* — una domanda **di sistema**, sulle condizioni che la community crea, non un giudizio sul singolo membro. Kindling deve restare uno strumento per rispondere a quella domanda, non uno strumento di sorveglianza individuale. Per questo, scelta esplicita e non negoziabile:

- Gli amministratori **non vedono mai** un output che identifica un singolo membro per il suo ruolo relazionale: niente "questo utente è il bridge principale", niente punteggio di centralità nominale, niente lista di membri isolati/periferici con nome. Ogni metrica esposta resta a livello aggregato — community, sotto-gruppo, canale, coorte temporale (es. "i membri che fanno almeno N connessioni nei primi 5 giorni restano di più") — mai a livello di persona nominata.
- Le grandezze calcolate per singolo nodo (centralità, ruolo di bridge/broker, velocità di integrazione del singolo membro) restano un **passaggio di calcolo interno**, necessario per costruire le metriche aggregate — non sono mai, di per sé, un prodotto da mostrare a un amministratore.
- Questo vincolo vale per API, dashboard (Streamlit e, in futuro, Next.js/Sigma.js) e qualunque altro layer di presentazione: va rispettato in fase di design degli endpoint e delle viste, non solo verificato a posteriori.
- Motivazione duplice: (a) è coerente con l'obiettivo dichiarato del progetto — capire le condizioni sistemiche, non profilare le persone; (b) evita di dare agli amministratori uno strumento che possa essere usato per creare pregiudizio verso singoli membri sulla base di quanto sono socialmente centrali o periferici nella rete.

## Architettura proposta

**1. Ingestion — bot Discord (discord.py)**
Il bot cattura eventi grezzi (messaggi, reply, reazioni, thread, voice join/leave, eventi/RSVP) e li scrive, senza mai modificarli, in una tabella append-only (`raw_events`, payload JSONB + colonne indicizzate su tipo, autore, canale, guild, timestamp, id del messaggio referenziato). Questo log immutabile è la fonte di verità: si possono ricalcolare metriche nuove in futuro senza dover re-ingerire nulla.

Oltre agli eventi di interazione, il bot raccoglie anche i **membri** della guild (member list, via `Server Members Intent`): dati anagrafici minimi del membro (id Discord, data di join, ruoli, eventuale stato di uscita/`left_at`) e gli aggiornamenti che li riguardano (join, leave, cambio ruolo). Questi dati alimentano la tabella dimensionale `membri` (vedi sezione Storage) e sono un prerequisito per metriche come la velocità di integrazione dei nuovi membri e il tasso di retention/abbandono — senza il dato di join/leave non è calcolabile "quanto tempo impiega un nuovo membro a costruire la prima connessione" né "quanti membri restano dopo N settimane". Come per gli eventi, anche gli snapshot/aggiornamenti sui membri vengono scritti in modo da preservare lo storico (non un semplice overwrite dello stato corrente), così da poter ricostruire l'evoluzione della membership nel tempo, non solo la sua foto attuale.

Nota di compliance: i Developer Terms of Service di Discord impongono cifratura at-rest, uso dei dati coerente con una privacy policy dichiarata, e cancellazione tempestiva su richiesta dell'utente o quando il dato non serve più allo scopo dichiarato. Conviene progettare da subito una colonna/flag di "diritto all'oblio" per autore su `raw_events` (e sulla tabella membri), costa pochissimo ora e molto di più da aggiungere dopo.

**2. Storage — Postgres self-hosted su DigitalOcean (Docker)**
`raw_events` (grezzo) + tabelle dimensionali (membri, canali, guild) + tabelle di snapshot del grafo calcolato (nodi/archi/metriche per finestra temporale, versionate).

Scelta: Postgres self-hosted, non un servizio managed a canone gratuito — un log eventi che deve crescere in continuo per mesi eccede in fretta i limiti di storage di un free tier, e comunque un free tier copre tipicamente solo lo storage, non il compute sempre acceso richiesto dal bot. Dato che il team gestisce già DigitalOcean, ha più senso avere Postgres in un container Docker sulla stessa droplet del bot/API — un solo provider, un solo bill, meno pezzi mobili da tenere sincronizzati. Il costo di questa scelta è che i backup e gli upgrade di Postgres diventano responsabilità del team invece che del managed service: da mitigare con backup automatici della droplet + `pg_dump` periodico verso object storage (DO Spaces), impostati fin dal giorno 1 perché il log grezzo non è ricostruibile a posteriori.

**Requisiti di hardening di rete — non opzionali, fin dal primo commit del compose file.** Su HeroesAscent (progetto separato, stesso team) si è verificato un incidente su MySQL in Docker: comparsa di una cartella `RECOVER_YOUR_DATA`, database reinizializzato, dati persi. La diagnosi corretta — non attribuibile a Docker in sé — è un'esposizione di rete: la porta del database era pubblicata direttamente su internet (`3306:3306`) e le credenziali erano presenti in chiaro in un repository GitHub pubblico. Questa combinazione è la firma tipica di attacchi automatizzati di massa contro database esposti (scanner che trovano la porta aperta, si autenticano con la password trapelata, cancellano i dati e lasciano una cartella "riscatto"), non di una corruzione da arresto non pulito del container. La lezione per Kindling riguarda la configurazione di rete e la gestione dei segreti, non la scelta del container runtime — con Docker configurato correttamente si ottiene anzi più isolamento di rete di default (un container non è raggiungibile dall'esterno a meno di pubblicarne esplicitamente la porta):

- **Postgres non pubblica mai la porta verso l'host/internet** — nessun `ports: - "5432:5432"` nel `docker-compose.yml`. È raggiungibile solo dagli altri container (bot, API) sulla rete Docker interna, oltre che dagli sviluppatori via tunnel SSH (vedi piano di deploy a due fasi sotto).
- **Credenziali mai nel repository**: file `.env` escluso da `.gitignore` fin dal primo commit, prima ancora di scrivere il compose file.
- **DigitalOcean Cloud Firewall** attivo fin dal provisioning della droplet: in ingresso solo SSH e le porte strettamente necessarie per l'API pubblica (tipicamente 443/80 dietro reverse proxy, da attivare quando l'API si sposta sul server — vedi sotto), nient'altro raggiungibile dall'esterno.
- **fail2ban** su SSH, dato che quella porta resta comunque esposta.

**3. Calcolo del grafo — Python, batch/incrementale**
Un job schedulato (cron, non serve un orchestratore vero e proprio a questa scala) ricostruisce il grafo in memoria dagli eventi grezzi e calcola le metriche SNA: centralità (degree, eigenvector, betweenness), reciprocità, clustering coefficient, community detection con **Leiden** (via `leidenalg`/`python-igraph` — successore di Louvain, garantisce community ben connesse), identificazione di bridge/broker, velocità di integrazione dei nuovi membri, decadimento del legame nel tempo. Come da principio di progettazione sopra, queste grandezze per-nodo restano interne al calcolo: le tabelle di snapshot pensate per essere lette dall'API/dashboard espongono aggregati, non il valore per membro nominato.

Per il grafo in evoluzione nel tempo, l'approccio più pragmatico a questa scala è a **snapshot periodici** (es. settimanali) confrontati tra loro, non un motore di temporal graph streaming — è anche l'approccio più comune in letteratura SNA.

Libreria: **NetworkX** per la fase di ricerca (più leggibile, più algoritmi, community enorme, ideale quando si sta ancora definendo quali metriche contano), con eventuale migrazione dei calcoli più pesanti a **python-igraph** se/quando la performance diventa un problema reale. `graph-tool` è più veloce di entrambi ma ha dipendenze C++ pesanti da installare e mantenere — poco adatto a un team di 2-4 persone che deve restare agile.

I risultati calcolati vengono riscritti in Postgres: l'API non carica mai il graph engine nel path di serving, legge solo tabelle già pronte.

**4. API — FastAPI**
Stesso linguaggio del layer di calcolo (Python), riduce la superficie tecnologica per un team piccolo. Espone gli endpoint che il frontend consuma.

**5. Frontend/dashboard**
Con un team piccolo e ancora in fase di scoperta delle metriche giuste, conviene partire leggeri: un dashboard interno in **Streamlit** (o simile) per iterare rapidamente con il community manager su quali visualizzazioni/metriche sono davvero utili, prima di investire in un frontend web vero. Quando le metriche sono validate, si passa a **Next.js** con **Sigma.js** per la visualizzazione del grafo (WebGL, regge bene migliaia di nodi, mantenuto attivamente) — Cytoscape.js resta un'opzione migliore per esplorazioni ravvicinate su sotto-grafi piccoli (es. il vicinato di un singolo membro) grazie alle interazioni più ricche. Anche qui vale il principio di non esposizione di profili individuali: la visualizzazione del grafo va pensata in forma anonimizzata/aggregata (es. nodi non etichettati con l'username, focus su pattern strutturali) piuttosto che come strumento per navigare la rete membro-per-membro.

**6. Hosting — droplet DigitalOcean dedicata, piano di deploy a due fasi**

Una droplet separata da quella di HeroesAscent (progetto DO a parte, "Kindling"), non condivisa, per isolare risorse e falle di sicurezza tra i due progetti.

*Fase 1 — ingestion-only (adesso):* **1 GB RAM / 1 vCPU / 25 GB SSD — 6 $/mese**, con solo Postgres e bot discord.py in Docker (nessuna porta pubblicata), + swapfile 1-2 GB come rete di sicurezza. Sono gli unici due processi always-on necessari in questa fase: il bot deve restare connesso al gateway Discord 24/7 per non perdere eventi (di interazione e di membership), Postgres deve essere sempre raggiungibile per scriverli. Consumo stimato: Postgres ~300-600 MB a regime, bot ~150 MB — comodo dentro 1 GB.

API, job di calcolo del grafo (NetworkX/igraph) ed eventuale dashboard Streamlit di esplorazione **si sviluppano in locale**, containerizzati nello stesso `docker-compose.yml` (che oggi, in Fase 1, definisce solo `postgres` e `bot`: le sezioni `api` e `job` vanno aggiunte allo stesso file quando si inizia a svilupparli, non su un compose separato), collegati al Postgres remoto via tunnel SSH (`ssh -L 5432:localhost:5432 utente@ip-droplet`, stessa modalità di accesso già in uso su HeroesAscent dopo la messa in sicurezza). Usare Docker anche in locale, non processi Python nudi, evita disallineamenti di versione tra ambiente di sviluppo e quello che poi girerà sul server. L'unica differenza tra le due fasi è l'host del database nella variabile d'ambiente (`localhost` via tunnel in locale, nome del servizio `postgres` sulla rete Docker interna una volta sul server).

*Trigger per il passaggio alla Fase 2:* non è una scadenza a calendario, ma una condizione — scatta quando l'API/dashboard deve essere raggiungibile da altri (in primis il community manager) senza dipendere dal laptop di chi sviluppa acceso e con il tunnel aperto, oppure quando il job di calcolo deve girare su uno schedule automatico senza intervento manuale. Con una strategia di raccolta dati pensata su un orizzonte di mesi, questo trigger può arrivare molto più avanti di "poche settimane" — è deliberatamente disaccoppiato dal tempo e legato solo a quando serve un servizio always-on per altri, non solo per chi sviluppa.

*Fase 2 — tutto su server (quando scatta il trigger):* resize verticale della droplet a **2 GB / 1 vCPU / 50 GB SSD — 12 $/mese** dal pannello DO (pochi minuti di downtime, stesso disco, nessuna migrazione dati), poi `docker compose up -d api job` sulla stessa macchina dove Postgres gira già — le immagini sono le stesse già testate in locale, cambia solo l'host del DB nella configurazione. Il Cloud Firewall si apre in quel momento anche sulla porta pubblica dell'API (443/80 dietro reverse proxy). Se in seguito si aggiunge anche il dashboard Streamlit sulla stessa macchina, 4 GB / 2 vCPU / 80 GB — 24 $/mese è la taglia naturale.

Questo piano evita di tenere una droplet da 12 $/mese sottoutilizzata durante la fase di sola raccolta dati e sviluppo: si paga la taglia più piccola finché serve solo ingestion, si passa a quella più grande solo quando c'è un servizio reale da tenere sempre acceso per altri.

## Perché non un graph database dedicato (per ora)

Le opzioni valutate hanno tutte un "ma" che le rende premature come cuore del sistema a questa scala:

- **Neo4j AuraDB Free**: 200k nodi / 400k archi, un solo database, e viene **cancellato dopo 30 giorni di inattività** — rischioso come fonte di verità, oltre a introdurre Cypher come secondo linguaggio di query per un team piccolo.
- **Memgraph / ArangoDB / FalkorDB**: licenze source-available (BSL/SSPL) con "uso in produzione gratuito" limitato a scenari interni — da rileggere con attenzione se in futuro si offre la piattaforma a terzi; inoltre richiedono di riscrivere le query in AQL o varianti di Cypher.
- **Apache AGE** (estensione graph su Postgres): interessante concettualmente perché resterebbe dentro Postgres, ma è un progetto Apache Incubator mantenuto da volontari con release poco frequenti — non la base su cui scommettere la parte "future-proof" del progetto oggi, meglio tenerlo d'occhio come opzione di migrazione.

Nessuna di queste è "sbagliata" in assoluto: se in futuro il grafo cresce di ordini di grandezza o servono query di traversal interattive in tempo reale (non solo metriche calcolate a batch), una di queste — o un export verso Neo4j/Memgraph per esplorazione interattiva — diventa sensata. Ma introdurla ora significherebbe aggiungere un secondo sistema di storage, un secondo linguaggio di query e un secondo bill, senza un beneficio misurabile alla scala attuale.

## Strumenti open source da riusare, non reinventare

- **CHAOSS / Augur / GrimoireLab**: framework maturo di metriche di salute delle community open source (dimensioni Diversity & Inclusion, Value, Growth-Maturity-Decline, Risk) — utile soprattutto come **riferimento concettuale/tassonomico** per definire le metriche di Kindling evitando le vanity metrics. La parte infrastrutturale (Perceval, GrimoireELK) non supporta Discord nativamente e va comunque costruito un ingestor ad hoc, ma il fatto che supportino già Slack/Discourse conferma che il pattern "ingestor per fonte → schema canonico → metriche" è quello giusto da replicare.
- **Leiden (`leidenalg`)**: algoritmo di community detection, da usare così com'è.
- **Gephi**: non per la produzione, ma utile come strumento manuale di esplorazione visiva degli snapshot del grafo durante la fase di ricerca.
- Non risulta esistere un tool open source pronto all'uso per "social graph su Discord orientato alla salute delle relazioni" — i bot Discord esistenti (Statbot, Sesh e simili) fanno essenzialmente activity analytics/leaderboard, cioè esattamente le vanity metrics che il progetto vuole evitare. Questo pezzo va costruito.

## Cosa rende questa architettura davvero future-proof

1. **`guild_id` come chiave di partizione fin dal giorno 1**, anche con una sola community attiva — rende l'estensione multi-community un dettaglio di query, non una riscrittura.
2. **Confine netto tra ingestion source-specific e schema canonico degli eventi** (attore, target, tipo di interazione, peso, timestamp, contesto). Aggiungere una fonte dati diversa da Discord in futuro significa scrivere un nuovo ingestor che produce lo stesso schema canonico, senza toccare il motore di calcolo del grafo.
3. **Postgres come unico sistema stateful**: è probabilmente la tecnologia con il miglior track record di longevità dell'intero stack, ed è comunque il punto di partenza naturale se in futuro si aggiunge Apache AGE, pgvector per embedding testuali, o un export verso un graph database dedicato.
4. **Log grezzo immutabile**: qualunque errore o evoluzione nelle metriche si corregge ricalcolando dagli eventi, non serve mai re-ingerire.
5. **Servizi containerizzati fin dal giorno 1** (Docker Compose: `postgres` e `bot` sono definiti nel file fin da questa Fase 1; `api` e `job`, quando si sviluppano, si aggiungono allo stesso file — non a un compose separato — anche se in Fase 1 girano solo i primi due sulla droplet): il passaggio da locale a server, o in futuro a più macchine/servizi managed, è un cambio di quali servizi sono avviati dove, non una riscrittura.

## Riepilogo

| Layer | Scelta MVP | Perché | Evoluzione futura |
|---|---|---|---|
| Ingestion | discord.py (bot), always-on sulla droplet — cattura eventi di interazione **e** membri (member list, join/leave, ruoli) | Libreria matura, community grande; i dati sui membri sono necessari per metriche di retention/integrazione | Nuovi ingestor per altre fonti sullo stesso schema canonico |
| Storage | Postgres self-hosted (Docker su droplet, porta mai pubblicata su internet, accesso dev via tunnel SSH) | Un solo provider, backup sotto controllo del team | Managed DO Database o Apache AGE/pgvector se il carico operativo lo giustifica |
| Calcolo SNA | Python: NetworkX → igraph/leidenalg — **in locale in Fase 1**, poi sulla droplet | Ricerca veloce, poi performance quando serve; nessun costo server finché lo sviluppo è locale | graph-tool o motore dedicato se la scala lo richiede |
| API | FastAPI — **in locale in Fase 1**, poi sulla droplet | Stesso linguaggio del calcolo, team piccolo | — |
| Dashboard | Streamlit → Next.js + Sigma.js | Iterare veloce sulle metriche prima di investire in UI | Cytoscape.js per esplorazioni su sotto-grafi |
| Hosting | Droplet DigitalOcean dedicata, progetto DO separato "Kindling". **Fase 1: 1 GB/25 GB SSD (6 $/mese)**, solo Postgres+bot, swapfile, Cloud Firewall, nessuna porta DB pubblicata. **Fase 2 (al bisogno di always-on per altri): resize a 2 GB/50 GB (12 $/mese)**, si aggiungono API e job usando le stesse immagini Docker già sviluppate in locale | Si paga la taglia piccola finché serve solo ingestion, si sale solo quando c'è un servizio reale da tenere acceso per altri — nessuna macchina sovradimensionata a prendere polvere durante lo sviluppo | Resize verticale a 4 GB quando si aggiunge anche il dashboard, o split su più droplet/servizi managed |

## Rischi aperti da monitorare

- Discord App Review e privacy policy pubblica se il bot cresce/richiede permessi privilegiati sui contenuti dei messaggi o sull'elenco membri (`Server Members Intent`).
- **Discord Developer Policy, regola 16 ("Handle Data with Care")**: vieta di usare i dati ottenuti dalle API per "profile Discord users, their identities, or their relationships with other users", oltre che per discriminare in base a caratteristiche protette o per finalità di eleggibilità (impiego, casa, assicurazioni). Verificato il 30/08/2026 leggendo il testo ufficiale della policy (support-dev.discord.com). Contattato il supporto sviluppatori Discord lo stesso giorno: la lettura ricevuta è che il divieto riguarda l'uso di dati profilati per creare pregiudizio/discriminazione, non l'analisi relazionale aggregata in sé svolta per finalità di community health. **Mitigato anche a monte** dal principio di progettazione "nessun profilo individuale esposto agli amministratori" (vedi sezione dedicata sopra), che il team ha scelto indipendentemente dalla regola 16 — coerente con l'obiettivo dichiarato del progetto di rispondere a una domanda di sistema, non di profilare le persone. Resta comunque buona norma tenere per iscritto lo scambio col supporto Discord (email/screenshot) prima di un'eventuale sottomissione ad App Review con permessi privilegiati (es. Message Content, Server Members Intent).
- Se in futuro si vuole offrire la piattaforma a terzi (non solo uso interno), rivedere le licenze source-available di qualsiasi componente non-Postgres introdotto in seguito (Memgraph/ArangoDB/FalkorDB).
- Apache AGE resta un'opzione di migrazione da rivalutare periodicamente (release cadence, PG18 support), non una dipendenza da introdurre oggi.
- **Postgres self-hosted**: backup e restore ora sono responsabilità del team (nessun managed service dietro le quinte) — impostare backup automatici della droplet e/o `pg_dump` schedulato verso DO Spaces fin dal giorno 1, dato che il log eventi grezzo (e i dati sui membri) non è recuperabile se perso.
- **Esposizione di rete e segreti**: da trattare come rischio di primo livello, non secondario, alla luce dell'incidente HeroesAscent — checklist di provisioning (Cloud Firewall, `.env` mai in git, nessuna porta DB pubblicata) da rispettare fin dalla prima droplet, non da aggiungere dopo un incidente.
- **Deriva tra ambiente locale e server (Fase 1 → Fase 2)**: mitigata sviluppando API/job in Docker anche in locale fin da subito (stesse immagini, cambia solo l'host del DB), ma da verificare comunque con un primo deploy di prova su server prima di considerare la Fase 2 conclusa.
