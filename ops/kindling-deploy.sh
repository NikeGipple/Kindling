#!/usr/bin/env bash
#
# Deploy di una modifica gia' pushata su GitHub: pull, controllo migration,
# build, riavvio del solo servizio che serve, verifiche.
#
# Stesso argomento di ops/kindling-weekly.sh, e per lo stesso motivo: una
# procedura di sei passi con un ordine che conta, un comando controintuitivo
# (`up -d api` e non `up -d`) e un valore calcolato (l'hash del commit) non si
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
# mancanti, uno fermato perche' il ledger stesso manca, e uno fallito per un
# errore imprevisto sono tre situazioni diverse, e chi legge l'esito da fuori
# (una shell interattiva, o in futuro un monitoraggio) deve poterle
# distinguere senza andare a leggere il log.
MIGRATIONS_MISSING_EXIT=10
MIGRATIONS_NO_LEDGER_EXIT=11

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

    local esito
    local stato
    set +e
    esito="$("$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" \
        exec -T postgres psql -U kindling -d kindling \
        -tAc 'SELECT filename FROM schema_migrations ORDER BY filename' 2>&1)"
    stato=$?
    set -e

    if [ "$stato" -ne 0 ]; then
        # "la tabella non esiste" e "qualcos'altro e' andato storto" (postgres
        # non ancora su, credenziali sbagliate) non sono lo stesso caso: solo
        # il primo ha un rimedio noto (applicare 0012), il secondo va guardato
        # a mano. Confuderli darebbe l'istruzione sbagliata.
        if printf '%s' "$esito" | grep -q 'schema_migrations" does not exist'; then
            log "ABORT nessun ledger schema_migrations: applicare prima 0012"
            echo "Il ledger delle migration (schema_migrations) non esiste ancora." >&2
            echo "Applicare 0012 prima di tutto il resto:" >&2
            echo "  docker compose exec postgres psql -U kindling -d kindling \\" >&2
            echo "    -f /docker-entrypoint-initdb.d/0012_schema_migrations.sql" >&2
            exit "$MIGRATIONS_NO_LEDGER_EXIT"
        fi
        log "ABORT verifica migration fallita: $esito"
        exit 1
    fi

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

# --- passo 5: riavviare solo il servizio che serve --------------------------
#
# `up -d api`, MAI `up -d` nudo. `bot` e `api` sono immagini distinte (non
# condivisa: `docker images` le elenca separate), ma nessuna delle due ha un
# `profiles`, quindi un `up -d` senza argomenti ricrea ENTRAMBI i container
# corrispondenti alle immagini appena costruite — non solo quello che serve a
# questo deploy. Ricreare `bot` fa cadere il gateway Discord per una modifica
# che non lo riguarda. `job` non ha bisogno di un `up`: non e' un servizio
# sempre acceso, prende l'immagine appena costruita al prossimo `run`.
log "START up-api"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" up -d api
log "END   up-api"

# --- passo 6: verifiche ------------------------------------------------
#
# Stampate, non usate per decidere l'esito dello script: sono la stessa
# checklist del runbook (sezione "Avvio e verifica"), qui automatizzata perche'
# rileggerla a mano dopo ogni deploy e' esattamente il tipo di passo che si
# salta quando si ha fretta.
log "START verifiche"

echo ""
echo "--- date delle immagini (devono essere ravvicinate) ---"
"$DOCKER_BIN" images --format '{{.Repository}}	{{.CreatedAt}}' | grep kindling || true

echo ""
echo "--- KINDLING_CODE_VERSION dentro il container job ---"
# Verifica diretta del punto di oggi: se questo stampa vuoto invece
# dell'hash appena calcolato, il build-arg non e' arrivato all'immagine.
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" run --rm -T job \
    printenv KINDLING_CODE_VERSION || echo "(vuoto o comando fallito)"

echo ""
echo "--- health dell'API ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T api python -c \
    "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())" \
    || echo "(fallito)"

echo ""
echo "--- perimetro del ruolo kindling_api: deve FALLIRE ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM graph_edges" \
    && echo "ATTENZIONE: kindling_api legge graph_edges, non dovrebbe" \
    || echo "(fallito come atteso: permission denied)"

echo ""
echo "--- perimetro del ruolo kindling_api: deve RIUSCIRE ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T postgres psql \
    -U kindling_api -d kindling -c "SELECT count(*) FROM metric_runs" \
    || echo "ATTENZIONE: kindling_api non legge metric_runs, dovrebbe"

echo ""
echo "--- API_DATABASE_URL usa davvero kindling_api ---"
"$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" exec -T api sh -c \
    'echo "$API_DATABASE_URL" | sed "s#:[^:@]*@#:***@#"'

log "END   verifiche"
log "=== fine (ok) ==="
