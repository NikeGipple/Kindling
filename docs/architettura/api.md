# API — nota di progettazione

*Decisa il 5 settembre 2026. Breve di proposito: la superficie è piccola. Chiude
le decisioni che il codice non deve prendere da solo, e niente di più.*

L'API è il layer che serve gli aggregati al dashboard. Non calcola niente: legge
tabelle già pronte, come previsto da `architettura.md` §4 ("l'API non carica mai
il graph engine nel path di serving").

## 1. Il vincolo da cui dipende tutto il resto

**L'API non legge nessuna tabella che contenga dati riferibili a una persona.**

È un criterio, non una lista, e la differenza conta: una tabella aggiunta domani
si classifica da sola, mentre una lista va ricordata — e chi la dimentica non
riceve nessun segnale. È la stessa correzione già fatta due volte in questo
progetto: il vincolo di soppressione è un trigger che ricava le colonne dal
catalogo invece di enumerarle (`modello-metriche.md` §9.1), e la regola di
ammissione degli archi è una funzione sola invece di due implementazioni
d'accordo per caso (§2.3). Ogni volta il difetto era lo stesso: un elenco che
qualcuno deve tenere aggiornato.

Come si applica:

| Tabella | Fuori/dentro | Perché |
|---|---|---|
| `raw_events` | **fuori** | ogni riga ha un `author_id` |
| `graph_edges` | **fuori** | un arco è una relazione tra due persone nominate |
| `voice_sessions`, `voice_session_participants` | **fuori** | chi era in canale, con chi, quando |
| `message_authors` | **fuori** | `message_id → author_id` |
| `members` | **fuori** | il caso non ovvio: vedi sotto |
| `guilds` | **dentro** | fatti sul server e sul deployment del bot |
| `metric_*` (sei tabelle) | **dentro** | aggregati già sopra soglia |

**`members` è il caso da non sbagliare.** A colpo d'occhio sembra una tabella di
date — `joined_at`, `left_at` — e "sono solo date" è vero solo finché non si
guarda *di chi* sono: la chiave è `(guild_id, author_id)`, quindi ogni riga dice
quando una persona specifica è entrata e quando se n'è andata. È esattamente il
profilo individuale che il principio esclude, e il fatto che le colonne siano
innocue prese da sole è la ragione per cui una lista di divieti lo lascerebbe
passare mentre il criterio no.

**`guilds` invece entra.** `guild_id`, `first_seen_at`, `backfilled_at`,
`left_at`, `rejoined_at` sono fatti sul server Discord e sul deployment del bot:
nessuno di essi è riferibile a una persona. Era finita fuori nella prima stesura
di questa nota per categoria — "non è una tabella `metric_*`" — che è appunto il
ragionamento per lista.

Il criterio non resta una convenzione: §4 lo rende una garanzia del database.

**E un valore non viaggia mai senza i flag che dicono quanto vale.**
`is_suppressed`, `is_significant`, `is_survivors_only`, `has_snapshot_coverage`,
`not_computable_reason`, `details` sono parte del dato. Un endpoint che
restituisce `{"targeted_excess": 0.31}` senza dire che quella riga non è
significativa annulla tutto il lavoro fatto per rendere dichiarabile la
differenza. §3 è la forma che questo prende nelle risposte.

## 2. Endpoint

Sette, e nessuno in più per simmetria con le tabelle.

| Endpoint | Risponde a |
|---|---|
| `GET /health` | l'API è viva e Postgres risponde |
| `GET /guilds` | quali community sono osservate, da quando, e a che data arrivano le metriche |
| `GET /guilds/{guild_id}` | l'osservabilità di una community: ancora, backfill, buchi |
| `GET /guilds/{guild_id}/runs` | **stato dell'ultimo snapshot** e storico delle esecuzioni |
| `GET /guilds/{guild_id}/robustness` | serie storica della robustezza strutturale |
| `GET /guilds/{guild_id}/communities` | serie storica di Leiden, distribuzione delle dimensioni inclusa |
| `GET /guilds/{guild_id}/cohorts` | serie per coorte: onboarding **e** retention insieme |

`GET /health` esegue una query banale su Postgres e risponde. Serve al
`depends_on: condition: service_healthy` del compose quando arriverà il
dashboard, e costa poco adesso.

Gli endpoint di serie accettano `limit` (default 12, massimo 200), ordinati dal
più recente. Lo "stato dell'ultimo snapshot" è `?limit=1`: non serve un endpoint
dedicato, e averlo significherebbe tenere allineata una forma di risposta in più
alle altre per risparmiare tre chiamate HTTP a un dashboard che gira sulla stessa
macchina. `runs` porta `as_of`, `params`, `stats` (durate per metrica) e
`code_version` — che è la domanda "quando ha girato l'ultima volta, quanto ci ha
messo, con quali parametri", cioè quella operativa.

**Due tabelle non hanno un endpoint proprio, ed è una decisione, non una
dimenticanza.**

- **`metric_cohort_retention` è dentro `/cohorts`.** Le due tabelle condividono
  la popolazione per progetto (`modello-metriche.md` §5.5), e `n_effective`,
  `excluded_rejoins` e `is_survivors_only` sono duplicati proprio perché una
  divergenza tra i due denominatori sia verificabile. Servirle da due endpoint
  inviterebbe a leggerle separate, che è il modo di fallire contro cui quella
  duplicazione esiste. La risposta raggruppa per `cohort_start`: una coorte con
  dentro le sue righe di onboarding (per `layer_scope` e `k`) e le sue righe di
  retention (per orizzonte).
- **`metric_community_sizes` è dentro `/communities`.** La distribuzione è di
  quella partizione, e la sua soppressione secondaria è calcolata rispetto al
  totale della riga madre: leggerla da sola significherebbe avere i bucket senza
  il totale a cui si riferiscono, cioè la condizione in cui la soppressione
  secondaria non protegge più niente.

**Guild sconosciuta: 404. Guild senza metriche: serie vuota.** Sono due cose
diverse e l'API le distingue, perché `guilds` è leggibile (§1): se la community
non è in `guilds` il bot non l'ha mai vista, e una serie vuota lo direbbe come se
fosse una community osservata e ancora senza dati.

### `GET /guilds/{guild_id}` — e perché `first_seen_at` si espone di proposito

Restituisce il record di osservabilità: `first_seen_at`, `backfilled_at`,
`left_at`, `rejoined_at`, più l'`as_of` dell'ultima run di metriche.

**`first_seen_at` è l'ancora di osservabilità** (`modello-metriche.md` §5.5),
cioè la spiegazione di `is_survivors_only` e di `before_observability_anchor`.
Senza, un dashboard mostra metà tabella qualificata come inaffidabile e non può
dire al community manager *perché*: che osserviamo solo da quella data, e che
prima di allora chi era già uscito non ha lasciato traccia. **Servire i caveat
senza la ragione dei caveat è peggio che non servirli** — trasforma una
limitazione spiegabile in un difetto apparente del sistema.

**`left_at` e `rejoined_at` si espongono per lo stesso motivo.** Se sono
entrambi valorizzati, l'ancora di quella guild **non è più un istante solo**: c'è
un buco di osservazione in mezzo, e le uscite avvenute lì dentro sono invisibili
esattamente come quelle precedenti all'ancora (`modello-metriche.md` §5.6). È
un'informazione che cambia la lettura di ogni metrica di coorte di quella
community, e tenerla nascosta lascerebbe il consumatore con dei numeri il cui
limite non è deducibile da nessuna parte. Su una guild senza interruzioni sono
entrambi `null`, che è il caso normale e non chiede niente a chi legge.

## 3. Come i flag sopravvivono alla serializzazione

**Ogni riga è `{chiave…, quality: {...}, values: {...}}`.** I valori stanno in un
oggetto annidato, la qualificazione in un altro accanto.

```json
{
  "layer": "voice",
  "removal_fraction": 0.10,
  "quality": {
    "n_effective": 9,
    "suppressed": false,
    "suppression_reason": null,
    "significant": false,
    "details": { "not_significant_because": ["too_few_nodes"] }
  },
  "values": {
    "giant_before": 1.0,
    "giant_after_targeted": 0.111,
    "targeted_excess": 0.44,
    "targeted_z": null
  }
}
```

**Considerata e scartata: un oggetto per *ogni* valore** (`targeted_excess:
{value, significant, reason}`). È la forma che meglio impedisce di leggere il
numero nudo, ma applicata a tutti i campi sarebbe **falsa**: quasi tutti i flag
dei nostri dati qualificano *la riga*, non il singolo campo. Ripetere
`is_significant` su dieci campi implicherebbe una granularità che quei dieci
campi non hanno, e inventare una struttura che il dato non ha è esattamente ciò
che questo progetto ha rifiutato di fare altrove.

**Ma la granularità per valore esiste, in due punti**, ed entrambi stanno nelle
coorti (`modello-metriche.md` §7.3):

- `median_reached` qualifica **solo** `median_days_to_k`;
- `is_computable` e `not_computable_reason` qualificano **solo**
  `retained_fraction`.

Quindi la regola è: **i flag di riga stanno in `quality`, i due flag puntuali
stanno accanto al valore che qualificano.** Metterli in `quality` li farebbe
sembrare giudizi sull'intera riga — `is_computable` in `quality` direbbe "questa
riga di retention non è calcolabile", mentre dice soltanto se *quel numero*
esiste, ed è la riga di retention il posto in cui sbagliare livello fa più
danno:

```json
{
  "horizon_days": 28,
  "quality": {
    "n_effective": 40,
    "suppressed": false,
    "suppression_reason": null,
    "is_survivors_only": true,
    "excluded_rejoins": 0
  },
  "values": {
    "retained_fraction": null,
    "is_computable": false,
    "not_computable_reason": "before_observability_anchor"
  }
}
```

Quindi la qualificazione sta dove sta nel dato, e la difesa è strutturale in un
altro modo:

1. **Per arrivare a un numero bisogna passare da `values`.** Non si può
   destrutturare la riga e ottenere `targeted_excess` senza aver visto `quality`
   come suo fratello: è lì, allo stesso livello, non in coda a dieci campi.
2. **`quality` è obbligatorio nel modello base**, non `Optional`. Ogni risposta
   di metrica eredita da `MetricRow`, che lo dichiara richiesto: una risposta
   nuova che se ne dimentica non compila lo schema, invece di partire e perdere i
   flag in produzione.
3. **Su una riga soppressa `values` c'è, con tutti i campi a `null`.** Non
   sparisce: se sparisse, "soppressa" somiglierebbe a "assente" a livello di
   JSON, che è la stessa confusione tra zero e NULL che il layer di calcolo
   evita in tabella. La forma della risposta resta stabile e chi la analizza non
   deve ramificare.

**Dentro `/cohorts` ogni riga annidata porta il proprio `quality`.** Il
raggruppamento per `cohort_start` non deve far collassare due qualificazioni
diverse in una sola: una riga di onboarding e una di retention della stessa
coorte possono essere soppresse per ragioni diverse, ed è normale che lo siano —
la retention è calcolabile a orizzonti diversi. Il `cohort_start` è solo la
chiave che le tiene insieme, non un livello che le qualifica.

### `quality.details` è diagnostica, non contratto

`details` viaggia così com'è: contiene il motivo (`not_significant_because`,
`stability_unavailable`, `snapshot_gaps`, `without_reconciled`) ed è per
costruzione privo di dati per-nodo (`modello-metriche.md` §8).

