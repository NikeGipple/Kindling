# Modello delle metriche aggregate — dagli archi ai numeri che l'admin legge

*Specifica del layer di metriche aggregate. Scritta il 3 settembre 2026. È il
documento di riferimento per il sottocomando `metrics` del job: la struttura e
le definizioni operative qui descritte sono la fonte di verità, il codice le
insegue. I valori numerici dei parametri sono provvisori e da ritarare sui dati
reali; le definizioni no.*

Parte da dove `modello-grafo.md` finisce — agli archi — e arriva alle tabelle
che l'API leggerà. Copre le tre metriche indicate come priorità MVP in
`metriche-aggregate-admin.md`: **robustezza strutturale** (catalogo §1),
**struttura e stabilità delle community con Leiden** (catalogo §3),
**onboarding e retention per coorte** (catalogo §5).

## 1. Vincoli ereditati

Non sono in discussione in questa specifica: vengono da decisioni già prese.

- **Nessun profilo individuale esposto agli amministratori**
  (`architettura.md`). Centralità, appartenenza a una community e conteggio
  delle connessioni del singolo membro sono passaggi di calcolo interni. Vedi
  §8 per dove muoiono esattamente.
- **Multirelazionale**: relazioni di tipo diverso si calcolano separatamente e
  non si sommano in un'unica sociomatrice senza una ragione sostanziale
  esplicita (`modello-grafo.md` §1). Vedi §2.1.
- **Soglia minima di cardinalità N applicata nel layer di calcolo**, non nella
  dashboard (`metriche-aggregate-admin.md`). Vedi §6.
- **Il job legge solo Postgres**, mai l'API Discord. Il layer metriche legge
  `graph_snapshots`, `graph_edges` e `members`, e nient'altro.
- **Le righe con `forgotten_at` valorizzato sono sempre escluse**, come nel
  resto del job. Conseguenza da conoscere: una richiesta di oblio cambia
  retroattivamente un aggregato già calcolato. È il comportamento voluto — un
  dato cancellato non deve poter rientrare dalla finestra di un derivato — e
  significa che un ricalcolo può legittimamente non riprodurre un numero
  vecchio.
- **Le tabelle del grafo sono interne**: `graph_edges` non viene mai esposta.
  Le tabelle definite in §9 sono, al contrario, le uniche pensate per essere
  lette dall'API.
- **`python-igraph` e `leidenalg`**, mai NetworkX: il job gira su una droplet
  da 1 GB condivisa con bot e Postgres (`architettura.md` §3).

## 2. Su cosa si calcolano le metriche strutturali

### 2.1 Per layer, mai su un grafo fuso

Robustezza e Leiden si calcolano **separatamente su ciascun layer**. Non esiste
in v0 un grafo unione, né una sociomatrice sommata: sarebbe esattamente la
fusione che `modello-grafo.md` §1 vieta, e non c'è nessuna ragione sostanziale
per farla qui. Sommare un minuto di co-presenza vocale a una reply richiede un
tasso di cambio tra le due cose che nessuno ha, e la robustezza calcolata su
quella somma sarebbe un numero di cui non si può dire di quale relazione parla.

Ogni riga di ogni tabella strutturale porta quindi il proprio `layer`, e il
calcolo viene tentato su tutti e quattro. Non tutti daranno un risultato
significativo, ed è corretto così: su L'Arco del Leone il traffico è
schiacciantemente vocale, quindi ci si aspetta che `voice` sia l'unico layer
con abbastanza struttura per reggere queste metriche, e che `reply`, `mention`
e `reaction` risultino non significativi per un pezzo. La differenza tra "non
significativo" e "assente" è informativa e va conservata (vedi §7): un layer
`reply` che comincia a diventare significativo è di per sé un cambiamento
della community.

**Come si legge una divergenza tra layer.** I layer non si mediano e non si
riassumono in un indice unico. Una divergenza è un dato, non un problema da
risolvere: `voice` robusto e `reply` fragile significa che la connettività
vocale regge alla rimozione dei suoi connettori mentre quella testuale dipende
da poche persone — due condizioni diverse della stessa community, con due
interventi diversi. Se in futuro servirà un indice sintetico, sarà una
decisione da prendere e motivare allora, non una comodità da introdurre ora
collassando i layer.

### 2.2 Proiezione non diretta dei layer direzionali

Robustezza e Leiden si calcolano sulla **proiezione non diretta** dei layer
`reply`, `mention` e `reaction`: i due orientamenti della stessa coppia
diventano un arco solo, con peso pari alla somma dei due.

Motivazione: entrambe le metriche sono domande sulla **connettività** — "se
tolgo questo nodo il grafo si spezza?", "quali nodi stanno insieme più di
quanto ci si aspetterebbe?" — e la connettività si legge sul grafo non
orientato. La modularità direzionale e le componenti fortemente connesse
esistono, ma introdurrebbero una seconda famiglia di numeri con una lettura
diversa senza che nessuna domanda del catalogo la richieda.

Questa somma non viola §1: avviene **dentro un layer**, tra due orientamenti
della stessa relazione, non tra relazioni di tipo diverso. La perdita di
informazione è reale e ha un posto dove essere recuperata: la direzionalità è
il contenuto della metrica di **reciprocità aggregata** (catalogo §6), fuori da
questa v0.

La proiezione usata è registrata nei parametri della run
(`directed_layer_projection: "undirected_sum"`), perché è una scelta
sostituibile e uno snapshot deve poter dichiarare come è stato costruito.

### 2.3 Quale peso, e quali archi entrano nel grafo

Si usa **`weight`** — normalizzato per la dimensione della sessione e decaduto
rispetto all'`as_of` dello snapshot.

- È il peso "rispetto a un istante", cioè l'unico coerente con la domanda che
  queste metriche pongono, che è sullo **stato attuale** della rete e non sulla
  sua storia cumulata. Uno snapshot dichiara il proprio `as_of` proprio perché
  senza quello i pesi non sono interpretabili (`modello-grafo.md` §5).
- `weight_undecayed` **non** si usa nel calcolo principale: darebbe la struttura
  di una rete che comprende legami spenti da settimane. Resta il termine di
  paragone naturale quando si vorrà ritarare l'emivita `H`, ed è per quello che
  è salvato.
- `raw_units` **non** si usa mai: non è normalizzato per dimensione della
  sessione, quindi reintrodurrebbe l'inflazione da gruppo affollato che
  `modello-grafo.md` §3.3 corregge apposta.

**Ammissione dell'arco: `weight > min_edge_weight`**, con
`min_edge_weight = 0.0` di default — cioè, oggi, `weight > 0` strettamente. Un
arco il cui ultimo contributo è oltre il cutoff `C` ha peso esattamente zero per
costruzione (la curva di decadimento è normalizzata perché `f(C) = 0` esatto).
Lasciarlo nel grafo non è neutro: un arco di peso nullo **tiene comunque insieme
due componenti**, quindi cambierebbe il conteggio delle componenti connesse e la
dimensione della componente gigante — cioè proprio le grandezze della metrica di
robustezza. Un legame spento non connette niente e non deve entrare.

**Il problema non finisce a zero: gli archi quasi spenti.** La curva di
decadimento scende con continuità, quindi il peso non passa da "pieno" a "zero"
di colpo. Con `H = 7` e `C = 30` giorni, una singola interazione di 29,9 giorni
fa contribuisce un peso dell'ordine di `1e-5`. Per la topologia quel valore non
è diverso da zero in nessun senso utile: **un arco di peso `1e-5` tiene insieme
due componenti esattamente come uno di peso 1**, perché le componenti connesse
non guardano i pesi. Una coppia che si è incrociata una volta un mese fa può
quindi da sola impedire alla rete di risultare frammentata, e la robustezza
misurata è quella di una connettività che non esiste più.

**Una sola definizione di arco ammesso, per tutti i percorsi.** La regola —
proiezione non diretta, poi la soglia sulla somma dei due orientamenti — è una
funzione sola, usata sia dalla costruzione del grafo strutturale sia dal
conteggio dei partner delle coorti (§5.2). Due implementazioni separate
coinciderebbero solo finché `min_edge_weight` vale 0.0: alla prima volta che si
alza, una coppia con due orientamenti sotto soglia che sommano sopra sarebbe un
partner per le metriche strutturali e non per le coorti, senza che niente
protesti. I pesi di **layer diversi** non si sommano mai, nemmeno qui: una
coppia sotto soglia su due layer separati resta sotto soglia.

`min_edge_weight` esiste per poter alzare quella soglia quando i dati diranno
dove sta il confine tra un legame debole e un residuo numerico. Resta a `0.0` in
v0 perché fissarlo ora significherebbe tararlo su tre giorni di dati, e perché
il default conserva esattamente la regola precedente: alzarlo sarà un cambio di
parametro registrato in `metric_runs.params`, non una riprogettazione. La
betweenness, al contrario delle componenti, i pesi li guarda: lì un arco da
`1e-5` diventa una scorciatoia lunghissima (distanza `1/weight = 1e5`) e non
falsa quasi nulla. È la connettività, non la centralità, il punto sensibile.

**Nodi**: il grafo di un layer è **indotto dai suoi archi**. Un membro senza
nessun arco in quel layer non è un nodo di quel grafo. Conseguenza da tenere a
mente leggendo i risultati: il numero di nodi non è il numero di membri della
community, e "la componente gigante copre il 90%" significa il 90% di chi ha
almeno una relazione in quel layer, non il 90% degli iscritti al server.

### 2.4 Archi ricostruiti (`is_reconciled`)

**Inclusi** nel calcolo principale. Escluderli per default introdurrebbe un
bias sistematico verso i periodi in cui il bot è stato giù: quella co-presenza
è avvenuta davvero, e cancellarla renderebbe la rete artificialmente più
sparsa proprio nelle settimane con più downtime.

Ma la riconciliazione inventa un estremo dell'intervallo, e una conclusione che
dipende da dati inventati va saputa. Quindi **ogni metrica strutturale viene
calcolata anche una seconda volta escludendo gli archi con `is_reconciled`**, e
le grandezze chiave della variante vengono salvate nel blocco `details` della
riga come verifica di sensibilità:

