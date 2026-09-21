# Dashboard — nota di progettazione

*Decisa il 12 settembre 2026, chiusa il 13. La dashboard è il primo componente
di Kindling che una persona guarda. Il suo lavoro non è mostrare numeri: è
mostrare numeri **insieme a quanto ci si può fidare di ciascuno**, in un momento
in cui quasi nessuno di essi è ancora affidabile.*

## 1. Il vincolo da cui dipende tutto il resto

**La dashboard parla solo con l'API. Mai con Postgres.**

Non è igiene: è il modo di ereditare gratis il perimetro che `api.md` §1 e §4
hanno già costruito — il criterio sulle tabelle leggibili e il ruolo
`kindling_api` che lo rende una garanzia del database. Una dashboard che si
collegasse a Postgres dovrebbe reinventare quel perimetro in un secondo posto,
e due implementazioni d'accordo per caso sono il difetto che questo progetto ha
già corretto due volte.

Il vincolo diventa strutturale in tre modi, in ordine di forza:

1. **Il container della dashboard non riceve nessuna variabile di database.**
   Né `DATABASE_URL` né `API_DATABASE_URL` compaiono nel suo `environment`. Non
   è che "non le usa": non le ha.
2. **`ops/kindling-deploy.sh` lo verifica a ogni deploy**, accanto al controllo
   del perimetro di `kindling_api` che oggi esce con codice 12. Una variabile
   aggiunta per comodità durante un debug e mai tolta è esattamente il genere di
   cosa che nessuno rilegge.
3. La dashboard è un **servizio separato** da `api`, non rotte HTML aggiunte a
   quello esistente. Sembra un'inutile duplicazione — stesso Dockerfile, stesso
   linguaggio — ma `api` è il processo che possiede la credenziale di lettura
   del database, e la dashboard è il processo che sta dietro una porta pubblica.
   Fonderli metterebbe il processo con la credenziale direttamente su internet.

### L'indirizzo dell'API è configurazione, non codice

`KINDLING_API_BASE_URL` in `.env`, letta all'avvio. **Mai un indirizzo scritto
dentro il codice**, nemmeno come default "tanto in produzione è sempre quello".

È la sola differenza tra sviluppo e produzione: puntata al server di fixture
(§7) si sviluppa, puntata a `http://api:8000` si va in produzione, e non cambia
nient'altro. Un indirizzo hardcoded rende il fixture inutilizzabile e quindi
cancella §7 per intero.

### Corollario: l'API non viene mai esposta

La dashboard è **server-rendered**: il browser non chiama mai l'API, le chiamate
partono dal container dashboard sulla rete interna del compose.

Quindi Caddy sta davanti alla **dashboard**, e `api` resta esattamente dov'è
oggi: nessun `ports:`, nessuna autenticazione, nessun certificato. Il
prerequisito di `api.md` §5 — "TLS e autenticazione sono il prerequisito del
momento in cui l'API diventa raggiungibile da fuori" — è soddisfatto non
facendone mai scattare la premessa. La voce corrispondente nel piano di lavoro
non si rimanda: si cancella.

**Il costo futuro, dichiarato adesso.** L'evoluzione prevista da
`architettura.md` §5 (Next.js + Sigma.js) è un frontend che chiama l'API **dal
browser**: la riespone su internet e reintroduce TLS, autenticazione e rate
limiting sull'API. Non è un motivo per non farlo mai. È un costo che va visto
prima di sceglierlo, non dopo.

## 2. Il layer di visualizzazione

`architettura.md` §5 nominava Streamlit come candidato di partenza. La
valutazione build-vs-riuso è stata fatta, non saltata, ed è finita altrove.

**Riuso, valutato e scartato.**

| Candidato | Perché no |
|---|---|
| Grafana + Infinity datasource | L'unico BI maturo che consuma davvero JSON REST. Minimo documentato 512 MB (raccomandato 2-4 GB). Ma il motivo vero è un altro: il suo modello dati è serie+tabelle, senza un posto di prima classe per la qualificazione. Una riga soppressa arriva come `null` e viene disegnata come **buco nel grafico** — "soppresso" diventa indistinguibile da "assente", cioè precisamente ciò che `api.md` §3 ha progettato per evitare. |
| Metabase, Superset, Redash, Datasette | SQL-native: vogliono credenziali di database. Violano §1 alla radice. |
| CHAOSS 8Knot | Dash app legata a GitHub e allo schema Postgres di Augur. Non adattabile. CHAOSS resta il riferimento tassonomico che `architettura.md` già dichiara. |
| Streamlit, Dash, Panel, Marimo, NiceGUI | Non sono riuso: sono framework di rendering. Il confronto si riduce a impronta e aderenza. |

**Misure reali** (finta API con la forma di risposta vera, 144 righe
`quality`/`values`, RSS con una sessione browser reale — non a server inattivo):

| | RSS | Disco |
|---|---|---|
| Streamlit 1.63, nessun client | 58 MB | 472 MB |
| Streamlit, 1 sessione (tabella + line chart) | **181 MB** | |
| Streamlit, 3 sessioni | 181 MB | |
| FastAPI + Jinja2 + httpx, stesso contenuto | **54 MB** | ~15 MB |

Il costo di Streamlit non è nel server: è negli import che arrivano al primo
script run (pandas, altair). Le sessioni successive costano quasi nulla.

**Decisione: FastAPI + Jinja2, server-rendered.** `fastapi` e `uvicorn` sono già
in `requirements.txt`; si aggiungono `jinja2` e `httpx`. Due argomenti, in
ordine di importanza:

1. **Streamlit non può fare il login Discord.** `st.login()` richiede un
   provider **OIDC** con discovery document; Discord è OAuth2 puro, senza
   discovery e senza `id_token`. Restano due strade, entrambe cattive:
   scriversi il flow a mano dentro un framework che rirunna lo script a ogni
   interazione con un widget — un controllo di autorizzazione dentro un loop di
   re-run è la classe di difetto `CLAUDE.md` §7 in forma di sicurezza — oppure
   un auth proxy che parli Discord (Authelia, Authentik: centinaia di MB su una
   macchina da 1 GB). Con FastAPI il controllo è un `Depends()` sulla rotta:
   strutturale, non convenzionale, come il ruolo `kindling_api`.
2. **La somma non ci sta.** Sui ~630 MB misurati il 31/08: `api` 120 + Caddy 30
   + Streamlit 181 lasciano ~300 MB, contro il `mem_limit: 400m` del job
   settimanale. Con la dashboard server-rendered restano ~426 MB e il job sta
   dentro il suo tetto. Il pezzo che verrebbe schiacciato è quello che produce i
   numeri.

**Quello che non si guadagna scegliendo un framework, e va detto.** Le regole di
rendering della §5 sono codice custom in ogni caso: nessuno strumento le regala.
Con Streamlit si scrivono in Python, con Jinja in un template. A parità di
codice da scrivere, 127 MB di differenza non si giustificano.

## 3. Autenticazione e autorizzazione

**Login con Discord OAuth2. Autorizzazione: permesso ADMINISTRATOR sul server
osservato.**

Scope richiesti: `identify guilds`. Nient'altro — in particolare **non**
`guilds.members.read`, che servirebbe solo per leggere i ruoli di un membro.

Il flow: login → Discord → `GET /users/@me/guilds` → per ogni guild la risposta
porta `owner` e `permissions` → si calcola l'insieme delle guild che Kindling
osserva (`GET /guilds` dell'API) **e** su cui l'utente è amministratore → si
scrive quell'insieme nella sessione → **il token Discord si scarta**.

**Perché ADMINISTRATOR e non un ruolo dedicato.** Chi ha ADMINISTRATOR può già
leggere ogni canale del server: dargli anche gli aggregati non gli concede
niente che non potesse ottenere a mano. Un ruolo dedicato avrebbe creato una
tessera nuova da tenere allineata, per di più configurata per guild. Così
l'autorizzazione resta un fatto di Discord e Kindling non ha niente da
configurare: quando una seconda community adotta la piattaforma, i suoi
amministratori funzionano da soli.

**Il costo, dichiarato.** Un community manager che non è amministratore del
server resta fuori, e l'unico modo di farlo entrare è renderlo amministratore —
un permesso molto più grande di "può leggere la dashboard". Per questo il
controllo vive in **una funzione sola**, con oggi un'unica implementazione: il
giorno in cui servirà un ruolo dedicato si cambia quella funzione, non si fa un
refactor.

**Kindling non memorizza niente su chi fa login.** Nessuna tabella utenti,
nessun `author_id` di chi si collega, nessun token conservato. La sessione
contiene l'insieme delle guild autorizzate e una scadenza. Il ruolo relazionale
di chi guarda la dashboard non è un dato che questo progetto ha motivo di avere,
e un dato che non esiste non va protetto.

### Le due scadenze, che proteggono da cose diverse

| | Valore | Da cosa protegge |
|---|---|---|
| **TTL del ricontrollo permesso** | **15 minuti** | Dalla revoca che non fa effetto |
| **Scadenza della sessione** | **8 ore** | Da un cookie rubato o un PC lasciato aperto |

Il TTL è il punto che si sbaglia più facilmente. Togliere ADMINISTRATOR a
qualcuno è proprio la leva con cui gli si toglie l'accesso: se la risposta di
Discord viene chiesta solo al login e la sessione dura settimane, quella persona
continua a leggere i dati della community e **su Discord risulta correttamente
non più amministratore**, quindi la revoca sembra aver funzionato. È §7.

Perché 15 minuti e non gli estremi: a ogni richiesta si pagherebbe la latenza di
Discord su ogni pagina e si andrebbe contro i suoi rate limit; a 24 ore un
giorno intero di accesso dopo la revoca è troppo per l'unico scenario per cui il
controllo esiste. A 15 minuti il costo è ~4 chiamate l'ora per persona
connessa — con una o due persone, niente.

### Tre cose che sbagliate non danno errore

- **`permissions` è una stringa, non un intero.** I bitfield Discord superano
  2^53 e JavaScript non li regge, quindi l'API li serializza come stringa. Il
  controllo è `int(g["permissions"]) & 0x8`. Una conversione dimenticata
  restituisce una risposta sbagliata in silenzio. Si accetta anche
  `g["owner"] is True` accanto al bit: costa nulla e toglie un dubbio.
- **Si nega, non si passa.** Se Discord non risponde, se la risposta non si
  parsa, se l'insieme autorizzato è vuoto: accesso negato con un messaggio
  chiaro. Mai un fallback permissivo "per intanto".
- **Il ricontrollo deve poter fallire.** Se allo scadere dei 15 minuti Discord
  non risponde, la sessione non si prolunga per inerzia: si nega. Un TTL che in
  caso di errore lascia passare è un TTL che non esiste.

### Superficie nuova

`state` sul callback OAuth, monouso e verificato — CSRF, obbligatorio, non un
affinamento. Cookie di sessione `HttpOnly` + `Secure` + `SameSite=Lax`. Nessun
redirect post-login verso una destinazione fornita dal client (open redirect).
Client id, client secret e chiave di firma della sessione in `.env`, mai in git.

**I log.** Uvicorn con access log scrive la query string: sul callback OAuth
significa scrivere il `code` in chiaro dentro `journalctl`. Va disattivato su
quella rotta **prima** del primo login reale, non dopo.

**Fase 2: vedi `dashboard-fase2.md`.** Estende questa sezione con quello che
qui manca o si è rivelato impreciso implementandola: come si ricontrolla il
permesso senza il token (3-quinquies), la scadenza delle 8 ore che deve essere
assoluta e imposta dalla guardia con `login_at` — il `max_age` del signer da
solo è un timeout di inattività (3-quinquies) — e i log, che con Caddy davanti
riguardano due componenti invece di uno (3-quater).

## 4. Le viste, e cosa contengono davvero

Quattro, e nessuna in più per simmetria con gli endpoint.

**Il catalogo di `metriche-aggregate-admin.md` elenca sette metriche. Ne
esistono tre.** Concentrazione strutturale, densità cross-community, reciprocità
e soglie di attenzione non hanno tabella, quindi non hanno endpoint: arriveranno
col job, non con l'API. La dashboard non le mostra e non accenna al fatto che
esisteranno.

### Inventario

Le definizioni stanno in `modello-metriche.md`; qui c'è solo cosa arriva alla
dashboard e quante righe sono.

| Vista | Endpoint | Righe per snapshot | Campi principali |
|---|---|---|---|
| **Stato** | `/guilds/{id}` + `/guilds/{id}/runs` | 1 + 1 | `first_seen_at`, `backfilled_at`, `left_at`, `rejoined_at`, `latest_metrics_as_of`; `params`, `stats`, `code_version` |
| **Robustezza** | `/guilds/{id}/robustness` | 4 layer × 3 frazioni = **12** | `nodes_removed`, `giant_before`, `giant_after_targeted`, `components_after_targeted`, `giant_after_random_mean/sd`, `targeted_excess`, `targeted_z` |
| **Community** | `/guilds/{id}/communities` | 4 layer, + 6 classi di dimensione ciascuno | `community_count`, `modularity`, `modularity_z`, `node_overlap`, `stability_jaccard`, `previous_gap_days`, `communities_born/dissolved/merged/split`; `sizes[]` con `bucket`, `community_count`, `member_count` |
| **Coorti** | `/guilds/{id}/cohorts?limit=1` | per coorte: 2 onboarding + 3 retention, fino a **25** coorti = **125** righe sull'ultimo snapshot | onboarding: `event_count`, `censored_count`, `censored_by_leave`, `median_days_to_k`, `median_reached`, `p25/p75_days_to_k`, `reached_by_14d/28d` — retention: `retained_fraction`, `is_computable`, `not_computable_reason` |

Parametri che fissano quei conteggi, da `job/config.py`: layer `voice / reply /
mention / reaction`; frazioni di rimozione `0,05 / 0,10 / 0,20`; `layer_scope`
delle coorti `any / voice`; `k = 5`; orizzonti di retention `7 / 14 / 28`;
classi di dimensione `small / 5-9 / 10-19 / 20-49 / 50-99 / 100+`; coorti
pubblicate per run, fino a `cohort_max_age_days / 7 ≈ 25,7` → **25** (dato
reale del 14/09/2026). Non è un massimo teorico raramente raggiunto: è il
numero di settimane da metà marzo 2026 a oggi, cioè quante `cohort_start`
cadono dentro la finestra di 180 giorni che `cohort_max_age_days` impone —
**non** quando la community è nata (che questa sessione non ha verificato e
potrebbe essere molto anteriore: `cohort_max_age_days` taglia la finestra
computata, non racconta la storia della guild). I membri con `joined_at` di
marzo sono visibili perché `joined_at` viene dal member-join nativo di
Discord, leggibile anche in backfill — è per questo che sono quasi tutti
"solo sopravvissuti": l'ancora di osservabilità (`first_seen_at`, il bot
osserva dal 28/08) è molto più recente della loro iscrizione.

**Stato è la vista iniziale, non un pannello di servizio.** È l'unica che oggi è
piena, ed è quella che spiega tutte le altre: `first_seen_at` è l'ancora di
osservabilità, cioè la ragione dei caveat che compaiono altrove. `api.md` §2 lo
dice già — servire i caveat senza la ragione dei caveat è peggio che non
servirli. Se `left_at` e `rejoined_at` sono entrambi valorizzati, la vista lo
dichiara in chiaro: c'è un buco di osservazione, e cambia la lettura di ogni
metrica di coorte.

**Coorti non si divide.** Onboarding e retention stanno nella stessa vista,
raggruppate per `cohort_start`, come nella risposta dell'API. Servirle separate
inviterebbe a leggerle separate, che è il modo di fallire contro cui la
duplicazione di `n_effective`/`excluded_rejoins`/`is_survivors_only` esiste.

### La vista Robustezza, in dettaglio

**La domanda a cui risponde.** Una sola: *la connettività di questa community
dipende da pochi connettori?* Il numero che la risponde è `targeted_excess`
(`modello-metriche.md` §3.4); tutto il resto della riga è il modo in cui quel
numero si è formato, e sta lì perché non ci si fidi alla cieca.

**Perché è la prima delle tre che restano**, e non perché sia la più facile: è
l'unica che oggi ha **tutti i valori popolati** (§6). È quindi l'unica su cui la
decisione centrale di §5 — *il numero non significativo si mostra,
dequalificato* — si può giudicare su dodici celle di dati veri invece che sul
fixture.

**Rotta e dati.** `GET /guilds/{guild_id}/robustezza`, che chiama
`/guilds/{guild_id}/robustness?limit=12`. Il `limit` conta **snapshot, non
righe** (il docstring di `api/db.py:fetch_robustness` lo dichiara e la query lo
fa): dodici snapshot sono dodici settimane, e ognuno porta le sue 12 righe. Serve
un metodo nuovo su `ApiClient` — `robustness()`, con
`TypeAdapter(list[RobustnessRow])` e le stesse tre eccezioni degli altri.

**Navigazione.** È la seconda vista, quindi serve il modo di passare da una
all'altra: una barra in `base.html` con le viste **che esistono**. Community e
Coorti non compaiono finché non sono costruite: una voce disabilitata è una
promessa, e §11 dice che di ciò che non c'è non si accenna.

**Tre assenze che non sono la stessa cosa**, e che la vista deve distinguere
come §6 distingue le quattro:

