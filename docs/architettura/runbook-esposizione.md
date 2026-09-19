# Runbook — fase 2: esposizione pubblica e login Discord

*Sono i passi che fa una persona, a mano, nell'ordine in cui vanno fatti.
L'ordine non è preferenza: due punti, invertiti, costano un fallimento che non
nomina la propria causa. La specifica è `dashboard-fase2.md` (qui sotto "la
spec"); deploy, migration e cron sono in `runbook-droplet.md`, che resta una
procedura separata. Scritto fuori da git e portato qui il 19/09/2026, dopo che
due sessioni se lo erano sovrascritto a vicenda.*

**Prerequisito già fatto**: `kindling.nexus` comprato su Cloudflare.
**Due hostname**: `kindling.nexus` per le pagine pubbliche,
`dashboard.kindling.nexus` per la dashboard. I nomi in questo runbook sono
quelli veri, non segnaposto.

**Il client secret non passa mai da una chat, da un incolla in un documento, da
un commit.** Va dal portale Discord al `.env` della droplet, e basta.

---

## 0. Prima di cominciare: la via di fuga

Decisa adesso, non quando servirà.

> **Se qualcosa va storto dopo l'esposizione: chiudi 80 e 443 sul Cloud
> Firewall.** Non fermare i container, non toccare il DNS. Niente è più
> raggiungibile da internet, il tunnel SSH continua a darti `/health`, i log e
> `docker compose exec` verso Postgres e l'API, e si ragiona con calma.

**Da quel momento la dashboard non è visibile da nessuna parte**: da fuori le
porte sono chiuse, e dal tunnel la guardia del login vale come sempre — il
tunnel non scavalca l'autenticazione, per decisione (spec, 8-bis). È sicuro, è
voluto, e **non è un guasto da diagnosticare**. Per capire cosa non va bastano
log ed `exec`; per guardare l'interfaccia senza login c'è il fixture in locale.
Se proprio serve l'interfaccia di produzione dal tunnel, c'è una procedura in
fondo a questo runbook — con il suo ritorno.

Tienilo aperto in una scheda: la console DigitalOcean, sezione Networking →
Firewalls → `kindling-fw`.

---

## 1. DNS su Cloudflare — per primo

Va per primo perché la propagazione non è istantanea e perché la richiesta del
certificato risolve il nome: se il record non è propagato, Caddy fallisce per
una ragione che non c'entra con Caddy.

**Due record A**, entrambi con la stessa configurazione:

| Tipo | Nome | Valore | Proxy |
|---|---|---|---|
| A | `@` (apex, cioè `kindling.nexus`) | IP della droplet | **DNS only** (grigia) |
| A | `dashboard` | IP della droplet | **DNS only** (grigia) |

TTL **60 secondi** su entrambi finché non è tutto fermo; lo alzerai al punto 8.

La nuvola dev'essere **grigia**. È la decisione di `3-bis` della spec: con
quella arancione il TLS automatico di Caddy non funziona più come previsto, e
tutta la sezione SSL/TLS del pannello Cloudflare diventa irrilevante per questi
nomi — il traffico non ci passa.

Verifica, prima di andare avanti. **Da Windows** (`dig` non esiste: è Unix):

```powershell
Resolve-DnsName kindling.nexus
Resolve-DnsName dashboard.kindling.nexus
# entrambi devono rispondere l'IP della droplet, e nient'altro

Resolve-DnsName kindling.nexus -Type NS
```

Per il CAA, `Resolve-DnsName -Type CAA` non è riconosciuto su tutte le versioni
di Windows. Se protesta, usa un resolver pubblico via HTTP — `curl.exe` c'è di
serie su Windows 10 e 11:

```powershell
curl.exe "https://dns.google/resolve?name=kindling.nexus&type=CAA"
```

**`curl.exe` con l'estensione**: in PowerShell `curl` da solo è un alias di
`Invoke-WebRequest`, che interpreta gli argomenti in un altro modo e risponde
qualcosa di incomprensibile. Nel JSON, **nessuna sezione `Answer` significa
nessun record CAA**, che è la risposta che vuoi.

*Dalla droplet, dove `dig` c'è (o si installa con `apt install dnsutils`), gli
equivalenti sono `dig +short <nome>`, `dig NS kindling.nexus` e
`dig CAA kindling.nexus`.*

**La via più semplice resta il pannello Cloudflare**, che non richiede nessun
comando: se il dominio risulta *Active*, la delega dei nameserver è corretta per
definizione; e in **DNS → Records** si vede a occhio se esiste una riga `CAA`.

Se una risoluzione risponde un IP di Cloudflare (tipicamente `104.x` o
`172.67.x`), quella nuvola è ancora arancione: torna a sistemarla.

**Il CAA è la trappola silenziosa**: se esiste un record CAA che non autorizza
`letsencrypt.org`, la richiesta del certificato fallisce con un errore che non
nomina la causa, e si finisce a cercare il problema nel firewall.

---

## 2. Applicazione Discord — si può fare in parallelo al punto 1

**È l'applicazione che già esiste**, quella del bot: non crearne una seconda
(il perché è in `3-ter` della spec).

1. <https://discord.com/developers/applications> → l'applicazione di Kindling
2. **OAuth2** → *Redirects* → *Add Redirect*:
   `https://dashboard.kindling.nexus/oauth/callback`
   Esatta. Uno slash finale di differenza è un errore che Discord segnala in
   modo poco esplicito.
3. **Client ID**: copialo (è pubblico, non è un segreto).
4. **Client Secret**: *Reset Secret* se non l'hai mai usato, poi copialo **una
   volta** — Discord non lo rimostra.
5. **General Information** → compila, se non ci sono già:
   - *Terms of Service URL*: `https://kindling.nexus/termini-di-servizio.html`
   - *Privacy Policy URL*: `https://kindling.nexus/informativa-privacy.html`

   **Sull'apex, non sul sottodominio della dashboard**: sono le pagine che una
   persona legge per decidere se autorizzare, quindi devono stare dove non
   serve essere già autorizzati. Sono i due file in `legal/`, che finora non
   erano serviti da nessuna parte.
6. **Verifica che l'account proprietario abbia la 2FA attiva.** È la difesa
   vera: chi entra nel portale può aggiungere una redirect URI, ed è quello —
   non il secret — il controllo sensibile dell'OAuth.

---

## 3. `.env` sulla droplet

```bash
ssh kindling
cd /root/kindling
cp .env .env.bak.$(date +%Y%m%d)   # sempre, prima di toccarlo
nano .env
```

Aggiungi:

```
KINDLING_DISCORD_CLIENT_ID=...
KINDLING_DISCORD_CLIENT_SECRET=...
KINDLING_OAUTH_REDIRECT_URI=https://dashboard.kindling.nexus/oauth/callback
KINDLING_SESSION_SECRET=
```

Il valore di `KINDLING_SESSION_SECRET` si genera sulla droplet, non altrove:

```bash
openssl rand -hex 32
```

`-hex` e non `-base64`, come per le altre credenziali del progetto.

Controlla che le quattro ci siano, una volta sola e **non vuote**:

```bash
grep -cE '^KINDLING_(DISCORD_CLIENT_ID|DISCORD_CLIENT_SECRET|SESSION_SECRET|OAUTH_REDIRECT_URI)=.+' .env
# deve dare 4
```

Per nome esatto e con un valore, non `grep -c '^KINDLING_'`: sulla droplet c'è
anche `KINDLING_PSEUDONYM_SALT`, e un conteggio di prefisso direbbe «tutto a
posto» proprio quando ne manca una. La dashboard rifiuta comunque di partire se
una delle quattro manca o è vuota, se la chiave di sessione ha meno di 32
caratteri o se la redirect URI non è https: questo controllo serve a saperlo
prima, non dopo.

---

## 4. Il firewall — prima del deploy

*Corretto il 18/09/2026: in una stesura precedente il deploy veniva prima del
firewall. `ops/kindling-deploy.sh` avvia `caddy` da sé — sta nella riga di `up`,
e il test che lo impone fa il suo mestiere — quindi le porte devono essere già
aperte quando lo script parte, o la prima richiesta di certificato fallisce.*

Aprirlo prima è sicuro, e non è una disattenzione: finché Compose non avvia
Caddy, sulle porte 80 e 443 non ascolta nessuno, e chi bussa trova `connection
refused`. Se poi lo script si ferma su una verifica prima dell'`up` (migration,
Caddyfile non valido), Caddy non parte affatto e non c'è esposto niente.

