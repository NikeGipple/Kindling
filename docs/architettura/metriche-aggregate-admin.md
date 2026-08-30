# Catalogo metriche aggregate per l'admin — Kindling

*Bozza di lavoro — 30 agosto 2026. Nasce come sviluppo diretto del principio
"nessun profilo individuale esposto agli amministratori" già fissato in
`stack-tecnologico-mvp.md`: qui si elencano gli indici e i
valori aggregati concreti che quel principio permette comunque di mostrare,
in modo che il sistema possa "sapere" a livello di singolo nodo (centralità,
ruolo di bridge, community di appartenenza, velocità di integrazione del
singolo) senza che nessuna di queste informazioni raggiunga mai l'admin come
tale. Da rivedere e affinare quando si comincerà a scrivere il job di
calcolo e le tabelle di snapshot.

## Principio di riferimento

Ogni metrica di questo catalogo deve poter essere descritta così: *"per
calcolarla il sistema usa informazioni per-nodo, ma restituisce solo una
proprietà dell'insieme"*. Se una metrica non supera questo test — se per
essere utile deve, anche implicitamente, restringere l'attenzione a un
membro o a un gruppo di 2-3 persone identificabile dal contesto — non va
esposta così com'è, va riformulata o accorpata a una cardinalità più larga.

## Principio operativo aggiuntivo — soglia minima di cardinalità

L'aggregazione da sola non garantisce l'anonimato quando i numeri in gioco
sono piccoli: "i membri isolati questa settimana sono 2" è tecnicamente un
aggregato, ma in una community che l'admin conosce di persona è spesso
de-anonimizzabile lo stesso. Regola da applicare a **tutte** le metriche di
questo catalogo, non solo a quelle di conteggio:

- Fissare una cardinalità minima **N** (valore di partenza da validare,
  indicativamente 5-10) sotto la quale una cella/sottogruppo non viene
  mostrata singolarmente ma accorpata alla categoria più vicina o sostituita
  da un simbolo di soppressione (pattern standard nelle statistiche
  ufficiali — k-anonymity).
- Vale anche per gli incroci tra dimensioni (es. "nuovi membri della coorte
  X che hanno raggiunto k connessioni" — se la coorte X ha 3 persone, la
  metrica va soppressa anche se non nomina nessuno).
- La soglia va applicata nel layer di calcolo/snapshot, non nella
  dashboard: le tabelle Postgres pensate per essere lette dall'API devono
  già contenere solo aggregati sopra soglia, così nessun layer di
  presentazione futuro può bucare la regola per errore.

## Catalogo

### 1. Robustezza strutturale (indice di resilienza/frammentazione)

- **Definizione**: simulazione di rimozione del top X% dei nodi per
  centralità (calcolo interno, mai esposto) e misura di quanto si
  frammenta il grafo residuo — numero di componenti connesse risultanti,
  variazione della dimensione della componente gigante.
- **Perché conta**: risponde direttamente alla domanda guida — la community
  dipende da un numero ristretto di connettori (fragile) o la connettività
  regge anche togliendo i nodi più centrali (distribuita)?
- **Dato sorgente**: grafo pesato da `raw_events` (snapshot periodico).
- **Cadenza**: per snapshot (settimanale, come da piano SNA già definito).
- **Cardinalità**: non applicabile a livello di conteggio persone — è già
  un rapporto/percentuale sull'intero grafo.
- **Note**: prototipo più immediato del principio "calcolo per-nodo interno
  → output puramente di sistema".

### 2. Concentrazione strutturale (distribuzione dei gradi)

- **Definizione**: coefficiente di Gini sulla distribuzione dei gradi dei
  nodi, oppure "percentuale della connettività totale detenuta dal top X%
  dei membri per numero di connessioni".
- **Perché conta**: misura se la rete si sta distribuendo nel tempo o si sta
  concentrando su pochi hub — trend, non snapshot singolo, è la lettura più
  utile.
- **Dato sorgente**: grado dei nodi nel grafo di snapshot.
- **Cadenza**: per snapshot, letto come serie temporale.
- **Cardinalità**: statistica sull'intera distribuzione, non richiede
  soglia minima.

### 3. Struttura delle community (Leiden)

- **Definizione**: (a) numero di community rilevate; (b) distribuzione
  delle loro dimensioni; (c) modularità della partizione; (d) stabilità
  della struttura tra snapshot consecutivi (es. indice di Jaccard tra le
  partizioni di due settimane successive — quanto la composizione delle
  community resta la stessa vs si ricompone).
- **Perché conta**: una community che si ricompone in modo riconoscibile nel
  tempo è un segnale diverso da una che si disgrega e si riforma di
  continuo — è una lettura diretta di "mantenimento delle relazioni" a
  livello di sistema.