- guild non osservata → 404 dall'API, pagina `non_osservata.html`. Già gestito.
- guild osservata, **nessuno snapshot ancora**: l'API risponde `[]` (`api.md`
  §2). Non è un errore e non è una soppressione: è *il calcolo non è ancora
  girato*. Frase propria, nessun simbolo di qualificazione.
- l'API non risponde → `errore.html`. Aspetto diverso da tutto il resto,
  regola 1.

#### Layout: quattro blocchi, uno per layer

Non una tabella da dodici righe con una colonna `layer`. La robustezza è
definita **per layer e mai su un grafo fuso** (`modello-metriche.md` §2.1), e una
tabella unica offre una colonna che attraversa i quattro layer — cioè invita
esattamente alla lettura che l'invariante 3 vieta. Quattro blocchi separati la
rendono scomoda per costruzione: è l'invariante 3 tradotto in layout invece che
in una nota a piè di pagina.

Ogni blocco porta il nome del layer, `quality.n_effective` **una volta sola** —
è la dimensione del grafo di quel layer, la stessa per le tre frazioni — e tre
righe, una per frazione:

| rimozione | nodi rimossi | gigante prima | gigante dopo, mirata | gigante dopo, a caso | componenti dopo | eccesso mirato | z |

`eccesso mirato` è `targeted_excess` e va messo in evidenza: è la risposta, le
altre colonne sono il procedimento che ci porta.

**`z` è l'unica colonna che può mancare su una riga pubblicata.** `targeted_z` è
`None` quando la deviazione standard del baseline è zero
(`modello-metriche.md` §3.4): il grafo è così piccolo o così regolare che ogni
rimozione casuale dà lo stesso risultato. È l'esito `ASSENTE` di `cella()` — "non
disponibile" — e **non** il simbolo di soppressione, che significa un'altra cosa.
La riga resta non significativa per `degenerate_baseline`, e tutte le altre
colonne restano leggibili.

`n_effective` si legge da una riga non soppressa del blocco. Se **tutte** le
righe del blocco sono soppresse, `n_effective` è `None` su tutte e il blocco
mostra la sola soppressione: non c'è una dimensione da dichiarare. Che le tre
righe di un (snapshot, layer) condividano `n_effective` è vero per costruzione —
la soglia di cardinalità guarda lo stesso `n` — ma è un'assunzione della vista,
non una garanzia del contratto: va **verificata da un test**, non data per buona.