**Il suo contenuto cambia insieme al codice del job, e i consumatori non devono
dipendere dalle sue chiavi.** Ha lo stesso statuto di `graph_snapshots.stats` e
di `metric_runs.stats`: la scatola nera dell'esecuzione, utile da leggere quando
un numero sembra sbagliato, non una struttura su cui costruire. Un dashboard che
ci disegna sopra un grafico si rompe al primo cambio del job — e non è un caso
teorico, perché quell'insieme di contatori è nato apposta per crescere senza
richiedere una migrazione. Ciò su cui si può contare sono le colonne tipizzate:
`quality` e `values`.

`previous_snapshot_id` resta nella risposta come **id opaco**: serve a dire "il
confronto è con quello", ma il consumatore non può risolverlo, perché
`graph_snapshots` non è leggibile dall'API.

## 4. Ruolo Postgres di sola lettura

L'API si collega con un ruolo dedicato che ha `SELECT` **solo** sulle sette
tabelle leggibili di §1 — le sei `metric_*` più `guilds` — e nient'altro: né
scrittura, né lettura delle tabelle che contengono dati riferibili a una
persona.

**Perché, e non è igiene.** Oggi "l'API non legge `graph_edges`" è una
convenzione, verificata da chi rilegge il codice. Con un ruolo dedicato diventa
una garanzia del database: un endpoint scritto per errore contro una tabella
interna fallisce con un errore di permessi invece di funzionare. È lo stesso
ragionamento del trigger di soppressione (`modello-metriche.md` §9.1) — la
garanzia non sta nel codice che la deve rispettare.

