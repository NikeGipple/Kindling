# Fase 2 — esposizione pubblica e login Discord

*Aggiunta a `docs/architettura/dashboard.md`, scritta il 18/09/2026. Estende §3
(autenticazione) e §8 (deploy) con quello che manca: Cloudflare, che non
esisteva quando quelle sezioni sono state scritte, e le decisioni prese oggi.
Non riscrive §3: il modello di autorizzazione — scope `identify guilds`,
permesso ADMINISTRATOR, TTL di ricontrollo 15 minuti, sessione 8 ore, `state`
monouso, token Discord scartato — resta quello, e va letto lì.*

**Due hostname, non uno.** `kindling.nexus` (apex) serve le pagine pubbliche —
privacy e termini, quelle che l'applicazione Discord richiede; 
`dashboard.kindling.nexus` serve la dashboard dietro il login. Un solo Caddy,
due blocchi, due certificati automatici.

La separazione non è estetica: **chi deve decidere se autorizzare Kindling legge
quelle due pagine prima di avere un accesso**. Su un hostname che risponde solo
a chi è già amministratore, sarebbero illeggibili proprio a chi servono. E
lasciare l'apex come porta d'ingresso pubblica tiene libero il posto per una
pagina sul progetto, senza che la prima cosa che si incontra sul dominio sia una
schermata di login per amministratori.

---

## 3-bis. Cloudflare, e perché per ora sta spento

Il dominio è su Cloudflare. Cloudflare offre due modi di puntare un record a
una macchina, e la differenza non è cosmetica: cambia chi fa il TLS.

**Decisione: record A in modalità "solo DNS" (nuvola grigia), per ora.**

Con il proxy acceso, Cloudflare termina lui il TLS. Ne segue che il TLS
automatico di Caddy — che §8 dà per scontato — non funziona più come scritto:
la sfida `TLS-ALPN-01` non raggiunge mai Caddy, e la `HTTP-01` regge solo
finché un redirect "Always Use HTTPS" non intercetta
`/.well-known/acme-challenge/`. Le strade che funzionano con il proxy acceso
sono due, e ognuna aggiunge pezzi: un certificato Origin di Cloudflare
installato a mano in Caddy (niente ACME, ma un certificato in più da
sostituire fra quindici anni o al cambio di dominio), oppure una build di
Caddy con il plugin Cloudflare per la sfida DNS-01, che richiede un token API
con permessi di scrittura sul DNS conservato sulla droplet — un segreto nuovo
con un raggio d'azione più grande di quello che sta proteggendo.

Con "solo DNS" non serve niente di tutto questo: Caddy chiede il certificato a
Let's Encrypt come da manuale, e la catena si verifica tutta in una volta. È un
cambiamento alla volta, che è il metodo con cui è stato costruito tutto il
resto.

**Il costo, dichiarato.** L'IP della droplet è pubblico nel DNS, e non c'è
nessuno davanti ad assorbire traffico ostile. Non è una regressione: è
esattamente la situazione di oggi, dove l'IP è già pubblico e la difesa è il
Cloud Firewall. Cambia solo che due porte in più rispondono.

**Quando si accenderà il proxy — e non è oggi — i quattro passi vanno fatti
insieme**, nello stesso modo in cui i cinque prerequisiti di §8 vanno fatti
insieme:

1. certificato Origin di Cloudflare installato in Caddy, e `tls` nel Caddyfile
   che punta a quel certificato invece che ad ACME;
2. modalità SSL/TLS su **Full (strict)**. Non "Flexible": con Flexible
   Cloudflare parla HTTP in chiaro verso l'origine e il redirect automatico di
   Caddy verso HTTPS produce un loop infinito — è il modo più comune di
   sbagliare questa configurazione, e dà una pagina di errore che non nomina la
   causa;
3. Cloud Firewall ristretto agli **IP di Cloudflare** su 80/443. Senza questo
   l'origine resta raggiungibile per IP e il proxy si aggira: si pagherebbe la
   complessità di Cloudflare senza ottenerne la protezione;
4. nessuna regola di cache sull'hostname, ed è la più pericolosa da dimenticare
   — vedi sotto.

### La cache è un rischio di riservatezza, non di prestazioni

