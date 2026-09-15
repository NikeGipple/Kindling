"""Vista Stato, scheletro e gestione degli errori, contro il fixture montato in-process.

Il fixture (``tools/fixture_api.py``) si monta con ``httpx.ASGITransport``: stesse
rotte e stessi modelli dell'API, nessuna rete e nessun uvicorn. La dashboard non
sa di parlare con un fixture — riceve solo un client httpx, come in produzione.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard import config
from dashboard.main import crea_app, data_ora
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_TODAY
from tools.fixture_api import app as fixture_app


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    # Il processo rifiuta di partire con una variabile di database: i test non
    # devono dipendere da cosa c'e' nella shell di chi li lancia.
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


def _client(transport: httpx.AsyncBaseTransport) -> TestClient:
    http = httpx.AsyncClient(transport=transport, base_url="http://api.test")
    return TestClient(crea_app(api_http=http))


@pytest.fixture
def dashboard():
    with _client(httpx.ASGITransport(app=fixture_app)) as client:
        yield client


# --- vista Stato sulle tre guild --------------------------------------------


def test_guild_limite_dichiara_il_buco_di_osservazione(dashboard):
    r = dashboard.get(f"/guilds/{GUILD_EDGE}")

    assert r.status_code == 200
    html = r.text
    # L'ancora di osservabilita', con la sua spiegazione.
    assert "lunedì 20 aprile 2026, 11:00 UTC" in html
    assert "ancora di osservabilità" in html
    # left_at E rejoined_at valorizzati: dichiarato in chiaro, con le due date.
    assert "C'è un buco di osservazione" in html
    assert "domenica 3 maggio 2026, 08:20 UTC" in html
    assert "venerdì 19 giugno 2026, 17:45 UTC" in html
    # code_version assente: detto, non lasciato vuoto.
    assert "non registrata" in html
    # stats e' mostrato come testo letterale, non interpretato.
    assert "snapshot_gaps" in html
    assert 'class="errore"' not in html


def test_guild_di_oggi_non_ha_caveat(dashboard):
    # dashboard.md 6: "Il bot osserva dal 28 agosto, l'ultimo calcolo e' di
    # lunedi'. Nessun caveat."
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text

    assert "venerdì 28 agosto 2026, 15:28 UTC" in html
    # Ultimo calcolo: lo snapshot 12, il primo ancorato al lunedi'.
    assert "Le metriche arrivano al <strong>lunedì 14 settembre 2026, 00:00 UTC</strong>" in html
    assert "<code>9d0dc98</code>" in html
    assert 'class="avviso"' not in html
    assert "buco di osservazione" not in html


def test_guild_di_oggi_ha_due_run_e_il_primo_non_e_a_mezzanotte(dashboard):
    # La prima cronologia con piu' di una riga. Lo snapshot 11 ha as_of 04:15,
    # preso da now() prima dell'ancoraggio: la vista lo mostra com'e'.
    html = dashboard.get(f"/guilds/{GUILD_TODAY}").text
    storico = html[html.index("Storico delle esecuzioni"):html.index("Diagnostica dell")]

    assert storico.count("<tr>") == 3
    assert storico.index("<code>12</code>") < storico.index("<code>11</code>")
    assert "lunedì 7 settembre 2026, 04:15 UTC" in storico


def test_guild_matura_mostra_lo_storico_delle_esecuzioni(dashboard):
    html = dashboard.get(f"/guilds/{GUILD_MATURE}").text

    storico = html[html.index("Storico delle esecuzioni"):html.index("Diagnostica dell")]
    # 12 run nello storico (limit di default) piu' la riga d'intestazione,
    # dalla piu' recente.
    assert storico.count("<tr>") == 13
    assert storico.index("<code>40</code>") < storico.index("<code>29</code>")
    assert "buco di osservazione" not in html


def test_elenco_delle_guild_osservate(dashboard):
    html = dashboard.get("/").text
    for gid in (GUILD_TODAY, GUILD_MATURE, GUILD_EDGE):
        assert f'href="/guilds/{gid}"' in html


def test_guild_sconosciuta_e_404_e_non_un_errore_di_sistema(dashboard):
    r = dashboard.get("/guilds/123")

    assert r.status_code == 404
    assert "Guild non osservata" in r.text
    assert 'class="errore"' not in r.text


def test_health_raggiunge_l_api(dashboard):
    r = dashboard.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api": "ok"}


def test_buco_di_osservazione_richiede_entrambe_le_date():
    # Solo left_at: il bot e' uscito e non e' rientrato. E' un altro fatto, e
    # non va chiamato "buco" (che ha un inizio e una fine).
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={
            "guild_id": 5, "first_seen_at": "2026-01-01T00:00:00Z",
            "left_at": "2026-03-01T00:00:00Z", "rejoined_at": None,
        })

    with _client(httpx.MockTransport(api)) as client:
        html = client.get("/guilds/5").text
    assert "buco di osservazione" not in html
    assert "Il bot non è più sul server" in html
    assert "Nessun calcolo di metriche ancora eseguito" in html


# --- errori: un aspetto diverso da ogni stato -------------------------------


def test_api_irraggiungibile_e_una_pagina_di_errore_distinta():
    def giu(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client(httpx.MockTransport(giu)) as client:
        r = client.get(f"/guilds/{GUILD_EDGE}")
        h = client.get("/health")

    assert r.status_code == 503
    assert 'class="errore"' in r.text
    assert "API di Kindling non risponde" in r.text  # l'apostrofo arriva escapato
    # Nessun rendering di stato dentro una pagina d'errore.
    assert 'class="cella' not in r.text and 'class="avviso"' not in r.text
    assert h.status_code == 503


def test_api_in_errore_5xx_e_irraggiungibile():
    with _client(httpx.MockTransport(lambda req: httpx.Response(503))) as client:
        assert client.get(f"/guilds/{GUILD_EDGE}").status_code == 503


def test_risposta_fuori_contratto_non_si_rende_come_dato():
    # Una risposta con i nomi del database invece di quelli del modello: e'
    # esattamente il caso in cui un accesso tollerante leggerebbe None.
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"guild_id": 1, "is_suppressed": False})

    with _client(httpx.MockTransport(api)) as client:
        r = client.get("/guilds/1")
    assert r.status_code == 502
    assert 'class="errore"' in r.text


# --- configurazione ----------------------------------------------------------


def test_nessun_default_per_l_indirizzo_dell_api(monkeypatch):
    monkeypatch.delenv("KINDLING_API_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "   ")
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "api:8000")
    with pytest.raises(RuntimeError, match="http"):
        config.api_base_url()

    monkeypatch.setenv("KINDLING_API_BASE_URL", "http://127.0.0.1:8899/")
    assert config.api_base_url() == "http://127.0.0.1:8899"


@pytest.mark.parametrize("nome", ["DATABASE_URL", "API_DATABASE_URL"])
def test_la_dashboard_non_parte_con_una_variabile_di_database(monkeypatch, nome):
    monkeypatch.setenv(nome, "")  # anche vuota: e' gia' una variabile ricevuta
    app = crea_app(api_http=httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture_app)))
    with pytest.raises(RuntimeError, match=nome):
        with TestClient(app):
            pass


def test_la_dashboard_non_parte_senza_indirizzo_dell_api(monkeypatch):
    monkeypatch.delenv("KINDLING_API_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KINDLING_API_BASE_URL"):
        with TestClient(crea_app()):
            pass


def test_data_ora_dichiara_sempre_utc():
    from datetime import datetime, timedelta, timezone

    roma = timezone(timedelta(hours=2))
    assert data_ora(datetime(2026, 9, 7, 6, 15, tzinfo=roma)) == "lunedì 7 settembre 2026, 04:15 UTC"
    assert data_ora(None) == "—"