Migration `0010`, additiva e idempotente. Due note che la rendono meno ovvia di
quanto sembri:

- **`CREATE ROLE IF NOT EXISTS` non esiste in Postgres.** Serve un blocco `DO`
  che controlli `pg_roles` prima di creare.
- **La migration non contiene nessuna password.** Il ruolo si crea con `LOGIN` e
  la password si imposta a parte sulla droplet (`ALTER ROLE … PASSWORD`),
  leggendola dal `.env`. Una migration sta in git; una password no.

Nel `.env.example` si aggiunge `API_DATABASE_URL=` vuota — la connection string
dell'API, distinta da `DATABASE_URL`, che resta quella del ruolo proprietario
usato da bot e job.

**Trappola da conoscere:** i `GRANT` sono espliciti, tabella per tabella, e non
si usa `ALTER DEFAULT PRIVILEGES`. Conseguenza voluta: una tabella leggibile
aggiunta in futuro **non** lo sarà finché non le si dà il `GRANT` — il sintomo è
un errore di permessi chiaro. Il criterio di §1 dice *quali* tabelle sono
leggibili; il `GRANT` resta un atto esplicito, perché è la differenza tra
decidere e lasciar accadere. L'alternativa (privilegi di default sullo
schema) renderebbe leggibile automaticamente anche una tabella interna aggiunta
domani, cioè il contrario di quello che serve. Meglio un `GRANT` dimenticato che
fallisce rumorosamente che una tabella interna esposta in silenzio.

