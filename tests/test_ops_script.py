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
# ma di come lo script usa il loro esito. Due difetti reali lo hanno reso
# necessario (entrambi in produzione il 07/09/2026, CLAUDE.md 7):
#
# - ``docker compose build`` non ricostruiva ``job`` (profiles: ["tools"]),
#   senza nessun errore — il job ha girato con un'immagine di tre giorni prima;
# - non esisteva nessun modo di sapere se le migration in ``migrations/`` erano
#   davvero applicate sul database della droplet, quindi nessuno lo verificava
#   prima di costruire il codice nuovo sopra.

DEPLOY_SCRIPT = REPO / "ops" / "kindling-deploy.sh"

# git: risponde a `pull` e a `rev-parse --short HEAD` con un hash fisso, cosi'
# i test possono verificare che sia proprio QUELLO ad arrivare al build.
GIT_STUB = '\n'.join([
    "#!/usr/bin/env bash",
    'case "$*" in',
    '    *pull) echo "Already up to date." ; exit 0 ;;',
    '    *"rev-parse --short HEAD") echo "deadbee" ; exit 0 ;;',
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
# l'assenza della tabella (l'errore esatto che Postgres darebbe).
DOCKER_STUB_DEPLOY = '\n'.join([
    "#!/usr/bin/env bash",
    'printf "%s\\n" "$*" >>"$DEPLOY_DOCKER_ARGS_FILE"',
    'printf "KINDLING_CODE_VERSION=%s\\n" "${KINDLING_CODE_VERSION:-<unset>}" >>"$DEPLOY_DOCKER_ENV_FILE"',
    'case "$*" in',
    '    *"schema_migrations ORDER BY filename"*)',
    '        if [ "$STUB_SCHEMA_MIGRATIONS" = "MISSING_TABLE" ]; then',
    '            echo \'ERROR:  relation "schema_migrations" does not exist\' >&2',
    "            exit 1",
    "        fi",
    '        for nome in $STUB_SCHEMA_MIGRATIONS; do printf "%s\\n" "$nome"; done',
    "        exit 0 ;;",
    '    *"graph_edges"*) echo "ERROR: permission denied" >&2 ; exit 1 ;;',
    '    *"images --format"*)',
    '        echo "kindling-bot	2026-09-07T10:00:00Z"',
    '        echo "kindling-api	2026-09-07T10:00:00Z"',
    '        echo "kindling-job	2026-09-07T10:00:00Z"',
    "        exit 0 ;;",
    "esac",
    "exit 0",
    "",
])


@dataclass(frozen=True)
class EsitoDeploy:
    status: int
    log: str
    docker_args: str
    docker_env: str


def _run_deploy(
    tmp_path: Path,
    *,
    migrations: tuple[str, ...] = ("0001_a.sql", "0002_b.sql"),
    schema_migrations: str = "0001_a.sql 0002_b.sql",
    git_body: str = GIT_STUB,
) -> EsitoDeploy:
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    _stub(binaries / "docker", DOCKER_STUB_DEPLOY)
    _stub(binaries / "git", git_body)

    project_dir = tmp_path / "progetto"
    (project_dir / "migrations").mkdir(parents=True, exist_ok=True)
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
    )


def test_procede_se_tutte_le_migration_sono_applicate(tmp_path):
    esito = _run_deploy(tmp_path)

    assert esito.status == 0
    assert "=== fine (ok) ===" in esito.log
    assert "up -d api" in esito.docker_args


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
    # modifica che non lo riguarda. Solo `up -d api`.
    esito = _run_deploy(tmp_path)

    invocazioni_up = [
        riga for riga in esito.docker_args.splitlines() if " up " in f" {riga} "
    ]
    assert invocazioni_up, "lo script deve invocare up almeno una volta"
    for riga in invocazioni_up:
        assert riga.endswith("up -d api"), riga


def test_kindling_code_version_arriva_al_build_dal_git_pullato_non_da_env(tmp_path):
    # Il punto di tutta la parte A: la versione non si legge da .env (lo
    # script non lo tocca nemmeno), si calcola da git rev-parse subito dopo il
    # pull e viaggia nell'ambiente della shell fino a docker compose build.
    esito = _run_deploy(tmp_path)

    righe_env = [r for r in esito.docker_env.splitlines() if r.strip()]
    assert righe_env, "il docker stub deve aver registrato qualcosa"
    for riga in righe_env:
        assert riga == "KINDLING_CODE_VERSION=deadbee", riga


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