**Un layer può mancare del tutto, e non è nessuno dei cinque stati.**
`compute_robustness` restituisce `None` su un grafo vuoto (`job/robustness.py`:
"una riga di zeri direbbe *rete perfettamente frammentata*, che è un'altra
cosa"), quindi un layer senza archi in quella settimana **non produce righe** e
non arriva all'API. Le 12 righe per snapshot dell'inventario di §4 sono un
massimo, non un fatto: su una community da 14 nodi è del tutto plausibile che
`voice` o `reaction` siano vuoti per una settimana intera.

Il blocco di quel layer si mostra lo stesso, con la propria frase: *nessuna
interazione di questo tipo in questa settimana*. Non con il simbolo di
soppressione — che significa "il dato c'è e non si può mostrare" — e non
omettendo il blocco, che farebbe sparire dalla pagina la differenza tra un layer
spento e un layer che nessuno ha calcolato. È la stessa distinzione tra i quattro
stati di §6, applicata al livello del layer invece che a quello della vista.

**Regola 6, in questo layout.** `rimozione` e `nodi rimossi` sono colonne
**adiacenti**, e `removal_fraction` non compare mai da sola: né come intestazione
di un blocco, né come etichetta di una serie, né in un titolo. La regola **non
può** essere imposta da `cella()`: `removal_fraction` è un campo della riga, non
di `RobustnessValues`, e `cella(row, "removal_fraction")` solleva `KeyError` di
proposito. È quindi un invariante di template, e si verifica con un test sul
markup prodotto, non con un test sul motore.

#### La serie, e perché sta nella stessa fetta

`targeted_excess` per snapshot: **un grafico per layer**, con le tre frazioni
come tre serie dentro lo stesso grafico. Tre serie nello stesso riquadro sono
confronti dentro un layer, non tra layer: l'invariante 3 resta intatta.

**Qui la regola 4 si applica per la prima volta.** Con meno di tre punti si
mostra la tabella, e oggi gli snapshot sono due: **in produzione si vedrà una
tabella**, e il primo grafico comparirà il 21 settembre. Il codice del grafico si
scrive lo stesso adesso e si esercita sulla guild `…002` del fixture, che ha
dodici settimane. Un percorso di rendering che comparisse per la prima volta in
produzione, da solo, quando nessuno sta guardando, è la classe di difetto di
`CLAUDE.md` §7 — la stessa ragione per cui il fixture esiste.

**La tabella della serie non si mostra quando duplicherebbe il blocco.** Con un
solo snapshot la ricaduta della regola 4 conterrebbe le stesse identiche celle
della tabella del blocco, incolonnate diversamente: due volte lo stesso dato, e
la seconda volta senza aggiungere niente. La tabella della serie compare da due
snapshot in su.

**Il grafico è SVG generato dal template, server-side.** Nessun JavaScript,
nessuna libreria, nessuna risorsa **da terzi**: la dashboard sta dietro un
tunnel SSH e non deve dipendere da internet per rendersi. Un grafico che ha
bisogno di una CDN è un grafico che un giorno non si disegna.

Dal 21/09/2026 «nessuna risorsa esterna» non vuol più dire «`base.html` non
carica niente»: il foglio di stile è uscito dal `<style>` in linea e sta in
`/static/dashboard.css`, servito **dalla stessa applicazione** — non da Caddy,
che legge dalla copia del repo sulla droplet e fra il `git pull` e il rebuild
servirebbe CSS nuovo con template vecchi. Il divieto che resta intero riguarda
le **origini altrui**: niente CDN, font remoti, analytics o widget, e la CSP
delle risposte (`default-src 'none'`, senza `'unsafe-inline'`) lo impone invece
di raccomandarlo.

**Regola 6 sulla legenda.** Una serie è fatta di righe con `nodes_removed`
diversi, quindi la coppia "stessa riga" non è applicabile alla legenda. La voce
di legenda porta la percentuale **e l'intervallo dei nodi rimossi su quella
serie**: `5% · da 1 a 3 nodi rimossi`, e `5% · 1 nodo rimosso` quando
l'intervallo è costante. È l'intervallo a soddisfare la regola 6, non il
`<title>` del singolo punto: un `<title>` si vede solo passandoci sopra, e
"accanto" non significa "a richiesta". Il `<title>` per punto — percentuale e
`nodes_removed` della **sua** riga — si mette lo stesso, come dettaglio in più.

**L'asse y non è ancorato a zero.** `targeted_excess` può essere negativo e non è
clampato (§5, nota di rendering): il dominio è `[min(valori, 0), max(valori, 0)]`
con lo zero disegnato come riferimento. Ancorare l'asse a zero nasconderebbe
proprio il caso in cui i nodi più centrali si sono rivelati meno critici di nodi
presi a caso, che è un risultato e non un errore.

### La vista Community, in dettaglio

**Le domande a cui risponde sono due, non una.** Robustezza ha un solo numero
che risponde alla sua domanda (`targeted_excess`) e tutto il resto è
procedimento. Community no: *questo layer è organizzato in gruppi distinguibili
dal rumore?* (`community_count`, `modularity`, `modularity_z`) e *quei gruppi
sono gli stessi di sette giorni fa, o si sono ricomposti?*
(`node_overlap`, `stability_jaccard`, `communities_born/dissolved/merged/split`)
sono domande diverse, con risposte che possono divergere — e nei dati del
15/09/2026 divergono davvero: `voice`@12 ha una struttura ben distinguibile dal
caso (`modularity_z = 5,624`) **e** una stabilità misurabile
(`stability_jaccard = 0,333`), ma la riga è comunque `is_significant = false`,
per un motivo che non ha niente a che fare né con l'una né con l'altra (sotto,
"L'esempio `voice`@12"). Le due domande non si fondono in una — sarebbe la
stessa violazione dell'invariante 3 che tiene separati i quattro layer, un
livello più in basso — e il layout le tiene visivamente vicine ma distinte
nella stessa riga, mai in un unico numero.

**Correzione (16/09/2026, durante l'implementazione): `/communities` ha un
`limit` esattamente come `/robustness`, non "nessuno".** La prima stesura di
questo paragrafo affermava un fatto sull'endpoint senza averlo letto —
esattamente l'errore di metodo che questo documento esiste per evitare, questa
volta commesso da chi scrive la spec, non da chi la legge. **Verificato in
`api/main.py::list_communities`**: usa la stessa dipendenza condivisa
`limit_param` di `list_robustness`, `list_runs` e `list_cohorts`
(`DEFAULT_LIMIT = 12`, `MAX_LIMIT = 200`, `api/config.py`). **Verificato in
`api/db.py::fetch_communities`/`fetch_community_sizes`**: stessa query con
`run.snapshot_id IN ({_LATEST_SNAPSHOTS})` di `fetch_robustness` — il limite
conta **snapshot, non righe**, identico in forma e in valore di default.
Nessun endpoint dell'API accetta `snapshot_id`: non esiste una rotta per
chiedere uno snapshot specifico, né qui né altrove.

**Rotta e dati.** `GET /guilds/{guild_id}/community`, che chiama
`/guilds/{guild_id}/communities?limit=12` — **una sola chiamata**, esattamente
come Robustezza chiama `/robustness?limit=12` una sola volta. La risposta
porta fino a 12 snapshot × 4 layer (con `sizes[]` annidato per riga) in un
unico payload, e **la stessa risposta alimenta sia i blocchi "oggi" sia i due
grafici della serie**: i blocchi leggono le righe dello `snapshot_id` più
recente (il primo per `layer` nell'ordinamento `ORDER BY run.as_of DESC,
c.layer` della query), la serie raggruppa le stesse righe per layer su tutti
gli snapshot restituiti. Non serve una seconda chiamata per la serie, e non
serve un parametro nuovo sull'API: la spec precedente inventava un
`snapshot_id` esplicito che non esiste, per un problema che il `limit`
condiviso risolve già. Serve un metodo nuovo su `ApiClient` — `communities(guild_id,
limit=12)`, sulla stessa forma di `robustness()` — con
`TypeAdapter(list[CommunityRow])` e le stesse tre eccezioni degli altri.

**Le tre assenze** sono le stesse di Robustezza, sopra: guild non osservata →
404 → `non_osservata.html`; guild osservata, nessuno snapshot → `[]`, frase
propria, nessun simbolo di qualificazione; API che non risponde → `errore.html`.
Non si ripete la spiegazione qui.

#### Layout: quattro blocchi, uno per layer, una riga sola

Come Robustezza, un blocco per layer — l'invariante 3 vale identica. A
differenza di Robustezza, dentro il blocco **non ci sono tre righe**: ogni
`(snapshot, layer)` produce **una** riga di `metric_communities`, non una per
frazione di rimozione. Il blocco è quindi una tabella a riga singola, larga,
seguita dalla distribuzione delle dimensioni come tabella propria (sotto).

La riga, in ordine di lettura, con le colonne raggruppate visivamente in due
metà — non due tabelle separate, perché sono la stessa riga e vanno lette
insieme, ma nemmeno indistinguibili, perché rispondono a domande diverse:

**Metà struttura**: `community` (`community_count`) · `modularità`
(`modularity`) · `modularità attesa` (`modularity_random_mean`) · `z`
(`modularity_z`).

**Metà stabilità**: `gap` (`previous_gap_days`, in giorni) · `sovrapposizione`
(`node_overlap`) · `stabilità` (`stability_jaccard`) · `nate` · `dissolte` ·
`fuse` · `scisse` (`communities_born/dissolved/merged/split`).

`nodi` (`quality.n_effective`) sta prima di entrambe le metà, una volta sola
per riga — non due, anche se sia la modularità sia la stabilità dipendono da
`n` — con lo stesso significato di `n_effective` in Robustezza: `graph.vcount()`
dello stesso grafo (`modello-metriche.md` §2.5, "Robustezza e Leiden
condividono lo stesso grafo"). **Verificato nel codice**: `job/metrics.py`
costruisce `graph` una volta per `(snapshot, layer)` e lo passa sia a
`compute_robustness` sia a `compute_communities` — non è un'assunzione di
layout come lo era in Robustezza (dove andava verificata riga per riga), qui è
strutturale: non esistono due `n` da poter divergere.

`modularità_random_sd` **non è una colonna**. Stessa scelta di Robustezza per
`giant_after_random_sd`: mostrarla renderebbe `z` ricavabile dalle altre
colonne visibili e lo farebbe entrare nel gruppo di precisione aritmetica
(sotto) — cosa che Robustezza evita deliberatamente escludendo `z` dal gruppo.
`previous_snapshot_id` **non è una colonna**: `api/models.py` lo dichiara
opaco — "il consumatore non può risolverlo" — e senza un modo di risalire a
una data che non sia già `previous_gap_days`, mostrarlo non aggiungerebbe
niente che il gap non dica già.

**L'etichetta di riga è una sola, e vive alla fine della riga struttura+stabilità.**
Non due, una per metà: `is_significant` è un solo flag su un solo `CommunityRow`,
e mostrarne due (una "non significativo" sopra i dati di struttura, una sotto
quelli di stabilità) suggerirebbe due giudizi indipendenti che i dati non
supportano — il flag è uno, l'ha scritto una sola valutazione di `reasons`
(`job/communities.py::compute_communities`). La distribuzione delle dimensioni,
sotto, ha le proprie etichette per riga, indipendenti da questa: vedi "La
distribuzione delle dimensioni".

#### `z` e i tre campi del confronto possono mancare su una riga pubblicata: tre stati, non uno

**`modularity_z` può essere `None` su una riga pubblicata.** Succede quando il
baseline è degenere, e non è un solo caso ma due (**correzione
16/09/2026, trovata durante l'implementazione**: la prima stesura citava solo
il primo). `job/communities.py::compute_communities` pone `modularity_z =
None` quando `random_mean is None` **o** `random_sd` è falso (zero):
`_modularity_baseline` restituisce `random_mean = None` con meno di due archi
(`graph.ecount() < 2`, il rewiring non ha nulla da scambiare), ma può anche
restituire un `random_mean` valido con `random_sd == 0` — le ripetizioni del
rewiring danno tutte la stessa modularità per caso. **Non** è lo stesso
evento di `baseline_degraded`: quel flag (`job/config.py::baseline_repetitions_for`)
riduce le ripetizioni a 20 sopra i 500 nodi, mai a una sola, e con i parametri
attuali le ripetizioni sono sempre 100 o 20 — il ramo a una sola ripetizione
di `_modularity_baseline` (`len(values) == 1` → `sd = 0.0` per costruzione)
esiste nel codice ma nessun parametro attuale lo raggiunge. In entrambi i casi
il motivo resta lo stesso, `degenerate_baseline` in `reasons`: la distinzione
tra "niente da scambiare" e "scambiato ma sempre uguale per caso" non è
contrattuale, e la vista non deve provare a distinguerle. È lo stesso esito `ASSENTE` di `targeted_z` in
Robustezza, sopra: non il simbolo di soppressione, che significa un'altra
cosa, e la riga resta leggibile per il resto — `community_count` e
`modularity` restano veri anche quando `z` non lo accompagna.

**`previous_gap_days`, `node_overlap`, `stability_jaccard` e i quattro conteggi
possono essere `None` tutti insieme, sulla PRIMA riga confrontabile.** Succede
quando non esiste (ancora) uno snapshot precedente comparabile per quella
guild — non solo "nessuno snapshot prima", ma anche parametri diversi,
parametri vuoti, o finestra di ampiezza diversa (`modello-metriche.md` §4.6,
`job/main.py::_previous_partition` via `db.fetch_previous_snapshot` →
`snapshot_comparability`). **Verificato nel codice, e la spec di
`modello-metriche.md` §4.6 va corretta**: il testo lì elenca tutti e cinque i
casi — nessuno snapshot precedente, parametri diversi, parametri vuoti,
finestra diversa, `node_overlap` sotto soglia — come se producessero tutti
`is_significant = false`. Non è così. `job/communities.py::compute_communities`
tocca `reasons` (da cui deriva `is_significant`) **solo** nel ramo
`node_overlap < min_node_overlap`; il ramo `previous is None` — che copre gli
altri quattro casi, indistintamente — scrive `details["stability_unavailable"]`
ma non aggiunge nessuna `reason`. Una riga sulla sua primissima osservazione
comparabile, con `n ≥ 30` e `modularity_z ≥ 2,0`, è `is_significant = true`
anche senza nessuno snapshot precedente: l'assenza di un confronto non è, di
per sé, un motivo di non significatività strutturale. Nei dati di produzione
di oggi questo non si vede ancora perché tutti e quattro i layer hanno anche
`n < 30` allo snapshot 11 (quindi `is_significant = false` comunque, per
`too_few_nodes`) — ma lo snapshot successivo a un cambio di parametri, o la
prima riga di un layer nuovo sopra soglia, lo eserciterà, ed è un percorso che
il fixture deve saper generare (vedi il prompt per Claude Code).

Questa è una correzione alla specifica esistente, non solo un chiarimento per
la dashboard: `modello-metriche.md` §4.6 andrebbe aggiornato per dire che è
**solo** `node_overlap < min_node_overlap` a rendere una riga non
significativa a causa del confronto; gli altri quattro casi rendono `NULL` i
soli campi del confronto, senza toccare `is_significant`. Non lo tocco in
questa sessione — non è tra i documenti che il compito chiede di scrivere — ma
va segnato come voce aperta (`stato-progetto.md` §7, stesso formato delle voci
I e J).

**Sulla riga, questo produce tre stati distinguibili senza mai leggere
`details`:**

| Stato | Come si riconosce (colonne, non `details`) | Cosa significa |
|---|---|---|
| Prima osservazione comparabile | `previous_gap_days` è `None` | Non c'è ancora un precedente valido con cui confrontarsi. Non implica `is_significant = false`: vedi sopra. |
| Confronto fatto, popolazione troppo cambiata | `previous_gap_days` **e** `node_overlap` presenti, `stability_jaccard` assente | Il precedente c'era, ma meno di metà dei nodi è in comune (`node_overlap < 0,50`): il confronto non riguarda più abbastanza la stessa popolazione, e **questa** riga è `is_significant = false` per costruzione (`node_overlap_below_minimum` è nei `reasons`). |
| Confronto fatto, stabilità calcolata | tutti e tre presenti | La community di oggi è confrontabile con quella di sette giorni fa, e il numero dice quanto si somiglia. |

Il secondo e il terzo stato **si distinguono guardando `node_overlap`
direttamente** — non serve una parola nuova, e non serve `details`: se
`node_overlap` c'è ed è basso, il lettore vede perché `stability_jaccard`
manca senza bisogno di un'etichetta che lo dica. È lo stesso meccanismo della
regola 6 su `nodes_removed`: il numero che disinnesca la lettura è già sulla
riga.

#### Regola 5 estesa: `node_overlap` viaggia con `stability_jaccard` quanto `previous_gap_days`

La regola 5 di §5 oggi dice solo "`stability_jaccard` non si mostra mai senza
`previous_gap_days` accanto". È incompleta per Community: sui dati reali del
15/09, `previous_gap_days = 6,823` compare su **tutti e quattro** i layer allo
snapshot 12, ma `stability_jaccard` è popolata solo su `voice`. Mostrare
`stability_jaccard` assente accanto solo al gap (`6,823`, un numero che dice
"quasi una settimana", cioè "il confronto dovrebbe essere buono") senza il
`node_overlap` che lo smentisce (`0,196` su `mention`, `0,361` su `reaction`,
`0,237` su `reply`) farebbe sembrare l'assenza un difetto del sistema invece
che un fatto sulla popolazione. **I tre campi — `previous_gap_days`,
`node_overlap`, `stability_jaccard` — viaggiano insieme, sempre**: dove uno dei
tre è mostrato, gli altri due sono in vista, anche quando sono `None`. Va
scritto in §5 come estensione della regola 5, non come regola nuova — è lo
stesso principio applicato a un terzo campo che l'inventario originale non
aveva ancora incontrato.

`node_overlap` e `stability_jaccard` **non condividono la precisione per la
regola del gruppo aritmetico** (§5, "Le colonne legate da un'operazione"):
`stability_jaccard` non è calcolato a partire da `node_overlap` — sono due
uscite indipendenti di `compare_partitions` (`job/communities.py`), non un
numero e la sua differenza. Che nei dati reali condividano oggi la stessa
precisione (tre decimali: `0,333`, `0,196`, `0,361`, `0,237`, `0,500`) è una
scelta di formattazione comune alle due colonne "frazione su [0,1]", non un
obbligo derivato dalla regola del gruppo.

#### L'esempio `voice`@12: `is_significant = false` non vuol dire "niente qui è vero"

Stessa lezione di `too_few_nodes_removed` in Robustezza e di `median_reached`
nelle coorti, un terzo campione della stessa classe di errore di lettura.
`voice`@12: `n = 15`, `modularity_z = 5,624` (ben oltre `min_modularity_z =
2,0`, quindi la struttura **è** distinguibile dal rumore), `node_overlap =
0,500` (esattamente al minimo — non **sotto**, quindi `stability_jaccard` **si
calcola**: `0,333`), un `merge` (una community precedente assorbita in una
nuova). Eppure `quality.significant` è `false`, per l'unico motivo
`too_few_nodes` (15 < 30). Leggere questa riga come "non ci si può fidare di
niente qui" sarebbe sbagliato quanto leggere `median_reached = false` come
"meno di metà della coorte" (§5, sul sesto stato): la riga dice con precisione
*quale* garanzia manca — la dimensione del campione — non che il segnale non
esista. `modularity_z` e `stability_jaccard` restano leggibili e vanno
mostrati come tali: dequalificati dall'etichetta di riga come tutto il resto
(regola generale, §5), ma **non** nascosti, e non è compito della dashboard
inventare una seconda etichetta più fine di quella che l'API restituisce — il
flag è uno, la lettura corretta sta nel non fermarsi all'etichetta.

#### La distribuzione delle dimensioni: la tabella ha tante righe quante le classi popolate, non sei fisse

`sizes[]` (`CommunitySizeBucket`) non è un elenco a sei righe (`small`, `5-9`,
`10-19`, `20-49`, `50-99`, `100+`) sempre presenti: `job/communities.py::
size_buckets` scrive **solo** le classi con almeno una community dentro.
`reaction`@12 ha una riga sola (`5-9`, 4 community, 25 membri): non mancano
cinque righe, non esistono community fuori da quella classe quella settimana.
Renderizzare sei righe fisse con "—" su quelle vuote inventerebbe una
distinzione (bucket vuoto) che i dati non fanno: un bucket **vuoto** e un
bucket **soppresso** sono cose diverse, e trattarli con lo stesso trattino li
confonderebbe.

**Ogni riga di bucket porta il proprio stato di soppressione**, indipendente
dalla riga madre: `quality.suppressed` / `suppression_reason` (`below_threshold`
o `secondary`). **Non `quality.significant` e non `quality.n_effective`: sono
sempre `None` su queste righe, per costruzione, non per un buco.** Doppia
verifica, non solo di lettura:

- **Nello schema**: `metric_community_sizes` non ha una colonna
  `is_significant` (migration `0006`, `CREATE TABLE metric_community_sizes`),
  e `CommunitySize` (`job/communities.py`) non ha un campo `n_effective` — solo
  `member_count`. Il commento della migration lo dice esplicitamente: *"E'
  l'n_effective di questa riga: i membri complessivi del bucket"* — riferito a
  `member_count`, non a una colonna chiamata `n_effective`. La numerosità del
  bucket **esiste**, ma vive in `values.member_count`, non in
  `quality.n_effective`.
- **Nel codice che assembla la risposta**: `api/assemble.py::communities`
  chiama `_quality(size)` sulla riga di `metric_community_sizes`, e `_quality`
  legge `row.get("n_effective")` e `row.get("is_significant")` — entrambi
  assenti da quella riga, quindi entrambi sempre `None`.

Dashboard.md §5 lo dice già in prosa ("è quella riga [madre] a portare
`modularity_z` e il proprio `is_significant`; il bucket ha solo la propria
soppressione") — questa sessione lo conferma su schema e codice, non solo su
prosa: **non è un buco da segnalare come voce aperta, è una scelta corretta e
già implementata.** La numerosità del bucket per la UI è `values.member_count`,
mai `quality.n_effective` — che su queste righe non va nemmeno letto.

**Colonne**: `classe` (`bucket`) · `community` (`community_count`) · `membri`
(`member_count`) · etichetta di riga (soppressione, quando presente).

#### La soppressione a cascata: un esempio reale, `reaction`@11

`reaction`@11 ha 3 community (`community_count = 3`): una da 12 membri
(classe `10-19`, pubblicata), e altre due sotto soglia. La classe `small` va
sotto soglia primaria (`below_threshold`); a quel punto **resta una sola altra
cella non soppressa** (`5-9`) accanto al totale pubblicato di riga
(`n_effective = 24`), e quella cella si ricava per differenza — la soppressione
primaria non avrebbe protetto niente. `apply_secondary_suppression`
(`job/suppression.py`) sopprime allora anche `5-9`, con motivo `secondary`. La
riga che arriva alla dashboard ha **una sola** classe pubblicata (`10-19`) e
due soppresse con motivi diversi (`below_threshold`, `secondary`) — entrambe
mostrate con lo stesso simbolo di soppressione (§5, "Soppresso": mai una cella
vuota, mai la riga nascosta), ma il motivo testuale può distinguerle, perché
`suppression_reason` è colonna tipizzata, non `details`.

**La vista non deve mai sommare i bucket visibili per dedurre quanto vale un
bucket nascosto, né mostrare "il resto è N".** `community_count` di riga
(`3`) meno quello pubblicato (`1`) darebbe `2` — la stessa aritmetica che la
soppressione secondaria esiste apposta per impedire al lettore di fare a
mano. Farla al posto suo nel template sarebbe ricostruire esattamente
l'aggregato sotto soglia che l'invariante 1 del progetto vieta.

#### La serie: due grafici per layer, non uno

**Correzione (16/09/2026, durante l'implementazione): il grafico di
`stability_jaccard` non ha un riferimento a `0,50` — non esiste nel job.** La
prima stesura di questo paragrafo dava a `stability_jaccard` un riferimento
orizzontale allo stesso modo di `modularity_z`, senza verificare che fosse
sorgente da un parametro reale. Non lo è: **verificato in `job/config.py`**, i
soli due valori `0,50` di `MetricParams` sono `min_node_overlap` — soglia su
`node_overlap`, una quantità diversa, non su `stability_jaccard` — e
`merge_min_share`, usato in `compute_communities` (riga con
`share >= params.merge_min_share`) per decidere se una community nuova
discende per fusione da una vecchia, un calcolo interno che non tocca
`stability_jaccard` né la sua significatività. Una linea a `0,50` sul grafico
letterebbe come "sopra = stabile": un giudizio che il job non fa mai, ed è
esplicitamente fuori fase (§11, "Nessun alerting e nessuna soglia di
attenzione"). `modularity_z` è diverso: il suo `2,0` **è** sorgente
(`min_modularity_z`, la soglia che entra davvero in `is_significant`).

`targeted_excess` di Robustezza è un solo numero su una scala; Community ne ha
due, su scale diverse (`modularity_z` è uno z-score, può essere negativo,
tipicamente tra -2 e +6 nei dati fin qui; `stability_jaccard` è una frazione
in `[0,1]`). Combinarli in un solo grafico a doppio asse nasconderebbe la
differenza di scala dietro una convenienza di layout — lo stesso errore, in
forma diversa, che l'invariante 3 vieta tra layer. **Due grafici per layer**,
uno per `modularity_z` (riferimento orizzontale a `2,0`, la soglia di
significatività — lo stesso genere di riferimento dichiarato che Robustezza
usa per lo zero di `targeted_excess`) e uno per `stability_jaccard`, **senza
riferimento orizzontale**: solo l'asse `[0, 1]` e lo zero come estremo, non
come soglia. Entrambi seguono la regola 4 (tre punti disegnabili minimo,
altrimenti tabella) e la regola dell'asse non ancorato a zero dove serve:
`modularity_z` può essere negativo (`mention`@11: `-1,009`), quindi il suo
asse segue la stessa regola di `targeted_excess`; `stability_jaccard` è
sempre in `[0,1]`, quindi il suo asse **può** ancorarsi a zero senza nascondere
niente — ma l'ancoraggio a zero è una scelta di leggibilità dell'asse, non un
riferimento dichiarato: nessuna linea, nessuna etichetta che suggerisca una
soglia.

**Oggi, con due snapshot, entrambi i grafici sono tabelle**, per la stessa
regola 4 già esercitata da Robustezza. Il codice si scrive e si esercita sul
fixture `…002` (dodici settimane), che varia sia `modularity_z` (`5,5` in
discesa di `0,1` a settimana su `reply`/`mention`/`reaction`, fisso a `2,6` o
`1,4` su `voice` a seconda della soglia dei 30 nodi) sia `stability_jaccard`
(`0,74` in discesa con rumore) apposta per esercitare una serie vera, non
piatta.

#### Il terzo caso che Robustezza non aveva: un valore assente su una riga pubblicata, dentro la serie

§5 dice, per `targeted_excess`: *"Non esiste un terzo caso. Su una riga
pubblicata `targeted_excess` non è mai `None`."* È vero per quel campo, non è
una legge generale — e per `stability_jaccard` è falso: una riga pubblicata,
non soppressa, **può** avere `stability_jaccard = None` (prima osservazione
comparabile, o `node_overlap` sotto soglia). Ai fini del **disegno della
serie**, questo terzo caso si comporta come un punto soppresso: non c'è un
numero da disegnare, quindi il punto non si disegna e la linea si interrompe
visibilmente — le stesse due frasi di §5 sul perché (disegnare uno zero o
saltare il punto congiungendo i vicini sono due modi di inventarlo). **Non**
riceve però il simbolo di soppressione: quello è un fatto della *cella* nella
tabella-riga, non del *grafico* — la riga non è soppressa, solo quel valore
manca, ed è la stessa distinzione tra `ASSENTE` e soppresso che vale ovunque
in questo documento. La serie di `modularity_z`, al contrario, ha lo stesso
comportamento di `targeted_z` in Robustezza: manca solo su baseline degenere
(`graph.ecount() < 2` **o** `random_sd == 0` — sopra, "tre stati, non uno"),
un caso limite quanto lì.

#### I rami difensivi

Si aggiungono ai tre già elencati in §5 ("I rami difensivi, e perché non si
provano"), stessa logica e stesso trattamento — nel codice, non nella
fixture:

- `n_effective` diverso tra le colonne struttura e stabilità della stessa
  riga — non può succedere, è lo stesso `graph.vcount()` letto una volta sola
  (sopra), ma il template non deve *assumerlo* senza un modo di accorgersene
  se smettesse di essere vero;
- `stability_jaccard` valorizzato senza `node_overlap` — il codice di
  `compute_communities` lo rende impossibile (`stability_jaccard` si scrive
  solo dentro il ramo che ha appena scritto `node_overlap`), ma è un invariante
  della vista da verificare con un test sul markup, come già per la regola 6 di
  Robustezza.

### La vista Coorti, in dettaglio

**Le domande a cui risponde sono due, come in Community, ma qui sono legate da
una popolazione condivisa invece che da un layer condiviso.** *Con quanta
rapidità un nuovo membro si integra?* (`event_count`, `median_days_to_k` con
`median_reached`, `p25/p75_days_to_k`, `reached_by_14d/28d`) e *quanti restano?*
(`retained_fraction` con `is_computable`) sono domande diverse — integrazione
rapida e permanenza possono divergere, ed è precisamente l'incrocio che la
metrica esiste per misurare (`modello-metriche.md` §5.5: "l'intero senso della
metrica è incrociare 'quanto in fretta si sono integrati' con 'quanti sono
rimasti'"). Non si fondono in un numero solo, e non si separano nemmeno in due
viste: `api/assemble.py::cohorts` le raggruppa già per `cohort_start` proprio
perché leggerle separate è "il modo di fallire" che l'invariante di
`n_effective`/`excluded_rejoins`/`is_survivors_only` duplicati esiste per
impedire (già in questo documento, sopra, "Coorti non si divide").

#### Rotta e dati, e perché qui `limit` non è 12

`GET /guilds/{guild_id}/coorti`, che chiama **`/guilds/{guild_id}/cohorts?limit=1`** —
non `?limit=12` come Robustezza e Community. È una scelta, non un'omissione, e
va giustificata perché rompe un pattern che le due viste precedenti hanno reso
familiare.

`limit` conta snapshot (stessa `_LATEST_SNAPSHOTS` di `api/db.py`, verificato
sopra per Robustezza e Community), e su Robustezza/Community ogni snapshot
porta lo stesso numero fisso di righe (12, o 4 più le classi di dimensione):
`limit=12` costruisce dodici punti comparabili di una manciata di serie. Sulle
coorti la stessa richiesta costruirebbe qualcosa di diverso: **125 righe per
snapshot** (fino a 25 coorti × 5 righe, verificato sopra) moltiplicate per
dodici — oltre 1.500 righe in un payload solo, per una vista che oggi non ha
bisogno di nessuna di quelle dodici copie.

**Un'ipotesi scartata eseguendo, non per intuito: "una coorte matura ha una
curva già completa, quindi le run successive la ripetono identica" è
falsa.** L'ho scritta come prima giustificazione di questa scelta, e
l'esecuzione l'ha smentita subito — lo stesso meccanismo che questo documento
chiede di applicare al codice del job vale anche alle proprie affermazioni
scritte in questa sessione. `reached_by_28d` non dipende da "quanti giorni
sono passati dalla maturità" ma da `max_observed`, il tempo osservato del
membro *più vecchio* della coorte: finché `max_observed < 28`,
`reached_by_28d` resta `None` anche a zero eventi, e diventa `0.0` non
appena `as_of` porta `max_observed` oltre 28 — un valore che **cambia**, da
un `as_of` all'altro, senza che nessun membro abbia fatto niente di nuovo.
Verificato sulla coorte del 10/08 del fixture: `reached_by_28d` è `None`
allo snapshot 11 (`max_observed ≈ 27,6` giorni) e `0,0` allo snapshot 12
(`max_observed ≈ 34,6` giorni) — stessi tre membri, stessi zero eventi, due
risposte diverse. Una coorte può continuare a cambiare ben oltre i 14 giorni
di maturità, finché non tutti i membri hanno raggiunto `k` o sono usciti.

**La ragione vera per `limit=1` non è che le coorti sono piatte: è che 25
fatti indipendenti che evolvono lentamente e in modo prevalentemente
monotono (da `None` a un numero, quasi mai indietro) non sono la stessa cosa
di poche grandezze comparabili su una manciata di snapshot.** Un grafico a
dodici punti per coorte moltiplicherebbe per 25 un tipo di lettura — "come
cambia nel tempo questo numero" — che qui non è la domanda: la domanda per
una coorte immatura è "quando matura" (`is_mature`/`observation_days` sulla
riga attuale bastano), e per una coorte matura è "cosa dice oggi", non "come
ci è arrivata". Se in futuro servirà seguire una coorte specifica settimana
per settimana — legittimo, e questa sessione lo lascia esplicitamente aperto
invece di escluderlo per sempre — è una vista a sé (una coorte, molti
snapshot), non un'estensione di questa: fuori da questa sessione, e non è
tra i deliverable richiesti.

Questo non è un'analogia con le viste precedenti: è la ragione per cui qui
l'analogia **non regge**, ed è precisamente il tipo di verifica che questo
documento chiede prima di scrivere "come per Robustezza". `ApiClient` ha
comunque bisogno di un metodo nuovo, `cohorts(guild_id, limit=1)`, sulla
stessa forma di `robustness()`/`communities()` ma con un default diverso — e
il default va reso esplicito nella firma, non lasciato implicito nel valore
del server (`DEFAULT_LIMIT = 12` in `api/config.py` è pensato per le altre due
viste, e affidarsi al default varrebbe una richiesta da 1.500 righe senza che
il codice lo dichiari in nessun punto).

**Conseguenza per il layout: niente grafici di serie per questa vista, in v0.**
Le regole 4 e "la qualificazione di una serie" (§5) non si applicano a
Coorti — non perché siano sospese, ma perché non c'è una serie nel senso in
cui Robustezza e Community ne hanno una (poche grandezze, molti snapshot);
c'è invece **una tabella con molte righe indipendenti su un solo snapshot**.
Se in futuro servirà vedere una coorte specifica maturare settimana per
settimana, è una vista a sé (una coorte, molti snapshot) e non un'estensione
di questa: fuori da questa sessione, e non è tra i deliverable richiesti.

**Le tre assenze** sono le stesse di Robustezza e Community, sopra: guild non
osservata → 404 → `non_osservata.html`; guild osservata, nessuno snapshot →
`[]`, frase propria; API che non risponde → `errore.html`. Non si ripete la
spiegazione.

#### Layout: un gruppo per coorte, e una scoperta che cambia la forma della riga

`CohortGroup` porta `onboarding: list[OnboardingRow]` (fino a 2, uno per
`layer_scope`) e `retention: list[RetentionRow]` (fino a 3, uno per
`horizon_days`) — cinque righe potenzialmente indipendenti, e la lettura più
ovvia sarebbe renderle come cinque righe indipendenti, ciascuna con la propria
etichetta. **Non è quello che il job scrive, e va verificato eseguendo
`compute_cohort`, non assumendolo per analogia con Community** (dove ogni
riga *è* davvero indipendente, perché ogni layer ha un grafo proprio).

**Verificato eseguendo `job/metrics.py::_cohort_metrics` e `job/cohorts.py`
sui membri reali di `tools/fixture_api.py::_scenario_today`** (script di
verifica, non lettura): per una data `cohort_start`, `job/metrics.py` calcola
`cohort_members` **una sola volta** e lo passa, identico, a tutte e cinque le
chiamate (due `compute_cohort`, tre `compute_retention`). Dentro
`compute_cohort`, `observation_days`, `is_mature`, `is_survivors_only` e
`has_snapshot_coverage` dipendono **solo** da `members`, `cohort_start`,
`as_of`, `observability_anchor` e `snapshot_windows` — **mai** da
`reached_at`, cioè mai dal `layer_scope`. Ne segue che `reasons` e quindi
`is_significant` non dipendono dal `layer_scope` nemmeno loro. Risultato,
eseguito su entrambi gli snapshot del fixture, quattro coorti per snapshot:
**`n_effective`, `excluded_rejoins`, `observation_days`, `is_mature`,
`has_snapshot_coverage`, `is_survivors_only` e `quality.significant` sono
identici tra la riga `any` e la riga `voice` della stessa coorte, sempre,
senza un'eccezione nei dati generati.** Esempio reale (coorte del 31/08,
snapshot 12): `any` ed `voice` hanno entrambe `n_effective=14`,
`is_mature=False`, `has_snapshot_coverage=True`, `is_survivors_only=False`,
`observation_days=7`, `significant=False` — e divergono **solo** su
`event_count` (1 contro 0), `p25_days_to_k` (11,158 contro assente) e i
derivati della curva: la persona che ha raggiunto `k=5` lo ha fatto contando
partner di ogni layer (`any`), non partner vocali soltanto.

**La stessa identità vale, con la stessa dimostrazione, per `is_survivors_only`
sulle tre righe di retention**: `compute_retention` lo calcola dalla stessa
funzione (`is_survivors_only(cohort_start, observability_anchor=...)`), che
non riceve né `members` né `horizon_days`. È letteralmente la stessa chiamata
per le cinque righe del gruppo. E la soppressione (sotto) usa lo stesso
`n_effective` — `len(cohort_members)` — per tutte e cinque: non può accadere
che una riga di un gruppo sia soppressa e un'altra no.

**Conseguenza per il layout**: il gruppo non è cinque righe con cinque
etichette potenzialmente diverse — è **una riga di qualificazione condivisa**
(coorte, `n_effective`, `excluded_rejoins`, maturità, copertura, "solo
sopravvissuti", "non significativo") più **due colonne di curva** (`any` e
`voice`, che divergono solo nei numeri derivati dalla curva di sopravvivenza)
più **tre colonne di retention** (7/14/28 giorni, dove invece `is_computable`
e `not_computable_reason` **variano davvero** per orizzonte — vedi sotto).
Renderla come cinque righe separate, ciascuna con la propria etichetta,
ripeterebbe sette volte un'informazione che il codice scrive una volta sola, e
inviterebbe a mostrare due etichette "non significativo" leggermente diverse
se mai un bug le facesse divergere — l'errore opposto e speculare a quello che
Community evita tenendo `n_effective` una volta sola per riga.

Colonne, in ordine di lettura:

**Condivise** (una volta per gruppo): `coorte` (`cohort_start`, lunedì della
settimana ISO) · `n` (`n_effective`) · `esclusi` (`excluded_rejoins`) ·
`maturità` (`is_mature`, con `observation_days` accanto — vedi sotto) ·
`copertura` (`has_snapshot_coverage`) · etichetta di riga (soppressione, "non
significativo", "solo sopravvissuti" — combinazione, vedi sotto).

**Integrazione — `any`** e **Integrazione — `voice`**, stesse sette colonne
per ciascuna: `eventi` (`event_count`) · `censurati` (`censored_count`, con
`censored_by_leave` come parte di quel numero, non accanto — sotto) ·
`mediana` (`median_days_to_k`, qualificata da `median_reached`, regola 7 già
in §5) · `p25` · `p75` · `raggiunta a 14g` (`reached_by_14d`) · `raggiunta a
28g` (`reached_by_28d`).

**Retention — 7g / 14g / 28g**, stesse due colonne per ciascun orizzonte:
`trattenuti` (`retained_fraction`) · etichetta cella (`is_computable` con
`not_computable_reason`, sesto-stato style — sotto).

`censored_by_leave` **non è una colonna a sé nel layout**, a differenza di
`nodes_removed` in Robustezza: è un sotto-conteggio di `censored_count`
(`censored_count = censored + censored_by_leave` nel senso che entrambi sono
censure, non due popolazioni disgiunte — verificato in
`SurvivalCurve.__init__`: `self.censored = self.total - self.events`, e
`self.censored_by_leave` è un sottoinsieme filtrato dello stesso insieme, non
un secondo conteggio indipendente). Va mostrato come nota della cella
(`N censurati, di cui M per uscita dal server`) non come colonna propria: una
colonna propria lo farebbe sembrare un terzo esito invece che un dettaglio del
secondo, e la distinzione fra "rischio competitivo" e "censura vera" è già
dichiarata nel prosa del job (`modello-metriche.md` §5.4) come una
semplificazione di v0, non un fatto che la UI deve rendere prominente quanto
`event_count`/`censored_count`.

Nessuna colonna del gruppo di curva è nel gruppo di precisione aritmetica di
§5 ("Le colonne legate da un'operazione"): `median_days_to_k`, `p25`, `p75`,
`reached_by_14d`, `reached_by_28d` sono uscite indipendenti della stessa curva
di Kaplan-Meier, non calcolate l'una dall'altra da una formula visibile in
tabella — stesso criterio già applicato a `node_overlap`/`stability_jaccard`
in Community, stesso esito (nessun gruppo).

#### I tre motivi di non significatività, tutti colonne tipizzate — a differenza di Robustezza e Community

`job/cohorts.py::compute_cohort` aggiunge a `reasons` **fino a tre** motivi,
non uno solo (la voce da verificare che questa sessione doveva chiudere):

```python
if not is_mature:
    reasons.append("cohort_not_mature")
if not coverage:
    reasons.append("no_snapshot_coverage")
if survivors_only:
    reasons.append("survivors_only_cohort")
```

e `is_significant = not reasons` — quindi una riga di onboarding può essere
non significativa per uno solo di questi motivi, per una qualunque coppia, o
per tutti e tre insieme (nel fixture, la coorte del 10/08 li ha tutti e tre
prima della soppressione: non matura sarebbe falso — è matura, `obs_days=22`
— ma soppressa comunque sotto `n=5`; la coorte del 17/08 ha `no_snapshot_coverage`
**e** `survivors_only_cohort` insieme, verificato: `cov=False`,
`surv_only=True`, entrambi veri sulla stessa riga eseguita).

`modello-metriche.md` §7.3 **elenca già correttamente questi tre motivi** —
non è un errore da correggere, a differenza di quanto accaduto due volte per
Community (§4.6): confrontato riga per riga con `compute_cohort`, il testo è
accurato.

**A differenza di Robustezza (`too_few_nodes`, non sempre distinguibile da
altre soglie nella stessa riga) e di Community (dove il motivo è spesso solo
parzialmente ricavabile da una colonna), qui i tre motivi sono *ciascuno* una
colonna tipizzata a sé**: `is_mature`, `has_snapshot_coverage` (entrambe in
`OnboardingQuality`) e `is_survivors_only` (in `CohortQuality`, ereditata). La
regola di §5 — "il motivo si può dire con parole proprie solo dove è
ricavabile da una colonna tipizzata, mai da `details`" — qui non lascia fuori
niente: la vista **può** e **deve** mostrare la frase corretta leggendo le tre
colonne, senza mai aprire `quality.details.not_significant_because` (che pure
esiste, duplicato, e va ignorato per lo stesso motivo per cui lo era in
Community). Non serve promuovere nessun'altra colonna come si era dovuto fare
altrove (regola 2, "follow-up per il job" della sezione precedente su
`details`): qui il follow-up è già stato fatto, dalla migration `0007`.

#### Soppressione e "solo sopravvissuti" si escludono per costruzione: verificato, non solo atteso

La domanda aperta 3 del compito era se soppressione (`is_suppressed`) e "solo
sopravvissuti" (`is_survivors_only`) potessero comparire **insieme** sulla
stessa riga pubblicata. **No, mai, e non per una scelta di rendering: per il
trigger del database.** `job/suppression.py::suppress()` azzera ogni campo
della dataclass fuori da `KEY_FIELDS`/`is_suppressed`/`suppression_reason`/
`details` — `is_survivors_only` compreso, perché non è nell'elenco delle
eccezioni. Il trigger `metric_suppressed_row_is_empty`
(`migrations/0006_metrics.sql`) impone lo stesso vincolo lato schema, per
qualunque scrittore, non solo per il job. **Verificato eseguendo**: la coorte
del 10/08 (`n=3`) ha `is_survivors_only=True` calcolato da `compute_cohort` —
la coorte precede davvero l'ancora — ma dopo `suppress()` il campo vale
`None`, non `True`. La riga soppressa non "nasconde" l'informazione dietro il
simbolo di soppressione: quell'informazione, a livello di dato, **non esiste
più** su quella riga.

Questo non è una regola nuova: è la stessa regola generale già scritta in §5
("Prima: il numero esiste? ... `quality.suppressed is True` → il numero non
c'è, e non c'è nemmeno tutto il resto ... Nessun'altra etichetta"),
verificata qui esplicitamente per il caso specifico delle coorti perché era
la domanda aperta del compito. **Non serve una regola nuova in §5** per
questo punto: serve confermarlo, con la citazione del codice, il che è fatto.
Quello che **serve** formalizzare come regola — perché oggi è solo accennato
in due punti diversi del documento — è la composizione delle etichette
quando la riga **non** è soppressa: sotto, dopo i casi limite.

#### La retention: tre motivi di non calcolabilità, uno dei quali coincide sempre con "solo sopravvissuti"

`compute_retention` ha una struttura diversa da `compute_cohort`: non
accumula `reasons`, sceglie **un solo** motivo con un `if/elif/elif` in
ordine di priorità — **verificato eseguendo**, non solo leggendo il codice:

```python
if survivors_only:
    reason = "before_observability_anchor"
elif not members:
    reason = "empty_cohort"
elif not all(member.joined_at + horizon <= as_of for member in members):
    reason = "horizon_not_reached"
```

Conseguenza verificata con un caso costruito apposta (coorte anteriore
all'ancora **e** con orizzonte non raggiunto insieme): il motivo riportato è
sempre `before_observability_anchor`, mai `horizon_not_reached`, quando
entrambe le condizioni sono vere. La UI non deve provare a indovinare quale
delle due "avrebbe reso" la riga non calcolabile: il job lo dice già, con un
solo campo.

**`before_observability_anchor` e `is_survivors_only = True` sono, per la
retention, la stessa condizione vista da due colonne diverse** — non solo
correlate, **logicamente equivalenti**: `reason == 'before_observability_anchor'`
se e solo se `is_survivors_only is True` (è l'unico ramo che lo imposta, ed è
controllato per primo). Mostrare qui *sia* l'etichetta di riga "solo
sopravvissuti" *sia* il motivo di cella "prima dell'ancora di osservabilità"
non è ridondanza da eliminare: rispondono a due domande diverse anche quando
la causa a monte è la stessa — "chi è stato contato" (riga) contro "perché
questo numero non esiste" (cella) — esattamente la stessa distinzione che §5
fa già per `is_significant` contro `is_survivors_only` nell'onboarding.

**`empty_cohort` non compare mai su una riga pubblicata: è un ramo difensivo,
verificato per esecuzione, non ipotizzato.** Una coorte con zero membri ha
`n_effective = 0`, e `0 < min_cardinality (5)` è vero per costruzione: la
soglia di soppressione intercetta ogni coorte vuota prima che
`not_computable_reason = 'empty_cohort'` possa mai raggiungere una riga
pubblicata. Eseguito: `compute_retention(members=[], ...)` produce
`not_computable_reason = 'empty_cohort'` e `n_effective = 0`, che
`apply_threshold` sopprime sempre. Il ramo resta nel codice — `split_cohorts`
può in teoria produrre una lista vuota se tutti i membri di una settimana sono
esclusi come rientri sospetti — ma non è raggiungibile in forma pubblicata,
stessa categoria di `targeted_excess = None` in Robustezza o di
`n_effective` divergente in Community: si tiene, non si prova con una
fixture dedicata (§5, "I rami difensivi, e perché non si provano").

Da qui, tre stati distinti e distinguibili per `retained_fraction`, nessuno
letto da `details`:

| Stato | Come si riconosce | Cosa significa |
|---|---|---|
| Calcolabile | `values.is_computable is True` | `retained_fraction` è un numero vero. |
| Non calcolabile: troppo presto | `not_computable_reason == 'horizon_not_reached'` | Non tutti i membri hanno ancora avuto l'orizzonte di osservazione: non è un dato basso, è un dato che non esiste ancora. |
| Non calcolabile: solo sopravvissuti | `not_computable_reason == 'before_observability_anchor'` | Coincide sempre con `quality.is_survivors_only = True` sulla stessa riga: la popolazione è distorta, non il tempo. |

`empty_cohort` non è nella tabella per la ragione appena verificata: non
comparirebbe mai lì.

#### Rappresentatività del fixture: la voce H si affina, non si chiude ancora

`stato-progetto.md` §7-H segnala che lo scenario `…001` ha **quattro**
`cohort_start` contro le **25** della produzione, e chiede di verificare
prima di disegnare il layout. Verificato eseguendo, coorte per coorte, non
assumendo: con quattro sole coorti lo scenario copre **due** delle possibili
combinazioni di motivi su una riga non soppressa — `no_snapshot_coverage` +
`survivors_only_cohort` insieme (17/08 e, prima della soppressione, anche
10/08: **stessa combinazione**, non una seconda) e `cohort_not_mature` da
solo (31/08 e 07/09: di nuovo la stessa combinazione, non una diversa) —
più la soppressione (10/08). Su **sette** combinazioni possibili dei tre
motivi (le sette non vuote di `2³ = 8`, l'ottava essendo "nessun motivo",
cioè `is_significant = True`), `…001` ne esercita **una sola** in forma
pubblicata, e la ripete due volte con coorti diverse. Per la retention copre
tutti e tre gli stati della tabella sopra (`horizon_not_reached` su più
righe, `before_observability_anchor` su 17/08 e 10/08, **calcolabile** su
31/08 a 7 giorni: `retained_fraction = 12/14 = 0,857142857142857…`, l'unico
numero di retention reale che il fixture produce oggi). Quello che **non**
copre, verificato per assenza dall'esecuzione sopra:

1. **Nessuna coorte con `is_significant = True`.** Serve una coorte matura
   (`observation_days ≥ 14`), coperta da uno snapshot, e posteriore
   all'ancora — cioè, con l'ancora fissata al 28/08, una coorte nata a
   marzo/aprile non basta (è "solo sopravvissuti"): serve una coorte
   **recente ma non recentissima**, con uno snapshot supplementare che la
   osservi abbastanza a lungo. `…001` si dichiara "lo stato reale di oggi" e
   oggi — verificato, non presunto — non ha nessuna coorte significativa: la
   tabella di §6 sotto lo riporta con i numeri veri.

   **Correzione del 16/09/2026, dalla sessione di implementazione: l'assenza
   vale per `…001` e per la produzione, non per il fixture.** Eseguito prima
   di costruire alcunché: `…002` ha **sei** coorti significative sull'ultimo
   snapshot (dal 10/08 all'01/06) e `…003` ne ha **una** (10/08, `n=11`), e ci
   sono dal commit `5cc2ba5`, cioè da prima di questa progettazione. Il caso
   "zero etichette" non andava quindi costruito: andava trovato. Quello che
   mancava davvero è la scala, punto 4.
2. **Le altre combinazioni di motivi**, e qui la sessione di implementazione
   si è fermata a chiedere, perché l'elenco che segue **non è costruibile
   com'è scritto**. Verificato per esecuzione il 16/09/2026, con
   `min_observation_days = 14` e finestre da `default_window_days = 7`:

   - `cohort_not_mature` implica `cohort_start > as_of − 21 giorni`, e la
     finestra dello snapshot su cui la run sta girando — che esiste sempre, è
     quello che sta calcolando — interseca allora
     `[cohort_start, cohort_start + 14)`. Quindi **`cohort_not_mature` implica
     la copertura**: la coppia `cohort_not_mature` + `no_snapshot_coverage` e
     la tripla che la contiene **non esistono**. Resta il solo caso degenere
     `cohort_start == as_of`, cioè cinque o più persone entrate nell'istante
     esatto dell'`as_of`, che per di più non può essere "solo sopravvissuti"
     (vorrebbe un'ancora nel futuro);
   - `cohort_not_mature` + `survivors_only_cohort` **esiste**, ma non può
     stare nello stesso snapshot di una coorte significativa: l'ancora è una
     sola per guild e `observation_days` decresce al crescere di
     `cohort_start`, quindi una coorte anteriore all'ancora è sempre più
     vecchia — e perciò più matura — di qualunque coorte posteriore. Le due
     cose si escludono su una guild sola, non per come è fatto il fixture.

   Restano **cinque** combinazioni pubblicate possibili più la soppressione, e
   il fixture le esercita tutte (punto 4): due che `…001` non aveva
   (`no_snapshot_coverage` da solo, `survivors_only_cohort` da solo) nello
   snapshot recente di `…004`, e la quinta — `cohort_not_mature` +
   `survivors_only_cohort` — nel run precedente della stessa guild, undici
   giorni dopo la sua ancora. Nessuna cambia la logica di rendering
   (l'etichetta resta "non significativo" più, se pertinente, "solo
   sopravvissuti" — regola 8 sotto), ma un test che asserisse "la vista
   gestisce `cohort_not_mature`" sui soli dati di `…001` starebbe in realtà
   testando solo la combinazione `no_snapshot_coverage` +
   `survivors_only_cohort`.
3. **Nessuna coorte con `censored_by_leave > 0` e insieme `event_count > 0`
   nella stessa riga** — il fixture ha entrambi separatamente (31/08 ha un
   evento e due `censored_by_leave`, ma sono componenti dello stesso
   `censored_count`, non un caso a sé da esercitare ulteriormente: già
   coperto).
4. **La scala reale — 25 coorti, non 4 — non è replicata, ed è la vera voce
   aperta.** I quattro `cohort_start` di `…001` esercitano la casistica dei
   *flag*, ma il layout deve reggere una tabella da **una ventina di righe**
   (stato-progetto.md, dati reali del 14/09: **25 coorti, 50 righe di
   onboarding — 12 soppresse, 34 "solo sopravvissuti", 4 non soppresse e non
   "solo sopravvissuti"** — la somma torna: 12+34+4=50, e per coorte
   (÷2 `layer_scope`) fa 6 coorti soppresse, 17 "solo sopravvissuti", 2 né
   l'uno né l'altro, e 6+17+2=25). Per la retention (75 righe attese, 25×3
   orizzonti) questa sessione non ha eseguito una query — **non c'è accesso
   al database di produzione da qui** — ma la stessa aritmetica si applica
   per costruzione (la soppressione è per `cohort_start`, non per riga,
   verificato sopra): **18 righe di retention soppresse** (6 coorti × 3),
   **51 con `before_observability_anchor`** (17 coorti × 3, tutte, perché la
   condizione non dipende dall'orizzonte), e **6** sulle 2 coorti restanti,
   il cui `is_computable` per orizzonte va verificato con una query quando
   sarà possibile — non è un'inferenza che vale la pena presentare come
   certa quanto le altre due.

   **`…001` non deve arrivare a 25 coorti**: moltiplicare i quattro scenari
   esistenti coprirebbe la stessa casistica di flag con più righe, senza
   aggiungere niente che il test non veda già. Quello che serve è **un quinto
   scenario, o un'estensione di `…002`**, con un blocco di ~20 coorti
   generate proceduralmente (stesso spirito dei parametri già presenti per
   Robustezza/Community in `…002`) per esercitare la vista al numero di righe
   reale — soprattutto per verificare che una tabella da 25 righe × ~20
   colonne resti leggibile, che l'ordinamento (`cohort_start DESC`) porti le
   coorti interessanti in cima, e che le 17 righe "solo sopravvissuti"
   consecutive non producano un muro grigio indistinguibile — è la
   preoccupazione originale della voce H, e ora ha un numero: **68% delle
   coorti** (17/25) è oggi in quello stato singolo.

   **Non implementare questo scenario in questa sessione** (il compito lo
   esclude): lasciarlo come istruzione esplicita per la sessione di
   implementazione, sotto.

   **Fatto il 16/09/2026: è la guild `…004`** (`tools/fixture_api.py::
   _scenario_scala`, §7 sotto). Venticinque coorti su un solo snapshot, con la
   stessa forma della produzione — **6 soppresse, 17 anteriori all'ancora
   (68%), 2 recenti non ancora mature** — più le tre significative che la
   produzione non ha. Ogni coorte si descrive con **tre fatti** (quante
   settimane fa, quanti membri, a che ora entra l'ultimo) e il resto lo calcola
   il job: la combinazione di motivi non è scritta da nessuna parte nel
   fixture, è ciò che `compute_cohort` deduce dai fatti, dall'ancora e dalle
   finestre. La copertura si spegne dove **mancano davvero gli snapshot**: due
   settimane senza run (10 e 17/08) tolgono la copertura alla sola coorte del
   03/08, altre due nel passato (25/05 e 01/06) a quella del 18/05 — che
   essendo anteriore all'ancora porta due motivi insieme. Verificato sul
   markup: 25 righe di gruppo senza troncamenti, ordinate `cohort_start DESC`,
   e la sequenza di "solo sopravvissuti" resta distinguibile riga per riga
   (`tests/test_dashboard_coorti.py`).

#### Casi limite, con numeri veri — eseguiti su `job/cohorts.py`, non letti

Tutti i numeri sotto vengono da un'esecuzione diretta di `compute_cohort` e
`compute_retention` sui membri di `tools/fixture_api.py::_scenario_today`
(stessa fonte che il fixture usa), fatta in questa sessione — non dal
fixture già scritto, e non da un'analogia con le altre viste.

- **La coorte del 31/08, snapshot 12: la prova che le colonne condivise sono
  davvero condivise e quelle di curva no.** `n=14`, `is_mature=False`
  (`observation_days=7` contro i 14 richiesti — lo stesso numero già in
  `stato-progetto.md` §9-4), `has_snapshot_coverage=True`,
  `is_survivors_only=False`, `is_significant=False` — **identici** su `any` e
  `voice`. Poi le colonne divergono: `any` ha `event_count=1`,
  `p25_days_to_k=11,157546…`, `voice` ha `event_count=0` e tutti i quantili
  assenti — la persona che ha raggiunto `k=5` partner lo ha fatto sommando
  layer diversi, non restando dentro `voice`. Sulla stessa coorte, la
  retention a 7 giorni **è calcolabile** (`is_computable=True`,
  `retained_fraction=12/14=0,857142857142857…`) mentre a 14 e 28 giorni non
  lo è (`horizon_not_reached`): la stessa coorte ha contemporaneamente una
  cella di retention piena e due vuote, sulla stessa riga.
- **La coorte del 07/09, snapshot 12: `median_reached=True` con un evento su
  otto, replicato ma non identico al caso già in §5.** `n=8`,
  `event_count=1` su **entrambi** gli ambiti (la stessa persona ha raggiunto
  `k` sia in `any` sia in `voice`), `median_days_to_k=4,582487893043981`,
  `median_reached=True`. È la stessa forma del caso già documentato in
  "Il sesto stato" (§5) — un evento solo che porta `S(t)` a 0,5 perché il
  gruppo a rischio si è ridotto — qui rieseguito con dati diversi (produzione
  del 14/09, non questa esecuzione) e confermato riproducibile: non è un
  artefatto di una coorte specifica, è il comportamento generale della curva
  su una coorte piccola.
- **La coorte del 17/08, snapshot 11 e 12: la combinazione di due motivi
  insieme, coperta dalla regola generale, non da un'etichetta terza.** `n=8`,
  `is_mature=True` (`observation_days=22` a snap 12), **ma**
  `has_snapshot_coverage=False` **e** `is_survivors_only=True` insieme:
  `reasons = ["no_snapshot_coverage", "survivors_only_cohort"]`,
  `is_significant=False`. `reached_by_14d=0,0` su questa riga — un numero che,
  letto da solo, direbbe "nessuno si è integrato in due settimane", mentre il
  fatto è che **nessuno snapshot copriva quella finestra**: è esattamente il
  rischio che `modello-metriche.md` §5.3 punto 4 descrive in teoria
  ("`reached_by_14d = 0.0` misura l'assenza di osservazione"), qui con un
  numero reale a dimostrarlo. La riga porta **due** etichette (non
  significativo, solo sopravvissuti), non tre: `no_snapshot_coverage` non ha
  un'etichetta propria, si legge dalla colonna `copertura` già in vista
  (stessa logica della regola "il motivo si legge da una colonna tipizzata").
- **La coorte del 10/08, entrambi gli snapshot: soppressione che vince su
  tutto, incluso su un `is_survivors_only=True` che esisteva un istante prima
  della soppressione.** `n=3 < 5`: soppressa. Eseguito il confronto
  prima/dopo `suppress()`: `is_survivors_only` passa da `True` a `None`. È la
  verifica per la domanda aperta 3, sopra.

#### Regola nuova in §5: le etichette di riga si compongono, non si scelgono — formalizzata, non solo accennata

Va aggiunta come regola 8 a "Otto regole che valgono ovunque" (sotto, §5):
vedi lì per il testo. Qui basta la giustificazione empirica che la rende
necessaria: sulle coorti, **una riga pubblicata (non soppressa) porta zero,
una o due etichette**, mai scelte tra loro. Verificato sopra sui dati
eseguiti: la coorte del 17/08 ne porta due contemporaneamente
("non significativo" e "solo sopravvissuti"); la coorte del 07/09 ne porta
una sola ("non significativo", perché `is_survivors_only=False`); nessuna
coorte matura, coperta e posteriore all'ancora esiste ancora in produzione
per mostrare il caso a zero etichette, ma la logica del codice lo prevede
(§4, "Nessuna coorte con `is_significant = True`", sopra) e il fixture lo ha
già: sei coorti in `…002`, una in `…003`, tre nella `…004` aggiunta il
16/09/2026 — il caso si rende, non si aspetta la produzione.

#### I rami difensivi

Si aggiungono ai tre di §5 e ai due di Community, stessa logica: nel codice,
non nella fixture.

- **`n_effective`/`is_mature`/`has_snapshot_coverage`/`is_survivors_only`/
  `is_significant` divergenti tra le righe `any` e `voice` della stessa
  coorte** — non può succedere, è la stessa chiamata sugli stessi `members`
  (sopra, "Layout"), ma il template non deve *assumerlo*: va verificato con
  un test che confronta le due righe del gruppo, come già per `n_effective`
  in Community.
- **Una riga di retention soppressa e una di onboarding della stessa coorte
  non soppressa, o viceversa** — non può succedere, stessa soglia sullo
  stesso `n_effective` per tutte e cinque le righe del gruppo (sopra), ma va
  verificato con un test sul gruppo intero, non riga per riga.
- **`not_computable_reason = 'empty_cohort'` su una riga pubblicata** — non
  può succedere, verificato sopra per esecuzione: `n_effective = 0` è sempre
  sotto `min_cardinality`. Non si costruisce una fixture apposta per
  provarlo, per lo stesso motivo di `targeted_excess = None` in Robustezza.
- **`is_survivors_only = True` con `not_computable_reason` diverso da
  `before_observability_anchor` sulla stessa riga di retention** — non può
  succedere, `survivors_only` è controllato per primo nell'`if/elif/elif` di
  `compute_retention` (verificato sopra per esecuzione con un caso costruito
  apposta), ma è un invariante di codice, non di schema: nessun `CHECK` lo
  impone (a differenza di
  `metric_cohort_retention_reason_matches_computable`, che vincola solo
  `is_computable`/`not_computable_reason` tra loro, non `is_survivors_only`).
  Va provato con un test sul markup.

## 5. Come si rappresenta la qualificazione

È il cuore di questo documento. Le altre sezioni descrivono un'applicazione web
qualunque; questa è l'unica parte che nessuno strumento avrebbe dato gratis.

### La regola strutturale

**La funzione che rende una cella riceve la riga, non il valore.** La firma è
`cella(row, campo)`, mai `cella(row["values"]["targeted_excess"])`. È
l'equivalente in UI della decisione di `api.md` §3: per arrivare a un numero
bisogna passare da `values`, e `quality` è lì come suo fratello. Una firma che
accetta un float non può sapere se quel float va mostrato.

**I nomi dei campi sono quelli del modello, non quelli della prosa né quelli del
database.** Nelle tabelle Postgres le colonne sono `is_suppressed` e
`is_significant`; `api/assemble.py` le espone come **`suppressed`** e
**`significant`**, senza prefisso. Ma `is_survivors_only`, `is_mature`,
`has_snapshot_coverage` e `is_computable` **mantengono** il prefisso, e
`is_computable`/`not_computable_reason` stanno in **`values`**, non in
`quality`. Non è una regola con due eccezioni: sono due rinomine puntuali. Un
accesso scritto dalla prosa — `quality.get("is_suppressed", False)` —
mostrerebbe ogni riga soppressa come non soppressa, senza nessun errore. È §7.

### I cinque stati, e quattro rendering

Cinque stati e quattro rendering, non cinque: **"non valutato" non si mostra
mai**, ed è una conclusione, non una dimenticanza — la riga della tabella qui
sotto dice perché.

| Stato | Come si riconosce | Come si mostra |
|---|---|---|
| **Soppresso** | `quality.suppressed is True` (tutti i `values` a `None`) | Simbolo di soppressione esplicito + `suppression_reason` in chiaro. **Mai uno zero, mai una cella vuota, mai la riga nascosta.** |
| **Non significativo** | `quality.significant is False` | Il numero **si mostra**, visivamente dequalificato. Nasconderlo toglierebbe la possibilità di vedere la serie formarsi. Sul motivo, vedi sotto: non si legge da `details`. |
| **Non valutato** | `quality.significant is None` | **Nessuna etichetta, mai.** Non è un rendering: è uno stato del dato che non ha niente da dire a chi guarda. Vedi sotto. |
| **Non calcolabile** | `values.is_computable is False` (solo retention) | Il numero **non esiste**, con `not_computable_reason`. Parola diversa da "soppresso": soppresso significa che esiste e non si può mostrare. |
| **Solo sopravvissuti** | `quality.is_survivors_only is True` (solo coorti) | Il numero esiste ed è mostrabile, ma conta le persone sbagliate. Badge permanente accanto al valore, con rimando a `first_seen_at`. |

`quality.significant` ha **tre** stati, non due. Una UI che lo tratta come
booleano collassa `None` e `False`, cioè confonde "non lo sappiamo" con "lo
sappiamo ed è rumore". Oggi è il 100% delle righe.

I flag **non sono universali**: `has_snapshot_coverage` e `is_mature` vivono
solo in `OnboardingQuality`; `is_survivors_only` ed `excluded_rejoins` solo
nelle coorti; `median_reached` qualifica **solo** `median_days_to_k`. Non esiste
un badge unico da applicare ovunque: la mappa stato→rendering è per endpoint, ed
è questa tabella.

### I cinque stati non sono esclusivi, e si compongono in un ordine

La tabella sopra si legge come un interruttore a cinque posizioni. Non lo è: una
riga può stare in più stati insieme, e il caso più frequente è proprio quello.
La composizione avviene rispondendo a **due domande in quest'ordine**.

**Prima: il numero esiste?** Due stati sono terminali e chiudono la questione.

- `quality.suppressed is True` → il numero non c'è, e non c'è nemmeno tutto il
  resto (`n_effective` è `None`, `significant` è `None`, `details` è vuoto per
  imposizione del trigger `metric_suppressed_row_is_empty`). Si mostra solo la
  soppressione con il suo motivo. Nessun'altra etichetta, perché non ci sono
  altre informazioni da mostrare.
- `values.is_computable is False` (solo retention) → quel singolo numero non
  esiste, con `not_computable_reason`. Terminale per quel valore, **non** per la
  riga: gli altri campi della riga restano leggibili.

**Poi, se il numero esiste: quanto vale, e chi sta contando?** Queste due non si
escludono — si mostrano **insieme**, perché rispondono a domande diverse.

- `quality.significant` dice quanto ci si può fidare del numero come statistica.
- `quality.is_survivors_only` dice **chi è stato contato**, e non è un giudizio
  sull'affidabilità: una coorte anteriore all'ancora di osservabilità è fatta
  per costruzione dai soli sopravvissuti, quindi `n_effective` significa "quanti
  erano ancora presenti", non "quanti sono entrati".

**Verificato nel codice**: in `job/cohorts.py` la significatività è
`is_significant = not reasons`, e `survivors_only` aggiunge sempre la reason
`survivors_only_cohort`. Quindi **una riga di soli sopravvissuti è per
costruzione anche non significativa**: la coppia non è un caso limite, è la
forma normale in cui quello stato si presenta. La combinazione opposta —
significativa *e* di soli sopravvissuti — non può esistere.

Conseguenza per la UI: su una riga di coorte anteriore all'ancora vanno mostrate
**due etichette distinte**, non una. Collassarle in un solo "non significativo"
perde l'informazione che conta di più, perché "non affidabile perché il campione
è piccolo" e "non affidabile perché stiamo contando solo chi è rimasto" portano
il community manager a due conclusioni diverse.

### Il perché della non significatività non si legge da `details`

La tabella diceva "con l'etichetta del perché", e la regola 2 più sotto vieta di
ricavare etichette da `quality.details`. Erano in contraddizione, perché
`not_significant_because` sta proprio lì. **Vince la regola 2**: `details` è
diagnostica dichiarata non contrattuale, le sue chiavi cambiano col codice del
job, e una UI che ci costruisse sopra un'etichetta si romperebbe in silenzio al
primo cambio.

Quindi l'etichetta dice "non significativo" e basta. Dove il motivo è
**ricavabile da una colonna tipizzata**, la dashboard può dirlo con parole
proprie — `quality.n_effective` sotto la soglia strutturale giustifica "il grafo
è troppo piccolo", perché quel numero è contrattuale. Ciò che non è ammesso è
leggere la lista dei motivi dal `details` e renderla.

**Follow-up per il job, non per la dashboard**: se il motivo della non
significatività deve essere mostrabile, va promosso a colonna tipizzata. È
l'invariante n. 4 del progetto — *se un dato dice quanto vale un altro dato, è
una colonna* — e vale qui esattamente come è valso per `previous_gap_days`, che
per la stessa ragione è uscito da `details` ed è diventato una colonna con la
migration `0011`. Finché quella colonna non esiste, il motivo non si mostra.

### Perché "non valutato" non ha rendering

`quality.significant is None` vuol dire "la significatività non è stata
valutata", e succede in **due modi**. Qui la regola è un criterio e non un
elenco di tabelle, per la stessa ragione di `api.md` §1: una tabella aggiunta
domani si classifica da sola, mentre un elenco va ricordato — e chi lo dimentica
non riceve nessun segnale.

**Primo: la riga è soppressa.** Non c'è niente da valutare — nessun numero,
`n_effective` a `None`. La soppressione è terminale: si mostra solo il simbolo
con il suo motivo, nessun'altra etichetta. Aggiungerne una non porterebbe
niente, perché "non abbiamo valutato la significatività" è una conseguenza ovvia
della soppressione, non un secondo fatto. Mostrarla farebbe sembrare due
problemi quello che è uno.

**Secondo: la tabella di quella metrica non ha la colonna `is_significant`**,
cioè la significatività non è un concetto definito per quella metrica. Non è un
dato mancante: è una domanda che lì non si pone. Oggi sono due tabelle su
cinque — `metric_cohort_retention` e `metric_community_sizes` — ma il numero non
conta: conta che il criterio le riconosca senza che nessuno le elenchi.

In entrambi i modi l'etichetta non ha niente da dire a chi guarda. Quindi `None`
non produce mai un'etichetta, e lo stato non ha rendering.

**Questa assenza va verificata, e il test ovvio non basta.** Un test che
asserisce "nessuna cella porta mai `NON_VALUTATO`" è quasi circolare: se il
codice di rendering non produce quello stato, resta verde qualunque cosa
succeda ai dati, e fallisce solo se qualcuno rimette l'etichetta nel codice.
Utile, ma protegge il codice da sé stesso, non dalla realtà.

Serve accanto un **test sui dati**: `significant is None` su una riga
pubblicata è ammesso solo sui tipi di riga che rientrano nel secondo modo. Una
tabella nuova con `is_significant` nullable lo fa fallire, e a quel punto
qualcuno decide — invece di scoprire in produzione un'etichetta muta, o
peggio la sua assenza silenziosa.

### Le due metriche che non hanno significatività, e non devono fingere di averla

Due delle cinque tabelle di metriche **non hanno la colonna `is_significant`**,
e non è una dimenticanza: per quelle due la significatività non è una domanda
che si pone.

- **`metric_cohort_retention`** — la retention è una frazione di una popolazione
  nota, non una stima da distinguere dal rumore. La sua qualificazione passa
  per `is_computable` / `not_computable_reason` e `is_survivors_only`, che sono
  colonne vere.
- **`metric_community_sizes`** — i bucket sono una distribuzione, cioè un modo
  di guardare la partizione della riga madre. È quella riga a portare
  `modularity_z` e il proprio `is_significant`; il bucket ha solo la propria
  soppressione, che può essere secondaria.

Su ogni riga di queste due, `quality.significant` è `None` sempre, anche quando
la riga è pubblicata e perfettamente leggibile. Applicando la tabella alla
lettera, ognuno di quei numeri porterebbe "significatività non valutata": un
avviso presente ovunque, che non distingue niente da niente e insegna a
ignorare gli avvisi proprio nelle viste in cui contano.

È il secondo dei due modi della sezione precedente, e si risolve lì: `None` non
produce etichetta. Qui conta la ragione, perché è la stessa che rende il
criterio preferibile all'elenco — una terza tabella senza quella colonna
ricadrebbe nella regola da sola.

### Il sesto stato: un valore che manca senza che manchi niente

`median_reached` non sta nella tabella dei cinque perché non qualifica la riga:
qualifica **solo** `median_days_to_k`, ed è l'unico caso in cui un valore assente
non è né soppressione né incalcolabilità.

Nel codice è `median_reached = median is not None`, e la mediana è
`quantile(0.5)` di una curva di Kaplan-Meier: il primo `t` con `S(t) ≤ 0,5`,
oppure `None`.

**Attenzione a come si dice, perché la formulazione ovvia è falsa.** "Meno di
metà della coorte ha raggiunto k" **non** è ciò che `median_reached = False`
significa, e la produzione lo dimostra: sullo snapshot 12 la coorte del 7
settembre ha `event_count = 1` su `n_effective = 8` — una persona su otto — e
`median_reached` vale **`True`**, con mediana 4,58 giorni. Non è un difetto del
job: con la censura amministrativa i membri osservati per meno di 4,58 giorni
escono dal gruppo a rischio prima dell'evento, e un evento solo su due ancora a
rischio porta `S(t)` a 0,5 esatti.

Quindi `median_reached = False` vuol dire una cosa più stretta: **entro
l'osservazione disponibile la curva non è mai scesa al 50%, quindi un tempo
mediano non è stimabile.** È questa la frase affermativa da mostrare — non una
cella vuota né il simbolo di soppressione, che direbbero "manca un dato", ma
nemmeno un conteggio di persone che quel flag non sa. `p25_days_to_k`,
`p75_days_to_k`, `reached_by_14d` e `reached_by_28d` restano leggibili e vanno
mostrati accanto: sono il modo in cui quella riga dice ancora qualcosa.

**Il codice ha mostrato la frase falsa fino al 16/09/2026**, ed è il genere di
divergenza che si nota solo quando qualcuno rende davvero la cella:
`dashboard/qualifica.py` scriveva "meno di metà della coorte ha raggiunto k" —
esattamente la formulazione che questa sezione dichiara falsa — dal giorno in
cui il sesto stato è stato implementato, con un test che ne fissava il testo.
Nessuna vista lo aveva ancora reso: Robustezza e Community non hanno mediane, e
la cella è arrivata su una pagina solo con Coorti. Corretto insieme a quella
vista, in `qualifica.py` e nel test che lo fissa. Lezione, la stessa di
CLAUDE.md §7 in una forma nuova: **una regola scritta in spec e un codice che la
contraddice possono convivere per settimane finché nessuno esegue il percorso
che li mette a confronto** — qui a proteggere è stato che la spec fosse scritta
prima, e che la sessione di implementazione la rileggesse riga per riga.

### Otto regole che valgono ovunque

1. **Niente di tutto questo è un errore.** L'errore è l'API che non risponde, e
   deve avere un aspetto diverso da tutti gli stati sopra. Chi apre la dashboard
   oggi vede quasi solo grigio: deve capire "il sistema sta osservando e non ha
   ancora abbastanza dati", non "è rotto".
2. **`quality.details` non si disegna.** È diagnostica, non contratto: le sue
   chiavi cambiano col codice del job (`api.md` §3). Può comparire come testo
   letterale in fondo a una vista di dettaglio, mai come sorgente di un grafico,
   di un'etichetta o di una condizione.
3. **I quattro layer non si sommano mai.** Nessun totale, nessuna media tra
   layer, e **nessun punteggio unico di salute della community**. È l'invariante
   n. 3 del progetto, e la dashboard è esattamente il posto in cui verrebbe
   voglia di violarla: un indice sintetico è la vanity metric definitiva, perché
   nasconde dentro un numero solo tutto ciò che questo documento esiste per
   rendere visibile.
4. **Una serie con meno di tre punti non si disegna come linea.** Si mostra la
   tabella. Il motivo non è statistico ma geometrico: **con due punti non esiste
   nessuna forma possibile oltre alla retta**, e una retta ha una pendenza, e
   una pendenza è un'affermazione sulla direzione. Con tre punti la serie può
   fare zigzag, e uno zigzag si legge come "rumoroso" — che oggi è la verità.
   Tre è il minimo a cui i dati possono contraddire la linea. Tre punti bastano
   a *disegnare*, non a *dire* che c'è una tendenza: frecce di direzione, parole
   come "in salita" e segnalazioni automatiche appartengono alla metrica 7 del
   catalogo, che non esiste.
5. **`stability_jaccard` non si mostra mai senza `previous_gap_days` accanto.**
   Con finestre da 7 giorni, un gap di 1-2 giorni significa finestre sovrapposte
   all'85-95%, e il numero misura in gran parte quella sovrapposizione invece
   della ricomposizione delle community. È il caso — raro — in cui un campo di
   `values` qualifica un altro campo di `values`. È anche già successo: la
   prima scrittura dello snapshot 11 la dava a 1,0 su tutti e quattro i layer,
   misurata su sette ore e mezza. Quel valore non esiste più in produzione — lo
   snapshot 10 è stato cancellato, e il rerun del 15/09/2026 ha riscritto l'11
   senza precedente — ma il caso resta quello che la regola esiste per
   disinnescare.

   **Estensione (vista Community, 15/09/2026): `node_overlap` viaggia insieme
   a `previous_gap_days` e `stability_jaccard`, non solo il gap.** Un gap
   vicino a 7 giorni legge come "il confronto dovrebbe essere buono", ed è
   proprio l'impressione sbagliata da dare quando `stability_jaccard` manca
   per `node_overlap` sotto soglia: sui dati reali del 15/09,
   `previous_gap_days = 6,823` su tutti e quattro i layer allo snapshot 12, ma
   `stability_jaccard` esiste solo su `voice` (`node_overlap = 0,500`) —
   `mention` (`0,196`), `reaction` (`0,361`) e `reply` (`0,237`) hanno
   `node_overlap` sotto il minimo. Mostrare il gap senza `node_overlap`
   farebbe sembrare l'assenza della stabilità un difetto invece che un fatto
   sulla popolazione. Dove uno dei tre campi compare, gli altri due sono in
   vista — anche quando sono `None`. `node_overlap` e `stability_jaccard` non
   condividono per questo la precisione del gruppo aritmetico (sotto): non
   sono legati da un'operazione, sono due uscite indipendenti dello stesso
   confronto tra partizioni.
6. **`removal_fraction` non si mostra mai senza `nodes_removed` accanto.** La
   percentuale è l'input; il conteggio è quello che è successo. Il job calcola
   `nodes_removed = max(1, ceil(X · n))` (`job/robustness.py`), quindi **non è
   mai zero** — ed è proprio per questo che la percentuale mente: a 10 nodi il 5%
   e il 10% rimuovono **lo stesso unico nodo**, e due righe con percentuali
   diverse descrivono la stessa identica rimozione; a 14 nodi ne rimuovono 1 e 2.
   Un `targeted_excess` letto sotto l'intestazione "5%" si legge come "togliere
   il 5% dei connettori non rompe niente" mentre il fatto è che è stato tolto un
   nodo solo. Il flag `too_few_nodes_removed` scatta sotto i due nodi rimossi e
   rende la riga non significativa, ma il numero resta visibile: è l'etichetta
   accanto a doverlo disinnescare.

7. **`median_days_to_k` non si mostra mai senza `event_count` accanto.** È lo
   stesso meccanismo delle regole 5 e 6, sul campo in cui morde di più. Una
   mediana di 4,58 giorni si legge come "in media ci mettono cinque giorni", e in
   produzione quel numero poggia su **un** evento su otto persone: la curva
   scende al 50% perché il gruppo a rischio si era ridotto a due, non perché
   metà della coorte abbia fatto qualcosa. Il numero che disinnesca la lettura è
   `event_count`, ed è contrattuale. Il gruppo a rischio no — la curva "vive
   dentro la funzione e viene buttata" (`modello-metriche.md` §8) — quindi
   `event_count` è tutto ciò che la dashboard ha, e va usato.

8. **Le etichette di riga si compongono, non si scelgono: dove più di una si
   applica, si mostrano tutte, nello stesso posto di riga.** Oggi vale per le
   coorti, le uniche righe con più di un'etichetta possibile
   ("non significativo" e "solo sopravvissuti"): §4 lo verifica eseguendo
   `compute_cohort` sui dati reali — la coorte del 17/08/2026 porta entrambe
   insieme (`no_snapshot_coverage` **e** `survivors_only_cohort` in
   `reasons`, `is_significant=False` **e** `is_survivors_only=True`), la
   coorte del 07/09/2026 ne porta una sola. Non è un caso limite da gestire,
   è la forma normale: **una riga di soli sopravvissuti è per costruzione
   anche non significativa** (`survivors_only_cohort` è sempre tra i
   `reasons` quando `is_survivors_only` è vero), quindi la combinazione
   opposta — significativa *e* di soli sopravvissuti — non può esistere, ma
   "non significativo per altri motivi, senza essere di soli sopravvissuti"
   sì (17/08 con anche `cohort_not_mature`, se la coorte non fosse ancora
   matura, ne porterebbe comunque solo due, mai tre: `cohort_not_mature` e
   `no_snapshot_coverage` non hanno un'etichetta propria — si leggono dalle
   colonne `maturità`/`copertura` già in vista, regola 2). **Questa
   composizione si ferma alla soppressione**: una riga soppressa non porta
   *nessuna* di queste etichette, nemmeno "solo sopravvissuti", perché
   `is_survivors_only` è tra i campi che `suppress()` azzera a `None` — non
   per scelta di rendering, per costruzione del dato (§4, "Soppressione e
   'solo sopravvissuti' si escludono per costruzione"). Vale anche perché
   generalizza a qualunque vista futura con più di un flag per riga: il
   criterio — non l'elenco — è che ogni etichetta la cui condizione è vera si
   mostra, nella stessa posizione di riga (§5, "Dove va l'etichetta di
   riga"), fino a quando la soppressione non le azzera tutte insieme.

Nota di rendering: `targeted_excess` **può essere negativo e non è clampato**
(significa che i nodi più centrali erano meno critici di nodi presi a caso). Un
asse y ancorato a zero lo nasconderebbe.

### La precisione di una colonna numerica

**Un valore non si mostra mai come zero con segno.** `-0,0` mostra il segno e
butta via la grandezza: è il peggiore dei due mondi, perché afferma una direzione
e nega il numero che dovrebbe sostenerla. È successo al primo sguardo sui dati
veri (14/09): lo snapshot 12 ha `targeted_excess = -0,0004` su tutte e tre le
frazioni di `voice`, e la pagina diceva `-0,0`.

La regola che lo risolve è una sola, e risolve anche il resto: **la precisione si
sceglie per colonna, non per valore.** Il minimo è **tre** decimali, e sale
finché nessun valore diverso da zero si arrotonda a zero. Ne seguono tre cose,
in quest'ordine — la prima comanda, le altre due la seguono:

- **nessun valore diverso da zero si rende come zero**, quindi uno zero con segno
  non può comparire: il segno si mostra solo dove c'è una cifra che lo sostiene.
  Il segno va tolto ogni volta che il valore *vale* zero, non solo quando è
  scritto `0.0`: in Python `-0.0` è un float con il bit di segno, e `f"{-0.0:.4f}"`
  restituisce `-0.0000`;
- **uno zero esatto prende le cifre della colonna** — `0,0000` in una colonna a
  quattro decimali — e non il segno. Non è una perdita, ed è la prima regola a
  renderlo vero: siccome nella colonna nessun valore diverso da zero si arrotonda
  a zero, `0,0000` significa **esattamente zero** e non "troppo piccolo per
  vedersi". La forma `0,0` è quella di una colonna di soli zeri, o di una
  formattazione senza colonna;
- **le cifre decimali si allineano.** `1,0`, `0,92` e `0,8` nella stessa colonna
  si confrontano peggio di `1,000`, `0,920` e `0,800`, perché la larghezza del
  numero smette di essere un indizio della sua grandezza. Il minimo della colonna
  è un **pavimento, non un bersaglio**: non si abbassa la precisione della colonna
  per adeguarla al valore più corto — `0,92` non diventa `0,9` perché la colonna
  contiene `1,0`. Un valore **più lungo** della precisione della colonna si
  arrotonda normalmente: `0,8571…` a tre decimali è `0,857`, e va bene — è la
  colonna a dichiarare la propria risoluzione, non il singolo valore a imporla.

Così la colonna dichiara da sola la propria risoluzione: quattro decimali su
`eccesso mirato` dicono, senza una parola in più, che lì si guardano i
decimillesimi.

**Le colonne legate da un'operazione condividono la precisione.** Quando in una
tabella una colonna è **calcolata** dalle altre, quelle colonne formano un gruppo
e prendono tutte la precisione più alta del gruppo. Senza questa regola la riga si
contraddice da sola: nel blocco `voice` del 14/09, `gigante dopo, mirata` e
`gigante dopo, a caso` valgono `0,8800` e `0,8796`, e a tre decimali diventano
**entrambe `0,880`** mentre la colonna accanto dichiara un `eccesso mirato` di
`-0,0004`. Il lettore vede `0,880 − 0,880 = −0,0004`, cioè una pagina che sembra
rotta — che è la regola 1 letta al contrario, e il difetto peggiore che questa
vista possa avere.

Le colonne che formano il gruppo in Robustezza sono quattro: `gigante prima`,
`gigante dopo, mirata`, `gigante dopo, a caso` ed `eccesso mirato`, che è
`(a caso − mirata) / prima`. **`z` non ne fa parte**: il suo denominatore è
`giant_after_random_sd`, che non si mostra, quindi `z` non è ricavabile dalla
tabella in nessun caso e la sua precisione resta la propria. Lo stesso vale per
`componenti dopo`.

Il criterio, non l'elenco: un gruppo esiste dove un numero mostrato si ottiene da
altri numeri mostrati. Dove il calcolo passa da una quantità che la tabella non
espone, gruppo non ce n'è.

**Che cosa è "la colonna": una tabella sola, non la pagina.** La precisione si
calcola sulle righe di *quella* tabella — il blocco di un layer sulle sue tre
righe, la tabella della serie sulle sue, un grafico sui propri punti per le
etichette dell'asse. Non sulla pagina intera.

Uniformare la precisione fra i quattro blocchi darebbe colonne omogenee, ma
creerebbe **un'unica colonna che attraversa i quattro layer** — cioè esattamente
la lettura che il layout a blocchi esiste per rendere scomoda (invariante 3, e
§4). Il prezzo è che due layer possono avere decimali diversi e la pagina risulta
un po' irregolare: è lo stesso prezzo dei quattro blocchi separati, pagato una
seconda volta e per la stessa ragione.

**Questo non è un modo per qualificare il numero, e non deve diventarlo.**
`-0,0004` su un grafo da 25 nodi è cento volte più piccolo di un nodo, che vale
`1/25 = 0,04`. A dirlo sono `nodes_removed` e `n_effective`, già sulla riga per
la regola 6: è esattamente il loro mestiere, e non serve un'etichetta nuova.

### Dove va l'etichetta di riga

`cella(row, campo)` restituisce le etichette della riga insieme a **ogni** valore,
ed è giusto così: è la firma che impedisce a un numero di viaggiare senza i suoi
flag. Ma renderle su ogni cella le moltiplica per il numero di colonne — nella
tabella di Robustezza sono sette colonne, cioè ventuno "non significativo" per
blocco e ottantaquattro per pagina — e un avviso ripetuto ottantaquattro volte
non distingue più niente da niente.

Quindi: **la dequalificazione visiva sta su ogni cella, l'etichetta testuale una
volta per riga**, in una posizione di riga e non attaccata a un numero.
Attaccarla al valore principale direbbe una cosa falsa — che è *quel* numero a
non essere significativo — mentre lo è la riga intera: quasi tutti i flag
qualificano la riga, non il campo ("La regola strutturale", sopra).

La posizione di riga è **una colonna propria, l'ultima**. Non è una scelta
estetica: nelle coorti su una stessa riga devono comparire **due** etichette
distinte — "non significativo" e "solo sopravvissuti" — e hanno bisogno di un
posto in cui stare insieme senza appoggiarsi a un valore.

**Non si collassa a livello di blocco**, nemmeno quando tutte le righe portano la
stessa etichetta. Dentro un blocco la significatività varia per riga —
`too_few_nodes_removed` scatta sulla sola frazione che rimuove meno di due nodi —
e una regola che sposta l'etichetta a seconda dei dati costringe chi guarda a
imparare due layout. Il grafico collassa a un'etichetta sola perché dodici punti
non hanno una posizione per riga; una tabella ce l'ha, ed è per questo che le due
regole differiscono senza contraddirsi.

**Verifica.** Per ogni riga renderizzata, ogni tipo di etichetta prodotto da
`cella()` compare **esattamente una volta** nella riga. Va verificato sul markup:
il macro che omette le etichette sulle celle di valore è anche il modo in cui
potrebbero sparire del tutto, e la differenza tra "una volta" e "mai" non si vede
guardando la pagina piena di grigio.

### La qualificazione di una serie

Le sei regole parlano di **celle**. Un grafico non è una cella: è
un'affermazione costruita da più righe, ognuna con la propria qualificazione.
Senza una regola propria, la serie le perde tutte — ed è esattamente il modo in
cui un valore finisce per viaggiare senza i flag che dicono quanto vale
(invariante 4 del progetto).

Per ogni **punto**:

- **soppresso** → il punto non si disegna, e la linea si interrompe visibilmente.
  Non c'è un numero: disegnare uno zero e saltare il punto congiungendo i vicini
  sono due modi diversi di inventarlo, e il secondo è peggiore perché non si
  vede.
- **non significativo** → il punto si disegna, dequalificato come la cella
  corrispondente. Nasconderlo toglierebbe la possibilità di vedere la serie
  formarsi, che è la ragione per cui in §5 quel numero si mostra.

Non esiste un terzo caso. **Su una riga pubblicata `targeted_excess` non è mai
`None`**: il job lo calcola sempre che il grafo non sia vuoto
(`job/robustness.py`), e un baseline degenere annulla `targeted_z`, non
`targeted_excess`. Il ramo difensivo nel codice resta — un `None` non deve mai
diventare uno zero né un punto inventato — ma non è uno stato, non si documenta
come tale e non si prova con una fixture costruita apposta: una fixture del
genere insegnerebbe alla vista a rendere una riga che l'API non produce, che è
l'errore che il fixture esiste per non commettere (§7).

Per il **segmento** tra due punti: eredita la qualificazione peggiore dei suoi
estremi. Una linea continua che attraversa un punto non significativo afferma
una continuità che quel punto non sostiene.

Per il **grafico intero**, non per la singola serie: se tutti i suoi punti sono
non significativi, l'etichetta "non significativo" compare **una volta sola**,
sul grafico. Oggi sarebbe il 100% dei punti di ogni serie, e ripeterla dodici
volte è il modo di insegnare a ignorarla — lo stesso argomento con cui §5 nega
un'etichetta a `None`.

Nel caso **misto** — alcuni punti significativi e altri no — non c'è etichetta di
grafico, e la legenda porta **obbligatoriamente** una voce che spiega lo stile
dequalificato. Non è un abbellimento: senza, la differenza di tratto è una
distinzione visibile e non spiegata, che è peggio di nessuna distinzione.

**Un layer assente in uno snapshot intermedio** (la riga non esiste, perché il
grafo di quel layer era vuoto) si comporta come un punto soppresso: la linea si
interrompe, e nessun simbolo. Se il layer manca da **tutti** gli snapshot
mostrati non c'è nessun grafico da disegnare: resta la sola frase del blocco, mai
una cornice di grafico vuota.

**Che cosa conta la regola 4: i punti disegnabili, non gli snapshot.**
L'argomento della regola è geometrico — con due punti non esiste nessuna forma
oltre alla retta — e vale sui punti che finiscono sul grafico, non su quelli che
esistono in tabella. Dodici snapshot di cui dieci soppressi sono due punti.

La regola si applica quindi su due livelli, e vanno tenuti distinti:

- **grafico o tabella**, per grafico: si disegna il grafico se **almeno una**
  delle tre serie raggiunge tre punti disegnabili; altrimenti si mostra la
  tabella della serie. La lettura letterale — "una serie sotto i tre punti e si
  mostra la tabella" — farebbe sparire il grafico di un intero layer per una
  sola serie corta, cioè lascerebbe a una soppressione il potere di cancellare
  ciò che le altre due serie hanno da dire.
- **linea o punti**, per serie: dentro un grafico disegnato, una serie con meno
  di tre punti disegnabili si mostra come **punti non congiunti**. Congiungerli
  sarebbe la retta che la regola 4 vieta; ometterli toglierebbe dati veri.

Quando le serie di un grafico condividono i punti disegnabili **per
costruzione**, i due livelli coincidono e la vista ne **esercita** uno solo:
l'altro resta nel codice come ramo difensivo, sotto la regola della sottosezione
"I rami difensivi". È il caso di Robustezza: le tre frazioni di un layer esistono
tutte o nessuna, e la soppressione vale per l'intero layer, quindi le tre serie
hanno sempre lo stesso numero di punti. Il secondo livello resta scritto qui
perché non è una regola di quella vista, ed è esigibile dove le serie di un
grafico misurano cose diverse.

Resta valido ciò che la regola 4 dice e non dice: tre punti bastano a
*disegnare*, non a *dire* che c'è una tendenza. Nessuna freccia, nessuna parola
come "in salita", nessuna retta di tendenza sovrapposta. Quelle appartengono
alla metrica 7 del catalogo, che non esiste.

### I rami difensivi, e perché non si provano

Tre punti del codice della dashboard gestiscono stati che **il job non produce**,
ma che il contratto dell'API non vieta:

- `targeted_excess` a `None` su una riga pubblicata — il job lo calcola sempre
  che il grafo non sia vuoto;
- `n_effective` diverso tra le righe di uno stesso `(snapshot, layer)` — il job
  usa lo stesso `graph.vcount()` per tutte;
- una serie con meno punti disegnabili delle sue sorelle nello stesso grafico —
  le tre frazioni di un layer esistono tutte o nessuna.

Il ramo resta nel codice, perché il contratto non li esclude e perché in tutti e
tre i casi fallisce in sicurezza: non mostra un numero inventato, mostra meno. Ma
**non si costruisce una fixture per provarlo**: una fixture così insegnerebbe
alla dashboard a rendere una risposta che l'API non può emettere, cioè il difetto
che il fixture esiste per non commettere (§7).

La distinzione va tenuta ferma, perché è facile usarla come scusa: un ramo che
**non si può raggiungere** si tiene e non si prova; un ramo che si raggiunge e
non è provato è un percorso di cui non si sa niente, e va provato.

## 6. Lo stato di oggi non è "vuoto": sono quattro stati diversi

La tentazione è trattare il presente come un unico "non ci sono ancora dati". È
sbagliato, e produrrebbe quattro schermate che dicono la stessa frase generica
mentre la situazione è diversa in ognuna.

**"Non significativo" non vuol dire "vuoto".** Sotto i 30 nodi i valori di
robustezza e community **vengono calcolati e scritti lo stesso**: scatta solo
`is_significant = False` con `not_significant_because: ["too_few_nodes"]`. Ci
sono numeri veri da mostrare. Quello che manca davvero è altro.

| Vista | Stato reale | Cosa deve dire la UI |
|---|---|---|
| **Stato** | Piena e corretta | *Il bot osserva dal 28 agosto, l'ultimo calcolo è di lunedì.* Nessun caveat. |
| **Robustezza** | 12 righe, **tutti i valori popolati**, tutte non significative | *Questi numeri esistono e non sono distinguibili dal rumore.* Il perché resta fuori: sta in `details`, e §5 lo vieta finché non è una colonna. |
| **Community** | Popolata; stabilità su un solo layer (dati del rerun del 15/09/2026). Lo snapshot 11 non ha precedente su nessuno dei quattro layer: `previous_gap_days` e `stability_jaccard` assenti, `no_previous_snapshot`. Lo snapshot 12 si confronta con l'11 con `previous_gap_days` **6,823** su tutti e quattro i layer, ma `stability_jaccard` c'è solo su `voice` (**0,333**): `mention`, `reaction` e `reply` hanno `node_overlap` sotto il minimo (`node_overlap_below_minimum`), non un precedente mancante. Nodi dall'11 al 12: `voice` 9→15, `mention` 26→29, `reaction` 24→25, `reply` 24→23. **Nessun layer è oggi `is_significant = true`**: tutti e quattro sono ancora sotto i 30 nodi (`too_few_nodes`), `voice`@12 compreso — che pure ha `modularity_z = 5,624` e `stability_jaccard = 0,333`, entrambi ben oltre le rispettive soglie. | *La struttura si vede, e su `voice` anche la stabilità: nessuna delle due letture dipende dal grafo essere "abbastanza grande" secondo la soglia strutturale, che qui non ha ancora acceso niente. Sugli altri tre layer la stabilità non si legge perché tra una settimana e l'altra sono cambiate troppe persone, non perché il grafo sia piccolo — sono due limiti diversi che oggi capitano insieme.* |
| **Coorti** | **25 coorti** sull'ultimo snapshot (14/09/2026), **50 righe di onboarding**: **12 soppresse** (`n<5`, 6 coorti × 2 `layer_scope`), **34 "solo sopravvissuti"** (17 coorti, anteriori all'ancora del 28/08), **4 né l'uno né l'altro** (2 coorti recenti, con copertura di snapshot). **Nessuna riga è oggi `is_significant = true`**: le uniche coorti non "solo sopravvissuti" non sono ancora mature (`observation_days < 14` dall'ultimo iscritto — la coorte del 31/08 ne ha 7). Retention: aritmeticamente **18** righe soppresse, **51** con `not_computable_reason = before_observability_anchor` (stessa causa di "solo sopravvissuti"), **6** sulle 2 coorti recenti (calcolabilità per orizzonte non verificata su dati reali in questa sessione — nessun accesso al database di produzione da qui). | *Il 68% delle coorti conta solo chi è rimasto da prima che Kindling iniziasse a osservare: quei numeri esistono ma contano le persone sbagliate. Le coorti nate dopo non sono ancora abbastanza mature per dire se l'integrazione funziona.* |

Sono quattro frasi diverse, e la differenza tra "non attendibile", "non ancora
calcolabile", "troppo pochi per essere mostrati" e "nessun caveat" è
precisamente l'informazione che la dashboard esiste per trasmettere.

**Le date che cambiano il quadro**, utili per non descrivere il presente come se
fosse permanente. La stabilità **è già popolata**, ma su un layer solo: dopo il
rerun del 15/09/2026 c'è soltanto su `voice` allo snapshot 12, con un gap di
6,823 giorni. Il primo valore mai prodotto era proprio quello che la regola 5
esiste per disinnescare — 1,0 su sette ore e mezza — e non esiste più in
produzione. Resta da venire il momento in cui la si legge su una cadenza
regolare e su più layer: il gap è già vicino a 7, ma sugli altri tre layer
`node_overlap` resta sotto il minimo. Le coorti diventano leggibili solo
quando la prima coorte posteriore all'ancora raggiunge i 14 giorni di
osservazione: al 14/09/2026 la coorte del 31/08 ne ha **7**, misurati
dall'ultimo iscritto (non da `cohort_start`) — matura quindi non prima del
**21/09/2026**, la stessa data che `stato-progetto.md` §9 indica già per altre
due verifiche attese (cadenza esatta a 7 giorni, `code_version` sulla run).
Maturare non basta da solo a renderla significativa: serve anche
`has_snapshot_coverage = true` sulla sua finestra, che la coorte del 31/08 ha
già (uno snapshot il 14/09 la copre). La
significatività strutturale non arriva con nessuna delle due: richiede 30 nodi,
**e** per le community anche `modularity_z ≥ 2,0`. Superare i 30 nodi non
accende tutto insieme, e la UI non deve promettere che lo faccia.

## 7. Si sviluppa contro il fixture, non contro la produzione

`tools/fixture_api.py` espone le stesse sette rotte con dati sintetici.
Si avvia con `uvicorn tools.fixture_api:app --port 8899` e si punta
`KINDLING_API_BASE_URL` lì.

**Perché non si sviluppa contro i dati veri.** Questo paragrafo diceva "la
produzione esercita oggi due stati su otto" — falso, verificato contro i dati
reali del 14-15/09/2026 nella sessione che ha progettato la vista Coorti (§4,
§6 sopra): riga soppressa (12 righe di onboarding su Coorti), `stability_jaccard`
popolata (`voice`, §6), retention non calcolabile e coorte di soli
sopravvissuti (entrambe lo stato dominante su Coorti, 34 e 51 righe) sono già
nei dati reali, non ipotetici. Quello che manca ancora, verificato per assenza
nella stessa sessione, è più mirato di "sei stati": **nessuna riga è oggi
`is_significant = true`, su nessuna vista** (§6), e l'unico valore anomalo di
`previous_gap_days` mai visto (1,0 su sette ore e mezza, regola 5) non esiste
più in produzione dopo il rerun del 15/09. E manca il **layer assente** (§4,
vista Robustezza: *nessuna interazione di questo tipo in questa settimana*):
sugli snapshot 11 e 12 i quattro layer ci sono tutti, `voice` compreso (9 e 15
nodi, §6); il fixture lo genera apposta su `…002`. *(Aggiunto il 19/09/2026.
Non era un'omissione della correzione del 16/09: questo paragrafo non ha mai
nominato il layer assente, nemmeno nella prima stesura del 14/09, che contava
"sei stati" senza di lui; la docstring di `tools/fixture_api.py`, scritta lo
stesso giorno, lo aveva. La correzione del 16/09 ha ereditato la lista corta
restringendola ancora, e nessuno dei due passaggi ha deciso che il layer
assente dovesse restarne fuori.)* La ragione di sviluppare contro il
fixture non è più "questi stati non sono mai successi": è che comparire una
volta in produzione, per caso, non li rende **riproducibili a comando** per un
test — e il valore anomalo di `previous_gap_days` lo dimostra: è successo, e un
rerun lo ha già cancellato. Un percorso di rendering mai eseguito non è codice
che funziona: è codice di cui non si sa niente.

**Quattro guild, quattro scenari**, perché scegliere la guild è già il gesto che
il flusso di autorizzazione richiede:

| Guild | Scenario |
|---|---|
| `900000000000000001` | Lo stato reale di oggi: **due** snapshot — l'11 pre-ancoraggio e il 12 ancorato — niente di significativo |
| `900000000000000002` | Dodici settimane di serie e stabilità calcolata. Tre layer grandi e significativi; `voice` è il layer a basso traffico — assente in due snapshot, sotto soglia in un altro — ed è quello che esercita l'interruzione della linea e il caso misto |
| `900000000000000003` | I casi che mordono: soppressione, `targeted_excess` negativo, baseline degenere, `node_overlap` sotto soglia, mediana non raggiunta, buco di osservazione, `code_version` assente |
| `900000000000000004` | La **scala** delle coorti (aggiunta il 16/09/2026 con la vista Coorti): 25 `cohort_start` su un solo snapshot, nelle stesse proporzioni della produzione — 6 soppresse, 17 anteriori all'ancora, 2 immature — più le significative che la produzione non ha, e due buchi negli snapshot di grafo che spengono la copertura di due coorti. Un secondo run, undici giorni dopo l'ancora, porta l'unica combinazione che non può coesistere con una coorte significativa (§4, punto 2) |

**Si costruisce contro `…003`**, che è il caso peggiore, si verifica la scala su
`…004`, e si controlla su `…001` e `…002`.

**Perché il fixture non può divergere dal contratto.** Non descrive la forma
delle risposte: istanzia i modelli di `api/models.py`, gli stessi che l'API usa
in produzione. Un campo rinominato rompe l'import subito, invece di lasciar
servire per mesi una forma che l'API non produce più. `python -m
tools.fixture_api --check` valida tutte le righe e impone l'invariante che in
produzione è imposta dal trigger `metric_suppressed_row_is_empty`: una riga
soppressa ha tutti i `values` a `None` e `details` vuoto.

**Non si butta via quando arrivano i dati.** Anche quando uno di questi stati
compare in produzione — è già successo, per le coorti soppresse e per quelle
di soli sopravvissuti — il fixture resta: è l'unico posto dove quello stato è
riproducibile a comando, non un fatto che la produzione fornisce quando serve
a un test. Nemmeno fra un anno la produzione darà su richiesta un
`previous_gap_days` anomalo: quello, verificato sopra, è già comparso una
volta e un rerun lo ha già cancellato.

## 8. Deploy, in due fasi

**Fase 1 — sviluppo, nessuna esposizione pubblica.** Il servizio `dashboard` si
aggiunge allo stesso `docker-compose.yml`, con la porta pubblicata **solo sul
loopback dell'host**:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

Non è "autenticazione posticcia" vietata da `api.md` §5 — è "non esposto", che è
un'altra cosa. In questa fase l'OAuth si può sviluppare ma non è ancora il
guardiano di niente.

**`127.0.0.1:8000:8000` e non nessun `ports:`, ed è la differenza che questo
progetto ha già pagato.** Una prima stesura di questa nota diceva "senza
`ports:` pubblicati, raggiungibile via tunnel SSH": le due cose sono
incompatibili. Senza **nessuna** porta pubblicata, nemmeno sul loopback,
`localhost:8000` sulla droplet non risponde, quindi un `ssh -L
8000:localhost:8000` — che si appoggia proprio al `localhost` della droplet —
fallisce con `connection refused` pur essendo il tunnel perfettamente
funzionante. È l'errore n. 3 di `CLAUDE.md`, costato ore di debug su fail2ban e
formato della chiave prima di arrivare alla causa vera.

La regola del progetto è **"mai su tutte le interfacce"**, non "mai una sezione
`ports:`". Un binding su `127.0.0.1` non è raggiungibile da internet in nessun
caso — lo stesso che Postgres ha dal primo giorno — ed è ciò che rende
possibile guardare la dashboard prima che esista un dominio.

**Nota su `api`**: oggi non ha nessun `ports:`, e `api.md` §5 gli attribuisce lo
stesso "via tunnel SSH" che qui era sbagliato. Non è un problema attivo — nessuno
ha ancora avuto bisogno di aprire un tunnel verso l'API, e `docker compose exec`
basta per interrogarla dall'interno della droplet. Ma la frase è imprecisa nello
stesso modo, e va corretta lì quando si tocca quel documento.

La forma del servizio, perché non sia una decisione presa a margine del codice:

| | |
|---|---|
| Nome del servizio | `dashboard` |
| Immagine | lo stesso `Dockerfile` di `bot`, `api` e `job` |
| Porta | `8000` nel container, pubblicata **solo** su `127.0.0.1:8000` dell'host — mai su tutte le interfacce. Nessun conflitto con `api`, che non pubblica niente |
| `environment` | **solo** `KINDLING_API_BASE_URL` (più, in fase 2, le variabili OAuth). Nessuna variabile di database: vedi §1 |
| `mem_limit` | `150m` (§9) |
| `command` | `uvicorn dashboard.main:app --host 0.0.0.0 --port 8000 --workers 1` |
| `healthcheck` | sulla propria `/health`, che verifica di saper raggiungere l'API |
| `depends_on` | `api`, con `condition: service_healthy` |

Il `depends_on` non è cerimonia: `api.md` §2 lo aveva già anticipato come la
ragione per cui `GET /health` esiste sull'API — *"serve al `depends_on:
condition: service_healthy` del compose quando arriverà il dashboard"*. Quella
previsione si incassa adesso.

**Fase 2 — esposizione.** Prerequisiti, tutti insieme e nessuno dopo:

1. dominio registrato e record A verso la droplet (la redirect URI di Discord
   deve essere HTTPS, esatta e stabile: un IP non basta);
2. servizio `caddy` nel compose, TLS automatico, davanti **alla sola
   dashboard**;
3. Cloud Firewall: 80/443 aperte verso Caddy, nient'altro;
4. applicazione Discord OAuth configurata, con la redirect URI definitiva;
5. il flow di §3 completo e verificato, log del callback inclusi.

Il giorno in cui compare un `ports:` **su tutte le interfacce**, o Caddy, i
cinque punti devono essere già fatti. Il binding su `127.0.0.1` della fase 1 non
è quel giorno: non è raggiungibile da internet, ed è la ragione per cui la
regola del progetto parla di interfacce e non di sezioni `ports:`. Allargare
quel binding prima dei cinque punti è la versione dashboard dell'incidente di
`CLAUDE.md`.

**L'eccezione di Caddy, decisa adesso e non il giorno in cui servirà.** Il test
che impone il loopback vale su *ogni* servizio del compose, quindi il giorno in
cui si aggiunge Caddy — che deve pubblicare 80 e 443 su tutte le interfacce, è
il suo mestiere — quel test fallisce. È il comportamento voluto: costringe a
un'eccezione esplicita nel momento esatto in cui il sistema diventa raggiungibile
da internet, invece di lasciar passare la modifica in silenzio.

Quando arriverà, il test si **aggiorna**, non si aggira. E l'eccezione è stretta:
il solo servizio Caddy, le sole porte 80 e 443. Ogni altro servizio, e ogni
altra porta di Caddy, restano sul loopback. Un'eccezione scritta come "Caddy può
pubblicare quello che vuole" riaprirebbe per intero la regola che questo test
esiste per tenere chiusa.

**Fase 2: vedi `dashboard-fase2.md`.** La forma del servizio `caddy`, i due
hostname, Cloudflare, e l'ordine dei passi dell'esposizione (8-bis, 8-ter). La
tabella qui sopra descrive il servizio `dashboard` della fase 1: in fase 2
cambiano il `command` e l'`environment` (8-bis).

## 9. Memoria

`mem_limit: 150m` sul servizio — margine sopra i 54 MB misurati, ma un tetto.
Uvicorn con 1 worker, per la stessa ragione dell'API: 1 vCPU condiviso con
l'heartbeat del gateway Discord.

Nessuna cache delle risposte dell'API in questa fase. I dati cambiano una volta
a settimana e le letture sono puntuali su tabelle minuscole: una cache
aggiungerebbe una seconda nozione di "quando è aggiornato questo numero" accanto
ad `as_of`, e due nozioni di attualità che possono divergere sono un difetto,
non un'ottimizzazione.

## 10. Igiene dello sviluppo

Screenshot, export CSV, dump di risposte JSON presi da dati reali: tutto in
`dashboard-dev/`, **aggiunta a `.gitignore` prima della prima riga di codice**.
Il repo è pubblico, e l'11/09 cinque dump con dati di persone reali erano a un
`git add -A` dal finire online. `.gitignore` oggi copre `*.dump`, `*.sql.gz` ed
`exports/` — **non** copre `*.csv`, `*.png` né `*.json`, che sono precisamente i
tre formati che lo sviluppo di una dashboard produce.

**`tools/` non va aggiunta ai `COPY` del `Dockerfile`**, ed è l'inverso esatto
dell'errore n. 5 di `CLAUDE.md`: lì `api/` andava aggiunta e nessuno lo ha
fatto. Qui è un attrezzo di sviluppo che non deve mai finire in un'immagine. La
riga va scritta nel commento in testa al `Dockerfile`, altrimenti il prossimo
che legge l'errore n. 5 la aggiunge per coerenza.

## 11. Fuori da questa fase

- **Nessuna visualizzazione del grafo.** Né nodi, né archi, né vicinati. È
  `api.md` §7, e non cambia perché c'è una UI.
- **Nessuna aggregazione calcolata nella dashboard.** Se serve un aggregato che
  non esiste, si aggiunge al job. Un aggregato costruito a runtime aggira
  soglie e soppressione, ed è la stessa violazione di un'API che leggesse
  `raw_events`.
- **Nessun export** (CSV, PDF, immagini). Un export è un file che esce dal
  perimetro e continua a esistere senza i suoi flag: il numero soppresso
  diventa una cella vuota in un foglio che gira.
- **Nessun alerting e nessuna soglia di attenzione** (metrica 7 del catalogo):
  richiede una serie storica che non esiste ancora.
- **Nessun punteggio sintetico**, in nessuna forma, mai. Vedi §5, regola 3.

## 12. Voci aperte del tema chiaro (misurate, non risolte)

Trovate il 21/09/2026 misurando i contrasti per scrivere il tema scuro
(`dashboard/static/dashboard.css`, blocco `prefers-color-scheme: dark`). Sono
difetti del tema **chiaro** — quello che è in produzione da sempre — e il tema
scuro li ha già evitati per costruzione. Non sono stati corretti insieme a
quello perché il fix cambia i colori di pagine che oggi si vedono: una tavolozza
in chiaro si decide guardandola, non si innesta a margine di un commit che
parlava d'altro. Stessa regola dei follow-up aperti di `CLAUDE.md`.

I rapporti sono misurati, non stimati: luminanza relativa WCAG 2.x per il
contrasto, matrici di Machado et al. a severità 1,0 per le simulazioni di
visione dei colori. Sono riproducibili dai valori scritti sotto, che sono quelli
letterali di `:root`.

### 12.1 Le tre serie dei grafici non si distinguono in deuteranopia né in protanopia

`--serie-0: #2f5d8a` (blu), `--serie-1: #b0601f` (ambra), `--serie-2: #4d7a3a`
(verde). Contro il fondo stanno bene — 6,58:1, 4,43:1 e 4,83:1, tutte sopra il
3:1 che una linea richiede. **Fra loro no**:

| coppia | visione tipica | deuteranopia | protanopia |
|---|---|---|---|
| ambra / verde | 1,09:1 | **1,24:1** | **1,11:1** |
| blu / ambra | 1,49:1 | 1,73:1 | 1,17:1 |
| blu / verde | 1,36:1 | 1,40:1 | 1,30:1 |

Le tre sono separate in **tinta** e quasi per niente in luminanza, e la tinta è
esattamente ciò che un occhio deuteranope o protanope non usa: ambra e verde,
simulate, diventano due olive alla stessa luce. Chi guarda vede tre linee e non
può dire quale sia quale.

Dove morde: la serie di Robustezza (§4), che porta tre soglie di rimozione nello
stesso grafico più i tre campioni di legenda che le nominano. Community disegna
una serie sola per grafico e non è toccata.

Il tema scuro lo evita separando le tre anche in luminanza — 8,29:1, 5,14:1 e
14,05:1 sul fondo — e la coppia che conta, ambra/verde, resta a 2,50:1 in
deuteranopia e 3,16:1 in protanopia.

**Fix non fatto qui**: rifare i tre colori chiari sullo stesso criterio, cioè
una scala di luminanza invece di tre tinte alla stessa forza. Cambia l'aspetto
di ogni grafico in chiaro, ed è la ragione per cui è una voce e non una riga.
Distinguere le serie anche per tratto sembra la via facile e **non lo è**: il
tratteggio è già preso, qualifica il "non significativo" (§5), e usarlo per due
cose diverse nello stesso grafico è peggio del difetto che risolverebbe.

### 12.2 `--grigio` è a 3,29:1, sotto la soglia del testo

`--grigio: #8a8a8f` sul fondo della pagina dà 3,29:1. Sugli altri fondi su cui
cade davvero scende: 3,12:1 sulle righe alternate delle coorti, 2,98:1 dentro
`--grigio-fondo`, **2,85:1** sotto il passaggio del mouse. La soglia per un
testo è 4,5:1, e questo è testo: lo portano il valore soppresso, il non
calcolabile, l'assente e il numero dequalificato (§5). È il colore con cui si
legge proprio ciò di cui bisogna accorgersi.

Il tema scuro sta sopra 4,5:1 su tutti e quattro quei fondi (5,49 / 5,12 / 4,68
/ 4,61), e gli è costato qualcosa: lì `--grigio` finisce vicino a `--tenue`
(6,25:1) e la distanza fra i due quasi sparisce. In chiaro quella distanza c'è
(3,29 contro 5,07) ma è comprata sotto soglia — cioè il dequalificato si
distingue dal testo secondario perché è meno leggibile del dovuto.

**Fix non fatto qui**: portare il grigio ad almeno 4,5:1 sul fondo **più
scuro** su cui cade — `--coorti-hover`, dove oggi sta a 2,85:1 — e non sul solo
`--fondo`, che è il più chiaro dei quattro e quindi quello che dà il numero
migliore. Misurare sul fondo sbagliato è come i 3,29:1 di oggi sono stati
accettati. Va deciso insieme a §12.1, perché tocca la stessa tavolozza e gli
stessi grafici.