Una regola "Cache Everything" su questo hostname servirebbe la pagina di un
amministratore a un altro. In un progetto che esiste per non esporre dati per
singola persona, sarebbe il fallimento peggiore possibile, e arriverebbe da una
casella spuntata in un pannello, non da una riga di codice.

**Quindi la difesa non sta su Cloudflare: sta nelle risposte.** Ogni risposta
autenticata porta `Cache-Control: private, no-store`. Vale a prescindere dal
proxy — protegge anche dalle cache intermedie e dal disco del browser su un
computer condiviso — ed è l'unica forma di difesa che non dipende da una
configurazione fatta altrove da qualcuno che potrebbe non ricordarsene.

---

## 3-ter. Una sola applicazione Discord, quella del bot

**Decisione: la dashboard usa l'applicazione Discord che già esiste**, quella
dell'ingestion, aggiungendovi una redirect URI e un client secret.

L'istinto dice di separare: la dashboard è la cosa esposta, il bot è la cosa
privilegiata. Ma dentro un'applicazione Discord **bot token e client secret
sono credenziali distinte**, con pulsanti di rigenerazione distinti: il secret
della dashboard si ruota senza toccare il bot, che quindi non perde il gateway.
E finiscono comunque in processi diversi — il container `dashboard` non riceve
`env_file` (§1, e c'è un test), quindi il token del bot non entra mai nel
processo esposto, con una o con due applicazioni.

Quello che si perderebbe separando è invece concreto: la schermata di consenso
mostrerebbe **un'applicazione che l'amministratore non ha mai visto**. Vedere il
nome e l'icona del bot che è già nel proprio server è un buon segnale; abituare
le persone ad accettare prompt OAuth sconosciuti è il contrario. È la stessa
logica con cui §3 ha rifiutato un ruolo Discord dedicato: nessuna tessera nuova
da tenere allineata.

**Il controllo davvero sensibile non è il secret: è l'elenco delle redirect
URI.** Un secret rubato, senza una redirect URI che l'attaccante controlla, non
produce granché; aggiungerne una richiede l'accesso al portale sviluppatori.
Quindi la difesa vera è **2FA sull'account Discord proprietario** e una sola
redirect URI, esatta. Vale identica con una o con due applicazioni.

La decisione è reversibile a costo basso: separare domani significa creare
un'applicazione, spostarci la redirect URI e cambiare due variabili nel `.env`.

---

## 3-quater. I log, e un componente nuovo che allarga una regola già scritta

§3 dice: *«Uvicorn con access log scrive la query string: sul callback OAuth
significa scrivere il `code` in chiaro dentro journalctl. Va disattivato su
quella rotta prima del primo login reale, non dopo.»*

La regola era giusta e completa quando è stata scritta. Con Caddy davanti non
lo è più: **Caddy, con la direttiva `log` attiva, logga l'URI completo**, query
string inclusa. Un componente nuovo ha allargato in silenzio la superficie di
una regola esistente — la classe di difetto di `stato-progetto.md` §10, in forma
di configurazione invece che di codice.

> **Correzione (18/09/2026), trovata implementando.** La prima stesura diceva
> «anche Caddy logga l'URI completo» senza condizioni, e proponeva `log_skip`
> sulla rotta del callback. È vero solo con la direttiva `log`: **senza, Caddy
> non scrive nessun access log**, e `log_skip` non ha effetto. Quella
> configurazione, insieme a `--no-access-log` su uvicorn, avrebbe eliminato ogni
> access log del sistema — il rimedio peggiore del problema. `log_skip` esiste
> (da Caddy 2.8; `caddy:2-alpine` è oggi 2.11.4), ma la forma scelta è un'altra.

**La forma adottata** (`ops/caddy/Caddyfile`): `log` attivo sull'hostname della
dashboard, con `format filter` che toglie **il solo parametro `code`** dalla
query string (`request>uri query { delete code }`). La riga del callback resta,
con stato e durata: è quella che serve a diagnosticare il primo login vero, e
non c'è ragione di buttarla per proteggere un parametro che si toglie da solo.
Le intestazioni `Cookie` e `Authorization` Caddy le oscura già di suo finché
`log_credentials` resta spento. Uvicorn gira con `--no-access-log` su **tutte**
le rotte, non solo sul callback come diceva §3: l'access log vive in Caddy.

**Non verificato**: se anche i log di **errore** di Caddy riportino l'URI — per
esempio su un 502 al callback con la dashboard giù. Il filtro vale per l'access
log e la documentazione di Caddy non lo dice. Va guardato dopo il primo login
vero, come chiede la regola qui sotto; finché non lo si è visto, è un'incognita
dichiarata, non un fatto.

**La regola si riscrive in forma generale**, così che il prossimo componente
che vede gli URI la erediti invece di sfuggirle:

> Nessun componente che veda l'URI del callback OAuth ne registra la query
> string. Oggi sono due — uvicorn e Caddy — e per entrambi va verificato
> guardando il log dopo un login vero, non leggendo la configurazione.

Il `code` transita comunque per Cloudflare in quanto parte dell'URL. È inerente
a qualunque proxy, dura pochi secondi, è monouso e senza il client secret non
si scambia. Va detto, non nascosto: è un allargamento reale del confine di
fiducia, ed è il prezzo del dominio.

---

## 3-quinquies. Come si ricontrolla un permesso senza il token

§3 afferma due cose che, messe insieme, implicano una terza che non dice:

- *«il token Discord si scarta»*;
- *«TTL del ricontrollo permesso: 15 minuti»*.

Se il token è stato scartato, **il ricontrollo non può essere una chiamata in
background**: non c'è più niente con cui autenticarla. Le alternative sono due,
e una sola è compatibile con §3.

**Conservare il token** in sessione lo renderebbe possibile — ma il cookie di
sessione è *firmato, non cifrato*: qualunque cosa ci finisca dentro è leggibile
da chi ha il cookie. Un token OAuth in un cookie leggibile è precisamente il
genere di dato che §3 ha deciso di non avere. Scartato.

**Un giro silenzioso attraverso Discord**, con `prompt=none` sull'URL di
autorizzazione: se l'utente ha già autorizzato l'applicazione, Discord salta la
schermata e rimanda indietro con un `code` nuovo. Nessun dato conservato.

> **Correzione (18/09/2026), trovata implementando.** La prima stesura diceva
> che, se l'autorizzazione è stata revocata, Discord «risponde con un errore».
> Discord **non lo documenta**: la documentazione dice solo che con `none` la
> schermata si salta se l'utente ha già autorizzato. Su un'autorizzazione
> revocata potrebbe tornare un errore, oppure riapparire la schermata di
> consenso. Non ci si appoggia a nessuno dei due.

**La regola, che non dipende da quel comportamento**: ogni esito diverso da
`code` valido, `state` valido, scambio riuscito e insieme autorizzato non vuoto
**nega l'accesso**, e cancella la sessione — un ricontrollo fallito non lascia al
suo posto la sessione di prima.

**Il caso decisivo non arriva mai come errore.** Chi perde ADMINISTRATOR — cioè
lo scenario per cui il TTL esiste — non ha revocato niente: riceve un `code`
valido, e Discord risponde regolarmente. Il ricontrollo è quindi **il flow
completo senza schermata** (`code` → token → `GET /users/@me/guilds` →
intersezione con le guild osservate → token scartato), e la decisione si prende
**ricalcolando l'insieme**, non leggendo un errore. Un test che simulasse solo
«Discord risponde errore» lascerebbe scoperto proprio questo caso; quello che
conta simula una risposta regolare con l'insieme ridotto o svuotato.

Il tipo del giro — login interattivo o ricontrollo — sta nella sessione insieme
allo `state`, non nell'URL: se lo decidesse il client, un giro silenzioso
potrebbe presentarsi come login e rinnovare le 8 ore senza nessuna interazione.

Il costo è un redirect visibile ogni quarto d'ora per chi tiene la dashboard
aperta: nessuna interazione, ma la pagina fa un giro. È accettabile perché la
dashboard è di sola lettura — non c'è nessun modulo a metà da perdere — ed è il
prezzo di non conservare niente.

### Le 8 ore sono una scadenza assoluta, e la impone la guardia

> **Correzione (18/09/2026), trovata implementando.** La prima stesura dava per
> scontato che `max_age=8*3600` di `SessionMiddleware` realizzasse le 8 ore di
> §3, «validate lato server dal signer». Non è così.

`SessionMiddleware` di Starlette (verificato sulla 0.48) **rifirma e riemette il
cookie a ogni risposta** con una sessione non vuota. Il `max_age` del signer
conta quindi dall'**ultima richiesta**, non dal login: è un timeout di
inattività. Un cookie rubato e usato di continuo non scadrebbe mai — ed è proprio
lo scenario da cui la scadenza di §3 protegge. Peggio: il ricontrollo silenzioso
ogni 15 minuti terrebbe viva la sessione da solo, e il meccanismo pensato per
ridurre l'accesso finirebbe per prolungarlo indefinitamente. È la classe di
difetto di `CLAUDE.md` §7 in forma pura.

Quindi:

- in sessione c'è `login_at`, scritto **solo** dal login interattivo (quello con
  la schermata di consenso), **mai** dal ricontrollo;
