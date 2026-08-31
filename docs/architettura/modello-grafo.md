# Modello del grafo — da `raw_events` agli archi pesati

*Specifica di costruzione del grafo sociale. Decisa il 31 agosto 2026. È il
documento di riferimento per il job di calcolo: i parametri qui elencati sono
provvisori e da ritarare sui dati reali, la struttura no.*

## 1. Vincoli ereditati

Questi non sono in discussione in questa specifica — vengono da decisioni già prese:

- **Multirelazionale**: relazioni di tipo diverso si calcolano **separatamente** e non
  si sommano in un'unica sociomatrice senza una ragione sostanziale esplicita
  (Wasserman & Faust p. 219, vedi `fondamenta-teoriche.md` §11.4). Il grafo di
  Kindling è quindi **multiplex a layer**, non un grafo unico pesato.
- **Co-presenza = sovrapposizione reale**, mai prossimità temporale di ingresso
  (`fondamenta-teoriche.md` §10.4: due persone entrate nello stesso canale a 15
  minuti di distanza possono non essersi mai incrociate).
- **La co-appartenenza cresce meccanicamente con la dimensione del gruppo**
  (§10.3): il peso va normalizzato per la dimensione della sessione, altrimenti
  i canali affollati producono archi forti per puro effetto numerico.
- **`raw_events` è la fonte di verità**: gli archi sono un derivato sempre
  ricalcolabile, mai un dato scritto una volta sola.
- **Nessun contenuto testuale** dei messaggi è disponibile per costruzione.
- **Le tabelle di grafo sono interne**: `graph_edges` e `voice_sessions` non
  vengono mai esposte da un endpoint API. L'API legge solo tabelle di aggregati
  sopra la soglia di cardinalità N (vedi `metriche-aggregate-admin.md`).

## 2. I quattro layer

| Layer | Codice | Direzione | Sorgente eventi | Unità di peso |
|---|---|---|---|---|
| Co-presenza vocale | `voice` | non diretto | `voice_join` / `voice_leave` | minuti di sovrapposizione |
| Reply | `reply` | diretto A→B | `message_reply` | n. reply |
| Menzioni | `mention` | diretto A→B | `payload.mention_ids` | n. menzioni |
| Reazioni | `reaction` | diretto A→B | `reaction_add` | n. reazioni |

`thread_create` ed `event_rsvp_*` **non** generano archi in v0: il primo è un atto
individuale, il secondo è un'affiliazione a un evento (semmai un futuro layer
two-mode, non ora).

Ogni layer vive in righe separate della stessa tabella, distinte da `layer`.
Nessun peso combinato tra layer viene calcolato o salvato.

## 3. Layer `voice` — ricostruzione delle sessioni

### 3.1 Intervalli di presenza

Da `voice_join` / `voice_leave` si ricostruisce, per ogni membro e canale, una
serie di intervalli `[inizio, fine)`. Un `voice_move` (spostamento tra canali)
è, a tutti gli effetti, **un leave dal canale precedente e un join nel nuovo**:
l'ingestion deve produrre entrambi gli eventi.

### 3.2 Sessione

Una **sessione** è, per un dato canale, un gruppo massimale di intervalli di
presenza che si sovrappongono, con episodi separati da meno della **finestra di
sessione (30 minuti)** considerati la stessa sessione.

Classificazione (`fondamenta-teoriche.md` §7.1):

- **Brace**: 2 partecipanti distinti.
- **Falò**: ≥3 partecipanti distinti.

Entrambe generano archi: la brace è un'unità di bonding valida di per sé.

### 3.3 Archi

Per ogni **coppia** di partecipanti a una sessione si calcola la
**sovrapposizione effettiva** dei rispettivi intervalli, in minuti. Una coppia
genera un contributo solo se la sovrapposizione **in quella sessione** è
≥ **5 minuti** (soglia applicata per sessione, non sulla somma del periodo).

Contributo della sessione *s* alla coppia (a,b):

```
contributo(a,b,s) = minuti_sovrapposizione(a,b,s) × 1/(n_s − 1)
```

dove `n_s` è il numero di partecipanti distinti della sessione. Il fattore
`1/(n_s − 1)` è la correzione per dimensione richiesta da §10.3: stare 60 minuti
in due pesa più che stare 60 minuti in venti.

