#!/usr/bin/env bash
#
# Deploy di una modifica gia' pushata su GitHub: pull, controllo migration,
# build, riavvio dei soli servizi che servono, verifiche.
#
# Stesso argomento di ops/kindling-weekly.sh, e per lo stesso motivo: una
# procedura di sei passi con un ordine che conta, un comando controintuitivo
# (`up -d api dashboard caddy` e non `up -d`) e un valore calcolato (l'hash del commit) non si
# legge, non si prova a mano e non si commenta se sta in una riga di
# istruzioni copiaincollate dal runbook. Qui invece ogni scelta ha il suo
# perche' accanto, e lo script si puo' rileggere, provare e correggere come
# qualunque altro file del repository.
#
#     <repo>/ops/kindling-deploy.sh
#
# Non applica NESSUNA migration: le elenca se ne manca qualcuna e si ferma.
# Applicarle e' la parte che questo progetto tratta come deliberata e non
# automatizzabile (vedi runbook-droplet.md, "Deploy di una modifica che tocca
# schema e codice" — Migration prima, sempre, e a mano). Uno script che le
# applicasse da solo le renderebbe invisibili nel momento in cui contano di
# piu': prima che tocchino dati veri.
#
# Nessuna credenziale qui dentro: DATABASE_URL e le altre variabili vivono nel
# .env della droplet, che docker compose legge da solo (env_file) e che non e'
# in git. KINDLING_CODE_VERSION non ci vive: arriva dall'immagine (vedi
# Dockerfile e docker-compose.yml, job.build.args) — questo script la calcola
# e la passa, non la scrive in nessun file.

set -euo pipefail

# Nessun prompt, mai: uno script di deploy lanciato da un terminale che poi si
# stacca (tmux, screen, o semplicemente una sessione SSH che cade) non deve
# restare a aspettare input su niente. Stessa riga e stesso perche' di
# ops/kindling-weekly.sh: `docker compose` non chiede input di norma, ma
# chiuderla qui copre anche i comandi che verranno aggiunti dopo.
exec 0< /dev/null

# --- percorsi assoluti -----------------------------------------------------
#
# Stessa disciplina di ops/kindling-weekly.sh: l'ambiente da cui si lancia un
# deploy (una sessione SSH minimale, o cron per un deploy automatizzato futuro)
# non garantisce il PATH di una shell interattiva.

PROJECT_DIR="${KINDLING_PROJECT_DIR:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"
DOCKER_BIN="${KINDLING_DOCKER_BIN:-/usr/bin/docker}"
GIT_BIN="${KINDLING_GIT_BIN:-/usr/bin/git}"
LOG_FILE="${KINDLING_DEPLOY_LOG_FILE:-/var/log/kindling/deploy.log}"
# Pausa fra i tentativi di `caddy reload` (passo 5-bis). Variabile solo perche'
# i test non aspettino dieci secondi per provare un reload che fallisce.
CADDY_RELOAD_PAUSE="${KINDLING_CADDY_RELOAD_PAUSE:-2}"
# python3 dell'host, per confrontare due JSON (passo 5-ter). Su Ubuntu c'e' di
# serie; variabile per i test, come docker e git.
PYTHON_BIN="${KINDLING_PYTHON_BIN:-/usr/bin/python3}"

# --- log ---------------------------------------------------------------
#
# File piu' stdout, non solo file: ops/kindling-weekly.sh logga solo su file
# perche' lo lancia cron, senza nessuno davanti allo schermo. Questo script lo
# lancia sempre una persona, che deve vedere l'esito subito — ma una traccia
# su file resta utile per confrontare deploy successivi, come job.log per le
# esecuzioni settimanali.

log() {
    local riga
    riga="$(date --utc '+%Y-%m-%dT%H:%M:%SZ') $*"
    printf '%s\n' "$riga"
    printf '%s\n' "$riga" >>"$LOG_FILE"
}

mkdir -p "$(dirname "$LOG_FILE")"

# --- codici di uscita --------------------------------------------------
#
# Distinti e non tradotti in un generico 1: un deploy fermato per migration
# mancanti, uno fermato perche' il ledger stesso manca, uno fermato perche' il
# perimetro del ruolo kindling_api non e' quello atteso, uno fermato perche' la
# dashboard ha variabili di database (o non si e' potuto verificarlo), e uno
# fallito per un errore imprevisto sono situazioni diverse — e cosi' api o
# dashboard che non eseguono il commit del deploy (17) e un bot che va ancora
# ricreato a mano (18), o un Caddy che non esegue il Caddyfile corrente (19)
# — e chi legge l'esito
# da fuori (una shell interattiva, o in futuro un monitoraggio) deve poterle
# distinguere senza andare a leggere il log.
MIGRATIONS_MISSING_EXIT=10
MIGRATIONS_NO_LEDGER_EXIT=11
PERIMETER_BROKEN_EXIT=12
DASHBOARD_DB_VARS_EXIT=13
DASHBOARD_UNVERIFIED_EXIT=14
CADDY_CONFIG_EXIT=15
CADDY_VOLUME_EXIT=16
CODE_VERSION_MISMATCH_EXIT=17
BOT_CODE_STALE_EXIT=18
CADDY_CONFIG_STALE_EXIT=19