- **la guardia** nega quando sono passate 8 ore da `login_at`, a prescindere da
  quanto è fresca la firma;
- il `max_age` del signer resta, come **seconda** difesa e non come la difesa.

La sessione contiene dunque: le guild autorizzate, `checked_at`, `login_at` — più
`state` e destinazione, solo fino al callback. Tre timestamp e un insieme di id
di server non identificano nessuno: il divieto di §3 («nessuna tabella utenti,
nessun `author_id` di chi si collega, nessun token conservato») resta intatto, e
restano fuori username, avatar e id Discord.

`login_at` rende anche possibile dire «la sessione è scaduta» come frase distinta
da «non hai mai fatto accesso»: senza, un cookie scaduto è indistinguibile da un
cookie mai esistito, perché il browser lo scarta e Starlette lo tratta come
assente.

Il test che conta non è un cookie vecchio di otto ore — quello lo rifiuterebbe
il signer, e passerebbe per la ragione sbagliata — ma **una firma fresca con
`login_at` vecchio**: l'unico stato che distingue la scadenza assoluta dal timeout
di inattività, ed è lo stato di un cookie rubato e usato di continuo.

*Questa è una decisione nuova, non una lettura di §3: §3 non la contiene, e
andrebbe incorporata lì quando si tocca quel documento.*