Vengono salvati **entrambi** i valori — minuti grezzi non normalizzati e peso
normalizzato — più il numero di sessioni condivise, così da poter confrontare
intensità e frequenza senza ricalcolare (e da poter riapplicare in futuro una
normalizzazione diversa, es. l'odds ratio di W&F eq. 8.5).

## 4. Riconciliazione delle sessioni

Le sessioni vocali non rispettano i confini degli snapshot e il log ha buchi
reali. Regole:

1. **Sessioni a cavallo di uno snapshot**: la ricostruzione legge gli eventi con
   un margine oltre i bordi della finestra, così una sessione che attraversa il
   confine viene ricostruita **intera**. I minuti vengono poi attribuiti allo
   snapshot in cui cade la **fine** della sessione. Mai spezzare una sessione a
   metà per far quadrare la finestra.
2. **Sessioni ancora aperte al momento del calcolo** (join senza leave, persone
   tuttora in canale): vengono **escluse** dallo snapshot corrente e recuperate
   al successivo, quando il `voice_leave` sarà arrivato. Nessun dato perso,
   nessun doppio conteggio: il ricalcolo da `raw_events` è idempotente.
3. **Sessioni orfane** (join senza leave che non può più arrivare — bot riavviato,
   crash, evento perso): l'intervallo si chiude al primo evento successivo dello
   stesso membro, o all'inizio del downtime del bot se noto. L'arco risultante
   viene marcato come ricostruito (`is_reconciled`), per poterlo escludere nelle
   verifiche di sensibilità. Un intervallo che risulterebbe più lungo di un tetto
   massimo (default **12 ore**) è implausibile: viene scartato, non troncato.
4. **Rilevare il downtime è un prerequisito**: senza sapere quando il bot era
   spento non si distingue "è ancora in canale" da "abbiamo perso il leave".
   L'ingestion deve quindi, all'avvio, (a) scrivere un evento marcatore di
   riavvio e (b) fotografare gli stati vocali correnti, emettendo join sintetici
   per chi è già in canale e chiudendo le sessioni rimaste aperte nel DB per chi
   non c'è più. È l'equivalente vocale di `!backfill_members`.

## 5. Decadimento del legame

Un legame non ha un peso assoluto: ha un peso **rispetto a un istante**. Il peso
di un arco allo snapshot è la somma dei contributi delle singole interazioni,
ciascuna pesata per la propria età.

```
f(Δ) = (2^(−Δ/H) − 2^(−C/H)) / (1 − 2^(−C/H))     per 0 ≤ Δ < C
f(Δ) = 0                                            per Δ ≥ C
```

- `Δ` = età dell'interazione rispetto all'`as_of` dello snapshot
- `H` = **emivita, default 7 giorni** — dopo H il contributo è circa dimezzato
- `C` = **cutoff, 30 giorni** — la finestra di relazione sostenuta già fissata in
  `fondamenta-teoriche.md` §7.2 (Millington, "minimo una discussione al mese")

La normalizzazione fa sì che `f(0) = 1` e `f(C) = 0` **esattamente**: la curva
scende con continuità fino a zero al cutoff, senza il gradino artificiale che
avrebbe un'esponenziale semplice troncata.

Per il layer `voice` l'età si misura dalla **fine** della sessione; per i layer
direzionali dal timestamp dell'interazione.

Conseguenze da rispettare:

- Ogni snapshot registra il proprio **`as_of`**: senza quello i pesi non sono
  interpretabili né confrontabili.
- Viene salvato anche il **peso non decaduto**, così l'effetto del decadimento è
  isolabile e `H` è ritarabile senza rifare la ricostruzione delle sessioni.
- `H = 7 giorni` è ingegneria nostra, non letteratura: è il primo parametro da
  rimettere in discussione quando ci saranno mesi di dati.

## 6. Layer direzionali — risoluzione del target

Reply e reazioni identificano il messaggio bersaglio, non il suo autore: serve
risolvere `message_id → author_id`.

- La risoluzione vive in una tabella derivata **`message_authors`**
  `(guild_id, message_id, author_id)`, ricostruibile da `raw_events` e
  arricchibile via API Discord. `raw_events` resta immutabile: nessuna colonna
  denormalizzata di comodo.
- **Reply cross-canale**: una reply può puntare a un messaggio di un altro
  canale. Serve `referenced_channel_id` su `raw_events` (gap già documentato in
  `CLAUDE.md`), valorizzato da `message.reference.channel_id`. `NULL` si tratta
  come "stesso canale della reply".
- **Target non risolvibile** (messaggio precedente all'avvio del bot, o
  cancellato): l'interazione viene contata nelle statistiche di copertura ma
  **non genera un arco**. Un arco verso un autore ignoto non esiste.
- Il job **non chiama mai l'API Discord**: legge solo Postgres. Il recupero degli
  autori mancanti è un comando di backfill lato bot, separato e opzionale.
- Le menzioni non hanno questo problema: `payload.mention_ids` contiene già gli
  id dei destinatari, sono sempre risolvibili.
- Self-loop esclusi ovunque (rispondersi o reagire a sé stessi non è una
  relazione). I bot sono già esclusi in ingestion.

## 7. Tabelle

Tutte derivate e ricostruibili; nessuna è la fonte di verità.

**`graph_snapshots`** — una riga per esecuzione del job
`id`, `guild_id`, `as_of`, `window_start`, `window_end`, `params` (JSONB: H, C,
soglie, finestra di sessione usate), `code_version`, `created_at`.
I parametri sono salvati **dentro** lo snapshot: due snapshot calcolati con
parametri diversi non sono confrontabili e devono poterlo dichiarare.

**`graph_edges`** — archi per snapshot e layer
`snapshot_id`, `layer`, `src_author_id`, `dst_author_id`, `weight` (normalizzato
e decaduto), `weight_undecayed`, `raw_units` (minuti grezzi non normalizzati per
`voice`, conteggio per gli altri), `interaction_count` (sessioni condivise o
numero interazioni), `last_interaction_at`, `is_reconciled`.
Per i layer non diretti la coppia è memorizzata una volta sola, con
`src_author_id < dst_author_id`.

**`voice_sessions`** e **`voice_session_participants`** — la ricostruzione
persistita, non solo un passaggio in memoria: serve alla riconciliazione tra
snapshot, al debug, e più avanti alle metriche di autosostenibilità del falò
(`fondamenta-teoriche.md` §7.3).
Sessione: `id`, `guild_id`, `channel_id`, `started_at`, `ended_at`,
`participant_count`, `is_complete`, `reconciliation_note`.
Partecipante: `session_id`, `author_id`, `joined_at`, `left_at`, `is_reconciled`.

**`message_authors`** — vedi §6.

## 8. Parametri

| Parametro | Default | Stato |
|---|---|---|
| Finestra di sessione | 30 min | Provvisoria — da rifittare (Halfaker & Keyes) |
| Soglia minima di sovrapposizione | 5 min | Ingegneria nostra, da validare |
| Emivita del decadimento `H` | 7 giorni | Ingegneria nostra, primo parametro da ritarare |
| Cutoff del decadimento `C` | 30 giorni | Da Millington, §7.2 |
| Tetto sessione orfana | 12 ore | Euristica di plausibilità |
| Normalizzazione dimensione | `1/(n−1)` | Da §10.3; alternativa futura: odds ratio |
| Cadenza snapshot | settimanale | Deciso |

Tutti in un unico modulo di configurazione, non sparsi nel codice: cambiarli
deve essere una modifica di un file, e ogni snapshot registra i valori usati.

## 9. Fuori da v0

Deliberatamente non in questa specifica:

- Qualunque **punteggio di legame unico** che combini i layer — resta la domanda
  aperta di `mvp-kindling-v0.md` §4/§7 (Gilbert & Karahalios 2009 vs Kivelä et
  al. 2014), e la letteratura SNA classica sconsiglia di risolverla sommando.
- Metriche SNA (centralità, Leiden, robustezza): questa specifica si ferma agli
  archi.
- Odds ratio come normalizzazione alternativa (§10.3).
- Layer di affiliazione da RSVP a eventi.
- Rilevamento di co-presenza ostile: alta co-presenza vocale non è di per sé un
  segnale positivo (`glossario-kindling.md` §28), ma il giudizio resta umano.