# Le variabili che il container dashboard non deve AVERE (dashboard.md 1). Stesso
# elenco di dashboard/config.py DATABASE_VARIABLES: tests/test_dashboard_deploy.py
# fallisce se i due divergono, perche' un elenco aggiornato in un posto solo e'
# esattamente il difetto di CLAUDE.md 7.
DASHBOARD_FORBIDDEN_VARS="DATABASE_URL API_DATABASE_URL"

log "=== deploy ==="

# --- passo 1: prendere il codice --------------------------------------------
#
# La droplet vede solo quello che e' su GitHub: questo pull e' cio' che lo
# rende vero. Nessun controllo su "ci sono modifiche locali non committate" —
# sulla droplet non dovrebbero mai essercene, e se ci sono e' un sintomo da
# guardare a mano, non un caso da gestire in automatico.
log "START git-pull"
"$GIT_BIN" -C "$PROJECT_DIR" pull
log "END   git-pull"

# --- passo 2: la versione del codice, per l'immagine ------------------------
#
# Calcolata QUI, dal codice appena tirato — non letta da .env, che non la
# contiene piu' di proposito (vedi .env.example): un valore che deve
# descrivere l'immagine va dentro l'immagine, non in un file che puo'
# divergere da quello che e' stato davvero costruito. export e non solo
# assegnazione: docker compose legge le variabili per l'interpolazione
# ${...} di docker-compose.yml dall'ambiente della shell che lo invoca.
KINDLING_CODE_VERSION="$("$GIT_BIN" -C "$PROJECT_DIR" rev-parse --short HEAD)"
export KINDLING_CODE_VERSION
log "code_version=$KINDLING_CODE_VERSION"