---

## 8-bis. La forma del servizio `caddy`

Come per `dashboard` in §8, scritta qui e non decisa a margine del codice.

| | |
|---|---|
| Nome del servizio | `caddy` |
| Immagine | `caddy:2-alpine` — ufficiale, non costruita da noi |
| Porte | `80` e `443` **su tutte le interfacce**. È l'unica eccezione alla regola del progetto, ed è stretta: solo questo servizio, solo queste due porte |
| `volumes` | `./ops/caddy:/etc/caddy:ro` (una directory: vedi sotto, «Il reload del Caddyfile»), `caddy_data:/data`, `caddy_config:/config`, `./public:/srv/public:ro` |
| `mem_limit` | `96m` (misurato ~30 MB in `dashboard.md` §2; il tetto è margine, non stima) |
| `depends_on` | `dashboard`, con `condition: service_started` — **non** `service_healthy`, vedi sotto |
| `restart` | `unless-stopped` |

**`caddy_data` non è un dettaglio: è la differenza tra funzionare e smettere di
funzionare dopo un mese.** In `/data` Caddy conserva i certificati e la chiave
dell'account ACME. Senza un volume, ogni ricreazione del container ne chiede di
nuovi; Let's Encrypt ne concede **5 a settimana per lo stesso insieme di nomi**,
e oltre quel limite rifiuta per una settimana intera. Un deploy al giorno per
cinque giorni e il sesto la dashboard non ha più un certificato — con i log di
Caddy che dicono "rate limited" e tutto il resto perfettamente funzionante. È la
forma esatta della classe di difetto di §10: un meccanismo che sembra funzionare
e non dice che non funziona, finché non smette.

**`service_started` e non `service_healthy`, ed è una correzione alla prima
stesura di questa spec.** Legare l'avvio di Caddy alla salute della dashboard
sembra prudente e invece accoppia le cose sbagliate: se la dashboard non diventa
sana, Caddy non parte, e allora non rispondono nemmeno **le pagine pubbliche
sull'apex** — che non hanno niente a che fare con la dashboard — e soprattutto
**non gira il rinnovo dei certificati**. Un guasto della dashboard diventerebbe,
dopo qualche settimana, un dominio intero senza certificato.

