"""La forma del servizio dashboard nel compose e nell'immagine (dashboard.md 1, 8, 10).

Sono controlli sul testo dei file di deploy, e lo sono di proposito: le regole che
verificano non producono nessun errore quando vengono violate. Un ``env_file``
aggiunto per comodita' da' al container ``DATABASE_URL`` e la dashboard continua a
funzionare; un ``ports:`` la pubblica e continua a funzionare; un ``COPY tools/``
mette il fixture nell'immagine e continua a funzionare. Qui falliscono.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def _servizio() -> dict:
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["dashboard"]


def test_dashboard_non_pubblica_porte():
    servizio = _servizio()
    assert "ports" not in servizio
    assert "network_mode" not in servizio


def test_dashboard_non_riceve_variabili_di_database():
    servizio = _servizio()
    # Niente env_file: .env contiene DATABASE_URL e API_DATABASE_URL.
    assert "env_file" not in servizio
    # L'environment e' l'elenco completo, ed e' di una riga sola.
    assert set(servizio["environment"]) == {"KINDLING_API_BASE_URL"}
    # Nessun indirizzo scritto nel compose: arriva da .env, senza default.
    assert servizio["environment"]["KINDLING_API_BASE_URL"] == "${KINDLING_API_BASE_URL:-}"


def test_dashboard_ha_la_forma_di_dashboard_md_8():
    servizio = _servizio()
    assert servizio["build"] == "."
    assert servizio["mem_limit"] == "150m"
    assert servizio["command"] == [
        "uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1",
    ]
    assert "localhost:8000/health" in " ".join(servizio["healthcheck"]["test"])
    assert servizio["depends_on"] == {"api": {"condition": "service_healthy"}}
    # Nessun profiles: un servizio escluso in silenzio da build/up e' il difetto
    # del 07/09/2026 (CLAUDE.md 7).
    assert "profiles" not in servizio


def test_dockerfile_copia_dashboard_e_non_tools():
    righe = [r.strip() for r in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines()]
    copy = [r for r in righe if r.startswith("COPY")]

    assert "COPY dashboard/ ./dashboard/" in copy
    # dashboard importa api.models: senza api/ nell'immagine non parte.
    assert "COPY api/ ./api/" in copy
    assert not any("tools" in r for r in copy), copy
    assert not any(r.startswith("COPY . ") for r in copy), copy


DEPLOY_SCRIPT = REPO / "ops" / "kindling-deploy.sh"

# I servizi sempre accesi che il deploy NON deve ricreare: postgres (stateful) e
# bot (farebbe cadere il gateway Discord). Tutti gli altri senza `profiles` il
# deploy li costruisce, quindi deve anche avviarli.
_ESCLUSI_DA_UP_DI_PROPOSITO = {"postgres", "bot"}


def _comandi_up() -> list[list[str]]:
    """Gli argomenti di ogni `compose ... up` NON commentato dello script."""
    comandi = []
    for riga in DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines():
        testo = riga.strip()
        if testo.startswith("#") or " up " not in f" {testo} ":
            continue
        parole = testo.split()
        comandi.append(parole[parole.index("up") + 1:])
    return comandi


def test_il_deploy_avvia_la_dashboard():
    # `compose build` costruisce l'immagine della dashboard; se la dashboard
    # manca dalla riga di `up`, il container non parte e lo script esce 0.
    comandi = _comandi_up()
    assert len(comandi) == 1, comandi
    argomenti = comandi[0]
    assert argomenti[0] == "-d"
    assert "dashboard" in argomenti[1:], argomenti
    assert "api" in argomenti[1:], argomenti


def test_il_deploy_avvia_ogni_servizio_che_costruisce():
    # La forma generale del difetto: un servizio aggiunto al compose domani,
    # senza profiles, verrebbe costruito e mai avviato. Qui si rompe.
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    costruiti_e_accesi = {
        nome for nome, s in compose["services"].items() if "profiles" not in s
    } - _ESCLUSI_DA_UP_DI_PROPOSITO
    (argomenti,) = _comandi_up()
    assert set(argomenti[1:]) == costruiti_e_accesi


def test_lo_script_e_la_dashboard_vietano_le_stesse_variabili():
    # Due elenchi dello stesso divieto: lo script (controllo al deploy) e
    # dashboard/config.py (controllo all'avvio). Se divergono, uno dei due
    # controlli smette di coprire una variabile senza dirlo.
    from dashboard.config import DATABASE_VARIABLES

    righe = [
        r for r in DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines()
        if r.startswith("DASHBOARD_FORBIDDEN_VARS=")
    ]
    assert len(righe) == 1, righe
    nello_script = righe[0].split("=", 1)[1].strip('"').split()
    assert sorted(nello_script) == sorted(DATABASE_VARIABLES)


def test_le_dipendenze_della_dashboard_sono_in_requirements():
    testo = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert "jinja2" in testo
    assert "httpx" in testo
