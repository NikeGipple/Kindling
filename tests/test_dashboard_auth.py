"""Login Discord e guardia della dashboard (dashboard.md 3, dashboard-fase2.md 3-quinquies).

Ogni test qui esiste perche' la cosa che prova, sbagliata, non darebbe errore:
un permesso letto male autorizza in silenzio, un ricontrollo che in caso di
guasto lascia passare sembra funzionare, una sessione che si rinnova da sola
sembra scadere. Discord e' finto (``httpx.MockTransport``), l'API e' il fixture;
il cookie di sessione e' quello vero di Starlette.
"""

from __future__ import annotations

import json
import time
from base64 import b64decode
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import itsdangerous
import pytest
from fastapi.testclient import TestClient

from dashboard import auth, config
from dashboard.main import crea_app
from tests.sessione_dashboard import (
    OAUTH_DI_TEST,
    dati_di_sessione,
    entra,
)
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

ORE_8 = auth.DURATA_SESSIONE_SECONDI
MINUTI_15 = auth.TTL_RICONTROLLO_SECONDI


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


# --- Discord finto -------------------------------------------------------------


def _guild(gid: int, permissions: Any = "0", owner: Any = False) -> dict:
    return {"id": str(gid), "name": "x", "owner": owner, "permissions": permissions}


def discord_finto(
    guilds: Any = None,
    *,
    token_status: int = 200,
    guilds_status: int = 200,
    guilds_body: Optional[bytes] = None,
    chiamate: Optional[list] = None,
) -> Callable[[httpx.Request], httpx.Response]:
    if guilds is None:
        guilds = [_guild(GUILD_TODAY, "8")]

    def handler(request: httpx.Request) -> httpx.Response:
        if chiamate is not None:
            chiamate.append(request)
        if request.url.path == "/api/v10/oauth2/token":
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "token-finto", "token_type": "Bearer"})
        if request.url.path == "/api/v10/users/@me/guilds":
            assert request.headers["Authorization"] == "Bearer token-finto"
            if guilds_status != 200:
                return httpx.Response(guilds_status, text="errore")
            if guilds_body is not None:
                return httpx.Response(200, content=guilds_body)
            return httpx.Response(200, json=guilds)
        return httpx.Response(404)

    return handler


def _app(
    handler: Optional[Callable] = None,
    *,
    api_transport: Optional[httpx.AsyncBaseTransport] = None,
    oauth: config.OAuthConfig = OAUTH_DI_TEST,
):
    return crea_app(
        api_http=httpx.AsyncClient(
            transport=api_transport or httpx.ASGITransport(app=fixture_app),
            base_url="http://api.test",
        ),
        oauth=oauth,
        discord_http=auth.crea_discord_http(
            transport=httpx.MockTransport(handler or discord_finto())
        ),
    )


@pytest.fixture
def client_per():
    aperti = []

    def fabbrica(handler=None, **kwargs) -> TestClient:
        client = TestClient(_app(handler, **kwargs), follow_redirects=False)
        client.__enter__()
        aperti.append(client)
        return client

    yield fabbrica
    for client in aperti:
        client.__exit__(None, None, None)


def _state(risposta: httpx.Response) -> str:
    return parse_qs(urlsplit(risposta.headers["location"]).query)["state"][0]


def _login(client: TestClient, **extra) -> httpx.Response:
    """Il login interattivo completo: bottone, Discord, callback. Restituisce il callback."""
    avvio = client.post("/login")
    assert avvio.status_code == 303
    return client.get("/oauth/callback", params={"code": "code-finto", "state": _state(avvio), **extra})


def _sessione(client: TestClient) -> dict:
    """Il contenuto del cookie di sessione: e' firmato, non cifrato, quindi si legge."""
    valore = client.cookies.get(auth.COOKIE_SESSIONE)
    assert valore, "nessun cookie di sessione"
    firmato = itsdangerous.TimestampSigner(OAUTH_DI_TEST.session_secret).unsign(valore)
    return json.loads(b64decode(firmato))


# --- il controllo di autorizzazione: la funzione sola -------------------------