Caddy risolve `dashboard:8000` al momento della richiesta: se il backend è giù,
risponde 502 su quell'hostname e continua a servire tutto il resto. Che è
esattamente il comportamento giusto.

**La dashboard mantiene `127.0.0.1:8000:8000`.** Caddy la raggiunge per nome
sulla rete interna del compose (`dashboard:8000`); il binding sul loopback resta
per il tunnel SSH. Non è una porta in più verso internet: è la stessa di oggi.
**Dal tunnel la guardia vale come da fuori: nessuno scavalcamento.**

> **Correzione (18/09/2026), trovata implementando.** La prima stesura diceva
> che il tunnel SSH «continua a essere il modo di guardare la dashboard
> scavalcando l'autenticazione quando qualcosa non va». Era falso dal momento in
> cui la guardia è entrata in funzione: vale su ogni richiesta, anche su quelle
> dal tunnel. Da `http://localhost:8000` si viene rimandati a `/login`, il login
> porta a Discord con la redirect URI pubblica, il callback atterra su
> `dashboard.kindling.nexus` e il cookie nasce su quel nome, non su `localhost`.

**Decisione: niente scavalcamento, e l'emergenza diventa una procedura invece di
una porta.** Le due alternative sono state valutate e scartate:

- **una seconda porta senza guardia**, raggiungibile solo dal loopback: sarebbe
  un accesso non autenticato all'intera dashboard, residente nel codice per
  sempre, a un errore di configurazione dall'essere raggiungibile da fuori — in
  un progetto la cui regola è «si nega, non si passa». Una comodità occasionale
  non paga quel rischio;
- **una redirect URI per `localhost` tenuta in permanenza**: non renderebbe
  l'emergenza più rapida, perché la configurazione si legge all'avvio e usarla
  richiederebbe comunque di cambiare il `.env` e riavviare. Se il costo al
  momento del bisogno è lo stesso, tenere l'allowlist allargata ogni giorno in
  attesa di un'emergenza che forse non arriva è costo puro — e contraddice «una
  sola redirect URI, esatta» di 3-ter.

**Cosa il tunnel serve davvero**, e non è poco: `/health`, i log, e
`docker compose exec` verso Postgres e l'API — cioè tutto ciò che serve a capire
perché qualcosa non va. **Per guardare l'interfaccia senza l'autenticazione di
produzione c'è il fixture in locale** (`dashboard.md` §7: si sviluppa contro il
fixture, non contro la produzione). Il tunnel verso la dashboard non era il modo
giusto di farlo: era il modo disponibile in fase 1, quando non ce n'erano altri.

Se un giorno serve davvero vedere l'interfaccia di produzione dal tunnel, la
procedura — redirect URI temporanea per `localhost`, e soprattutto come si torna
indietro — è scritta nel runbook della fase 2, in fondo.

**`/health` non passa da Caddy.** Resta fuori dalla guardia, perché la chiama
l'healthcheck del container e `depends_on` ci si appoggia; ma quell'healthcheck
gira su `localhost` dentro il container, e da internet la rotta non serve. Nel
caso degradato risponde con il testo interno dell'eccezione, e ogni visita genera
una chiamata all'API: il blocco della dashboard nel Caddyfile ha
`respond /health 404` — 404 e non 403, perché non conferma nemmeno che la rotta
esista.

**Il `command` della dashboard cambia rispetto alla tabella di §8**:
`uvicorn dashboard.main:crea_app --factory ... --no-access-log`. `--factory`
perché l'app si costruisce all'avvio leggendo la configurazione OAuth — una
variabile mancante ferma il processo lì, e importare il modulo (i test lo fanno)
non la pretende; `--no-access-log` per 3-quater. L'`environment` passa da una
variabile a cinque: `KINDLING_API_BASE_URL` più le quattro del login.

**Il reload del Caddyfile è esplicito.** Il Caddyfile è un bind mount: se cambia
solo lui, `up -d` non ricrea il container ed esce 0 con la configurazione
vecchia ancora attiva — la stessa famiglia del `build` che saltava `job` perché
stava in un profilo (`CLAUDE.md` §7). `ops/kindling-deploy.sh` valida il
Caddyfile prima di `up` e lo ricarica dopo, e un fallimento ferma lo script
(codice 15). Caddy valida prima di applicare, quindi un reload fallito lascia in
piedi la configurazione precedente: è giusto, purché lo script lo dica. Lo
script verifica anche che `/data` del container sia il volume con etichetta
`com.docker.compose.volume=caddy_data` (codice 16): per etichetta e non per
nome, perché Compose antepone il nome del progetto, e dopo `up`, perché prima del
primo avvio il volume non esiste.