- robustezza: `targeted_excess` senza archi ricostruiti;
- community: numero di community e modularità senza archi ricostruiti.

Non due righe separate, ma un confronto dentro la stessa riga: la domanda non è
"quanto vale la metrica senza i ricostruiti", è "la lettura cambia se li tolgo".
Se cambia, il numero principale va guardato con sospetto, ed è il tipo di cosa
che nessuno andrebbe a verificare a mano.

**Quando nessun arco del layer è ricostruito — il caso normale — la variante
coincide con il calcolo principale e non si ricalcola**: sarebbero `R`
ripetizioni di baseline per ogni `X` più `R` rewiring con Leiden, cioè il
raddoppio della voce di costo che §11.1 identifica come dominante, per un
risultato identico. Il fatto che coincida va però **registrato** in `details`
(`without_reconciled: {identical: true}`): "nessuna differenza" e "non
calcolata" sono due cose diverse a valle, e solo la prima autorizza a concludere
che il risultato non dipende da dati ricostruiti.

## 3. Robustezza strutturale (catalogo §1)

### 3.1 Cosa si misura

Si rimuove il top X% dei nodi per centralità e si misura quanto resta insieme
il grafo residuo. Grandezze registrate, per ogni (snapshot, layer, X):

- `giant_before` — frazione dei nodi nella componente connessa più grande
  **prima** della rimozione, sul totale dei nodi del layer;
- `giant_after_targeted` — stessa frazione dopo la rimozione mirata, **sempre
  rapportata al numero di nodi originale**, non a quelli residui. Rapportarla
  ai residui nasconderebbe metà dell'effetto: togliere il 20% dei nodi e
  trovare che "il 100% dei rimasti è ancora connesso" è vero e fuorviante;
- `components_after_targeted` — numero di componenti connesse dopo la rimozione;
- le stesse due grandezze per il baseline casuale (§3.3);
- `targeted_excess` — l'indice di lettura (§3.4).

### 3.2 Quale centralità, e quanti nodi

**Betweenness pesata.** La domanda del catalogo è esplicitamente sui
*connettori* — "la community dipende da un numero ristretto di connettori?" —
e la betweenness è la misura di brokeraggio: conta quante volte un nodo sta sul
cammino più breve tra altri due. Un ordinamento per grado risponderebbe a una
domanda diversa (chi ha più legami, non chi tiene insieme parti che altrimenti
si staccherebbero), ed è la metrica di concentrazione strutturale del catalogo
§2, fuori da questa v0.

**Trappola da chiudere nel codice: igraph interpreta `weights` come
*lunghezze*, non come forze.** Il peso di un arco di Kindling è una forza: più
alto, più le due persone sono legate. Passarlo direttamente a
`betweenness(weights=...)` significherebbe dire a igraph che i legami forti
sono *lontani*, e il risultato sarebbe la betweenness di una rete rovesciata.
La distanza da passare è **`1/weight`**, e questo è un requisito della
specifica, non un dettaglio di implementazione.

**X**: non un valore solo. Il catalogo dichiara che X va validato empiricamente
e che un valore troppo piccolo o troppo grande rende la simulazione poco
informativa — quindi fissarne uno a tavolino ora sarebbe scegliere in silenzio
proprio il parametro che il catalogo segnala come aperto. Si calcola su una
**griglia fissa X ∈ {5%, 10%, 20%}** e si salvano tutte e tre le righe. Costa
tre esecuzioni di un calcolo già economico e rende il parametro ritarabile
guardando i dati invece che ricalcolandoli.

Numero di nodi rimossi: `ceil(X · n)`, con minimo 1. Se il minimo 1 è anche
tutto ciò che X consente (grafi piccoli), la riga risulterà comunque non
significativa per la regola di §7.1 — ma il numero rimosso è registrato, così
si vede che 5% e 10% hanno rimosso lo stesso nodo.

**Pareggi.** Su grafi piccoli molti nodi hanno betweenness zero e l'ordinamento
è arbitrario: senza una regola, il risultato dipenderebbe dall'ordine in cui
igraph ha ricevuto gli archi, cioè dall'ordinamento della query. I nodi si
ordinano per `(betweenness decrescente, author_id crescente)` e si prendono i
primi `ceil(X · n)`. Il tiebreak su `author_id` è interno e non esce da nessuna
parte (§8); serve solo a rendere il taglio deterministico.

### 3.3 Il baseline: senza, il numero non dice niente

"Rimuovo il top 10% e restano 4 componenti" non è un risultato: qualunque grafo
si frammenta se si toglie abbastanza. La domanda è se si frammenta **più di
quanto farebbe togliendo nodi a caso** — è la differenza tra "fragile in
assoluto" e "fragile rispetto a un grafo delle sue dimensioni e della sua
densità".

Baseline: la stessa rimozione — **lo stesso numero di nodi**, non la stessa
percentuale ricalcolata — fatta a caso, ripetuta `R` volte (default 100). Si
salvano media e deviazione standard di `giant_after` e `components_after` sulle
R ripetizioni.

Il campionamento casuale usa un generatore inizializzato con il **seed della
run** (§4.2 e §10), salvato nei parametri: senza, due esecuzioni sullo stesso
snapshot darebbero due baseline diversi e la variazione tra snapshot
misurerebbe in parte il rumore del baseline.

### 3.4 L'indice di lettura

```
targeted_excess = (giant_after_random_mean − giant_after_targeted) / giant_before
```

Quanta componente gigante in più si perde attaccando i connettori rispetto ad
attaccare a caso, in frazione di quella iniziale.

- ≈ 0 → la rete non ha connettori privilegiati: togliere i nodi più centrali
  fa lo stesso danno che toglierne a caso. Connettività distribuita.
- alto → la connettività dipende da pochi nodi. Fragile.

Si salva anche `targeted_z`, lo scostamento in deviazioni standard del baseline
(`(giant_after_random_mean − giant_after_targeted) / giant_after_random_sd`),
che è la forma in cui si legge se lo scarto è distinguibile dalla variabilità
del caso; è NULL quando la deviazione standard del baseline è zero.

Tutti gli ingredienti restano in tabella come colonne: l'indice deve poter
essere ricalcolato con una formula diversa senza rieseguire il job.

## 4. Struttura e stabilità delle community — Leiden (catalogo §3)

### 4.1 Cosa si misura

Per ogni (snapshot, layer):

- `community_count` — numero di community rilevate;
- `modularity` — modularità della partizione;
- distribuzione delle dimensioni, in una tabella figlia e già soppressa (§6.3);
- `stability_jaccard` — stabilità rispetto allo snapshot precedente (§4.4);
- i conteggi di community nate, dissolte, fuse, scisse (§4.5).

### 4.2 Determinismo: seed obbligatorio, e non solo per igiene

Leiden è stocastico. Senza un seed fissato, due esecuzioni sullo stesso grafo
possono dare partizioni diverse, e la "stabilità tra snapshot" finirebbe per
misurare il rumore dell'algoritmo invece del cambiamento della community — cioè
esattamente il contrario di quello che deve misurare.

Requisiti, tutti e quattro necessari:

1. **`leiden_seed` fissato** in configurazione e salvato nei parametri della run.
2. **`n_iterations = -1`** (iterare fino a convergenza) invece del default:
   riduce la variabilità residua a parità di seed.
3. **Ordinamento deterministico dei nodi** nel grafo prima di passarlo a
   `leidenalg`. Il risultato di Leiden dipende dall'ordine in cui i nodi sono
   visitati; se quell'ordine dipende dall'ordine di lettura degli archi da
   Postgres, il seed da solo non basta a garantire la riproducibilità. I nodi
   si inseriscono ordinati per `author_id` crescente.
4. **Il generatore di igraph va legato al seed durante il rewiring del baseline**
   (§7.2). `Graph.rewire` non usa il generatore che gli si passa: usa quello di
   igraph, che è uno **stato globale di processo** e di default è il modulo
   `random`. Senza legarlo, `modularity_random_mean`, `modularity_random_sd` e
   `modularity_z` cambiano tra due esecuzioni sullo stesso snapshot — e con loro
   `is_significant`, che è un flag pubblicato. Essendo stato globale, va anche
   **ripristinato** dopo l'uso: il calcolo delle metriche non deve cambiare il
   comportamento di qualunque altro uso di igraph nello stesso processo.

C'è una seconda ragione, strutturale, per cui il seed non è opzionale:
**la partizione dello snapshot precedente non viene salvata da nessuna parte**
(è un dato per-nodo, §8), quindi per calcolare la stabilità la si **ricostruisce**
rieseguendo Leiden sugli archi di quello snapshot. Questa ricostruzione è
esatta solo se il calcolo è deterministico. Il determinismo è ciò che permette
di non persistere un output per-nodo, non solo una comodità di riproducibilità.

### 4.3 Funzione obiettivo

`ModularityVertexPartition` con i pesi degli archi. La grandezza che il catalogo
chiede di riportare è la **modularità della partizione**: usare la modularità
anche come funzione obiettivo rende il numero riportato coerente con ciò che è
stato effettivamente ottimizzato, invece di riportare il valore di una funzione
che l'algoritmo non stava massimizzando.

Limite noto e dichiarato: la modularità ha un **limite di risoluzione** e tende
a non separare community piccole in reti grandi. La sostituzione naturale è
CPM (`CPMVertexPartition`) con un parametro di risoluzione, che però va tarato
sulla scala dei pesi — e la scala dei pesi di Kindling dipende da `H`, che è a
sua volta il primo parametro da ritarare. Farlo ora significherebbe tarare un
parametro sopra un parametro provvisorio. CPM è il candidato numero uno al
passaggio quando ci saranno mesi di dati; la funzione obiettivo usata è salvata
nei parametri della run, così gli snapshot vecchi restano leggibili.

### 4.4 Identità delle community tra snapshot

**Le community non hanno identità tra due snapshot.** La community #3 di questa
settimana non è la #3 della scorsa: gli indici che `leidenalg` restituisce sono
posizioni in una lista, non nomi. Un Jaccard calcolato sugli indici confronta
due insiemi scelti a caso, e produce un numero che sembra una misura di
stabilità e non lo è.