def test_permissions_oltre_2_alla_53_si_legge_come_intero_non_come_float():
    # 2^60 + 8: il bit ADMINISTRATOR c'e', e passando da un float sparisce
    # (a quella grandezza i float vanno a passi di 256).
    assert float(2**60 + 8) == float(2**60)
    assert auth.e_amministratore(_guild(1, str(2**60 + 8))) is True
    # 2^54 + 6: il bit NON c'e', e un float arrotonda a 2^54 + 8, che lo accende.
    # E' il caso che autorizzerebbe in silenzio.
    assert int(float(2**54 + 6)) & 0x8 == 0x8
    assert auth.e_amministratore(_guild(1, str(2**54 + 6))) is False


def test_owner_senza_il_bit_administrator_e_autorizzato():
    assert auth.e_amministratore(_guild(1, "0", owner=True)) is True
    # `is True`, non un valore "vero": la stringa "false" e' truthy.
    assert auth.e_amministratore(_guild(1, "0", owner="false")) is False


@pytest.mark.parametrize("permissions", [8, None, "", "0x8", "8.0", "-8", "²"])
def test_un_permesso_che_non_e_una_stringa_di_cifre_non_si_interpreta(permissions):
    # Un intero al posto della stringa e' un cambio di contratto di Discord: non
    # si indovina, si nega (il chiamante trasforma l'eccezione in accesso negato).
    with pytest.raises(auth.RispostaDiscordNonValida):
        auth.e_amministratore(_guild(1, permissions))


def test_l_insieme_autorizzato_e_l_intersezione_con_le_osservate():
    risposta = [
        _guild(GUILD_TODAY, "8"),  # admin e osservata
        _guild(GUILD_MATURE, "0"),  # osservata, non admin
        _guild(999, "8"),  # admin, non osservata
    ]
    assert auth.guild_autorizzate(risposta, {GUILD_TODAY, GUILD_MATURE}).guilds == {GUILD_TODAY}


# --- il login ------------------------------------------------------------------


