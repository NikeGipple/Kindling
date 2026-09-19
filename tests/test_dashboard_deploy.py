"""La forma del servizio dashboard nel compose e nell'immagine (dashboard.md 1, 8, 10).

Sono controlli sul testo dei file di deploy, e lo sono di proposito: le regole che
verificano non producono nessun errore quando vengono violate. Un ``env_file``
aggiunto per comodita' da' al container ``DATABASE_URL`` e la dashboard continua a
funzionare; un ``ports:`` su tutte le interfacce la pubblica su internet e
continua a funzionare; un ``COPY tools/`` mette il fixture nell'immagine e
continua a funzionare. Qui falliscono.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

LOOPBACK = {"127.0.0.1", "::1"}


def _compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


def _servizio() -> dict:
    return _compose()["services"]["dashboard"]


def _ip_del_binding(voce: Union[str, int, dict]) -> Optional[str]:
    """L'IP dell'host su cui Compose pubblica una voce di ``ports:``, o None.

    None significa "nessun IP esplicito", che per Docker e' TUTTE le interfacce:
    ``"8000:8000"`` equivale a ``"0.0.0.0:8000:8000"``. Copre le due sintassi di
    Compose — breve (``"IP:HOST:CONTAINER[/proto]"``, anche ``"[::1]:..."``) e
    lunga (``{target, published, host_ip}``).
    """
    if isinstance(voce, dict):
        return voce.get("host_ip")
    testo = str(voce).split("/", 1)[0]
    if testo.startswith("["):
        return testo[1:testo.index("]")]
    parti = testo.split(":")
    return parti[0] if len(parti) == 3 else None


@pytest.mark.parametrize(
    "voce",
    ["8000:8000", "8000", 8000, "0.0.0.0:8000:8000", "[::]:8000:8000",
     "8000:8000/tcp", {"target": 8000, "published": 8000},
     {"target": 8000, "published": 8000, "host_ip": "0.0.0.0"}],
)
def test_il_controllo_riconosce_i_binding_su_tutte_le_interfacce(voce):
    # Un controllo che non fallisce su nessuna delle forme vietate non controlla
    # niente (CLAUDE.md 7): qui si prova che le riconosce tutte.
    assert _ip_del_binding(voce) not in LOOPBACK


@pytest.mark.parametrize(
    "voce", ["127.0.0.1:8000:8000", "[::1]:8000:8000", "127.0.0.1:8000:8000/tcp",
             {"target": 8000, "published": 8000, "host_ip": "127.0.0.1"}],
)
def test_il_controllo_accetta_il_loopback(voce):
    assert _ip_del_binding(voce) in LOOPBACK


def test_dashboard_pubblica_solo_sul_loopback():
    # dashboard.md 8: "127.0.0.1:8000:8000", non nessun ports:. Senza porta sul
    # loopback il tunnel SSH trova connection refused (CLAUDE.md, errore n. 3);
    # con una porta su tutte le interfacce la dashboard e' su internet senza TLS
    # ne' autenticazione.
    servizio = _servizio()
    assert servizio.get("ports") == ["127.0.0.1:8000:8000"]
    assert "network_mode" not in servizio


# L'unica eccezione alla regola, decisa in dashboard.md 8 e scritta STRETTA
# (dashboard-fase2.md 8-bis): il solo servizio caddy, le sole porte 80 e 443,
# ciascuna verso la stessa porta nel container. Non "caddy puo' pubblicare
# quello che vuole".
ECCEZIONE_PUBBLICA = {("caddy", 80, 80), ("caddy", 443, 443)}


def _porte_del_binding(voce: Union[str, int, dict]) -> tuple[Optional[int], Optional[int]]:
    """(porta dell'host, porta del container) di una voce di ``ports:``.

    ``"8000"`` da solo pubblica la porta del container su una porta dell'host
    scelta da Docker: l'host e' None, e None non e' mai nell'eccezione.
    """
    if isinstance(voce, dict):
        pubblicata = voce.get("published")
        return (int(pubblicata) if pubblicata is not None else None, int(voce["target"]))
    testo = str(voce).split("/", 1)[0]
    if testo.startswith("["):
        testo = testo[testo.index("]") + 2:]
    parti = testo.split(":")
    if len(parti) == 1:
        return (None, int(parti[0]))
    return (int(parti[-2]), int(parti[-1]))


def _esposti_non_ammessi(compose: dict) -> list:
    """Ogni binding su tutte le interfacce che l'eccezione di caddy non copre."""
    return [
        (nome, voce)
        for nome, servizio in compose["services"].items()
        for voce in servizio.get("ports", [])
        if _ip_del_binding(voce) not in LOOPBACK
        and (nome, *_porte_del_binding(voce)) not in ECCEZIONE_PUBBLICA
    ]


def test_nessun_servizio_pubblica_su_tutte_le_interfacce():
    # La regola del progetto, applicata a ogni servizio e non solo alla
    # dashboard: un ports: aggiunto domani su api o bot fallisce qui. Con
    # l'unica eccezione di caddy, 80 e 443.
    esposti = _esposti_non_ammessi(_compose())
    assert esposti == [], f"porte pubblicate su tutte le interfacce: {esposti}"


def test_caddy_pubblica_esattamente_80_e_443():
    # L'eccezione usata, per intero e non oltre: senza la 80 non passa la
    # verifica di Let's Encrypt ne' il redirect verso https.
    assert _compose()["services"]["caddy"]["ports"] == ["80:80", "443:443"]


@pytest.mark.parametrize(
    ("servizio", "voce"),
    [
        ("caddy", "8080:8080"),  # una terza porta di caddy
        ("caddy", "2019:2019"),  # l'endpoint di amministrazione di caddy
        ("caddy", "80:8000"),  # 80 dell'host, ma verso un'altra porta
        ("caddy", "443"),  # porta dell'host scelta da Docker
        ("caddy", {"target": 22, "published": 22}),
        ("api", "80:80"),  # la porta giusta su un altro servizio
        ("dashboard", "443:443"),
        ("postgres", "5432:5432"),  # l'incidente di CLAUDE.md
    ],
)
def test_l_eccezione_di_caddy_non_copre_altro(servizio, voce):
    # Il test gemello: un'eccezione senza un test che ne misura il bordo e' un
    # buco. Stesso compose di produzione, una voce in piu': deve fallire.
    compose = _compose()
    compose["services"][servizio].setdefault("ports", []).append(voce)
    assert _esposti_non_ammessi(compose) == [(servizio, voce)]


def test_caddy_ha_la_forma_di_dashboard_fase2_8_bis():
    compose = _compose()
    caddy = compose["services"]["caddy"]
    assert caddy["image"] == "caddy:2-alpine"
    assert caddy["restart"] == "unless-stopped"
    assert caddy["mem_limit"] == "96m"
    assert set(caddy["volumes"]) == {
        "./ops/caddy:/etc/caddy:ro",
        "caddy_data:/data",
        "caddy_config:/config",
        "./legal:/srv/legal:ro",
    }
    # caddy_data e' un volume NOMINATO del compose: senza, i certificati non
    # sopravvivono alla ricreazione del container e al sesto in una settimana
    # Let's Encrypt rifiuta.
    assert "caddy_data" in compose["volumes"]
    assert "caddy_config" in compose["volumes"]
    # service_started, non service_healthy: una dashboard malata non deve
    # spegnere le pagine pubbliche ne' il rinnovo dei certificati.
    assert caddy["depends_on"] == {"dashboard": {"condition": "service_started"}}
    assert "profiles" not in caddy
    assert "env_file" not in caddy and "environment" not in caddy
    # Ogni file montato esiste nel repo: un bind mount di un percorso assente
    # crea una directory vuota al suo posto, in silenzio.
    assert (REPO / "ops" / "caddy" / "Caddyfile").is_file()
    assert (REPO / "legal" / "index.html").is_file()


# --- KINDLING_CODE_VERSION: ogni immagine costruita da qui ne ha una ----------
#
# Fino al 19/09/2026 l'argomento lo riceveva solo `job`: bot, api e dashboard
# avevano `build: .` e giravano con la versione vuota del fallback del
# Dockerfile, senza nessun errore, mentre la verifica del deploy guardava
# proprio `job` ed era verde. Qui fallisce un servizio che nasce con `build:`
# senza l'argomento — anche uno che oggi non esiste.

CODE_VERSION_ARG = "${KINDLING_CODE_VERSION:-}"


def _servizi_senza_code_version(compose: dict) -> list[str]:
    """I servizi con ``build:`` che non passano KINDLING_CODE_VERSION fra gli args.

    Si accetta solo la forma lunga con ``args`` come mappa, e con il valore
    esatto: ``build: .``, un ``args`` a lista o un default diverso sono tutti
    "manca" — la forma che lo script di deploy si aspetta e' una sola.
    """
    mancanti = []
    for nome, servizio in compose["services"].items():
        if "build" not in servizio:
            continue
        build = servizio["build"]
        args = build.get("args") if isinstance(build, dict) else None
        if not isinstance(args, dict) or args.get("KINDLING_CODE_VERSION") != CODE_VERSION_ARG:
            mancanti.append(nome)
    return mancanti


def test_ogni_servizio_costruito_riceve_kindling_code_version():
    compose = _compose()
    # Il controllo non deve passare perche' non trova niente da controllare.
    costruiti = {n for n, s in compose["services"].items() if "build" in s}
    assert costruiti >= {"bot", "api", "dashboard", "job"}, costruiti
    assert _servizi_senza_code_version(compose) == []


@pytest.mark.parametrize("servizio", ["bot", "api", "dashboard", "job"])
def test_il_controllo_riconosce_un_build_senza_argomento(servizio):
    compose = _compose()
    compose["services"][servizio]["build"] = "."
    assert _servizi_senza_code_version(compose) == [servizio]


def test_il_controllo_vale_anche_per_un_servizio_nuovo():
    compose = _compose()
    compose["services"]["nuovo"] = {"build": {"context": "."}}
    assert _servizi_senza_code_version(compose) == ["nuovo"]


def test_nessun_servizio_mette_kindling_code_version_nell_environment():
    # environment ed env_file vincono sul valore inciso con l'ARG: una riga,
    # anche vuota, sovrascriverebbe il commit vero con la build riuscita.
    for nome, servizio in _compose()["services"].items():
        ambiente = servizio.get("environment") or {}
        nomi = ambiente.keys() if isinstance(ambiente, dict) else [
            voce.split("=", 1)[0] for voce in ambiente
        ]
        assert "KINDLING_CODE_VERSION" not in nomi, nome


VARIABILI_DELLA_DASHBOARD = {
    "KINDLING_API_BASE_URL",
    "KINDLING_DISCORD_CLIENT_ID",
    "KINDLING_DISCORD_CLIENT_SECRET",
    "KINDLING_SESSION_SECRET",
    "KINDLING_OAUTH_REDIRECT_URI",
}


def test_dashboard_non_riceve_variabili_di_database():
    servizio = _servizio()
    # Niente env_file: .env contiene DATABASE_URL e API_DATABASE_URL (e il token
    # del bot).
    assert "env_file" not in servizio
    # L'environment e' l'elenco completo, ESATTO e non "contiene": l'indirizzo
    # dell'API e le quattro variabili del login, nient'altro.
    assert set(servizio["environment"]) == VARIABILI_DELLA_DASHBOARD
    # Nessun valore scritto nel compose: arrivano da .env, senza default.
    for nome in VARIABILI_DELLA_DASHBOARD:
        assert servizio["environment"][nome] == f"${{{nome}:-}}", nome


def test_il_divieto_sulle_variabili_di_database_resta_separato():
    # Le variabili nuove non hanno allargato il divieto ne' l'hanno aggirato.
    from dashboard.config import DATABASE_VARIABLES

    assert not set(DATABASE_VARIABLES) & set(_servizio()["environment"])
    assert "DISCORD_TOKEN" not in _servizio()["environment"]


def test_env_example_dichiara_le_variabili_del_login_senza_valori():
    righe = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    for nome in VARIABILI_DELLA_DASHBOARD - {"KINDLING_API_BASE_URL"}:
        assert f"{nome}=" in righe, nome  # presente, e VUOTA: nessun esempio di segreto


def test_dashboard_ha_la_forma_di_dashboard_md_8():
    servizio = _servizio()
    assert servizio["build"]["context"] == "."
    assert servizio["mem_limit"] == "150m"
    assert servizio["command"] == [
        "uvicorn", "dashboard.main:crea_app", "--factory", "--host", "0.0.0.0", "--port", "8000",
        "--workers", "1", "--no-access-log",
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
    """Gli argomenti di ogni `compose ... up` eseguito dallo script.

    Salta commenti ed `echo`: lo script stampa `docker compose up -d
    --force-recreate bot` come istruzione per chi legge (verifica del bot), e
    una riga stampata non avvia niente.
    """
    comandi = []
    for riga in DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines():
        testo = riga.strip()
        if testo.startswith(("#", "echo ")) or " up " not in f" {testo} ":
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
    # SessionMiddleware di Starlette la importa: senza, la dashboard non parte.
    assert "itsdangerous" in testo


# --- ops/caddy/Caddyfile -------------------------------------------------------
#
# Controlli sul testo, per la stessa ragione del resto del file: un access log
# spento, un HSTS che impegna i sottodomini o /health esposta non producono
# nessun errore.

CADDYFILE = REPO / "ops" / "caddy" / "Caddyfile"


def _mount_del_caddyfile(compose: dict) -> list[str]:
    """Le voci di volume di caddy che portano il Caddyfile dentro il container."""
    return [
        v for v in compose["services"]["caddy"]["volumes"]
        if isinstance(v, str) and ":/etc/caddy" in v
    ]


def _problemi_del_mount(compose: dict) -> list[str]:
    """Cosa c'e' di sbagliato nel mount del Caddyfile; vuoto se va bene.

    Una DIRECTORY, non il file: un bind mount di file e' legato all'inode, e
    `git pull` sostituisce il Caddyfile invece di riscriverlo. Il container
    vedeva il file di quando era partito e il `caddy reload` del deploy lo
    ricaricava riuscendo (19/09/2026, HTTP/3 rimasto acceso). E in sola
    lettura, e non `./ops` intera, che contiene gli script di deploy.
    """
    voci = _mount_del_caddyfile(compose)
    if len(voci) != 1:
        return [f"attesa una voce sola verso /etc/caddy, trovate {voci}"]
    parti = voci[0].split(":")
    if len(parti) != 3:
        return [f"forma inattesa: {voci[0]}"]
    sorgente, destinazione, modo = parti
    problemi = []
    if not (REPO / sorgente).is_dir():
        problemi.append(f"la sorgente {sorgente} non e' una directory del repo")
    if destinazione != "/etc/caddy":
        problemi.append(f"destinazione {destinazione}: deve essere la directory /etc/caddy")
    if modo != "ro":
        problemi.append(f"modo {modo}: deve essere ro")
    if sorgente.rstrip("/") in (".", "./ops"):
        problemi.append(f"{sorgente} monta piu' del solo Caddyfile")
    return problemi


def test_il_caddyfile_si_monta_come_directory_in_sola_lettura():
    assert _problemi_del_mount(_compose()) == []
    assert (REPO / "ops" / "caddy" / "Caddyfile").is_file()


@pytest.mark.parametrize(
    "voce",
    [
        "./ops/caddy/Caddyfile:/etc/caddy/Caddyfile:ro",  # il ritorno al mount di file
        "./ops/caddy:/etc/caddy",  # senza :ro
        "./ops/caddy:/etc/caddy:rw",
        "./ops:/etc/caddy:ro",  # gli script di deploy dentro il proxy
    ],
    ids=["file", "senza-modo", "rw", "ops-intera"],
)
def test_il_controllo_del_mount_riconosce_le_forme_sbagliate(voce):
    compose = _compose()
    volumi = compose["services"]["caddy"]["volumes"]
    compose["services"]["caddy"]["volumes"] = [
        voce if v in _mount_del_caddyfile(compose) else v for v in volumi
    ]
    assert _problemi_del_mount(compose) != []


def _blocchi_di_primo_livello() -> dict[str, str]:
    """Il corpo di ogni blocco di primo livello, senza i commenti.

    Il blocco di opzioni globali non ha nome: compare con la chiave ``""``.
    """
    righe = [
        r.split("#", 1)[0].rstrip()
        for r in CADDYFILE.read_text(encoding="utf-8").splitlines()
    ]
    blocchi: dict[str, str] = {}
    nome, corpo, profondita = None, [], 0
    for riga in righe:
        if not riga.strip():
            continue
        if profondita == 0 and riga.endswith("{"):
            nome, corpo = riga[:-1].strip(), []
        elif profondita > 0:
            corpo.append(riga.strip())
        profondita += riga.count("{") - riga.count("}")
        if profondita == 0 and nome is not None:
            blocchi[nome] = "\n".join(corpo[:-1])
            nome = None
    return blocchi


def _blocchi_caddy() -> dict[str, str]:
    """I soli blocchi di sito, senza quello di opzioni globali."""
    return {n: c for n, c in _blocchi_di_primo_livello().items() if n}


def test_caddyfile_http3_spento():
    # Con h3 acceso Caddy annuncia `Alt-Svc: h3=":443"` su ogni risposta, ma la
    # 443/UDP non e' pubblicata (test_caddy_pubblica_esattamente_80_e_443) ne'
    # aperta sul firewall: ogni browser pagherebbe un timeout prima di ripiegare
    # su TCP. Le opzioni globali valgono solo come PRIMO blocco del file.
    righe = [
        r.split("#", 1)[0].strip()
        for r in CADDYFILE.read_text(encoding="utf-8").splitlines()
    ]
    assert [r for r in righe if r][0] == "{"
    globali = _blocchi_di_primo_livello()[""]
    assert "protocols h1 h2" in globali.splitlines()
    assert "h3" not in globali


def test_caddyfile_due_hostname_e_nient_altro():
    assert set(_blocchi_caddy()) == {"kindling.nexus", "dashboard.kindling.nexus"}


def test_caddyfile_apex_pubblico_serve_solo_file_statici():
    apex = _blocchi_caddy()["kindling.nexus"]
    assert "root * /srv/legal" in apex
    assert "file_server" in apex
    assert "reverse_proxy" not in apex


def test_caddyfile_hsts_di_un_giorno_senza_sottodomini():
    for nome, corpo in _blocchi_caddy().items():
        assert 'header Strict-Transport-Security "max-age=86400"' in corpo, nome
        assert "includeSubDomains" not in corpo, nome


def test_caddyfile_dashboard_logga_senza_il_code():
    # Senza la direttiva `log` Caddy non scrive nessun access log: l'errore
    # della prima stesura della spec (dashboard-fase2.md 3-quater). Con `log`,
    # il `code` del callback va tolto dalla query string.
    dashboard = _blocchi_caddy()["dashboard.kindling.nexus"]
    assert "log {" in dashboard
    assert "format filter {" in dashboard
    assert "request>uri query {" in dashboard
    assert "delete code" in dashboard
    # Non uno skip del callback ne' un log buttato: la riga del callback serve.
    assert "log_skip" not in dashboard
    assert "output discard" not in dashboard


def test_caddyfile_health_non_esposta_e_proxy_verso_la_dashboard():
    dashboard = _blocchi_caddy()["dashboard.kindling.nexus"]
    assert "respond /health 404" in dashboard
    assert "reverse_proxy dashboard:8000" in dashboard


def test_legal_index_linka_le_due_pagine_legali():
    index = (REPO / "legal" / "index.html").read_text(encoding="utf-8")
    assert 'href="informativa-privacy.html"' in index
    assert 'href="termini-di-servizio.html"' in index
    assert 'href="style.css"' in index