DigitalOcean → Networking → Firewalls → `kindling-fw` → **Inbound Rules**:

| Tipo | Protocollo | Porta | Sorgente |
|---|---|---|---|
| HTTP | TCP | 80 | All IPv4, All IPv6 |
| HTTPS | TCP | 443 | All IPv4, All IPv6 |

La 80 serve, non è un di più: è su quella che passa la verifica di Let's
Encrypt, ed è quella che fa il redirect verso HTTPS.

SSH resta com'è. **Nient'altro si apre** — nemmeno la 443/UDP: HTTP/3 è spento
nel Caddyfile (opzioni globali, `protocols h1 h2`) proprio perché questa porta
non è aperta. Si riaccende solo aprendo UDP 443 qui **e** pubblicandola nel
compose, insieme.

---

## 5. Il codice

```bash
# backup del database prima di toccare produzione, come sempre.
# DUE comandi: il primo crea il dump dentro il container, il secondo lo porta
# fuori. Una prima stesura di questo runbook aveva solo il `docker cp`, che
# copiava un file che nessun pg_dump aveva mai creato — un backup che fallisce
# esattamente nel momento in cui serve.
docker compose exec postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" -f /tmp/pre-fase2.dump'
docker cp "$(docker compose ps -q postgres):/tmp/pre-fase2.dump" /root/backups/pre-fase2.dump
ls -lh /root/backups/pre-fase2.dump    # deve pesare qualcosa, non zero

git pull            # verifica che dica davvero il commit nuovo, non "Already up to date"
./ops/kindling-deploy.sh
```