# --- passo 3: fermarsi se manca qualche migration ---------------------------
#
# Non le applica: le elenca e si ferma. Vedi l'intestazione del file per il
# perche'. Il confronto e' con schema_migrations (migration 0012), non con
# un elenco scritto qui — un elenco scritto in questo script sarebbe
# esattamente la lista-che-ci-si-dimentica-di-aggiornare gia' registrata in
# CLAUDE.md 7.
check_migrations() {
    log "START check-migrations"

    # to_regclass non fallisce mai per una tabella assente: torna NULL (stampa
    # vuoto con -tAc). Prima si guardava il TESTO dell'errore di psql
    # ('... "schema_migrations" does not exist', in inglese, prodotto per un
    # umano) per distinguere "il ledger non c'e'" da "qualcos'altro e' andato
    # storto" — la stessa fragilita' che questo branch toglie altrove
    # (CLAUDE.md 7). Con to_regclass i due casi si separano da soli: un psql
    # che fallisce qui vuol dire davvero "qualcos'altro non va" (postgres non
    # ancora su, credenziali sbagliate), non "il ledger manca".
    local ledger
    local stato
    set +e
    ledger="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" \
        exec -T postgres psql -U kindling -d kindling \
        -tAc "SELECT to_regclass('schema_migrations')" 2>&1)"
    stato=$?
    set -e

    if [ "$stato" -ne 0 ]; then
        log "ABORT verifica migration fallita: $ledger"
        exit 1
    fi

    if [ -z "$ledger" ]; then
        log "ABORT nessun ledger schema_migrations: applicare prima 0012"
        echo "Il ledger delle migration (schema_migrations) non esiste ancora." >&2
        echo "Applicare 0012 prima di tutto il resto:" >&2
        echo "  docker compose exec postgres psql -U kindling -d kindling \\" >&2
        echo "    -f /docker-entrypoint-initdb.d/0012_schema_migrations.sql" >&2
        exit "$MIGRATIONS_NO_LEDGER_EXIT"
    fi

    local esito
    esito="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" \
        exec -T postgres psql -U kindling -d kindling \
        -tAc 'SELECT filename FROM schema_migrations ORDER BY filename')"

    local mancanti=()
    local percorso
    local nome
    for percorso in "$PROJECT_DIR"/migrations/*.sql; do
        nome="$(basename "$percorso")"
        if ! printf '%s\n' "$esito" | grep -qx "$nome"; then
            mancanti+=("$nome")
        fi
    done

    if [ "${#mancanti[@]}" -gt 0 ]; then
        log "ABORT migration non applicate: ${mancanti[*]}"
        echo "Migration non applicate, in ordine:" >&2
        for nome in "${mancanti[@]}"; do
            echo "  $nome" >&2
        done
        echo "" >&2
        echo "Applicarle a mano, in quest'ordine, poi rilanciare questo script:" >&2
        for nome in "${mancanti[@]}"; do
            echo "  docker compose exec postgres psql -U kindling -d kindling \\" >&2
            echo "    -f /docker-entrypoint-initdb.d/$nome" >&2
        done
        exit "$MIGRATIONS_MISSING_EXIT"
    fi

    log "END   check-migrations tutte applicate"
}

check_migrations

# --- passo 4: build --------------------------------------------------------
#
# Due comandi, non uno: `docker compose build` da solo NON ricostruisce
# `job`, perche' ha `profiles: ["tools"]` e Compose esclude i servizi fuori
# dal profilo attivo sia da `build` sia da `up`, senza avviso. E' successo
# davvero il 07/09/2026: build semplice, job rimasto all'immagine di tre
# giorni prima, metriche scritte con codice vecchio ed exit=0 — vedi
# CLAUDE.md 7. Da allora i due comandi vanno sempre insieme.
log "START build"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" build
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" --profile tools build job
log "END   build"

# --- passo 4-bis: il Caddyfile si valida PRIMA di avviare qualunque cosa -----
#
# Un Caddyfile sbagliato fa andare il container in restart al primo avvio, o
# (con un container gia' acceso) viene rifiutato dal reload del passo 5-bis:
# in entrambi i casi meglio saperlo qui, prima di toccare i servizi. `run --rm
# --no-deps`: un container usa e getta con lo stesso file montato, senza porte
# pubblicate e senza avviare la dashboard.
log "START caddy-validate"
if ! "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" run --rm --no-deps -T caddy \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    log "ABORT ops/caddy/Caddyfile non valido: nessun servizio toccato"
    exit "$CADDY_CONFIG_EXIT"
fi
log "END   caddy-validate"

# --- passo 5: riavviare solo i servizi che servono --------------------------
#
# `up -d api dashboard caddy`, MAI `up -d` nudo. `bot`, `api` e `dashboard` sono
# immagini distinte (non condivisa: `docker images` le elenca separate), ma
# nessuna ha un `profiles`, quindi un `up -d` senza argomenti ricrea TUTTI i
# container corrispondenti alle immagini appena costruite — non solo quelli che
# servono a questo deploy. Ricreare `bot` fa cadere il gateway Discord per una
# modifica che non lo riguarda. `job` non ha bisogno di un `up`: non e' un
# servizio sempre acceso, prende l'immagine appena costruita al prossimo `run`.
#
# L'elenco e' esplicito, quindi va tenuto allineato a mano: `build` qui sopra
# costruisce ogni servizio senza profiles, e un servizio sempre acceso che manca
# da questa riga ha l'immagine nuova e il container vecchio (o nessun
# container), con exit=0. E' successo in bozza con `dashboard`, aggiunta al
# compose mentre questa riga diceva ancora `up -d api` — la stessa forma del
# difetto di `profiles`/`job` in CLAUDE.md 7. tests/test_dashboard_deploy.py
# confronta questa riga con i servizi del compose.
#
# `caddy` e' un'immagine ufficiale, non costruita da noi, ma sta qui per la stessa
# ragione: senza profiles, un servizio del compose che il deploy non avvia resta
# spento (o vecchio) con exit=0.
log "START up-api-dashboard-caddy"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" up -d api dashboard caddy
log "END   up-api-dashboard-caddy"

# --- passo 5-bis: ricaricare il Caddyfile, e fallire se non si ricarica -----
#
# Il reload funziona solo perche' il mount e' una DIRECTORY (ops/caddy ->
# /etc/caddy, vedi docker-compose.yml). Fino al 19/09/2026 era il singolo file,
# legato all'inode: `git pull` sostituisce il file invece di riscriverlo, e
# questo `exec` nel container vecchio ricaricava il Caddyfile di quando il
# container era partito — riuscendo. Il passo 5-ter esiste per quel caso.
#
# Il Caddyfile sta in un bind mount: se cambia solo lui, `up -d` NON ricrea il
# container ed esce 0 con la configurazione vecchia ancora attiva — la stessa
# famiglia del `build` che saltava `job` perche' stava in un profilo (CLAUDE.md
# 7). Il reload e' esplicito e decide l'esito: Caddy valida prima di applicare,
# quindi un reload fallito lascia in piedi la configurazione precedente, ed e'
# proprio per questo che lo script deve dirlo invece di proseguire.
#
# Qualche tentativo, perche' su un container appena creato l'endpoint di
# amministrazione di Caddy puo' non essere ancora in ascolto: un fallimento
# spurio a ogni primo avvio insegnerebbe a ignorarlo.
log "START caddy-reload"
caddy_ricaricato=0
for tentativo in 1 2 3 4 5; do
    if "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T caddy \
        caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
        caddy_ricaricato=1
        break
    fi
    sleep "$CADDY_RELOAD_PAUSE"
done
if [ "$caddy_ricaricato" -ne 1 ]; then
    log "ABORT caddy reload fallito: Caddy serve ancora la configurazione precedente (o non e' partito)"
    exit "$CADDY_CONFIG_EXIT"
fi
log "END   caddy-reload"

# --- passo 5-ter: la configurazione in esercizio e' quella del file ---------
#
# Un reload riuscito dice che Caddy ha caricato QUALCOSA, non che abbia
# caricato il Caddyfile del repository: e' esattamente cosi' che il mount di
# file ha nascosto una modifica, scoperta solo perche' un `Alt-Svc` si vedeva
# da fuori. Una modifica che non si vede in un'intestazione sarebbe passata
# inosservata per sempre.
#
# Le due meta':
# - in esercizio: l'admin API del container che serve il traffico
#   (127.0.0.1:2019/config/, raggiungibile solo dall'interno: la 2019 non e'
#   pubblicata e non va pubblicata). `wget` e' quello di busybox, che
#   l'immagine alpine ha; curl no.
#
#   127.0.0.1 e NON `localhost`, anche se si legge peggio: il wget di busybox
#   risolve `localhost` e prova ::1 per primo, senza ripiegare su IPv4, mentre
#   Caddy ascolta su `localhost:2019` che in Go diventa 127.0.0.1 soltanto. Con
#   `localhost` il primo deploy di questo passo e' uscito 19 per un
#   "Connection refused" immediato, a configurazione identica (19/09/2026).
#   `caddy reload`, che e' Go e prova entrambe le famiglie, passava lo stesso.
#   tests/test_ops_script.py fallisce se qui torna un nome invece di un IPv4.
#
#   Nessun tentativo ripetuto, di proposito, a differenza dei cinque del
#   reload: il reload gira prima, riprova finche' l'endpoint risponde e ferma
#   lo script se non risponde. Quando si arriva qui la 2019 e' in ascolto per
#   costruzione. I tentativi del reload non sono di troppo: sono loro a
#   garantirlo.
# - attesa: `caddy adapt` in un container NUOVO, l'unico modo di leggere con
#   certezza il file che sta oggi sull'host.
#
# Il confronto e' fra STRUTTURE, non fra stringhe: l'admin API riserializza il
# JSON caricato (in Go: chiavi in ordine alfabetico, numeri nella forma piu'
# corta), quindi ordine delle chiavi, spazi e 1e+09 contro 1000000000 possono
# differire senza che la configurazione differisca. Un confronto testuale
# sarebbe rosso sempre. Si confronta TUTTA la configurazione e non una
# sottostruttura: /config/ restituisce esattamente cio' che `caddy adapt`
# produce. MISURATO in produzione sul deploy d2bffce (19/09/2026): le due meta'
# UGUALI, stesse chiavi di primo livello (`apps`, `logging`) — `logging`
# compreso. Non c'e' niente da ritagliare, e restringere il confronto a una
# parte vorrebbe dire smettere di vedere una modifica al resto.
#
# Non si esce subito: si registra, e l'uscita (19) viene con le altre in fondo,
# cosi' il perimetro di kindling_api e le altre verifiche si vedono comunque.
log "START caddy-config-in-esercizio"
caddy_config_stantia=0
caddy_config_motivo=""
if ! caddy_in_esercizio="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T caddy \
    wget -qO- http://127.0.0.1:2019/config/)"; then
    caddy_config_stantia=1
    caddy_config_motivo="admin API di caddy non raggiungibile: configurazione in esercizio non verificabile"
elif ! caddy_attesa="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" run --rm --no-deps -T caddy \
    caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile)"; then
    caddy_config_stantia=1
    caddy_config_motivo="caddy adapt fallito: configurazione attesa non verificabile"
else
    # Uscite: 0 uguali, 1 diverse (stampa il primo percorso diverso), 2 non
    # confrontabili (JSON illeggibile, configurazione attesa vuota). Un bool non
    # e' mai uguale a un numero, anche se per Python True == 1.
    set +e
    caddy_scarto="$(CADDY_IN_ESERCIZIO="$caddy_in_esercizio" CADDY_ATTESA="$caddy_attesa" \
        "$PYTHON_BIN" -c '
import json, os, sys

def primo_scarto(a, b, via):
    if isinstance(a, dict) and isinstance(b, dict):
        for chiave in sorted(set(a) | set(b)):
            if chiave not in a or chiave not in b:
                return via + "." + chiave
            trovato = primo_scarto(a[chiave], b[chiave], via + "." + chiave)
            if trovato:
                return trovato
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return via + " (lunghezza %d contro %d)" % (len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            trovato = primo_scarto(x, y, "%s[%d]" % (via, i))
            if trovato:
                return trovato
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        return None if type(a) is type(b) and a == b else via
    return None if a == b else via

try:
    in_esercizio = json.loads(os.environ["CADDY_IN_ESERCIZIO"])
    attesa = json.loads(os.environ["CADDY_ATTESA"])
except Exception as errore:
    print("JSON illeggibile: %s" % errore)
    sys.exit(2)
if not isinstance(attesa, dict) or not attesa:
    print("configurazione attesa vuota")
    sys.exit(2)
scarto = primo_scarto(in_esercizio, attesa, "$")
if scarto is None:
    sys.exit(0)
print(scarto)
sys.exit(1)
')"
    confronto=$?
    set -e
    case "$confronto" in
        0) ;;
        1)
            caddy_config_stantia=1
            caddy_config_motivo="Caddy non esegue il Caddyfile corrente (primo punto diverso: $caddy_scarto)" ;;
        *)
            caddy_config_stantia=1
            caddy_config_motivo="confronto della configurazione di caddy non riuscito (${caddy_scarto:-uscita $confronto}): non verificabile" ;;
    esac
fi
if [ "$caddy_config_stantia" -ne 0 ]; then
    log "ABORT $caddy_config_motivo"
    echo "Ricreare il container di Caddy, che rilegge il Caddyfile da zero:" >&2
    echo "  docker compose up -d --force-recreate caddy" >&2
else
    log "END   caddy-config-in-esercizio uguale al Caddyfile corrente"
fi

# --- passo 6: verifiche ------------------------------------------------
#
# Due tipi, e non vanno confusi. Le prime quattro sono INFORMATIVE: stampate
# per rileggere a colpo d'occhio la stessa checklist del runbook (sezione
# "Avvio e verifica") senza doverla ricopiare a mano — se una va male lo si
# vede, ma lo script prosegue, perche' un'immagine con una data strana o un
# health check lento non sono di per se' un deploy fallito.
#
# Le ultime due NON lo sono: sono il perimetro del ruolo `kindling_api`, il
# primo invariante non negoziabile del progetto (runbook-droplet.md dice
# testualmente "fermarsi li', perche' in quello stato l'API puo' leggere le
# tabelle interne"). Prima qui si stampava solo `ATTENZIONE` e si usciva 0 — in
# fondo a una schermata lunga, dopo un build: il posto esatto in cui un avviso
# non viene letto. Un deploy che lascia l'API in grado di leggere le tabelle
# interne non e' un deploy riuscito con una nota a margine, quindi adesso
# ferma lo script con un codice di uscita dedicato. Stesso criterio, dopo, per
# la dashboard senza variabili di database, i certificati di Caddy e la
# versione del codice che api, dashboard e bot eseguono davvero.
log "START verifiche"

echo ""
echo "--- date delle immagini (da guardare, nessuno le controlla: la verifica e' KINDLING_CODE_VERSION di api, dashboard e bot, piu' sotto) ---"
"$DOCKER_BIN" images --format '{{.Repository}}	{{.CreatedAt}}' | grep kindling || true

echo ""
echo "--- KINDLING_CODE_VERSION dentro il container job (informativo) ---"
# Solo il job, e solo da guardare. Per dodici giorni e' stata l'UNICA verifica
# della versione, ed era verde perche' il job era l'unico servizio a cui il
# build-arg arrivava: copriva il caso gia' sano. Le verifiche che decidono sono
# quelle di api, dashboard e bot, piu' sotto. Attenzione a leggerla: `printenv`
# di una variabile vuota stampa una riga vuota ed esce 0.
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" run --rm -T job \
    printenv KINDLING_CODE_VERSION || echo "(vuoto o comando fallito)"

echo ""
echo "--- health dell'API ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T api python -c \
    "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())" \
    || echo "(fallito)"

echo ""
echo "--- stato dei container api, dashboard e caddy ---"
# `ps` e non un health check via exec: subito dopo `up -d` la dashboard puo'
# essere ancora in avvio, e un "(fallito)" che compare a ogni deploy insegna a
# ignorarlo. `ps` distingue "Up (health: starting)" da "Restarting", che e' il
# sintomo di una dashboard che non parte (es. KINDLING_API_BASE_URL vuota).
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" ps api dashboard caddy \
    || echo "(fallito)"

echo ""
echo "--- API_DATABASE_URL usa davvero kindling_api ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T api sh -c \
    'echo "$API_DATABASE_URL" | sed "s#:[^:@]*@#:***@#"'

# --- perimetro del ruolo kindling_api: non informativo, decide l'esito -----
#
# `if cmd; then` e non `set +e`/`set -e`: sotto `set -e` il comando in
# condizione di un `if` e' gia' esente dall'aborto immediato, quindi non serve
# disattivarlo. perimetro_rotto accumula invece di uscire al primo problema,
# cosi' chi legge vede ENTRAMBI gli esiti prima che lo script si fermi, non
# solo il primo.
perimetro_rotto=0

echo ""
echo "--- perimetro del ruolo kindling_api: deve FALLIRE ---"
if "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM graph_edges"; then
    log "ABORT kindling_api legge graph_edges: perimetro compromesso"
    echo "ATTENZIONE: kindling_api legge graph_edges, non dovrebbe" >&2
    perimetro_rotto=1
else
    echo "(fallito come atteso: permission denied)"
fi

# graph_snapshots: la TABELLA resta illeggibile, e non perche' contenga dati di
# qualcuno — non ne contiene. E' che cio' che l'API puo' leggere dev'essere un
# elenco DECISO e non uno che cresce da solo (api.md 1): con il SELECT sulla
# tabella, una colonna aggiunta domani sarebbe leggibile senza che nessuno
# l'abbia scelto. Dal 25/09/2026 quel che si legge e' la sola vista
# graph_snapshot_params, controllata sotto.
#
# Questi due controlli esistevano solo in tests/test_parametri_grafo.py, cioe'
# addosso al repository. Un GRANT allargato a mano sulla droplet — il modo in cui
# questi permessi si allargano davvero, durante un debug — non incontrava niente
# fino al successivo `pytest` di qualcun altro.
if "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM graph_snapshots"; then
    log "ABORT kindling_api legge graph_snapshots: perimetro compromesso"
    echo "ATTENZIONE: kindling_api legge graph_snapshots, dovrebbe leggere solo" >&2
    echo "  la vista graph_snapshot_params (migration 0014)" >&2
    perimetro_rotto=1
else
    echo "(fallito come atteso: permission denied)"
fi

echo ""
echo "--- perimetro del ruolo kindling_api: deve RIUSCIRE ---"
if ! "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM metric_runs"; then
    log "ABORT kindling_api non legge metric_runs: perimetro compromesso"
    echo "ATTENZIONE: kindling_api non legge metric_runs, dovrebbe" >&2
    perimetro_rotto=1
fi

# La vista, senza la quale GET /guilds/{id}/runs fallisce — cioe' OGNI pagina
# della dashboard, perche' tutte passano di li'. Il caso non e' teorico: 0010
# comincia con REVOKE ALL ON ALL TABLES IN SCHEMA, che in Postgres comprende
# anche le viste, quindi riapplicare 0010 DA SOLA dopo 0014 toglie proprio
# questo permesso.
if ! "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM graph_snapshot_params"; then
    log "ABORT kindling_api non legge graph_snapshot_params: perimetro compromesso"
    echo "ATTENZIONE: kindling_api non legge la vista graph_snapshot_params." >&2
    echo "  Applicare 0014, o riapplicare TUTTE le migration in ordine: 0010 da" >&2
    echo "  sola revoca anche i permessi sulle viste." >&2
    perimetro_rotto=1
fi

# --- la dashboard non ha variabili di database: non informativo -------------
#
# dashboard.md 1: la dashboard parla solo con l'API, e il suo container non
# RICEVE nessuna variabile di database — non "non le usa": non le ha. Una
# variabile aggiunta al compose per comodita' durante un debug e mai tolta e'
# esattamente il genere di cosa che nessuno rilegge.
#
# `docker inspect` sulla configurazione del container, NON `exec ... env`: se una
# variabile c'e', dashboard/config.py rifiuta di partire e il container va in
# restart, quindi un `exec` fallirebbe — e un exec fallito letto come "nessuna
# variabile trovata" sarebbe un controllo che passa proprio quando la regola e'
# violata (CLAUDE.md 7). Per lo stesso motivo "non si e' potuto verificare" ha un
# codice suo e non passa mai per "tutto a posto".
#
# Si confrontano i NOMI (`NOME=` a inizio riga), anche con valore vuoto: una riga
# `DATABASE_URL=` e' gia' una variabile ricevuta. I valori non si stampano mai.
dashboard_rotta=0
dashboard_non_verificata=0

echo ""
echo "--- la dashboard non ha variabili di database: deve essere VUOTO ---"
dashboard_id="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" ps -a -q dashboard || true)"
if [ -z "$dashboard_id" ]; then
    log "ABORT container dashboard non trovato: variabili di database non verificabili"
    echo "ATTENZIONE: nessun container dashboard, controllo non eseguito" >&2
    dashboard_non_verificata=1
elif ! dashboard_env="$("$DOCKER_BIN" inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$dashboard_id")"; then
    log "ABORT docker inspect della dashboard fallito: variabili di database non verificabili"
    echo "ATTENZIONE: docker inspect fallito, controllo non eseguito" >&2
    dashboard_non_verificata=1
else
    for nome in $DASHBOARD_FORBIDDEN_VARS; do
        # Here-string e non `printf | grep -q`: con pipefail, grep -q che esce al
        # primo match puo' far morire printf di SIGPIPE e rendere FALSA la
        # pipeline proprio quando la variabile c'e'.
        if grep -q "^${nome}=" <<<"$dashboard_env"; then
            log "ABORT la dashboard ha la variabile $nome: il perimetro di dashboard.md 1 e' violato"
            echo "ATTENZIONE: il container dashboard ha $nome." >&2
            echo "  Toglierla da docker-compose.yml (services.dashboard: nessun env_file," >&2
            echo "  environment con la sola KINDLING_API_BASE_URL), poi rilanciare lo script." >&2
            dashboard_rotta=1
        fi
    done
    if [ "$dashboard_rotta" -eq 0 ]; then
        echo "(nessuna variabile di database)"
    fi
fi

# --- i certificati di Caddy persistono: non informativo ---------------------
#
# /data del container caddy deve essere il volume NOMINATO caddy_data, non un
# volume anonimo ne' niente: altrimenti ogni ricreazione chiede certificati
# nuovi e al sesto della settimana Let's Encrypt rifiuta (dashboard-fase2.md
# 8-bis) — il guasto arriva giorni dopo il deploy che l'ha causato.
#
# Per ETICHETTA (com.docker.compose.volume=caddy_data) e non per nome: Compose
# antepone il nome del progetto, e un controllo sul nome indovinato passerebbe
# o fallirebbe per la ragione sbagliata. E dopo `up`: prima del primo avvio il
# volume non esiste ancora.
caddy_volume_rotto=0

echo ""
echo "--- /data di caddy e' il volume caddy_data ---"
caddy_id="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" ps -a -q caddy || true)"
caddy_volumi="$("$DOCKER_BIN" volume ls -q --filter label=com.docker.compose.volume=caddy_data || true)"
if [ -z "$caddy_id" ]; then
    log "ABORT container caddy non trovato: persistenza dei certificati non verificabile"
    caddy_volume_rotto=1
elif [ -z "$caddy_volumi" ]; then
    log "ABORT nessun volume con etichetta com.docker.compose.volume=caddy_data"
    caddy_volume_rotto=1
else
    caddy_mount="$("$DOCKER_BIN" inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}} {{.Name}}{{end}}{{end}}' "$caddy_id" || true)"
    montato=0
    for volume in $caddy_volumi; do
        if [ "$caddy_mount" = "volume $volume" ]; then
            montato=1
        fi
    done
    if [ "$montato" -eq 1 ]; then
        echo "(ok: $caddy_mount)"
    else
        log "ABORT /data di caddy non e' il volume caddy_data (montato: '${caddy_mount:-niente}')"
        caddy_volume_rotto=1
    fi
fi

# --- quale codice gira davvero: non informativo -----------------------------
#
# KINDLING_CODE_VERSION letta dalla CONFIGURAZIONE del container (`docker
# inspect`, Config.Env), non con `exec ... printenv`, per due ragioni:
# - `printenv` di una variabile vuota esce 0 e stampa una riga vuota, quindi un
#   controllo sul codice di uscita prenderebbe "vuota" per un successo — la
#   classe di difetto di CLAUDE.md 7 dentro il controllo scritto per scoprirla;
# - un container in restart non accetta `exec`, e la sua configurazione si
#   legge lo stesso.
# Quattro esiti distinti, e solo il primo porta un valore: "ok <hash>", "vuota"
# (immagine costruita senza il build-arg), "assente" (immagine costruita senza
# l'ARG del Dockerfile), e "non verificabile" (nessun container, inspect
# fallito). Solo "ok" puo' passare.
code_version_del_container() {
    local servizio="$1"
    local id
    local ambiente
    local riga
    id="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" ps -a -q "$servizio" || true)"
    if [ -z "$id" ]; then
        echo "non-verificabile: nessun container $servizio"
        return
    fi
    if ! ambiente="$("$DOCKER_BIN" inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id")"; then
        echo "non-verificabile: docker inspect di $servizio fallito"
        return
    fi
    # Here-string e non una pipe, come per la dashboard sopra: con pipefail un
    # grep che si ferma al primo match puo' rendere falsa la pipeline.
    riga="$(grep -m1 '^KINDLING_CODE_VERSION=' <<<"$ambiente" || true)"
    if [ -z "$riga" ]; then
        echo "assente"
    elif [ "$riga" = "KINDLING_CODE_VERSION=" ]; then
        echo "vuota"
    else
        echo "ok ${riga#KINDLING_CODE_VERSION=}"
    fi
}

# api e dashboard: questo script li ha appena ricreati, quindi DEVONO eseguire
# il commit del deploy. Nessuno scostamento ha una lettura innocente: o il
# build-arg non e' arrivato all'immagine, o il container non e' stato
# ricreato — le due cose successe insieme il 17/09/2026 senza che niente lo
# dicesse. Anche "non verificabile" ferma: subito dopo `up`, un container api o
# dashboard che non c'e' e' gia' un deploy fallito, non un dubbio.
versione_rotta=0

echo ""
echo "--- api e dashboard eseguono il commit del deploy ($KINDLING_CODE_VERSION) ---"
for servizio in api dashboard; do
    stato="$(code_version_del_container "$servizio")"
    case "$stato" in
        "ok $KINDLING_CODE_VERSION")
            echo "$servizio: $KINDLING_CODE_VERSION (ok)" ;;
        "ok "*)
            log "ABORT $servizio esegue ${stato#ok } invece di $KINDLING_CODE_VERSION: container non ricreato o immagine non ricostruita"
            versione_rotta=1 ;;
        vuota)
            log "ABORT $servizio ha KINDLING_CODE_VERSION vuota: il build-arg non e' arrivato all'immagine (docker-compose.yml, $servizio.build.args)"
            versione_rotta=1 ;;
        assente)
            log "ABORT $servizio non ha KINDLING_CODE_VERSION: immagine costruita senza l'ARG del Dockerfile"
            versione_rotta=1 ;;
        *)
            log "ABORT versione di $servizio ${stato}"
            versione_rotta=1 ;;
    esac
done

# bot: questo script NON lo ricrea, di proposito (passo 5: non si stacca il
# gateway per un deploy che non lo riguarda). Uno scostamento dal commit del
# deploy e' quindi normale, e un controllo di uguaglianza sarebbe rosso a ogni
# deploy — un controllo sempre rosso si impara a ignorare. Si ferma solo se il
# codice che il bot esegue e' DIVERSO da quello del repository: se fra la sua
# versione e HEAD sono cambiati bot/ o requirements.txt.
#
# bot/ perche' e' il suo codice (bot/ non importa niente da api/, job/ o
# dashboard/); requirements.txt perche' un container vecchio ha anche le
# dipendenze vecchie, e un aggiornamento di discord.py non ricreato e' codice
# diverso da quello del repository quanto una modifica a bot/. NON il
# Dockerfile: quasi ogni sua modifica sono commenti o COPY di altre componenti,
# e un rosso a ogni ritocco di commento e' di nuovo un rosso da ignorare — un
# cambio vero di CMD o di immagine base va ricordato a mano.
#
# Errore e non avviso: un avviso in fondo a questa schermata e' il posto
# esatto in cui non viene letto (vedi il perimetro di kindling_api). Tutto il
# resto del deploy e' gia' fatto quando si arriva qui; il codice di uscita dice
# solo che c'e' ancora un passo, e quale.
bot_da_ricreare=0

echo ""
echo "--- il bot esegue il codice del repository ---"
stato="$(code_version_del_container bot)"
case "$stato" in
    "ok "*)
        versione_bot="${stato#ok }"
        if [ "$versione_bot" = "$KINDLING_CODE_VERSION" ]; then
            echo "bot: $versione_bot (ok, stesso commit del deploy)"
        elif ! "$GIT_BIN" -C "$PROJECT_DIR" cat-file -e "${versione_bot}^{commit}" 2>/dev/null; then
            # Storia riscritta, clone superficiale: non verificabile vuol dire
            # non verificato, non "presumibilmente uguale".
            log "ABORT bot esegue $versione_bot, commit assente dal repository: non verificabile"
            bot_da_ricreare=1
        else
            set +e
            "$GIT_BIN" -C "$PROJECT_DIR" diff --quiet "$versione_bot" HEAD -- bot/ requirements.txt
            diff_esito=$?
            set -e
            case "$diff_esito" in
                0)
                    echo "bot: $versione_bot, bot/ e requirements.txt invariati fino a HEAD (ok: container vecchio, codice uguale)" ;;
                1)
                    log "ABORT bot esegue $versione_bot, e da li' a HEAD bot/ o requirements.txt sono cambiati"
                    bot_da_ricreare=1 ;;
                *)
                    log "ABORT git diff fallito (uscita $diff_esito): codice del bot non verificabile"
                    bot_da_ricreare=1 ;;
            esac
        fi ;;
    vuota)
        log "ABORT bot ha KINDLING_CODE_VERSION vuota: il container precede il meccanismo, va ricreato"
        bot_da_ricreare=1 ;;
    assente)
        log "ABORT bot non ha KINDLING_CODE_VERSION: il container precede il meccanismo, va ricreato"
        bot_da_ricreare=1 ;;
    *)
        log "ABORT versione del bot ${stato}"
        bot_da_ricreare=1 ;;
esac
if [ "$bot_da_ricreare" -ne 0 ]; then
    echo "Il bot non esegue (o non si sa se esegue) il codice del repository." >&2
    echo "Ricrearlo, sapendo che il gateway Discord cade per qualche secondo:" >&2
    echo "  docker compose up -d --force-recreate bot" >&2
fi

if [ "$perimetro_rotto" -ne 0 ]; then
    log "ABORT perimetro di kindling_api compromesso, vedi sopra"
    exit "$PERIMETER_BROKEN_EXIT"
fi

if [ "$dashboard_rotta" -ne 0 ]; then
    log "ABORT variabili di database nella dashboard, vedi sopra"
    exit "$DASHBOARD_DB_VARS_EXIT"
fi

if [ "$dashboard_non_verificata" -ne 0 ]; then
    log "ABORT variabili di database della dashboard non verificate, vedi sopra"
    exit "$DASHBOARD_UNVERIFIED_EXIT"
fi

if [ "$caddy_volume_rotto" -ne 0 ]; then
    log "ABORT certificati di caddy non persistenti, vedi sopra"
    exit "$CADDY_VOLUME_EXIT"
fi

if [ "$caddy_config_stantia" -ne 0 ]; then
    log "ABORT caddy non esegue il Caddyfile corrente, vedi sopra"
    exit "$CADDY_CONFIG_STALE_EXIT"
fi

if [ "$versione_rotta" -ne 0 ]; then
    log "ABORT api o dashboard non eseguono il commit del deploy, vedi sopra"
    exit "$CODE_VERSION_MISMATCH_EXIT"
fi

# Ultimo, dopo tutti gli altri: e' l'unico esito che non dice "il deploy e'
# sbagliato" ma "manca ancora un passo a mano".
if [ "$bot_da_ricreare" -ne 0 ]; then
    log "ABORT il bot va ricreato, vedi sopra"
    exit "$BOT_CODE_STALE_EXIT"
fi

log "END   verifiche"
log "=== fine (ok) ==="
