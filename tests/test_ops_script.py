"""Lo script di esecuzione settimanale: due comportamenti che sbagliano in silenzio.

Non e' un test di `flock` — quello e' verificato in produzione — ma di come lo
script **usa** il suo esito, che e' la parte che era sbagliata: un'esecuzione
saltata usciva con 0, indistinguibile dall'esterno da una riuscita. Qualunque
monitoraggio futuro (il MAILTO di cron, un wrapper, un controllo sull'ultimo
codice di uscita) avrebbe letto "tutto bene" su una settimana in cui il job non
ha calcolato niente. Vale lo stesso principio del runbook: un job che non parte
produce assenza e non errore — e qui l'assenza aveva un modo di farsi notare,
che si stava perdendo.

L'altro comportamento e' `-T`. Senza, `docker compose run` alloca un terminale e
prova a leggere da stdin: lanciato in background il kernel ferma il processo con
SIGTTIN, e un processo fermo **continua a tenere il lock**, facendo saltare
l'esecuzione successiva con una riga SKIP inspiegabile.

``flock`` e ``docker`` sono sostituiti da stub sul PATH: qui interessa cosa fa lo
script con quello che gli tornano, non che quei programmi funzionino.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "kindling-weekly.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash non disponibile")

# Lo stub di docker registra gli argomenti ricevuti, cosi' il test puo' guardare
# come e' stato invocato invece di fidarsi del sorgente.
DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$DOCKER_ARGS_FILE"
exit 0
"""

# flock che simula il lock occupato: e' il codice che --conflict-exit-code
# produrrebbe.
FLOCK_BUSY = """#!/usr/bin/env bash
exit 99
"""

# flock che lascia passare: esegue il comando che gli viene dato, come farebbe
# dopo aver preso il lock.
FLOCK_FREE = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do
    case "$1" in
        --nonblock|-n) shift ;;
        --conflict-exit-code) shift 2 ;;
        *) break ;;
    esac
done
shift  # il file di lock
exec "$@"
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run(tmp_path: Path, *, flock_body: str):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _stub(binaries / "docker", DOCKER_STUB)
    _stub(binaries / "flock", flock_body)

    log = tmp_path / "job.log"
    args_file = tmp_path / "docker-args.txt"
    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        DOCKER_ARGS_FILE=str(args_file),
        KINDLING_DOCKER_BIN=str(binaries / "docker"),
        KINDLING_LOG_FILE=str(log),
        KINDLING_LOCK_FILE=str(tmp_path / "lock"),
        KINDLING_PROJECT_DIR=str(tmp_path),
    )
    env.pop("KINDLING_LOCKED", None)

    completed = subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )
    return (
        completed.returncode,
        log.read_text(encoding="utf-8") if log.exists() else "",
        args_file.read_text(encoding="utf-8") if args_file.exists() else "",
    )


def test_esecuzione_saltata_esce_con_99_e_non_con_zero(tmp_path):
    status, log, docker_args = _run(tmp_path, flock_body=FLOCK_BUSY)

    assert "SKIP" in log
    # Il punto: dall'esterno una settimana saltata NON deve somigliare a una
    # settimana andata bene.
    assert status == 99, "un'esecuzione saltata deve essere distinguibile"
    assert docker_args == "", "con il lock occupato non si lancia nessun job"


def test_esecuzione_normale_esce_con_zero(tmp_path):
    status, log, docker_args = _run(tmp_path, flock_body=FLOCK_FREE)

    assert status == 0
    assert "=== fine (ok) ===" in log
    assert "SKIP" not in log
    assert docker_args.count("\n") == 2, "snapshot e metrics, in quest'ordine"


def test_docker_compose_run_non_alloca_un_terminale(tmp_path):
    # Senza -T, `docker compose run` prova a leggere da stdin: in background il
    # kernel ferma il processo con SIGTTIN, e un processo fermo continua a
    # tenere il lock — l'esecuzione successiva salta senza una ragione
    # leggibile. E' la forma che il runbook usa gia' per `docker compose exec`
    # nella procedura delle migration.
    _, _, docker_args = _run(tmp_path, flock_body=FLOCK_FREE)

    invocazioni = [line for line in docker_args.splitlines() if line.strip()]
    assert invocazioni, "lo stub deve aver registrato qualcosa"
    for invocazione in invocazioni:
        assert "run --rm -T" in invocazione, invocazione


def test_snapshot_fallito_non_fa_partire_metrics(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _stub(binaries / "flock", FLOCK_FREE)
    # Fallisce solo sullo snapshot: metrics non deve essere mai invocato.
    _stub(
        binaries / "docker",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$DOCKER_ARGS_FILE"
case "$*" in *snapshot*) exit 3 ;; esac
exit 0
""",
    )

    log = tmp_path / "job.log"
    args_file = tmp_path / "docker-args.txt"
    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        DOCKER_ARGS_FILE=str(args_file),
        KINDLING_DOCKER_BIN=str(binaries / "docker"),
        KINDLING_LOG_FILE=str(log),
        KINDLING_LOCK_FILE=str(tmp_path / "lock"),
        KINDLING_PROJECT_DIR=str(tmp_path),
    )
    env.pop("KINDLING_LOCKED", None)
    completed = subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 1
    assert "ABORT metrics non eseguito" in log.read_text(encoding="utf-8")
    assert "metrics" not in args_file.read_text(encoding="utf-8")
