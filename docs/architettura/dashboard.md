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

Nel codice è `median_reached = median is not None`. Vale `False` quando **meno
di metà della coorte ha raggiunto le k connessioni** entro l'osservazione: la
mediana della curva di sopravvivenza non esiste perché la curva non scende mai
sotto il 50%.

Quindi non si mostra come cella vuota né con il simbolo di soppressione — che
direbbero "manca un dato". Si mostra come **frase affermativa**: *meno di metà
della coorte ha raggiunto k connessioni*. È un risultato, non un'assenza, ed è
con ogni probabilità il dato più interessante di quella riga. `p25_days_to_k`,
`p75_days_to_k`, `reached_by_14d` e `reached_by_28d` restano leggibili e vanno
mostrati accanto: sono il modo in cui quella riga dice ancora qualcosa.

### Sei regole che valgono ovunque

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
   `values` qualifica un altro campo di `values`. È anche già successo: sullo
   snapshot 11 valeva 1,0 su tutti e quattro i layer, misurata su sette ore e
   mezza.
6. **`removal_fraction` non si mostra mai senza `nodes_removed` accanto.** La
   percentuale è l'input; il conteggio è quello che è successo. A 14 nodi il 5%
   è **zero nodi**, e il `targeted_excess` di `0,00` che ne risulta si legge come
   "togliere il 5% dei connettori non rompe niente" mentre il fatto è che non è
   stato tolto nessuno. Il flag `too_few_nodes_removed` scatta, ma il numero
   resta visibile: è l'etichetta accanto a doverlo disinnescare.

Nota di rendering: `targeted_excess` **può essere negativo e non è clampato**
(significa che i nodi più centrali erano meno critici di nodi presi a caso). Un
asse y ancorato a zero lo nasconderebbe.

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
| **Robustezza** | 12 righe, **tutti i valori popolati**, tutte non significative | *Questi numeri esistono ma il grafo è troppo piccolo perché siano distinguibili dal rumore.* |
| **Community** | Metà popolata: `community_count`, `modularity`, `modularity_z` ci sono; **tutta la stabilità è NULL** finché non esistono due snapshot | *La struttura si vede; quanto sia stabile non si può ancora dire, serve un secondo snapshot.* |
| **Coorti** | Quasi tutto assente: soppressione a N=5, `is_mature` richiede 14 giorni dall'ultimo iscritto, `has_snapshot_coverage=False` sulle coorti anteriori all'ancora | *Non ci sono ancora coorti abbastanza numerose e abbastanza osservate.* |

Sono quattro frasi diverse, e la differenza tra "non attendibile", "non ancora
calcolabile", "troppo pochi per essere mostrati" e "nessun caveat" è
precisamente l'informazione che la dashboard esiste per trasmettere.

**Due date che cambiano il quadro**, utili per non descrivere il presente come
se fosse permanente: con il secondo snapshot la riga di stabilità si popola per
la prima volta (a condizione che `node_overlap ≥ 0,50`, altrimenti resta NULL
con `node_overlap_below_minimum`); le coorti diventano leggibili solo quando la
prima coorte posteriore all'ancora raggiunge i 14 giorni di osservazione. La
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
| `900000000000000001` | Lo stato reale di oggi: un punto, niente di significativo |
| `900000000000000002` | Dodici settimane di serie, valori significativi, stabilità calcolata |
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