Il percorso di destinazione è **assoluto**, e non è pedanteria: la working
directory qui è `/root/kindling`, cioè il checkout git, e un `docker cp ...
./nome.dump` scritto lì mette un dump con dati di persone reali dentro il
repository. È già successo il 17/09, corretto prima di essere eseguito.

Attenzione al fallimento silenzioso già successo il 17/09: se `git pull` dice
*Already up to date* e `code_version` non cambia, **il commit non è su GitHub** e
stai per deployare il codice di ieri con tutte le verifiche verdi.

Lo script ha due uscite nuove, entrambe bloccanti:

- **15** — `ops/Caddyfile` non valido (fermato *prima* di `up`, niente
  toccato), oppure `caddy reload` fallito dopo `up` (Caddy serve ancora la
  configurazione precedente);
- **16** — `/data` del container caddy non è il volume `caddy_data`: i
  certificati non persisterebbero, e al sesto riavvio della settimana Let's
  Encrypt rifiuterebbe.

---

## 6. Il primo certificato — questo va guardato

Subito dopo lo script, senza riavviare niente:

```bash
docker compose logs -f caddy
```

Devi vedere **due** certificati ottenuti, uno per `kindling.nexus` e uno per
`dashboard.kindling.nexus`. Se ne vedi uno solo, guarda quale manca: è quasi
sempre il record DNS corrispondente a non essere propagato. Se invece vedi:

- **timeout / connection refused** sulla verifica → il firewall non è aperto, o
  non su quella porta;
- **NXDOMAIN o IP sbagliato** → il DNS non è propagato, o la nuvola è arancione
  (torna al punto 1);
- **rate limited** → hai già chiesto 5 certificati questa settimana per lo
  stesso nome. Ferma tutto: non riprovare in ciclo, peggiora. Lo script ha già
  verificato che `/data` sia il volume `caddy_data` (uscita 16 altrimenti); se
  è passato, la causa è altrove — di solito troppi tentativi a porte chiuse o
  con il DNS non ancora propagato.

Poi, da locale:

```bash
# dalla droplet, o da Git Bash
curl -sI https://kindling.nexus/informativa-privacy.html | head -3
curl -sI https://kindling.nexus/ | head -3
curl -sI http://dashboard.kindling.nexus/ | head -3   # deve rispondere 308 verso https
```

```powershell
# da PowerShell: curl.exe con l'estensione, sempre
curl.exe -sI https://kindling.nexus/informativa-privacy.html
curl.exe -sI https://kindling.nexus/
curl.exe -sI http://dashboard.kindling.nexus/
```