## 5. Nessuna esposizione pubblica in questa fase

Il servizio `api` si aggiunge allo stesso `docker-compose.yml` di `postgres`,
`bot` e `job`, **senza `ports:` pubblicati**: raggiungibile dagli altri container
sulla rete interna del compose e, per lo sviluppo, via tunnel SSH come già si fa
con Postgres.

Niente Caddy, niente 443 aperta sul Cloud Firewall, **niente autenticazione** —
perché non c'è ancora niente di esposto da autenticare, e un'autenticazione
posticcia "per intanto" è peggio di nessuna: dà l'impressione che il problema sia
risolto.

**TLS e autenticazione sono un prerequisito del momento in cui l'API diventa
raggiungibile da fuori, non un affinamento successivo.** Quel momento è quando
esisterà il dashboard, ed è lo stesso punto in cui `architettura.md` colloca il
vero lavoro aggiuntivo: "si apre una porta nuova su internet, quindi la si apre
con TLS e autenticazione già configurate, non 'per provare'". Aggiungere `ports:`
a questo servizio senza aver fatto quel passo è la versione API dell'incidente
descritto in `CLAUDE.md`.

## 6. Memoria

Sulla droplet da 1 GB il budget stimato è ~80-120 MB (`architettura.md`, sezione
Hosting).

- `mem_limit: 200m` sul servizio: margine sopra la stima, ma un tetto — se
  qualcosa deve morire per esaurimento memoria non deve essere Postgres o il bot.
- **uvicorn con 1 worker.** Più worker moltiplicherebbero la RAM su una macchina
  che non ha CPU da parallelizzare: 1 vCPU condiviso con l'heartbeat del gateway
  Discord.
- Pool asyncpg piccolo (`min_size=1`, `max_size=2`): le query sono letture
  puntuali su tabelle minuscole, e un pool grande sarebbe solo memoria ferma.

## 7. Fuori da questa fase

- **Nessun endpoint di scrittura.** L'API legge e basta.
- **Nessun endpoint sul grafo**, sugli archi o su qualunque cosa per-nodo.
- **Nessuna aggregazione calcolata al volo.** Soglie e soppressione vivono nel
  layer di calcolo, e un aggregato costruito a runtime le aggirerebbe: sommare
  due celle soppresse in una cella sopra soglia è precisamente ciò che la regola
  di §6.4 di `modello-metriche.md` impedisce. Se serve un aggregato che non
  esiste, si aggiunge al job.
- **Nessuna autenticazione posticcia** (§5).
- Le metriche del catalogo non ancora calcolate (concentrazione strutturale,
  densità cross-community, reciprocità, trend) non hanno endpoint perché non
  hanno tabelle: arriveranno con il job, non con l'API.