Serve una regola di **matching esplicita tra partizioni**:

1. **Nucleo comune — per il Jaccard, non per i conteggi.** Il confronto di
   *somiglianza* si fa solo sui nodi presenti in **entrambi** i grafi. Un
   Jaccard calcolato sull'unione mescolerebbe due fenomeni diversi: la
   ricomposizione delle community e il ricambio di membri. Il ricambio si
   riporta a parte, come `node_overlap` (frazione di nodi comuni sull'unione),
   che è un numero interessante di per sé.

   La restrizione **non** si estende ai quattro conteggi di §4.5, che si
   calcolano sulle partizioni intere: vedi lì il perché.
2. **Sovrapposizione.** Per ogni coppia (community precedente P, community
   nuova Q) ristrette al nucleo comune si calcola
   `J(P,Q) = |P ∩ Q| / |P ∪ Q|`.
3. **Accoppiamento greedy 1-1.** Le coppie si ordinano per `J` decrescente e si
   accoppiano una alla volta, saltando quelle in cui P o Q è già accoppiata, e
   fermandosi sotto `jaccard_match_min` (default 0.30). Un accoppiamento
   bipartito ottimo (Hungarian) sarebbe più corretto ma richiederebbe `scipy`,
   che non è tra le dipendenze e che non si giustifica su una droplet da 1 GB
   per questo solo uso. L'approssimazione è dichiarata qui e nei parametri
   (`partition_matching: "greedy_jaccard"`), non nascosta nel codice.
4. **Stabilità aggregata.** Media dei `J` delle coppie accoppiate, **pesata per
   la dimensione della community precedente**. Senza il peso, una community di
   3 persone conterebbe quanto quella da 200 e il numero racconterebbe
   soprattutto le fluttuazioni delle micro-community. Le community precedenti
   non accoppiate contribuiscono con `J = 0` al peso, altrimenti la stabilità
   ignorerebbe proprio le community sparite.

### 4.5 Nascita, morte, fusione, scissione

L'accoppiamento 1-1 lascia fuori i casi interessanti, che vanno contati
esplicitamente invece di sparire dentro "non accoppiata".

**I quattro conteggi si calcolano sulle partizioni intere, non sul nucleo
comune.** È una deroga esplicita alla restrizione di §4.4, e la ragione è che
le due grandezze rispondono a domande diverse: il Jaccard è una misura di
*somiglianza*, e restringerla al nucleo comune la protegge dal ricambio; un
conteggio di gruppi non è una misura di somiglianza. Restringerlo renderebbe
invisibile il caso più interessante — una community composta interamente da
nodi assenti la settimana prima **è** una community nata, ed è proprio il
fenomeno che si vuole vedere su una community in crescita. Tenere tutti e
quattro sulla stessa popolazione è anche ciò che li rende confrontabili tra
loro.

- **`born`** — community nuova senza nessun match sopra soglia, **comprese
  quelle interamente fuori dal nucleo comune**.
- **`merged`** — community precedente non accoppiata i cui membri
  *sopravvissuti* (quelli ancora nel grafo) sono finiti per almeno
  `merge_min_share` (default 0.50) dentro **una sola** community nuova. Non è
  sparita: è stata assorbita.
- **`split`** — community precedente non accoppiata i cui membri sopravvissuti
  si distribuiscono su ≥ 2 community nuove senza che nessuna raggiunga
  `merge_min_share`.
- **`dissolved`** — community precedente non accoppiata che non ricade in
  nessuno dei due casi sopra, compresa quella che non ha più **nessun** membro
  nel grafo: una community senza sopravvissuti non può essersi né fusa né
  scissa.

**Limite da conoscere, e da leggere accanto a `node_overlap` — vale per
`dissolved` e per `born`, in modo simmetrico.**

`dissolved` conta insieme due cose diverse: "queste persone ci sono ancora ma
non stanno più insieme" e "queste persone non sono più nel grafo". La seconda è
ricambio, non disgregazione — e con il decadimento dei pesi (`H` = 7 giorni,
`C` = 30) un nodo esce dal grafo anche solo smettendo di interagire per un mese,
senza lasciare il server.

`born` ha esattamente la stessa asimmetria dall'altro lato: un nodo **rientra**
nel grafo semplicemente riprendendo a interagire, quindi una parte di `born`
ogni settimana è ricambio di popolazione e non nascita di una community. Un
gruppo di persone tornate attive insieme dopo tre settimane di pausa si presenta
identico a un gruppo di nuovi arrivati che si sono trovati.

`node_overlap` è il numero che separa le due letture, e i quattro conteggi non
vanno letti senza. Un `born` o un `dissolved` alto con `node_overlap` basso
racconta un ricambio di popolazione; con `node_overlap` alto racconta community
che si formano o si sciolgono davvero. Sono letture opposte, e i conteggi da
soli non le distinguono.

I quattro conteggi sono colonne, non un JSONB: una community che si dissolve e
una che si fonde sono segnali opposti sulla domanda guida ("mantenimento delle
relazioni"), e vanno poter essere letti come serie senza aprire un JSON.

### 4.6 Quando la stabilità non è definita

`stability_jaccard` è NULL, con `is_significant = false` e la ragione in
`details`, quando:

- non esiste uno snapshot precedente per quella guild;
- lo snapshot precedente è stato calcolato con **parametri del grafo diversi**
  (`graph_snapshots.params` non identico) o con parametri delle metriche diversi:
  in quel caso la differenza tra le due partizioni contiene il cambio di
  parametri, e attribuirla alla community sarebbe falso;
- i **parametri sono vuoti** da almeno una delle due parti. `{}` è il default di
  uno snapshot scritto prima che la colonna esistesse, e significa "non so con
  cosa è stato calcolato" — non "stessi parametri". Due `{}` non sono
  confrontabili tra loro più di quanto lo siano con qualunque altra cosa, e sono
  proprio gli snapshot vecchi quelli che finiranno per essere usati come
  precedente;
- le **finestre hanno ampiezza diversa**. `window_end − window_start` non è un
  parametro e non compare in `params`, ma due finestre diverse producono grafi
  di densità diversa, e la stabilità calcolata tra loro misura il cambio di
  finestra chiamandolo ricomposizione della community. **Non è un caso
  teorico**: il primo snapshot copre ~3 giorni e i successivi ne coprono 7,
  quindi la prima stabilità mai calcolata cadrebbe esattamente lì. Il confronto
  sull'ampiezza è esatto — due esecuzioni con la stessa `--window-days` danno la
  stessa durata al microsecondo, quindi una differenza non è rumore numerico;
- `node_overlap < min_node_overlap` (default 0.50): sotto metà di nodi in
  comune, il confronto non riguarda più abbastanza la stessa popolazione.

Uno snapshot precedente "vicino ma non identico" nei parametri non viene
adattato né riscalato: la stabilità semplicemente non è calcolabile, e lo dice.

## 5. Onboarding e retention per coorte (catalogo §5)

### 5.1 Coorte

**Settimana ISO del `joined_at`**, per guild. Allineata alla cadenza settimanale
degli snapshot, e sufficientemente larga da avere qualche persona dentro anche
in una community piccola. La riga di coorte è identificata dal lunedì della
settimana (`cohort_start`).

La segmentazione alternativa suggerita dal catalogo ("iscritti durante un
evento" vs organici) richiede di legare un join a un evento, che oggi non è
osservabile: fuori da v0.

Una coorte più vecchia di `cohort_max_age_days` (default 180) non viene più
ricalcolata a ogni run: il suo esito è ormai fermo e il costo di rileggerne gli
snapshot no.

### 5.2 Che cosa conta come "connessione distinta"

Un **partner distinto**: una persona con cui il membro ha, in almeno uno
snapshot dall'ingresso in poi, un arco ammesso al grafo (§2.3) e con
`interaction_count ≥ partner_min_interactions`. Non un peso, non un'intensità —
una persona.

**`partner_min_interactions`, default 1** — cioè, oggi, "una qualunque
interazione basta". La soglia si applica alla coppia **dopo** la proiezione non
diretta (§2.3), cioè alla somma degli orientamenti: su un layer direzionale una
relazione fatta di una reply per verso ha **due** interazioni, non una per
direzione. È la conseguenza dell'avere una regola di ammissione sola, ed è la
lettura giusta — la soglia riguarda la relazione, non la singola direzione — ma
va detta, perché con due regole separate lo stesso parametro avrebbe significato
"interazioni in una direzione".

Per `layer_scope = 'any'` la soglia resta **per layer**: un partner qualifica se
qualifica in almeno un layer, e le interazioni di layer diversi non si sommano
per raggiungerla. Sommarle costruirebbe il peso combinato che §1 vieta. Il default conserva il comportamento più semplice, ma il
limite va dichiarato adesso e non scoperto dopo: con la soglia a 1, un membro
che nel primo giorno lascia cinque reazioni emoji a cinque persone diverse
risulta **integrato** esattamente come uno che ha passato cinque serate in
vocale con cinque persone. Non è la stessa cosa, e la metrica esiste per
verificare se l'integrazione rapida predice la retention: se "integrato"
comprende cinque emoji, la correlazione che si sta cercando viene misurata
contro una definizione che non la può contenere.

**Il modo in cui questa metrica fallisce è sembrando funzionare.** Un `k` troppo
facile non produce un errore né un valore assurdo: produce coorti in cui quasi
tutti raggiungono `k` quasi subito, cioè una curva di sopravvivenza che crolla
nei primi giorni e una mediana bassa e stabile — che si legge come "l'onboarding
funziona benissimo". Il segnale da cercare, quando ci saranno dati, è proprio
quello: se `reached_by_14d` è vicino a 1 per ogni coorte e non discrimina nulla
rispetto alla retention, il problema è la definizione di partner, non la
community. `partner_min_interactions` e `k` vanno ritarati insieme — alzare solo
`k` con una soglia di partner a 1 rende la metrica più severa senza renderla più
significativa.

**`layer_scope`.** Il conteggio si calcola due volte e si salvano entrambe le
righe:

- `voice` — solo il layer di co-presenza vocale, il layer portante su L'Arco;
- `any` — l'unione degli insiemi di partner su tutti i layer.

`any` è **un'unione di insiemi di persone, non una somma di pesi**, e per questo
non viola il vincolo di §1: non richiede un tasso di cambio tra un minuto di
voce e una reply, e non produce nessun peso combinato. Dice "con quante persone
distinte questo membro ha avuto una qualunque relazione", che è la domanda che
la metrica di onboarding pone. Resta una decisione che tocca un vincolo
ereditato ed è elencata tra i punti da confermare in revisione (§13).

`reply`, `mention` e `reaction` singolarmente sono ammessi dallo schema ma non
calcolati in v0: su L'Arco produrrebbero coorti tutte sotto soglia.

### 5.3 Il tempo all'evento è misurato in snapshot, non in giorni

Il grafo esiste solo a snapshot: non c'è modo di sapere dagli archi in quale
istante esatto un membro ha raggiunto la sua k-esima connessione, e
ricostruirlo dagli eventi grezzi significherebbe rifare qui la ricostruzione
delle sessioni, che è la parte costosa e delicata del calcolo del grafo.

Definizione operativa: si scorrono gli snapshot della guild in ordine di
`as_of`, si accumulano i partner distinti del membro, e il tempo all'evento è
`as_of_snapshot − joined_at` del **primo snapshot in cui il conteggio cumulato
raggiunge k**.

Conseguenze da dichiarare, tutte e tre:

1. La granularità è quella della cadenza degli snapshot (una settimana), non il
   giorno. Un tempo mediano di "7 giorni" significa "entro il primo snapshot
   utile", non "esattamente una settimana".
2. Due coorti sono confrontabili solo se la serie di snapshot sotto di loro ha
   la stessa cadenza. Una settimana in cui il job non è stato eseguito produce
   **censura intervallare**: l'evento è avvenuto in una finestra più larga.

   In `details` finiscono tre numeri, **sempre tutti e tre**, anche quando la
   run non li produce — in quel caso valgono `null`, perché una chiave assente e
   una che vale zero devono restare distinguibili come ovunque altrove:

   - `snapshots_used` — quanti snapshot la scansione ha effettivamente letto;
   - `snapshots_skipped_params` — quanti **esistono** ma non sono confrontabili
     (§4.6);
   - `snapshot_gaps` — la spaziatura della serie: `cadence_days_median`
     (mediana delle distanze tra `as_of` consecutivi) e `max_gap_days`. È
     `null` con meno di due snapshot, perché una spaziatura non esiste e non
     è zero.

   Gli ultimi due non si sostituiscono a vicenda: uno snapshot che esiste ma è
   stato calcolato con parametri diversi finisce nel secondo; una settimana in
   cui il cron è fallito e lo snapshot **non c'è** finisce nel terzo. Un cron
   settimanale che salta un giro produce solo il terzo.

   `snapshot_gaps` riporta due numeri e **nessun flag**: dire "c'è un buco"
   richiederebbe una soglia, e una soglia fissata oggi su tre giorni di dati
   sarebbe un parametro senza base messo davanti a un dato che si legge da sé —
   se il massimo è il doppio della mediana, una settimana manca.
3. Si usano **solo gli snapshot confrontabili** con quello corrente — stessi
   parametri *e* stessa ampiezza di finestra, esattamente la regola di §4.6, che
   è una sola e vale per entrambi gli usi. Uno snapshot non confrontabile è un
   buco, contato come tale, non un dato da mescolare.

Il partner conta come connessione fatta anche se in seguito l'arco decade: la
domanda è "quanto ci mette a farne k", e l'evento è averle fatte. Il
mantenimento è la domanda della stabilità delle community, non di questa.

### 5.4 Censura a destra: il punto centrale

**La coorte entrata la settimana scorsa non ha ancora avuto il tempo di
raggiungere k connessioni. Contarla come "non integrata" è un errore, non un
dato.** Su una coorte giovane, la frazione grezza "quanti hanno raggiunto k"
non è una misura bassa dell'integrazione: è una misura di quanto poco tempo è
passato.

Trattamento: **stimatore di Kaplan–Meier** sul tempo dal `joined_at` al
raggiungimento di k connessioni.

```
S(t) = Π   (1 − d_i / n_i)      su tutti i tempi di evento t_i ≤ t
```

dove `d_i` è il numero di membri che raggiungono k al tempo `t_i` e `n_i` il
numero ancora a rischio appena prima. Un membro è **censurato** — esce dal
denominatore senza contare come fallimento — quando:

- non ha ancora raggiunto k all'`as_of` della run (osservazione incompleta);
- è uscito dal server (`left_at`) prima di raggiungere k.

Il secondo caso è, propriamente, un **rischio competitivo** e non una censura:
chi se ne va prima di integrarsi non è "uno di cui non sappiamo ancora", è uno
che non si integrerà. Trattarlo come censura fa sì che Kaplan–Meier
**sovrastimi** la probabilità di integrazione, tanto più quanto più chi esce
presto è sistematicamente meno integrato — che è proprio l'ipotesi che questa
metrica esiste per verificare. È una semplificazione consapevole di v0: il
numero di censure per uscita è salvato a parte (`censored_by_leave`) così la
distorsione è visibile, e il modello a rischi competitivi è in §12.

**Cosa si pubblica**, con il proprio flag di calcolabilità ciascuno:

**Nessuna estrapolazione finché la curva non è arrivata a zero.** Oltre il tempo
più lungo osservato nella coorte, prolungare la curva in avanti produce in
generale un numero che non viene dai dati — con una sola eccezione, che non è
una concessione ma un fatto: se a `max_observed` la sopravvivenza è **esattamente
zero**, tutti hanno avuto l'evento, la curva resta a zero per costruzione e
`1 − S(t) = 1` è esatto per ogni `t` successivo.

La distinzione è ciò che separa i due casi che l'admin deve poter distinguere:
una coorte tutta integrata entro il giorno 3 e osservata per un mese *ha* una
risposta a 28 giorni, ed è 1.0; una coorte con un evento al giorno 2 e il resto
ancora in sospeso non ce l'ha. Una regola basata solo su `max_observed` le
farebbe sparire entrambe, cioè renderebbe il caso migliore indistinguibile da
quello indeterminato.

- `median_days_to_k` — il primo `t` con `S(t) ≤ 0.5`. Se la curva non scende mai
  sotto 0.5 nell'osservazione disponibile, la mediana **non è raggiunta**: il
  valore è NULL e `median_reached = false`. Mai un numero estrapolato.
- `p25_days_to_k`, `p75_days_to_k` — stessa regola.
- `reached_by_14d`, `reached_by_28d` — `1 − S(t)` a orizzonte fisso, cioè la
  frazione stimata di coorte integrata entro 14 e 28 giorni. Sono più leggibili
  di una mediana spesso non raggiunta, e per un admin sono la forma azionabile
  della metrica — cioè quella che verrà letta davvero, e quindi il posto in cui
  un numero estrapolato fa più danno. **NULL quando l'orizzonte supera il tempo
  massimo osservato e restano censurati**; `1.0` quando la curva è già a zero a
  quel punto. Senza la prima metà della regola basterebbe un singolo evento
  perché la curva rispondesse a qualunque orizzonte: una coorte osservata tre
  giorni, con un evento al giorno 2, pubblicherebbe un `reached_by_28d` ricavato
  da due giorni di dati.
- `event_count`, `censored_count`, `censored_by_leave` — gli ingredienti.

La **curva di sopravvivenza completa non viene pubblicata** in v0: i suoi
gradini sono di ampiezza `1/n_i` e su una coorte piccola raccontano quante
persone hanno fatto cosa e quando, con una granularità che le colonne sopra non
hanno. È calcolata internamente e buttata.

**Maturità della coorte.** Anche con Kaplan–Meier, una coorte osservata per tre
giorni produce una curva su cui quasi tutto è NULL. La coorte viene **comunque
scritta**, con `observation_days` e `is_mature` (osservazione ≥
`min_observation_days`, default 14): sopprimere le coorti giovani nasconderebbe
il fatto che esistono, e riportarle senza dire che sono giovani è il modo in cui
si legge un numero incompleto come se fosse un risultato.

### 5.5 Retention

Per coorte e orizzonte `T ∈ {7, 14, 28}` giorni: frazione dei membri della
coorte con `left_at IS NULL OR left_at ≥ joined_at + T`.

**Stessa popolazione di coorte di `metric_cohorts`**, cioè al netto dei rientri
sospetti esclusi da §5.6. Non è un dettaglio implementativo: `metric_cohorts` e
`metric_cohort_retention` vengono lette affiancate — l'intero senso della
metrica è incrociare "quanto in fretta si sono integrati" con "quanti sono
rimasti" — e due denominatori diversi per la stessa coorte in due tabelle
affiancate producono un incrocio sbagliato che non si nota mai, perché entrambe
le tabelle restano internamente coerenti. Per questo `excluded_rejoins` è
riportato **anche** in `metric_cohort_retention` (§9.5): la sua uguaglianza tra
le due tabelle è verificabile con una query, e una divergenza è un bug visibile
invece che un numero plausibile.

**Calcolabile solo se `as_of ≥ max(joined_at della coorte) + T`**, cioè se
*tutti* i membri della coorte hanno avuto T giorni di osservazione. Altrimenti
il valore è NULL con `is_computable = false`. Senza questa regola una coorte
entrata ieri risulterebbe con "100% di retention a 28 giorni", che è il modo
più diretto di trasformare l'assenza di dati in un ottimo risultato.

### 5.6 Rientri: cosa comporta l'overwrite di `members`

`members` sovrascrive `joined_at`/`left_at` quando un membro rientra dopo
un'uscita (scelta esplicita dell'MVP, `CLAUDE.md`). Per le coorti questo ha
quattro conseguenze, tutte da dichiarare perché nessuna è visibile nel numero
finale:

1. **Le coorti passate non sono stabili nel tempo.** Un membro che rientra viene
   riassegnato alla coorte del rientro e la sua coorte originale lo perde
   retroattivamente. Ricalcolare una coorte vecchia oggi può dare un valore
   diverso da quello di un mese fa **senza che nessun dato sia stato corretto**.
2. **La retention delle coorti vecchie è sottostimata**: chi è uscito e
   rientrato scompare dalla coorte originale invece di risultare tornato.
3. **L'integrazione delle coorti recenti è sovrastimata**: un rientrante riparte
   con un `joined_at` nuovo ma con le sue relazioni preesistenti già nel grafo,
   quindi raggiunge k quasi subito e abbassa il tempo mediano della coorte in
   cui è finito.
4. I bias (2) e (3) vanno in **direzioni opposte** e non si compensano: colpiscono
   coorti diverse.

**Mitigazione applicata in v0.** Un rientrante è riconoscibile senza cambiare
`members`: se esiste attività della persona in quella guild **antecedente al suo
`joined_at`**, o è un rientro o è un dato incoerente — in entrambi i casi non è
un nuovo membro di cui misurare l'onboarding. Questi membri vengono **esclusi
dalle coorti** e contati a parte (`excluded_rejoins`), con
`exclude_suspected_rejoins` come parametro (default `true`).

Nessun falso positivo dai membri caricati con `!backfill_members`: quel comando
scrive la data di join reale, che precede la loro attività.

Questa mitigazione riduce (3) e non risolve (1) e (2): la correzione vera è
tracciare i rientri, che è fuori dall'MVP e resta in §12.

## 6. Soppressione sotto soglia N

### 6.1 Il valore di N e dove si applica

**N = 5** come punto di partenza. È il minimo dell'intervallo indicato dal
catalogo (5-10) ed è il valore convenzionale della k-anonymity nelle tabelle di
frequenza delle statistiche ufficiali — una convenzione diffusa, non un
risultato di letteratura specifico per le reti sociali. Resta un parametro
salvato nei parametri della run, alzabile senza migrazione, ed è tra le cose da
ritarare per prime.

Va detto senza girarci intorno: **con i dati di oggi N = 5 sopprimerà quasi
tutte le righe che contano persone** — le coorti settimanali, in una community
in cui gli ingressi sono pochi per settimana, e i bucket delle community
piccole. È il comportamento corretto: una tabella quasi tutta soppressa in
questa fase è il segno che la regola funziona, non che la soglia è sbagliata.
Le righe strutturali, invece, verranno scritte e pubblicate anche adesso, e
marcate come non significative — vedi subito sotto perché non è una
contraddizione.

**Applicazione anche alle metriche puramente strutturali, con una soglia
propria.** Il catalogo dice che robustezza (§1) e densità (§4) non richiedono
soglia perché sono "già un rapporto sull'intero grafo". Qui si va oltre il
catalogo — una riga strutturale il cui grafo ha meno di `min_nodes_publish` nodi
è soppressa — ma la soglia è un **parametro distinto da `N`**
(`min_nodes_publish`, default: uguale a `N`), e la motivazione va data
onestamente, perché quella di `N` non regge.

`N` protegge da un'inferenza sulle persone: "i membri isolati sono 2" nomina un
insieme di due persone che l'admin può indovinare. Le grandezze strutturali non
funzionano così: `targeted_excess`, `modularity`, la frazione di componente
gigante sono **rapporti sull'intero grafo**, e conoscerne il valore non isola
nessuno né dice nulla di verificabile su un membro specifico. Il grafo reale di
oggi ha 9 nodi, supera `N = 5`, e viene pubblicato: se la giustificazione fosse
l'anonimato, 9 nodi non sarebbero abbastanza e pubblicare quelle righe sarebbe
una violazione. **Non lo è, ed è per questo che quelle righe si pubblicano** —
non perché 9 nodi bastino a rendere anonimo qualcuno.

Il rischio reale delle righe strutturali su grafi piccoli è un altro, e non è
l'anonimato: è che il numero **non significhi niente**. Quello è già gestito, e
in modo più preciso di una soglia di pubblicazione, da `is_significant` e da
`min_nodes_structural` (§7): un grafo di 9 nodi produce righe scritte,
pubblicabili e marcate come non significative — che è l'informazione corretta,
mentre sopprimerle direbbe "non mostrabile", cioè una cosa falsa.

`min_nodes_publish` esiste comunque, separato, per il caso residuo in cui una
grandezza strutturale diventi de-anonimizzabile per contesto (un grafo di 3
nodi con una componente gigante di 2 dice qualcosa su una coppia specifica), e
perché la soglia dell'anonimato e quella della pubblicabilità strutturale non
devono muoversi insieme per forza: alzare `N` a 10 per proteggere le coorti non
deve, di per sé, far sparire le metriche strutturali di una community piccola.

### 6.2 Come si rappresenta una cella soppressa

Mai con uno zero. Zero è un valore legittimo e diverso da "non mostrabile", e
una tabella che li confonde è una tabella in cui il valore soppresso è
indistinguibile da un risultato reale.

Ogni riga porta:

| Colonna | Semantica |
|---|---|
| `n_effective` | numerosità su cui la riga è calcolata (nodi del grafo, membri della coorte) — **NULL** se la riga è soppressa |
| `is_suppressed` | `true` sotto la soglia applicabile alla riga: `N` per le righe che contano persone, `min_nodes_publish` per le righe strutturali (§6.1); oppure per soppressione secondaria (§6.4) |
| `suppression_reason` | testo breve: `below_threshold`, `secondary`, … |
| tutte le colonne di valore | **NULL** quando `is_suppressed` |

Anche `n_effective` viene azzerato a NULL: scrivere "coorte di 3 persone,
valori soppressi" pubblica comunque il fatto che quella settimana sono entrate
tre persone, che è a sua volta un'informazione sotto soglia.

**"Colonna di valore" significa ogni colonna fuori dalla chiave primaria, non
solo quelle che sembrano un risultato.** Su una riga soppressa vanno a NULL
anche i conteggi ausiliari e gli ingredienti diagnostici, perché la numerosità
si ricava per differenza o per divisione da quasi tutti: `nodes_removed` è
`ceil(X · n)` e con `X` in chiave restituisce `n` a meno di un'unità;
`event_count + censored_count` è la dimensione della coorte; una frazione come
`giant_before = 0.888…` ha `n` per denominatore. Lasciarne anche uno solo
significa pubblicare la numerosità che la soppressione doveva nascondere,
attraverso una colonna che nessuno guarda come "il valore". L'unica cosa che
sopravvive su una riga soppressa è la chiave primaria più `is_suppressed`,
`suppression_reason` e `details` — e questo è il vincolo che lo schema deve
imporre (§9.3).

**I flag di calcolabilità sono nullable, e su una riga soppressa sono NULL.**
`is_significant`, `is_mature` e `is_computable` non possono essere
`NOT NULL DEFAULT FALSE`: un vincolo che pretende NULL su una colonna
`NOT NULL` rende la riga soppressa semplicemente non scrivibile. Ma la ragione
non è solo tecnica, ed è quella che conta: **`false` afferma che la metrica è
stata valutata e non supera i minimi**, mentre su una riga soppressa non è stata
valutata affatto. Sono due stati diversi, e collassarli su `false` rimetterebbe
in tabella la stessa confusione che §6.2 evita per lo zero — un giudizio negativo
al posto di un'assenza. NULL = non valutato.

**`details` è l'unica eccezione: resta `NOT NULL DEFAULT '{}'`.** Su una riga
soppressa deve valere `'{}'::jsonb`, non NULL. Un JSONB vuoto non pubblica
niente — è la stessa quantità di informazione di un NULL — mentre il default
`'{}'` serve a tutte le altre righe, che scrivono in `details` senza doverlo
inizializzare. Renderla nullable per l'unico caso in cui deve essere vuota
peggiorerebbe la colonna per tutti gli altri. Il vincolo lo dice esplicitamente
invece di lasciarlo dedurre.

La garanzia non è affidata al codice che scrive: ogni tabella porta un
**`CHECK` che rende impossibile scrivere un valore su una riga soppressa**
(§9). È la ragione principale per cui queste tabelle hanno colonne tipizzate e
non un valore JSONB — vedi §9.1.

### 6.3 Community piccole

La distribuzione delle dimensioni delle community non riporta mai una community
sotto N come voce a sé: le community con meno di N membri sono accorpate in un
bucket unico `small` che ne riporta il conteggio e il totale dei membri. Se il
totale dei membri del bucket `small` è a sua volta `< N`, anche quel bucket è
soppresso.

Le classi di dimensione hanno estremi fissi, la prima delle quali parte da N:
`small` (`< N`), `[N,10)`, `[10,20)`, `[20,50)`, `[50,100)`, `[100,∞)`. Se
N ≥ 10 la seconda classe scompare.

**Il vocabolario dei bucket dipende da N, e questo rompe la serie storica.**
L'etichetta `'5-9'` non è un nome: è `[N,10)` con `N = 5`. Alzando `N` a 6 la
stessa etichetta indicherebbe un intervallo diverso, e a 10 sparirebbe del
tutto — mentre le righe vecchie in tabella continuerebbero a portarla. Una serie
storica letta per etichetta mescolerebbe allora due vocabolari senza nessun
segnale che è successo.

Due conseguenze, entrambe vincolanti:

1. Gli **estremi effettivi dei bucket** vengono salvati in
   `metric_runs.params` (`community_size_buckets`, la lista degli estremi
   usati), non solo dedotti da `N`. Una riga di `metric_community_sizes` è
   interpretabile solo insieme alla run che l'ha prodotta.
2. **Un cambio di `N` interrompe la comparabilità di quella tabella**, allo
   stesso modo in cui un cambio dei parametri del grafo interrompe la
   comparabilità di due snapshot (§4.6). Le righe vecchie non vanno riscritte né
   rimappate: vanno lette con gli estremi della loro run. Chi costruirà la vista
   di trend del catalogo §7 deve raggruppare per estremi, non per etichetta.

### 6.4 Incroci di dimensioni e soppressione secondaria

**La soglia si applica alla cella più fine effettivamente scritta**, cioè alla
combinazione completa della chiave primaria della riga — non al totale della
dimensione superiore. Una coorte sopra soglia può avere una riga soppressa per
un `layer_scope` in cui `n_effective` è più basso, e la valutazione va fatta lì.

Non basta. Quando in un gruppo di righe che condividono un totale pubblicato
**una sola** cella è soppressa, quella cella si ricava per differenza dal
totale, e la soppressione non ha protetto niente. Regola di **soppressione
secondaria**: se in un gruppo del genere esiste esattamente una cella
soppressa, se ne sopprime anche una seconda, la più piccola tra le restanti,
con `suppression_reason = 'secondary'`.

Dove si applica in v0: alla distribuzione delle dimensioni delle community, che
è l'unico posto in cui un totale è pubblicato accanto alle sue parti
(`metric_communities.n_effective` è la somma dei membri dei bucket). Le altre
tabelle non pubblicano totali da cui una cella sia derivabile; se in futuro se
ne aggiunge uno, la regola vale anche lì e va applicata prima di pubblicarlo.

## 7. Calcolabilità: metrica non calcolabile ≠ metrica pari a zero

Oggi ci sono circa tre giorni di dati e un grafo di 9 nodi. Su un campione così,
betweenness e modularità producono numeri formalmente validi e sostanzialmente
casuali. Un numero nudo scritto in tabella, a valle, è indistinguibile da uno
affidabile.

Ogni riga porta quindi, oltre a `n_effective`:

- **`is_significant`** BOOLEAN nullable — `true`/`false` quando la metrica è
  stata valutata contro i minimi di calcolabilità definiti sotto, **NULL**
  quando non è stata valutata affatto perché la riga è soppressa (§6.2);
- **`details`** JSONB — la ragione, e gli ingredienti del giudizio.

`is_significant = false` non impedisce di scrivere il valore (a differenza di
`is_suppressed`, che lo impedisce): il valore c'è, ma chi legge sa che non è
distinguibile dal rumore. Sono due flag distinti perché sono due cause diverse
— una riguarda l'anonimato, l'altra l'affidabilità — e collassarle
significherebbe non poter più dire quale delle due è scattata.

### 7.1 Robustezza

`is_significant = false` se:

- il grafo ha meno di `min_nodes_structural` nodi (default 30). Sotto poche
  decine di nodi la betweenness è dominata da una manciata di cammini e il top
  X% è 1-2 nodi: la rimozione non è una statistica, è un aneddoto;
- il numero di nodi rimossi è `< 2` (conseguenza tipica del minimo di 1 nodo su
  grafi piccoli);
- il baseline casuale ha deviazione standard nulla su tutte le R ripetizioni —
  significa che il grafo è così piccolo o così regolare che ogni rimozione
  casuale dà lo stesso risultato, e `targeted_z` non è definibile.

### 7.2 Community

Qui una soglia di numerosità non basta, perché **la modularità di un grafo
casuale non è zero**: è positiva e cresce al diminuire della dimensione. Una
modularità di 0.4 su un grafo di 20 nodi può essere esattamente quello che si
otterrebbe partizionando rumore.

Quindi la modularità ha un **baseline come la robustezza**: `R` grafi ottenuti
per rewiring che **preserva la sequenza dei gradi** (`Graph.rewire`), con il
multiset dei pesi riassegnato casualmente agli archi — approssimazione
dichiarata, perché il rewiring non preserva i pesi — e Leiden eseguito su
ciascuno con lo stesso seed. Si salvano `modularity_random_mean`,
`modularity_random_sd` e `modularity_z`.

**Il baseline è la voce di costo dominante del job, e va degradato prima di
diventare un problema.** `R = 100` ripetizioni per quattro layer sono fino a
~400 esecuzioni di Leiden per snapshot, più ~300 rimozioni casuali per la
robustezza (R × 3 valori di X), il tutto su **1 vCPU condiviso con l'heartbeat
del gateway Discord del bot** (`architettura.md`, sezione Hosting). Oggi, su 9
nodi, è gratis. Su qualche migliaio di nodi non lo è più, e il modo in cui ce ne
accorgeremmo è il job che ruba tempo al bot o l'OOM killer.

Regola: sopra `baseline_downgrade_nodes` nodi (default 500) il numero di
ripetizioni scende da `baseline_repetitions` (100) a
`baseline_repetitions_reduced` (20), per entrambi i baseline — rewiring della
modularità e rimozione casuale della robustezza. Il numero effettivamente usato
e il fatto che la degradazione sia scattata vanno in `details`
(`baseline_repetitions_used`, `baseline_degraded`), non solo nei parametri della
run: la deviazione standard di un baseline da 20 ripetizioni è più rumorosa di
quella da 100, quindi `modularity_z` e `targeted_z` di due snapshot con
degradazione diversa non sono confrontabili alla pari, e chi li legge deve poter
vedere da quale delle due situazioni viene la riga che ha in mano.

`is_significant = false` se:

- il grafo ha meno di `min_nodes_structural` nodi;
- `modularity_z < min_modularity_z` (default 2.0): la partizione trovata non è
  distinguibile da quella che si troverebbe su un grafo casuale con gli stessi
  gradi.

La stabilità ha in più le condizioni di §4.6.

### 7.3 Coorti

`is_significant = false` se `is_mature = false` (osservazione <
`min_observation_days`). Le singole colonne hanno inoltre i propri flag
puntuali (`median_reached`, `is_computable` della retention), perché una coorte
matura può comunque avere una mediana non raggiunta.

## 8. Dove muoiono gli output per-nodo

Il confine è preciso e va rispettato nel codice, non verificato a posteriori.

**Restano dentro la funzione che li produce, e non attraversano mai il confine
del modulo che li calcola:**

- il vettore di betweenness dei nodi e l'elenco dei nodi rimossi (§3);
- l'assegnazione nodo → community di Leiden, per lo snapshot corrente **e per
  quello precedente ricostruito** (§4);
- il conteggio dei partner distinti e il tempo all'evento del singolo membro
  (§5);
- l'elenco dei rientri sospetti (§5.6), di cui esce solo il conteggio.

Le funzioni di calcolo restituiscono **solo aggregati**. Nessuna struttura dati
indicizzata per `author_id` viene ritornata verso `main.py`, e nessun `details`
JSONB contiene id, pseudonimi o etichette di nodo.

**Nessuna tabella.** Non esiste, e non va aggiunta, una tabella di centralità
per nodo o di appartenenza alla community. La partizione precedente si
ricostruisce (§4.2) proprio per non doverla persistere: è il motivo per cui il
determinismo è un requisito e non un'aspirazione.

**Nessun log.** I log del job finiscono su stdout del container, quindi in
journald, quindi persistiti. Nessun messaggio di log, a nessun livello, contiene
`author_id`, l'elenco dei nodi rimossi o la composizione delle community. Il
livello `DEBUG` non fa eccezione: un livello di log non è un confine di
sicurezza, perché è una variabile d'ambiente.

**`--dry-run` non è una scorciatoia.** Stampa esattamente gli stessi aggregati
che scriverebbe in tabella, soppressione inclusa: serve a vedere cosa
produrrebbe una run, non a guardare i dati per-nodo senza scriverli.

## 9. Tabelle

### 9.1 Perché tipizzate e non una tabella di metriche con valore JSONB

Considerata e scartata l'alternativa di un'unica tabella
`metric_values (snapshot_id, metric, dimensions JSONB, value JSONB)`.

> **Motivazione corretta dopo l'implementazione del vincolo.** La prima stesura
> di questa sezione dava come ragione "decisiva" il fatto che la regola di §6.2
> fosse esprimibile solo su colonne vere, come
> `CHECK (NOT is_suppressed OR valore IS NULL)`. Quella ragione **è caduta**
> quando il vincolo è diventato un trigger che legge la riga come JSONB (§9.3):
> lo stesso trigger funzionerebbe su una tabella con valore JSONB. Resta valida
> la prima ragione, e l'implementazione ne ha aggiunta una nuova.

1. Le tre metriche hanno **chiavi di riga diverse** (robustezza: snapshot ×
   layer × X; community: snapshot × layer; coorte: snapshot × coorte ×
   layer_scope × k). Una tabella unica avrebbe una chiave finta, e ogni lettura
   dovrebbe estrarre e castare le dimensioni da JSONB per fare qualunque cosa —
   filtrare, ordinare, indicizzare.
2. **Il vincolo di soppressione è enunciabile solo su colonne tipizzate.** Su
   colonne vere si scrive per intero: *tutto tranne chiave, flag e ragione*, e
   il trigger può verificarlo perché sa dal catalogo di Postgres quali sono le
   colonne di chiave. Su un valore JSONB non esiste un "tutto": il contenuto è
   una struttura che il codice sceglie riga per riga, quindi il vincolo
   andrebbe scritto contro una forma attesa — chiave per chiave, metrica per
   metrica — che è di nuovo l'elenco che §6.2 rifiuta, e per giunta un elenco
   che il database non può verificare essere completo.

La differenza tra la ragione vecchia e quella nuova non è formale: la vecchia
diceva "il database può imporlo solo così", ed era falsa; la nuova dice che con
le colonne tipizzate la regola è **esprimibile in una forma che non va
mantenuta**, mentre con un JSONB tornerebbe a essere una lista da aggiornare a
mano.

Il JSONB resta per gli ingredienti che cambieranno insieme al codice — baseline,
diagnostica, verifiche di sensibilità — nella colonna `details`, per la stessa
ragione per cui `graph_snapshots.stats` è JSONB: è la scatola nera
dell'esecuzione, non un dato di dominio. Ed è anche il motivo per cui `details`
è l'unica colonna esentata dal vincolo (§6.2): su di lei una forma attesa non
esiste per costruzione, quindi l'unica regola verificabile è che su una riga
soppressa sia vuota.

### 9.2 `metric_runs` — i parametri, dove già stanno per il grafo

Una riga per esecuzione del layer metriche su uno snapshot. `snapshot_id` è
**chiave primaria**, non solo FK: c'è al più una run di metriche per snapshot, e
ricalcolare riscrive.

```
metric_runs
  snapshot_id   BIGINT PK  REFERENCES graph_snapshots(id) ON DELETE CASCADE
  guild_id      BIGINT NOT NULL      -- denormalizzato: ogni query filtra per community
  as_of         TIMESTAMPTZ NOT NULL -- copiato dallo snapshot, per non doverlo joinare
  params        JSONB NOT NULL       -- N, X, k, seed, R, soglie, estremi dei bucket, funzione obiettivo…
  stats         JSONB NOT NULL DEFAULT '{}'  -- durations_ms per metrica, snapshot riletti, degradazioni
  code_version  TEXT
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
```

`stats` è la scatola nera dell'esecuzione, esattamente come
`graph_snapshots.stats`: durate per metrica (§11.1), numero di snapshot riletti
per le coorti, degradazioni dei baseline scattate. JSONB e non colonne dedicate
per la stessa ragione della migration `0005` — l'insieme dei contatori cambierà
insieme al codice, e aggiungerne uno non deve richiedere una migrazione.

I parametri delle metriche stanno qui e **non** in `graph_snapshots.params`:
quello descrive come è stato costruito il grafo, e riscriverlo per aggiungerci i
parametri di un layer successivo significherebbe modificare uno snapshot già
calcolato. Come per il grafo, due run con parametri diversi non sono
confrontabili e devono poterlo dichiarare da sole.

Tutte le tabelle di metrica hanno `snapshot_id` come primo elemento della chiave
primaria, con FK verso `metric_runs(snapshot_id)` e `ON DELETE CASCADE`: ogni
riga è legata allo snapshot che l'ha prodotta e non può sopravvivergli.

### 9.3 `metric_robustness`

```
metric_robustness
  snapshot_id               BIGINT NOT NULL  → metric_runs
  layer                     TEXT   NOT NULL
  removal_fraction          DOUBLE PRECISION NOT NULL   -- 0.05 | 0.10 | 0.20
  PRIMARY KEY (snapshot_id, layer, removal_fraction)

  n_effective               INTEGER          -- nodi del grafo; NULL se soppressa
  nodes_removed             INTEGER
  giant_before              DOUBLE PRECISION
  giant_after_targeted      DOUBLE PRECISION
  components_after_targeted INTEGER
  giant_after_random_mean   DOUBLE PRECISION
  giant_after_random_sd     DOUBLE PRECISION
  components_after_random_mean DOUBLE PRECISION
  targeted_excess           DOUBLE PRECISION
  targeted_z                DOUBLE PRECISION -- NULL se sd = 0
  is_suppressed             BOOLEAN NOT NULL DEFAULT FALSE
  suppression_reason        TEXT
  is_significant            BOOLEAN          -- NULL = non valutata (riga soppressa)
  details                   JSONB   NOT NULL DEFAULT '{}'
```

**Il vincolo di soppressione: se `is_suppressed`, allora tutte le colonne fuori
dalla chiave primaria sono NULL tranne `is_suppressed`, `suppression_reason` e
`details`, che vale `'{}'::jsonb`.** Vale identico su tutte e cinque le tabelle
di metrica.

`nodes_removed` è il caso da non dimenticare: è `ceil(removal_fraction · n)` e
`removal_fraction` sta in chiave, quindi da solo restituisce la dimensione del
grafo a meno di un'unità. Lo stesso vale per `giant_before`, che è una frazione
con `n` al denominatore, e in `metric_cohorts` per `event_count +
censored_count`, che è la dimensione della coorte.

**Non è un `CHECK` dichiarativo, ed è una scelta.** Un `CHECK` di tabella può
solo enumerare le colonne da annullare (`num_nonnulls(a, b, c, …) = 0`), cioè
esattamente l'elenco che §6.2 rifiuta: un elenco che si dimentica di aggiornare
il giorno in cui si aggiunge una colonna, e il cui fallimento è silenzioso —
la colonna nuova continua a essere pubblicata su righe soppresse senza che
niente protesti. Il vincolo è quindi imposto da un **trigger `BEFORE INSERT OR
UPDATE`**, uno solo, condiviso da tutte e cinque le tabelle: legge la riga come
JSONB, ricava le colonne di chiave primaria dal catalogo di Postgres, e verifica
che tutto il resto sia NULL. Aggiungere una colonna la mette sotto vincolo senza
toccare niente; aggiungerne una alla chiave primaria pure.

Resta comunque nello schema, in forma dichiarativa, la parte stabile che un
`CHECK` esprime bene: `suppression_reason` valorizzato solo su righe soppresse.

Il trigger è **schema, non codice del job**: vive nella migration, è applicato
dal database a ogni scrittura da qualunque client, e vale anche per una `INSERT`
scritta a mano in psql. È questa la garanzia che §9.1 usa per giustificare le
tabelle tipizzate, e per questo va verificata da un test contro un database vero
e non solo affermata qui.

### 9.4 `metric_communities` e `metric_community_sizes`

```
metric_communities
  snapshot_id            BIGINT NOT NULL  → metric_runs
  layer                  TEXT   NOT NULL
  PRIMARY KEY (snapshot_id, layer)

  n_effective            INTEGER          -- nodi del grafo
  community_count        INTEGER
  modularity             DOUBLE PRECISION
  modularity_random_mean DOUBLE PRECISION
  modularity_random_sd   DOUBLE PRECISION
  modularity_z           DOUBLE PRECISION

  previous_snapshot_id   BIGINT           -- NULL se non confrontabile
  node_overlap           DOUBLE PRECISION
  stability_jaccard      DOUBLE PRECISION
  communities_born       INTEGER
  communities_dissolved  INTEGER
  communities_merged     INTEGER
  communities_split      INTEGER

  is_suppressed          BOOLEAN NOT NULL DEFAULT FALSE
  suppression_reason     TEXT
  is_significant         BOOLEAN          -- NULL = non valutata (riga soppressa)
  details                JSONB   NOT NULL DEFAULT '{}'
```

```
metric_community_sizes
  snapshot_id        BIGINT NOT NULL  → metric_runs
  layer              TEXT   NOT NULL
  bucket             TEXT   NOT NULL   -- 'small' | '5-9' | '10-19' | '20-49' | '50-99' | '100+'
  PRIMARY KEY (snapshot_id, layer, bucket)

  community_count    INTEGER
  member_count       INTEGER           -- l'n_effective di questa riga
  is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE
  suppression_reason TEXT              -- 'below_threshold' | 'secondary'
```

Tabella figlia e non un JSONB dentro `metric_communities` per la ragione di
§9.1: la distribuzione delle dimensioni è **proprio la parte con i numeri
piccoli**, quella su cui la soppressione (primaria e secondaria) deve essere
imposta dal database.

### 9.5 `metric_cohorts` e `metric_cohort_retention`

```
metric_cohorts
  snapshot_id        BIGINT NOT NULL  → metric_runs
  cohort_start       DATE   NOT NULL   -- lunedì della settimana ISO di join
  layer_scope        TEXT   NOT NULL   -- 'any' | 'voice' | …
  k                  INTEGER NOT NULL
  PRIMARY KEY (snapshot_id, cohort_start, layer_scope, k)

  n_effective        INTEGER           -- membri della coorte dopo l'esclusione dei rientri
  observation_days   INTEGER
  is_mature          BOOLEAN           -- NULL = non valutata (riga soppressa)
  event_count        INTEGER
  censored_count     INTEGER
  censored_by_leave  INTEGER
  median_days_to_k   DOUBLE PRECISION  -- NULL se non raggiunta
  median_reached     BOOLEAN
  p25_days_to_k      DOUBLE PRECISION
  p75_days_to_k      DOUBLE PRECISION
  reached_by_14d     DOUBLE PRECISION
  reached_by_28d     DOUBLE PRECISION
  excluded_rejoins   INTEGER
  is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE
  suppression_reason TEXT
  is_significant     BOOLEAN           -- NULL = non valutata (riga soppressa)
  details            JSONB  NOT NULL DEFAULT '{}'
```

```
metric_cohort_retention
  snapshot_id        BIGINT NOT NULL  → metric_runs
  cohort_start       DATE   NOT NULL
  horizon_days       INTEGER NOT NULL  -- 7 | 14 | 28
  PRIMARY KEY (snapshot_id, cohort_start, horizon_days)

  n_effective        INTEGER           -- stessa popolazione di metric_cohorts (§5.5)
  excluded_rejoins   INTEGER           -- ripetuto qui apposta: rende verificabile che il denominatore coincida
  retained_fraction  DOUBLE PRECISION  -- NULL se non calcolabile
  is_computable      BOOLEAN           -- NULL = non valutata (riga soppressa)
  is_suppressed      BOOLEAN NOT NULL DEFAULT FALSE
  suppression_reason TEXT
```

Tabella separata perché la retention **non dipende** da `layer_scope` né da `k`:
è solo membership. Metterla nella stessa riga la duplicherebbe per ogni
combinazione, ed è il modo in cui due numeri identici cominciano a divergere.

La popolazione è però **la stessa** di `metric_cohorts` (§5.5), rientri sospetti
esclusi. `n_effective` ed `excluded_rejoins` sono duplicati qui apposta, contro
la regola generale di non ripetere un dato: sono l'unico modo per accorgersi che
i due denominatori hanno smesso di coincidere, e il costo di una divergenza
silenziosa tra queste due tabelle è un'intera conclusione sbagliata
sull'incrocio integrazione/retention, che è la ragione per cui la metrica
esiste.

### 9.6 Riesecuzione

Stesso pattern di `write_snapshot` per `graph_edges`, e per la stessa ragione:
dentro una transazione, `DELETE FROM <tabella> WHERE snapshot_id = $1` seguito
dagli `INSERT`, mai un UPSERT riga per riga. Un ricalcolo con `k` o `X` diversi
può far **sparire** righe, e un UPSERT lascerebbe in tabella il fantasma di
quelle vecchie — con l'aggravante che sarebbero indistinguibili dalle nuove.

`metric_runs` è invece un `INSERT … ON CONFLICT (snapshot_id) DO UPDATE`.

## 10. Parametri

Tutti in `job/config.py`, in una dataclass separata da `GraphParams` (sono
parametri di un layer diverso, e devono poter cambiare senza rendere
incomparabili gli snapshot del grafo), e tutti salvati in `metric_runs.params`.

| Parametro | Default | Stato |
|---|---|---|
| Soglia di cardinalità `N` | 5 | Convenzione statistiche ufficiali (k-anonymity); da alzare verso 10 quando la scala lo consente |
| Soglia di pubblicazione strutturale `min_nodes_publish` | = `N` | Ingegneria nostra (§6.1) — separata da `N` perché protegge da un rischio diverso |
| Peso minimo dell'arco `min_edge_weight` | 0.0 | Ingegneria nostra (§2.3) — a 0.0 è la regola `weight > 0`; da alzare quando i dati diranno dove sta il residuo numerico |
| Griglia `X` di rimozione | 0.05, 0.10, 0.20 | Ingegneria nostra — il catalogo dichiara X da validare empiricamente |
| Centralità di attacco | betweenness, distanza `1/weight` | Letteratura (Freeman 1977) per la misura; ingegneria nostra la scelta come criterio |
| Ripetizioni del baseline `R` | 100 | Ingegneria nostra; compromesso con la CPU della droplet |
| Soglia di degradazione `baseline_downgrade_nodes` | 500 nodi | Ingegneria nostra (§7.2) — puro budget di CPU |
| Ripetizioni degradate `baseline_repetitions_reduced` | 20 | Ingegneria nostra |
| Seed | 20260903 | Ingegneria nostra — il valore è irrilevante, la sua stabilità no |
| Funzione obiettivo Leiden | modularity | Letteratura (Traag et al. 2019); CPM è il candidato al passaggio |
| Iterazioni Leiden | −1 (a convergenza) | Ingegneria nostra, per il determinismo |
| Soglia di match Jaccard | 0.30 | **Provvisorio** — nessuna base, da tarare sulle prime partizioni reali |
| Quota di fusione `merge_min_share` | 0.50 | Provvisorio |
| Sovrapposizione minima di nodi | 0.50 | Provvisorio |
| `k` (connessioni per "integrato") | 5 | Da Millington via catalogo §5; **da tarare sui dati reali** |
| `partner_min_interactions` | 1 | **Provvisorio** (§5.2) — a 1 cinque emoji valgono cinque serate in vocale; da ritarare insieme a `k` |
| `layer_scope` calcolati | `any`, `voice` | Ingegneria nostra (§5.2) |
| Orizzonti di retention | 7, 14, 28 giorni | Ingegneria nostra |
| Osservazione minima coorte | 14 giorni | Provvisorio |
| Età massima coorte ricalcolata | 180 giorni | Ingegneria nostra, solo costo |
| Esclusione rientri sospetti | `true` | Ingegneria nostra (§5.6) |
| Nodi minimi per significatività | 30 | **Provvisorio** — il numero è un'euristica, il principio no |
| `modularity_z` minimo | 2.0 | Convenzione statistica; provvisorio |
| Estremi dei bucket di dimensione | `< N`, `[N,10)`, `[10,20)`, `[20,50)`, `[50,100)`, `[100,∞)` | Ingegneria nostra — **salvati in `params`**, perché dipendono da `N` (§6.3) |
| Proiezione layer direzionali | `undirected_sum` | Ingegneria nostra (§2.2) |

`k = 5` e `N` sono i due valori che il catalogo dichiara esplicitamente da
tarare sui dati e non da fissare a tavolino: sono qui come punto di partenza per
poter calcolare qualcosa, non come risposta.

## 11. Esecuzione

Nuovo sottocomando del job, che **legge uno snapshot già scritto** invece di
ricalcolare il grafo:

```
python -m job.main metrics [--snapshot-id N | --guild-id N] [--dry-run]
```

Senza argomenti lavora su **tutte** le guild che hanno almeno uno snapshot,
prendendo l'ultimo di ciascuna — lo stesso contratto di `snapshot`, perché due
sottocomandi dello stesso job non possono voler dire cose diverse con "senza
argomenti". `--guild-id` restringe a una community, `--snapshot-id` a uno
snapshot preciso, e i due sono mutuamente esclusivi. `--dry-run` calcola e
stampa gli aggregati — soppressione inclusa (§8) — senza scrivere nulla.

La separazione dal sottocomando `snapshot` è voluta: permette di ricalcolare le
metriche con parametri nuovi senza rifare la ricostruzione delle sessioni, che
è la parte costosa e delicata, e rende la riesecuzione del layer metriche
un'operazione senza conseguenze sul grafo.

Il calcolo legge anche gli snapshot **precedenti** della stessa guild: lo
snapshot immediatamente precedente per la stabilità (§4.4), e la serie completa
dalla coorte più vecchia riportata in poi per il tempo a `k` connessioni (§5.3).
In entrambi i casi solo quelli con parametri del grafo identici.

Dipendenza nuova: **`leidenalg`** in `requirements.txt` (oggi c'è solo
`python-igraph`). Nessun'altra: Kaplan–Meier è una produttoria di poche righe e
non giustifica `lifelines`, e il campionamento casuale usa `random.Random` con
il seed della run.

### 11.1 Budget di esecuzione, e come vederlo crescere

Il job gira su 1 vCPU e 1 GB condivisi con bot e Postgres. Due voci di costo
sono note fin da ora e non vanno scoperte da un incidente.

**CPU: i baseline.** Fino a ~400 esecuzioni di Leiden e ~300 rimozioni casuali
per snapshot, degradabili sopra una soglia di dimensione del grafo (§7.2). Resta
comunque valido l'accorgimento già previsto in `architettura.md`: job in orario
di bassa attività e con priorità CPU ridotta (`nice`).

**Memoria: la rilettura degli snapshot per le coorti.** §5.3 richiede di
scorrere la serie di snapshot fino a `cohort_max_age_days` (180 giorni, cioè
fino a ~26 snapshot settimanali) per ricostruire quando ogni membro ha raggiunto
`k` partner. È il picco di memoria del job, e caricare tutti gli archi di tutti
quegli snapshot insieme è precisamente il modo di farlo esplodere. Vincolo di
implementazione: si legge **uno snapshot alla volta**, filtrando gli archi a
quelli **incidenti ai membri delle coorti ancora aperte**, e si accumulano
insiemi di partner — non archi. Nessun momento in cui il job tiene in memoria
più di uno snapshot di archi.

**Misurare invece di stimare.** `metric_runs.stats` porta le **durate di
esecuzione per metrica** (`durations_ms`: robustezza, community, coorti, e il
totale) insieme al numero di snapshot riletti e alle degradazioni scattate.
Stessa ragione per cui `graph_snapshots.stats` esiste (migration `0005`): un
numero che vive solo in una riga di log non permette nessun confronto tra
un'esecuzione e la successiva, e un tempo di esecuzione ha senso solo come
serie. È l'unico strumento per **vedere il problema arrivare** — la durata che
cresce di snapshot in snapshot — invece di scoprirlo dall'OOM killer o da un
bot che perde l'heartbeat.

## 12. Fuori da v0

Deliberatamente non in questa specifica:

- Le altre metriche del catalogo: concentrazione strutturale/Gini (§2), densità
  cross vs within community (§4), reciprocità aggregata (§6), serie temporali e
  soglie di attenzione (§7). Quest'ultima in particolare **presuppone** le
  tabelle definite qui, e ha senso solo dopo qualche snapshot.
- **Qualunque grafo unione o indice sintetico tra layer** (§2.1).
- **CPM** come funzione obiettivo, e l'analisi multi-risoluzione (§4.3).
- **Modularità direzionale** e componenti fortemente connesse sui layer
  direzionali (§2.2).
- **Matching bipartito ottimo** tra partizioni al posto del greedy (§4.4).
- **Rischi competitivi** nel modello di sopravvivenza: distinguere "non ancora
  integrato" da "uscito prima di integrarsi" (§5.4).
- **Storico dei rientri multipli** in `members`, che è la sola correzione vera
  ai bias di §5.6.
- **Segmentazione per canale, ruolo o tipo di evento**: è il punto aperto del
  catalogo su cui l'incrocio di più dimensioni pubbliche ricrea una cella a
  cardinalità 1. Va progettata con la regola di soppressione degli incroci già
  in mano, non aggiunta dopo.
- Confronto tra community diverse (multi-guild): le soglie e i parametri
  andrebbero armonizzati prima, e c'è una sola community.

## 13. Decisioni confermate in revisione (03/09/2026)

Tre decisioni di questa spec toccano un vincolo ereditato o un valore che il
catalogo dichiara aperto. Sono state portate in revisione esplicitamente e
**confermate tutte e tre il 3 settembre 2026**, la terza con una correzione.
Restano elencate qui perché sono i punti da riaprire per primi se una
conclusione futura dovesse risultare sospetta.

1. **Confermato — `layer_scope = 'any'`** per il conteggio delle connessioni
   distinte (§5.2): è un'unione di insiemi di persone, non una somma di pesi,
   quindi non costruisce una sociomatrice fusa — ma è comunque l'unico punto
   della spec in cui i layer si toccano. La revisione ha aggiunto
   `partner_min_interactions` (§5.2): l'ammissione di un partner con una sola
   interazione rende "integrato" chi ha lasciato cinque emoji, e questa metrica
   fallisce sembrando funzionare.
2. **Confermata — proiezione non diretta** dei layer
   `reply`/`mention`/`reaction` per le metriche strutturali (§2.2): perde la
   direzionalità, che tornerà con la metrica di reciprocità.
3. **Confermata con correzione — `N = 5`** (§6.1) e l'estensione della soglia
   alle metriche strutturali, che il catalogo dichiarava non soggette a soglia.
   La correzione riguarda la **motivazione**, che era sbagliata: la stesura
   precedente giustificava la soglia strutturale con l'anonimato, ma il grafo
   reale di oggi ha 9 nodi, supera `N = 5` ed è pubblicato — se la ragione fosse
   l'anonimato, 9 nodi non basterebbero. Le righe strutturali si pubblicano
   perché sono rapporti sull'intero grafo che non identificano nessuno, e il
   loro rischio reale (un numero privo di significato) è gestito da
   `is_significant`, non dalla soppressione. La soglia strutturale esiste
   comunque, come `min_nodes_publish` separato da `N`, per il caso residuo di
   de-anonimizzazione per contesto.