def test_login_interattivo_completo(client_per):
    client = client_per()
    avvio = client.post("/login")
    url = urlsplit(avvio.headers["location"])
    parametri = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == auth.DISCORD_AUTHORIZE_URL
    assert parametri["scope"] == ["identify guilds"]
    assert parametri["prompt"] == ["consent"]
    assert parametri["redirect_uri"] == [OAUTH_DI_TEST.redirect_uri]

    callback = client.get(
        "/oauth/callback", params={"code": "code-finto", "state": parametri["state"][0]}
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_la_sessione_non_contiene_token_ne_identita(client_per):
    client = client_per()
    _login(client)
    # Esattamente questi quattro campi: nessun token, nessuno username, nessun
    # id Discord di chi si collega (dashboard.md 3). Il cookie e' leggibile.
    # "nomi" non e' un'identita': e' come si chiama una cosa che chi legge
    # quel cookie amministra, e che quindi sa gia'.
    assert set(_sessione(client)) == {"guilds", "nomi", "login_at", "checked_at"}
    assert _sessione(client)["guilds"] == [GUILD_TODAY]
    assert "token-finto" not in client.cookies.get(auth.COOKIE_SESSIONE)


def test_la_destinazione_dopo_il_login_viene_dalla_sessione(client_per):
    client = client_per()
    assert client.get(f"/guilds/{GUILD_TODAY}/coorti").status_code == 303
    callback = _login(client)
    assert callback.headers["location"] == f"/guilds/{GUILD_TODAY}/coorti"


def test_una_destinazione_in_query_string_si_ignora(client_per):
    client = client_per()
    callback = _login(client, next="https://evil.example", dopo="//evil.example")
    assert callback.headers["location"] == "/"


@pytest.mark.parametrize(
    "dopo",
    ["//evil.example", "https://evil.example", "/\\evil.example", "evil.example",
     "/\tevil", None, 42],
)
def test_destinazioni_non_locali_diventano_la_radice(dopo):
    assert auth.destinazione_sicura(dopo) == "/"


def test_una_destinazione_esterna_in_sessione_non_esce_dal_sito(client_per):
    # Anche il percorso di una richiesta vera puo' essere //evil.example: il
    # controllo vale su cio' che la sessione contiene, non solo sulla query.
    client = client_per()
    entra(client, {"dopo": "//evil.example"})
    assert _login(client).headers["location"] == "/"


def test_una_richiesta_a_doppio_slash_non_diventa_un_redirect_esterno(client_per):
    client = client_per()
    client.get("//evil.example")
    assert _login(client).headers["location"] == "/"


# --- Discord che non risponde come dovrebbe: si nega ---------------------------


@pytest.mark.parametrize(
    "handler",
    [
        discord_finto(guilds_status=500),
        discord_finto(token_status=500),
        discord_finto(token_status=400),
        discord_finto(guilds_body=b"{non e' json"),
        discord_finto(guilds={"guilds": []}),
        discord_finto(guilds=[{"id": "1"}]),
    ],
    ids=["guild-500", "token-500", "token-400", "json-malformato", "non-lista", "voce-senza-permessi"],
)
def test_discord_in_errore_nega(client_per, handler):
    client = client_per(handler)
    r = _login(client)
    assert r.status_code == 403
    assert "La verifica non è riuscita" in r.text
    assert client.get("/").status_code == 303  # fuori, non dentro


def test_discord_irraggiungibile_nega(client_per):
    def giu(request):
        raise httpx.ConnectError("giu'")

    client = client_per(giu)
    r = _login(client)
    assert r.status_code == 403
    assert "La verifica non è riuscita" in r.text


def test_lista_vuota_nega_con_la_sua_frase(client_per):
    client = client_per(discord_finto(guilds=[]))
    r = _login(client)
    assert r.status_code == 403
    assert "Nessun server da mostrarti" in r.text
    assert client.get("/").status_code == 303


def test_amministratore_solo_di_server_non_osservati_nega(client_per):
    client = client_per(discord_finto(guilds=[_guild(999, "8")]))
    assert "Nessun server da mostrarti" in _login(client).text


def test_api_di_kindling_giu_durante_il_login_nega(client_per):
    def api_giu(request):
        raise httpx.ConnectError("api giu'")

    client = client_per(api_transport=httpx.MockTransport(api_giu))
    r = _login(client)
    assert r.status_code == 403
    assert "La verifica non è riuscita" in r.text


def test_errore_restituito_da_discord_al_callback_nega(client_per):
    client = client_per()
    avvio = client.post("/login")
    r = client.get("/oauth/callback", params={"error": "access_denied", "state": _state(avvio)})
    assert r.status_code == 403
    assert "La verifica non è riuscita" in r.text


# --- state ---------------------------------------------------------------------


def test_state_assente_nega(client_per):
    client = client_per()
    client.post("/login")
    r = client.get("/oauth/callback", params={"code": "code-finto"})
    assert r.status_code == 403
    assert client.get("/").status_code == 303


def test_state_diverso_nega(client_per):
    client = client_per()
    avvio = client.post("/login")
    r = client.get("/oauth/callback", params={"code": "code-finto", "state": _state(avvio) + "x"})
    assert r.status_code == 403
    assert client.get("/").status_code == 303


def test_state_riusato_nega(client_per):
    chiamate = []
    client = client_per(discord_finto(chiamate=chiamate))
    avvio = client.post("/login")
    params = {"code": "code-finto", "state": _state(avvio)}
    assert client.get("/oauth/callback", params=params).status_code == 303
    scambi = len(chiamate)

    # Lo stesso state una seconda volta: negato, e Discord non viene nemmeno chiamato.
    assert client.get("/oauth/callback", params=params).status_code == 403
    assert len(chiamate) == scambi


def test_lo_state_si_consuma_al_primo_callback_anche_se_sbagliato(client_per):
    # Il test qui sopra passerebbe anche con uno state letto e non tolto,
    # perche' il login riuscito cancella comunque la sessione. Questo no: dopo
    # un callback con lo state sbagliato, quello giusto non vale piu'.
    client = client_per()
    avvio = client.post("/login")
    giusto = _state(avvio)
    assert client.get("/oauth/callback", params={"code": "c", "state": "sbagliato"}).status_code == 403
    assert client.get("/oauth/callback", params={"code": "c", "state": giusto}).status_code == 403
    assert client.get("/").status_code == 303


def test_un_callback_senza_flusso_non_cancella_una_sessione_valida(client_per):
    # State sbagliato = non e' il giro di questa sessione: nega, ma non deve
    # poter cancellare la sessione di chi e' gia' dentro.
    client = entra(client_per())
    assert client.get("/oauth/callback", params={"code": "c", "state": "s"}).status_code == 403
    assert client.get("/").status_code == 200


# --- le 8 ore: scadenza assoluta, non inattivita' ------------------------------


def test_cookie_firmato_oltre_le_8_ore_lo_rifiuta_il_signer(client_per):
    # La seconda difesa: la firma stessa e' vecchia. Il contenuto direbbe
    # "sessione fresca", quindi a negare puo' essere solo il signer.
    client = entra(client_per(), dati_di_sessione(), firmato_a=time.time() - ORE_8 - 60)
    r = client.get("/")
    assert r.status_code == 401
    assert "La sessione è scaduta" in r.text


def test_firma_fresca_e_login_at_vecchio_e_scaduta(client_per):
    # IL test delle 8 ore. Firma fresca: il signer lo accetta. E' lo stato di un
    # cookie rubato e usato di continuo, che Starlette rifirma a ogni risposta:
    # solo login_at, controllato dalla guardia, lo ferma.
    client = entra(client_per(), dati_di_sessione(login_at=time.time() - ORE_8 - 1))
    r = client.get("/")
    assert r.status_code == 401
    assert "La sessione è scaduta" in r.text
    # E dopo, fuori: la sessione e' stata cancellata, non solo rifiutata una volta.
    assert client.get("/").status_code == 303


def test_starlette_rifirma_a_ogni_risposta(client_per):
    # Il fatto su cui poggia dashboard-fase2.md 3-quinquies: se un giorno
    # Starlette smettesse di rifirmare, la ragione di login_at cambierebbe, e
    # questo test lo direbbe.
    client = entra(client_per())
    r = client.get("/")
    assert r.status_code == 200
    assert auth.COOKIE_SESSIONE in r.headers.get("set-cookie", "")


def test_l_uso_continuo_non_allunga_la_sessione(client_per, monkeypatch):
    # Richieste ogni 10 minuti, ricontrolli riusciti ogni 15: a 8 ore dal login
    # si esce comunque.
    client = client_per()
    inizio = time.time()
    monkeypatch.setattr(auth, "adesso", lambda: inizio)
    _login(client)

    istante = inizio
    esiti = []
    while istante < inizio + ORE_8 + 20 * 60:
        istante += 10 * 60
        monkeypatch.setattr(auth, "adesso", lambda t=istante: t)
        r = client.get("/")
        if r.status_code == 303 and "discord.com" in r.headers["location"]:
            r = client.get(
                "/oauth/callback", params={"code": "code-finto", "state": _state(r)}
            )
            if r.status_code == 303:
                r = client.get(r.headers["location"])
        esiti.append((istante - inizio, r.status_code))

    dentro = [t for t, s in esiti if s == 200]
    assert dentro and max(dentro) < ORE_8
    assert any(s == 401 for t, s in esiti if t >= ORE_8)
    assert all(s != 200 for t, s in esiti if t >= ORE_8)


# --- il ricontrollo ogni 15 minuti ---------------------------------------------


def test_checked_at_vecchio_fa_scattare_il_ricontrollo_silenzioso(client_per):
    client = entra(client_per(), dati_di_sessione(checked_at=time.time() - MINUTI_15 - 1))
    r = client.get(f"/guilds/{GUILD_TODAY}")
    assert r.status_code == 303
    parametri = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.headers["location"].startswith(auth.DISCORD_AUTHORIZE_URL)
    assert parametri["prompt"] == ["none"]


def test_checked_at_recente_non_ricontrolla(client_per):
    client = entra(client_per(), dati_di_sessione(checked_at=time.time() - MINUTI_15 + 30))
    assert client.get(f"/guilds/{GUILD_TODAY}").status_code == 200


def test_il_ricontrollo_riuscito_non_rinnova_login_at(client_per):
    login_at = time.time() - 3 * 3600
    client = entra(
        client_per(),
        dati_di_sessione([GUILD_TODAY], login_at=login_at, checked_at=time.time() - MINUTI_15 - 1),
    )
    r = client.get(f"/guilds/{GUILD_TODAY}/robustezza")
    callback = client.get("/oauth/callback", params={"code": "code-finto", "state": _state(r)})
    assert callback.status_code == 303
    assert callback.headers["location"] == f"/guilds/{GUILD_TODAY}/robustezza"
    sessione = _sessione(client)
    assert sessione["login_at"] == login_at  # le 8 ore contano ancora dal login
    assert sessione["checked_at"] > time.time() - 60


@pytest.mark.parametrize(
    "handler",
    [discord_finto(guilds_status=500), discord_finto(guilds_body=b"<html>"),
     discord_finto(token_status=401)],
    ids=["guild-500", "json-malformato", "token-401"],
)
def test_ricontrollo_fallito_nega_e_non_prolunga(client_per, handler):
    client = entra(
        client_per(handler), dati_di_sessione(checked_at=time.time() - MINUTI_15 - 1)
    )
    r = client.get("/")
    r = client.get("/oauth/callback", params={"code": "code-finto", "state": _state(r)})
    assert r.status_code == 403
    assert "La verifica non è riuscita" in r.text
    # Non prolungata: la sessione di prima non c'e' piu'.
    assert client.get("/").status_code == 303
    assert client.get("/").headers["location"] == "/login"


def test_ricontrollo_con_errore_da_discord_nega(client_per):
    # Il comportamento di prompt=none su un'autorizzazione revocata non e'
    # documentato: se arriva un errore, si nega.
    client = entra(client_per(), dati_di_sessione(checked_at=time.time() - MINUTI_15 - 1))
    r = client.get("/")
    r = client.get("/oauth/callback", params={"error": "consent_required", "state": _state(r)})
    assert r.status_code == 403
    assert client.get("/").headers["location"] == "/login"


def test_ricontrollo_riuscito_con_insieme_ridotto_toglie_il_server(client_per):
    # IL caso per cui il TTL esiste: chi perde ADMINISTRATOR riceve comunque un
    # code valido e Discord risponde regolarmente. Nessun errore da leggere:
    # la decisione viene dal ricalcolo dell'insieme.
    client = entra(
        client_per(discord_finto(guilds=[_guild(GUILD_TODAY, "8"), _guild(GUILD_MATURE, "0")])),
        dati_di_sessione(
            [GUILD_TODAY, GUILD_MATURE], checked_at=time.time() - MINUTI_15 - 1
        ),
    )
    r = client.get(f"/guilds/{GUILD_MATURE}")
    r = client.get("/oauth/callback", params={"code": "code-finto", "state": _state(r)})
    assert r.status_code == 303
    assert _sessione(client)["guilds"] == [GUILD_TODAY]
    negata = client.get(f"/guilds/{GUILD_MATURE}")
    assert negata.status_code == 404
    assert "Server non disponibile" in negata.text
    assert client.get(f"/guilds/{GUILD_TODAY}").status_code == 200


def test_ricontrollo_riuscito_con_insieme_svuotato_nega(client_per):
    client = entra(
        client_per(discord_finto(guilds=[_guild(GUILD_TODAY, "0")])),
        dati_di_sessione([GUILD_TODAY], checked_at=time.time() - MINUTI_15 - 1),
    )
    r = client.get(f"/guilds/{GUILD_TODAY}")
    r = client.get("/oauth/callback", params={"code": "code-finto", "state": _state(r)})
    assert r.status_code == 403
    assert "Nessun server da mostrarti" in r.text
    assert client.get(f"/guilds/{GUILD_TODAY}").status_code == 303


def test_un_flusso_silenzioso_non_si_fa_passare_per_login(client_per):
    # Il tipo del giro sta in sessione: un ricontrollo resta un ricontrollo
    # anche se chi lo completa aggiunge parametri, e non rinnova login_at.
    login_at = time.time() - 7 * 3600
    client = entra(
        client_per(),
        dati_di_sessione([GUILD_TODAY], login_at=login_at, checked_at=time.time() - MINUTI_15 - 1),
    )
    r = client.get("/")
    client.get(
        "/oauth/callback",
        params={"code": "code-finto", "state": _state(r), "prompt": "consent", "ricontrollo": "false"},
    )
    assert _sessione(client)["login_at"] == login_at


# --- la guardia e le sue eccezioni ---------------------------------------------


_SENZA_GUARDIA = {"/health", "/login", "/oauth/callback"}


def _rotte_get(app) -> list[str]:
    return sorted(
        r.path for r in app.routes
        if "GET" in getattr(r, "methods", set()) and r.path not in _SENZA_GUARDIA
    )


def test_ogni_vista_sta_dietro_la_guardia(client_per):
    # Enumera le rotte: una vista aggiunta domani senza `dependencies=protetta`
    # risponderebbe 200 a chiunque, e fallisce qui.
    client = client_per()
    rotte = _rotte_get(client.app)
    assert "/" in rotte and "/guilds/{guild_id}/coorti" in rotte
    for rotta in rotte:
        r = client.get(rotta.replace("{guild_id}", str(GUILD_TODAY)))
        assert r.status_code == 303, rotta
        assert r.headers["location"] == "/login", rotta


def test_le_sole_rotte_senza_guardia_sono_quelle_attese(client_per):
    app = client_per().app
    tutte = {r.path for r in app.routes if hasattr(r, "methods")}
    assert tutte - set(_rotte_get(app)) == _SENZA_GUARDIA | {"/logout"}


def test_health_risponde_senza_sessione(client_per):
    r = client_per().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api": "ok"}
    assert set(r.json()) == {"status", "api"}


def test_una_guild_fuori_dall_insieme_e_404_senza_chiamare_l_api(client_per):
    chiamate = []

    async def api(request):
        chiamate.append(request.url.path)
        return await httpx.ASGITransport(app=fixture_app).handle_async_request(request)

    class Registra(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await api(request)

    client = entra(client_per(api_transport=Registra()), dati_di_sessione([GUILD_TODAY]))
    r = client.get(f"/guilds/{GUILD_EDGE}")
    assert r.status_code == 404
    assert "Server non disponibile" in r.text
    assert chiamate == []


def test_l_elenco_mostra_solo_i_server_autorizzati(client_per):
    client = entra(client_per(), dati_di_sessione([GUILD_TODAY]))
    html = client.get("/").text
    assert str(GUILD_TODAY) in html
    assert str(GUILD_MATURE) not in html
    assert str(GUILD_EDGE) not in html


def test_risposta_autenticata_porta_cache_control_private_no_store(client_per):
    client = entra(client_per())
    for rotta in ["/", f"/guilds/{GUILD_TODAY}", f"/guilds/{GUILD_TODAY}/coorti"]:
        r = client.get(rotta)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store", rotta


def test_la_testata_mostra_il_numero_di_server_e_non_un_identita(client_per):
    client = entra(client_per(), dati_di_sessione([GUILD_TODAY, GUILD_MATURE]))
    html = client.get("/").text
    assert "Connesso · amministratore di 2 server" in html
    assert 'action="/logout"' in html
    # Senza sessione, niente testata di sessione.
    assert 'action="/logout"' not in client_per().get("/login").text


# --- logout --------------------------------------------------------------------


def test_logout_e_post_e_cancella_la_sessione(client_per):
    client = entra(client_per())
    assert client.get("/logout").status_code == 405
    assert client.get("/").status_code == 200
    r = client.post("/logout")
    assert r.status_code == 303
    assert client.get("/").headers["location"] == "/login"


def test_logout_funziona_anche_a_ricontrollo_scaduto(client_per):
    client = entra(client_per(), dati_di_sessione(checked_at=time.time() - MINUTI_15 - 1))
    assert client.post("/logout").status_code == 303
    assert client.get("/").headers["location"] == "/login"


# --- cookie --------------------------------------------------------------------


def _set_cookie_dopo_login(oauth: config.OAuthConfig, client_per) -> str:
    client = client_per(oauth=oauth)
    r = client.post("/login")
    return r.headers["set-cookie"].lower()


def test_cookie_secure_con_redirect_uri_https(client_per):
    oauth = config.OAuthConfig(
        client_id=OAUTH_DI_TEST.client_id,
        client_secret=OAUTH_DI_TEST.client_secret,
        session_secret=OAUTH_DI_TEST.session_secret,
        redirect_uri="https://dashboard.example.test/oauth/callback",
    )
    cookie = _set_cookie_dopo_login(oauth, client_per)
    assert "; secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"max-age={ORE_8}" in cookie


def test_cookie_non_secure_in_locale(client_per):
    cookie = _set_cookie_dopo_login(OAUTH_DI_TEST, client_per)
    assert "; secure" not in cookie
    assert "httponly" in cookie


# --- configurazione ------------------------------------------------------------


_VARIABILI = {
    "KINDLING_DISCORD_CLIENT_ID": "100000000000000001",
    "KINDLING_DISCORD_CLIENT_SECRET": "x" * 32,
    "KINDLING_SESSION_SECRET": "0" * 64,
    "KINDLING_OAUTH_REDIRECT_URI": "https://dashboard.example.test/oauth/callback",
}


@pytest.fixture
def ambiente(monkeypatch):
    for nome, valore in _VARIABILI.items():
        monkeypatch.setenv(nome, valore)
    return monkeypatch


def test_la_configurazione_completa_si_legge(ambiente):
    oauth = config.oauth_da_ambiente()
    assert oauth.cookie_secure is True
    # Il repr non mostra i segreti (un log di debug non deve poterli stampare).
    assert "x" * 32 not in repr(oauth) and "0" * 64 not in repr(oauth)


@pytest.mark.parametrize("nome", sorted(_VARIABILI))
@pytest.mark.parametrize("valore", [None, ""])
def test_una_variabile_mancante_o_vuota_ferma_l_avvio_e_si_nomina(ambiente, nome, valore):
    if valore is None:
        ambiente.delenv(nome)
    else:
        ambiente.setenv(nome, valore)
    with pytest.raises(RuntimeError, match=nome):
        config.oauth_da_ambiente()
    with pytest.raises(RuntimeError, match=nome):
        crea_app()


@pytest.mark.parametrize(
    "uri",
    ["http://dashboard.example.test/oauth/callback", "http://10.0.0.5:8000/oauth/callback",
     "ftp://localhost/oauth/callback", "dashboard.example.test/oauth/callback"],
)
def test_redirect_uri_in_chiaro_su_host_pubblico_rifiutata(ambiente, uri):
    ambiente.setenv("KINDLING_OAUTH_REDIRECT_URI", uri)
    with pytest.raises(RuntimeError, match="KINDLING_OAUTH_REDIRECT_URI"):
        config.oauth_da_ambiente()


@pytest.mark.parametrize(
    "uri", ["http://localhost:8000/oauth/callback", "http://127.0.0.1:8000/oauth/callback"]
)
def test_redirect_uri_http_accettata_solo_in_locale(ambiente, uri):
    ambiente.setenv("KINDLING_OAUTH_REDIRECT_URI", uri)
    assert config.oauth_da_ambiente().cookie_secure is False


def test_session_secret_corta_rifiutata(ambiente):
    ambiente.setenv("KINDLING_SESSION_SECRET", "a" * 31)
    with pytest.raises(RuntimeError, match="KINDLING_SESSION_SECRET"):
        config.oauth_da_ambiente()
