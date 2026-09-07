#!/usr/bin/env bash
#
# Esecuzione settimanale del job Kindling: snapshot del grafo, poi metriche.
#
# Lanciato da cron il lunedi' mattina presto in UTC (vedi ops/kindling.cron), ma
# scritto per essere lanciato anche a mano: e' cosi' che verra' debuggato, ed e'
# un'operazione sicura — rieseguirlo nella stessa settimana RISCRIVE lo snapshot
# invece di duplicarlo, e lo stesso vale per le metriche.
#
# "Nella stessa settimana" e non "nello stesso istante": il job ancora as_of al
# lunedi' 00:00 UTC della settimana ISO (modello-grafo.md 5.1), ed e' questo che
# fa ripetere la chiave graph_snapshots (guild_id, as_of, window_start,
# window_end) e rende raggiungibile l'ON CONFLICT che riscrive. Finche' as_of
# veniva preso dall'orologio, questa intestazione era falsa: la chiave non si
# ripeteva mai e ogni lancio a mano lasciava una riga in piu'.
#
#     <repo>/ops/kindling-weekly.sh
#
# Perche' uno script e non una riga di crontab: una riga di crontab con flock,
# nice, due comandi concatenati e una redirezione non si legge, non si prova a
# mano e non si commenta. Qui invece ogni scelta ha il suo perche' accanto.
#
# Nessuna credenziale qui dentro: DATABASE_URL e le altre variabili vivono nel
# .env della droplet, che docker compose legge da solo (env_file) e che non e'
# in git.

set -euo pipefail

# Questo script non ha niente da leggere da stdin, mai. Una riga sola in testa
# invece di una redirezione ripetuta su ogni invocazione: copre tutti i comandi,
# compresi quelli che verranno aggiunti dopo.
#
# Serve perche' `-T` da solo non basta: toglie il TTY ma `docker compose run`
# tiene comunque stdin attaccato, e un processo in background che prova a
# leggere dal terminale viene fermato dal kernel con SIGTTIN ([1]+ Stopped). Un
# processo fermo continua a TENERE IL LOCK, quindi l'esecuzione successiva salta
# con una riga SKIP che non spiega niente.
#
# Ridimensionamento, per chi rileggera' questa riga: **sotto cron non puo'
# accadere** — cron non assegna un terminale di controllo, quindi SIGTTIN non e'
# possibile. Il difetto si manifesta solo lanciando lo script con `&` da una
# shell interattiva, ed e' li' che ha reso illeggibile il test del lock. Va
# corretto per quello, non perche' il cron di lunedi' fosse a rischio.
exec 0< /dev/null

# --- percorsi assoluti -----------------------------------------------------
#
# L'ambiente di cron e' minimale: niente PATH dell'utente, niente shell di
# login. `docker` sta in /usr/bin su una installazione standard del repository
# ufficiale Docker, ma non e' detto sia nel PATH di cron — quindi si passa dal
# percorso completo invece di sperare.
#
# La directory del progetto lo script se la ricava da se', risalendo da dove il
# file si trova: funziona ovunque il repo sia clonato, e sostituire un percorso
# hardcoded con un altro sposterebbe solo il problema. `readlink -f` risolve gli
# eventuali symlink, cosi' vale anche se lo script viene collegato altrove.
#
# L'override resta: e' quello che permette di provare lo script senza
# modificarlo.

PROJECT_DIR="${KINDLING_PROJECT_DIR:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"
DOCKER_BIN="${KINDLING_DOCKER_BIN:-/usr/bin/docker}"
LOG_FILE="${KINDLING_LOG_FILE:-/var/log/kindling/job.log}"
LOCK_FILE="${KINDLING_LOCK_FILE:-/var/lock/kindling-job.lock}"

# --- log -------------------------------------------------------------------

log() {
    printf '%s %s\n' "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$LOG_FILE"
}

mkdir -p "$(dirname "$LOG_FILE")"