- **Dato sorgente**: partizione Leiden (`leidenalg`) per snapshot.
- **Cadenza**: per snapshot.
- **Cardinalità**: applicare soglia minima N alle community troppo piccole
  prima di riportarne la dimensione singolarmente — accorparle in una
  categoria "community piccole (<N membri)".

### 4. Densità cross-community vs within-community

- **Definizione**: percentuale di interazioni (archi pesati) che
  attraversano i confini tra community rilevate rispetto a quelle interne
  a una stessa community.
- **Perché conta**: distingue una community "a silos" (poca densità cross)
  da una integrata — utile per capire se le condizioni create favoriscono
  la formazione di ponti tra sottogruppi, senza mai dover indicare chi fa
  da ponte.
- **Dato sorgente**: grafo + partizione Leiden dello stesso snapshot.
- **Cadenza**: per snapshot.
- **Cardinalità**: rapporto aggregato sull'intero grafo, non richiede
  soglia salvo quando si scende a livello di singola coppia di community
  piccole (vedi punto 3).

### 5. Onboarding e retention per coorte

- **Definizione**: tempo mediano (o curva di sopravvivenza) perché un nuovo
  membro raggiunga *k* connessioni distinte (k da validare, punto di
  partenza discusso: 5), calcolato per coorte di ingresso (settimana di
  join, o "iscritti durante un evento" vs organici); incrociato con
  retention/abbandono della stessa coorte.
- **Perché conta**: è la metrica che permette di verificare se
  l'integrazione rapida correla davvero con la permanenza — collegata al
  gap già chiuso sulla tabella `members` (`joined_at`/`left_at`) descritto
  in `note-claude-md.md`.
- **Dato sorgente**: tabella `members` (join/leave) + grafo delle
  interazioni per calcolare il raggiungimento di *k* connessioni.
- **Cadenza**: per coorte, aggiornata a ogni snapshot.
- **Cardinalità**: sopprimere/accorpare coorti sotto soglia N (es. una
  settimana con pochi nuovi ingressi).

### 6. Reciprocità aggregata

- **Definizione**: percentuale di interazioni reciproche (A→B e B→A in una
  finestra temporale) vs unidirezionali, aggregata per canale, periodo o
  tipo di evento.
- **Perché conta**: un calo di reciprocità community-wide è un segnale di
  salute anche senza sapere chi non risponde a chi.
- **Dato sorgente**: `raw_events` (messaggi, reply, reazioni).
- **Cadenza**: per snapshot, per canale/periodo.
- **Cardinalità**: applicare soglia minima ai canali/periodi con troppo
  poche interazioni per essere significativi.

### 7. Serie temporali e soglie di attenzione (meta-livello)

- **Definizione**: ciascuna delle metriche sopra letta come trend nel tempo,
  con soglie che segnalano una direzione da monitorare (es. "indice di
  concentrazione strutturale in salita da 3 snapshot consecutivi",
  "velocità di integrazione delle nuove coorti in calo", "stabilità delle
  community in diminuzione").
- **Perché conta**: è quello che trasforma un numero in una leva d'azione
  per l'admin — cambiare il formato di un evento, aprire un canale,
  rivedere l'onboarding — restando su decisioni di sistema, mai su persone.
- **Dato sorgente**: le tabelle di snapshot delle metriche 1-6.
- **Cadenza**: calcolata a ogni nuovo snapshot confrontandolo con lo
  storico.
- **Cardinalità**: eredita le soglie delle metriche sottostanti.

## Priorità suggerita per l'MVP

Le tre metriche più direttamente collegate alla domanda guida e ai dati già
pianificati (grafo pesato + tabella `members`): **onboarding/retention per
coorte** (usa un gap appena chiuso, dato già disponibile), **robustezza
strutturale** (esempio più chiaro del principio di aggregazione, buon primo
banco di prova per il job Leiden/centralità), **stabilità delle community**
(richiede solo Leiden su snapshot successivi, già nel piano SNA). Le altre
restano nel catalogo ma possono aspettare la prima iterazione di feedback
con il community manager.

## Aperti da validare

- Valore di *k* (connessioni per considerare un nuovo membro "integrato") e
  valore di N (soglia minima di cardinalità) — entrambi da tarare sui dati
  reali, non da fissare a tavolino.
- Percentuale X di nodi da rimuovere nella simulazione di robustezza (punto
  1) — da validare empiricamente sul grafo reale, un valore troppo piccolo
  o troppo grande rende la simulazione poco informativa.
- Se e come mostrare queste metriche segmentate per sotto-gruppi "pubblici"
  già noti all'admin (canale, ruolo, tipo di evento) senza che
  l'incrocio di più dimensioni pubbliche ricrei di fatto una cella a
  cardinalità 1.