*Corretto il 19/09/2026: il reload così com'era scritto non poteva applicare
niente.* Il Caddyfile era montato come **singolo file**
(`./ops/Caddyfile:/etc/caddy/Caddyfile:ro`), e un bind mount di file è legato
all'inode. `git pull` sostituisce i file (ne scrive uno nuovo e lo rinomina),
quindi il container continuava a vedere il file di quando era stato avviato; il
reload, eseguito con `exec` proprio in quel container, ricaricava quello — senza
errori. La validazione passava perché gira in un container nuovo (`run`), che
monta il file corrente. Il rimedio conteneva il difetto che doveva curare: il
problema (`up -d` non ricrea per un bind mount cambiato) era capito al livello
giusto, il comportamento di `exec` sullo stesso mount no. Ora si monta la
**directory** `./ops/caddy` su `/etc/caddy`, in sola lettura, e dopo il reload
lo script confronta la configurazione in esercizio (admin API, dall'interno del
container) con l'adattamento del Caddyfile corrente in un container nuovo
(codice 19 se differiscono).

### I file statici stanno sull'apex, non sulla dashboard

In `public/` (fino al 20/09/2026 `legal/`) ci sono `informativa-privacy.html` e `termini-di-servizio.html`, e
oggi **non sono serviti da nessuna parte**. L'applicazione Discord chiede una
Privacy Policy URL e una Terms of Service URL, e da oggi c'è un dominio dove
metterle.

Li serve **Caddy sull'apex**, come file statici. Due ragioni distinte:

- **non la dashboard**, perché il processo Python non serve file statici e non
  deve cominciare adesso;
- **non l'hostname della dashboard**, perché quelle pagine servono a chi deve
  ancora decidere se autorizzare l'applicazione — cioè a qualcuno che per
  definizione non ha un accesso. Una privacy policy raggiungibile solo da chi è
  già dentro è una privacy policy che non si può leggere quando serve.

La cartella `public/` diventa la radice web dell'apex, quindi gli URL sono
`https://kindling.nexus/informativa-privacy.html` e
`https://kindling.nexus/termini-di-servizio.html`. Serve un `public/index.html`,
anche minimo: senza, l'apex risponde 404 sulla radice, che come porta
d'ingresso di un dominio è peggio di una pagina di tre righe. **Se l'apex
crescerà oltre le due pagine legali**, la cartella andrà rinominata o affiancata
da una `public/`: oggi il nome regge, domani forse no.

### HSTS: va aggiunto, ed è un impegno

Caddy ottiene e rinnova i certificati da solo e reindirizza il 80 sul 443, ma
**non manda `Strict-Transport-Security`**. Vale la pena metterlo: dice al
browser di non provare nemmeno a parlare in HTTP con questo dominio, il che
chiude la finestra del primo redirect.

È però un impegno che non si ritira: finché il `max-age` non è scaduto, un
browser che l'ha visto **rifiuta** l'HTTP su quel nome, anche se tu volessi
tornare indietro. Quindi: `max-age=86400` (un giorno) al primo giro, da alzare a
un anno quando è tutto fermo, e **senza `includeSubDomains`** finché non è certo
che ogni sottodominio futuro sarà in HTTPS — quella direttiva impegna anche i
nomi che non esistono ancora.

### I test, che si aggiornano e non si aggirano

§8 lo aveva previsto per uno solo; sono tre.

1. **`test_nessun_servizio_pubblica_su_tutte_le_interfacce`** — previsto da §8.
   L'eccezione si scrive stretta: il solo servizio `caddy`, le sole porte 80 e
   443. Un'eccezione scritta come "caddy può pubblicare quello che vuole"
   riaprirebbe per intero la regola che il test esiste per tenere chiusa. Serve
   anche il test gemello che prova che l'eccezione **non** copre una terza porta
   di Caddy né un altro servizio: un'eccezione senza un test che ne misura il
   bordo è un buco.