# --- lock ------------------------------------------------------------------
#
# Su 1 vCPU condiviso con il bot e Postgres, due esecuzioni sovrapposte sono il
# modo piu' diretto di rubare l'heartbeat del gateway Discord. Se l'esecuzione
# precedente e' ancora in corso, questa non parte e lo dice: -n non aspetta, e
# aspettare sarebbe peggio (si accumulerebbero processi in coda).
#
# Il rilancio con flock avviene una volta sola: KINDLING_LOCKED evita il loop
# se per qualche ragione flock non prendesse il posto del processo.
#
# Niente `exec`: sostituirebbe questo processo con flock, e il ramo che scrive
# la riga SKIP non esisterebbe piu' per essere eseguito — il lock occupato
# diventerebbe un'uscita silenziosa con codice 1, cioe' un fallimento senza
# spiegazione nel log.
#
# --conflict-exit-code rende distinguibile "lock occupato" da "il lavoro e'
# fallito con codice 1": senza, i due casi hanno lo stesso codice di uscita.
#
# E quel codice arriva fino in fondo, NON viene tradotto in 0. Un'esecuzione
# saltata deve restare distinguibile dall'esterno da una riuscita: altrimenti il
# MAILTO di cron, un wrapper o un controllo sull'ultimo codice di uscita
# leggerebbero "tutto bene" su una settimana in cui il job non ha calcolato
# niente. E' lo stesso principio del runbook — un job che non parte produce
# assenza e non errore — e qui l'assenza ha un modo di farsi notare, che non va
# buttato via.
LOCK_BUSY_EXIT=99

if [ -z "${KINDLING_LOCKED:-}" ]; then
    export KINDLING_LOCKED=1
    set +e
    flock --nonblock --conflict-exit-code "$LOCK_BUSY_EXIT" "$LOCK_FILE" "$0" "$@"
    lock_status=$?
    set -e
    if [ "$lock_status" -eq "$LOCK_BUSY_EXIT" ]; then
        log "SKIP  un'altra esecuzione e' gia' in corso (lock $LOCK_FILE)"
    fi
    exit "$lock_status"
fi

# --- esecuzione ------------------------------------------------------------
#
# nice: il job non deve rubare tempo di CPU al bot (architettura.md, sezione
# Hosting). Il container ha gia' mem_limit nel compose; nice copre la CPU.
run_job() {
    local step="$1"
    shift
    log "START $step"
    local started
    started=$(date +%s)
    set +e
    # -T: niente terminale. Senza, `docker compose run` prova a leggere da
    # stdin, e lanciato in background (da cron, o con &) il kernel ferma il
    # processo con SIGTTIN. Un processo fermo continua a TENERE IL LOCK, quindi
    # l'esecuzione successiva salta con una riga SKIP che non spiega niente —
    # ed e' il modo in cui questo difetto si e' presentato in produzione. E' la
    # stessa forma che il runbook usa gia' per `docker compose exec -T` nella
    # procedura delle migration.
    nice -n 10 "$DOCKER_BIN" compose --project-directory "$PROJECT_DIR" \
        run --rm -T job python -m job.main "$@" >>"$LOG_FILE" 2>&1
    local status=$?
    set -e
    log "END   $step exit=$status durata=$(( $(date +%s) - started ))s"
    return $status
}

log "=== esecuzione settimanale ==="

# snapshot PRIMA, metrics SOLO se il primo e' andato a buon fine: metriche
# calcolate su uno snapshot fallito a meta' sono peggio di metriche assenti —
# sono numeri pubblicabili ricavati da un grafo incompleto, e a valle nessuno
# li distingue da quelli buoni.
if ! run_job snapshot snapshot --window-days 7; then
    log "ABORT metrics non eseguito: lo snapshot e' fallito"
    log "=== fine (con errori) ==="
    exit 1
fi

if ! run_job metrics metrics; then
    log "=== fine (con errori) ==="
    exit 1
fi

log "=== fine (ok) ==="