Le prime due devono dare `200` (la seconda è `legal/index.html`), la terza un
redirect verso HTTPS. Se `curl` non protesta sul certificato, la catena è a
posto.

`/health` non si serve da fuori (`respond /health 404` nel Caddyfile): serve
all'healthcheck del container, che la chiama dall'interno.

```bash
curl -sI https://dashboard.kindling.nexus/health | head -1   # deve dare 404
```

```powershell
curl.exe -sI https://dashboard.kindling.nexus/health   # prima riga: 404
```

---

## 7. Il login, e le quattro verifiche che contano

Apri `https://dashboard.kindling.nexus` in una finestra **anonima** (per non avere
una sessione preesistente).

1. **Entra.** Ti manda a Discord, torni, vedi l'elenco dei server. Se non torni:
   la redirect URI non combacia — confronta carattere per carattere con quella
   nel portale.
2. **La revoca fa effetto entro 15 minuti.** È la verifica più importante,
   perché è l'unico scenario per cui il TTL esiste.

   **Il caso vero è la perdita di ADMINISTRATOR**, e va provato quello se si
   può: su un server di prova dove un secondo account è amministratore, entra
   con quell'account, togligli il ruolo, ricarica dopo 15 minuti. La pagina fa
   un giro su Discord senza chiederti niente, torna, e **quel server non deve
   più comparire** (con un solo server: «Nessun server da mostrarti»). Qui
   Discord non restituisce nessun errore: la decisione viene dal ricalcolo
   dell'insieme (spec, 3-quinquies).

   **La deautorizzazione** (*Impostazioni Discord → App autorizzate → Kindling →
   Deautorizza*) è un caso diverso, e il suo esito con `prompt=none` **non è
   documentato da Discord**. Dopo 15 minuti, alla ricarica, può succedere una di
   due cose: torni con «La verifica non è riuscita», oppure Discord ti mostra di
   nuovo la schermata di consenso. **In quel secondo caso non autorizzare**:
   annulla, e devi finire su «La verifica non è riuscita». Annota quale dei due
   comportamenti hai visto: è l'informazione che la spec non ha.

   Se resti dentro senza nessun giro su Discord, il ricontrollo non sta girando,
   e vale la pena fermarsi lì.
3. **Il `code` non è nei log.** Dopo un login vero:
   ```bash
   docker compose logs caddy | grep -c 'oauth/callback'   # > 0: la riga c'è
   docker compose logs caddy | grep -c 'code='            # deve dare 0
   docker compose logs dashboard | grep -ci 'code='       # deve dare 0
   ```
   *Corretto il 18/09/2026:* niente `log_skip`. Senza la direttiva `log` Caddy
   non scrive nessun access log; con `log` attivo, il filtro toglie il solo
   `code` e la riga del callback **resta** — il primo comando deve dare più di
   zero, ed è la prova che l'access log esiste davvero. Guarda il log, non la
   configurazione: è l'unico modo di saperlo.

   **Da verificare, non ancora verificato**: se anche i log di *errore* di
   Caddy riportino l'URI. Se riesci a produrlo a costo basso — per esempio
   `docker compose stop dashboard`, un login fino al callback (502), poi
   `docker compose start dashboard` — ripeti il secondo comando. Se non lo fai,
   resta scritto come non verificato in `stato-progetto.md`, non come sicuro.
4. **La cache non conserva le pagine.**
   ```bash
   curl -sI https://dashboard.kindling.nexus/ | grep -i cache-control
   ```
   Deve dire `private, no-store`. La dashboard lo mette su **ogni** risposta,
   quindi si vede anche senza sessione (lì la risposta è il redirect verso
   `/login`); per una pagina autenticata: strumenti del browser → Rete →
   intestazioni della risposta.

---

## 8. Chiudere

- **TTL del DNS** da 60s al valore normale (1 ora o auto).
- **Misura la memoria**, che è la verifica che `stato-progetto.md` §5 rimanda
  esplicitamente «all'arrivo di Caddy»:
  ```bash
  free -m
  docker stats --no-stream
  ```
  Scrivila in `stato-progetto.md` §5 accanto a quella del 14/09.
- **Aggiorna `stato-progetto.md`**: la voce §7-A "Fase 2 della dashboard" si
  chiude, e nascono le voci nuove che questo passaggio lascia aperte (il proxy
  Cloudflare da valutare, `3-bis` della spec).