2. **`test_dashboard_non_riceve_variabili_di_database`** — oggi asserisce
   `set(servizio["environment"]) == {"KINDLING_API_BASE_URL"}`, cioè un elenco
   chiuso. Le variabili OAuth vanno aggiunte a quell'elenco, che deve restare
   chiuso: il valore del test è proprio che sia esatto e non "contiene". Il
   divieto sulle variabili di database resta separato e invariato.
3. **`test_il_deploy_avvia_ogni_servizio_che_costruisce`** — `caddy` non ha
   `profiles`, quindi entra nell'insieme atteso e il test fallirà finché
   `ops/kindling-deploy.sh` non lo avvia. È il test che funziona come previsto:
   costringe da solo a non lasciare un servizio costruito e mai acceso.

### Memoria: la riverifica è adesso

`stato-progetto.md` §5 chiude il rischio "margine di memoria su 1 GB" lasciando
aperta la parte periodica, e indica esplicitamente **l'arrivo di Caddy** come il
momento per riverificare. Misura da fare dopo il primo deploy, con tutto acceso,
e da scrivere in `stato-progetto.md` accanto a quella del 14/09: `free -m` e
`docker stats --no-stream`.

---

## 8-ter. L'ordine dei passi, e perché è questo

I cinque prerequisiti di §8 «tutti insieme e nessuno dopo» restano validi, ma
fra loro c'è un ordine obbligato, e due punti in cui sbagliarlo costa.

1. **DNS prima di tutto.** Due record A — `kindling.nexus` e
   `dashboard.kindling.nexus` — entrambi **solo DNS** (nuvola grigia), TTL
   basso (60s) finché non è tutto fermo. Va fatto per primo perché la propagazione non è istantanea e
   perché ACME risolve il nome: se il record non è propagato, Caddy fallisce
   per una ragione che non c'entra con Caddy.
2. **Discord: redirect URI e secret.** Si può fare in parallelo al DNS.
3. **`.env` sulla droplet.** Le **quattro** variabili nuove: client id, client
   secret, chiave di firma della sessione, redirect URI. Il secret non passa mai
   da una chat, da un log, da un commit.
4. **Il firewall si apre prima di lanciare il deploy.** Aprirlo prima è sicuro:
   finché Compose non avvia Caddy, sulle porte 80 e 443 non ascolta nessuno, e
   chi bussa trova `connection refused`; se lo script si ferma su una verifica
   prima dell'`up`, Caddy non parte affatto e non c'è esposto niente. Aprirlo
   dopo, invece, fa fallire la prima richiesta di certificato.
5. **`./ops/kindling-deploy.sh`, e subito `docker compose logs -f caddy`**:
   `caddy` sta nella riga di `up` dello script, e il test che lo impone fa il
   suo mestiere. Il momento in cui si prende il primo certificato è l'unico che
   dice se DNS, firewall e nome sono giusti, e va guardato.

> **Correzione (18/09/2026), trovata implementando.** La prima stesura diceva
> «le tre variabili nuove» (sono quattro), e ai passi 4 e 5 metteva il firewall
> *dopo* il deploy e il primo avvio di Caddy fuori dallo script. Lo script
> invece avvia Caddy da sé — la regola «ogni servizio costruito si avvia» vale
> anche per lui — quindi l'ordine giusto è `.env` → firewall → deploy → log.
6. **OAuth per ultimo**, quando `https://dashboard.kindling.nexus` risponde già con
   un certificato valido. La redirect URI deve essere esatta: uno slash finale
   di differenza è un errore che Discord segnala in modo poco esplicito.

**Il rollback, deciso prima di servire.** Se qualcosa va storto dopo
l'esposizione, il passo indietro è: chiudere 80/443 sul Cloud Firewall. Non
fermare i container, non toccare il DNS — chiudere le porte. Niente è più
raggiungibile da internet, il tunnel SSH continua a dare `/health`, i log e
`docker compose exec`, e si ragiona con calma.

Da quel momento **la dashboard non è visibile da nessuna parte**: da fuori le
porte sono chiuse, e dal tunnel la guardia vale come sempre (8-bis). È sicuro, è
voluto, e non è un guasto da diagnosticare. *(Corretto il 18/09/2026: la prima
stesura diceva che il sistema tornava «esattamente allo stato di ieri», quando
ieri la dashboard si guardava dal tunnel senza login.)*
