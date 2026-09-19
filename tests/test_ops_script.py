"""Lo script di esecuzione settimanale: comportamenti che sbagliano in silenzio.

Non e' un test di ``flock`` o di ``docker`` — quelli sono verificati in
produzione — ma di come lo script **usa** il loro esito e di come li invoca, che
e' la parte che era sbagliata:

- un'esecuzione saltata usciva con 0, indistinguibile dall'esterno da una
  riuscita. Qualunque monitoraggio futuro (il MAILTO di cron, un wrapper, un
  controllo sull'ultimo codice di uscita) avrebbe letto "tutto bene" su una
  settimana in cui il job non ha calcolato niente. Vale lo stesso principio del
  runbook: un job che non parte produce assenza e non errore — e qui l'assenza
  aveva un modo di farsi notare, che si stava perdendo;
- ``docker compose run`` teneva stdin attaccato, quindi un'esecuzione lanciata
  in background da una shell interattiva veniva fermata dal kernel con SIGTTIN.
  Un processo fermo continua a tenere il lock, e l'esecuzione successiva saltava
  con una riga SKIP che non spiegava niente.

``flock`` e ``docker`` sono sostituiti da stub sul PATH: qui interessa cosa fa
lo script con quello che gli tornano, non che quei programmi funzionino.

**Cosa questi test NON provano.** SIGTTIN non e' riproducibile qui: servirebbero
un terminale di controllo e un process group in background, che pytest non puo'
allestire in modo portabile. Quello che si verifica e' la proprieta' che lo
evita — nessun comando dello script eredita uno stdin leggibile — passando dei
dati sullo stdin del processo e controllando che non arrivino fino a docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "kindling-weekly.sh"
CRON = REPO / "ops" / "kindling.cron"
LOGROTATE = REPO / "ops" / "kindling.logrotate"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash non disponibile")

# Lo stub di docker registra gli argomenti ricevuti e legge stdin fino a EOF,
# cosi' il test puo' guardare come e' stato invocato e che cosa gli e' arrivato
# invece di fidarsi del sorgente.
DOCKER_STUB = '\n'.join([
    "#!/usr/bin/env bash",
    'printf "%s\\n" "$*" >>"$DOCKER_ARGS_FILE"',
    'cat >>"$DOCKER_STDIN_FILE"',
    "exit 0",
    "",
])

# flock che simula il lock occupato: e' il codice che --conflict-exit-code
# produrrebbe.
FLOCK_BUSY = '#!/usr/bin/env bash\nexit 99\n'

# flock che lascia passare: esegue il comando che gli viene dato, come farebbe
# dopo aver preso il lock.
FLOCK_FREE = '\n'.join([
    "#!/usr/bin/env bash",
    "while [ $# -gt 0 ]; do",
    '    case "$1" in',
    "        --nonblock|-n) shift ;;",
    "        --conflict-exit-code) shift 2 ;;",
    "        *) break ;;",
    "    esac",
    "done",
    "shift  # il file di lock",
    'exec "$@"',
    "",
])


@dataclass(frozen=True)
class Esito:
    status: int
    log: str
    docker_args: str
    docker_stdin: str


def _stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    *,
    flock_body: str = FLOCK_FREE,
    docker_body: str = DOCKER_STUB,
    stdin_data: str = "",
) -> Esito:
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    _stub(binaries / "docker", docker_body)
    _stub(binaries / "flock", flock_body)

    log = tmp_path / "job.log"
    args_file = tmp_path / "docker-args.txt"
    stdin_file = tmp_path / "docker-stdin.txt"
    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        DOCKER_ARGS_FILE=str(args_file),
        DOCKER_STDIN_FILE=str(stdin_file),
        KINDLING_DOCKER_BIN=str(binaries / "docker"),
        KINDLING_LOG_FILE=str(log),
        KINDLING_LOCK_FILE=str(tmp_path / "lock"),
        KINDLING_PROJECT_DIR=str(tmp_path),
    )
    env.pop("KINDLING_LOCKED", None)

    completed = subprocess.run(
        [BASH, str(SCRIPT)],
        env=env,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=120,
    )

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return Esito(
        status=completed.returncode,
        log=_read(log),
        docker_args=_read(args_file),
        docker_stdin=_read(stdin_file),
    )


def test_esecuzione_saltata_esce_con_99_e_non_con_zero(tmp_path):
    esito = _run(tmp_path, flock_body=FLOCK_BUSY)

    assert "SKIP" in esito.log
    # Il punto: dall'esterno una settimana saltata NON deve somigliare a una
    # settimana andata bene.
    assert esito.status == 99, "un'esecuzione saltata deve essere distinguibile"
    assert esito.docker_args == "", "con il lock occupato non si lancia nessun job"


def test_esecuzione_normale_esce_con_zero(tmp_path):
    esito = _run(tmp_path)

    assert esito.status == 0
    assert "=== fine (ok) ===" in esito.log
    assert "SKIP" not in esito.log
    assert esito.docker_args.count("\n") == 2, "snapshot e metrics, in quest'ordine"


def test_nessun_comando_eredita_uno_stdin_leggibile(tmp_path):
    # E' la proprieta' che evita SIGTTIN: `docker compose run` teneva stdin
    # attaccato anche con -T, e un processo in background che prova a leggere
    # dal terminale viene fermato dal kernel. Lo script redirige stdin da
    # /dev/null una volta sola, in testa, quindi nessun comando puo' leggere.
    esito = _run(tmp_path, stdin_data="questo non deve arrivare a docker\n")

    assert esito.status == 0
    assert esito.docker_stdin == "", esito.docker_stdin


def test_docker_compose_run_non_alloca_un_terminale(tmp_path):
    # -T e la redirezione di stdin risolvono problemi diversi e stanno insieme:
    # -T e' la forma corretta per un contesto non interattivo, la redirezione e'
    # cio' che impedisce la lettura. E' la stessa forma che il runbook usa gia'
    # per `docker compose exec -T` nella procedura delle migration.
    esito = _run(tmp_path)

    invocazioni = [line for line in esito.docker_args.splitlines() if line.strip()]
    assert invocazioni, "lo stub deve aver registrato qualcosa"
    for invocazione in invocazioni:
        assert "run --rm -T" in invocazione, invocazione


def test_snapshot_fallito_non_fa_partire_metrics(tmp_path):
    # Fallisce solo sullo snapshot: metrics non deve essere mai invocato.
    docker = '\n'.join([
        "#!/usr/bin/env bash",
        'printf "%s\\n" "$*" >>"$DOCKER_ARGS_FILE"',
        'cat >>"$DOCKER_STDIN_FILE"',
        'case "$*" in *snapshot*) exit 3 ;; esac',
        "exit 0",
        "",
    ])
    esito = _run(tmp_path, docker_body=docker)

    assert esito.status == 1
    assert "ABORT metrics non eseguito" in esito.log
    assert "metrics" not in esito.docker_args


# --- i due file di configurazione che cron e logrotate leggono ---------------
#
# Non sono lo script, ma sbagliano nello stesso modo: in silenzio. /etc/cron.d
# scarta una riga malformata senza dirlo a nessuno, e una stanza di logrotate
# che non nomina un file semplicemente non lo ruota. Qui si legge il file
# committato, che e' esattamente quello che viene copiato sulla droplet.


def _riga_del_job() -> str:
    righe = [
        riga
        for riga in CRON.read_text(encoding="utf-8").splitlines()
        if riga.strip() and not riga.lstrip().startswith("#") and "=" not in riga.split()[0]
    ]
    assert len(righe) == 1, f"attesa una sola riga di job, trovate {righe}"
    return righe[0]


def test_il_cron_non_prova_a_mandare_mail():
    # Sulla droplet non c'e' nessun MTA: senza MAILTO="" l'output di
    # un'esecuzione fallita finisce in una mail che nessuno consegnera' mai, e
    # l'unica traccia resta una riga di /var/log/syslog che nessuno guarda.
    testo = CRON.read_text(encoding="utf-8")
    assert 'MAILTO=""' in testo


def test_l_output_del_cron_finisce_in_journald_e_non_in_un_file():
    # La pipe a logger, non una redirezione: la shell aprirebbe il file PRIMA di
    # eseguire il comando, quindi una cartella mancante non darebbe un log vuoto
    # ma un job che non parte — e con MAILTO="" senza una riga da nessuna parte.
    # La cartella oggi se la ripara lo script (mkdir -p), e quella condizione
    # deve restare dentro lo script.
    riga = _riga_del_job()

    assert "| /usr/bin/logger -t kindling-cron" in riga, riga
    assert "2>&1" in riga, "anche stderr deve passare da logger: " + riga
    assert ">>" not in riga, "nessuna redirezione su file: " + riga
    # Percorso assoluto anche per logger, benche' PATH sia dichiarato nel file:
    # e' la stessa disciplina applicata a docker, e due regole diverse nello
    # stesso file sono un invito a sbagliare quella che conta.
    assert "/usr/bin/logger" in riga, riga


def test_la_riga_del_cron_ha_il_campo_utente_e_il_comando_al_settimo_posto():
    # In /etc/cron.d le colonne sono sei (m h dom mon dow utente) e il comando
    # comincia alla settima. Il controllo del runbook estrae proprio $7: se la
    # forma della riga cambiasse, quel controllo verificherebbe un'altra cosa
    # senza smettere di passare — ed e' gia' successo con $NF, diventato
    # l'argomento di logger quando e' arrivata la pipe.
    campi = _riga_del_job().split()

    assert campi[:5] == ["15", "4", "*", "*", "1"], campi
    assert campi[5] == "root", "manca il campo utente: " + " ".join(campi)
    assert campi[6].endswith("kindling-weekly.sh"), campi[6]
    assert Path(campi[6]).name == SCRIPT.name


def test_logrotate_copre_tutti_i_log_della_cartella():
    # Il glob e non il singolo job.log: un log nuovo che nessuno si ricorda di
    # aggiungere alla stanza e' il modo in cui una rotazione smette di coprire
    # quello che dovrebbe.
    testo = LOGROTATE.read_text(encoding="utf-8")

    assert "/var/log/kindling/*.log {" in testo
    assert "/var/log/kindling/job.log {" not in testo


# --- ops/kindling-deploy.sh --------------------------------------------------
#
# Stesso criterio dello script settimanale: non un test di ``git`` o ``docker``,
# ma di come lo script usa il loro esito. Difetti reali lo hanno reso
# necessario e poi corretto (07/09/2026, CLAUDE.md 7):
#
# - ``docker compose build`` non ricostruiva ``job`` (profiles: ["tools"]),
#   senza nessun errore — il job ha girato con un'immagine di tre giorni prima;
# - non esisteva nessun modo di sapere se le migration in ``migrations/`` erano
#   davvero applicate sul database della droplet, quindi nessuno lo verificava
#   prima di costruire il codice nuovo sopra;
# - il perimetro del ruolo ``kindling_api`` rotto veniva solo stampato
#   (``ATTENZIONE``) senza fermare niente, in fondo a una schermata lunga —
#   il posto esatto in cui un avviso non viene letto.

DEPLOY_SCRIPT = REPO / "ops" / "kindling-deploy.sh"

# git: risponde a `pull` e a `rev-parse --short HEAD` con un hash fisso, cosi'
# i test possono verificare che sia proprio QUELLO ad arrivare al build.
#
# E alle due domande della verifica del bot: `cat-file -e` (il commit che il
# container esegue esiste nel repository? STUB_GIT_COMMIT_MISSING=1 dice di no)
# e `diff --quiet ... -- bot/ ...` (e' cambiato qualcosa da li' a HEAD? l'uscita
# e' STUB_GIT_BOT_DIFF: 0 no, 1 si', altro = git fallito). Le invocazioni di diff
# si registrano in GIT_ARGS_FILE, per verificare COSA viene confrontato.
GIT_STUB = '\n'.join([
    "#!/usr/bin/env bash",
    'case "$*" in',
    '    *pull) echo "Already up to date." ; exit 0 ;;',
    '    *"rev-parse --short HEAD") echo "deadbee" ; exit 0 ;;',
    '    *"cat-file -e"*)',
    '        if [ "${STUB_GIT_COMMIT_MISSING:-}" = "1" ]; then exit 128; fi',
    "        exit 0 ;;",
    '    *"diff --quiet"*)',
    '        printf "%s\\n" "$*" >>"${GIT_ARGS_FILE:-/dev/null}"',
    '        exit "${STUB_GIT_BOT_DIFF:-0}" ;;',
    "esac",
    "exit 1",
    "",
])

# docker: registra ogni invocazione E l'ambiente con cui e' stata chiamata (per
# verificare che KINDLING_CODE_VERSION arrivi fino al build), e si comporta in
# modo diverso a seconda del comando — proprio come dovra' fare il vero
# `docker compose` con `exec`, `build`, `up`, `run`.
#
# Il contenuto del ledger e' parametrico via STUB_SCHEMA_MIGRATIONS: una lista
# di nomi separati da spazio, o la stringa "MISSING_TABLE" per simulare
# l'assenza della tabella. Lo stub risponde a `to_regclass` (la query che lo
# script usa per accorgersene, senza dover leggere un messaggio d'errore) E a
# `SELECT filename FROM schema_migrations`, coerentemente tra loro.
#
# STUB_GRAPH_EDGES_LEAKS e STUB_METRIC_RUNS_DENIED simulano il perimetro del
# ruolo kindling_api rotto nei due versi possibili: legge quello che non
# dovrebbe, o non legge quello che dovrebbe.
#
# KINDLING_CODE_VERSION dei container api, dashboard e bot (letta dallo script
# con `docker inspect`): STUB_API_CODE_VERSION, STUB_DASHBOARD_CODE_VERSION,
# STUB_BOT_CODE_VERSION. Una stringa vuota e' la variabile VUOTA, "<absent>" la
# variabile assente — sono due casi diversi, ed e' il punto. Non impostate
# valgono "sano": api e dashboard al commit del deploy (deadbee), il bot a un
# commit precedente (cafe123), cioe' il caso normale di un container che il
# deploy non ricrea. STUB_BOT_MISSING=1: nessun container bot.
DOCKER_STUB_DEPLOY = '\n'.join([
    "#!/usr/bin/env bash",
    '_versione() { if [ "$1" != "<absent>" ]; then printf "KINDLING_CODE_VERSION=%s\\n" "$1"; fi; }',
    'printf "%s\\n" "$*" >>"$DEPLOY_DOCKER_ARGS_FILE"',
    'printf "KINDLING_CODE_VERSION=%s\\n" "${KINDLING_CODE_VERSION:-<unset>}" >>"$DEPLOY_DOCKER_ENV_FILE"',
    'case "$*" in',
    '    *"to_regclass"*)',
    '        if [ "$STUB_SCHEMA_MIGRATIONS" = "MISSING_TABLE" ]; then',
    '            echo ""',
    "        else",
    '            echo "schema_migrations"',
    "        fi",
    "        exit 0 ;;",
    '    *"schema_migrations ORDER BY filename"*)',
    '        for nome in $STUB_SCHEMA_MIGRATIONS; do printf "%s\\n" "$nome"; done',
    "        exit 0 ;;",
    '    *"graph_edges"*)',
    '        if [ "${STUB_GRAPH_EDGES_LEAKS:-}" = "1" ]; then',
    '            echo "5" ; exit 0',
    "        fi",
    '        echo "ERROR: permission denied" >&2 ; exit 1 ;;',
    '    *"metric_runs"*)',
    '        if [ "${STUB_METRIC_RUNS_DENIED:-}" = "1" ]; then',
    '            echo "ERROR: permission denied" >&2 ; exit 1',
    "        fi",
    '        echo "5" ; exit 0 ;;',
    '    *"images --format"*)',
    '        echo "kindling-bot	2026-09-07T10:00:00Z"',
    '        echo "kindling-api	2026-09-07T10:00:00Z"',
    '        echo "kindling-dashboard	2026-09-07T10:00:00Z"',
    '        echo "kindling-job	2026-09-07T10:00:00Z"',
    "        exit 0 ;;",
    # Container dashboard: STUB_DASHBOARD_MISSING=1 simula un container che non
    # esiste, STUB_DASHBOARD_INSPECT_FAILS=1 un inspect che fallisce, e
    # STUB_DASHBOARD_ENV (righe separate da `;`) l'environment del container.
    '    *"ps -a -q dashboard"*)',
    '        if [ "${STUB_DASHBOARD_MISSING:-}" = "1" ]; then exit 0; fi',
    '        echo "c0ffee" ; exit 0 ;;',
    # Caddy: STUB_CADDY_VALIDATE_FAILS / STUB_CADDY_RELOAD_FAILS simulano un
    # Caddyfile rifiutato; STUB_CADDY_MISSING un container che non c'e';
    # STUB_CADDY_VOLUMES i volumi con l'etichetta caddy_data, e STUB_CADDY_MOUNT
    # cosa e' montato su /data ("TIPO NOME", come lo stampa lo script). Non
    # impostate valgono "sano": i test che costruiscono l'ambiente da se' non
    # devono conoscere Caddy per passare.
    '    *"caddy validate"*)',
    '        if [ "${STUB_CADDY_VALIDATE_FAILS:-}" = "1" ]; then',
    '            echo "Error: adapting config" >&2 ; exit 1',
    "        fi",
    "        exit 0 ;;",
    # Configurazione di Caddy (passo 5-ter): la meta' in esercizio dall'admin
    # API (wget dentro il container), quella attesa da `caddy adapt` in un
    # container nuovo. STUB_CADDY_RUNNING_FILE / STUB_CADDY_ADAPTED_FILE: file
    # con il JSON da restituire; non impostati, entrambe {"apps":{}}.
    # STUB_CADDY_ADMIN_FAILS / STUB_CADDY_ADAPT_FAILS: il comando fallisce.
    '    *"localhost:2019/config/"*)',
    '        if [ "${STUB_CADDY_ADMIN_FAILS:-}" = "1" ]; then exit 1; fi',
    '        if [ -n "${STUB_CADDY_RUNNING_FILE:-}" ]; then cat "$STUB_CADDY_RUNNING_FILE"; exit 0; fi',
    "        printf '%s\\n' '{\"apps\":{}}' ; exit 0 ;;",
    '    *"caddy adapt"*)',
    '        if [ "${STUB_CADDY_ADAPT_FAILS:-}" = "1" ]; then echo "Error: adapting" >&2; exit 1; fi',
    '        if [ -n "${STUB_CADDY_ADAPTED_FILE:-}" ]; then cat "$STUB_CADDY_ADAPTED_FILE"; exit 0; fi',
    "        printf '%s\\n' '{\"apps\":{}}' ; exit 0 ;;",
    '    *"caddy reload"*)',
    '        if [ "${STUB_CADDY_RELOAD_FAILS:-}" = "1" ]; then',
    '            echo "Error: loading new config" >&2 ; exit 1',
    "        fi",
    "        exit 0 ;;",
    '    *"ps -a -q caddy"*)',
    '        if [ "${STUB_CADDY_MISSING:-}" = "1" ]; then exit 0; fi',
    '        echo "cadd1e" ; exit 0 ;;',
    '    *"label=com.docker.compose.volume=caddy_data"*)',
    '        for v in ${STUB_CADDY_VOLUMES-kindling_caddy_data}; do printf "%s\\n" "$v"; done ; exit 0 ;;',
    '    *".Mounts"*)',
    '        printf "%s\\n" "${STUB_CADDY_MOUNT-volume kindling_caddy_data}" ; exit 0 ;;',
    '    *"ps -a -q api"*)',
    '        echo "a91a91" ; exit 0 ;;',
    '    *"ps -a -q bot"*)',
    '        if [ "${STUB_BOT_MISSING:-}" = "1" ]; then exit 0; fi',
    '        echo "b07b07" ; exit 0 ;;',
    '    "inspect "*" a91a91")',
    '        echo "PATH=/usr/local/bin:/usr/bin"',
    '        _versione "${STUB_API_CODE_VERSION-deadbee}" ; exit 0 ;;',
    '    "inspect "*" b07b07")',
    '        echo "PATH=/usr/local/bin:/usr/bin"',
    '        _versione "${STUB_BOT_CODE_VERSION-cafe123}" ; exit 0 ;;',
    '    "inspect "*)',
    '        if [ "${STUB_DASHBOARD_INSPECT_FAILS:-}" = "1" ]; then',
    '            echo "Error: no such object" >&2 ; exit 1',
    "        fi",
    '        printf "%s\\n" "$STUB_DASHBOARD_ENV" | tr ";" "\\n"',
    '        _versione "${STUB_DASHBOARD_CODE_VERSION-deadbee}" ; exit 0 ;;',
    "esac",
    "exit 0",
    "",
])


# La stessa configurazione come la restituisce l'admin API (Go: chiavi in
# ordine alfabetico, compatta, numeri nella forma piu' corta) e come la stampa
# `caddy adapt` (ordine del Caddyfile, spazi, intero). Sono UGUALI: un
# confronto fra stringhe le direbbe diverse a ogni deploy.
CADDY_IN_ESERCIZIO = (
    '{"apps":{"http":{"servers":{"srv0":{"idle_timeout":1e+09,'
    '"listen":[":443"],"protocols":["h1","h2"]}}}}}'
)
CADDY_ATTESA = """{
  "apps": {"http": {"servers": {"srv0": {
    "listen": [":443"], "protocols": ["h1", "h2"], "idle_timeout": 1000000000
  }}}}
}"""


@dataclass(frozen=True)
class EsitoDeploy:
    status: int
    log: str
    docker_args: str
    docker_env: str
    git_args: str = ""
    stderr: str = ""


def _run_deploy(
    tmp_path: Path,
    *,
    migrations: tuple[str, ...] = ("0001_a.sql", "0002_b.sql"),
    schema_migrations: str = "0001_a.sql 0002_b.sql",
    git_body: str = GIT_STUB,
    graph_edges_leaks: bool = False,
    metric_runs_denied: bool = False,
    dashboard_env: str = "PATH=/usr/local/bin:/usr/bin;KINDLING_API_BASE_URL=http://api:8000",
    dashboard_missing: bool = False,
    dashboard_inspect_fails: bool = False,
    caddy_validate_fails: bool = False,
    caddy_reload_fails: bool = False,
    caddy_missing: bool = False,
    caddy_volumes: str = "kindling_caddy_data",
    caddy_mount: str = "volume kindling_caddy_data",
    api_code_version: str = "deadbee",
    dashboard_code_version: str = "deadbee",
    bot_code_version: str = "cafe123",
    bot_missing: bool = False,
    git_commit_missing: bool = False,
    git_bot_diff: int = 0,
    caddy_running: str = CADDY_IN_ESERCIZIO,
    caddy_adapted: str = CADDY_ATTESA,
    caddy_admin_fails: bool = False,
    caddy_adapt_fails: bool = False,
) -> EsitoDeploy:
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    _stub(binaries / "docker", DOCKER_STUB_DEPLOY)
    _stub(binaries / "git", git_body)

    project_dir = tmp_path / "progetto"
    (project_dir / "migrations").mkdir(parents=True, exist_ok=True)
    running_file = tmp_path / "caddy-running.json"
    adapted_file = tmp_path / "caddy-adapted.json"
    running_file.write_text(caddy_running, encoding="utf-8")
    adapted_file.write_text(caddy_adapted, encoding="utf-8")
    for nome in migrations:
        (project_dir / "migrations" / nome).write_text("-- fake\n", encoding="utf-8")

    log = tmp_path / "deploy.log"
    args_file = tmp_path / "docker-args.txt"
    env_file = tmp_path / "docker-env.txt"
    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        DEPLOY_DOCKER_ARGS_FILE=str(args_file),
        DEPLOY_DOCKER_ENV_FILE=str(env_file),
        STUB_SCHEMA_MIGRATIONS=schema_migrations,
        STUB_GRAPH_EDGES_LEAKS="1" if graph_edges_leaks else "0",
        STUB_METRIC_RUNS_DENIED="1" if metric_runs_denied else "0",
        STUB_DASHBOARD_ENV=dashboard_env,
        STUB_DASHBOARD_MISSING="1" if dashboard_missing else "0",
        STUB_DASHBOARD_INSPECT_FAILS="1" if dashboard_inspect_fails else "0",
        STUB_CADDY_VALIDATE_FAILS="1" if caddy_validate_fails else "0",
        STUB_CADDY_RELOAD_FAILS="1" if caddy_reload_fails else "0",
        STUB_CADDY_MISSING="1" if caddy_missing else "0",
        STUB_CADDY_VOLUMES=caddy_volumes,
        STUB_CADDY_MOUNT=caddy_mount,
        STUB_API_CODE_VERSION=api_code_version,
        STUB_DASHBOARD_CODE_VERSION=dashboard_code_version,
        STUB_BOT_CODE_VERSION=bot_code_version,
        STUB_BOT_MISSING="1" if bot_missing else "0",
        STUB_GIT_COMMIT_MISSING="1" if git_commit_missing else "0",
        STUB_GIT_BOT_DIFF=str(git_bot_diff),
        GIT_ARGS_FILE=str(tmp_path / "git-args.txt"),
        STUB_CADDY_RUNNING_FILE=str(running_file),
        STUB_CADDY_ADAPTED_FILE=str(adapted_file),
        STUB_CADDY_ADMIN_FAILS="1" if caddy_admin_fails else "0",
        STUB_CADDY_ADAPT_FAILS="1" if caddy_adapt_fails else "0",
        KINDLING_PYTHON_BIN=sys.executable,
        KINDLING_CADDY_RELOAD_PAUSE="0",
        KINDLING_DOCKER_BIN=str(binaries / "docker"),
        KINDLING_GIT_BIN=str(binaries / "git"),
        KINDLING_PROJECT_DIR=str(project_dir),
        KINDLING_DEPLOY_LOG_FILE=str(log),
    )

    completed = subprocess.run(
        [BASH, str(DEPLOY_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return EsitoDeploy(
        status=completed.returncode,
        log=_read(log),
        docker_args=_read(args_file),
        docker_env=_read(env_file),
        git_args=_read(tmp_path / "git-args.txt"),
        stderr=completed.stderr,
    )


def test_procede_se_tutte_le_migration_sono_applicate(tmp_path):
    esito = _run_deploy(tmp_path)

    assert esito.status == 0
    assert "=== fine (ok) ===" in esito.log
    assert "up -d api dashboard" in esito.docker_args


def test_si_ferma_se_manca_una_migration_e_non_costruisce_niente(tmp_path):
    # 0002_b.sql esiste come file ma non e' nel ledger: e' il caso reale,
    # verificato in produzione, di una migration scritta ma non ancora
    # applicata sulla droplet.
    esito = _run_deploy(tmp_path, schema_migrations="0001_a.sql")

    assert esito.status == 10, "migration mancanti: codice di uscita distinto"
    assert "0002_b.sql" in esito.log
    assert "build" not in esito.docker_args, "non deve costruire nulla se manca una migration"
    assert "up -d" not in esito.docker_args


def test_si_ferma_con_codice_diverso_se_manca_il_ledger_stesso(tmp_path):
    # Non lo stesso caso di "una migration manca": qui lo script non sa NIENTE
    # sullo stato del database, e deve dirlo invece di scambiarlo per "tutto
    # applicato" (il ledger vuoto sarebbe indistinguibile da "nessuna riga
    # trovata" se non si controllasse l'errore specifico).
    esito = _run_deploy(tmp_path, schema_migrations="MISSING_TABLE")

    assert esito.status == 11
    assert esito.status != 10, "manca il ledger, non solo una migration: sono due casi diversi"
    assert "0012" in esito.log
    assert "build" not in esito.docker_args


def test_build_normale_e_build_del_profilo_tools_vanno_sempre_insieme(tmp_path):
    # Il difetto del 07/09/2026: un `docker compose build` da solo non
    # ricostruisce `job`. Da qui in avanti i due comandi vanno sempre insieme,
    # in quest'ordine.
    esito = _run_deploy(tmp_path)

    invocazioni = [riga for riga in esito.docker_args.splitlines() if riga.strip()]
    build_semplice = [i for i, riga in enumerate(invocazioni) if riga.endswith(" build")]
    build_job = [
        i for i, riga in enumerate(invocazioni) if "--profile tools build job" in riga
    ]
    assert len(build_semplice) == 1, invocazioni
    assert len(build_job) == 1, invocazioni
    assert build_semplice[0] < build_job[0], "build semplice deve venire prima"


def test_non_lancia_mai_up_d_nudo(tmp_path):
    # Ricreerebbe anche `bot`, facendo cadere il gateway Discord per una
    # modifica che non lo riguarda. Solo `up -d api dashboard caddy`.
    esito = _run_deploy(tmp_path)

    invocazioni_up = [
        riga for riga in esito.docker_args.splitlines() if " up " in f" {riga} "
    ]
    assert invocazioni_up, "lo script deve invocare up almeno una volta"
    for riga in invocazioni_up:
        assert riga.endswith("up -d api dashboard caddy"), riga


# --- caddy (dashboard-fase2.md 8-bis) ------------------------------------------


def _indice(esito: EsitoDeploy, frammento: str) -> int:
    righe = esito.docker_args.splitlines()
    trovate = [i for i, riga in enumerate(righe) if frammento in riga]
    assert len(trovate) == 1, (frammento, righe)
    return trovate[0]


def test_caddyfile_validato_prima_di_up_e_ricaricato_dopo(tmp_path):
    esito = _run_deploy(tmp_path)

    assert esito.status == 0, esito.log
    up = _indice(esito, "up -d api dashboard caddy")
    assert _indice(esito, "caddy validate") < up
    # Il reload e' cio' che applica un Caddyfile cambiato: `up -d` non ricrea
    # il container per un bind mount modificato ed esce 0 con la config vecchia.
    assert _indice(esito, "caddy reload") > up
    # Il container di validazione non pubblica porte e non avvia la dashboard.
    validate = esito.docker_args.splitlines()[_indice(esito, "caddy validate")]
    assert "run --rm --no-deps" in validate
    assert "--service-ports" not in validate


def test_caddyfile_non_valido_ferma_il_deploy_prima_di_up(tmp_path):
    esito = _run_deploy(tmp_path, caddy_validate_fails=True)

    assert esito.status == 15
    assert "Caddyfile non valido" in esito.log
    assert "up -d" not in esito.docker_args


def test_reload_fallito_ferma_il_deploy_e_non_passa_per_riuscito(tmp_path):
    esito = _run_deploy(tmp_path, caddy_reload_fails=True)

    assert esito.status == 15
    assert "caddy reload fallito" in esito.log
    assert "=== fine (ok) ===" not in esito.log
    # Qualche tentativo prima di arrendersi, non uno solo.
    assert esito.docker_args.count("caddy reload") > 1


@pytest.mark.parametrize(
    ("argomenti", "atteso"),
    [
        ({"caddy_mount": "volume 3f9a0c1d2e"}, "non e' il volume caddy_data"),  # anonimo
        ({"caddy_mount": ""}, "non e' il volume caddy_data"),  # niente montato
        ({"caddy_mount": "bind "}, "non e' il volume caddy_data"),
        ({"caddy_volumes": ""}, "nessun volume con etichetta"),
        ({"caddy_missing": True}, "container caddy non trovato"),
    ],
    ids=["volume-anonimo", "niente-su-data", "bind", "volume-assente", "container-assente"],
)
def test_certificati_non_persistenti_fermano_il_deploy(tmp_path, argomenti, atteso):
    esito = _run_deploy(tmp_path, **argomenti)

    assert esito.status == 16, esito.log
    assert atteso in esito.log
    assert "=== fine (ok) ===" not in esito.log


def test_il_volume_si_riconosce_per_etichetta_con_qualunque_prefisso(tmp_path):
    # Il prefisso e' il nome del progetto Compose: non si indovina. Qualunque
    # volume con l'etichetta giusta, purche' sia proprio quello montato su /data.
    esito = _run_deploy(
        tmp_path, caddy_volumes="altro_caddy_data project_caddy_data",
        caddy_mount="volume project_caddy_data",
    )

    assert esito.status == 0, esito.log


# --- la dashboard non ha variabili di database (dashboard.md 1, punto 2) -----


def test_dashboard_con_database_url_ferma_il_deploy(tmp_path):
    esito = _run_deploy(
        tmp_path,
        dashboard_env="PATH=/usr/bin;KINDLING_API_BASE_URL=http://api:8000;DATABASE_URL=postgresql://kindling:segreta@postgres:5432/kindling",
    )

    assert esito.status == 13
    assert "DATABASE_URL" in esito.log
    assert "=== fine (ok) ===" not in esito.log
    # Il valore non si stampa mai: il nome basta a dire cosa togliere.
    assert "segreta" not in esito.log


def test_dashboard_con_api_database_url_vuota_ferma_il_deploy(tmp_path):
    # Anche vuota: una riga nell'environment e' gia' una variabile ricevuta.
    esito = _run_deploy(tmp_path, dashboard_env="PATH=/usr/bin;API_DATABASE_URL=")

    assert esito.status == 13
    assert "API_DATABASE_URL" in esito.log


def test_nome_simile_non_e_una_variabile_di_database(tmp_path):
    # Il confronto e' sul nome esatto a inizio riga: KINDLING_API_BASE_URL
    # contiene "API_" e "URL" e non deve far scattare niente.
    esito = _run_deploy(tmp_path, dashboard_env="MY_DATABASE_URL_NOTE=x;KINDLING_API_BASE_URL=http://api:8000")

    assert esito.status == 0, esito.log


def test_dashboard_assente_non_passa_per_verificata(tmp_path):
    esito = _run_deploy(tmp_path, dashboard_missing=True)

    assert esito.status == 14
    assert "non verificabili" in esito.log
    assert "=== fine (ok) ===" not in esito.log


def test_inspect_fallito_non_passa_per_verificato(tmp_path):
    esito = _run_deploy(tmp_path, dashboard_inspect_fails=True)

    assert esito.status == 14


def test_perimetro_e_dashboard_rotti_si_vedono_entrambi(tmp_path):
    # Come per i due controlli del perimetro: chi legge vede tutti i problemi
    # prima che lo script si fermi. Il codice resta quello del perimetro.
    esito = _run_deploy(tmp_path, graph_edges_leaks=True, dashboard_env="DATABASE_URL=x")

    assert esito.status == 12
    assert "perimetro compromesso" in esito.log
    assert "la dashboard ha la variabile DATABASE_URL" in esito.log


def test_kindling_code_version_arriva_al_build_dal_git_pullato_non_da_env(tmp_path):
    # Il punto di tutta la parte A: la versione non si legge da .env (lo
    # script non lo tocca nemmeno), si calcola da git rev-parse subito dopo il
    # pull e viaggia nell'ambiente della shell fino a docker compose build.
    esito = _run_deploy(tmp_path)

    righe_env = [r for r in esito.docker_env.splitlines() if r.strip()]
    assert righe_env, "il docker stub deve aver registrato qualcosa"
    for riga in righe_env:
        assert riga == "KINDLING_CODE_VERSION=deadbee", riga


def test_si_ferma_se_kindling_api_legge_graph_edges(tmp_path):
    # Il primo invariante non negoziabile del progetto: se il ruolo di sola
    # lettura legge una tabella interna, non e' un dettaglio da segnalare a
    # margine, e' un deploy da considerare fallito.
    esito = _run_deploy(tmp_path, graph_edges_leaks=True)

    assert esito.status == 12
    assert "perimetro compromesso" in esito.log
    assert "ATTENZIONE" in esito.log or "graph_edges" in esito.log


def test_si_ferma_se_kindling_api_non_legge_metric_runs(tmp_path):
    # Il verso opposto dello stesso invariante: se il ruolo non legge nemmeno
    # quello che DEVE poter leggere, l'API in produzione e' rotta, non solo il
    # perimetro.
    esito = _run_deploy(tmp_path, metric_runs_denied=True)

    assert esito.status == 12
    assert "perimetro compromesso" in esito.log


def test_il_controllo_del_perimetro_mostra_entrambi_gli_esiti_prima_di_fermarsi(tmp_path):
    # Non deve uscire al primo problema: se sono rotti entrambi i controlli,
    # chi legge deve vedere tutti e due, non solo il primo che ha fatto fallire
    # lo script.
    esito = _run_deploy(tmp_path, graph_edges_leaks=True, metric_runs_denied=True)

    assert esito.status == 12
    assert esito.log.count("perimetro compromesso") == 2, esito.log


def test_le_verifiche_informative_non_fermano_lo_script(tmp_path):
    # Il resto del passo 6 (date immagini, health, code_version,
    # API_DATABASE_URL) resta informativo: solo il perimetro del ruolo decide
    # l'esito. Qui nessuna delle due condizioni di rottura e' simulata, quindi
    # lo script deve arrivare in fondo.
    esito = _run_deploy(tmp_path)

    assert esito.status == 0
    assert "=== fine (ok) ===" in esito.log


def test_pull_fallito_non_chiama_nessun_docker(tmp_path):
    git_che_fallisce = '#!/usr/bin/env bash\nexit 17\n'
    esito = _run_deploy(tmp_path, git_body=git_che_fallisce)

    assert esito.status == 17
    assert esito.docker_args == "", "senza codice aggiornato non si costruisce niente"


def test_nessun_comando_eredita_uno_stdin_leggibile_nel_deploy(tmp_path):
    # Stessa proprieta' di ops/kindling-weekly.sh, stesso motivo: un deploy
    # lanciato da una sessione che si stacca non deve lasciare nessun comando
    # in attesa di leggere da un terminale che non c'e' piu'.
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    _stub(binaries / "docker", DOCKER_STUB_DEPLOY)
    _stub(binaries / "git", GIT_STUB)

    project_dir = tmp_path / "progetto"
    (project_dir / "migrations").mkdir(parents=True)
    (project_dir / "migrations" / "0001_a.sql").write_text("-- fake\n", encoding="utf-8")

    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        DEPLOY_DOCKER_ARGS_FILE=str(tmp_path / "docker-args.txt"),
        DEPLOY_DOCKER_ENV_FILE=str(tmp_path / "docker-env.txt"),
        STUB_SCHEMA_MIGRATIONS="0001_a.sql",
        KINDLING_PYTHON_BIN=sys.executable,
        KINDLING_DOCKER_BIN=str(binaries / "docker"),
        KINDLING_GIT_BIN=str(binaries / "git"),
        KINDLING_PROJECT_DIR=str(project_dir),
        KINDLING_DEPLOY_LOG_FILE=str(tmp_path / "deploy.log"),
    )

    completed = subprocess.run(
        [BASH, str(DEPLOY_SCRIPT)],
        env=env,
        input="questo non deve arrivare a docker\n",
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0


# --- quale codice gira davvero: api, dashboard, bot (19/09/2026) --------------
#
# Fino a questa data l'unica verifica della versione era `printenv` dentro
# `job`, l'unico servizio a cui il build-arg arrivava: verde per dodici giorni
# mentre bot, api e dashboard non avevano provenienza. Qui ogni esito che non e'
# "stesso codice" deve fermare lo script — compreso "vuota", che `printenv`
# avrebbe preso per un successo.


def test_il_caso_normale_passa_con_il_bot_vecchio_e_bot_invariato(tmp_path):
    esito = _run_deploy(tmp_path)

    assert esito.status == 0, esito.log
    # Il bot (cafe123) non e' il commit del deploy: si confronta, e su cosa.
    assert "diff --quiet cafe123 HEAD -- bot/ requirements.txt" in esito.git_args


@pytest.mark.parametrize(
    ("argomenti", "atteso"),
    [
        ({"api_code_version": "0ld0ld0"}, "api esegue 0ld0ld0 invece di deadbee"),
        ({"dashboard_code_version": "0ld0ld0"}, "dashboard esegue 0ld0ld0 invece di deadbee"),
        ({"api_code_version": ""}, "api ha KINDLING_CODE_VERSION vuota"),
        ({"dashboard_code_version": ""}, "dashboard ha KINDLING_CODE_VERSION vuota"),
        ({"api_code_version": "<absent>"}, "api non ha KINDLING_CODE_VERSION"),
    ],
    ids=["api-vecchia", "dashboard-vecchia", "api-vuota", "dashboard-vuota", "api-assente"],
)
def test_api_o_dashboard_fuori_commit_fermano_il_deploy(tmp_path, argomenti, atteso):
    esito = _run_deploy(tmp_path, **argomenti)

    assert esito.status == 17, esito.log
    assert atteso in esito.log
    assert "=== fine (ok) ===" not in esito.log


@pytest.mark.parametrize(
    ("argomenti", "atteso"),
    [
        ({"bot_code_version": ""}, "bot ha KINDLING_CODE_VERSION vuota"),
        ({"bot_code_version": "<absent>"}, "bot non ha KINDLING_CODE_VERSION"),
        ({"git_bot_diff": 1}, "bot/ o requirements.txt sono cambiati"),
        ({"git_bot_diff": 128}, "codice del bot non verificabile"),
        ({"git_commit_missing": True}, "commit assente dal repository"),
        ({"bot_missing": True}, "nessun container bot"),
    ],
    ids=["vuota", "assente", "codice-cambiato", "diff-fallito", "commit-assente", "container-assente"],
)
def test_bot_da_ricreare_ferma_il_deploy_e_dice_come(tmp_path, argomenti, atteso):
    esito = _run_deploy(tmp_path, **argomenti)

    assert esito.status == 18, esito.log
    assert atteso in esito.log
    assert "=== fine (ok) ===" not in esito.log
    assert "docker compose up -d --force-recreate bot" in esito.stderr
    # Lo script non lo ricrea da se': staccare il gateway resta una scelta.
    assert "force-recreate" not in esito.docker_args


def test_il_bot_allo_stesso_commit_non_chiede_nessun_diff(tmp_path):
    esito = _run_deploy(tmp_path, bot_code_version="deadbee", git_bot_diff=1)

    assert esito.status == 0, esito.log
    assert esito.git_args == ""


def test_api_fuori_commit_e_bot_da_ricreare_si_vedono_entrambi(tmp_path):
    # Il codice di uscita e' quello di api/dashboard, che dice "deploy
    # sbagliato"; il bot dice solo "manca un passo", e viene dopo.
    esito = _run_deploy(tmp_path, api_code_version="", bot_code_version="")

    assert esito.status == 17
    assert "api ha KINDLING_CODE_VERSION vuota" in esito.log
    assert "bot ha KINDLING_CODE_VERSION vuota" in esito.log


# --- Caddy esegue il Caddyfile corrente (19/09/2026) -------------------------
#
# Con il Caddyfile montato come file, un `git pull` lasciava il container sul
# file vecchio e il reload riusciva lo stesso: HTTP/3 e' rimasto acceso dopo il
# deploy che lo spegneva. Il mount ora e' una directory; questo e' il controllo
# che se ne accorgerebbe se succedesse di nuovo, per qualunque causa.


def test_la_stessa_configurazione_serializzata_diversamente_passa(tmp_path):
    # Chiavi in altro ordine, spazi, 1e+09 contro 1000000000: stessa config.
    esito = _run_deploy(tmp_path)

    assert esito.status == 0, esito.log
    assert "caddy-config-in-esercizio uguale al Caddyfile corrente" in esito.log


def test_il_confronto_legge_le_due_meta_nei_posti_giusti(tmp_path):
    esito = _run_deploy(tmp_path)

    # In esercizio: exec nel container che serve il traffico, sulla 2019 interna.
    admin = esito.docker_args.splitlines()[_indice(esito, "localhost:2019/config/")]
    assert " exec -T caddy " in f" {admin} "
    # Attesa: un container NUOVO, l'unico che vede il file corrente.
    adapt = esito.docker_args.splitlines()[_indice(esito, "caddy adapt")]
    assert "run --rm --no-deps" in adapt
    # Dopo il reload, non prima.
    assert _indice(esito, "caddy adapt") > _indice(esito, "caddy reload")


def test_configurazione_in_esercizio_vecchia_ferma_il_deploy(tmp_path):
    # Il caso del 19/09: il container gira ancora senza il blocco globale.
    vecchia = '{"apps":{"http":{"servers":{"srv0":{"idle_timeout":1e+09,"listen":[":443"]}}}}}'
    esito = _run_deploy(tmp_path, caddy_running=vecchia)

    assert esito.status == 19, esito.log
    assert "Caddy non esegue il Caddyfile corrente" in esito.log
    assert "$.apps.http.servers.srv0.protocols" in esito.log
    assert "docker compose up -d --force-recreate caddy" in esito.stderr
    assert "=== fine (ok) ===" not in esito.log


def test_un_valore_diverso_in_una_lista_ferma_il_deploy(tmp_path):
    diversa = CADDY_IN_ESERCIZIO.replace('"h1","h2"', '"h1","h2","h3"')
    esito = _run_deploy(tmp_path, caddy_running=diversa)

    assert esito.status == 19, esito.log
    assert "protocols (lunghezza 3 contro 2)" in esito.log


def test_un_booleano_non_e_uguale_a_un_numero(tmp_path):
    # Per Python True == 1: senza la regola apposta, questi due passerebbero.
    esito = _run_deploy(
        tmp_path, caddy_running='{"apps":{"x":true}}', caddy_adapted='{"apps":{"x":1}}',
    )

    assert esito.status == 19, esito.log


@pytest.mark.parametrize(
    ("argomenti", "atteso"),
    [
        ({"caddy_admin_fails": True}, "admin API di caddy non raggiungibile"),
        ({"caddy_adapt_fails": True}, "caddy adapt fallito"),
        ({"caddy_running": ""}, "JSON illeggibile"),
        ({"caddy_adapted": "non json"}, "JSON illeggibile"),
        ({"caddy_running": "{}", "caddy_adapted": "{}"}, "configurazione attesa vuota"),
    ],
    ids=["admin-giu", "adapt-fallito", "in-esercizio-vuota", "attesa-illeggibile", "entrambe-vuote"],
)
def test_un_confronto_che_non_si_puo_fare_non_passa_per_riuscito(tmp_path, argomenti, atteso):
    esito = _run_deploy(tmp_path, **argomenti)

    assert esito.status == 19, esito.log
    assert atteso in esito.log
    assert "=== fine (ok) ===" not in esito.log


def test_caddy_stantio_non_nasconde_il_perimetro(tmp_path):
    # Si registra e si esce in fondo: il perimetro di kindling_api si verifica
    # lo stesso, e il suo codice ha la precedenza.
    esito = _run_deploy(tmp_path, caddy_admin_fails=True, graph_edges_leaks=True)

    assert esito.status == 12
    assert "admin API di caddy non raggiungibile" in esito.log
    assert "perimetro compromesso" in esito.log