- **Il `.env.bak`**: cancellalo quando sei sicuro, non lasciarlo lì.

---

## Emergenza: vedere l'interfaccia di produzione dal tunnel

Non è una procedura di routine. Per capire cosa non va bastano `/health`, i log
e `docker compose exec`; per guardare l'interfaccia senza login c'è il fixture
in locale. Questa serve solo se devi vedere **proprio** la dashboard di
produzione, per esempio con 80/443 chiuse dopo la via di fuga del passo 0.

Il tunnel non scavalca il login (spec, 8-bis): quello che si fa qui è far
atterrare il login su `localhost` invece che sul nome pubblico, per il tempo
strettamente necessario.

**Andata**

1. Portale Discord → l'applicazione di Kindling → **OAuth2 → Redirects** →
   aggiungi `http://localhost:8000/oauth/callback`. **Aggiungi**, non
   sostituire: quella pubblica resta dov'è.
2. Sulla droplet:
   ```bash
   cd /root/kindling
   cp .env .env.bak.emergenza
   nano .env   # KINDLING_OAUTH_REDIRECT_URI=http://localhost:8000/oauth/callback
   docker compose up -d dashboard
   ```
   `up -d`, **non** `restart`: `restart` riavvia il container con l'ambiente che
   aveva, e la variabile nuova non arriva — la dashboard ripartirebbe con la
   redirect URI pubblica, exit 0, e il login continuerebbe ad atterrare sul
   nome sbagliato. Da questo momento il login su `dashboard.kindling.nexus` non
   funziona: è il prezzo, ed è il motivo per cui non si resta in questo stato.
3. Dal tuo computer: `ssh -L 8000:localhost:8000 kindling`, poi
   `http://localhost:8000` nel browser. Il login passa da Discord e torna su
   `localhost`. Il cookie qui non è `Secure` (redirect URI http su localhost), e
   va bene: il traffico viaggia dentro il tunnel.

**Ritorno — va fatto sempre, anche se l'emergenza è finita male**

4. Sulla droplet, rimetti la redirect URI pubblica:
   ```bash
   cd /root/kindling
   cp .env.bak.emergenza .env
   grep '^KINDLING_OAUTH_REDIRECT_URI=' .env
   # deve dire https://dashboard.kindling.nexus/oauth/callback
   docker compose up -d dashboard
   docker compose exec -T dashboard printenv KINDLING_OAUTH_REDIRECT_URI
   # di nuovo https://...: è il valore che il processo ha DAVVERO, non quello
   # scritto nel file
   ```
   Se durante l'emergenza hai cambiato altro nel `.env`, non copiare il backup
   sopra: correggi a mano la sola riga della redirect URI.
5. Portale Discord → **OAuth2 → Redirects** → **togli**
   `http://localhost:8000/oauth/callback`. Deve restarne una sola, quella
   pubblica. Una redirect URI dimenticata nell'elenco è esattamente l'allowlist
   allargata che la spec ha rifiutato di tenere in permanenza.
6. Se avevi chiuso 80/443, riaprile solo quando la causa dell'emergenza è
   risolta, e rifai la verifica 1 del passo 7 (login da una finestra anonima su
   `https://dashboard.kindling.nexus`).
7. `rm .env.bak.emergenza`.

L'emergenza è chiusa quando sono veri tutti e tre: `printenv` dice `https://`,
il portale ha una sola redirect URI, e il login pubblico funziona.

---

## Quello che resta aperto dopo questo passaggio

- **Il proxy di Cloudflare**, spento di proposito. Se e quando lo accendi, i
  quattro passi di `3-bis` vanno fatti insieme: certificato Origin, Full
  (strict), firewall ristretto agli IP di Cloudflare, nessuna regola di cache.
- **Il nome del server** continua a non esistere: la dashboard mostra numeri di
  diciotto cifre perché `GuildRow` non ha un campo `name`. È un lavoro su bot e
  API, non sulla dashboard.
- **Un community manager senza ADMINISTRATOR resta fuori**, ed è il costo
  dichiarato di §3. Il controllo vive in una funzione sola proprio perché il
  giorno in cui servirà un ruolo dedicato si cambi quella, non si faccia un
  refactor.
