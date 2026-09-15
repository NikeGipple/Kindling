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
| **Coorti** | `/guilds/{id}/cohorts` | per coorte: 2 onboarding + 3 retention | onboarding: `event_count`, `censored_count`, `censored_by_leave`, `median_days_to_k`, `median_reached`, `p25/p75_days_to_k`, `reached_by_14d/28d` — retention: `retained_fraction`, `is_computable`, `not_computable_reason` |

Parametri che fissano quei conteggi, da `job/config.py`: layer `voice / reply /
mention / reaction`; frazioni di rimozione `0,05 / 0,10 / 0,20`; `layer_scope`
delle coorti `any / voice`; `k = 5`; orizzonti di retention `7 / 14 / 28`;
classi di dimensione `small / 5-9 / 10-19 / 20-49 / 50-99 / 100+`.

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
nessuna libreria, nessuna risorsa esterna: `base.html` non ne carica, la
dashboard sta dietro un tunnel SSH e non deve dipendere da internet per
rendersi. Un grafico che ha bisogno di una CDN è un grafico che un giorno non si
disegna.

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

### Sette regole che valgono ovunque

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
| **Community** | Popolata; stabilità su un solo layer (dati del rerun del 15/09/2026). Lo snapshot 11 non ha precedente su nessuno dei quattro layer: `previous_gap_days` e `stability_jaccard` assenti, `no_previous_snapshot`. Lo snapshot 12 si confronta con l'11 con `previous_gap_days` **6,823** su tutti e quattro i layer, ma `stability_jaccard` c'è solo su `voice` (**0,333**): `mention`, `reaction` e `reply` hanno `node_overlap` sotto il minimo (`node_overlap_below_minimum`), non un precedente mancante. Nodi dall'11 al 12: `voice` 9→15, `mention` 26→29, `reaction` 24→25, `reply` 24→23 | *La struttura si vede. La stabilità si legge solo su `voice`, e su una distanza quasi settimanale; sugli altri tre layer tra una settimana e l'altra sono cambiate troppe persone perché il confronto dica qualcosa.* |
| **Coorti** | Quasi tutto assente: soppressione a N=5, `is_mature` richiede 14 giorni dall'ultimo iscritto, `has_snapshot_coverage=False` sulle coorti anteriori all'ancora | *Non ci sono ancora coorti abbastanza numerose e abbastanza osservate.* |

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
`node_overlap` resta sotto il minimo. Le coorti diventano leggibili solo quando la prima coorte posteriore
all'ancora raggiunge i 14 giorni di osservazione. La
significatività strutturale non arriva con nessuna delle due: richiede 30 nodi,
**e** per le community anche `modularity_z ≥ 2,0`. Superare i 30 nodi non
accende tutto insieme, e la UI non deve promettere che lo faccia.

## 7. Si sviluppa contro il fixture, non contro la produzione

`tools/fixture_api.py` espone le stesse sette rotte con dati sintetici.
Si avvia con `uvicorn tools.fixture_api:app --port 8899` e si punta
`KINDLING_API_BASE_URL` lì.

**Perché non si sviluppa contro i dati veri.** La produzione esercita oggi due
stati su otto. Gli altri sei — riga soppressa, valore significativo,
`stability_jaccard` popolata, `previous_gap_days` anomalo, retention non
calcolabile, coorte di soli sopravvissuti — comparirebbero per la prima volta in
produzione, da soli, quando nessuno sta guardando. Un percorso di rendering mai
eseguito non è codice che funziona: è codice di cui non si sa niente.

**Tre guild, tre scenari**, perché scegliere la guild è già il gesto che il
flusso di autorizzazione richiede:

| Guild | Scenario |
|---|---|
| `900000000000000001` | Lo stato reale di oggi: **due** snapshot — l'11 pre-ancoraggio e il 12 ancorato — niente di significativo |
| `900000000000000002` | Dodici settimane di serie e stabilità calcolata. Tre layer grandi e significativi; `voice` è il layer a basso traffico — assente in due snapshot, sotto soglia in un altro — ed è quello che esercita l'interruzione della linea e il caso misto |
| `900000000000000003` | I casi che mordono: soppressione, `targeted_excess` negativo, baseline degenere, `node_overlap` sotto soglia, mediana non raggiunta, buco di osservazione, `code_version` assente |

**Si costruisce contro `…003`**, che è il caso peggiore, e si controlla su
`…001` e `…002`.

**Perché il fixture non può divergere dal contratto.** Non descrive la forma
delle risposte: istanzia i modelli di `api/models.py`, gli stessi che l'API usa
in produzione. Un campo rinominato rompe l'import subito, invece di lasciar
servire per mesi una forma che l'API non produce più. `python -m
tools.fixture_api --check` valida tutte le righe e impone l'invariante che in
produzione è imposta dal trigger `metric_suppressed_row_is_empty`: una riga
soppressa ha tutti i `values` a `None` e `details` vuoto.

**Non si butta via quando arrivano i dati.** Nemmeno fra un anno la produzione
darà su richiesta una coorte soppressa o un `previous_gap_days` anomalo.

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
